"""Utilities for dynamic column generation from database."""

import hashlib
import json
import re
from django.core.cache import cache
from django.db import connections
from .constants import COLUMN_DEFINITIONS


def _get_display_name(name):
    """Normalize test names for display (e.g. Dibbles -> Dibels)."""
    if not name:
        return name
    name = str(name)
    if 'Dibbles' in name or 'dibels' in name.lower():
        name = re.sub(r'(?i)(?:Dibbles|Dibels)', 'Dibels', name)
    if 'iready' in name.lower():
        name = re.sub(r'(?i)iready', 'iReady', name)
    return name


def test_metadata_matches(info: dict, row_test_name, row_subject, row_window=None) -> bool:
    """Return True when a results row belongs to the given test column metadata.

    Uses exact case-insensitive equality — never substring checks — so similarly
    named assessments (e.g. CAA vs CAASPP) are never mixed.
    """
    if (info.get('test_name') or '').strip().lower() != (row_test_name or '').strip().lower():
        return False
    if (info.get('subject') or '').strip().lower() != (row_subject or '').strip().lower():
        return False
    expected_window = (info.get('test_window') or '').strip()
    actual_window = (row_window or '').strip()
    if not expected_window:
        return True
    if not actual_window:
        return False
    return expected_window.lower() == actual_window.lower()


# Prefer SSID when the result row has one. Local student IDs are reused across
# students, so matching on local_student_id alone attaches the wrong scores.
RESULTS_STUDENT_MATCH_SQL = (
    "("
    "(r.ssid IS NOT NULL AND r.ssid != '' AND r.ssid != 'NaN' AND cs.ssid IS NOT NULL AND cs.ssid != '' AND r.ssid = cs.ssid)"
    " OR "
    "((r.ssid IS NULL OR r.ssid = '' OR r.ssid = 'NaN') AND r.local_student_id = cs.student_id)"
    ")"
)

# Bulk fetch: params are (student_ssids, student_ids) in that order.
RESULTS_BULK_MATCH_SQL = (
    "("
    "(r.ssid IS NOT NULL AND r.ssid != '' AND r.ssid != 'NaN' AND r.ssid = ANY(%s))"
    " OR "
    "((r.ssid IS NULL OR r.ssid = '' OR r.ssid = 'NaN') AND r.local_student_id = ANY(%s))"
    ")"
)

RESULTS_BLANK_SSID_SQL = "(r.ssid IS NULL OR r.ssid = '' OR r.ssid = 'NaN')"

# Placeholder values produced by assessment data imports when a student has no
# usable score (NaN, NS, N/A, ...). They must never surface on charts/tables as
# raw strings, so ``normalize_score_value()`` maps them to ``None`` (blank).
SCORE_SENTINELS = frozenset({
    '', 'nan', 'ns', 'n/a', 'na', 'n/s', 'null', 'none',
    'inf', '-inf', 'infinity', '-infinity',
})

# SQL fragment: treat import sentinels as NULL so they sort with blanks.
_PL_BLANK_SQL = (
    "r.pl IS NULL OR btrim(r.pl) = '' OR lower(btrim(r.pl)) IN "
    "('nan','ns','n/a','na','n/s','null','none')"
)

# Initial ELPAC (and similar) use text PL labels, not 1-4. Map to a stable
# proficiency order so ASC goes Novice → Intermediate → IFEP instead of
# alphabetical ("Initial Fluent..." before "Intermediate...").
# NOTE: ``%%`` so psycopg does not treat LIKE wildcards as placeholders.
PL_SORT_SQL = (
    "CASE "
    f"WHEN {_PL_BLANK_SQL} THEN NULL "
    "WHEN lower(btrim(r.pl)) LIKE 'novice%%' THEN 1 "
    "WHEN lower(btrim(r.pl)) LIKE 'intermediate%%' THEN 2 "
    "WHEN lower(btrim(r.pl)) LIKE '%%ifep%%' "
    "  OR lower(btrim(r.pl)) LIKE 'initial fluent%%' THEN 3 "
    "ELSE CAST(NULLIF(regexp_replace(btrim(r.pl), '[^0-9.].*', '', 'g'), '') AS NUMERIC) "
    "END"
)

