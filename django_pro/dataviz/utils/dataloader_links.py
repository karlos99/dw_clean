"""DataLoader → Dataset Builder link registry and SQL builders.

Guided joins attach ClickHouse analytics mirrors (synced from dataLoader) to a
base student table on SSID and school year where applicable. Survey links emit
a pivot subquery so selected questions appear as wide ``survey_*`` columns
matching Data Explorer keys.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from dataexploration.survey_utils import get_survey_columns

LINK_TYPE_AP_RESULTS = 'ap_results'
LINK_TYPE_AP_AWARDS = 'ap_awards'
LINK_TYPE_SAT_RESULTS = 'sat_results'
LINK_TYPE_SURVEY = 'survey'

ANALYTICS_TABLE_BY_LINK = {
    LINK_TYPE_AP_RESULTS: 'analytics_ap_results',
    LINK_TYPE_AP_AWARDS: 'analytics_ap_awards',
    LINK_TYPE_SAT_RESULTS: 'analytics_sat_results',
}

AP_RESULTS_COLUMNS = frozenset({'ssid', 'admin_year', 'exam_name', 'exam_grade', 'exam_id'})
AP_AWARDS_COLUMNS = frozenset({'ssid', 'award_year', 'award_description'})
SAT_RESULTS_COLUMNS = frozenset({
    'ssid', 'school_year', 'test_date', 'school_name',
    'total_score', 'ebrw_score', 'math_score',
    'percentile_state_total', 'percentile_state_rw', 'percentile_state_math',
    'percentile_country_total', 'percentile_country_rw', 'percentile_country_math',
    'percentile_natrep_total', 'percentile_natrep_rw', 'percentile_natrep_math',
    'percentile_natuser_total', 'percentile_natuser_rw', 'percentile_natuser_math',
})

_SURVEY_LINK_SEP = '|'

SCHOOL_YEAR_JOIN_MATCH_BASE = 'match_base'
SCHOOL_YEAR_JOIN_SSID_ONLY = 'ssid_only'
_YEAR_FIXED_RE = re.compile(r'^\d{4}-\d{4}$')


def normalize_school_year_join(mode: str | None) -> str:
    """Return match_base, ssid_only, or a fixed YYYY-YYYY year string."""
    if not mode or mode == SCHOOL_YEAR_JOIN_MATCH_BASE:
        return SCHOOL_YEAR_JOIN_MATCH_BASE
    if mode == SCHOOL_YEAR_JOIN_SSID_ONLY:
        return SCHOOL_YEAR_JOIN_SSID_ONLY
    mode = str(mode).strip()
    if _YEAR_FIXED_RE.fullmatch(mode):
        return mode
    return SCHOOL_YEAR_JOIN_MATCH_BASE


def _analytics_year_column_expr(link_type: str, target: str) -> str | None:
    if link_type == LINK_TYPE_AP_RESULTS:
        return f'"{target}".admin_year'
    if link_type == LINK_TYPE_AP_AWARDS:
        return f'"{target}".award_year'
    if link_type == LINK_TYPE_SAT_RESULTS:
        return f'"{target}".school_year'
    return None


def build_table_year_join_on(
    link_type: str,
    base_table: str,
    target: str,
    school_year_join: str | None,
) -> str | None:
    """Extra ON predicate for AP/SAT year matching (SSID is always a normal condition)."""
    mode = normalize_school_year_join(school_year_join)
    if mode == SCHOOL_YEAR_JOIN_SSID_ONLY:
        return None
    right = _analytics_year_column_expr(link_type, target)
    if not right:
        return None
    if mode == SCHOOL_YEAR_JOIN_MATCH_BASE:
        sy = _base_school_year_column(base_table)
        if not sy:
            return None
        return school_year_match_expr(format_table_col(sy), right)
    # Fixed import year
    left_literal = f"'{mode}'"
    return school_year_match_expr(left_literal, right)


def _safe_sql_ident(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name or ''):
        raise ValueError(f'Invalid SQL identifier: {name!r}')
    return name


def survey_link_id(survey_type: str, school_year: str, window: str, grade_range: str) -> str:
    parts = [survey_type or '', school_year or '', window or '', grade_range or '']
    return _SURVEY_LINK_SEP.join(parts)


def parse_survey_link_id(link_id: str) -> dict[str, str]:
    parts = (link_id or '').split(_SURVEY_LINK_SEP)
    while len(parts) < 4:
        parts.append('')
    return {
        'survey_type': parts[0],
        'school_year': parts[1],
        'window': parts[2],
        'grade_range': parts[3],
    }


def survey_virtual_alias(link_id: str) -> str:
    digest = hashlib.md5((link_id or '').encode()).hexdigest()[:12]
    return f'dl_survey_{digest}'


def school_year_match_expr(left_expr: str, right_expr: str) -> str:
    """Match school_year (YYYY-YYYY) to admin_year or other year formats."""
    left4 = f"substring({left_expr} FROM 1 FOR 4)"
    right4 = f"substring({right_expr} FROM 1 FOR 4)"
    return (
        f"({left_expr} = {right_expr} "
        f"OR {left4} = {right_expr} "
        f"OR {right4} = {left4} "
        f"OR {right_expr} = {left4})"
    )


def _base_ssid_column(base_table: str) -> str:
    return f'{base_table}.ssid'


def _base_school_year_column(base_table: str) -> str | None:
    if base_table in (
        'current_student', 'student_enroll', 'analytics_student_profile',
        'results', 'student_marks_list', 'schedule_list',
    ):
        return f'{base_table}.school_year'
    return None


def columns_for_link_type(link_type: str) -> frozenset[str]:
    if link_type == LINK_TYPE_AP_RESULTS:
        return AP_RESULTS_COLUMNS
    if link_type == LINK_TYPE_AP_AWARDS:
        return AP_AWARDS_COLUMNS
    if link_type == LINK_TYPE_SAT_RESULTS:
        return SAT_RESULTS_COLUMNS
    return frozenset()


def required_analytics_table(link_type: str) -> str | None:
    if link_type == LINK_TYPE_SURVEY:
        return 'analytics_survey_responses'
    return ANALYTICS_TABLE_BY_LINK.get(link_type)


def check_link_allowed(link_type: str, allowed_tables: dict) -> str | None:
    """Return error message if analytics table is blocked by project allowlist."""
    tbl = required_analytics_table(link_type)
    if not tbl:
        return f'Unknown DataLoader link type: {link_type}'
    if tbl in allowed_tables:
        return None
    return (
        f'DataLoader link requires table "{tbl}", which is not enabled in '
        'Project Settings → Allowed Dataset Tables.'
    )


def build_table_join_spec(link_type: str, base_table: str, join_type: str = 'left') -> dict[str, Any]:
    """Preset join for AP / SAT analytics tables."""
    target = ANALYTICS_TABLE_BY_LINK[link_type]
    base_ssid = _base_ssid_column(base_table)
    conditions = [{'on_left': base_ssid, 'on_right': f'{target}.ssid'}]

    if link_type == LINK_TYPE_AP_RESULTS:
        year_left = _base_school_year_column(base_table)
        if year_left:
            # Stored as raw ON fragment extension — compiler uses conditions only;
            # year match added via extra_on in expanded join.
            pass
    elif link_type == LINK_TYPE_AP_AWARDS:
        year_left = _base_school_year_column(base_table)
        if year_left:
            pass
    return {
        'kind': 'dataloader',
        'link_type': link_type,
        'table': target,
        'join_type': join_type or 'left',
        'conditions': conditions,
    }


def _survey_meta_for_link(link_id: str, question_keys: list[str]) -> tuple[dict, list[dict]]:
    parsed = parse_survey_link_id(link_id)
    survey_data = get_survey_columns()
    col_meta = survey_data.get('col_meta') or {}
    definitions = survey_data.get('definitions') or {}

    questions = []
    for key in question_keys:
        meta = col_meta.get(key)
        if not meta:
            continue
        if (
            meta.get('survey_type') != parsed['survey_type']
            or meta.get('school_year') != parsed['school_year']
            or meta.get('window') != parsed['window']
            or (meta.get('grade_range') or '') != parsed['grade_range']
        ):
            continue
        questions.append({
            'key': key,
            'label': definitions.get(key, key),
            'question_order': meta['question_order'],
        })
    if not questions:
        raise ValueError('No valid survey questions selected for this link.')
    return parsed, questions


def build_survey_pivot_subquery(
    link_id: str,
    question_keys: list[str],
) -> tuple[str, str, set[str], list[Any]]:
    """
    Returns (subquery_sql_without_outer_parens, virtual_alias, column_names, params).
    Uses ClickHouse maxIf pivot; compatible with QueryExecutor translation when on PG.
    """
    parsed, questions = _survey_meta_for_link(link_id, question_keys)
    alias = survey_virtual_alias(link_id)
    col_names: set[str] = set()

    pivot_parts = []
    for q in questions:
        key = q['key']
        _safe_sql_ident(key)
        order = int(q['question_order'])
        pivot_parts.append(f"maxIf(value, question_order = {order}) AS \"{key}\"")
        col_names.add(key)

    pivot_sql = ', '.join(pivot_parts)
    params = [
        parsed['survey_type'],
        parsed['school_year'],
        parsed['window'],
        parsed['grade_range'],
    ]

    subquery = f"""
        SELECT ssid, school_year, {pivot_sql}
        FROM analytics_survey_responses
        WHERE survey_type = %s
          AND school_year = %s
          AND testing_window = %s
          AND grade_range = %s
        GROUP BY ssid, school_year
    """.strip()

    return subquery, alias, col_names, params


def build_survey_join_on(
    base_table: str,
    virtual_alias: str,
    school_year_join: str | None = None,
) -> tuple[str, list[Any]]:
    """ON clause connecting base table to survey pivot subquery."""
    base_ssid = _base_ssid_column(base_table)
    parts = [f'"{virtual_alias}".ssid = {format_table_col(base_ssid)}']
    mode = normalize_school_year_join(school_year_join)
    if mode == SCHOOL_YEAR_JOIN_SSID_ONLY:
        return ' AND '.join(parts), []
    sy = _base_school_year_column(base_table)
    left = f'"{virtual_alias}".school_year'
    if mode == SCHOOL_YEAR_JOIN_MATCH_BASE and sy:
        right = format_table_col(sy)
        parts.append(school_year_match_expr(left, right))
    elif _YEAR_FIXED_RE.fullmatch(mode):
        parts.append(school_year_match_expr(f"'{mode}'", left))
    return ' AND '.join(parts), []


def format_table_col(qualified: str) -> str:
    tbl, col = qualified.split('.', 1)
    return f'"{tbl}"."{col}"'


def expand_dataloader_joins(
    joins_list: list[dict],
    base_table: str,
) -> tuple[list[dict], list[str], dict[str, set[str]], list[Any]]:
    """
    Expand kind=dataloader joins for SQL compilation.

    Returns:
      - table_joins: normal joins for physical analytics tables
      - raw_join_sql_fragments: full JOIN clauses (survey pivot subqueries)
      - virtual_columns: alias -> allowed column names
      - extra_params: parameters appended for survey inner filters (in order)
    """
    table_joins: list[dict] = []
    raw_fragments: list[str] = []
    virtual_columns: dict[str, set[str]] = {}
    extra_params: list[Any] = []

    for j in joins_list or []:
        if j.get('kind') != 'dataloader':
            table_joins.append(j)
            continue

        link_type = j.get('link_type') or ''
        join_type = (j.get('join_type') or 'left').lower()
        join_keyword = {
            'left': 'LEFT JOIN',
            'inner': 'INNER JOIN',
            'right': 'RIGHT JOIN',
        }.get(join_type, 'LEFT JOIN')

        year_mode = j.get('school_year_join')

        if link_type == LINK_TYPE_SURVEY:
            link_id = j.get('link_id') or ''
            question_keys = list(j.get('columns') or [])
            subquery, alias, cols, params = build_survey_pivot_subquery(link_id, question_keys)
            extra_params.extend(params)
            virtual_columns[alias] = set(cols) | {'ssid', 'school_year'}
            on_sql, _ = build_survey_join_on(base_table, alias, year_mode)
            raw_fragments.append(f'{join_keyword} ({subquery}) AS "{alias}" ON {on_sql}')
            continue

        target = ANALYTICS_TABLE_BY_LINK.get(link_type)
        if not target:
            raise ValueError(f'Unsupported DataLoader link_type: {link_type}')

        spec = build_table_join_spec(link_type, base_table, join_type)
        conditions = list(spec.get('conditions') or [])
        extra_on = build_table_year_join_on(link_type, base_table, target, year_mode)

        table_joins.append({
            'table': target,
            'join_type': join_type,
            'conditions': conditions,
            '_extra_on': extra_on,
            'kind': 'dataloader',
            'link_type': link_type,
            'school_year_join': normalize_school_year_join(year_mode),
        })

    return table_joins, raw_fragments, virtual_columns, extra_params


def build_survey_catalog() -> list[dict]:
    """Published surveys as attachable link entries."""
    data = get_survey_columns()
    structure = data.get('structure') or {}
    definitions = data.get('definitions') or {}
    out = []

    for school_year, type_map in structure.items():
        for _type_grade_key, survey_data in type_map.items():
            survey_type = survey_data.get('survey_type') or _type_grade_key.split('|')[0]
            grade_range = survey_data.get('grade_range') or ''
            for window, question_dict in (survey_data.get('windows') or {}).items():
                link_id = survey_link_id(survey_type, school_year, window, grade_range)
                label_parts = [survey_type, school_year, window]
                if grade_range:
                    label_parts.append(grade_range)
                questions = []
                for _order, q in sorted((question_dict or {}).items()):
                    key = q.get('key')
                    if not key:
                        continue
                    questions.append({
                        'key': key,
                        'label': q.get('label') or definitions.get(key, key),
                        'topic': q.get('domain') or '',
                    })
                if questions:
                    out.append({
                        'link_type': LINK_TYPE_SURVEY,
                        'link_id': link_id,
                        'label': ' — '.join(label_parts),
                        'school_year': school_year,
                        'window': window,
                        'grade_range': grade_range,
                        'survey_type': survey_type,
                        'questions': questions,
                    })
    return out


def build_ap_catalog() -> dict:
    return {
        'link_type': LINK_TYPE_AP_RESULTS,
        'label': 'AP Exam Results',
        'table': 'analytics_ap_results',
        'columns': sorted(AP_RESULTS_COLUMNS),
        'join_keys': ['ssid', 'admin_year ↔ school_year'],
        'note': 'May produce multiple rows per student (one per exam).',
    }


def build_ap_awards_catalog() -> dict:
    return {
        'link_type': LINK_TYPE_AP_AWARDS,
        'label': 'AP Student Awards',
        'table': 'analytics_ap_awards',
        'columns': sorted(AP_AWARDS_COLUMNS),
        'join_keys': ['ssid', 'award_year ↔ school_year'],
    }


def build_sat_catalog() -> dict:
    return {
        'link_type': LINK_TYPE_SAT_RESULTS,
        'label': 'SAT Results',
        'table': 'analytics_sat_results',
        'columns': sorted(SAT_RESULTS_COLUMNS),
        'join_keys': ['ssid', 'school_year'],
    }


def _collect_school_years(surveys: list[dict]) -> list[str]:
    years = {s.get('school_year') for s in surveys if s.get('school_year')}
    return sorted(years, reverse=True)


def _format_testing_window(window: str | None) -> str:
    if not window:
        return ''
    w = str(window).strip()
    if w.upper() == w and len(w) > 2:
        return w.title()
    return w


def _survey_variant_label(survey: dict) -> str:
    """Human-readable window + grade band for one import row."""
    parts = []
    win = _format_testing_window(survey.get('window'))
    if win:
        parts.append(win)
    gr = (survey.get('grade_range') or '').strip()
    if gr:
        parts.append(gr)
    return ' · '.join(parts) if parts else 'All students'


def build_survey_toolbox_groups(surveys: list[dict]) -> list[dict]:
    """
    Group published survey imports by survey type + school year so the builder
    sidebar shows one card per survey program with draggable variants inside.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for survey in surveys:
        survey_type = (survey.get('survey_type') or 'Survey').strip()
        school_year = (survey.get('school_year') or '').strip()
        bucket_key = (survey_type, school_year)
        if bucket_key not in buckets:
            safe_id = hashlib.md5(f'{survey_type}|{school_year}'.encode()).hexdigest()[:10]
            buckets[bucket_key] = {
                'group_id': f'survey_grp_{safe_id}',
                'survey_type': survey_type,
                'school_year': school_year,
                'title': survey_type,
                'subtitle': school_year,
                'items': [],
            }
        variant = _survey_variant_label(survey)
        qcount = len(survey.get('questions') or [])
        link_id = survey['link_id']
        buckets[bucket_key]['items'].append({
            'drag_id': f'survey:{link_id}',
            'link_type': LINK_TYPE_SURVEY,
            'link_id': link_id,
            'label': f'{survey_type} ({school_year}) — {variant}',
            'short_label': variant,
            'display_name': f'{survey_type} · {school_year} · {variant}',
            'table': survey_virtual_alias(link_id),
            'default_school_year': school_year,
            'questions': survey.get('questions') or [],
            'question_count': qcount,
            'survey_type': survey_type,
            'school_year': school_year,
        })

    groups = list(buckets.values())
    for group in groups:
        group['items'].sort(key=lambda row: (row.get('short_label') or '').lower())
    groups.sort(
        key=lambda g: (
            g.get('school_year') or '',
            (g.get('survey_type') or '').lower(),
        ),
        reverse=True,
    )
    return groups


