"""Column management utilities and views."""

import re

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST

from ..models import DefaultColumnSelection
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS
from ..column_utils import get_test_columns, get_mark_columns, get_ap_columns
from ..survey_utils import get_survey_columns
from ..utils import get_user_roles, _apply_de_school_filter

# Virtual columns sourced from current_student.add_data JSON.
# List keys (array or comma-string) vs scalar keys (text / bool / number).
JSON_LIST_COLUMNS = {
    'race_list': 'l_race',
}
JSON_SCALAR_COLUMNS = {
    'avid': 'AVID',
    'avid_type': 'AVID_type',
    'dsusd_fep': 'DSUSD_FEP',
}
JSON_LIST_KEYS = frozenset(JSON_LIST_COLUMNS.values())
_JSON_VIRTUAL_AUTO_INCLUDE = {
    'race_list': ('race',),
    'dsusd_fep': ('ell', 'fep_date'),
}
_JSON_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

RACE_LIST_COL = 'race_list'
L_RACE_JSON_KEY = 'l_race'
DSUSD_FEP_COL = 'dsusd_fep'
DSUSD_FEP_JSON_KEY = 'DSUSD_FEP'


def get_saved_default_columns():
    """
    Return the active default column list, with three-level fallback:
      1. ProjectConfig.default_columns  (project-admin-controlled)
      2. DefaultColumnSelection table   (legacy)
      3. DEFAULT_COLUMNS constant       (hard-coded safe default)
    """
    try:
        from landing.utils import get_project_config
        cfg = get_project_config()
        if cfg.default_columns:
            return cfg.default_columns
    except Exception:
        pass

    selection = DefaultColumnSelection.objects.order_by('-updated_at', '-id').first()
    if selection and selection.columns:
        return selection.columns
    return DEFAULT_COLUMNS


def get_enabled_columns():
    """
    Return (definitions, categories) filtered against ProjectConfig.enabled_columns.

    If ProjectConfig.enabled_columns is None or empty, returns the full
    COLUMN_DEFINITIONS and COLUMN_CATEGORIES unchanged (safe default — no
    disruption to existing behaviour).

    When an allowlist includes a related column (e.g. ``race`` or ``ell``) but
    not the matching JSON-list virtual column, that virtual column is
    auto-included so it appears for allowlists that predate it.

    Returns:
        tuple[dict, dict]: (filtered_definitions, filtered_categories)
    """
    try:
        from landing.utils import get_project_config
        cfg = get_project_config()
        allowlist = cfg.enabled_columns  # list of keys or None
    except Exception:
        allowlist = None

    if not allowlist:
        return COLUMN_DEFINITIONS, COLUMN_CATEGORIES

    allowed_set = set(allowlist)
    for virt, related in _JSON_VIRTUAL_AUTO_INCLUDE.items():
        if virt not in allowed_set and any(r in allowed_set for r in related):
            allowed_set.add(virt)

    filtered_defs = {k: v for k, v in COLUMN_DEFINITIONS.items() if k in allowed_set}

    filtered_cats = {}
    for cat_key, cat in COLUMN_CATEGORIES.items():
        cols = [c for c in cat['columns'] if c in allowed_set]
        if cols:
            filtered_cats[cat_key] = {**cat, 'columns': cols}

    return filtered_defs, filtered_cats


def _safe_json_key(json_key: str) -> str:
    if not json_key or not _JSON_KEY_RE.fullmatch(json_key):
        raise ValueError(f'Invalid add_data JSON key: {json_key!r}')
    return json_key


def json_list_key_for_column(col: str) -> str | None:
    """Return the add_data JSON key for a virtual column or add_data->'key' path."""
    if not col or not isinstance(col, str):
        return None
    bare = col.split('.')[-1]
    if bare in JSON_LIST_COLUMNS:
        return JSON_LIST_COLUMNS[bare]
    for key in JSON_LIST_KEYS:
        if f"add_data->'{key}'" in col or f'add_data->"{key}"' in col:
            return key
    return None


def is_json_list_column(col: str) -> bool:
    return json_list_key_for_column(col) is not None


def is_race_list_column(col: str) -> bool:
    """True for the Race List virtual column or raw add_data->'l_race' paths."""
    return json_list_key_for_column(col) == L_RACE_JSON_KEY


def race_list_add_data_ref(table_alias: str = 'cs') -> str:
    """Return a SQL reference to the add_data column (alias or "table"."add_data")."""
    if table_alias.startswith('"'):
        return f'{table_alias}."add_data"'
    return f'{table_alias}.add_data'


def known_add_data_json_paths():
    """Dataset-builder paths for virtual add_data keys (always listed)."""
    keys = list(JSON_LIST_COLUMNS.values()) + list(JSON_SCALAR_COLUMNS.values())
    return [f"add_data->'{k}'" for k in keys]


