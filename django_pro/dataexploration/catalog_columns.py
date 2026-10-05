"""Course catalog / CTE pathway columns for the Data Explorer (optional, read-only)."""
from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from clickhouse_utils.course_program_sync import DIMENSION_BY_COLUMN

COURSE_PROGRAM_DEFINITIONS = {
    'course_prog_tag': 'Tag',
    'course_prog_category': 'Category',
    'course_prog_discipline': 'Discipline',
    'course_prog_level': 'Level',
}

# Legacy keys from earlier 8-column layout (saved queries / bookmarks).
_LEGACY_COLUMN_ALIASES = {
    'course_prog_staff_tag': 'course_prog_tag',
    'course_prog_staff_category': 'course_prog_category',
    'course_prog_staff_discipline': 'course_prog_discipline',
    'course_prog_staff_level': 'course_prog_level',
    'course_prog_cte_cluster': 'course_prog_category',
    'course_prog_cte_pathway': 'course_prog_discipline',
    'course_prog_cte_level': 'course_prog_level',
}

COURSE_PROGRAM_CATEGORY = {
    'title': 'Course Programs',
    'columns': list(COURSE_PROGRAM_DEFINITIONS.keys()),
}

COURSE_PROGRAM_PREFIX = 'course_prog_'


def course_program_allowlist_keys() -> frozenset[str]:
    """All column keys accepted for course program selection (includes legacy aliases)."""
    return frozenset(COURSE_PROGRAM_DEFINITIONS) | frozenset(_LEGACY_COLUMN_ALIASES)


def course_program_columns_enabled() -> bool:
    try:
        from landing.utils import get_project_config
        return bool(get_project_config().enable_course_program_columns)
    except Exception:
        return False


def merge_course_program_columns(definitions: dict, categories: dict) -> tuple[dict, dict]:
    """Insert Course Programs category immediately after Performance & Behavior."""
    if not course_program_columns_enabled():
        return definitions, categories
    merged_defs = {**definitions, **COURSE_PROGRAM_DEFINITIONS}
    merged_cats: dict = {}
    inserted = False
    for key, cat in categories.items():
        merged_cats[key] = cat
        if key == 'performance':
            merged_cats['course_programs'] = dict(COURSE_PROGRAM_CATEGORY)
            inserted = True
    if not inserted:
        merged_cats['course_programs'] = dict(COURSE_PROGRAM_CATEGORY)
    return merged_defs, merged_cats


def is_course_program_column(col: str) -> bool:
    if not isinstance(col, str) or not col.startswith(COURSE_PROGRAM_PREFIX):
        return False
    if col in COURSE_PROGRAM_DEFINITIONS:
        return True
    return col in _LEGACY_COLUMN_ALIASES


def canonical_course_program_column(col: str) -> str | None:
    if col in COURSE_PROGRAM_DEFINITIONS:
        return col
    if col in _LEGACY_COLUMN_ALIASES:
        return _LEGACY_COLUMN_ALIASES[col]
    return None


def mirror_legacy_course_program_filter_options(
    filter_options: dict,
    selected_cols: list[str],
) -> None:
    """Saved queries may use legacy column keys; reuse canonical option lists."""
    for legacy_key, canonical in _LEGACY_COLUMN_ALIASES.items():
        if legacy_key in selected_cols and canonical in filter_options:
            filter_options[legacy_key] = filter_options[canonical]


def filters_without_course_program_dimension(filters: dict | None, canonical: str) -> dict:
    """Faceted filter options: drop filters on the same Tag/Category/Discipline/Level dimension."""
    if not isinstance(filters, dict):
        return {}
    return {
        k: v for k, v in filters.items()
        if canonical_course_program_column(k) != canonical
    }


def normalize_course_program_columns(selected_cols: list[str]) -> list[str]:
    """Map legacy column keys to the unified Tag/Category/Discipline/Level keys."""
    out: list[str] = []
    seen: set[str] = set()
    for col in selected_cols:
        if not is_course_program_column(col):
            continue
        canonical = _LEGACY_COLUMN_ALIASES.get(col, col)
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


# Schedule rows representing a student's actual enrollment: current-year rows are
# 'Active', historical years are imported as 'Completed'. 'Pending' rows are
# future/not-yet-finalized schedules and are excluded. Coursecatalog tags and CTE
# pathway data are matched to these rows by course id, across every school year.
_SCHEDULE_ENROLLED_CLAUSE = "sl.schedule_status IN ('Active', 'Completed')"

