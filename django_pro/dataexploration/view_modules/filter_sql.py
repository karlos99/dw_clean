"""Shared SQL filter builders for Data Explorer list + export views."""

import re
from datetime import date, datetime, timezone

from ..column_utils import (
    RESULTS_BLANK_SSID_SQL,
    parse_markall_key,
    markall_key as _markall_key,
    get_mark_columns,
    parse_sat_column_key,
    sat_column_db_field,
    parse_apall_sort_key,
    _normalise_markall_course_picker,
)
from .column_management import (
    get_column_expression,
    json_list_key_for_column,
    append_json_list_filter,
)


def normalize_filter_param(col, value):
    """Coerce filter values to native Python types for typed columns."""
    if value in (None, ''):
        return value

    if col in {'birth_date', 'us_enter_date', 'fep_date'}:
        if isinstance(value, (datetime, date)):
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return value
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                try:
                    parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
                except ValueError:
                    return value
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

    return value


def markall_excluded_course_sql():
    """Exclude Homeroom placeholder courses from Elementary All Marks filters."""
    return "(sm.course_name NOT ILIKE '%%Homeroom%%')"


def markall_course_filter_parts(subject, course_picker=None, excludes=None):
    """SQL predicate + extra params restricting which courses participate.

    Homeroom courses (e.g. "Homeroom Gr 1", "IS Homeroom Gr KN-5") are
    administrative placeholder courses and are filtered out of the Elementary
    subject's "All Marks" view.

    In **all** mode, ``courses`` are names to exclude (course picker unchecks).
    In **only** mode, ``courses`` are the sole names to include.

    Returns ``(sql_fragment, extra_params)``; ``sql_fragment`` is empty when no
    restriction applies beyond Elementary Homeroom handling.
    """
    picker = _normalise_markall_course_picker(course_picker, excludes)
    mode = picker['mode']
    names = picker['courses']
    clauses = []
    extra = []
    if subject == 'Elementary':
        clauses.append("(sm.course_name NOT ILIKE '%%Homeroom%%')")
    if mode == 'only':
        if names:
            sorted_names = sorted(names)
            placeholders = ", ".join(["%s"] * len(sorted_names))
            clauses.append(f"sm.course_name IN ({placeholders})")
            extra.extend(sorted_names)
        else:
            clauses.append("(1=0)")
    elif names:
        sorted_names = sorted(names)
        placeholders = ", ".join(["%s"] * len(sorted_names))
        clauses.append(f"sm.course_name NOT IN ({placeholders})")
        extra.extend(sorted_names)
    if not clauses:
        return "", []
    return (" AND " + " AND ".join(clauses), extra)


def markall_subject_actual(year, subject_norm):
    """Resolve a normalized markall subject token back to its real spelling."""
    try:
        marks_struct = get_mark_columns()
        subjects = marks_struct.get(year, {})
        for subject in subjects:
            if _markall_key(year, subject) == _markall_key(year, subject_norm):
                return subject
    except Exception:
        pass
    return subject_norm.replace('_', ' ')


def is_markall_umbrella_key(col):
    """True for a ``markall_`` umbrella key (no ``_<i>_COURSE``/``_MARKS`` suffix)."""
    if not isinstance(col, str) or not col.startswith('markall_'):
        return False
    parsed = parse_markall_key(col)
    return bool(parsed) and parsed['kind'] is None


def extract_markall_course_picker(filters_to_apply):
    """Read per-umbrella All Marks course picker state from a filters dict.

    **All** mode (default): ``exclude:<course>`` values — unchecked courses.
    **Only** mode: ``mode:only`` plus ``include:<course>`` for each selected course.

    Returns ``{umbrella_key: {'mode': 'all'|'only', 'courses': set}}``.
    """
    pickers = {}
    if not isinstance(filters_to_apply, dict):
        return pickers
    for col, values in filters_to_apply.items():
        if not is_markall_umbrella_key(col):
            continue
        mode = 'all'
        names = set()
        for value in values or []:
            value_str = str(value)
            if value_str == 'mode:only':
                mode = 'only'
                names = set()
            elif mode == 'only' and value_str.startswith('include:'):
                names.add(value_str[len('include:'):])
            elif mode == 'all' and value_str.startswith('exclude:'):
                names.add(value_str[len('exclude:'):])
        if mode == 'only' or names:
            pickers[col] = {'mode': mode, 'courses': names}
        elif mode == 'only':
            pickers[col] = {'mode': 'only', 'courses': set()}
    return pickers


def extract_markall_course_excludes(filters_to_apply):
    """Legacy helper: exclude sets for umbrellas still in **all** mode."""
    pickers = extract_markall_course_picker(filters_to_apply)
    return {
        k: v['courses']
        for k, v in pickers.items()
        if v.get('mode') == 'all' and v.get('courses')
    }