def json_scalar_expr(col: str, table_alias: str = 'cs') -> str:
    """Text extract of a scalar add_data JSON key (string, bool, or number)."""
    key = _safe_json_key(JSON_SCALAR_COLUMNS[col])
    return f"({race_list_add_data_ref(table_alias)}::jsonb->>'{key}')"


def json_list_array_sql(json_key: str, table_alias: str = 'cs') -> str:
    """
    Normalize add_data->'key' to a text[] safely.

    Supports:
      - JSON array of scalars: ["White", "Asian"]
      - JSON string (single or comma-separated): "White" / "White, Asian"
    """
    key = _safe_json_key(json_key)
    ref = race_list_add_data_ref(table_alias)
    return (
        f"(CASE "
        f"WHEN {ref} IS NULL OR {ref}->'{key}' IS NULL OR jsonb_typeof({ref}->'{key}') = 'null' "
        f"THEN ARRAY[]::text[] "
        f"WHEN jsonb_typeof({ref}->'{key}') = 'array' "
        f"THEN ARRAY(SELECT jsonb_array_elements_text({ref}->'{key}')) "
        f"WHEN jsonb_typeof({ref}->'{key}') = 'string' AND BTRIM(COALESCE({ref}->>'{key}', '')) <> '' "
        f"THEN regexp_split_to_array(BTRIM({ref}->>'{key}'), '[[:space:]]*,[[:space:]]*') "
        f"ELSE ARRAY[]::text[] END)"
    )


def json_list_display_expr(json_key: str, table_alias: str = 'cs') -> str:
    """Comma-joined strings for SELECT / display."""
    return f"array_to_string({json_list_array_sql(json_key, table_alias)}, ', ')"


def json_list_overlap_sql(json_key: str, table_alias: str = 'cs') -> str:
    """Array-overlap against filter values; caller supplies ONE %s text[] param."""
    return f"({json_list_array_sql(json_key, table_alias)} && %s::text[])"


def json_list_blank_sql(json_key: str, table_alias: str = 'cs') -> str:
    """True when the JSON key is missing or yields no values."""
    return f"(cardinality({json_list_array_sql(json_key, table_alias)}) = 0)"


def append_json_list_filter(where_clauses, params, values, json_key: str, table_alias: str = 'cs') -> None:
    """Append any-of overlap filter (plus optional blank) for a JSON-list column."""
    if not values:
        return
    has_blank = "" in values or '(Blank)' in values
    real_values = [v for v in values if v not in ("", "(Blank)")]
    conditions = []
    if real_values:
        conditions.append(json_list_overlap_sql(json_key, table_alias))
        params.append(list(real_values))
    if has_blank:
        conditions.append(json_list_blank_sql(json_key, table_alias))
    if conditions:
        where_clauses.append(f"({' OR '.join(conditions)})")


def json_list_unnest_select_expr(json_key: str, table_alias: str = 'cs') -> str:
    """Set-returning expression of individual values (for DISTINCT options)."""
    return f"unnest({json_list_array_sql(json_key, table_alias)})"


def race_list_array_sql(table_alias: str = 'cs') -> str:
    return json_list_array_sql(L_RACE_JSON_KEY, table_alias)


def race_list_display_expr(table_alias: str = 'cs') -> str:
    """Comma-joined race strings for SELECT / display."""
    return json_list_display_expr(L_RACE_JSON_KEY, table_alias)


def race_list_overlap_sql(table_alias: str = 'cs') -> str:
    """Array-overlap against filter values; caller supplies ONE %s text[] param."""
    return json_list_overlap_sql(L_RACE_JSON_KEY, table_alias)


def race_list_blank_sql(table_alias: str = 'cs') -> str:
    """True when l_race is missing or yields no races."""
    return json_list_blank_sql(L_RACE_JSON_KEY, table_alias)


def append_race_list_filter(where_clauses, params, values, table_alias: str = 'cs') -> None:
    """Append any-of race overlap filter (plus optional blank) for Race List."""
    append_json_list_filter(where_clauses, params, values, L_RACE_JSON_KEY, table_alias)


def race_list_unnest_select_expr(table_alias: str = 'cs') -> str:
    """Set-returning expression of individual race values (for DISTINCT options)."""
    return json_list_unnest_select_expr(L_RACE_JSON_KEY, table_alias)