def _catalog_match_join(schedule_alias: str = 'sl') -> str:
    """Join staff catalog + CTE rows for one schedule_list course row."""
    sl = schedule_alias
    return f"""
        LEFT JOIN LATERAL (
            SELECT cc.*
            FROM courses_catalog cc
            WHERE (
                ({sl}.course_key IS NOT NULL AND BTRIM({sl}.course_key) <> '' AND cc.course_key = {sl}.course_key)
                OR ({sl}.course_code IS NOT NULL AND BTRIM({sl}.course_code) <> '' AND cc.course_id = {sl}.course_code)
                OR (
                    {sl}.course_state_equivalence_code IS NOT NULL
                    AND BTRIM({sl}.course_state_equivalence_code) <> ''
                    AND cc.state_course_code = {sl}.course_state_equivalence_code
                )
            )
            ORDER BY
                CASE
                    WHEN {sl}.course_key IS NOT NULL AND BTRIM({sl}.course_key) <> ''
                         AND cc.course_key = {sl}.course_key THEN 1
                    WHEN {sl}.course_code IS NOT NULL AND BTRIM({sl}.course_code) <> ''
                         AND cc.course_id = {sl}.course_code THEN 2
                    WHEN {sl}.course_state_equivalence_code IS NOT NULL
                         AND BTRIM({sl}.course_state_equivalence_code) <> ''
                         AND cc.state_course_code = {sl}.course_state_equivalence_code THEN 3
                    ELSE 4
                END,
                cc.course_key
            LIMIT 1
        ) cc ON TRUE
        LEFT JOIN coursecatalog_catalogplacement pl ON pl.catalog_entry_id = cc.course_key
        LEFT JOIN coursecatalog_catalogtag tg ON pl.tag_id = tg.id
        LEFT JOIN coursecatalog_catalogcategory cat ON pl.category_id = cat.id
        LEFT JOIN coursecatalog_catalogdiscipline disc ON pl.discipline_id = disc.id
        LEFT JOIN coursecatalog_cataloglevel lv ON pl.level_id = lv.id
        LEFT JOIN coursecatalog_cte_pathways cte
            ON cte.course_id IS NOT NULL
           AND BTRIM(cte.course_id) <> ''
           AND {sl}.course_code IS NOT NULL
           AND BTRIM({sl}.course_code) <> ''
           AND cte.course_id = {sl}.course_code
"""


_CATALOG_JOINS_FROM_SCHEDULE = _catalog_match_join('sl')

_COURSE_PROG_DIMENSION = {
    'course_prog_tag': {
        'any_nonempty': (
            "(tg.name IS NOT NULL AND BTRIM(tg.name) <> '') "
            "OR (cte.course_id IS NOT NULL AND cte.course_id <> '')"
        ),
    },
    'course_prog_category': {
        'any_nonempty': (
            "(cat.name IS NOT NULL AND BTRIM(cat.name) <> '') "
            "OR (cte.career_cluster IS NOT NULL AND BTRIM(cte.career_cluster) <> '')"
        ),
    },
    'course_prog_discipline': {
        'any_nonempty': (
            "(disc.name IS NOT NULL AND BTRIM(disc.name) <> '') "
            "OR (cte.cte_pathway IS NOT NULL AND BTRIM(cte.cte_pathway) <> '')"
        ),
    },
    'course_prog_level': {
        'any_nonempty': (
            "(lv.name IS NOT NULL AND BTRIM(lv.name) <> '') "
            "OR (cte.cte_course_level IS NOT NULL AND BTRIM(cte.cte_course_level) <> '')"
        ),
    },
}


def _course_program_exists_sql(inner_predicate: str) -> str:
    return (
        "EXISTS ("
        " SELECT 1 FROM schedule_list sl "
        f"{_CATALOG_JOINS_FROM_SCHEDULE}"
        " WHERE sl.stu_annual_key = cs.stu_annual_key"
        "   AND sl.school_year = cs.school_year"
        f"   AND {_SCHEDULE_ENROLLED_CLAUSE}"
        f"   AND ({inner_predicate})"
        ")"
    )


