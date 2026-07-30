"""
Django management command to sync data from PostgreSQL to ClickHouse.

Usage:
    python manage.py sync_to_clickhouse              # full sync all tables
    python manage.py sync_to_clickhouse --schema-only   # create tables only
    python manage.py sync_to_clickhouse --tables current_student,results  # specific tables

This uses ClickHouse's postgresql table function to pull data directly
from PostgreSQL into ClickHouse in a single efficient operation per table.

Tables synced (all analytics-relevant tables):
    current_student, student_enroll, results, tests,
    student_marks_list, nextyearlist_placement, mathr_recommendation,
    student_attendance, student_incidents
"""

import os
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

from clickhouse_utils.client import get_clickhouse_client
from clickhouse_utils.schema import get_all_ddl

logger = logging.getLogger(__name__)

ALL_TABLES = [
    'current_student',
    'student_enroll',
    'results',
    'tests',
    'student_marks_list',
    'nextyearlist_placement',
    'school',
    'schedule_list',
    'relative_year',
    'mathr_recommendation',
    'student_attendance',
    'student_incidents',

]


class Command(BaseCommand):
    help = 'Sync PostgreSQL data to ClickHouse analytics tables'

    def add_arguments(self, parser):
        parser.add_argument(
            '--schema-only',
            action='store_true',
            help='Only create tables, do not copy data',
        )
        parser.add_argument(
            '--tables',
            type=str,
            help=f'Comma-separated list of tables to sync. Available: {", ".join(ALL_TABLES)}',
        )

    def handle(self, *args, **options):
        schema_only = options['schema_only']
        tables_arg = options.get('tables')

        if tables_arg:
            tables = [t.strip() for t in tables_arg.split(',')]
        else:
            tables = list(ALL_TABLES)

        ch = get_clickhouse_client()
        if ch is None:
            self.stderr.write(self.style.ERROR(
                'ClickHouse client not available. Is ClickHouse running and configured?'
            ))
            return

        ch_db = getattr(settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db')

        self.stdout.write(f'Connected to ClickHouse database: {ch_db}')

        # ── Create tables ──────────────────────────────────────────────
        self.stdout.write('Creating tables...')
        for ddl in get_all_ddl():
            try:
                ch.command(ddl)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f'Table creation error (may already exist): {exc}'))

        self.stdout.write(self.style.SUCCESS('Table schemas ensured.'))

        if schema_only:
            self.stdout.write(self.style.SUCCESS('Schema-only sync complete.'))
            return

        # ── Copy data from PostgreSQL to ClickHouse ────────────────────
        pg_host = os.getenv('POSTGRES_HOST', 'postgres')
        pg_port = os.getenv('POSTGRES_PORT', '5432')
        pg_db = os.getenv('POSTGRES_DB', 'djago_db')
        pg_user = os.getenv('POSTGRES_USER', 'dsdata_user')
        pg_password = os.getenv('POSTGRES_PASSWORD', '')

        for table in tables:
            if table not in ALL_TABLES:
                self.stderr.write(self.style.WARNING(f'Unknown table "{table}", skipping.'))
                continue

            self.stdout.write(f'Syncing {table}...')

            try:
                # Truncate table first to prevent duplicate accumulation
                ch.command(f"TRUNCATE TABLE {ch_db}.{table}")

                insert_sql = f"""
                    INSERT INTO {ch_db}.{table}
                    SELECT * FROM postgresql(
                        '{pg_host}:{pg_port}',
                        '{pg_db}',
                        '{table}',
                        '{pg_user}',
                        '{pg_password}'
                    )
                """
                ch.command(insert_sql)
                self.stdout.write(self.style.SUCCESS(f'  {table} synced successfully.'))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'  {table} sync failed: {exc}'))

        self.stdout.write(self.style.SUCCESS('Sync complete.'))
