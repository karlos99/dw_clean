"""SQL to unnest ``student_marks_table_alt.mark_data`` for sync and analytics.

ClickHouse must not read the PostgreSQL ``student_marks_list`` materialized view;
that view can lag behind ``student_marks_table_alt``. The SELECT below matches
``studata`` migration 0015 (Live period label, ordinality in ``marks_id``).
"""

# Row shape matches ClickHouse / ORM ``student_marks_list`` columns.
STUDENT_MARKS_FROM_ALT_SELECT = """
SELECT
    m.marks_id || '_' || (e.item ->> 'period') || '_' || e.ord::text AS marks_id,
    m.stu_annual_key,
    m.ssid,
    m.school_year,
    m.student_id,
    m.school_name,
    m.state_school_id,
    m.school_type,
    m.subject,
    m.course_name,
    m.staff_name,
    m.staff_email,
    m.staff_employee_id,
    CASE
        WHEN (e.item ->> 'status') ILIKE 'current' THEN 'Live'
        ELSE (e.item ->> 'period')
    END AS mark_period,
    (e.item ->> 'mark') AS mark,
    NULL::timestamptz AS created_at,
    NULL::timestamptz AS updated_at
FROM student_marks_table_alt m,
     LATERAL jsonb_array_elements(
         CASE
             WHEN m.mark_data IS NOT NULL AND m.mark_data <> ''
             THEN (m.mark_data::jsonb) -> 'marks'
             ELSE '[]'::jsonb
         END
     ) WITH ORDINALITY AS e(item, ord)
"""

STUDENT_MARKS_FROM_ALT_COUNT = f"""
SELECT COUNT(*) FROM ({STUDENT_MARKS_FROM_ALT_SELECT.strip()}) AS marks_live
"""

STUDENT_MARKS_LIST_COLUMN_NAMES = (
    'marks_id',
    'stu_annual_key',
    'ssid',
    'school_year',
    'student_id',
    'school_name',
    'state_school_id',
    'school_type',
    'subject',
    'course_name',
    'staff_name',
    'staff_email',
    'staff_employee_id',
    'mark_period',
    'mark',
    'created_at',
    'updated_at',
)
