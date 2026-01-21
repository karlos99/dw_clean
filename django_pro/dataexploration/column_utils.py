"""Utilities for dynamic column generation from database."""

from django.db import connections
from .constants import COLUMN_DEFINITIONS


def get_test_columns(column_filters=None):
    """Get dynamic test columns from database organized by year and test.
    
    Returns structure: {year: {test_name_subject: [column_keys]}}
    Example: {'2023-2024': {'CAASPP ELA': ['test_2023_2024_CAASPP_ELA_SS', 'test_2023_2024_CAASPP_ELA_PL']}}
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

        # Structure: {year: {test_name_subject: [column_keys]}}
        columns = {}
        for row in cursor.fetchall():
            year, test_name, subject = row
            
            # Normalize for column key
            test_key = f"{year}_{test_name}_{subject}".replace(" ", "_").replace("-", "_")
            ss_key = f"test_{test_key}_SS"
            pl_key = f"test_{test_key}_PL"

            # Store in COLUMN_DEFINITIONS with descriptive names
            COLUMN_DEFINITIONS[ss_key] = f"{test_name} {subject} - {year} SS"
            COLUMN_DEFINITIONS[pl_key] = f"{test_name} {subject} - {year} PL"

            if year not in columns:
                columns[year] = {}

            test_subject = f"{test_name} {subject}"
            if test_subject not in columns[year]:
                columns[year][test_subject] = []

            columns[year][test_subject].extend([ss_key, pl_key])

        return columns