SS_SORT_SQL = (
    "CASE "
    "WHEN r.ss IS NULL OR btrim(r.ss) = '' OR lower(btrim(r.ss)) IN "
    "('nan','ns','n/a','na','n/s','null','none') THEN NULL "
    "ELSE CAST(NULLIF(regexp_replace(btrim(r.ss), '[^0-9.]', '', 'g'), '') AS NUMERIC) "
    "END"
)


def parse_apall_sort_key(col_key: str):
    """Parse ``apall_<YYYY_YYYY>_<n>_SUBJECT|PL`` into year/index/field, or None."""
    if not isinstance(col_key, str) or not col_key.startswith('apall_'):
        return None
    # apall_2024_2025_1_SUBJECT
    rest = col_key[len('apall_'):]
    parts = rest.split('_')
    if len(parts) < 4:
        return None
    # year is YYYY_YYYY (two parts), then index, then field
    try:
        y0, y1, idx_s, field = parts[0], parts[1], parts[2], parts[3]
        if len(y0) != 4 or len(y1) != 4 or field not in ('SUBJECT', 'PL'):
            return None
        return {
            'year': f'{y0}-{y1}',
            'index': int(idx_s),
            'field': field,
        }
    except (ValueError, TypeError, IndexError):
        return None


def normalize_score_value(value):
    """Return ``None`` for missing/placeholder score values, else the trimmed value."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in SCORE_SENTINELS:
        return None
    return s


def export_cell(value, blank='\u2014'):
    """Return a CSV cell value with blank/sentinel values shown as the blank marker.

    Maps ``None``, empty strings and sentinel score values (NaN/NS/N/A ...) to the
    same ``\u2014`` marker used across charts and the data explorer, so exports stay
    consistent instead of surfacing raw ``NaN`` / ``None`` strings.
    """
    v = normalize_score_value(value)
    return blank if v is None else v


def assign_result_value_maps(rows):
    """Build local-id / ssid maps from ``(local_student_id, ssid, value)`` rows.

    Rows that include an SSID are indexed only by SSID. Local-id indexing is
    reserved for results with a blank SSID so recycled local IDs cannot leak.
    """
    by_id = {}
    by_ssid = {}
    for row in rows:
        local_id, ssid, val = row[0], row[1], row[2]
        ssid_s = str(ssid).strip() if ssid is not None else ''
        if ssid_s:
            by_ssid[ssid_s] = normalize_score_value(val)
        elif local_id:
            by_id[str(local_id).strip()] = normalize_score_value(val)
    return by_id, by_ssid


def lookup_result_value(student, by_id, by_ssid, blank='—'):
    """Resolve a student's assessment value, preferring SSID over local ID."""
    ssid = student.get('ssid')
    if ssid is not None:
        ssid_s = str(ssid).strip()
        if ssid_s and ssid_s in by_ssid:
            return by_ssid[ssid_s] or blank
    sid = student.get('student_id')
    if sid is not None:
        sid_s = str(sid).strip()
        if sid_s and sid_s in by_id:
            return by_id[sid_s] or blank
    return blank


def _json_key_value(raw, key):
    if raw is None or not key:
        return None
    if isinstance(raw, dict):
        return raw.get(key)
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return obj.get(key) if isinstance(obj, dict) else None


def _fold(val):
    return (str(val) if val is not None else '').strip().casefold()


