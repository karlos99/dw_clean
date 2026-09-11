"""
data_loader.py — Execute queries for dataviz charts.

Supports:
  - run_saved_query(query_id, extra_filters, user) → list[dict]
  - run_raw_sql(sql)                               → dict(columns, rows, ...)
"""
import hashlib
import json
import re

from django.core.cache import cache
from django.db import connections

from clickhouse_utils.timing import timed_fn
from dataexploration.column_utils import ap_column_key


# ── SQL safety guards (copied verbatim from original views.py) ────────────────

_SQL_READONLY_RE = re.compile(
    r'^\s*(select|with|explain|show|describe|desc)\b',
    re.IGNORECASE,
)
_SQL_DANGER_RE = re.compile(
    r'\b(insert|update|delete|drop|truncate|alter|create|replace|call|exec|execute|grant|revoke|copy|vacuum)\b',
    re.IGNORECASE,
)
MAX_SQL_ROWS       = 2000   # safety cap for run_raw_sql (SQL Lab)
DATASET_SQL_PREVIEW_ROWS = 50

_YEAR_PLACEHOLDER_RE = re.compile(r'\{\{([^}]+)\}\}')


def resolve_sql_year_placeholders(sql: str) -> tuple[str, str | None]:
    """Replace ``{{This School Year}}`` / ``{{Last School Year}}`` style tokens.

    Resolution order for each label:
      1. ``relative_year.relative_label`` (DB table)
      2. ProjectConfig via ``resolve_school_year()`` alias map

    Returns (resolved_sql, error_or_none).
    """
    if not sql or '{{' not in sql:
        return sql, None

    from dataexploration.constants import SCHOOL_YEAR_ALIASES
    from dataexploration.school_year_utils import resolve_school_year

    label_to_year: dict[str, str] = {}

    try:
        from dataviz.models import RelativeYear
        for row in RelativeYear.objects.all():
            if row.relative_label and row.school_year:
                label_to_year[row.relative_label.strip()] = row.school_year.strip()
    except Exception:
        pass

    alias_by_label = {label: alias for alias, label in SCHOOL_YEAR_ALIASES.items()}
    for label, alias in alias_by_label.items():
        if label in label_to_year:
            continue
        resolved = resolve_school_year(alias)
        if resolved and resolved != alias:
            label_to_year[label] = resolved

    resolved = sql
    for match in _YEAR_PLACEHOLDER_RE.finditer(sql):
        label = match.group(1).strip()
        year = label_to_year.get(label)
        if not year:
            return sql, (
                f"Unknown or unresolved school year '{{{{{label}}}}}'. "
                f"Use labels like 'This School Year' or 'Last School Year', "
                f"and ensure Project Settings has a default school year."
            )
        resolved = resolved.replace(match.group(0), year)

    return resolved, None


def run_raw_sql(sql: str) -> dict:
    """
    Execute a read-only SQL query inside a BEGIN READ ONLY transaction.
    Returns {'columns', 'col_types', 'rows', 'row_count', 'has_more', 'error'}.
    Results are cached for 60 seconds keyed by SQL hash.
    """
    sql = (sql or '').strip()
    if not sql:
        return {'error': 'No SQL provided'}
    if not _SQL_READONLY_RE.match(sql):
        return {'error': 'Only SELECT / WITH queries are allowed.'}
    if _SQL_DANGER_RE.search(sql):
        return {'error': 'Query contains disallowed keywords.'}

    sql, placeholder_err = resolve_sql_year_placeholders(sql)
    if placeholder_err:
        return {'error': placeholder_err}

    cache_key = f"dv_raw_sql:{hashlib.md5(sql.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from clickhouse_utils.executor import QueryExecutor

        executor = QueryExecutor(analytics=True)
        result = executor.execute(sql)

        if result['error']:
            return {'columns': [], 'rows': [], 'row_count': 0, 'has_more': False, 'error': result['error']}

        columns = result['columns']
        rows = result['rows']
        has_more = result['row_count'] >= MAX_SQL_ROWS

        col_types: dict = {}
        for col in columns:
            for row in rows:
                v = row.get(col)
                if v is not None:
                    col_types[col] = 'number' if isinstance(v, (int, float)) else 'string'
                    break
            col_types.setdefault(col, 'string')

        result = {
            'columns': columns, 'col_types': col_types,
            'rows': rows, 'row_count': len(rows), 'has_more': has_more,
        }
        cache.set(cache_key, result, 60)
        return result
    except Exception as exc:
        return {'error': str(exc)}


def execute_sql_dataset(source_sql: str, limit: int | None = DATASET_SQL_PREVIEW_ROWS) -> dict:
    """Run a read-only SQL dataset query; optionally truncate rows for preview."""
    sql = (source_sql or '').strip()
    if not sql:
        return {'error': 'No SQL provided', 'columns': [], 'rows': [], 'row_count': 0}

    result = run_raw_sql(sql)
    if result.get('error'):
        return result

    rows = result.get('rows') or []
    total_rows = len(rows)
    has_more = bool(result.get('has_more')) or (limit is not None and total_rows > limit)
    if limit is not None:
        rows = rows[:limit]

    return {
        'columns': result.get('columns') or [],
        'col_types': result.get('col_types') or {},
        'rows': rows,
        'row_count': len(rows),
        'total_rows': total_rows,
        'has_more': has_more,
        'error': None,
    }


# All columns that exist directly in the current_student table
_CS_COLUMNS = frozenset([
    'stu_annual_key', 'ssid', 'student_id', 'student_name', 'grade', 'school',
    'school_state_id', 'gender', 'race', 'ell', 'sed', 'foster', 'homeless',
    'sped', 'gate', 'in_504', 'dli', 'atendace_rate', 'suspension_indicator',
    'ltel', 'status', 'school_year',
])

# Virtual columns resolved via JSON expressions (not real CS table columns)
_VIRTUAL_CS_COLUMNS = frozenset(['race_list', 'dsusd_fep', 'avid', 'avid_type'])

# Column-Picker fields that live in student_enroll, not current_student.
# 'ell'/'grade' exist in both tables (see the COALESCE handling below); the
# rest are enrollment-only, so selecting them requires the se. join alias —
# there is no matching cs.{col} to fall back to.
_ENROLL_FIELDS = {
    'ell': 'se.ell_status', 'birth_date': 'se.birth_date',
    'us_enter_date': 'se.us_enter_date', 'fep_date': 'se.fep_date',
    'migrant': 'se.migrant', 'grade': 'se.grade',
    'enter_date': 'se.enter_date', 'leave_date': 'se.leave_date',
}

# ── Helpers for identifying & resolving mark / test columns ───────────────────

_SAFE_IDENT_RE = re.compile(r'^[a-zA-Z0-9_]+$')


def _norm_part(v: str) -> str:
    """Normalise a DB value for column-key comparison (spaces/hyphens → underscores)."""
    return (v or '').replace(' ', '_').replace('-', '_')


def _parse_year(col_key: str, prefix: str) -> str | None:
    """Extract school_year from a column key like ``test_2025_2026_...`` or ``mark_2025_2026_...``."""
    s = (col_key or '').replace('-', '_')
    if not s.startswith(prefix):
        return None
    tail = s[len(prefix):]
    if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
        return tail[:9].replace('_', '-')
    parts = tail.split('_', 1)
    return parts[0].replace('_', '-') if parts else None


