# Migration: Replace managed tables with compatibility views over the new alt tables.
#
# Background
# ----------
# Three source tables were replaced with new "alt" versions that restructure the data:
#   schedule_list       → schedule_table_alt      (adds school_type column)
#   teacher_access_list → teacher_access_table_alt (same columns, external reload)
#   student_marks_list  → student_marks_table_alt  (mark_period + mark merged into
#                                                   mark_data JSON; adds course_info,
#                                                   school_type, staff_email)
#
# Strategy
# --------
# All existing raw SQL throughout the project references the old table names and flat
# column layout (mark_period, mark).  To avoid touching every query file, we:
#
#   1. Rename the old managed tables to *_old (preserving data as a backup).
#   2. Create PostgreSQL VIEWs with the original names that read from the alt tables.
#      The student_marks_list view unnests mark_data JSON so each (course, period) row
#      looks identical to the original flat schema.
#   3. Recreate the elementary_homeroom_teacher view (which JOINs schedule_list) so it
#      continues to point at the right underlying data.
#   4. Mark the three Django models as managed=False (they now back views, not tables).
#
# The new StudentMarksAlt model provides direct access to the JSON structure when needed.

from django.db import migrations, models


_CREATE_VIEWS_SQL = """
-- -----------------------------------------------------------------------
-- 1. Drop dependent views so we can rename the underlying tables safely.
-- -----------------------------------------------------------------------
DROP VIEW IF EXISTS elementary_homeroom_teacher;

-- -----------------------------------------------------------------------
-- 2. Rename old managed tables → *_old  (skip if already renamed).
-- -----------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'schedule_list' AND table_type = 'BASE TABLE'
    ) THEN
        ALTER TABLE schedule_list RENAME TO schedule_list_old;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'teacher_access_list' AND table_type = 'BASE TABLE'
    ) THEN
        ALTER TABLE teacher_access_list RENAME TO teacher_access_list_old;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'student_marks_list' AND table_type = 'BASE TABLE'
    ) THEN
        ALTER TABLE student_marks_list RENAME TO student_marks_list_old;
    END IF;
END $$;

-- -----------------------------------------------------------------------
-- 3a. schedule_list  →  schedule_table_alt
--     Adds school_type column; all other columns are identical.
--     created_at / updated_at are surfaced as NULL (not in source table).
-- -----------------------------------------------------------------------
CREATE OR REPLACE VIEW schedule_list AS
SELECT
    schedule_id,
    stu_annual_key,
    ssid,
    school_year,
    student_id,
    school_name,
    state_school_id,
    school_type,
    course_state_equivalence_code,
    course_code,
    course_key,
    course_subject,
    course_name,
    period,
    staff_name,
    staff_email,
    staff_employee_id,
    schedule_status,
    NULL::timestamptz AS created_at,
    NULL::timestamptz AS updated_at
FROM schedule_table_alt;

-- -----------------------------------------------------------------------
-- 3b. teacher_access_list  →  teacher_access_table_alt
--     Column structure is identical to the old table.
-- -----------------------------------------------------------------------
CREATE OR REPLACE VIEW teacher_access_list AS
SELECT
    access_id,
    schedule_id,
    stu_annual_key,
    ssid,
    school_year,
    student_id,
    school_name,
    state_school_id,
    staff_name,
    staff_email,
    staff_employee_id,
    NULL::timestamptz AS created_at,
    NULL::timestamptz AS updated_at
FROM teacher_access_table_alt;

-- -----------------------------------------------------------------------
-- 3c. student_marks_list  →  student_marks_table_alt (JSON unnested)
--
--     Old schema: one row per (student, school_year, course, mark_period)
--     New schema: one row per (student, school_year, course) with mark_data JSON:
--         {"current_grade": "A", "marks": [{"period": "P1", "mark": "A"}, ...]}
--
--     The LATERAL join unnests the marks array so each period becomes its own row,
--     preserving mark_period and mark columns that all existing queries rely on.
--     marks_id is derived as <original_marks_id>_<period> to stay unique per row.
-- -----------------------------------------------------------------------
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

-- -----------------------------------------------------------------------
-- 4. Recreate elementary_homeroom_teacher (still references schedule_list).
-- -----------------------------------------------------------------------
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

_DROP_VIEWS_SQL = """
-- Reverse: drop views and restore original table names if the _old backups exist.
DROP VIEW IF EXISTS elementary_homeroom_teacher;
DROP VIEW IF EXISTS student_marks_list;
DROP VIEW IF EXISTS teacher_access_list;
DROP VIEW IF EXISTS schedule_list;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'schedule_list_old'
    ) THEN
        ALTER TABLE schedule_list_old RENAME TO schedule_list;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'teacher_access_list_old'
    ) THEN
        ALTER TABLE teacher_access_list_old RENAME TO teacher_access_list;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'student_marks_list_old'
    ) THEN
        ALTER TABLE student_marks_list_old RENAME TO student_marks_list;
    END IF;
