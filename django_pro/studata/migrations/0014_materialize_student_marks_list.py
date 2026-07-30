# Migration: Materialize student_marks_list to avoid re-running the JSONB
# unnest over student_marks_table_alt on every query.
#
# Background
# ----------
# student_marks_list (see 0012_alt_tables_compatibility_views.py) is a plain
# VIEW that does `LATERAL jsonb_array_elements(...)` over the entire
# student_marks_table_alt table on every SELECT. Dataviz charts that pull
# mark_* columns hit this view on every cache miss, making it one of the
# slowest parts of chart rendering. student_marks_table_alt is refreshed by
# an external nightly ETL job, so a materialized view refreshed once after
# that job finishes (via the `refresh_marks_view` management command) is
# never meaningfully stale.
#
# The SELECT body is unchanged from 0012 — only VIEW -> MATERIALIZED VIEW,
# plus a unique index so `REFRESH MATERIALIZED VIEW CONCURRENTLY`
# (non-blocking refresh) is possible.
#
# Note: the source `mark_data.marks` JSON arrays contain genuine duplicate
# periods for the same student/course (e.g. two "Q4" entries with different
# marks — confirmed against real data), so `marks_id || period` alone is not
# unique. The array ordinal (`WITH ORDINALITY`) is appended to make the
# surrogate key unique. `marks_id` is never selected by any chart/data_loader
# query (`_fetch_mark_data` only selects stu_annual_key/mark/school_year/
# subject/course_name/mark_period), so this is purely an internal key change
# with zero effect on query results.

from django.db import migrations


_MATERIALIZE_SQL = """
DROP VIEW IF EXISTS elementary_homeroom_teacher;
DROP VIEW IF EXISTS student_marks_list;

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

_REVERT_SQL = """
DROP VIEW IF EXISTS elementary_homeroom_teacher;
DROP MATERIALIZED VIEW IF EXISTS student_marks_list;

CREATE OR REPLACE VIEW student_marks_list AS
SELECT
    m.marks_id || '_' || (e.item ->> 'period') AS marks_id,
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
     ) AS e(item);

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
        ('studata', '0013_currentstudent_add_data'),
    ]

    operations = [
        migrations.RunSQL(
            sql=_MATERIALIZE_SQL,
            reverse_sql=_REVERT_SQL,
        ),
    ]