def _course_program_no_value_sql(any_nonempty: str) -> str:
    return (
        "NOT EXISTS ("
        " SELECT 1 FROM schedule_list sl "
        f"{_CATALOG_JOINS_FROM_SCHEDULE}"
        " WHERE sl.stu_annual_key = cs.stu_annual_key"
        "   AND sl.school_year = cs.school_year"
        f"   AND {_SCHEDULE_ENROLLED_CLAUSE}"
        f"   AND ({any_nonempty})"
        ")"
    )


def apply_course_program_column_filter(
    where_clauses: list,
    params: list,
    col: str,
    values: list,
) -> None:
    """Append WHERE fragments for a Tag/Category/Discipline/Level column filter."""
    if not course_program_columns_enabled():
        return
    canonical = canonical_course_program_column(col)
    if not canonical or not values:
        return
    dim = _COURSE_PROG_DIMENSION.get(canonical)
    if not dim:
        return

    has_blank = '' in values
    real_values = [v for v in values if v != '']
    conditions: list[str] = []

    if real_values:
        if canonical == 'course_prog_tag':
            staff_vals = [v for v in real_values if str(v) != 'CTE']
            want_cte = any(str(v) == 'CTE' for v in real_values)
            match_parts: list[str] = []
            if staff_vals:
                match_parts.append("(tg.name IS NOT NULL AND tg.name = ANY(%s))")
                params.append(staff_vals)
            if want_cte:
                match_parts.append("(cte.course_id IS NOT NULL AND cte.course_id <> '')")
            if match_parts:
                conditions.append(_course_program_exists_sql(' OR '.join(match_parts)))
        elif canonical == 'course_prog_category':
            conditions.append(
                _course_program_exists_sql(
                    "(cat.name IS NOT NULL AND cat.name = ANY(%s)) "
                    "OR (cte.career_cluster IS NOT NULL AND cte.career_cluster = ANY(%s))"
                )
            )
            params.extend([real_values, real_values])
        elif canonical == 'course_prog_discipline':
            conditions.append(
                _course_program_exists_sql(
                    "(disc.name IS NOT NULL AND disc.name = ANY(%s)) "
                    "OR (cte.cte_pathway IS NOT NULL AND cte.cte_pathway = ANY(%s))"
                )
            )
            params.extend([real_values, real_values])
        elif canonical == 'course_prog_level':
            conditions.append(
                _course_program_exists_sql(
                    "(lv.name IS NOT NULL AND lv.name = ANY(%s)) "
                    "OR (cte.cte_course_level IS NOT NULL AND cte.cte_course_level = ANY(%s))"
                )
            )
            params.extend([real_values, real_values])

    if has_blank:
        conditions.append(_course_program_no_value_sql(dim['any_nonempty']))

    if conditions:
        where_clauses.append(f"({' OR '.join(conditions)})")


def clickhouse_course_program_ready() -> bool:
    """True when pre-synced ClickHouse course-program tables are populated."""
    if not getattr(settings, 'USE_CLICKHOUSE_ANALYTICS', True):
        return False
    cached = cache.get('analytics:course_program_ch_ready')
    if cached is not None:
        return bool(cached)
    ready = False
    try:
        from clickhouse_utils.executor import QueryExecutor
        result = QueryExecutor(analytics=True).execute(
            'SELECT count() AS c FROM analytics_student_course_program',
            [],
        )
        if not result.get('error') and result.get('rows'):
            row = result['rows'][0]
            count = row.get('c') if isinstance(row, dict) else row[0]
            ready = int(count or 0) > 0
    except Exception:
        ready = False
    cache.set('analytics:course_program_ch_ready', ready, 300)
    return ready


def split_course_program_filters(filters_dict: dict | None) -> tuple[dict, dict]:
    """Split course-program column filters from all other explorer filters."""
    if not isinstance(filters_dict, dict):
        return {}, {}
    course_program: dict = {}
    other: dict = {}
    for key, values in filters_dict.items():
        if is_course_program_column(key):
            if values:
                course_program[key] = values
        else:
            other[key] = values
    return course_program, other


def _apply_one_course_program_filter_clickhouse(
    where_clauses: list,
    params: list,
    col: str,
    values: list,
) -> None:
    canonical = canonical_course_program_column(col)
    if not canonical:
        return
    dimension = DIMENSION_BY_COLUMN.get(canonical)
    if not dimension:
        return
    has_blank = '' in values
    real_values = [str(v) for v in values if v != '']
    conditions: list[str] = []
    if real_values:
        conditions.append(
            "(cs.stu_annual_key, cs.school_year) IN ("
            " SELECT v.stu_annual_key, v.school_year"
            " FROM analytics_course_program_values v"
            " WHERE v.dimension = %s"
            "   AND v.value = ANY(%s)"
            ")"
        )
        params.extend([dimension, real_values])
    if has_blank:
        conditions.append(
            "(cs.stu_annual_key, cs.school_year) NOT IN ("
            " SELECT v.stu_annual_key, v.school_year"
            " FROM analytics_course_program_values v"
            " WHERE v.dimension = %s"
            ")"
        )
        params.append(dimension)
    if conditions:
        where_clauses.append(f"({' OR '.join(conditions)})")