def apply_column_filters(
    where_clauses,
    params,
    filters_to_apply,
    test_col_map,
    mark_col_map,
    db_columns,
    enroll_columns,
    needs_enroll,
    needs_students=False,
    needs_eht=False,
    ap_exam_infos=None,
    ap_award_infos=None,
):
    """Append SQL filter clauses + params for the provided filters dict."""
    ap_exam_infos = ap_exam_infos or []
    ap_award_infos = ap_award_infos or []

    if not isinstance(filters_to_apply, dict):
        return

    for col, values in filters_to_apply.items():
        if col == 'search':
            continue
        if not values:
            continue

        if col == 'atendace_rate':
            expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
            if expr:
                conditions = []
                for val in values:
                    val_str = str(val).strip()
                    if not val_str:
                        conditions.append(f"({expr} IS NULL)")
                        continue

                    match = re.match(r'^([<>]=?|=)?\s*(\d+(?:\.\d+)?)$', val_str)
                    if match:
                        op = match.group(1) or '='
                        num = match.group(2)
                        conditions.append(f"{expr} {op} %s")
                        params.append(num)
                        continue

                    match_range = re.match(r'^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$', val_str)
                    if match_range:
                        low, high = match_range.groups()
                        conditions.append(f"{expr} BETWEEN %s AND %s")
                        params.extend([low, high])
                        continue

                    conditions.append(f"{expr} = %s")
                    params.append(val)

                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        has_blank = "" in values
        real_values = [v for v in values if v != ""]

        if col.startswith('test_'):
            test_info = test_col_map.get(col) or test_col_map.get(col.replace('-', '_'))
            if not test_info:
                continue
            metric_col = test_info['metric']
            window_clause = ""
            window_params = []
            if test_info.get('test_window'):
                window_clause = "AND t.test_window ILIKE %s "
                window_params = [test_info['test_window']]

            id_subquery_base = (
                "SELECT r.local_student_id FROM results r "
                "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                "WHERE r.school_year = %s "
                f"AND {RESULTS_BLANK_SSID_SQL} "
                "AND t.test_name ILIKE %s "
                "AND t.subject ILIKE %s "
                f"{window_clause}"
            )
            ssid_subquery_base = (
                "SELECT r.ssid FROM results r "
                "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                "WHERE r.ssid IS NOT NULL AND r.ssid != '' "
                "AND r.school_year = %s "
                "AND t.test_name ILIKE %s "
                "AND t.subject ILIKE %s "
                f"{window_clause}"
            )
            base_params = [
                test_info['year'],
                test_info['test_name'],
                test_info['subject'],
                *window_params,
            ]

            conditions = []

            if real_values:
                val_id = id_subquery_base
                val_ssid = ssid_subquery_base
                if metric_col in {'ss', 'pl'}:
                    val_id += f"AND r.{metric_col} = ANY(%s)"
                    val_ssid += f"AND r.{metric_col} = ANY(%s)"
                else:
                    val_id += "AND (r.other_info ->> %s) = ANY(%s)"
                    val_ssid += "AND (r.other_info ->> %s) = ANY(%s)"

                val_id_params = list(base_params)
                val_ssid_params = list(base_params)
                if metric_col in {'oi', 'dfp'}:
                    val_id_params.append(test_info.get('other_info_key', ''))
                    val_ssid_params.append(test_info.get('other_info_key', ''))
                val_id_params.append(real_values)
                val_ssid_params.append(real_values)

                conditions.append(f"(cs.student_id IN ({val_id}) OR cs.ssid IN ({val_ssid}))")
                params.extend(val_id_params + val_ssid_params)

            if has_blank:
                blank_subconditions = []
                blank_subconditions.append(
                    f"(cs.student_id NOT IN ({id_subquery_base}) AND cs.ssid NOT IN ({ssid_subquery_base}))"
                )
                params.extend(base_params + base_params)

                null_id = id_subquery_base
                null_ssid = ssid_subquery_base
                null_id_params = list(base_params)
                null_ssid_params = list(base_params)

                if metric_col in {'ss', 'pl'}:
                    null_id += f"AND r.{metric_col} IS NULL"
                    null_ssid += f"AND r.{metric_col} IS NULL"
                else:
                    null_id += "AND (r.other_info ->> %s) IS NULL"
                    null_ssid += "AND (r.other_info ->> %s) IS NULL"
                    null_id_params.append(test_info.get('other_info_key', ''))
                    null_ssid_params.append(test_info.get('other_info_key', ''))

                blank_subconditions.append(f"(cs.student_id IN ({null_id}) OR cs.ssid IN ({null_ssid}))")
                params.extend(null_id_params + null_ssid_params)

                conditions.append(f"({' OR '.join(blank_subconditions)})")

            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('mark_') and not col.startswith('markall_'):
            mark_info = mark_col_map.get(col) or mark_col_map.get(col.replace('-', '_'))
            if not mark_info:
                continue

            conditions = []
            mark_subquery_base = (
                "SELECT sm.ssid FROM student_marks_list sm "
                "WHERE sm.school_year = %s "
                "AND sm.subject ILIKE %s "
                "AND sm.course_name ILIKE %s "
            )
            mark_base_params = [
                mark_info['year'],
                f"%{mark_info['subject']}%",
                f"%{mark_info['course_name']}%",
            ]

            if mark_info.get('mark_period'):
                mark_subquery_base += "AND sm.mark_period ILIKE %s "
                mark_base_params.append(mark_info['mark_period'])
            else:
                mark_subquery_base += "AND (sm.mark_period IS NULL OR sm.mark_period = '') "

            if real_values:
                val_clause = mark_subquery_base + "AND sm.mark = ANY(%s)"
                val_params = list(mark_base_params) + [real_values]
                conditions.append(f"cs.ssid IN ({val_clause})")
                params.extend(val_params)

            if has_blank:
                blank_clause = mark_subquery_base + "AND (sm.mark IS NULL OR sm.mark = '')"
                blank_params = list(mark_base_params)
                conditions.append(
                    f"(cs.ssid NOT IN ({mark_subquery_base}) OR cs.ssid IN ({blank_clause}))"
                )
                params.extend(mark_base_params + blank_params)

            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('apall_'):
            parsed = parse_apall_sort_key(col)
            if not parsed:
                # Umbrella key — carries subject excludes, not a row filter.
                continue
            year = parsed['year']
            idx = parsed['index']
            field = "exam_name" if parsed['field'] == 'SUBJECT' else "exam_grade"
            excludes = set()
            _excl_vals = filters_to_apply.get(f"apall_{year.replace('-', '_')}")
            if isinstance(_excl_vals, list):
                excludes = {
                    str(v)[len('exclude:'):] for v in _excl_vals if str(v).startswith('exclude:')
                }
            exclude_clause = ""
            exclude_params = []
            if excludes:
                placeholders = ", ".join(["%s"] * len(excludes))
                exclude_clause = f" AND e.name NOT IN ({placeholders})"
                exclude_params = sorted(excludes)
            rank_sql = (
                "SELECT ssid FROM ("
                " SELECT a.ssid, e.name AS exam_name, a.exam_grade,"
                " row_number() OVER (PARTITION BY a.ssid ORDER BY e.name) AS rn"
                " FROM \"dataLoader_apexamresult\" a"
                " INNER JOIN \"dataLoader_apexamcode\" e ON a.exam_id = e.code"
                " INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id"
                " WHERE a.admin_year = %s AND il.is_published = true"
                f"{exclude_clause}"
                ") t WHERE rn = %s"
            )
            rank_params = [year] + exclude_params + [idx]
            conditions = []
            if real_values:
                conditions.append(f"cs.ssid IN ({rank_sql} AND t.{field} = ANY(%s))")
                params.extend(rank_params + [real_values])
            if has_blank:
                conditions.append(f"cs.ssid NOT IN ({rank_sql} AND t.{field} IS NOT NULL AND t.{field} <> '')")
                params.extend(rank_params)
            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('ap_') and not col.startswith('apaward_') and not col.startswith('apawards_all_'):
            ap_info = next((i for i in ap_exam_infos if i['col_name'] == col), None)
            if not ap_info:
                continue
            year = ap_info['year']
            subject = ap_info['subject']
            sub_base = (
                "SELECT a.ssid FROM \"dataLoader_apexamresult\" a "
                "INNER JOIN \"dataLoader_apexamcode\" e ON a.exam_id = e.code "
                "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                "WHERE a.admin_year = %s AND e.name = %s AND il.is_published = true "
            )
            sub_params = [year, subject]
            conditions = []
            if real_values:
                conditions.append(f"cs.ssid IN ({sub_base} AND a.exam_grade = ANY(%s))")
                params.extend(sub_params + [real_values])
            if has_blank:
                conditions.append(
                    f"(cs.ssid NOT IN ({sub_base}) OR "
                    f"cs.ssid IN ({sub_base} AND (a.exam_grade IS NULL OR a.exam_grade = '')))"
                )
                params.extend(sub_params + sub_params)
            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('apaward_') and not col.startswith('apawards_all_'):
            ap_info = next((i for i in ap_award_infos if i['col_name'] == col), None)
            if not ap_info:
                continue
            sub_base = (
                "SELECT a.ssid FROM \"dataLoader_apstudentaward\" a "
                "INNER JOIN \"dataLoader_apawardtype\" t ON a.award_type_id = t.code "
                "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                "WHERE a.award_year = %s AND t.description = %s AND il.is_published = true "
            )
            sub_params = [ap_info['year'], ap_info['award']]
            conditions = []
            if real_values:
                conditions.append(f"cs.ssid IN ({sub_base})")
                params.extend(sub_params)
            if has_blank:
                conditions.append(f"cs.ssid NOT IN ({sub_base})")
                params.extend(sub_params)
            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('apawards_all_'):
            year = col[len('apawards_all_'):].replace('_', '-')
            sub_base = (
                "SELECT a.ssid FROM \"dataLoader_apstudentaward\" a "
                "INNER JOIN \"dataLoader_apawardtype\" t ON a.award_type_id = t.code "
                "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                "WHERE a.award_year = %s AND il.is_published = true "
            )
            sub_params = [year]
            conditions = []
            if real_values:
                conditions.append(f"cs.ssid IN ({sub_base} AND t.description = ANY(%s))")
                params.extend(sub_params + [real_values])
            if has_blank:
                conditions.append(f"cs.ssid NOT IN ({sub_base})")
                params.extend(sub_params)
            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('sat_'):
            parsed = parse_sat_column_key(col)
            if not parsed:
                continue
            sat_db_field = sat_column_db_field(col)
            if not sat_db_field:
                continue
            sub_base = (
                "SELECT r.ssid FROM \"dataLoader_satresult\" r "
                "INNER JOIN \"dataLoader_satimportlog\" il ON r.import_log_id = il.id "
                "WHERE r.school_year = %s AND il.is_published = true "
            )
            sub_params = [parsed['year']]
            conditions = []
            if real_values:
                conditions.append(f"cs.ssid IN ({sub_base} AND r.{sat_db_field}::text = ANY(%s))")
                params.extend(sub_params + [[str(v) for v in real_values]])
            if has_blank:
                conditions.append(f"cs.ssid NOT IN ({sub_base})")
                params.extend(sub_params)
            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        if col.startswith('markall_'):
            parsed = parse_markall_key(col)
            if not parsed or parsed['kind'] is None:
                # Umbrella-level keys only carry course include/exclude bookkeeping
                # for the All Marks view; they are not row filters.
                continue
            year = parsed['year']
            subject = markall_subject_actual(parsed['year'], parsed['subject_norm'])
            is_marks_col = parsed['kind'] == 'MARKS'
            course_filter_sql = ""
            if subject == 'Elementary':
                course_filter_sql = " AND " + markall_excluded_course_sql()
            sub_base = (
                "SELECT sm.ssid FROM student_marks_list sm "
                "WHERE sm.school_year = %s AND sm.subject = %s "
                f"{course_filter_sql}"
            )
            sub_params = [year, subject]
            conditions = []
            if real_values:
                if is_marks_col:
                    periods = [str(v).split(':', 1)[1] for v in real_values if str(v).startswith('period:')]
                    marks = [str(v).split(':', 1)[1] for v in real_values if str(v).startswith('mark:')]
                    if periods and marks:
                        val_clause = sub_base + "AND sm.mark_period = ANY(%s) AND sm.mark = ANY(%s)"
                        conditions.append(f"cs.ssid IN ({val_clause})")
                        params.extend(sub_params + [periods, marks])
                    elif periods:
                        val_clause = sub_base + "AND sm.mark_period = ANY(%s)"
                        conditions.append(f"cs.ssid IN ({val_clause})")
                        params.extend(sub_params + [periods])
                    elif marks:
                        val_clause = sub_base + "AND sm.mark = ANY(%s)"
                        conditions.append(f"cs.ssid IN ({val_clause})")
                        params.extend(sub_params + [marks])
                else:
                    val_clause = sub_base + "AND sm.course_name = ANY(%s)"
                    conditions.append(f"cs.ssid IN ({val_clause})")
                    params.extend(sub_params + [real_values])
            if has_blank:
                conditions.append(f"cs.ssid NOT IN ({sub_base})")
                params.extend(sub_params + sub_params)
            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
            continue

        json_key = json_list_key_for_column(col)
        if json_key:
            append_json_list_filter(where_clauses, params, values, json_key)
            continue

        expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
        if expr:
            conditions = []
            if real_values:
                normalized_values = [normalize_filter_param(col, v) for v in real_values]
                conditions.append(f"{expr} = ANY(%s)")
                params.append(normalized_values)
            if has_blank:
                if col in {'birth_date', 'us_enter_date', 'fep_date'}:
                    conditions.append(f"{expr} IS NULL")
                else:
                    conditions.append(f"({expr} IS NULL OR {expr} = '')")

            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")