def enrich_students_tests_marks(students, test_cols_info=None, mark_cols_info=None, blank='—'):
    """Fill test_* / mark_* cells with one ClickHouse round-trip each."""
    if not students:
        return
    from clickhouse_utils.executor import QueryExecutor

    test_cols_info = test_cols_info or []
    mark_cols_info = mark_cols_info or []
    exec_ = QueryExecutor(analytics=True)

    if test_cols_info:
        student_ssids = [s.get('ssid') for s in students if s.get('ssid')]
        student_ids = [s.get('student_id') for s in students if s.get('student_id')]
        years = list({info['year'] for info in test_cols_info if info.get('year')})
        result = exec_.execute(
            f"SELECT r.local_student_id, r.ssid, r.school_year, r.test_name, r.subject, "
            f"r.test_window, r.ss, r.pl, r.other_info "
            f"FROM analytics_student_results r "
            f"WHERE {RESULTS_BULK_MATCH_SQL} AND r.school_year = ANY(%s)",
            [student_ssids, student_ids, years],
        )
        rows = []
        if not result.get('error'):
            for row in result.get('rows') or []:
                rows.append((
                    row.get('local_student_id'), row.get('ssid'), row.get('school_year'),
                    row.get('test_name'), row.get('subject'), row.get('test_window'),
                    row.get('ss'), row.get('pl'), row.get('other_info'),
                ))
        for test_info in test_cols_info:
            want_year = _fold(test_info.get('year'))
            want_name = _fold(test_info.get('test_name'))
            want_subj = _fold(test_info.get('subject'))
            want_win = _fold(test_info.get('test_window')) if test_info.get('test_window') else None
            metric = test_info.get('metric')
            matched = []
            for local_id, ssid, year, tname, subj, window, ss, pl, other in rows:
                if _fold(year) != want_year or _fold(tname) != want_name or _fold(subj) != want_subj:
                    continue
                if want_win is not None and _fold(window) != want_win:
                    continue
                if metric == 'ss':
                    val = ss
                elif metric == 'pl':
                    val = pl
                else:
                    val = _json_key_value(other, test_info.get('other_info_key', ''))
                matched.append((local_id, ssid, val))
            by_id, by_ssid = assign_result_value_maps(matched)
            for student in students:
                student[test_info['col_name']] = lookup_result_value(
                    student, by_id, by_ssid, blank=blank,
                )

    if mark_cols_info:
        student_keys = [s.get('stu_annual_key') for s in students if s.get('stu_annual_key')]
        years = list({info['year'] for info in mark_cols_info if info.get('year')})
        result = exec_.execute(
            "SELECT sm.stu_annual_key, sm.school_year, sm.subject, sm.course_name, "
            "sm.mark_period, sm.mark FROM analytics_student_marks sm "
            "WHERE sm.stu_annual_key = ANY(%s) AND sm.school_year = ANY(%s)",
            [student_keys, years],
        )
        rows = []
        if not result.get('error'):
            for row in result.get('rows') or []:
                rows.append((
                    row.get('stu_annual_key'), row.get('school_year'), row.get('subject'),
                    row.get('course_name'), row.get('mark_period'), row.get('mark'),
                ))
        for mark_info in mark_cols_info:
            want_year = _fold(mark_info.get('year'))
            subj_needle = _fold(mark_info.get('subject'))
            course_needle = _fold(mark_info.get('course_name'))
            want_period = _fold(mark_info['mark_period']) if mark_info.get('mark_period') else None
            mark_data = {}
            for sak, year, subject, course, period, mark in rows:
                if _fold(year) != want_year:
                    continue
                if subj_needle not in _fold(subject) or course_needle not in _fold(course):
                    continue
                if want_period is None:
                    if _fold(period):
                        continue
                elif _fold(period) != want_period:
                    continue
                mark_data[sak] = mark
            for student in students:
                student[mark_info['col_name']] = mark_data.get(
                    student.get('stu_annual_key'), blank,
                )


def _generate_cache_key(prefix, filters, extra_hash_str=""):
    """Generate a consistent cache key from filters and extra conditions."""
    if filters:
        # Sort keys to ensure consistent hash
        filter_str = json.dumps(filters, sort_keys=True)
    else:
        filter_str = "None"
    
    combined_str = f"{filter_str}_{extra_hash_str}"
    key_hash = hashlib.md5(combined_str.encode('utf-8')).hexdigest()
    return f"{prefix}_{key_hash}"


def ap_column_key(year, subject, kind):
    """Build a stable column key for AP exam results / awards.

    kind is ``'exam'`` (Performance Level) or ``'award'``.
    Used both when building the column tree and when enriching query rows so the
    keys always match.
    """
    year_key = re.sub(r'[^A-Za-z0-9_]+', '_', (year or '')).strip('_') or 'Unknown'
    part = re.sub(r'[^A-Za-z0-9_]+', '_', (subject or '')).strip('_') or 'Unknown'
    if kind == 'exam':
        return f"ap_{year_key}_{part}_PL"
    return f"apaward_{year_key}_{part}"


