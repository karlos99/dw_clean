"""Utilities for dynamic column generation from database."""

import hashlib
import json
from django.core.cache import cache
from django.db import connections
from .constants import COLUMN_DEFINITIONS


def _generate_cache_key(prefix, filters):
    """Generate a consistent cache key from filters."""
    if filters:
        # Sort keys to ensure consistent hash
        filter_str = json.dumps(filters, sort_keys=True)
    else:
        filter_str = "None"
    
    key_hash = hashlib.md5(filter_str.encode('utf-8')).hexdigest()
    return f"{prefix}_{key_hash}"


def get_test_columns(column_filters=None):
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
    cache_key = _generate_cache_key("test_columns", column_filters)
    cached_data = cache.get(cache_key)

    if cached_data:
        columns, definitions = cached_data
        COLUMN_DEFINITIONS.update(definitions)
        return columns

    new_definitions = {}

    with connections['default'].cursor() as cursor:
        where_clauses = ["r.school_year IS NOT NULL", "t.test_name IS NOT NULL"]
        params = []

        # Add column-specific filters from base student data
        if column_filters:
            for col, value in column_filters.items():
                if isinstance(value, (list, tuple, set)):
                    value = next((v for v in value if v), '')
                if value and value != 'All' and col in COLUMN_DEFINITIONS:
                    # Only apply filters for non-test columns
                    if not col.startswith('test_'):
                        where_clauses.append(f"cs.{col} ILIKE %s")
                        params.append(f'%{value}%')

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

            assessment_name = (test_name or '').strip() or 'Assessment'
            testing_name = (subject or '').strip() or 'Testing'
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
            label_base = f"{test_name} {subject} - {year}"
            
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
                assessment_name = (test_name or '').strip() or 'Assessment'
                testing_name = (subject or '').strip() or 'Testing'
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
                key_hash = hashlib.md5(raw_key_str.encode('utf-8')).hexdigest()[:6]
                other_key = f"test_{test_key}_OI_{key_norm}_{key_hash}"

                # Table header label should remain consistent with the existing format.
                label_base = f"{test_name} {subject} - {year}"
                
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


def get_mark_columns(column_filters=None):
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
    cache_key = _generate_cache_key("mark_columns", column_filters)
    cached_data = cache.get(cache_key)

    if cached_data:
        columns, definitions = cached_data
        COLUMN_DEFINITIONS.update(definitions)
        return columns

    new_definitions = {}

    with connections['default'].cursor() as cursor:
        where_clauses = ["sm.school_year IS NOT NULL"]
        params = []

        # Apply base column filters from current_student
        if column_filters:
            for col, value in column_filters.items():
                if isinstance(value, (list, tuple, set)):
                    value = next((v for v in value if v), '')
                if col == 'search' and value:
                    where_clauses.append(
                        "(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)"
                    )
                    search_param = f"%{value}%"
                    params.extend([search_param, search_param, search_param])
                    continue
                if value and value != 'All' and col in COLUMN_DEFINITIONS:
                    if not col.startswith(('test_', 'mark_')):
                        where_clauses.append(f"cs.{col} ILIKE %s")
                        params.append(f"%{value}%")

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT DISTINCT
                sm.school_year,
                sm.subject,
                sm.course_name,
                sm.mark_period
            FROM student_marks_list sm
            INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
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

        cache.set(cache_key, (columns, new_definitions), timeout=3600)
        return columns
