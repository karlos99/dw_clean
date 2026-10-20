from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        'Refresh the student_marks_list materialized view (CONCURRENTLY, '
        'non-blocking). student_marks_table_alt is loaded by an external '
        'nightly ETL job not in this repo — schedule this command to run '
        'right after that job finishes, e.g. a cron entry alongside the '
        'ETL\'s own schedule: '
        '0 6 * * * cd /path/to/project && python manage.py refresh_marks_view'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-clickhouse',
            action='store_true',
            help=(
                'Do not rebuild ClickHouse student_marks_list / analytics marks '
                'tables from student_marks_table_alt after refreshing the MV.'
            ),
        )

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY student_marks_list;')
        self.stdout.write(self.style.SUCCESS('student_marks_list refreshed.'))

        if options.get('skip_clickhouse'):
            return

        self.stdout.write(
            'Syncing ClickHouse marks from student_marks_table_alt '
            '(student_marks_list, analytics_student_marks, analytics_marks_metadata)...'
        )
        call_command(
            'sync_to_clickhouse',
            tables='student_marks_list,analytics_student_marks,analytics_marks_metadata',
        )
        self.stdout.write(self.style.SUCCESS('ClickHouse marks analytics synced from alt table.'))