def markall_key(year, subject):
    """Build the 'All Marks' umbrella column key for a subject/year.

    e.g. markall_2025_2026_Math, markall_2025_2026_Physical_Education
    """
    year_key = re.sub(r'[^A-Za-z0-9_]+', '_', (year or '')).strip('_') or 'Unknown'
    subj_key = re.sub(r'[^A-Za-z0-9_]+', '_', (subject or '')).strip('_') or 'Unknown'
    return f"markall_{year_key}_{subj_key}"


def parse_markall_key(col_key):
    """Resolve a ``markall_`` umbrella/expanded key to (year, subject_norm, idx, kind).

    Kind is 'COURSE', 'MARKS', or None for the umbrella key.
    Returns None if not a markall_ key.
    """
    if not isinstance(col_key, str) or not col_key.startswith('markall_'):
        return None
    tail = col_key[len('markall_'):]
    parts = tail.split('_')
    if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    year = f"{parts[0]}-{parts[1]}"
    # Remaining tokens: subject(s) then optional _<idx>_<COURSE|MARKS>
    rest = parts[2:]
    idx = None
    kind = None
    if len(rest) >= 2 and rest[-2].isdigit() and rest[-1] in ('COURSE', 'MARKS'):
        idx = int(rest[-2])
        kind = rest[-1]
        rest = rest[:-2]
    subject_norm = '_'.join(rest)
    return {'year': year, 'subject_norm': subject_norm, 'idx': idx, 'kind': kind}


def get_ap_columns(column_filters=None):
    """Get dynamic AP exam + award columns organized by year.

    Returns structure:
        {
            year: {
                'subjects': { subject: {'PL': column_key} },
                'awards':   { award_description: column_key },
            }
        }
    Data comes from the College Board AP tables loaded by the Data Loader app
    (``dataLoader_apexamresult`` / ``dataLoader_apstudentaward``).
    """
    cache_key = _generate_cache_key("ap_columns", column_filters, "")
    cached_data = cache.get(cache_key)

    if cached_data:
        columns, definitions = cached_data
        COLUMN_DEFINITIONS.update(definitions)
        return columns

    new_definitions = {}
    columns = {}

    with connections['default'].cursor() as cursor:
        # AP exam performance levels (subject = exam name) — only published imports
        cursor.execute(
            """
            SELECT DISTINCT r.admin_year, e.name
            FROM "dataLoader_apexamresult" r
            JOIN "dataLoader_apexamcode" e ON r.exam_id = e.code
            JOIN "dataLoader_apimportlog" il ON r.import_log_id = il.id
            WHERE r.exam_grade IS NOT NULL AND r.exam_grade <> ''
              AND il.is_published = true
            ORDER BY r.admin_year DESC, e.name
            """
        )
        for year, subject in cursor.fetchall():
            year_val = (year or '').strip() or 'Unknown'
            subject_val = (subject or '').strip() or 'Unknown'
            col_key = ap_column_key(year_val, subject_val, 'exam')
            label = f"{subject_val}\nPerformance Level\n{year_val}"
            COLUMN_DEFINITIONS[col_key] = label
            new_definitions[col_key] = label
            bucket = columns.setdefault(year_val, {'subjects': {}, 'awards': {}})
            bucket['subjects'][subject_val] = {'PL': col_key}

        # AP awards — only published imports
        cursor.execute(
            """
            SELECT DISTINCT a.award_year, t.description
            FROM "dataLoader_apstudentaward" a
            JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
            JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
            WHERE il.is_published = true
            ORDER BY a.award_year DESC, t.description
            """
        )
        for year, award in cursor.fetchall():
            year_val = (year or '').strip() or 'Unknown'
            award_val = (award or '').strip() or 'Unknown'
            col_key = ap_column_key(year_val, award_val, 'award')
            label = f"{award_val}\n{year_val}"
            COLUMN_DEFINITIONS[col_key] = label
            new_definitions[col_key] = label
            bucket = columns.setdefault(year_val, {'subjects': {}, 'awards': {}})
            bucket['awards'][award_val] = col_key

        # Combined "All Awards" column per year (lists every award the student earned)
        for year_val in columns:
            all_key = f"apawards_all_{year_val.replace('-', '_')}"
            label = f"All Awards\n{year_val}"
            COLUMN_DEFINITIONS[all_key] = label
            new_definitions[all_key] = label
            columns[year_val]['all_awards'] = all_key

        # "All Results" option per year — expands into wide subject/PL pairs
        for year_val in columns:
            all_results_key = f"apall_{year_val.replace('-', '_')}"
            label = f"All Results\n{year_val}"
            COLUMN_DEFINITIONS[all_results_key] = label
            new_definitions[all_results_key] = label
            columns[year_val]['all_results'] = all_results_key

    cache.set(cache_key, (columns, new_definitions), timeout=3600)
    return columns