def _build_test_columns_from_db(cursor, selected_test_cols: list[str]) -> tuple[list[dict], dict]:
    """Map ``test_*`` column keys to DB metadata (year, test_name, subject, …).

    Returns ``(infos_list, col_map_dict)``.  Uses a 10-minute cache.
    """
    if not selected_test_cols:
        return [], {}

    cache_key = f"dv_test_cols:{hashlib.md5(json.dumps(sorted(selected_test_cols)).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    normalized_to_original = {c.replace('-', '_'): c for c in selected_test_cols}
    years = sorted({y for y in (_parse_year(c, 'test_') for c in selected_test_cols) if y})
    if not years:
        return [], {}

    # Check for test_window column
    cursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'"
    )
    has_test_window = cursor.fetchone() is not None

    window_expr = "COALESCE(t.test_window, '')" if has_test_window else "''"
    cursor.execute(
        f"""
        SELECT DISTINCT t.school_year, t.test_name, t.subject, {window_expr}
        FROM tests t
        WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL
        """,
        [years],
    )

    infos: list[dict] = []
    col_map: dict[str, dict] = {}

    for yr, test_name, subject, test_window in cursor.fetchall():
        yr_norm = _norm_part(yr)
        tn_norm = _norm_part(test_name)
        sub_norm = _norm_part(subject)
        win_norm = _norm_part(test_window).strip('_')
        win_value = (test_window or '').strip() or None

        key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}" if win_norm else f"{yr_norm}_{tn_norm}_{sub_norm}"

        for suffix, metric in (('SS', 'ss'), ('PL', 'pl'), ('DFP', 'dfp')):
            norm_key = f"test_{key_base}_{suffix}"
            original_key = normalized_to_original.get(norm_key)
            if not original_key:
                continue
            info = {
                'col_name': original_key,
                'year': yr,
                'test_name': test_name or '',
                'subject': subject or '',
                'test_window': win_value if win_norm else None,
                'metric': metric,
            }
            if metric == 'dfp':
                info['other_info_key'] = 'dfp'
            infos.append(info)
            col_map[original_key] = info
            col_map[original_key.replace('-', '_')] = info

    # Handle other_info-backed columns (test_*_OI_*)
    if any('_OI_' in k for k in normalized_to_original):
        cursor.execute(
            f"""
            SELECT DISTINCT r.school_year, t.test_name, t.subject, {window_expr}, k.key
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
            WHERE r.school_year = ANY(%s)
              AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL AND r.other_info IS NOT NULL
            """,
            [years],
        )
        for yr, test_name, subject, test_window, raw_key in cursor.fetchall():
            if not raw_key:
                continue
            yr_norm = _norm_part(yr)
            tn_norm = _norm_part(test_name)
            sub_norm = _norm_part(subject)
            win_norm = _norm_part(test_window).strip('_')
            win_value = (test_window or '').strip() or None
            key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}" if win_norm else f"{yr_norm}_{tn_norm}_{sub_norm}"
            oi_norm = _norm_part(str(raw_key))
            oi_hash = hashlib.md5(str(raw_key).encode()).hexdigest()[:6]
            other_norm = f"test_{key_base}_OI_{oi_norm}_{oi_hash}"
            original_key = normalized_to_original.get(other_norm)
            if not original_key:
                continue
            info = {
                'col_name': original_key,
                'year': yr,
                'test_name': test_name or '',
                'subject': subject or '',
                'test_window': win_value if win_norm else None,
                'metric': 'oi',
                'other_info_key': str(raw_key),
            }
            infos.append(info)
            col_map[original_key] = info
            col_map[original_key.replace('-', '_')] = info

    result = (infos, col_map)
    cache.set(cache_key, result, 600)
    return result


def _build_mark_columns_from_db(cursor, selected_mark_cols: list[str]) -> tuple[list[dict], dict]:
    """Map ``mark_*`` column keys to student-marks metadata.

    Returns ``(infos_list, col_map_dict)``.  Uses a 10-minute cache.
    """
    if not selected_mark_cols:
        return [], {}

    cache_key = f"dv_mark_cols:{hashlib.md5(json.dumps(sorted(selected_mark_cols)).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    normalized_to_original = {c.replace('-', '_'): c for c in selected_mark_cols}
    years = sorted({y for y in (_parse_year(c, 'mark_') for c in selected_mark_cols) if y})
    if not years:
        return [], {}

    cursor.execute(
        """
        SELECT DISTINCT school_year, subject, course_name, mark_period
        FROM student_marks_list
        WHERE school_year = ANY(%s) AND school_year IS NOT NULL
        """,
        [years],
    )

    infos: list[dict] = []
    col_map: dict[str, dict] = {}

    for year, subject, course_name, mark_period in cursor.fetchall():
        year_norm = _norm_part(year)
        subject_norm = _norm_part(subject)
        course_norm = _norm_part(course_name)
        period_norm = _norm_part(mark_period)
        mark_norm = f"mark_{year_norm}_{subject_norm}_{course_norm}_{period_norm}"

        original_key = normalized_to_original.get(mark_norm)
        if not original_key:
            continue
        info = {
            'col_name': original_key,
            'year': year or '',
            'subject': subject or '',
            'course_name': course_name or '',
            'mark_period': (mark_period or '').strip() or None,
        }
        infos.append(info)
        col_map[original_key] = info
        col_map[original_key.replace('-', '_')] = info

    result = (infos, col_map)
    cache.set(cache_key, result, 600)
    return result


def _build_ap_exam_columns_from_db(cursor, selected_ap_cols: list[str]) -> tuple[list[dict], dict]:
    """Map ``ap_*_PL`` column keys to AP exam metadata (year + subject).

    Returns ``(infos_list, col_map_dict)``.  Uses a 10-minute cache.
    """
    if not selected_ap_cols:
        return [], {}

    cache_key = f"dv_ap_exam_cols:{hashlib.md5(json.dumps(sorted(selected_ap_cols)).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    normalized_to_original = {c.replace('-', '_'): c for c in selected_ap_cols}
    years = sorted({y for y in (_parse_year(c, 'ap_') for c in selected_ap_cols) if y})
    if not years:
        return [], {}

    cursor.execute(
        """
        SELECT DISTINCT r.admin_year, e.name
        FROM "dataLoader_apexamresult" r
        JOIN "dataLoader_apexamcode" e ON r.exam_id = e.code
        JOIN "dataLoader_apimportlog" il ON r.import_log_id = il.id
        WHERE r.admin_year = ANY(%s) AND r.admin_year IS NOT NULL
          AND il.is_published = true
        """,
        [years],
    )

    infos: list[dict] = []
    col_map: dict[str, dict] = {}

    for year, subject in cursor.fetchall():
        year_val = (year or '').strip() or 'Unknown'
        subject_val = (subject or '').strip() or 'Unknown'
        ap_norm = ap_column_key(year_val, subject_val, 'exam').replace('-', '_')

        original_key = normalized_to_original.get(ap_norm)
        if not original_key:
            continue
        info = {
            'col_name': original_key,
            'year': year_val,
            'subject': subject_val,
        }
        infos.append(info)
        col_map[original_key] = info
        col_map[original_key.replace('-', '_')] = info

    result = (infos, col_map)
    cache.set(cache_key, result, 600)
    return result


def _build_ap_award_columns_from_db(cursor, selected_award_cols: list[str]) -> tuple[list[dict], dict]:
    """Map ``apaward_*`` / ``apawards_all_*`` column keys to AP award metadata.

    ``apaward_{year}_{award}`` → individual award.
    ``apawards_all_{year}``    → combined column listing every award that year.
    Returns ``(infos_list, col_map_dict)``.  Uses a 10-minute cache.
    """
    if not selected_award_cols:
        return [], {}

    cache_key = f"dv_ap_award_cols:{hashlib.md5(json.dumps(sorted(selected_award_cols)).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    normalized_to_original = {c.replace('-', '_'): c for c in selected_award_cols}
    years = set()
    for c in selected_award_cols:
        if c.startswith('apawards_all_'):
            years.add(c[len('apawards_all_'):].replace('_', '-'))
        else:
            y = _parse_year(c, 'apaward_')
            if y:
                years.add(y)
    years = sorted(years)
    if not years:
        return [], {}

    cursor.execute(
        """
        SELECT DISTINCT a.award_year, t.description
        FROM "dataLoader_apstudentaward" a
        JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
        JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
        WHERE a.award_year = ANY(%s) AND a.award_year IS NOT NULL
          AND il.is_published = true
        """,
        [years],
    )

    infos: list[dict] = []
    col_map: dict[str, dict] = {}

    for year, award in cursor.fetchall():
        year_val = (year or '').strip() or 'Unknown'
        award_val = (award or '').strip() or 'Unknown'

        # Individual award column
        ap_norm = ap_column_key(year_val, award_val, 'award').replace('-', '_')
        original_key = normalized_to_original.get(ap_norm)
        if original_key:
            info = {
                'col_name': original_key,
                'year': year_val,
                'award': award_val,
            }
            infos.append(info)
            col_map[original_key] = info
            col_map[original_key.replace('-', '_')] = info

        # Combined "all awards" column for this year
        all_norm = f"apawards_all_{year_val.replace('-', '_')}"
        original_all = normalized_to_original.get(all_norm)
        if original_all:
            info_all = {
                'col_name': original_all,
                'year': year_val,
                'award': '__ALL__',
            }
            infos.append(info_all)
            col_map[original_all] = info_all
            col_map[original_all.replace('-', '_')] = info_all

    result = (infos, col_map)
    cache.set(cache_key, result, 600)
    return result