def get_column_expression(col, db_columns, enroll_columns=None, use_enroll=False, use_students=False, use_eht=False):
    """Generate SQL expression for a column based on available tables."""
    if col == 'elem_teacher_name' and use_eht:
        return "eht.elem_teacher_name", "elem_teacher_name"
    if col == 'ltel' and use_students:
        return "COALESCE(s.ltel, sssid.ltel)", "ltel"
    if col == 'ell':
        ell_candidates = []
        if 'ell' in db_columns:
            ell_candidates.append("cs.ell")
        if use_enroll and enroll_columns:
            if 'ell_status' in enroll_columns:
                ell_candidates.append("se.ell_status")
        if ell_candidates:
            return f"COALESCE({', '.join(ell_candidates)})", "ell"
        return None, None
    if col == 'atendace_rate' and 'attendance_rate' in db_columns:
        return "cs.attendance_rate", "atendace_rate"
    if col in {'birth_date', 'us_enter_date', 'fep_date'}:
        if col in db_columns:
            return f"cs.{col}", col
        if use_enroll and enroll_columns and col in enroll_columns:
            return f"se.{col}", col
        return None, None
    if col == 'migrant':
        if col in db_columns:
            return "cs.migrant", "migrant"
        if use_enroll and enroll_columns and col in enroll_columns:
            return "se.migrant", "migrant"
        return None, None
    if col in JSON_SCALAR_COLUMNS:
        return json_scalar_expr(col), col
    json_key = JSON_LIST_COLUMNS.get(col)
    if json_key:
        return json_list_display_expr(json_key, 'cs'), col
    if col in db_columns:
        return f"cs.{col}", col
    return None, None


def user_can_manage_defaults(user):
    """Check if user has permission to manage default columns."""
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    user_roles = get_user_roles(user)
    return bool({'Project Admin', 'District Admin'} & user_roles)


@login_required
def manage_default_columns(request):
    """Admin interface for setting default columns shown to users."""
    if not user_can_manage_defaults(request.user):
        return HttpResponseForbidden("You don't have permission to manage default columns.")

    test_structure = get_test_columns()
    default_columns = get_saved_default_columns()

    if request.method == 'POST':
        import json
        columns_json = request.POST.get('columns', '[]')
        try:
            try:
                columns = json.loads(columns_json)
                if isinstance(columns, str):
                    columns = [columns]
            except (json.JSONDecodeError, TypeError, ValueError):
                columns = request.POST.getlist('columns')

            selection, created = DefaultColumnSelection.objects.update_or_create(
                id=1,
                defaults={'columns': columns}
            )
            
            from dataexploration.school_year_utils import get_year_labels_map, get_year_alias_map
            return render(request, 'dataexploration/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'selected_columns': columns,
                'year_labels': get_year_labels_map(list(test_structure.keys())),
                'year_to_alias': get_year_alias_map(),
                'success': True,
            })
        except Exception as e:
            from dataexploration.school_year_utils import get_year_labels_map, get_year_alias_map
            return render(request, 'dataexploration/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'selected_columns': default_columns,
                'year_labels': get_year_labels_map(list(test_structure.keys())),
                'year_to_alias': get_year_alias_map(),
                'error': str(e),
            })

    from dataexploration.school_year_utils import get_year_labels_map, get_year_alias_map
    context = {
        'column_categories': COLUMN_CATEGORIES,
        'column_definitions': COLUMN_DEFINITIONS,
        'test_structure': test_structure,
        'selected_columns': default_columns,
        'year_labels': get_year_labels_map(list(test_structure.keys())),
        'year_to_alias': get_year_alias_map(),
    }

    return render(request, 'dataexploration/manage_default_columns.html', context)


def load_column_structures(request):
    """Load dynamic column structures for the selector via HTMX."""
    import json
    
    filters_json = request.GET.get('column_filters')
    column_filters = None
    if filters_json:
        try:
            column_filters = json.loads(filters_json)
        except json.JSONDecodeError:
            pass

    # Build school filtering based on user permissions
    where_clauses = []
    params = []
    user = request.user
    
    _apply_de_school_filter(where_clauses, params, user)

    test_structure = get_test_columns(
        column_filters=column_filters,
        extra_where_clauses=where_clauses,
        extra_params=params
    )
    marks_structure = get_mark_columns(
        column_filters=column_filters,
        extra_where_clauses=where_clauses,
        extra_params=params
    )
    survey_structure = get_survey_columns()
    ap_structure = get_ap_columns()
    
    from dataexploration.school_year_utils import get_year_labels_map, get_year_alias_map

    all_years = list(test_structure.keys()) + list(marks_structure.keys()) + list(ap_structure.keys())
    context = {
        'test_structure': test_structure,
        'marks_structure': marks_structure,
        'survey_structure': survey_structure.get('structure', {}),
        'ap_structure': ap_structure,
        'column_definitions': COLUMN_DEFINITIONS,
        'year_labels': get_year_labels_map(all_years),
        'year_to_alias': get_year_alias_map(),
    }
    
    return render(request, 'dataexploration/partials/column_structures.html', context)