def get_test_columns(column_filters=None, extra_join_sql="", extra_where_clauses=None, extra_params=None):
    """Get dynamic test columns from database organized by year and test.
    
                Returns structure:
                        {
                            year: {
                                assessment_name: {
                                    'windows': {test_window: {testing_name: {'SS': ss_key, 'PL': pl_key}}},
                                    'additional_info': {test_window: {testing_name: [{'key': col_key, 'label': label}, ...]}}
                                }
                            }
                        }
        Notes:
            - If a test has no test_window, it is grouped under an empty-string key '' and keeps legacy column keys.
            - If a test has a test_window, the window is included in the column key to avoid collisions.
                        - Additional assessment info comes from results.other_info (JSONB) keys.
    """
    extra_hash_str = f"{extra_join_sql}_{json.dumps(extra_where_clauses)}_{json.dumps(extra_params)}" if extra_join_sql or extra_where_clauses else ""
    cache_key = _generate_cache_key("test_columns_v4", column_filters, extra_hash_str)
    cached_data = cache.get(cache_key)

    if cached_data:
        columns, definitions = cached_data
        COLUMN_DEFINITIONS.update(definitions)
        return columns

    new_definitions = {}

    with connections['default'].cursor() as cursor:
        where_clauses = ["r.school_year IS NOT NULL", "t.test_name IS NOT NULL"]
        params = []
        if extra_where_clauses:
            where_clauses.extend(extra_where_clauses)
        if extra_params:
            params.extend(extra_params)

        # Add column-specific filters from base student data
        if column_filters:
            for col, raw_val in column_filters.items():
                if isinstance(raw_val, (list, tuple, set)):
                    vals = [str(v).strip() for v in raw_val if v is not None and str(v).strip()]
                elif raw_val and str(raw_val).strip() and str(raw_val).strip() != 'All':
                    vals = [str(raw_val).strip()]
                else:
                    vals = []

                if not vals:
                    continue

                if col == 'search':
                    search_param = f"%{vals[0]}%"
                    where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
                    params.extend([search_param, search_param, search_param])
                elif col in {'school_year', 'grade', 'status', 'school_state_id'}:
                    where_clauses.append(f"cs.{col} = ANY(%s)")
                    params.append(vals)
                elif col in COLUMN_DEFINITIONS and not col.startswith(('test_', 'mark_', 'survey_')):
                    where_clauses.append(f"cs.{col} = ANY(%s)")
                    params.append(vals)

        where_sql = " AND ".join(where_clauses)

        # Detect whether tests.test_window exists
        cursor.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'"
        )
        has_test_window = cursor.fetchone() is not None

        if has_test_window:
            query = f"""
            SELECT DISTINCT
                r.school_year,
                t.test_name,
                t.subject,
                t.test_window
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            INNER JOIN current_student cs ON r.local_student_id = cs.student_id
            {extra_join_sql}
            WHERE {where_sql}
            ORDER BY r.school_year DESC, t.test_name, t.subject, t.test_window
            """
        else:
            query = f"""
            SELECT DISTINCT
                r.school_year,
                t.test_name,
                t.subject
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            INNER JOIN current_student cs ON r.local_student_id = cs.student_id
            {extra_join_sql}
            WHERE {where_sql}
            ORDER BY r.school_year DESC, t.test_name, t.subject
            """
        
        cursor.execute(query, params)

        def _norm_part(v):
            return (v or '').replace(' ', '_').replace('-', '_')

        # Structure:
        # {year: {assessment_name: {'windows': {...}, 'additional_info': {...}}}}
        columns = {}
        for row in cursor.fetchall():
            if has_test_window:
                year, test_name, subject, test_window = row
            else:
                year, test_name, subject = row
                test_window = None

            assessment_name = _get_display_name((test_name or '').strip()) or 'Assessment'
            clean_subject = _get_display_name((subject or '').strip())
            testing_name = clean_subject or 'Testing'
            window_name = (test_window or '').strip()
            
            # Normalize for column key
            # Keep legacy keys when there is no window; include window in the key when present to avoid collisions.
            if window_name:
                test_key = f"{year}_{test_name}_{subject}_{window_name}".replace(" ", "_").replace("-", "_")
            else:
                test_key = f"{year}_{test_name}_{subject}".replace(" ", "_").replace("-", "_")

            ss_key = f"test_{test_key}_SS"
            pl_key = f"test_{test_key}_PL"

            # Store in COLUMN_DEFINITIONS with descriptive names
            # Keep column header labels consistent with the existing format.
            # Test window is represented in the selection tree, not the header text.
            display_test_name = _get_display_name(test_name)
            label_base = f"{display_test_name} {clean_subject} ({year})"
            
            COLUMN_DEFINITIONS[ss_key] = f"{label_base} SS"
            new_definitions[ss_key] = COLUMN_DEFINITIONS[ss_key]
            
            COLUMN_DEFINITIONS[pl_key] = f"{label_base} PL"
            new_definitions[pl_key] = COLUMN_DEFINITIONS[pl_key]

            if year not in columns:
                columns[year] = {}

            if assessment_name not in columns[year]:
                columns[year][assessment_name] = {
                    'windows': {},
                    'additional_info': {},
                }

            if '' not in columns[year][assessment_name]['windows'] and window_name == '':
                columns[year][assessment_name]['windows'][''] = {}
            if window_name not in columns[year][assessment_name]['windows']:
                columns[year][assessment_name]['windows'][window_name] = {}

            columns[year][assessment_name]['windows'][window_name][testing_name] = {
                'SS': ss_key,
                'PL': pl_key,
            }

        # Fetch additional assessment info keys from results.other_info (JSONB)
        # Grouped in the UI under: Assessment -> Additional Assessment Info -> (Window) -> Testing -> keys
        other_info_query = None
        if has_test_window:
            other_info_query = f"""
            SELECT DISTINCT
                r.school_year,
                t.test_name,
                t.subject,
                t.test_window,
                k.key
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            INNER JOIN current_student cs ON r.local_student_id = cs.student_id
            CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
            WHERE {where_sql}
              AND r.other_info IS NOT NULL
            ORDER BY r.school_year DESC, t.test_name, t.subject, t.test_window, k.key
            """
        else:
            other_info_query = f"""
            SELECT DISTINCT
                r.school_year,
                t.test_name,
                t.subject,
                NULL AS test_window,
                k.key
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            INNER JOIN current_student cs ON r.local_student_id = cs.student_id
            CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
            WHERE {where_sql}
              AND r.other_info IS NOT NULL
            ORDER BY r.school_year DESC, t.test_name, t.subject, k.key
            """

        try:
            cursor.execute(other_info_query, params)
            for (year, test_name, subject, test_window, raw_key) in cursor.fetchall():
                if not raw_key:
                    continue
                assessment_name = _get_display_name((test_name or '').strip()) or 'Assessment'
                clean_subject = _get_display_name((subject or '').strip())
                testing_name = clean_subject or 'Testing'
                window_name = (test_window or '').strip() if test_window else ''

                if year not in columns:
                    columns[year] = {}
                if assessment_name not in columns[year]:
                    columns[year][assessment_name] = {
                        'windows': {},
                        'additional_info': {},
                    }

                # Keep key generation consistent with SS/PL keys (window included only when present)
                if window_name:
                    test_key = f"{year}_{test_name}_{subject}_{window_name}".replace(" ", "_").replace("-", "_")
                else:
                    test_key = f"{year}_{test_name}_{subject}".replace(" ", "_").replace("-", "_")

                raw_key_str = str(raw_key)
                key_norm = _norm_part(raw_key_str)

                # Table header label should remain consistent with the existing format.
                display_test_name = _get_display_name(test_name)
                label_base = f"{display_test_name} {clean_subject} ({year})"

                # Promote DFP out of "Additional Info" to live alongside SS and PL
                if raw_key_str.lower() == 'dfp':
                    dfp_key = f"test_{test_key}_DFP"
                    COLUMN_DEFINITIONS[dfp_key] = f"{label_base} DFP"
                    new_definitions[dfp_key] = COLUMN_DEFINITIONS[dfp_key]

                    # Ensure the windows/testing_name entry exists
                    if window_name not in columns[year][assessment_name]['windows']:
                        columns[year][assessment_name]['windows'][window_name] = {}
                    if testing_name not in columns[year][assessment_name]['windows'][window_name]:
                        columns[year][assessment_name]['windows'][window_name] = {}
                    columns[year][assessment_name]['windows'][window_name][testing_name]['DFP'] = dfp_key
                    continue

                key_hash = hashlib.md5(raw_key_str.encode('utf-8')).hexdigest()[:6]
                other_key = f"test_{test_key}_OI_{key_norm}_{key_hash}"

                COLUMN_DEFINITIONS[other_key] = f"{label_base} {raw_key_str}"
                new_definitions[other_key] = COLUMN_DEFINITIONS[other_key]

                add = columns[year][assessment_name]['additional_info']
                if window_name not in add:
                    add[window_name] = {}
                if testing_name not in add[window_name]:
                    add[window_name][testing_name] = []
                add[window_name][testing_name].append({'key': other_key, 'label': raw_key_str})
        except Exception:
            # If other_info is missing/unavailable, keep base assessment structure working.
            pass

        cache.set(cache_key, (columns, new_definitions), timeout=3600)
        return columns