def build_fixed_loader_toolbox_items(catalog_data: dict) -> list[dict]:
    """Non-survey Data Loader sources (AP, SAT)."""
    ap = catalog_data.get('ap_results') or {}
    sat = catalog_data.get('sat_results') or {}
    return [
        {
            'drag_id': 'ap_results',
            'link_type': LINK_TYPE_AP_RESULTS,
            'label': ap.get('label') or 'AP Exam Results',
            'display_name': ap.get('label') or 'AP Exam Results',
            'table': ap.get('table') or 'analytics_ap_results',
            'columns': ap.get('columns') or sorted(AP_RESULTS_COLUMNS),
            'default_school_year': '',
        },
        {
            'drag_id': 'sat_results',
            'link_type': LINK_TYPE_SAT_RESULTS,
            'label': sat.get('label') or 'SAT Results',
            'display_name': sat.get('label') or 'SAT Results',
            'table': sat.get('table') or 'analytics_sat_results',
            'columns': sat.get('columns') or sorted(SAT_RESULTS_COLUMNS),
            'default_school_year': '',
        },
    ]


def build_drag_toolbox_items(catalog_data: dict | None = None) -> list[dict]:
    """Flat draggable list (surveys + AP + SAT) for backwards compatibility."""
    data = catalog_data or catalog()
    items: list[dict] = []
    for group in build_survey_toolbox_groups(data.get('surveys') or []):
        items.extend(group['items'])
    items.extend(build_fixed_loader_toolbox_items(data))
    return items