def _analytics_rows(sql, params):
    """Return ClickHouse rows, or None if the analytics table is unavailable."""
    from clickhouse_utils.executor import QueryExecutor
    result = QueryExecutor(analytics=True).execute(sql, params)
    if result.get('error'):
        return None
    return result.get('rows') or []


def _fetch_ap_exam_data(students: list[dict], ap_exam_infos: list[dict]):
    """Bulk-fetch AP exam grades and merge into *students* in-place."""
    if not students or not ap_exam_infos:
        return
    ssids = list({str(s.get('ssid') or '').strip() for s in students if str(s.get('ssid') or '').strip()})
    if not ssids:
        return

    student_map = {}
    for s in students:
        ssid = str(s.get('ssid') or '').strip()
        if ssid:
            student_map[ssid] = s

    infos_by_year: dict = {}
    for info in ap_exam_infos:
        infos_by_year.setdefault(info['year'], []).append(info)

    rows = _analytics_rows(
        "SELECT ssid, admin_year, exam_name, exam_grade FROM analytics_ap_results "
        "WHERE ssid = ANY(%s)",
        [ssids],
    )
    if rows is None:
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT r.ssid, r.admin_year, e.name, r.exam_grade
                    FROM "dataLoader_apexamresult" r
                    JOIN "dataLoader_apexamcode" e ON r.exam_id = e.code
                    JOIN "dataLoader_apimportlog" il ON r.import_log_id = il.id
                    WHERE r.ssid = ANY(%s) AND il.is_published = true
                    """,
                    [ssids],
                )
                rows = [
                    {'ssid': r[0], 'admin_year': r[1], 'exam_name': r[2], 'exam_grade': r[3]}
                    for r in cursor.fetchall()
                ]
        except Exception:
            return

    for row in rows:
        s_obj = student_map.get(str(row.get('ssid') or '').strip())
        if not s_obj:
            continue
        year_val = (row.get('admin_year') or '').strip() or 'Unknown'
        subject = (row.get('exam_name') or '').strip()
        grade = row.get('exam_grade')
        for info in infos_by_year.get(year_val, ()):
            if info['subject'].lower() == subject.lower():
                s_obj[info['col_name']] = str(grade).strip() if grade else '\u2014'



def _fetch_ap_award_data(students: list[dict], award_infos: list[dict]):
    """Bulk-fetch AP awards and merge into *students* in-place.

    Individual columns get the award description; the combined ``apawards_all_{year}``
    column gets a comma-separated list of every award earned that year.
    """
    if not students or not award_infos:
        return
    ssids = list({str(s.get('ssid') or '').strip() for s in students if str(s.get('ssid') or '').strip()})
    if not ssids:
        return

    student_map = {}
    for s in students:
        ssid = str(s.get('ssid') or '').strip()
        if ssid:
            student_map[ssid] = s

    infos_by_year: dict = {}
    for info in award_infos:
        infos_by_year.setdefault(info['year'], []).append(info)

    rows = _analytics_rows(
        "SELECT ssid, award_year, award_description FROM analytics_ap_awards "
        "WHERE ssid = ANY(%s)",
        [ssids],
    )
    if rows is None:
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT a.ssid, a.award_year, t.description
                    FROM "dataLoader_apstudentaward" a
                    JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
                    JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                    WHERE a.ssid = ANY(%s) AND il.is_published = true
                    """,
                    [ssids],
                )
                rows = [
                    {'ssid': r[0], 'award_year': r[1], 'award_description': r[2]}
                    for r in cursor.fetchall()
                ]
        except Exception:
            return

    all_by_year: dict = {}
    for row in rows:
        ssid = str(row.get('ssid') or '').strip()
        s_obj = student_map.get(ssid)
        if not s_obj:
            continue
        year_val = (row.get('award_year') or '').strip() or 'Unknown'
        award = row.get('award_description')
        for info in infos_by_year.get(year_val, ()):
            if info['award'] == '__ALL__':
                all_by_year.setdefault((ssid, year_val), set()).add(award)
            elif info['award'].lower() == (award or '').strip().lower():
                s_obj[info['col_name']] = award or '\u2014'

    for (ssid, year_val), awards in all_by_year.items():
        s_obj = student_map.get(ssid)
        if not s_obj:
            continue
        for info in infos_by_year.get(year_val, ()):
            if info['award'] == '__ALL__':
                s_obj[info['col_name']] = ', '.join(sorted(a for a in awards if a))