def invert_test_to_assessment_first(test_structure: dict) -> dict:
    """Transform year-first test structure into assessment-first.

    Input:  {year: {assessment: {windows: {window: {testing_name: {SS: key, PL: key}}}}}}
    Output: {assessment: {testing_name: {years: {year: {windows: {window: {SS: key, PL: key}}}}}}}
    """
    result = {}
    for year, assessments in test_structure.items():
        for assess_name, assess_data in assessments.items():
            if assess_name not in result:
                result[assess_name] = {}

            # Process windows
            for window_name, win_data in assess_data.get('windows', {}).items():
                for testing_name, metrics in win_data.items():
                    if testing_name not in result[assess_name]:
                        result[assess_name][testing_name] = {'years': {}, 'additional_info': {}}
                    yr = result[assess_name][testing_name]['years']
                    if year not in yr:
                        yr[year] = {'windows': {}}
                    if window_name not in yr[year]['windows']:
                        yr[year]['windows'][window_name] = {}
                    # Nest by testing_name within the window to avoid overwriting
                    yr[year]['windows'][window_name][testing_name] = metrics

            # Process additional_info
            for window_name, win_data in assess_data.get('additional_info', {}).items():
                for testing_name, keys in win_data.items():
                    if testing_name not in result[assess_name]:
                        result[assess_name][testing_name] = {'years': {}, 'additional_info': {}}
                    ai = result[assess_name][testing_name]['additional_info']
                    if year not in ai:
                        ai[year] = {}
                    if window_name not in ai[year]:
                        ai[year][window_name] = {}
                    ai[year][window_name][testing_name] = keys

    # Sort years descending within each subject for consistent display
    for assess_name in result:
        for testing_name in result[assess_name]:
            yr = result[assess_name][testing_name].get('years', {})
            result[assess_name][testing_name]['years'] = dict(
                sorted(yr.items(), key=lambda x: x[0], reverse=True)
            )
    return result