END $$;

-- Recreate elementary_homeroom_teacher against the restored table.
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
        ('studata', '0011_result_results_local_s_de0513_idx_and_more'),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Mark the three models as unmanaged (they now back DB views, not
        # tables Django owns).  Django will no longer issue DDL for them.
        # ------------------------------------------------------------------
        migrations.AlterModelOptions(
            name='schedule',
            options={
                'managed': False,
                'verbose_name': 'Schedule',
                'verbose_name_plural': 'Schedules',
            },
        ),
        migrations.AlterModelOptions(
            name='studentmarks',
            options={
                'managed': False,
                'verbose_name': 'Student Mark',
                'verbose_name_plural': 'Student Marks',
            },
        ),
        migrations.AlterModelOptions(
            name='teacheraccess',
            options={
                'managed': False,
                'verbose_name': 'Teacher Access',
                'verbose_name_plural': 'Teacher Accesses',
            },
        ),

        # ------------------------------------------------------------------
        # Remove index definitions that belonged to the old managed tables.
        # Views do not have indexes and Django would otherwise try to create
        # them against the view (which fails).
        # ------------------------------------------------------------------
        migrations.AlterUniqueTogether(name='schedule', unique_together=set()),
        migrations.AlterIndexTogether(name='schedule', index_together=set()),
        migrations.AlterUniqueTogether(name='studentmarks', unique_together=set()),
        migrations.AlterIndexTogether(name='studentmarks', index_together=set()),
        migrations.AlterUniqueTogether(name='teacheraccess', unique_together=set()),
        migrations.AlterIndexTogether(name='teacheraccess', index_together=set()),

        # ------------------------------------------------------------------
        # New field: school_type on Schedule (from schedule_table_alt).
        # Because managed=False the AddField updates Django state only —
        # no ALTER TABLE is executed.
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name='schedule',
            name='school_type',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),

        # ------------------------------------------------------------------
        # New fields on StudentMarks exposed by the view:
        #   school_type  – from student_marks_table_alt
        #   staff_email  – from student_marks_table_alt
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name='studentmarks',
            name='school_type',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='studentmarks',
            name='staff_email',
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),

        # ------------------------------------------------------------------
        # created_at / updated_at become nullable (the views expose NULL).
        # ------------------------------------------------------------------
        migrations.AlterField(
            model_name='schedule',
            name='created_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='schedule',
            name='updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='studentmarks',
            name='created_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='studentmarks',
            name='updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='teacheraccess',
            name='created_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='teacheraccess',
            name='updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ------------------------------------------------------------------
        # New model: StudentMarksAlt – direct access to the JSON source table.
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name='StudentMarksAlt',
            fields=[
                ('marks_id', models.CharField(db_index=True, max_length=255, primary_key=True, serialize=False)),
                ('stu_annual_key', models.CharField(db_index=True, max_length=255)),
                ('ssid', models.CharField(db_index=True, max_length=50)),
                ('school_year', models.CharField(max_length=20)),
                ('student_id', models.CharField(blank=True, max_length=50, null=True)),
                ('school_name', models.CharField(blank=True, max_length=255, null=True)),
                ('state_school_id', models.CharField(blank=True, max_length=50, null=True)),
                ('school_type', models.CharField(blank=True, max_length=255, null=True)),
                ('subject', models.CharField(blank=True, max_length=255, null=True)),
                ('course_name', models.CharField(blank=True, max_length=255, null=True)),
                ('staff_name', models.CharField(blank=True, max_length=255, null=True)),
                ('staff_email', models.EmailField(blank=True, max_length=254, null=True)),
                ('staff_employee_id', models.CharField(blank=True, db_index=True, max_length=50, null=True)),
                ('mark_data', models.JSONField(blank=True, null=True)),
                ('course_info', models.JSONField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Student Mark (Alt)',
                'verbose_name_plural': 'Student Marks (Alt)',
                'db_table': 'student_marks_table_alt',
                'managed': False,
            },
        ),

        # ------------------------------------------------------------------
        # Execute the actual database changes: rename old tables, create views.
        # ------------------------------------------------------------------
        migrations.RunSQL(
            sql=_CREATE_VIEWS_SQL,
            reverse_sql=_DROP_VIEWS_SQL,
        ),
    ]
