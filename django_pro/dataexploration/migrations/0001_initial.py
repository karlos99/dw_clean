# Generated migration for StudentWithTests view

from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('studata', '0006_result_test_delete_studentcombined_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE VIEW student_with_tests AS
            SELECT 
                CONCAT(cs.stu_annual_key, '_', r.test_id) as id,
                cs.stu_annual_key,
                cs.ssid,
                cs.student_id,
                cs.grade,
                cs.school,
                cs.school_state_id,
                cs.gender,
                cs.race,
                cs.ell as esl,
                cs.sed,
                cs.foster,
                cs.homeless,
                cs.sped,
                cs.gate,
                cs.in_504,
                cs.dli,
                cs.atendace_rate,
                cs.suspension_indicator,
                cs.ltel,
                cs.status,
                cs.school_year,
                se.last_name,
                se.middle_name,
                se.first_name,
                se.birth_date,
                se.us_enter_date,
                se.state_enter_date,
                se.ell_status,
                se.fep_date,
                se.instructional_setting,
                se.special_program_code,
                se.migrant,
                t.test_group_key,
                t.test_name,
                t.subject,
                t.test_window,
                r.test_id,
                r.local_student_id,
                r.school_code,
                r.pl,
                r.ss,
                r.other_info
            FROM current_student cs
            LEFT JOIN student_enroll se ON cs.stu_annual_key = se.stu_annual_key
            INNER JOIN results r ON cs.ssid = r.ssid AND cs.school_year = r.school_year
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            """,
            reverse_sql="DROP VIEW IF EXISTS student_with_tests"
        ),
    ]
