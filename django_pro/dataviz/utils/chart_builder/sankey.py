"""Sankey pathway builder — student / entity flow across ordered stages.

The chart is intentionally data-shape agnostic.  A stage path can come from:

* a JSON column holding an object (e.g. ``school_and_college_history``) whose
  scalar keys are the early stages and whose ``colleges`` list is the college
  trajectory;
* a JSON column holding an array of stage values (strings or objects);
* a delimited string (e.g. ``Seq 1: A -> Seq 2: B``);
* several ordinary columns supplied as the ordered stage columns.

Node names are always unique per stage, so the same school name appearing at
two different levels is rendered as two nodes instead of being merged.

Everything is built in a single pass with small, local data structures
(``Counter`` / dicts) and the JSON decoder is memoised, which keeps even large
pathway extracts cheap.  The heavy row/column work is done in Polars before
this builder is called.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache

import polars as pl

from .constants import PALETTES
from .common import _base_option, _message_option, _theme

# ── Stage labels ─────────────────────────────────────────────────────────────

_DEFAULT_STAGE_LABELS = {
    'grade_5': 'Elementary',
    'grade5': 'Elementary',
    'elementary': 'Elementary',
    'elementary_school': 'Elementary',
    'grade_8': 'Grade 8',
    'grade8': 'Grade 8',
    'middle_school': 'Middle School',
    'middle': 'Middle School',
    'high_school': 'High School',
    'highschool': 'High School',
    'hs': 'High School',
    'college': 'College',
}

# Keys that are structural inside a pathway JSON object and must not become
# ordinary scalar stages.
_JSON_NON_STAGE_KEYS = frozenset({
    'college', 'college_sequence', 'colleges', 'college_path',
    'post_hs_college_path', 'school_and_college_history',
    'college_sequence_max', 'college_sequences_found',
})

_PREFERRED_STAGE_ORDER = ('grade_5', 'grade_8', 'high_school')

# Stage colours are semantic: one colour band per depth.  When the user picks a
# named palette we use that palette instead (still one band per depth).
_SK_STAGE_COLORS = (
    '#4f46e5', '#0ea5e9', '#10b981', '#f59e0b',
    '#ef4444', '#8b5cf6', '#14b8a6', '#f97316',
)

# Per-entity colours for the default "School" colour mode. 30 curated hues
# keep nearby schools visually distinct without generating muddy random colours.
_SK_NODE_COLORS = (
    '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948',
    '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac', '#1f77b4', '#ff7f0e',
    '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f',
    '#bcbd22', '#17becf', '#393b79', '#637939', '#8c6d31', '#843c39',
    '#7b4173', '#5254a3', '#8ca252', '#bd9e39', '#ad494a', '#a55194',
    '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5', '#c49c94',
    '#f7b6d2', '#dbdb8d', '#9edae5', '#6b6ecf', '#b5cf6b', '#e7ba52',
    '#d6616b', '#ce6dbd', '#6baed6', '#fd8d3c', '#74c476', '#9e9ac8',
)
_SK_OTHER_COLOR = '#94a3b8'

# ── College system classification ────────────────────────────────────────────

SK_COLLEGE_SYSTEMS = [
    ('UC', 'University of California (UC)'),
    ('CSU', 'California State University (CSU)'),
    ('CCC', 'California Community College (CCC)'),
    ('Private', 'Private'),
    ('Out-of-State', 'Out of State'),
    ('Other', 'Other'),
    ('Unknown', 'Unknown'),
]

_CSU_NAME_TOKENS = (
    'CALIFORNIA STATE UNIVERSITY',
    'CALIFORNIA STATE UNIV',
    'CAL STATE',
    'CALIFORNIA POLYTECHNIC STATE UNIVERSITY',
    'CALIFORNIA STATE POLYTECHNIC UNIVERSITY',
    'POLYTECHNIC STATE UNIVERSITY',
    'SAN DIEGO STATE UNIVERSITY',
    'SAN JOSE STATE UNIVERSITY',
    'SAN FRANCISCO STATE UNIVERSITY',
    'SONOMA STATE UNIVERSITY',
    'CHICO STATE',
    'FRESNO STATE',
    'SACRAMENTO STATE',
    'LONG BEACH STATE',
    'NORTHRIDGE STATE',
    'FULLERTON STATE',
    'POMONA STATE',
)
_UC_NAME_TOKENS = (
    'UNIVERSITY OF CALIFORNIA',
    'UNIV OF CALIFORNIA',
)
_UC_WORD_RE = re.compile(r'\bUC\b')
_CSU_WORD_RE = re.compile(r'\bCSU\b')

# Used to recognise high schools that were stored in the ``grade_8`` field
# (some students attended a high-school campus in 8th grade). Those nodes are
# labelled "High School (8th)" instead of "Grade 8".
_HS_VALUE_RE = re.compile(r'(?i)(?:\bHS\b|\bH\.S\.|HIGH SCHOOL)')
_MS_VALUE_RE = re.compile(r'(?i)(?:\bMS\b|\bM\.S\.|MIDDLE SCHOOL)')


def _norm_key(value) -> str:
    """Lower-case, punctuation-collapsed key for tolerant comparisons."""
    if value is None:
        return ''
    return re.sub(r'[^a-z0-9]+', '-', str(value).strip().lower()).strip('-')


_SYSTEM_ALIASES = {}
for _key, _label in SK_COLLEGE_SYSTEMS:
    _SYSTEM_ALIASES[_norm_key(_key)] = _key
    _SYSTEM_ALIASES[_norm_key(_label)] = _key


def normalize_college_system(value) -> str:
    """Map a system key or label to its canonical key (``UC``, ``CSU``, …)."""
    return _SYSTEM_ALIASES.get(_norm_key(value), str(value or '').strip())


def classify_college_system(name, college_type='', control='', state='') -> str:
    """Classify one college into UC / CSU / CCC / Private / Out-of-State / Other.

    ``state`` is only used when present.  A blank state is treated as in-state
    so the name patterns still work on datasets that do not export state.
    """
    n = str(name or '').upper().strip()
    ctype = _norm_key(college_type)
    ctrl = _norm_key(control)
    st = str(state or '').upper().strip()
    in_ca = (not st) or st in ('CA', 'CALIFORNIA', 'CALIF', 'CAL')

    if n and in_ca and (any(tok in n for tok in _UC_NAME_TOKENS) or _UC_WORD_RE.search(n)):
        return 'UC'
    if n and in_ca and (any(tok in n for tok in _CSU_NAME_TOKENS) or _CSU_WORD_RE.search(n)):
        return 'CSU'
    if st and not in_ca:
        return 'Out-of-State'
    if ctrl == 'private':
        return 'Private'
    if ctype in ('2-year', '2-years', 'less-than-2-years') or 'COMMUNITY COLLEGE' in n or 'CITY COLLEGE' in n:
        return 'CCC'
    if n or ctype or ctrl:
        return 'Other'
    return 'Unknown'


# ── Config helpers ───────────────────────────────────────────────────────────

def _as_list(value) -> list:
    """Accept a list, JSON array string, or comma/semicolon separated string."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v is not None and str(v).strip() != '']
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith('['):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else [parsed]
            except (ValueError, TypeError):
                pass
        return [part.strip() for part in re.split(r'[,;\n]+', text) if part.strip()]
    return [value]


