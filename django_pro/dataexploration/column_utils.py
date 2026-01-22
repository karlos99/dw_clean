"""Utilities for dynamic column generation from database."""

from django.db import connections
from .constants import COLUMN_DEFINITIONS


def get_test_columns(column_filters=None):
    """Get dynamic test columns from database organized by year and test.
    
        Returns structure: {year: {assessment_name: {test_window: {testing_name: {'SS': ss_key, 'PL': pl_key}}}}}
        Notes:
            - If a test has no test_window, it is grouped under an empty-string key '' and keeps legacy column keys.
            - If a test has a test_window, the window is included in the column key to avoid collisions.
    """
    with connections['default'].cursor() as cursor:
        where_clauses = ["r.school_year IS NOT NULL", "t.test_name IS NOT NULL"]
        params = []

        # Add column-specific filters from base student data
        if column_filters:
            for col, value in column_filters.items():
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

        # Structure: {year: {assessment_name: {test_window: {testing_name: {'SS': key, 'PL': key}}}}}
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
            COLUMN_DEFINITIONS[pl_key] = f"{label_base} PL"

            if year not in columns:
                columns[year] = {}

            if assessment_name not in columns[year]:
                columns[year][assessment_name] = {}

            if '' not in columns[year][assessment_name] and window_name == '':
                columns[year][assessment_name][''] = {}
            if window_name not in columns[year][assessment_name]:
                columns[year][assessment_name][window_name] = {}

            columns[year][assessment_name][window_name][testing_name] = {
                'SS': ss_key,
                'PL': pl_key,
            }

        return columns