def apply_course_program_filters_for_clickhouse(
    where_clauses: list,
    params: list,
    course_program_filters: dict,
    scope_where: list,
    scope_params: list,
) -> None:
    """Apply course-program filters to the ClickHouse student query."""
    if not course_program_filters or not course_program_columns_enabled():
        return
    if clickhouse_course_program_ready():
        for col, values in course_program_filters.items():
            _apply_one_course_program_filter_clickhouse(where_clauses, params, col, values)
        return
    pg_where = list(scope_where)
    pg_params = list(scope_params)
    for col, values in course_program_filters.items():
        apply_course_program_column_filter(pg_where, pg_params, col, values)
    if not pg_where:
        return
    sql = f"SELECT DISTINCT cs.stu_annual_key FROM current_student cs WHERE {' AND '.join(pg_where)}"
    with connection.cursor() as cursor:
        cursor.execute(sql, pg_params)
        keys = [row[0] for row in cursor.fetchall() if row and row[0]]
    if not keys:
        where_clauses.append("1=0")
    else:
        where_clauses.append("cs.stu_annual_key = ANY(%s)")
        params.append(keys)


def apply_course_program_filters_on_postgres_scope(
    where_clauses: list,
    params: list,
    filters_dict: dict | None,
) -> None:
    """Apply only course-program filters (for PostgreSQL filter-option queries)."""
    if not isinstance(filters_dict, dict):
        return
    for col, values in filters_dict.items():
        if is_course_program_column(col) and values:
            apply_course_program_column_filter(where_clauses, params, col, values)


def course_program_filter_options_sql(
    canonical: str,
    option_where_sql: str,
    join_sql: str,
    option_params: list | None = None,
) -> tuple[str, list]:
    """DISTINCT filter values for one course-program dimension, scoped to current_student."""
    if canonical not in _COURSE_PROG_DIMENSION:
        return '', []
    option_params = list(option_params or [])
    dimension = DIMENSION_BY_COLUMN.get(canonical)
    if clickhouse_course_program_ready() and dimension:
        query = f"""
            SELECT DISTINCT v.value AS v
            FROM analytics_course_program_values v
            INNER JOIN current_student cs
                ON cs.stu_annual_key = v.stu_annual_key
               AND cs.school_year = v.school_year
            {join_sql}
            WHERE {option_where_sql}
              AND v.dimension = %s
              AND v.value != ''
            ORDER BY v ASC
            LIMIT 31
        """
        return query, option_params + [dimension]

    base_from = f"""
        FROM current_student cs
        {join_sql}
        INNER JOIN schedule_list sl
            ON sl.stu_annual_key = cs.stu_annual_key
           AND sl.school_year = cs.school_year
           AND {_SCHEDULE_ENROLLED_CLAUSE}
        {_CATALOG_JOINS_FROM_SCHEDULE}
        WHERE {option_where_sql}
    """

    if canonical == 'course_prog_tag':
        inner = f"""
            SELECT DISTINCT tg.name AS v
            {base_from}
              AND tg.name IS NOT NULL AND BTRIM(tg.name) <> ''
            UNION
            SELECT DISTINCT 'CTE' AS v
            {base_from}
              AND cte.course_id IS NOT NULL AND cte.course_id <> ''
        """
    elif canonical == 'course_prog_category':
        inner = f"""
            SELECT DISTINCT cat.name AS v
            {base_from}
              AND cat.name IS NOT NULL AND BTRIM(cat.name) <> ''
            UNION
            SELECT DISTINCT cte.career_cluster AS v
            {base_from}
              AND cte.career_cluster IS NOT NULL AND BTRIM(cte.career_cluster) <> ''
        """
    elif canonical == 'course_prog_discipline':
        inner = f"""
            SELECT DISTINCT disc.name AS v
            {base_from}
              AND disc.name IS NOT NULL AND BTRIM(disc.name) <> ''
            UNION
            SELECT DISTINCT cte.cte_pathway AS v
            {base_from}
              AND cte.cte_pathway IS NOT NULL AND BTRIM(cte.cte_pathway) <> ''
        """
    else:
        inner = f"""
            SELECT DISTINCT lv.name AS v
            {base_from}
              AND lv.name IS NOT NULL AND BTRIM(lv.name) <> ''
            UNION
            SELECT DISTINCT cte.cte_course_level AS v
            {base_from}
              AND cte.cte_course_level IS NOT NULL AND BTRIM(cte.cte_course_level) <> ''
        """

    query = f"""
        SELECT v FROM (
            {inner}
        ) _cp_opts
        WHERE v IS NOT NULL AND BTRIM(v) <> ''
        ORDER BY v ASC
        LIMIT 31
    """
    return query, option_params * 2


