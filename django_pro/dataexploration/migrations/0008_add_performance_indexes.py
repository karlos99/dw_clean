# Generated migration for performance indexes

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('dataexploration', '0005_savedquery_description_foldershare'),
    ]

    operations = [
        # Add indexes for common filter columns on current_student
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS idx_current_student_school ON current_student(school);
                CREATE INDEX IF NOT EXISTS idx_current_student_grade ON current_student(grade);
                CREATE INDEX IF NOT EXISTS idx_current_student_status ON current_student(status);
                CREATE INDEX IF NOT EXISTS idx_current_student_school_year ON current_student(school_year);
                CREATE INDEX IF NOT EXISTS idx_current_student_student_name ON current_student(student_name);
                
                -- Composite index for common queries
                CREATE INDEX IF NOT EXISTS idx_current_student_year_status ON current_student(school_year, status);
                
                -- Add indexes for results table joins
                CREATE INDEX IF NOT EXISTS idx_results_local_student_id ON results(local_student_id);
                CREATE INDEX IF NOT EXISTS idx_results_school_year ON results(school_year);
                CREATE INDEX IF NOT EXISTS idx_results_pl ON results(pl) WHERE pl IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_results_ss ON results(ss) WHERE ss IS NOT NULL;
                
                -- Composite indexes for test filtering
                CREATE INDEX IF NOT EXISTS idx_results_student_year ON results(local_student_id, school_year);
                
                -- Add indexes for tests table
                CREATE INDEX IF NOT EXISTS idx_tests_name_subject ON tests(test_name, subject);
                CREATE INDEX IF NOT EXISTS idx_tests_window ON tests(test_window) WHERE test_window IS NOT NULL;
                
                -- Add index for student_enroll joins
                CREATE INDEX IF NOT EXISTS idx_student_enroll_student_year ON student_enroll(student_id, school_year);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS idx_current_student_school;
                DROP INDEX IF EXISTS idx_current_student_grade;
                DROP INDEX IF EXISTS idx_current_student_status;
                DROP INDEX IF EXISTS idx_current_student_school_year;
                DROP INDEX IF EXISTS idx_current_student_student_name;
                DROP INDEX IF EXISTS idx_current_student_year_status;
                DROP INDEX IF EXISTS idx_results_local_student_id;
                DROP INDEX IF EXISTS idx_results_school_year;
                DROP INDEX IF EXISTS idx_results_pl;
                DROP INDEX IF EXISTS idx_results_ss;
                DROP INDEX IF EXISTS idx_results_student_year;
                DROP INDEX IF EXISTS idx_tests_name_subject;
                DROP INDEX IF EXISTS idx_tests_window;
                DROP INDEX IF EXISTS idx_student_enroll_student_year;
            """
        ),
    ]