def _as_int(value, default=None):
    try:
        if value is None or value == '':
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'y')


def _find_col(df: pl.DataFrame, name) -> str:
    """Resolve a config column name to an actual dataframe column."""
    if not name or not isinstance(name, str):
        return ''
    if name in df.columns:
        return name
    norm = name.replace('-', '_').lower()
    for col in df.columns:
        if str(col).replace('-', '_').lower() == norm:
            return col
    bare = norm.split('.')[-1]
    for col in df.columns:
        c_norm = str(col).replace('-', '_').lower()
        if c_norm.split('.')[-1] == bare:
            return col
    return ''


def _stage_label(key: str, labels_cfg, index: int) -> str:
    """Resolve a friendly stage label for *key* at position *index*."""
    mapped = None
    if isinstance(labels_cfg, dict):
        mapped = labels_cfg.get(key)
    elif isinstance(labels_cfg, (list, tuple)):
        if index < len(labels_cfg):
            mapped = labels_cfg[index]
    if mapped:
        return str(mapped)
    index_match = re.match(r'^__index_(\d+)__$', str(key))
    if index_match:
        return f'Stage {int(index_match.group(1)) + 1}'
    norm = str(key).replace('-', '_').lower()
    if norm in _DEFAULT_STAGE_LABELS:
        return _DEFAULT_STAGE_LABELS[norm]
    return str(key).replace('_', ' ').replace('-', ' ').strip().title() or f'Stage {index + 1}'


def _unique_stage_labels(keys: list[str], labels_cfg) -> list[str]:
    """Build labels that never collide, so nodes from different depths differ."""
    labels: list[str] = []
    used: set[str] = set()
    for i, key in enumerate(keys):
        base = _stage_label(key, labels_cfg, i)
        label = base
        suffix = 2
        while label in used:
            label = f'{base} {suffix}'
            suffix += 1
        used.add(label)
        labels.append(label)
    return labels


@lru_cache(maxsize=16384)
def _loads_cached(raw: str):
    """Decode a JSON string once; pathway strings repeat heavily."""
    return json.loads(raw)


def _parse_path_value(raw):
    """Return a decoded JSON value, a delimited marker, or ``None``."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return str(raw)
    text = raw.strip()
    if not text or text.lower() in ('null', 'none', 'nan', 'undefined'):
        return None
    if text[0] in '{[':
        try:
            return _loads_cached(text)
        except (ValueError, TypeError):
            return None
    return {'__delimited__': text}


def _as_scalar(value) -> str:
    """Convert a stage value to display text without turning JSON into noise."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip() for v in value if v is not None and str(v).strip()]
        return ', '.join(parts)
    if isinstance(value, dict):
        for key in ('name', 'value', 'label', 'college', 'school'):
            if value.get(key):
                return str(value[key]).strip()
        return ''
    return str(value).strip()


def _split_delimited(text: str, cfg) -> list[str]:
    """Split ``Seq 1: A -> Seq 2: B`` (or a custom delimiter) into stage values."""
    delimiter = str(cfg.get('sk_delimiter') or '->').strip() or '->'
    if '→' in text and '->' not in text:
        parts = text.split('→')
    elif delimiter in text:
        parts = text.split(delimiter)
    else:
        parts = [text]
    cleaned = []
    for part in parts:
        value = part.strip()
        # Remove "Seq 1:" / "Step 2:" / "College 1:" prefixes.
        value = re.sub(r'^(?:seq(?:uence)?|step|college|stage)\s*#?\s*\d+\s*[:.\-]\s*', '', value, flags=re.IGNORECASE)
        cleaned.append(value.strip())
    return [v for v in cleaned if v]


def _json_auto_stage_keys(obj: dict, cfg) -> list[str]:
    """Choose scalar object keys to use as stages when the user left it blank."""
    keys = []
    for key in _PREFERRED_STAGE_ORDER:
        if key in obj:
            keys.append(key)
    for key, value in obj.items():
        if key in keys or key in _JSON_NON_STAGE_KEYS:
            continue
        if isinstance(value, (dict, list)):
            continue
        keys.append(key)
    return keys