def course_program_filter_blank_check_sql(
    canonical: str,
    option_where_sql: str,
    join_sql: str,
    option_params: list | None = None,
) -> tuple[str, list]:
    """Return SQL that finds whether any in-scope student has no value for this dimension."""
    option_params = list(option_params or [])
    dimension = DIMENSION_BY_COLUMN.get(canonical)
    if clickhouse_course_program_ready() and dimension:
        query = f"""
            SELECT 1
            FROM current_student cs
            {join_sql}
            WHERE {option_where_sql}
              AND (cs.stu_annual_key, cs.school_year) NOT IN (
                SELECT v.stu_annual_key, v.school_year
                FROM analytics_course_program_values v
                WHERE v.dimension = %s
              )
            LIMIT 1
        """
        return query, option_params + [dimension]
    dim = _COURSE_PROG_DIMENSION.get(canonical)
    if not dim:
        return '', []
    query = f"""
        SELECT 1
        FROM current_student cs
        {join_sql}
        WHERE {option_where_sql}
          AND {_course_program_no_value_sql(dim['any_nonempty'])}
        LIMIT 1
    """
    return query, option_params


def _fetch_course_program_data_clickhouse(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    from clickhouse_utils.executor import QueryExecutor

    conditions = []
    params: list = []
    for key, sy in pairs:
        conditions.append("(cp.stu_annual_key = %s AND cp.school_year = %s)")
        params.extend([key, sy])
    where_pairs = " OR ".join(conditions)
    sql = f"""
        SELECT
            cp.stu_annual_key,
            cp.school_year,
            cp.course_prog_tag,
            cp.course_prog_category,
            cp.course_prog_discipline,
            cp.course_prog_level
        FROM analytics_student_course_program cp
        WHERE {where_pairs}
    """
    out: dict[tuple[str, str], dict[str, str]] = {}
    result = QueryExecutor(analytics=True).execute(sql, params)
    if result.get('error'):
        return out
    for row in result.get('rows') or []:
        if isinstance(row, dict):
            record = row
        else:
            cols = result.get('columns') or []
            record = dict(zip(cols, row))
        tup = (str(record.get('stu_annual_key') or ''), str(record.get('school_year') or ''))
        out[tup] = {
            'course_prog_tag': (record.get('course_prog_tag') or '').strip() or '—',
            'course_prog_category': (record.get('course_prog_category') or '').strip() or '—',
            'course_prog_discipline': (record.get('course_prog_discipline') or '').strip() or '—',
            'course_prog_level': (record.get('course_prog_level') or '').strip() or '—',
        }
    return out


def _merge_agg_sql(staff_expr: str, cte_expr: str) -> str:
    """Combine two DISTINCT string_agg expressions into one comma-separated cell."""
    return (
        f"NULLIF(TRIM(BOTH ', ' FROM CONCAT_WS(', ', "
        f"NULLIF({staff_expr}, ''), NULLIF({cte_expr}, ''))), '')"
    )


def fetch_course_program_data_for_students(students: list[dict]) -> dict[tuple[str, str], dict[str, str]]:
    """
    Return {(stu_annual_key, school_year): {column_key: aggregated value}} for enrolled courses.

    Values come only from ``schedule_list`` rows for that student and school year
    (``schedule_status IN ('Active', 'Completed')``), matched to catalog / CTE by
    schedule course ids across every school year.
    """
    pairs = []
    seen = set()
    for row in students:
        key = row.get('stu_annual_key')
        sy = row.get('school_year') or ''
        if not key:
            continue
        tup = (str(key), str(sy))
        if tup in seen:
            continue
        seen.add(tup)
        pairs.append(tup)
    if not pairs:
        return {}

    if clickhouse_course_program_ready():
        return _fetch_course_program_data_clickhouse(pairs)

    annual_keys = [p[0] for p in pairs]
    school_years = [p[1] for p in pairs]

    staff_tag = (
        "string_agg(DISTINCT tg.name, ', ' ORDER BY tg.name) "
        "FILTER (WHERE tg.name IS NOT NULL AND tg.name <> '')"
    )
    cte_tag = (
        "CASE WHEN bool_or(cte.course_id IS NOT NULL AND cte.course_id <> '') "
        "THEN 'CTE' ELSE NULL END"
    )
    staff_cat = (
        "string_agg(DISTINCT cat.name, ', ' ORDER BY cat.name) "
        "FILTER (WHERE cat.name IS NOT NULL AND cat.name <> '')"
    )
    cte_cat = (
        "string_agg(DISTINCT cte.career_cluster, ', ' ORDER BY cte.career_cluster) "
        "FILTER (WHERE cte.career_cluster IS NOT NULL AND cte.career_cluster <> '')"
    )
    staff_disc = (
        "string_agg(DISTINCT disc.name, ', ' ORDER BY disc.name) "
        "FILTER (WHERE disc.name IS NOT NULL AND disc.name <> '')"
    )
    cte_disc = (
        "string_agg(DISTINCT cte.cte_pathway, ', ' ORDER BY cte.cte_pathway) "
        "FILTER (WHERE cte.cte_pathway IS NOT NULL AND cte.cte_pathway <> '')"
    )
    staff_lvl = (
        "string_agg(DISTINCT lv.name, ', ' ORDER BY lv.name) "
        "FILTER (WHERE lv.name IS NOT NULL AND lv.name <> '')"
    )
    cte_lvl = (
        "string_agg(DISTINCT cte.cte_course_level, ', ' ORDER BY cte.cte_course_level) "
        "FILTER (WHERE cte.cte_course_level IS NOT NULL AND cte.cte_course_level <> '')"
    )

    sql = f"""
        WITH pair AS (
            SELECT unnest(%s::text[]) AS stu_annual_key,
                   unnest(%s::text[]) AS school_year
        ),
        sched AS (
            SELECT sl.stu_annual_key, sl.school_year, sl.course_code, sl.course_key,
                   sl.course_state_equivalence_code
            FROM schedule_list sl
            INNER JOIN pair p
                ON p.stu_annual_key = sl.stu_annual_key
               AND p.school_year = sl.school_year
            WHERE {_SCHEDULE_ENROLLED_CLAUSE}
        )
        SELECT
            s.stu_annual_key,
            s.school_year,
            {_merge_agg_sql(staff_tag, cte_tag)} AS course_prog_tag,
            {_merge_agg_sql(staff_cat, cte_cat)} AS course_prog_category,
            {_merge_agg_sql(staff_disc, cte_disc)} AS course_prog_discipline,
            {_merge_agg_sql(staff_lvl, cte_lvl)} AS course_prog_level
        FROM sched s
        {_catalog_match_join('s')}
        GROUP BY s.stu_annual_key, s.school_year
    """
    out: dict[tuple[str, str], dict[str, str]] = {}
    with connection.cursor() as cursor:
        cursor.execute(sql, [annual_keys, school_years])
        cols = [c[0] for c in cursor.description]
        for row in cursor.fetchall():
            record = dict(zip(cols, row))
            tup = (record.pop('stu_annual_key'), record.pop('school_year'))
            for k, v in record.items():
                record[k] = (v or '').strip() or '—'
            out[tup] = record
    return out


def apply_course_program_columns(students: list[dict], selected_cols: list[str]) -> None:
    """Fill selected course program columns on student dicts in place."""
    wanted = normalize_course_program_columns(selected_cols)
    if not wanted or not students:
        return
    data = fetch_course_program_data_for_students(students)
    for student in students:
        key = (
            str(student.get('stu_annual_key') or ''),
            str(student.get('school_year') or ''),
        )
        values = data.get(key, {})
        for col in wanted:
            student[col] = values.get(col, '—')
        for col in selected_cols:
            if col in _LEGACY_COLUMN_ALIASES:
                canonical = _LEGACY_COLUMN_ALIASES[col]
                student[col] = values.get(canonical, '—')