def invert_marks_to_assessment_first(mark_structure: dict) -> dict:
    """Transform year-first mark structure into subject/course-first.

    Input:  {year: {subject: {course: {period: key}}}}
    Output: {subject: {course: {years: {year: {period: key}}}}}
    """
    result = {}
    for year, subjects in mark_structure.items():
        for subject, courses in subjects.items():
            if subject not in result:
                result[subject] = {}
            for course, periods in courses.items():
                if course not in result[subject]:
                    result[subject][course] = {'years': {}}
                if year not in result[subject][course]['years']:
                    result[subject][course]['years'][year] = {}
                result[subject][course]['years'][year] = periods

    # Sort years descending within each course
    for subject in result:
        for course in result[subject]:
            yr = result[subject][course].get('years', {})
            result[subject][course]['years'] = dict(
                sorted(yr.items(), key=lambda x: x[0], reverse=True)
            )
    return result


def get_mark_columns(column_filters=None, extra_join_sql="", extra_where_clauses=None, extra_params=None):
    """Get dynamic mark columns from student_marks_list organized by year/subject/course/period.

    Returns structure:
        {
            year: {
                subject: {
                    course_name: {
                        period: column_key
                    }
                }
            }
        }
    """
    extra_hash_str = f"{extra_join_sql}_{json.dumps(extra_where_clauses)}_{json.dumps(extra_params)}" if extra_join_sql or extra_where_clauses else ""
    cache_key = _generate_cache_key("mark_columns", column_filters, extra_hash_str)
    cached_data = cache.get(cache_key)

    if cached_data:
        columns, definitions = cached_data
        COLUMN_DEFINITIONS.update(definitions)
        return columns

    new_definitions = {}

    with connections['default'].cursor() as cursor:
        where_clauses = ["sm.school_year IS NOT NULL"]
        params = []
        if extra_where_clauses:
            where_clauses.extend(extra_where_clauses)
        if extra_params:
            params.extend(extra_params)

        # Apply base column filters from current_student
        if column_filters:
            for col, raw_val in column_filters.items():
                if isinstance(raw_val, (list, tuple, set)):
                    vals = [str(v).strip() for v in raw_val if v is not None and str(v).strip()]
                elif raw_val and str(raw_val).strip() and str(raw_val).strip() != 'All':
                    vals = [str(raw_val).strip()]
                else:
                    vals = []

                if not vals:
                    continue

                if col == 'search':
                    search_param = f"%{vals[0]}%"
                    where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
                    params.extend([search_param, search_param, search_param])
                elif col in {'school_year', 'grade', 'status', 'school_state_id'}:
                    where_clauses.append(f"cs.{col} = ANY(%s)")
                    params.append(vals)
                elif col in COLUMN_DEFINITIONS and not col.startswith(('test_', 'mark_', 'survey_')):
                    where_clauses.append(f"cs.{col} = ANY(%s)")
                    params.append(vals)

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT DISTINCT
                sm.school_year,
                sm.subject,
                sm.course_name,
                sm.mark_period
            FROM student_marks_list sm
            INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
            {extra_join_sql}
            WHERE {where_sql}
            ORDER BY sm.school_year DESC, sm.subject, sm.course_name, sm.mark_period
        """

        cursor.execute(query, params)

        columns = {}
        for (year, subject, course_name, mark_period) in cursor.fetchall():
            year_val = (year or '').strip() or 'Unknown'
            subject_val = (subject or '').strip() or 'Unknown'
            course_val = (course_name or '').strip() or 'Course'
            period_val = (mark_period or '').strip() or 'Unknown'

            key_base = f"{year_val}_{subject_val}_{course_val}_{period_val}".replace(" ", "_").replace("-", "_")
            mark_key = f"mark_{key_base}"

            label = f"{course_val} ({subject_val}) {period_val}\n{year_val}"
            COLUMN_DEFINITIONS[mark_key] = label
            new_definitions[mark_key] = label

            if year_val not in columns:
                columns[year_val] = {}
            if subject_val not in columns[year_val]:
                columns[year_val][subject_val] = {}
            if course_val not in columns[year_val][subject_val]:
                columns[year_val][subject_val][course_val] = {}

            columns[year_val][subject_val][course_val][period_val] = mark_key

        # "All Marks" umbrella per subject — expands into course/marks wide pairs.
        # Keys are deterministic (markall_<year>_<subject>) so they can be
        # recomputed by callers; definitions are exposed for label resolution.
        for year_val, subjects in columns.items():
            year_key = year_val.replace(' ', '_').replace('-', '_')
            for subject_val in subjects:
                subj_key = subject_val.replace(' ', '_').replace('-', '_')
                umbrella = f"markall_{year_key}_{subj_key}"
                label = f"All Marks\n{subject_val}\n{year_val}"
                COLUMN_DEFINITIONS[umbrella] = label
                new_definitions[umbrella] = label

        cache.set(cache_key, (columns, new_definitions), timeout=3600)
        return columns