def _college_items(value, cfg) -> list[tuple[str, float]]:
    """Normalise the colleges list into ``[(name, sequence)]``."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    name_key = str(cfg.get('sk_college_name_key') or 'college')
    seq_key = str(cfg.get('sk_college_seq_key') or 'college_sequence')
    out = []
    for idx, item in enumerate(value):
        if item is None:
            continue
        if isinstance(item, dict):
            name = item.get(name_key)
            if name is None:
                # A few exports use a different name field; fall back to common
                # aliases before giving up.
                for alt in ('college', 'name', 'school', 'institution', 'value'):
                    if item.get(alt):
                        name = item.get(alt)
                        break
            seq = item.get(seq_key, idx)
        else:
            name = item
            seq = idx
        name = _as_scalar(name)
        if not name:
            continue
        try:
            seq_num = float(seq)
        except (TypeError, ValueError):
            seq_num = float(idx)
        out.append((name, seq_num))
    # Preserve the JSON array order. The stored college_sequence is
    # inconsistent across exports (some 0-based, some 1-based), while the array
    # order is the actual trajectory.
    return out


# ── Row filtering ────────────────────────────────────────────────────────────

def _col_str_expr(col: str):
    return pl.col(col).cast(pl.Utf8).fill_null('').str.strip_chars()


def _norm_expr(expr):
    """Expression equivalent of :func:`_norm_key` (lower-case, hyphenated)."""
    return (
        expr.str.to_lowercase()
        .str.replace_all(r'[^a-z0-9]+', '-')
        .str.strip_chars('-')
    )


def _apply_generic_filter(df, flt, cfg):
    if not isinstance(flt, dict):
        return df
    col_name = flt.get('col') or flt.get('column') or ''
    op = str(flt.get('op') or flt.get('operator') or 'in').strip().lower()
    values = _as_list(flt.get('values', flt.get('val', flt.get('value'))))

    if col_name in ('college_system', '__dv_sankey_system__'):
        return _apply_system_filter(df, values, cfg)

    col = _find_col(df, col_name)
    if not col:
        return df
    expr = _col_str_expr(col)
    lowered = values and [_norm_key(v) for v in values]

    if op in ('in', 'eq', '=', 'is', 'any_of'):
        if not lowered:
            return df
        return df.filter(_norm_expr(expr).is_in(lowered))
    if op in ('not_in', 'nin', 'neq', '!='):
        if not lowered:
            return df
        return df.filter(~_norm_expr(expr).is_in(lowered))
    if op in ('contains', 'like'):
        if not lowered:
            return df
        keep = pl.lit(False)
        for value in values:
            keep = keep | expr.str.to_lowercase().str.contains(str(value).lower(), literal=True)
        return df.filter(keep)
    if op in ('not_contains', 'not_like'):
        keep = pl.lit(True)
        for value in values:
            keep = keep & ~expr.str.to_lowercase().str.contains(str(value).lower(), literal=True)
        return df.filter(keep)
    if op in ('is_blank', 'blank', 'empty'):
        return df.filter(expr == '')
    if op in ('is_not_blank', 'not_blank', 'not_empty'):
        return df.filter(expr != '')
    return df


def _col_for_scope(df, cfg, base_prefix):
    """Pick the first/latest source column for college attributes."""
    scope = str(cfg.get('sk_college_filter_scope') or 'first').strip().lower()
    # The college name source is configurable under ``sk_college_system_source``
    # because the name is what the system classifier consumes.
    explicit_key = 'sk_college_system_source' if base_prefix == 'name' else f'sk_college_{base_prefix}_source'
    explicit = cfg.get(explicit_key) or ''
    col = _find_col(df, explicit)
    if col:
        return col
    if scope == 'latest':
        return _find_col(df, f'latest_college_{base_prefix}')
    return _find_col(df, f'first_post_hs_college_{base_prefix}')


def _row_system_series(df, cfg) -> list[str]:
    name_col = _col_for_scope(df, cfg, 'name')
    if not name_col:
        return ['Unknown'] * df.height
    type_col = _col_for_scope(df, cfg, 'type')
    control_col = _col_for_scope(df, cfg, 'control')
    state_col = _col_for_scope(df, cfg, 'state')
    names = df[name_col].to_list()
    types = df[type_col].to_list() if type_col else [None] * df.height
    controls = df[control_col].to_list() if control_col else [None] * df.height
    states = df[state_col].to_list() if state_col else [None] * df.height
    return [
        classify_college_system(name, ctype, control, state)
        for name, ctype, control, state in zip(names, types, controls, states)
    ]


def _apply_system_filter(df, values, cfg):
    systems = _as_list(values)
    if not systems:
        return df
    wanted = {normalize_college_system(s) for s in systems}
    row_systems = _row_system_series(df, cfg)
    return df.with_columns(
        pl.Series('__dv_sankey_system__', row_systems)
    ).filter(pl.col('__dv_sankey_system__').is_in(list(wanted))).drop('__dv_sankey_system__')


def _apply_type_filter(df, values, cfg):
    types = _as_list(values)
    if not types:
        return df
    col = _col_for_scope(df, cfg, 'type')
    if not col:
        return df
    wanted = {_norm_key(v) for v in types}
    return df.filter(_norm_expr(_col_str_expr(col)).is_in(list(wanted)))


def _apply_filters(df, cfg):
    """Apply chart-level pathway filters without changing the caller's frame."""
    if df.is_empty():
        return df

    for flt in _as_list(cfg.get('sk_filters')):
        df = _apply_generic_filter(df, flt, cfg)
        if df.is_empty():
            return df

    df = _apply_type_filter(df, cfg.get('sk_college_types'), cfg)
    if df.is_empty():
        return df
    df = _apply_system_filter(df, cfg.get('sk_college_systems'), cfg)
    return df


# ── Path extraction ──────────────────────────────────────────────────────────