def catalog() -> dict:
    surveys = build_survey_catalog()
    return {
        'surveys': surveys,
        'ap_results': build_ap_catalog(),
        'ap_awards': build_ap_awards_catalog(),
        'sat_results': build_sat_catalog(),
        'school_years': _collect_school_years(surveys),
    }


def suggest_attach_payload(
    link_type: str,
    base_table: str,
    *,
    link_id: str = '',
    selected_columns: list[str] | None = None,
    join_type: str = 'left',
    school_year_join: str | None = None,
) -> dict:
    """Join JSON + column qualified names for the dataset builder UI."""
    selected_columns = list(selected_columns or [])
    year_mode = normalize_school_year_join(school_year_join)
    join_entry: dict[str, Any] = {
        'kind': 'dataloader',
        'link_type': link_type,
        'join_type': join_type or 'left',
        'school_year_join': year_mode,
    }

    if link_type == LINK_TYPE_SURVEY:
        join_entry['link_id'] = link_id
        join_entry['columns'] = list(selected_columns)
        alias = survey_virtual_alias(link_id)
        join_entry['table'] = alias
        join_entry['conditions'] = [{
            'on_left': f'{base_table}.ssid',
            'on_right': f'{alias}.ssid',
        }]
        qualified = [f'{alias}.{c}' for c in selected_columns]
        return {'join': join_entry, 'columns': qualified, 'virtual_table': alias}

    target = ANALYTICS_TABLE_BY_LINK[link_type]
    spec = build_table_join_spec(link_type, base_table, join_type)
    join_entry['table'] = target
    join_entry['conditions'] = spec['conditions']
    if not selected_columns:
        selected_columns = sorted(columns_for_link_type(link_type) - {'ssid'})
    join_entry['columns'] = list(selected_columns)
    qualified = [f'{target}.{c}' for c in selected_columns]
    return {'join': join_entry, 'columns': qualified, 'virtual_table': target}