def _fetch_sat_rows_from_pg(ssids: list[str]) -> list[dict]:
    """Fallback: read published SAT rows from Postgres and resolve latest/highest."""
    from dataLoader.models import SATSettings

    columns = [
        'ssid', 'school_year', 'test_date', 'school_name',
        'total_score', 'ebrw_score', 'math_score',
        'percentile_state_total', 'percentile_state_rw', 'percentile_state_math',
        'percentile_country_total', 'percentile_country_rw', 'percentile_country_math',
        'percentile_natrep_total', 'percentile_natrep_rw', 'percentile_natrep_math',
        'percentile_natuser_total', 'percentile_natuser_rw', 'percentile_natuser_math',
    ]
    select_cols = ', '.join(f'r.{c}' for c in columns)
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {select_cols}
                FROM "dataLoader_satresult" r
                JOIN "dataLoader_satimportlog" il ON r.import_log_id = il.id
                WHERE r.ssid = ANY(%s) AND il.is_published = true
                """,
                [ssids],
            )
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception:
        return []

    try:
        sat_settings = SATSettings.get_solo()
        mode = sat_settings.result_mode
        highest_metric = sat_settings.highest_metric
    except Exception:
        mode, highest_metric = 'latest', 'total'

    metric_attr = {'total': 'total_score', 'rw': 'ebrw_score', 'math': 'math_score'}.get(
        highest_metric, 'total_score'
    )

    best: dict = {}
    for row in rows:
        key = (str(row.get('ssid') or '').strip(), str(row.get('school_year') or '').strip())
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        if mode == 'highest':
            if (row.get(metric_attr) or -1) > (current.get(metric_attr) or -1):
                best[key] = row
        else:
            if (str(row.get('test_date') or '')) > (str(current.get('test_date') or '')):
                best[key] = row
    return list(best.values())


def _fetch_sat_data(students: list[dict], sat_infos: list[dict]):
    """Bulk-fetch SAT scores/percentiles and merge into *students* in-place."""
    if not students or not sat_infos:
        return
    ssids = list({str(s.get('ssid') or '').strip() for s in students if str(s.get('ssid') or '').strip()})
    if not ssids:
        return

    from dataexploration.column_utils import sat_column_db_field

    rows = _analytics_rows(
        "SELECT * FROM analytics_sat_results WHERE ssid = ANY(%s)",
        [ssids],
    )
    if rows is None:
        rows = _fetch_sat_rows_from_pg(ssids)

    by_key = {}
    for row in rows:
        ssid = str(row.get('ssid') or '').strip()
        year = str(row.get('school_year') or '').strip()
        by_key[(ssid, year)] = row

    for student in students:
        ssid = str(student.get('ssid') or '').strip()
        for info in sat_infos:
            row = by_key.get((ssid, info.get('year')))
            field = sat_column_db_field(info['col_name'])
            value = row.get(field) if (row and field) else None
            if value is None or str(value).strip() == '':
                student[info['col_name']] = '\u2014'
            else:
                student[info['col_name']] = str(value).strip()


def _fetch_test_data(cursor, test_cols_info: list[dict], student_ids: list[str], students: list[dict],
                     tested_school_codes: list[str] | None = None):
    """Bulk-fetch test scores and merge into student dicts (mutates *students* in-place).

    ``tested_school_codes``: when provided, restricts results to rows where
    ``results.school_code`` is one of the given codes (tested/assessment school filter).
    """
    if not test_cols_info:
        return

    from dataexploration.column_utils import test_metadata_matches, normalize_score_value
    from dataviz.utils.chart_builder import _record_pg_val

    # Compound key: (student_id, school_year) — a student may appear in
    # multiple rows when multi-year enrollment is queried. Each key maps to a
    # LIST so that all enrollment rows for the same (student, year) get data.
    # We also keep a mapping of (ssid, school_year) in case local_student_id is not set.
    student_map: dict[str, list] = {}
    student_ssid_map: dict[str, list] = {}
    for s in students:
        sid = _get_row_val(s, 'student_id')
        ssid = _get_row_val(s, 'ssid')
        if sid and str(sid).strip():
            student_map.setdefault(str(sid).strip(), []).append(s)
        if ssid and str(ssid).strip():
            student_ssid_map.setdefault(str(ssid).strip(), []).append(s)

    student_ssids = list(student_ssid_map.keys())
    student_ids = student_ids or list(student_map.keys())
    if not student_ids and not student_ssids:
        return

    def _test_where(alias):
        where_parts = []
        params = []
        for info in test_cols_info:
            part = (
                f"(r.school_year = %s AND LOWER({alias}.test_name) = LOWER(%s)"
                f" AND LOWER({alias}.subject) = LOWER(%s)"
            )
            part_params = [info['year'], info['test_name'], info['subject']]
            if info.get('test_window'):
                part += f" AND LOWER({alias}.test_window) = LOWER(%s)"
                part_params.append(info['test_window'])
            part += ")"
            where_parts.append(part)
            params.extend(part_params)
        return where_parts, params

    tested_school_clause = ''
    school_params = []
    if tested_school_codes:
        codes = [c for c in tested_school_codes if c]
        if codes:
            tested_school_clause = "AND r.school_code = ANY(%s)"
            school_params = [codes]

    where_r, params_r = _test_where('r')
    ch_sql = f"""
        SELECT r.local_student_id, r.ssid, r.ss, r.pl, r.other_info,
               r.school_year, r.test_name, r.subject, r.test_window
        FROM analytics_student_results r
        WHERE (
            (r.ssid IS NOT NULL AND r.ssid != '' AND r.ssid != 'NaN' AND r.ssid = ANY(%s))
            OR ((r.ssid IS NULL OR r.ssid = '' OR r.ssid = 'NaN') AND r.local_student_id = ANY(%s))
          )
          AND ({" OR ".join(where_r)})
          {tested_school_clause}
    """
    fetched_rows = None
    if not any(info.get('metric') not in ('ss', 'pl') for info in test_cols_info):
        fetched_rows = _analytics_rows(ch_sql, [student_ssids, student_ids, *params_r, *school_params])
    if fetched_rows is None:
        where_t, params_t = _test_where('t')
        sql = f"""
            SELECT r.local_student_id, r.ssid, r.ss, r.pl, r.other_info,
                   r.school_year, t.test_name, t.subject, t.test_window
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            WHERE (
                (r.ssid IS NOT NULL AND r.ssid != '' AND r.ssid != 'NaN' AND r.ssid = ANY(%s))
                OR ((r.ssid IS NULL OR r.ssid = '' OR r.ssid = 'NaN') AND r.local_student_id = ANY(%s))
              )
              AND ({" OR ".join(where_t)})
              {tested_school_clause}
        """
        from clickhouse_utils.executor import QueryExecutor
        with QueryExecutor().pg_cursor() as cursor:
            cursor.execute(sql, [student_ssids, student_ids, *params_t, *school_params])
            fetched_rows = [
                {
                    'local_student_id': row[0], 'ssid': row[1], 'ss': row[2], 'pl': row[3],
                    'other_info': row[4], 'school_year': row[5], 'test_name': row[6],
                    'subject': row[7], 'test_window': row[8],
                }
                for row in cursor.fetchall()
            ]

    infos_by_year: dict = {}
    for info in test_cols_info:
        infos_by_year.setdefault(info['year'], []).append(info)

    for row in fetched_rows:
        sid = row.get('local_student_id')
        ssid = row.get('ssid')
        ss = row.get('ss')
        pl = row.get('pl')
        oi = row.get('other_info')
        ry = row.get('school_year')
        rt = row.get('test_name')
        rs = row.get('subject')
        rw = row.get('test_window')
        # Prefer SSID when present — local_student_id is reused across students.
        s_objs = []
        if ssid:
            s_objs = student_ssid_map.get(str(ssid).strip()) or []
        elif sid:
            s_objs = student_map.get(str(sid).strip()) or []
        if not s_objs:
            continue
        result_year = str(ry or '').strip()
        for info in infos_by_year.get(ry, ()):
            if test_metadata_matches(info, rt, rs, rw):
                metric = info['metric']
                col_name = info['col_name']
                if metric == 'ss':
                    val = normalize_score_value(ss) or '\u2014'
                elif metric == 'pl':
                    val = normalize_score_value(pl) or '\u2014'
                elif metric == 'oi' or metric == 'dfp':
                    oi_key = info.get('other_info_key')
                    if oi_key:
                        if isinstance(oi, str):
                            try:
                                oi = json.loads(oi)
                            except (json.JSONDecodeError, TypeError):
                                oi = None
                        if isinstance(oi, dict):
                            v = oi.get(oi_key)
                            val = normalize_score_value(v) or '\u2014'
                        else:
                            val = '\u2014'
                    else:
                        val = '\u2014'
                else:
                    continue
                for s_obj in s_objs:
                    # Attach at student level on every enrollment row — matches data
                    # exploration EXISTS filters (student has result), not only when
                    # enrollment.school_year equals result.school_year.
                    s_obj[col_name] = val
                    _record_pg_val(s_obj, col_name, val)


def _fetch_mark_data(cursor, mark_cols_info: list[dict], student_keys: list[str], students: list[dict]):
    """Bulk-fetch student marks and merge into student dicts (mutates *students* in-place)."""
    if not mark_cols_info or not student_keys:
        return

    student_map = {s.get('stu_annual_key'): s for s in students if s.get('stu_annual_key')}
    where_parts = []
    params = []
    for info in mark_cols_info:
        part = "(sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s"
        part_params = [info['year'], f"%{info['subject']}%", f"%{info['course_name']}%"]
        if info.get('mark_period'):
            part += " AND sm.mark_period ILIKE %s"
            part_params.append(info['mark_period'])
        else:
            part += " AND (sm.mark_period IS NULL OR sm.mark_period = '')"
        part += ")"
        where_parts.append(part)
        params.extend(part_params)

    sql = f"""
        SELECT sm.stu_annual_key, sm.mark, sm.school_year, sm.subject, sm.course_name, sm.mark_period
        FROM analytics_student_marks sm
        WHERE sm.stu_annual_key = ANY(%s)
          AND ({" OR ".join(where_parts)})
    """
    fetched_rows = _analytics_rows(sql, [student_keys, *params])
    if fetched_rows is None:
        pg_sql = sql.replace('analytics_student_marks', 'student_marks_list')
        cursor.execute(pg_sql, [student_keys, *params])
        fetched_rows = [
            {
                'stu_annual_key': row[0], 'mark': row[1], 'school_year': row[2],
                'subject': row[3], 'course_name': row[4], 'mark_period': row[5],
            }
            for row in cursor.fetchall()
        ]

    infos_by_year: dict = {}
    for info in mark_cols_info:
        infos_by_year.setdefault(info['year'], []).append(info)

    for row in fetched_rows:
        sk = row.get('stu_annual_key')
        mark = row.get('mark')
        ry = row.get('school_year')
        rs = row.get('subject')
        rc = row.get('course_name')
        rp = row.get('mark_period')
        s_obj = student_map.get(sk)
        if not s_obj: continue
        for info in infos_by_year.get(ry, ()):
            if (info['subject'].lower() in rs.lower() and
                info['course_name'].lower() in rc.lower()):
                ip = info.get('mark_period')
                if not ip and not rp: pass
                elif ip and rp and ip.lower() == rp.lower(): pass
                else: continue
                s_obj[info['col_name']] = (str(mark).strip() if mark else '\u2014')


def _norm_col_key(c: str) -> str:
    return str(c).replace('-', '_').lower()


def _row_col_keys_norm(row: dict) -> set:
    return {_norm_col_key(k) for k in row}


def _get_row_val(row: dict, col: str):
    norm = _norm_col_key(col)
    for k, v in row.items():
        if _norm_col_key(k) == norm:
            return v
    return None


def _row_has_col(row: dict, col: str) -> bool:
    return _norm_col_key(col) in _row_col_keys_norm(row)


def _rows_have_value(rows: list[dict], col: str) -> bool:
    return any(str(_get_row_val(r, col) or '').strip() for r in rows)


def _fetch_base_columns_for_rows(
    rows: list[dict],
    missing_base: list[str],
    filters: dict,
    user=None,
) -> tuple[list[dict], str | None]:
    """Fetch missing base current_student columns keyed by row identifiers."""
    if not rows or not missing_base:
        return rows, None

    id_key = None
    ids = []
    for candidate in ('stu_annual_key', 'ssid', 'student_id'):
        ids = list({str(_get_row_val(r, candidate) or '').strip() for r in rows if _get_row_val(r, candidate)})
        ids = [i for i in ids if i]
        if ids:
            id_key = candidate
            break
    if not ids:
        return rows, None

    fetch_cols = list(dict.fromkeys(missing_base))
    for k in ('stu_annual_key', 'ssid', 'student_id'):
        if k not in fetch_cols:
            fetch_cols.insert(0, k)

    config = {
        'columns': fetch_cols,
        'filters': {id_key: ids, **{k: v for k, v in filters.items() if k not in ('tested_school',)}},
    }
    result = _run_saved_query_sql(config, user=user)
    if result.get('error'):
        return rows, result['error']

    lookup = {}
    for r in result.get('rows', []):
        val = str(_get_row_val(r, id_key) or '').strip()
        if val:
            lookup[val] = r

    for row in rows:
        match = str(_get_row_val(row, id_key) or '').strip()
        if match and match in lookup:
            for k, v in lookup[match].items():
                if not _row_has_col(row, k) or _get_row_val(row, k) in (None, '', '\u2014'):
                    row[k.split('.')[-1] if '.' in k else k] = v
    return rows, None


def _enrich_rows_test_mark(
    rows: list[dict],
    test_cols: list[str],
    mark_cols: list[str],
    filters: dict,
    ap_cols: list[str] | None = None,
    award_cols: list[str] | None = None,
) -> str | None:
    """Bulk-fetch test/mark/AP columns and merge into rows in-place."""
    ap_cols = ap_cols or []
    award_cols = award_cols or []
    if not rows or (not test_cols and not mark_cols and not ap_cols and not award_cols):
        return None

    need_test = bool(test_cols)
    need_mark = bool(mark_cols)
    need_ap = bool(ap_cols)
    need_award = bool(award_cols)
    tested_school_filter = filters.get('tested_school')
    if tested_school_filter and not isinstance(tested_school_filter, list):
        tested_school_filter = [tested_school_filter]

    try:
        with connections['default'].cursor() as cursor:
            _multi_years = (
                filters.get('school_year')
                if isinstance(filters.get('school_year'), list) and len(filters.get('school_year', [])) > 1
                else None
            )
            test_infos = mark_infos = ap_exam_infos = award_infos = None
            student_ids = student_keys = None

            if need_test:
                test_infos, _ = _build_test_columns_from_db(cursor, test_cols)
                if _multi_years and test_infos:
                    _seen = set()
                    _expanded = []
                    for _info in test_infos:
                        col_year = _parse_year(_info['col_name'], 'test_')
                        if col_year:
                            _expanded.append({**_info, 'year': col_year})
                        else:
                            for _yr in _multi_years:
                                _key = (_yr, _info['test_name'], _info['subject'], _info.get('test_window'), _info['metric'])
                                if _key not in _seen:
                                    _seen.add(_key)
                                    _expanded.append({**_info, 'year': _yr})
                    test_infos = _expanded
                student_ids = list({
                    str(_get_row_val(r, 'student_id') or '').strip()
                    for r in rows
                    if str(_get_row_val(r, 'student_id') or '').strip()
                })

            if need_mark:
                mark_infos, _ = _build_mark_columns_from_db(cursor, mark_cols)
                if _multi_years and mark_infos:
                    _seen = set()
                    _expanded = []
                    for _info in mark_infos:
                        col_year = _parse_year(_info['col_name'], 'mark_')
                        if col_year:
                            _expanded.append({**_info, 'year': col_year})
                        else:
                            for _yr in _multi_years:
                                _key = (_yr, _info['subject'], _info['course_name'], _info.get('mark_period'))
                                if _key not in _seen:
                                    _seen.add(_key)
                                    _expanded.append({**_info, 'year': _yr})
                    mark_infos = _expanded
                student_keys = [r.get('stu_annual_key') for r in rows if r.get('stu_annual_key')]

            if need_ap:
                ap_exam_infos, _ = _build_ap_exam_columns_from_db(cursor, ap_cols)
            if need_award:
                award_infos, _ = _build_ap_award_columns_from_db(cursor, award_cols)

            from clickhouse_utils.executor import QueryExecutor
            from clickhouse_utils.thread_pool import get_io_pool

            if need_test and need_mark:
                def _fetch_tests():
                    _fetch_test_data(
                        QueryExecutor(analytics=True).cursor(), test_infos, student_ids, rows,
                        tested_school_codes=tested_school_filter,
                    )

                def _fetch_marks():
                    _fetch_mark_data(QueryExecutor(analytics=True).cursor(), mark_infos, student_keys, rows)

                pool = get_io_pool()
                for f in [pool.submit(_fetch_tests), pool.submit(_fetch_marks)]:
                    f.result()
            elif need_test:
                _fetch_test_data(
                    QueryExecutor(analytics=True).cursor(), test_infos, student_ids, rows,
                    tested_school_codes=tested_school_filter,
                )
            elif need_mark:
                _fetch_mark_data(QueryExecutor(analytics=True).cursor(), mark_infos, student_keys, rows)

        # AP/survey prefer ClickHouse analytics tables, with PostgreSQL fallback.
        if need_ap:
            _fetch_ap_exam_data(rows, ap_exam_infos)
        if need_award:
            _fetch_ap_award_data(rows, award_infos)
    except Exception as exc:
        return str(exc)
    return None


def _build_enriched_column_list(rows: list[dict], needed_columns: list[str]) -> list[str]:
    cols = []
    seen = set()
    if rows:
        for k in rows[0]:
            if k not in seen:
                cols.append(k)
                seen.add(k)
    for c in needed_columns:
        bare = c.split('.')[-1]
        if bare not in seen:
            cols.append(bare)
            seen.add(bare)
    return cols


def enrich_drill_rows(
    rows: list[dict],
    needed_columns: list[str],
    filters: dict | None = None,
    user=None,
    dynamic_filters: dict | None = None,
) -> tuple[list[dict], list[str], str | None]:
    """
    Enrich existing rows in-place with missing base and test/mark columns.
    Avoids re-running the full explorer query for dataset drill-through.
    """
    if not rows:
        return [], list(needed_columns or []), None

    filters = dict(filters or {})
    needed_columns = list(needed_columns or [])

    try:
        from dataexploration.school_year_utils import resolve_relative_column_key
        resolved_needed = [resolve_relative_column_key(c) for c in needed_columns]
    except Exception:
        resolved_needed = list(needed_columns)

    row_keys_norm = _row_col_keys_norm(rows[0])
    missing_base: list[str] = []
    test_cols: list[str] = []
    mark_cols: list[str] = []
    ap_cols: list[str] = []
    award_cols: list[str] = []

    for c, rc in zip(needed_columns, resolved_needed):
        bare = c.split('.')[-1]
        cn = _norm_col_key(bare)
        if cn in row_keys_norm:
            continue
        if rc.startswith('apawards_all_') or rc.startswith('apaward_'):
            award_cols.append(rc)
        elif rc.startswith('ap_'):
            ap_cols.append(rc)
        elif rc.startswith('test_'):
            test_cols.append(rc)
        elif rc.startswith('mark_'):
            mark_cols.append(rc)
        elif bare in _CS_COLUMNS or bare in _ENROLL_FIELDS or bare in _VIRTUAL_CS_COLUMNS:
            if _SAFE_IDENT_RE.match(bare) and bare not in missing_base:
                missing_base.append(bare)

    need_test = bool(test_cols)
    need_mark = bool(mark_cols)
    need_ap = bool(ap_cols)
    need_award = bool(award_cols)

    if not missing_base and not need_test and not need_mark and not need_ap and not need_award:
        return rows, _build_enriched_column_list(rows, needed_columns), None

    if need_test:
        for k in ('student_id', 'school_year'):
            if not _rows_have_value(rows, k) and k not in missing_base:
                missing_base.append(k)
    if need_mark:
        if not _rows_have_value(rows, 'stu_annual_key') and 'stu_annual_key' not in missing_base:
            missing_base.append('stu_annual_key')
    if (need_ap or need_award):
        if not _rows_have_value(rows, 'ssid') and 'ssid' not in missing_base:
            missing_base.append('ssid')

    if missing_base:
        rows, err = _fetch_base_columns_for_rows(rows, missing_base, filters, user=user)
        if err:
            return rows, [], err

    if need_test or need_mark or need_ap or need_award:
        err = _enrich_rows_test_mark(rows, test_cols, mark_cols, filters, ap_cols=ap_cols, award_cols=award_cols)
        if err:
            return rows, [], err

    dyn = dict(dynamic_filters or {})
    for fkey, fval in filters.items():
        bare = fkey.split('.')[-1]
        if bare.startswith(('test_', 'mark_')):
            dyn[bare] = fval

    if dyn and rows:
        rows = [
            row for row in rows
            if all(
                _matches_dynamic_filter(_get_row_val(row, fk), fv)
                for fk, fv in dyn.items()
            )
        ]

    return rows, _build_enriched_column_list(rows, needed_columns), None


def _matches_dynamic_filter(row_value, filter_value) -> bool:
    blank_tokens = {'', '—', '(Blank)'}

    def _normalize(value):
        if value is None:
            return ''
        return str(value).strip()

    normalized_row = _normalize(row_value)
    if isinstance(filter_value, list):
        selected = [_normalize(item) for item in filter_value if _normalize(item)]
        if not selected:
            return True
        if '(Blank)' in selected and normalized_row in {'', '—'}:
            return True
        return normalized_row in {item for item in selected if item != '(Blank)'}

    normalized_filter = _normalize(filter_value)
    if not normalized_filter:
        return True
    if normalized_filter == '(Blank)':
        return normalized_row in {'', '—'}
    return normalized_row == normalized_filter


# ── Public entry points ──────────────────────────────────────────────────────

def run_saved_query_preview(saved_query) -> dict:
    """Return the first 5 rows + column list for a SavedQuery for preview.

    Reuses the existing query SQL from the config but limits to 5 rows.
    Does NOT apply user filters — this is a schema-only preview.
    """
    config = dict(saved_query.config or {})
    columns = config.get('columns', [])
    if not columns:
        return {'error': 'No columns configured in saved query', 'rows': [], 'columns': []}

    # Build a minimal SELECT from current_student for preview
    base_cols = [c for c in columns if c in _CS_COLUMNS and _SAFE_IDENT_RE.match(c)]
    if not base_cols:
        base_cols = ['student_id', 'student_name', 'grade', 'school', 'school_year']

    select_exprs = [f'cs.{c}' for c in base_cols]
    col_sql = ', '.join(select_exprs)
    sql = f'SELECT {col_sql} FROM current_student cs WHERE cs.status = %s LIMIT 5'

    try:
        from clickhouse_utils.executor import QueryExecutor

        executor = QueryExecutor(analytics=True)
        result = executor.execute(sql, ['Active'])
        if result['error']:
            return {'error': result['error'], 'rows': [], 'columns': []}
        return {
            'columns': base_cols,
            'rows': result['rows'],
            'row_count': result['row_count'],
        }
    except Exception as exc:
        return {'error': str(exc), 'rows': [], 'columns': []}


def run_saved_query(query_id: int, extra_filters: dict, user, extra_columns: list | None = None, apply_user_filters: bool = False, limit: int | None = None, offset: int | None = None, order_by: str | None = None, cursor: str | None = None) -> dict:
    """
    Execute a ``dataexploration.SavedQuery`` and return rows as a list of dicts.

    ``extra_filters`` are merged on top of the query's saved filters before execution.
    ``extra_columns`` are appended to the query's column list so the drill table
    can request columns beyond what the original chart query included.
    Returns {'rows': [...], 'columns': [...], 'error': str|None}.
    """
    from dataexploration.models import SavedQuery
    from dataexploration.utils import can_user_view_query

    try:
        query = SavedQuery.objects.get(pk=query_id)
    except SavedQuery.DoesNotExist:
        return {'error': f'SavedQuery {query_id} not found', 'rows': [], 'columns': []}

    if not can_user_view_query(user, query):
        return {'error': 'Access denied', 'rows': [], 'columns': []}

    config = dict(query.config or {})

    # Merge extra_filters into the query config filters
    if extra_filters:
        merged = dict(config.get('filters', {}))
        merged.update(extra_filters)
        config['filters'] = merged

    # Append extra columns requested by the drill table
    if extra_columns:
        existing = list(config.get('columns', []))
        existing_set = set(existing)
        for c in extra_columns:
            if c not in existing_set:
                existing.append(c)
                existing_set.add(c)
        config['columns'] = existing

    return _run_saved_query_sql(
        config, user=user if apply_user_filters else None,
        limit=limit, offset=offset, order_by=order_by, cursor=cursor,
    )


def run_column_query(selected_columns: list[str], filters: dict | None = None, user=None, apply_user_filters: bool = False, limit: int | None = None, offset: int | None = None, order_by: str | None = None, cursor: str | None = None) -> dict:
    """
    Execute a query using directly-selected columns (Column Picker source type).

    Builds a config dict compatible with ``_run_saved_query_sql`` and delegates to it.
    ``selected_columns`` is a list of column keys (base, test_*, mark_*).
    ``filters`` is a dict of {column_key: value/list} filters.
    Returns {'rows': [...], 'columns': [...], 'error': str|None}.
    """
    if not selected_columns:
        return {'error': 'No columns selected', 'rows': [], 'columns': []}

    config = {
        'columns': selected_columns,
        'filters': filters or {},
    }
    return _run_saved_query_sql(
        config, user=user if apply_user_filters else None,
        limit=limit, offset=offset, order_by=order_by, cursor=cursor,
    )


@timed_fn('_run_saved_query_sql')
def _run_saved_query_sql(config: dict, user=None, limit: int | None = None, offset: int | None = None, order_by: str | None = None, count_only: bool = False, cursor: str | None = None) -> dict:
    """
    Execute a saved query config against ``current_student`` using raw SQL.

    Base columns are selected from ``current_student``.  Mark (``mark_*``) and
    test (``test_*``) columns are fetched via separate bulk queries against
    ``student_marks_list`` and ``results``/``tests`` respectively, then merged
    into each row dict.

    If ``user`` is provided, school-level access filters are applied based on
    the user's role and permitted schools.
    """
    # ── Cache check ───────────────────────────────────────────────────────────
    # We cache the entire result dict (rows, columns, types) for 5 minutes.
    # The key is based on the hash of the JSON-serialized config.
    # If a user is provided, we include the user's ID and active role/school 
    # to ensure they only get data they are allowed to see.
    user_part = ""
    if user:
        user_role = getattr(user, 'active_role', None)
        role_name = user_role.name if user_role else 'None'
        active_school = getattr(user, 'active_school', None)
        school_id = active_school.state_id if active_school else 'All'
        user_part = f":u{user.pk}:r{role_name}:s{school_id}"

    config_hash = hashlib.md5(json.dumps({
        **config,
        'limit': limit,
        'offset': offset,
        'order_by': order_by,
        'cursor': cursor or '',
        'count_only': count_only,
        # A successful ClickHouse sync advances this version and naturally
        # invalidates cached analytics results without clearing unrelated app
        # caches.
        'clickhouse_sync_version': cache.get('analytics:clickhouse_sync_version', 'legacy'),
    }, sort_keys=True).encode()).hexdigest()
    cache_key = f"dv_query_result:{config_hash}{user_part}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    requested = config.get('columns', [])
    filters   = config.get('filters', {}) if isinstance(config.get('filters'), dict) else {}

    # Resolve school-year aliases (e.g. '__sy_this__') to concrete year values.
    if 'school_year' in filters:
        try:
            from dataexploration.school_year_utils import resolve_school_year
            sy_val = filters['school_year']
            if isinstance(sy_val, list):
                filters = {**filters, 'school_year': [resolve_school_year(v) for v in sy_val]}
            else:
                filters = {**filters, 'school_year': resolve_school_year(sy_val)}
        except Exception:
            pass

    # Partition requested columns into base / test / mark buckets.
    # Base columns go directly into SQL (cs.{col} or se.{col} — see
    # _ENROLL_FIELDS below) so must be safe identifiers.
    # Mark/test columns are only used as Python dict keys — the actual SQL uses
    # parameterised metadata values — so they can contain / and other chars.
    base_cols:  list[str] = []
    test_cols:  list[str] = []
    mark_cols:  list[str] = []
    ap_cols:    list[str] = []
    award_cols: list[str] = []
    for c in requested:
        if (c in _CS_COLUMNS or c in _ENROLL_FIELDS or c in _VIRTUAL_CS_COLUMNS) and _SAFE_IDENT_RE.match(c):
            base_cols.append(c)
        elif c.startswith('apawards_all_') or c.startswith('apaward_'):
            award_cols.append(c)
        elif c.startswith('ap_'):
            ap_cols.append(c)
        elif c.startswith('test_'):
            test_cols.append(c)
        elif c.startswith('mark_'):
            mark_cols.append(c)

    # Resolve relative column key aliases (e.g. test___sy_this___... → test_2025_2026_...)
    # before they reach _parse_year / _build_test_columns_from_db which expect real years.
    try:
        from dataexploration.school_year_utils import resolve_relative_column_key
        test_cols = [resolve_relative_column_key(c) for c in test_cols]
        mark_cols = [resolve_relative_column_key(c) for c in mark_cols]
        ap_cols = [resolve_relative_column_key(c) for c in ap_cols]
        award_cols = [resolve_relative_column_key(c) for c in award_cols]
    except Exception:
        pass

    # Ensure we always have join keys so we can do bulk fetches
    need_test = bool(test_cols)
    need_mark = bool(mark_cols)
    need_ap = bool(ap_cols)
    need_award = bool(award_cols)
    if need_test and 'student_id' not in base_cols:
        base_cols.append('student_id')
    if need_test and 'school_year' not in base_cols:
        base_cols.append('school_year')
    if need_mark and 'stu_annual_key' not in base_cols:
        base_cols.append('stu_annual_key')
    if (need_ap or need_award) and 'ssid' not in base_cols:
        base_cols.append('ssid')
    if cursor:
        if 'stu_annual_key' not in base_cols:
            base_cols.append('stu_annual_key')
        if 'student_name' not in base_cols:
            base_cols.append('student_name')

    if not base_cols:
        base_cols = ['student_id', 'student_name', 'grade', 'school', 'school_year']

    # Build WHERE clauses — apply ALL saved-query filters generically.
    where_parts: list[str] = []
    params: list = []
    dynamic_filters: dict[str, object] = {}

    # Status filter — mirror data exploration: default Active; explicit empty = all statuses.
    from dataviz.utils.status_filter import apply_status_filter
    apply_status_filter(where_parts, params, filters, alias='cs', pop=True)

    # ── Role-based access ──────────────────────────────────────────────
    if user:
        is_unrestricted = False
        if user.is_superuser:
            is_unrestricted = True
        else:
            # Check for Project Admin or District roles
            active_role = getattr(user, 'active_role', None)
            role_name = active_role.name if active_role else ''
            
            # If no active role, check all roles
            user_roles = [r.name for r in user.roles.all()] if not role_name else [role_name]
            
            unrestricted_names = {'Project Admin', 'District Admin', 'District View'}
            if any(name in unrestricted_names or 'District' in name for name in user_roles):
                is_unrestricted = True

        # School Gr View grade restrictions
        active_role_name = getattr(getattr(user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                where_parts.append("cs.grade = ANY(%s)")
                params.append(delegated_grades)

        # Teacher / Homeroom restriction
        if hasattr(user, 'get_accessible_student_keys'):
            keys = user.get_accessible_student_keys()
            if keys is not None:
                if keys:
                    where_parts.append("cs.stu_annual_key = ANY(%s)")
                    params.append(list(keys))
                else:
                    where_parts.append("1=0")  # No students assigned

        if not is_unrestricted:
            if hasattr(user, 'active_school') and user.active_school:
                # Explicitly filtered to one school via the UI selector
                where_parts.append("cs.school_state_id = %s")
                params.append(user.active_school.state_id)
            else:
                # Filter to the list of schools the user is permitted to see
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append("cs.school_state_id = ANY(%s)")
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")  # Deny access if no schools assigned

    needs_enroll = False

    # When a school_year filter is present (especially multi-year or past),
    # force the enroll join so grade comes from the enrollment record for that year.
    _needs_year_grade = False
    if 'school_year' in filters:
        try:
            from dataexploration.school_year_utils import resolve_school_year
            default_sy = resolve_school_year('__sy_this__')
            sy_val = filters['school_year']
            if isinstance(sy_val, list):
                if len(sy_val) > 1 or (len(sy_val) == 1 and sy_val[0] != default_sy):
                    _needs_year_grade = True
            elif isinstance(sy_val, str) and sy_val and sy_val != default_sy:
                _needs_year_grade = True
        except Exception:
            pass
    if _needs_year_grade:
        needs_enroll = True

    def _add_filter(expr: str, value):
        """Add an IN / = filter using the given SQL expression."""
        if not value:
            return

        if isinstance(value, dict):
            gte = value.get('gte')
            lte = value.get('lte')
            exclude = value.get('exclude')
            if exclude:
                conds = []
                if gte:
                    conds.append(f'{expr} < %s')
                    params.append(gte)
                if lte:
                    conds.append(f'{expr} > %s')
                    params.append(lte)
                if conds:
                    where_parts.append(f"({' OR '.join(conds)} OR {expr} IS NULL)")
            else:
                if gte:
                    where_parts.append(f'{expr} >= %s')
                    params.append(gte)
                if lte:
                    where_parts.append(f'{expr} <= %s')
                    params.append(lte)
            return

        if isinstance(value, list):
            value = [v for v in value if v]
            if not value:
                return
            has_blank = '(Blank)' in value
            others = [v for v in value if v != '(Blank)']
            parts = []
            if others:
                placeholders = ', '.join(['%s'] * len(others))
                parts.append(f'{expr} IN ({placeholders})')
                params.extend(others)
            if has_blank:
                parts.append(f"({expr} IS NULL OR {expr} = '' OR {expr} = '—')")

            if len(parts) > 1:
                where_parts.append(f"({' OR '.join(parts)})")
            elif parts:
                where_parts.append(parts[0])
        else:
            if value == '(Blank)':
                where_parts.append(f"({expr} IS NULL OR {expr} = '' OR {expr} = '—')")
            else:
                where_parts.append(f'{expr} = %s')
                params.append(value)

    # Apply every filter whose key is a base CS column or an enroll field.
    # Pull out special non-column filters first.
    tested_school_filter = filters.pop('tested_school', None) if isinstance(filters, dict) else None
    if tested_school_filter and not isinstance(tested_school_filter, list):
        tested_school_filter = [tested_school_filter]

    from dataexploration.view_modules.column_management import (
        JSON_LIST_COLUMNS,
        JSON_SCALAR_COLUMNS,
        append_json_list_filter,
        json_list_display_expr,
        json_scalar_expr,
    )

    for fkey, fval in filters.items():
        if fkey == 'search':
            continue  # handled separately below
        if fkey.startswith(('test_', 'mark_')):
            try:
                from dataexploration.school_year_utils import resolve_relative_column_key
                dynamic_filters[resolve_relative_column_key(fkey)] = fval
            except Exception:
                dynamic_filters[fkey] = fval
            continue  # dynamic columns are filtered after enrichment below
        json_key = JSON_LIST_COLUMNS.get(fkey)
        if json_key:
            vals = fval if isinstance(fval, list) else ([fval] if fval else [])
            append_json_list_filter(where_parts, params, vals, json_key)
            continue
        if fkey in _ENROLL_FIELDS:
            needs_enroll = True
            _add_filter(_ENROLL_FIELDS[fkey], fval)
        elif fkey in _CS_COLUMNS and _SAFE_IDENT_RE.match(fkey):
            _add_filter(f'cs.{fkey}', fval)
        elif fkey in JSON_SCALAR_COLUMNS:
            _add_filter(json_scalar_expr(fkey), fval)

    # Search filter
    search = filters.get('search', '')
    if search:
        where_parts.append(
            "(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)"
        )
        like = f'%{search}%'
        params.extend([like, like, like])

    # Also check if any requested SELECT columns need the enroll join
    for c in base_cols:
        if c in _ENROLL_FIELDS:
            needs_enroll = True
            break

    # Build JOIN and SELECT
    join_sql = ''
    if needs_enroll:
        join_sql = (
            " LEFT JOIN ("
            " SELECT DISTINCT ON (student_id, school_year) *"
            " FROM student_enroll"
            " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
            " ) se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
        )

    # Build SELECT expressions — use enrollment values when join is active.
    # 'ell'/'grade' exist in both current_student and student_enroll (COALESCE
    # prefers the more current enrollment-record value); the rest of
    # _ENROLL_FIELDS (birth_date, enter_date, leave_date, ...) live ONLY in
    # student_enroll, so they must be selected via the se. join, not cs. —
    # current_student has no such column.
    select_exprs = []
    for c in base_cols:
        if c == 'ell' and needs_enroll:
            select_exprs.append('COALESCE(cs.ell, se.ell_status) AS ell')
        elif c == 'grade' and needs_enroll:
            select_exprs.append('COALESCE(se.grade, cs.grade) AS grade')
        elif c in _ENROLL_FIELDS and needs_enroll:
            select_exprs.append(f'{_ENROLL_FIELDS[c]} AS {c}')
        elif c in JSON_LIST_COLUMNS:
            select_exprs.append(f'{json_list_display_expr(JSON_LIST_COLUMNS[c], "cs")} AS {c}')
        elif c in JSON_SCALAR_COLUMNS:
            select_exprs.append(f'{json_scalar_expr(c)} AS {c}')
        else:
            select_exprs.append(f'cs.{c}')

    # Hide school years disabled via Project Settings
    try:
        from landing.utils import get_disabled_school_years
        _disabled_years = get_disabled_school_years()
        if _disabled_years:
            where_parts.append("cs.school_year NOT IN (%s)")
            params.append(_disabled_years)
    except Exception:
        pass

    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    count_where_sql = where_sql
    col_sql   = ', '.join(select_exprs)
    from_sql  = f'FROM current_student cs{join_sql}'

    order_sql = ''
    order_expr = 'cs.stu_annual_key'
    order_key = 'stu_annual_key'
    if order_by and _SAFE_IDENT_RE.match(order_by.split('.')[-1]):
        bare_ob = order_by.split('.')[-1]
        if bare_ob in _CS_COLUMNS:
            order_expr = f'cs.{bare_ob}'
            order_key = bare_ob
            order_sql = f' ORDER BY {order_expr}, cs.stu_annual_key'
        elif bare_ob in _ENROLL_FIELDS and needs_enroll:
            order_expr = _ENROLL_FIELDS[bare_ob]
            order_key = bare_ob
            order_sql = f' ORDER BY {order_expr}, cs.stu_annual_key'
    elif cursor:
        order_sql = ' ORDER BY cs.stu_annual_key'

    from clickhouse_utils.pagination import decode_cursor, keyset_clause, cursor_from_row
    parsed_cursor = decode_cursor(cursor) if cursor else None
    use_keyset = bool(parsed_cursor and parsed_cursor.get('k') == order_key)
    if use_keyset:
        where_parts.append(
            keyset_clause(order_expr, parsed_cursor['d']).removeprefix(' AND ')
        )
        params.extend([parsed_cursor['v'], parsed_cursor['v'], parsed_cursor['sak']])
        where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    limit_sql = ''
    if limit is not None and limit > 0:
        limit_sql = f' LIMIT {int(limit)}'
        if not use_keyset and offset is not None and offset > 0:
            limit_sql += f' OFFSET {int(offset)}'

    count_sql = f'SELECT COUNT(*) AS cnt {from_sql} {count_where_sql}'
    sql       = f'SELECT {col_sql} {from_sql} {where_sql}{order_sql}{limit_sql}'

    try:
        from clickhouse_utils.executor import QueryExecutor

        phase1_executor = QueryExecutor(analytics=True)
        total_rows = None
        if count_only or limit is not None:
            count_result = phase1_executor.execute(count_sql, params)
            if not count_result.get('error') and count_result.get('rows'):
                total_rows = count_result['rows'][0].get('cnt', 0)

        if count_only:
            return {
                'columns': base_cols,
                'col_types': {},
                'rows': [],
                'row_count': total_rows or 0,
                'total_rows': total_rows or 0,
                'has_more': False,
                'error': None,
            }

        phase1_result = phase1_executor.execute(sql, params)
        if phase1_result['error']:
            return {'error': phase1_result['error'], 'rows': [], 'columns': []}
        db_cols = phase1_result['columns']
        rows = phase1_result['rows']
        raw = rows  # for has_more check below

        if not rows:
            res = {
                'columns': base_cols,
                'col_types': {},
                'rows': [],
                'row_count': 0,
                'has_more': False,
                'error': None,
            }
            cache.set(cache_key, res, 1800)
            return res

        # Phase 2: test/mark/AP bulk fetch
        if rows and (need_test or need_mark or need_ap or need_award):
            enrich_filters = dict(filters)
            if tested_school_filter:
                enrich_filters['tested_school'] = tested_school_filter
            err = _enrich_rows_test_mark(rows, test_cols, mark_cols, enrich_filters, ap_cols=ap_cols, award_cols=award_cols)
            if err:
                return {'error': err, 'rows': [], 'columns': []}

            if rows and dynamic_filters:
                rows = [
                    row for row in rows
                    if all(_matches_dynamic_filter(row.get(filter_key), filter_value) for filter_key, filter_value in dynamic_filters.items())
                ]

        # Build final ordered column list (base + test + mark + AP)
        all_cols = list(db_cols)
        for tc in test_cols:
            if tc not in all_cols:
                all_cols.append(tc)
        for mc in mark_cols:
            if mc not in all_cols:
                all_cols.append(mc)
        for ac in ap_cols + award_cols:
            if ac not in all_cols:
                all_cols.append(ac)

        col_types: dict = {}
        for col in all_cols:
            for row in rows:
                v = row.get(col)
                if v is not None and v != '\u2014':
                    col_types[col] = 'number' if isinstance(v, (int, float)) else 'string'
                    break
            col_types.setdefault(col, 'string')

        next_cursor = cursor_from_row(rows[-1], order_key) if rows else ''
        res = {
            'columns':   all_cols,
            'col_types': col_types,
            'rows':      rows,
            'row_count': len(rows),
            'total_rows': total_rows if total_rows is not None else len(rows),
            'has_more':  False,
            'next_cursor': next_cursor,
            'error':     None,
        }
        # Cache for 30 minutes (1800s) — confirmed acceptable staleness window
        cache.set(cache_key, res, 1800)
        return res
    except Exception as exc:
        return {'error': str(exc), 'rows': [], 'columns': []}