def _extract_paths(df: pl.DataFrame, cfg: dict):
    """Return ``(stage_rows, stage_keys, stage_labels, skipped)``.

    ``stage_rows`` is a list of lists of stage *values* (one list per data row).
    Every row's list is positionally aligned to ``stage_labels``.
    """
    path_col = _find_col(df, cfg.get('sk_path_col') or cfg.get('x_col') or '')
    stage_cols = [_find_col(df, c) for c in _as_list(cfg.get('sk_stage_cols'))]
    stage_cols = [c for c in stage_cols if c]
    blank_label = cfg.get('sk_blank_label')
    blank_label = '(Not reported)' if blank_label is None else str(blank_label)

    def _clean(seq):
        """Trim at the first blank unless a blank label is configured."""
        out = []
        for value in seq:
            text = str(value or '').strip()
            if not text:
                if blank_label:
                    out.append(blank_label)
                    continue
                break
            out.append(text)
        return out

    # Multi-column mode: ordinary columns, one per stage.
    if stage_cols and not path_col:
        keys = list(stage_cols)
        labels = _unique_stage_labels(keys, cfg.get('sk_stage_labels'))
        rows = [_clean([_as_scalar(v) for v in row]) for row in df.select(stage_cols).iter_rows()]
        return rows, keys, labels, 0

    # No JSON path column: if the user mapped several columns into X, each one
    # is a stage.
    if not path_col:
        x_cols = [_find_col(df, c) for c in _as_list(cfg.get('_x_cols'))]
        x_cols = [c for c in x_cols if c]
        if len(x_cols) > 1:
            keys = list(x_cols)
            labels = _unique_stage_labels(keys, cfg.get('sk_stage_labels'))
            rows = [_clean([_as_scalar(v) for v in row]) for row in df.select(x_cols).iter_rows()]
            return rows, keys, labels, 0
        return [], [], [], df.height

    raw_values = df[path_col].to_list()
    path_format = str(cfg.get('sk_path_format') or 'auto').strip().lower()
    explicit_stages = [str(s) for s in _as_list(cfg.get('sk_stages'))]
    colleges_key = str(cfg.get('sk_colleges_key') or 'colleges')
    max_steps = _as_int(cfg.get('sk_max_college_steps'), 0) or 0
    auto_cap = max(1, min(12, _as_int(cfg.get('sk_auto_max_college_steps'), 4) or 4))

    # ── Shape detection (sample only; the rest is streamed once) ────────────
    mode = {'auto': '', 'json': 'object', 'delimited': 'delimited'}.get(path_format, '')
    first_obj = None
    for raw in raw_values[:25]:
        parsed = _parse_path_value(raw)
        if parsed is None:
            continue
        if isinstance(parsed, dict) and '__delimited__' in parsed:
            mode = mode or 'delimited'
            break
        if isinstance(parsed, list):
            mode = mode or 'array'
            break
        if isinstance(parsed, dict):
            mode = mode or 'object'
            first_obj = parsed
            break
    if not mode:
        mode = 'object'

    # ── Stage keys ───────────────────────────────────────────────────────────
    scalar_keys: list[str] = []
    college_keys: list[str] = []
    college_base_label = str(cfg.get('sk_college_stage_label') or 'College')
    collapse_colleges = _as_bool(cfg.get('sk_collapse_college'), False)

    if mode == 'object':
        scalar_keys = list(explicit_stages)
        if not scalar_keys:
            if first_obj is None:
                for raw in raw_values:
                    parsed = _parse_path_value(raw)
                    if isinstance(parsed, dict) and '__delimited__' not in parsed:
                        first_obj = parsed
                        break
            if first_obj is not None:
                scalar_keys = _json_auto_stage_keys(first_obj, cfg)
        college_count = max_steps if max_steps > 0 else auto_cap
        college_count = max(1, min(12, college_count))
        if collapse_colleges:
            college_keys = [colleges_key]
        else:
            college_keys = [f'{colleges_key}__{i}' for i in range(college_count)]
    elif mode == 'array':
        if explicit_stages:
            scalar_keys = list(explicit_stages)
        else:
            # Use the configured cap rather than a sample-derived length: a
            # later row may have a longer trajectory than the first 100 rows.
            scalar_keys = [f'__index_{i}__' for i in range(auto_cap)]
    else:  # delimited
        if explicit_stages:
            scalar_keys = list(explicit_stages)
        else:
            scalar_keys = [f'__index_{i}__' for i in range(auto_cap)]

    keys = list(scalar_keys) + college_keys
    labels_cfg = cfg.get('sk_stage_labels')
    labels = _unique_stage_labels(keys, labels_cfg)

    # Delimited paths default to "College 1", "College 2", … — the common case
    # is a college trajectory such as ``Seq 1: A -> Seq 2: B``.
    if mode == 'delimited' and not labels_cfg:
        labels = [f'{college_base_label} {i + 1}' for i in range(len(scalar_keys))]
    elif mode == 'array' and not labels_cfg:
        labels = [f'Stage {i + 1}' for i in range(len(scalar_keys))]

    # College stages use "College 1", "College 2", … unless explicitly labelled.
    if college_keys:
        if isinstance(labels_cfg, dict):
            college_labels = [labels_cfg.get(k) for k in college_keys]
        elif isinstance(labels_cfg, (list, tuple)):
            college_labels = list(labels_cfg[len(scalar_keys):])
        else:
            college_labels = []
        for i, _key in enumerate(college_keys):
            if i < len(college_labels) and college_labels[i]:
                continue
            label = college_base_label if collapse_colleges else f'{college_base_label} {i + 1}'
            while label in labels:
                label = f'{label} +'
            labels[len(scalar_keys) + i] = label

    rows: list[list[str]] = []
    skipped = 0
    for raw in raw_values:
        parsed = _parse_path_value(raw)
        if parsed is None:
            skipped += 1
            continue

        values: list[str] = []
        colleges: list[str] = []

        if mode == 'object' and isinstance(parsed, dict):
            for key in scalar_keys:
                values.append(_as_scalar(parsed.get(key)))
            college_items = _college_items(parsed.get(colleges_key), cfg)
            if not college_items:
                top_college = _as_scalar(parsed.get('college'))
                if top_college:
                    college_items = [(top_college, 0)]
            colleges = [name for name, _seq in college_items]
            values = _clean(values)
            colleges = _clean(colleges)
        elif mode == 'delimited':
            values = _clean(_split_delimited(str(parsed.get('__delimited__', '')), cfg))
        elif isinstance(parsed, list):
            items = []
            for item in parsed:
                if isinstance(item, dict):
                    name = item.get(cfg.get('sk_college_name_key') or 'college')
                    items.append(_as_scalar(name) or _as_scalar(item))
                else:
                    items.append(_as_scalar(item))
            values = _clean(items)
        else:
            values = _clean([_as_scalar(parsed)])

        if not values and not colleges:
            skipped += 1
            continue

        stage_values = list(values[: len(scalar_keys)])
        if mode == 'object' and college_keys:
            if collapse_colleges:
                if colleges:
                    stage_values.append(colleges[-1])
            else:
                stage_values.extend(colleges[: len(college_keys)])
        if len(stage_values) > len(keys):
            stage_values = stage_values[:len(keys)]
        # A row whose every stage is the blank sentinel carries no pathway
        # information; counting it would create a large synthetic flow.
        if blank_label and stage_values and all(v == blank_label for v in stage_values):
            skipped += 1
            continue
        rows.append(stage_values)

    return rows, keys, labels, skipped



