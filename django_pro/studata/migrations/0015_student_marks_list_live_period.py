# Migration: Update student_marks_list materialized view so marks with
# status = 'Current' (case-insensitive) are labeled as 'Live' in mark_period.

from django.db import migrations


_UPDATE_SQL = """
DROP VIEW IF EXISTS elementary_homeroom_teacher CASCADE;
DROP VIEW IF EXISTS student_marks_list CASCADE;
DROP MATERIALIZED VIEW IF EXISTS student_marks_list CASCADE;

CREATE MATERIALIZED VIEW student_marks_list AS
SELECT
    m.marks_id || '_' || (e.item ->> 'period') || '_' || e.ord AS marks_id,
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
    (e.item ->> 'mark')    AS mark,
    NULL::timestamptz AS created_at,
    NULL::timestamptz AS updated_at
FROM student_marks_table_alt m,
     LATERAL jsonb_array_elements(
         CASE
             WHEN m.mark_data IS NOT NULL AND m.mark_data <> ''
             THEN (m.mark_data::jsonb) -> 'marks'
             ELSE '[]'::jsonb
         END
     ) WITH ORDINALITY AS e(item, ord);

CREATE UNIQUE INDEX student_marks_list_marks_id_idx ON student_marks_list (marks_id);
CREATE INDEX student_marks_list_school_year_idx ON student_marks_list (school_year);
CREATE INDEX student_marks_list_stu_annual_key_idx ON student_marks_list (stu_annual_key);
CREATE INDEX student_marks_list_mark_period_idx ON student_marks_list (mark_period);

CREATE OR REPLACE VIEW elementary_homeroom_teacher AS
SELECT DISTINCT
    sl.stu_annual_key,
    sl.ssid,
    sl.staff_email,
    regexp_replace(sl.staff_name, '^([^,]+),\\s*(\\w).*$', '\\1, \\2.') AS elem_teacher_name,
    sl.school_name,
    sl.school_year
FROM schedule_list sl
INNER JOIN school sch ON sch.state_id = sl.state_school_id
WHERE sch.school_group = 'Elementary School'
  AND sl.period = '0';
"""

_REVERT_SQL = """
DROP VIEW IF EXISTS elementary_homeroom_teacher CASCADE;
DROP VIEW IF EXISTS student_marks_list CASCADE;
DROP MATERIALIZED VIEW IF EXISTS student_marks_list CASCADE;

CREATE MATERIALIZED VIEW student_marks_list AS
SELECT
    m.marks_id || '_' || (e.item ->> 'period') || '_' || e.ord AS marks_id,
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
    (e.item ->> 'period')  AS mark_period,
    (e.item ->> 'mark')    AS mark,
    NULL::timestamptz AS created_at,
    NULL::timestamptz AS updated_at
FROM student_marks_table_alt m,
     LATERAL jsonb_array_elements(
         CASE
             WHEN m.mark_data IS NOT NULL AND m.mark_data <> ''
             THEN (m.mark_data::jsonb) -> 'marks'
             ELSE '[]'::jsonb
         END
     ) WITH ORDINALITY AS e(item, ord);

CREATE UNIQUE INDEX student_marks_list_marks_id_idx ON student_marks_list (marks_id);
CREATE INDEX student_marks_list_school_year_idx ON student_marks_list (school_year);
CREATE INDEX student_marks_list_stu_annual_key_idx ON student_marks_list (stu_annual_key);

CREATE OR REPLACE VIEW elementary_homeroom_teacher AS
SELECT DISTINCT
    sl.stu_annual_key,
    sl.ssid,
    sl.staff_email,
    regexp_replace(sl.staff_name, '^([^,]+),\\s*(\\w).*$', '\\1, \\2.') AS elem_teacher_name,
    sl.school_name,
    sl.school_year
FROM schedule_list sl
INNER JOIN school sch ON sch.state_id = sl.state_school_id
WHERE sch.school_group = 'Elementary School'
  AND sl.period = '0';
"""


class Migration(migrations.Migration):

    dependencies = [
        ('studata', '0014_materialize_student_marks_list'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_UPDATE_SQL,
            reverse_sql=_REVERT_SQL,
        ),
    ]