# ── Option builder ───────────────────────────────────────────────────────────

def _sankey_option(df: pl.DataFrame, x_col: str, x_cols: list[str], cfg: dict, labels: dict) -> dict:
    """Build an ECharts Sankey option from a pathway column / stage columns."""
    if df.is_empty():
        return _message_option('No data for pathway chart', cfg, error=True)

    # ``x_col``/``x_cols`` are the generic chart mappings.  Sankey accepts the
    # JSON path column as X, or several ordered columns as the stage path.
    work_cfg = dict(cfg)
    if not work_cfg.get('sk_path_col') and x_col:
        work_cfg['sk_path_col'] = x_col
    if not work_cfg.get('sk_stage_cols') and x_cols:
        work_cfg['sk_stage_cols'] = list(x_cols)
    work_cfg['_x_cols'] = list(x_cols)

    if not _find_col(df, work_cfg.get('sk_path_col') or '') and not _as_list(work_cfg.get('sk_stage_cols')):
        return _message_option(
            'Pick the JSON pathway column (or drag the stage columns into X)',
            cfg, error=True,
        )

    work = _apply_filters(df, work_cfg)
    if work.is_empty():
        return _message_option('All rows filtered out by pathway filters', cfg)

    stage_rows, stage_keys, stage_labels, skipped = _extract_paths(work, work_cfg)
    if not stage_rows:
        return _message_option(
            'No usable pathway values found. Check the path column and stage fields.',
            cfg, error=True,
        )

    # Some exports store a high-school campus in the ``grade_8`` field (the
    # student attended that campus in 8th grade). Keep the value but label it
    # as a high-school stage rather than a middle-school one.
    middle_idx = None
    for index, key in enumerate(stage_keys):
        norm = str(key).lower().replace('-', '_')
        if norm in ('grade_8', 'grade8', 'grade_8_school', 'middle_school', 'middle') or 'grade_8' in norm:
            middle_idx = index
            break

    def _node_stage_label(depth: int, value: str) -> str:
        if (
            depth == middle_idx
            and value
            and _HS_VALUE_RE.search(str(value))
            and not _MS_VALUE_RE.search(str(value))
        ):
            return 'High School (8th)'
        return stage_labels[depth] if depth < len(stage_labels) else f'Stage {depth + 1}'

    def _node_name(depth: int, value: str) -> str:
        return f'{_node_stage_label(depth, value)} · {value}'

    min_link = max(1, _as_int(work_cfg.get('sk_min_link_value'), 1) or 1)
    max_nodes = _as_int(work_cfg.get('sk_max_nodes'), 300)
    if max_nodes is None:
        max_nodes = 300
    color_mode = str(work_cfg.get('sk_color_mode') or 'node').strip().lower()
    if color_mode not in ('node', 'stage', 'first'):
        color_mode = 'node'
    link_color_mode = str(work_cfg.get('sk_link_color_mode') or 'source').strip().lower()
    if link_color_mode not in ('source', 'gradient', 'target'):
        link_color_mode = 'source'
    order_mode = str(work_cfg.get('sk_order_mode') or 'value').strip().lower()
    if order_mode not in ('value', 'cohort'):
        order_mode = 'value'
    top_per_stage = _as_int(work_cfg.get('sk_top_per_stage'), 12)
    if top_per_stage is None:
        top_per_stage = 12
    group_after = _as_int(work_cfg.get('sk_group_after_stage'), 3)
    if group_after is None:
        group_after = 3
    group_after = max(0, group_after)

    link_counts: Counter = Counter()
    node_totals: Counter = Counter()
    node_depth: dict[str, int] = {}
    node_first_counts: dict[str, Counter] = {}
    first_totals: Counter = Counter()
    linked = 0

    for values in stage_rows:
        # ``values`` is positional against the stage list; drop trailing empties
        # so paths stop where the student's record ends.
        while values and not str(values[-1]).strip():
            values.pop()
        if len(values) < 2:
            continue
        linked += 1
        first_node = _node_name(0, values[0])
        first_totals[first_node] += 1
        for depth, value in enumerate(values):
            if not value:
                continue
            node = _node_name(depth, value)
            node_totals[node] += 1
            node_depth.setdefault(node, depth)
            node_first_counts.setdefault(node, Counter())[first_node] += 1
        for depth in range(len(values) - 1):
            if not values[depth] or not values[depth + 1]:
                continue
            src = _node_name(depth, values[depth])
            dst = _node_name(depth + 1, values[depth + 1])
            if src == dst:
                continue
            link_counts[(src, dst)] += 1

    if not link_counts:
        return _message_option(
            'No pathway links to display — the selected rows do not have at least two stages.',
            cfg,
        )

    # Preserve colour assignments for values that are about to be grouped into
    # "Other", so expanding the group can reuse the same distinct colours.
    color_value_totals: Counter = Counter()
    color_value_first_depth: dict[str, int] = {}
    for node, total in node_totals.items():
        value = node.split(' · ', 1)[1] if ' · ' in node else node
        color_value_totals[value] += total
        depth = node_depth.get(node, 0)
        if value not in color_value_first_depth or depth < color_value_first_depth[value]:
            color_value_first_depth[value] = depth

    # ── Keep the graph readable: group the long tail of college destinations ──
    # The first stages (elementary/middle/high school) are always kept; later
    # stages keep their top-N values and collapse the rest into one "Other"
    # node per stage.  Flow volumes are preserved.
    node_remap: dict[str, str] = {}
    other_details: dict[str, list[tuple[str, int]]] = {}
    other_expand: dict[str, dict] = {}
    if top_per_stage > 0:
        by_depth: dict[int, list[str]] = {}
        for node, depth in node_depth.items():
            by_depth.setdefault(depth, []).append(node)

        remap = node_remap
        for depth, depth_nodes in by_depth.items():
            if depth < group_after or len(depth_nodes) <= top_per_stage:
                continue
            ordered = sorted(depth_nodes, key=lambda n: (-node_totals[n], n))
            pruned = ordered[top_per_stage:]
            if not pruned:
                continue
            other = f'{stage_labels[depth]} · Other'
            suffix = 2
            while other in node_totals and other not in pruned:
                other = f'{stage_labels[depth]} · Other {suffix}'
                suffix += 1
            remap.update({n: other for n in pruned})
            node_totals[other] = sum(node_totals[n] for n in pruned)
            node_depth[other] = depth
            merged: Counter = Counter()
            detail_rows = []
            child_rows = []
            for n in pruned:
                merged.update(node_first_counts.get(n, Counter()))
                detail_rows.append((
                    n.split(' · ', 1)[1] if ' · ' in n else n,
                    int(node_totals[n]),
                ))
                incoming = [
                    [src, int(count)]
                    for (src, dst), count in link_counts.items()
                    if dst == n and count >= min_link
                ]
                outgoing = [
                    [dst, int(count)]
                    for (src, dst), count in link_counts.items()
                    if src == n and count >= min_link
                ]
                if incoming or outgoing:
                    child_rows.append({
                        'name': n,
                        'value': int(node_totals[n]),
                        'incoming': incoming,
                        'outgoing': outgoing,
                    })
                node_first_counts.pop(n, None)
                node_totals.pop(n, None)
                node_depth.pop(n, None)
            detail_rows.sort(key=lambda pair: (-pair[1], pair[0]))
            child_rows.sort(key=lambda child: (-child['value'], child['name']))
            other_details[other] = detail_rows
            if child_rows:
                other_expand[other] = {'depth': depth, 'children': child_rows}
            node_first_counts[other] = merged

        # Endpoint nodes may themselves have been grouped at an earlier/later
        # depth; point those links at the final "Other" node names.
        for group in other_expand.values():
            for child in group['children']:
                child['incoming'] = [
                    [node_remap.get(src, src), count]
                    for src, count in child['incoming']
                    if node_remap.get(src, src) != child['name']
                ]
                child['outgoing'] = [
                    [node_remap.get(dst, dst), count]
                    for dst, count in child['outgoing']
                    if node_remap.get(dst, dst) != child['name']
                ]

        if remap:
            grouped_links: Counter = Counter()
            for (src, dst), count in link_counts.items():
                new_src = remap.get(src, src)
                new_dst = remap.get(dst, dst)
                if new_src == new_dst:
                    continue
                grouped_links[(new_src, new_dst)] += count
            link_counts = grouped_links
            linked_nodes = set()
            for src, dst in link_counts:
                linked_nodes.add(src)
                linked_nodes.add(dst)
            for node in list(node_totals):
                if node not in linked_nodes:
                    node_totals.pop(node, None)
                    node_depth.pop(node, None)
                    node_first_counts.pop(node, None)

    # Prune hair-thin links, then optionally keep only the largest nodes.
    links = [
        {'source': src, 'target': dst, 'value': float(value)}
        for (src, dst), value in link_counts.items()
        if value >= min_link
    ]
    if not links:
        return _message_option('No pathway links meet the minimum link value', cfg)

    if max_nodes > 0:
        ranked = sorted(node_totals.items(), key=lambda kv: kv[1], reverse=True)
        keep = {name for name, _ in ranked[:max_nodes]}
        links = [link for link in links if link['source'] in keep and link['target'] in keep]
        if not links:
            return _message_option('No pathway links left after pruning nodes', cfg)
        keep = {link['source'] for link in links} | {link['target'] for link in links}
        node_totals = Counter({n: v for n, v in node_totals.items() if n in keep})
        node_depth = {n: d for n, d in node_depth.items() if n in keep}
        node_first_counts = {n: c for n, c in node_first_counts.items() if n in keep}
        other_details = {n: rows for n, rows in other_details.items() if n in keep}
        other_expand = {n: group for n, group in other_expand.items() if n in keep}

    # ── Upstream ancestry, for click-to-trace ────────────────────────────────
    # For every visible node, record the exact upstream school/college counts
    # from the same rows. This is what powers the click panel in the renderer.
    ancestor_payload = {}
    if _as_bool(work_cfg.get('sk_trace_ancestors'), True):
        for values in stage_rows:
            while values and not str(values[-1]).strip():
                values.pop()
            if len(values) < 2:
                continue
            nodes: list[str] = []
            for depth, value in enumerate(values):
                if not value:
                    continue
                node = _node_name(depth, value)
                node = node_remap.get(node, node)
                if node in node_totals:
                    nodes.append(node)
            if len(nodes) < 2:
                continue
            for index in range(1, len(nodes)):
                target = nodes[index]
                entry = ancestor_payload.get(target)
                if entry is None:
                    entry = {
                        'total': int(node_totals.get(target, 0)),
                        'ancestors': Counter(),
                    }
                    ancestor_payload[target] = entry
                for ancestor in nodes[:index]:
                    entry['ancestors'][ancestor] += 1

    # ── Stable vertical order ────────────────────────────────────────────────
    # First-stage schools are ranked largest → smallest (blank/"Other" at the
    # bottom). Deeper nodes are either ordered largest-first, or aligned to the
    # weighted average rank of the first schools feeding them.
    blank_label = cfg.get('sk_blank_label')
    if blank_label is None:
        blank_label = '(Not reported)'
    neutral_values = {'Other', 'Unknown', '(Blank)', '(blank)', '', str(blank_label)}

    def _node_value(node: str) -> str:
        return node.split(' · ', 1)[1] if ' · ' in node else node

    def _is_neutral(node: str) -> bool:
        return _node_value(node) in neutral_values

    first_order = {
        node: i for i, (node, _count) in enumerate(
            sorted(first_totals.items(), key=lambda kv: (_is_neutral(kv[0]), -kv[1], kv[0]))
        )
    }
    fallback_rank = len(first_order)

    def _sort_key(node: str):
        depth = node_depth.get(node, 0)
        if order_mode == 'cohort':
            counts = node_first_counts.get(node) or {}
            total = sum(counts.values()) or 1
            avg_rank = sum(
                first_order.get(fn, fallback_rank) * count
                for fn, count in counts.items()
            ) / total
            return (depth, _is_neutral(node), avg_rank, -node_totals[node], node)
        return (depth, _is_neutral(node), -node_totals[node], node)

    # ── Colours ──────────────────────────────────────────────────────────────
    # Use the pre-grouping value map so expanded "Other" children keep the
    # same distinct colours they would have had in the full chart.
    value_totals = color_value_totals
    value_first_depth = color_value_first_depth

    palette_name = str(work_cfg.get('palette') or '').strip()
    stage_palette = PALETTES.get(palette_name, _SK_STAGE_COLORS) if palette_name else _SK_STAGE_COLORS
    theme = _theme(cfg)
    user_cmap = cfg.get('color_map') or {}

    def _stage_color(depth: int) -> str:
        return stage_palette[depth % len(stage_palette)] if stage_palette else '#4f46e5'

    sorted_values = sorted(
        value_totals,
        key=lambda v: (v in neutral_values, value_first_depth.get(v, 0), -value_totals[v], v),
    )
    value_colors = {}
    for index, value in enumerate(sorted_values):
        if value in neutral_values:
            value_colors[value] = _SK_OTHER_COLOR
        else:
            value_colors[value] = _SK_NODE_COLORS[index % len(_SK_NODE_COLORS)]

    # Assign colours to the children that will appear when "Other" expands.
    for other, group in other_expand.items():
        depth = group['depth']
        stage_label = stage_labels[depth] if depth < len(stage_labels) else ''
        for child in group['children']:
            child_value = (
                child['name'].split(' · ', 1)[1]
                if ' · ' in child['name'] else child['name']
            )
            if color_mode == 'stage':
                color = _stage_color(depth)
            else:
                color = value_colors.get(child_value, _stage_color(depth))
            child['color'] = (
                user_cmap.get(child_value)
                or user_cmap.get(stage_label)
                or color
            )

    def _node_color(node: str, depth: int) -> str:
        value = _node_value(node)
        stage_label = stage_labels[depth] if depth < len(stage_labels) else ''
        if value in neutral_values:
            return user_cmap.get(value) or _SK_OTHER_COLOR
        if color_mode == 'stage':
            color = _stage_color(depth)
        elif color_mode == 'first':
            counts = node_first_counts.get(node) or {}
            if counts:
                dominant = max(
                    counts.items(),
                    key=lambda kv: (kv[1], -first_order.get(kv[0], fallback_rank)),
                )[0]
                color = value_colors.get(_node_value(dominant), _stage_color(depth))
            else:
                color = _stage_color(depth)
        else:
            color = value_colors.get(value, _stage_color(depth))
        return user_cmap.get(value) or user_cmap.get(stage_label) or color

    depth_count = max((node_depth.get(n, 0) for n in node_totals), default=0) + 1
    orient = str(work_cfg.get('sk_orient') or 'horizontal').strip().lower()
    if orient not in ('horizontal', 'vertical'):
        orient = 'horizontal'
    node_align = str(work_cfg.get('sk_node_align') or 'justify').strip().lower()
    if node_align not in ('left', 'right', 'justify'):
        node_align = 'justify'
    label_mode = str(work_cfg.get('sk_label_mode') or 'value').strip().lower()
    if label_mode not in ('value', 'full', 'stage'):
        label_mode = 'value'
    show_labels = _as_bool(work_cfg.get('sk_show_labels'), True)
    label_font = _as_int(work_cfg.get('label_font_size'), 10) or 10
    label_min = _as_int(work_cfg.get('sk_label_min_value'), 5)
    if label_min is None:
        label_min = 5
    label_min = max(0, label_min)

    levels = []
    first_pos = 'right' if orient == 'horizontal' else 'bottom'
    last_pos = 'left' if orient == 'horizontal' else 'top'
    for depth in range(depth_count):
        level = {
            'depth': depth,
            'label': {
                'position': last_pos if depth == depth_count - 1 else first_pos,
                'width': 120 if depth == 0 else 88,
                'fontSize': 10 if depth == 0 else 9,
            },
        }
        if color_mode == 'stage':
            level['itemStyle'] = {
                'color': _stage_color(depth),
                'borderColor': '#ffffff',
                'borderWidth': 1,
            }
        levels.append(level)

    nodes = []
    for name in sorted(node_totals, key=_sort_key):
        depth = node_depth.get(name, 0)
        nodes.append({
            'name': name,
            'depth': depth,
            'value': float(node_totals[name]),
            'itemStyle': {
                'color': _node_color(name, depth),
                'borderColor': '#ffffff',
                'borderWidth': 1,
            },
        })

    iterations = _as_int(work_cfg.get('sk_layout_iterations'), 0)
    if iterations is None:
        iterations = 0
    iterations = max(0, min(128, iterations))

    label_cfg = {
        'show': show_labels,
        'fontSize': label_font,
        'color': theme['text'],
        'position': first_pos,
        'overflow': 'truncate',
        'width': 130,
    }
    if show_labels:
        js = [
            "function(p){var v=Number(p.value)||0;",
            "var d=(p.data&&p.data.depth!=null)?p.data.depth:0;",
        ]
        if label_min > 0:
            js.append(f"if(d>0&&v<{label_min})return '';")
        if label_mode == 'value':
            js.append("var s=String(p.name);var i=s.indexOf(' · ');return i>=0?s.slice(i+3):s;")
        elif label_mode == 'stage':
            js.append("var s=String(p.name);var i=s.indexOf(' · ');return i>=0?s.slice(0,i):s;")
        else:
            js.append("return String(p.name);")
        js.append('}')
        label_cfg['__formatter_js__'] = ''.join(js)

    total_students = max(linked, 1)
    tooltip_js = (
        "function(p){"
        "var esc=function(s){return String(s).replace(/[&<>\"']/g,function(c){"
        "return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});};"
        "if(p.dataType==='edge'){"
        "var a=String(p.data.source||'').split(' · ');var b=String(p.data.target||'').split(' · ');"
        "var v=Number(p.value)||0;"
        "return '<b>'+esc(a.slice(1).join(' · '))+' → '+esc(b.slice(1).join(' · '))+'</b><br/>"
        "Students: <b>'+v+'</b> ('+(v/" + str(total_students) + "*100).toFixed(1)+'% of flow)';}"
        "var q=String(p.name||'').split(' · ');var n=Number(p.value)||0;"
        "return '<b>'+esc(q.slice(1).join(' · '))+'</b><br/>'+esc(q[0])+'<br/>Students: <b>'+n+'</b>"
        "<br/><span style=\"color:#64748b\">Click to trace upstream; click Other to expand</span>';}"
    )

    option = _base_option(cfg, axis_trigger=False)
    # A Sankey has no legend/axes; keep the chart area clean while preserving
    # the shared title, palette and toolbox.
    option['legend'] = {'show': False}
    option['tooltip'] = {
        'show': cfg.get('show_tooltip', True) is not False,
        'trigger': 'item',
        '__formatter_js__': tooltip_js,
    }

    # Stage headers are painted above the columns so the flow is readable
    # without a long, wrapping subtitle.
    if orient == 'horizontal' and _as_bool(work_cfg.get('sk_show_stage_flow'), True):
        def _short_header(label: str) -> str:
            low = str(label).lower()
            if 'elementary' in low:
                return 'Elementary'
            if 'middle' in low:
                return 'Middle'
            if 'high' in low:
                return 'High'
            return str(label)

        graphic = []
        for depth in range(depth_count):
            label = stage_labels[depth] if depth < len(stage_labels) else f'Stage {depth + 1}'
            header_text = 'Grade 8 / HS' if depth == middle_idx else _short_header(label)
            item = {
                'type': 'text',
                'top': 50,
                'z': 10,
                'silent': True,
                'style': {
                    'text': header_text,
                    'fill': '#64748b',
                    'fontSize': 10,
                    'fontWeight': 'bold',
                },
            }
            if depth == depth_count - 1:
                # Right-align the last header so it never runs off the canvas.
                item['right'] = '2%'
                item['style']['textAlign'] = 'right'
            else:
                item['left'] = f'{((depth + 0.5) / max(depth_count, 1)) * 100:.4f}%'
                item['style']['textAlign'] = 'center'
            graphic.append(item)
        if graphic:
            option['graphic'] = graphic

    title_shown = bool((option.get('title') or {}).get('show'))
    sankey_top = 74 if (title_shown or option.get('graphic')) else 18
    sankey_bottom = 18
    sankey_side = 18

    option['series'] = [{
        'type': 'sankey',
        'name': str(cfg.get('sk_series_name') or 'Student Flow'),
        'data': nodes,
        'links': links,
        'nodeAlign': node_align,
        'orient': orient,
        'top': sankey_top,
        'bottom': sankey_bottom,
        'left': sankey_side,
        'right': sankey_side,
        'nodeWidth': max(4, _as_int(work_cfg.get('sk_node_width'), 14) or 14),
        'nodeGap': max(2, _as_int(work_cfg.get('sk_node_gap'), 14) or 14),
        'layoutIterations': iterations,
        'draggable': True,
        'emphasis': {'focus': 'adjacency'},
        'lineStyle': {
            'color': link_color_mode,
            'curveness': max(0.0, min(1.0, _as_float(work_cfg.get('sk_curveness'), 0.35))),
            'opacity': max(0.05, min(1.0, _as_float(work_cfg.get('sk_line_opacity'), 0.28))),
        },
        'label': label_cfg,
        'levels': levels,
    }]
    # Standalone/preview renderers use this to reserve enough vertical room for
    # the school bands. Page slots with an explicit height take precedence.
    requested_height = _as_int(cfg.get('container_height'), 0) or 0
    option['__min_height__'] = requested_height if requested_height >= 600 else 900
    option['__category_names__'] = []
    option['__no_drill__'] = True
    if ancestor_payload:
        option['__sk_ancestors__'] = {
            target: {
                'total': entry['total'],
                'ancestors': [
                    [name, int(count)]
                    for name, count in entry['ancestors'].most_common()
                ],
            }
            for target, entry in ancestor_payload.items()
        }
    if other_details:
        option['__sk_other_details__'] = {
            node: [[name, count] for name, count in rows]
            for node, rows in other_details.items()
        }
    if other_expand:
        option['__sk_other_expand__'] = other_expand
    # Expose useful diagnostics to the preview (stripped before rendering).
    option['__sk_diag__'] = {
        'rows_used': linked,
        'rows_skipped': skipped,
        'nodes': len(nodes),
        'links': len(links),
        'stages': list(stage_labels[:depth_count]),
        'color_mode': color_mode,
        'order_mode': order_mode,
        'grouped_to_other': top_per_stage > 0,
    }
    return option



# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
