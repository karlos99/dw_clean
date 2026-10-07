"""
Django management command to sync data from PostgreSQL to ClickHouse.

Usage:
    python manage.py sync_to_clickhouse              # incremental where possible
    python manage.py sync_to_clickhouse --full       # force atomic full reload
    python manage.py sync_to_clickhouse --schema-only
    python manage.py sync_to_clickhouse --tables current_student,results
"""

import os
import logging
import time
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from clickhouse_utils.client import get_clickhouse_client
from clickhouse_utils.marks_source_sql import (
    STUDENT_MARKS_FROM_ALT_COUNT,
    STUDENT_MARKS_FROM_ALT_SELECT,
    STUDENT_MARKS_LIST_COLUMN_NAMES,
)
from clickhouse_utils.course_program_sync import (
    COURSE_PROGRAM_AGG_COLUMNS,
    COURSE_PROGRAM_AGG_FROM_TEMP,
    COURSE_PROGRAM_CATALOG_INDEXES,
    COURSE_PROGRAM_VALUES_COLUMNS,
    COURSE_PROGRAM_VALUES_FROM_TEMP,
    CREATE_MATCHED_TEMP_SQL,
    MATCHED_TEMP_INDEX_SQL,
)

COURSE_PROGRAM_CH_TABLES = (
    'analytics_student_course_program',
    'analytics_course_program_values',
)
from clickhouse_utils.schema import (
    ANALYTICS_TABLE_NAMES,
    COLUMN_MIGRATION_DDL,
    PROJECTION_DDL,
    SECONDARY_INDEX_DDL,
    get_all_ddl,
)

logger = logging.getLogger(__name__)

SOURCE_TABLES = [
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

DERIVED_TABLES = [
    'results_with_tests',
    *ANALYTICS_TABLE_NAMES,
]

ALL_TABLES = SOURCE_TABLES + DERIVED_TABLES

TABLE_PKS = {
    'current_student': 'stu_annual_key',
    'student_enroll': 'stu_enrollment_key',
    'results': 'test_id',
    'tests': 'test_group_key',
    'results_with_tests': 'test_id',
    'student_marks_list': 'marks_id',
    'nextyearlist_placement': 'id',
    'school': 'dw_schoolkey',
    'schedule_list': 'schedule_id',
    'relative_year': 'school_year',
    'mathr_recommendation': 'id',
    'student_attendance': 'stu_annual_key',
    'student_incidents': 'stu_annual_key',
}

TABLE_TS_COLS = {
    # student_marks_list is rebuilt from student_marks_table_alt (always full sync).
    'schedule_list': 'updated_at',
    'mathr_recommendation': 'updated_at',
    'student_attendance': 'updated_at',
    'student_incidents': 'updated_at',
}

INCREMENTAL_FULL_RATIO = 0.3

ANALYTICS_DEPENDENCIES = {
    'results_with_tests': {'results', 'tests'},
    'analytics_student_profile': {
        'current_student', 'student_enroll', 'school', 'student_attendance',
    },
    'analytics_student_results': {
        'results', 'tests', 'results_with_tests', 'current_student',
    },
    'analytics_student_marks': {'student_marks_list', 'current_student'},
    'analytics_ap_results': set(),
    'analytics_ap_awards': set(),
    'analytics_sat_results': set(),
    'analytics_survey_responses': set(),
    # Column-discovery tables: depend on the denormalized result/mark tables and
    # current_student (to preserve the INNER JOIN that scopes discovery to known
    # students).
    'analytics_test_metadata': {'results_with_tests', 'current_student'},
    'analytics_marks_metadata': {'student_marks_list', 'current_student'},
    'analytics_student_course_program': {'schedule_list'},
    'analytics_course_program_values': {'schedule_list'},
    # CCGI dashboards: flattened from the PostgreSQL ccgi_* JSONB tables and
    # enriched with each student's most recent current_student row.
    'analytics_ccgi_favorite_career': {'current_student'},
    'analytics_ccgi_career_cluster': {'current_student'},
    'analytics_ccgi_college_financial': {'current_student'},
    'analytics_ccgi_college_application': {'current_student'},
    'analytics_ccgi_application_status': {'current_student'},
}


def choose_sync_mode(table, watermark, pg_count, ch_count, changed_count):
    """Return 'skip', 'incremental', or 'full'."""
    if table in DERIVED_TABLES:
        return 'full'
    ts_col = TABLE_TS_COLS.get(table)
    if not ts_col or not watermark:
        return 'full'
    if changed_count == 0:
        if pg_count is not None and ch_count is not None and int(pg_count) != int(ch_count):
            return 'full'
        return 'skip'
    if pg_count and changed_count / max(int(pg_count), 1) > INCREMENTAL_FULL_RATIO:
        return 'full'
    return 'incremental'


def _ch_ts_literal(value):
    if not value:
        return None
    text = value.isoformat() if hasattr(value, 'isoformat') else str(value)
    text = text.replace('T', ' ').split('+')[0].split('Z')[0]
    return text


def _pg_table_fn(pg_host, pg_port, pg_db, pg_user, pg_password, table):
    return (
        f"postgresql('{pg_host}:{pg_port}', '{pg_db}', '{table}', "
        f"'{pg_user}', '{pg_password}')"
    )


def _watermark_key(table):
    return f'analytics:ch_watermark:{table}'


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
        parser.add_argument(
            '--full',
            action='store_true',
            help='Force atomic full reload for every selected table.',
        )
        parser.add_argument(
            '--non-atomic',
            action='store_true',
            help='Use the legacy truncate-and-reload behavior (not recommended).',
        )

    def handle(self, *args, **options):
        schema_only = options['schema_only']
        tables_arg = options.get('tables')
        atomic = not options.get('non_atomic', False)
        force_full = options.get('full', False)

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

        self.stdout.write('Creating tables...')
        for ddl in get_all_ddl():
            try:
                ch.command(ddl)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(
                    f'Table creation error (may already exist): {exc}'
                ))
        self.stdout.write(self.style.SUCCESS('Table schemas ensured.'))
        for projection_sql in PROJECTION_DDL:
            try:
                ch.command(projection_sql)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f'Projection skipped: {exc}'))

        for index_sql in SECONDARY_INDEX_DDL:
            try:
                ch.command(index_sql)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f'Index skipped: {exc}'))

        for column_sql in COLUMN_MIGRATION_DDL:
            try:
                ch.command(column_sql)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f'Column migration skipped: {exc}'))

        if schema_only:
            self.stdout.write(self.style.SUCCESS('Schema-only sync complete.'))
            return

        pg_host = os.getenv('POSTGRES_HOST', 'postgres')
        pg_port = os.getenv('POSTGRES_PORT', '5432')
        pg_db = os.getenv('POSTGRES_DB', 'djago_db')
        pg_user = os.getenv('POSTGRES_USER', 'dsdata_user')
        pg_password = os.getenv('POSTGRES_PASSWORD', '')
        pg_fn_kwargs = dict(
            pg_host=pg_host, pg_port=pg_port, pg_db=pg_db,
            pg_user=pg_user, pg_password=pg_password,
        )

        requested = [t for t in tables if t in ALL_TABLES]
        unknown = [t for t in tables if t not in ALL_TABLES]
        for table in unknown:
            self.stderr.write(self.style.WARNING(f'Unknown table "{table}", skipping.'))

        source_requested = [t for t in requested if t in SOURCE_TABLES]
        derived_requested = [t for t in requested if t in DERIVED_TABLES]
        synced_sources = set()
        sync_failed = False

        for table in source_requested:
            self.stdout.write(f'Syncing {table}...')
            try:
                mode = self._sync_source_table(
                    ch, ch_db, table, atomic=atomic, force_full=force_full,
                    pg_fn_kwargs=pg_fn_kwargs,
                )
                synced_sources.add(table)
                self.stdout.write(self.style.SUCCESS(f'  {table} synced ({mode}).'))
            except Exception as exc:
                sync_failed = True
                self.stderr.write(self.style.ERROR(f'  {table} sync failed: {exc}'))

        derived_to_build = []
        if not tables_arg:
            derived_to_build = list(DERIVED_TABLES)
        else:
            for derived in DERIVED_TABLES:
                if derived in derived_requested:
                    derived_to_build.append(derived)
                elif synced_sources & ANALYTICS_DEPENDENCIES.get(derived, set()):
                    derived_to_build.append(derived)

        derived_to_build = [
            t for t in derived_to_build
            if t not in COURSE_PROGRAM_CH_TABLES or t == 'analytics_student_course_program'
        ]
        if any(t in COURSE_PROGRAM_CH_TABLES for t in (derived_requested if tables_arg else DERIVED_TABLES)):
            if 'analytics_student_course_program' not in derived_to_build:
                derived_to_build.append('analytics_student_course_program')

        for table in derived_to_build:
            self.stdout.write(f'Rebuilding {table}...')
            try:
                if table == 'analytics_student_course_program':
                    self._rebuild_course_program_tables(ch, ch_db, atomic=atomic)
                    self.stdout.write(self.style.SUCCESS('  course program analytics rebuilt.'))
                    continue
                self._rebuild_derived_table(
                    ch, ch_db, table, atomic=atomic, pg_fn_kwargs=pg_fn_kwargs,
                )
                self.stdout.write(self.style.SUCCESS(f'  {table} rebuilt.'))
            except Exception as exc:
                sync_failed = True
                self.stderr.write(self.style.ERROR(f'  {table} rebuild failed: {exc}'))

        if not sync_failed:
            sync_version = timezone.now().isoformat()
            cache.set('analytics:clickhouse_sync_version', sync_version, None)
            self._reconcile(ch, ch_db)
            self.stdout.write(self.style.SUCCESS(f'Sync complete (version {sync_version}).'))
            try:
                from dataviz.views.api.chart_api import warm_published_page_charts
                n = warm_published_page_charts(limit=200, warm_schools=True)
                self.stdout.write(f'Warmed {n} published page charts.')
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f'Chart cache warm skipped: {exc}'))
            try:
                from dataexploration.view_modules.student_list_views import warm_data_explorer_default_cache
                de_warmed = warm_data_explorer_default_cache()
                self.stdout.write(f'Warmed Data Explorer default cache ({de_warmed} views).')
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f'Data Explorer cache warm skipped: {exc}'))
        else:
            self.stderr.write(self.style.WARNING(
                'Sync completed with failures; cache version was not advanced.'
            ))

    def _sync_source_table(self, ch, ch_db, table, atomic, force_full, pg_fn_kwargs):
        if table == 'student_marks_list':
            self._full_replace_marks_from_alt(ch, ch_db, atomic)
            pg_count = self._pg_count(table)
            self._record_meta(
                ch, ch_db, table,
                pg_count if pg_count is not None else self._ch_count(ch, ch_db, table) or 0,
                None, 'full-alt',
            )
            return 'full-alt'

        pg_fn = _pg_table_fn(table=table, **pg_fn_kwargs)
        ts_col = TABLE_TS_COLS.get(table)
        watermark = cache.get(_watermark_key(table)) if ts_col and not force_full else None
        pg_count, ch_count, changed_count, source_max_ts = self._probe_counts(
            ch, ch_db, table, ts_col, watermark, pg_fn,
        )
        mode = 'full' if force_full else choose_sync_mode(
            table, watermark, pg_count, ch_count, changed_count,
        )
        if mode == 'skip':
            self._record_meta(ch, ch_db, table, pg_count or ch_count or 0, source_max_ts, 'skip')
            return 'skip'
        if mode == 'incremental':
            try:
                self._incremental_replace(
                    ch, ch_db, table, ts_col, _ch_ts_literal(watermark), pg_fn, atomic,
                )
                new_max = source_max_ts or self._pg_max_ts(table, ts_col)
                cache.set(_watermark_key(table), new_max, None)
                self._record_meta(ch, ch_db, table, pg_count or 0, new_max, 'incremental')
                if pg_count is not None:
                    ch_after = self._ch_count(ch, ch_db, table)
                    if ch_after is not None and int(ch_after) != int(pg_count):
                        self.stdout.write(self.style.WARNING(
                            f'  {table} incremental row count mismatch '
                            f'(pg={pg_count} ch={ch_after}); falling back to full.'
                        ))
                        self._full_replace(ch, ch_db, table, pg_fn, atomic)
                        cache.set(_watermark_key(table), self._pg_max_ts(table, ts_col), None)
                        self._record_meta(
                            ch, ch_db, table, pg_count, cache.get(_watermark_key(table)), 'full',
                        )
                        return 'full-fallback'
                return 'incremental'
            except Exception as exc:
                logger.warning('Incremental sync failed for %s: %s', table, exc)
                self.stdout.write(self.style.WARNING(
                    f'  {table} incremental failed ({exc}); falling back to full.'
                ))
        self._full_replace(ch, ch_db, table, pg_fn, atomic)
        if ts_col:
            cache.set(_watermark_key(table), self._pg_max_ts(table, ts_col), None)
        self._record_meta(
            ch, ch_db, table, pg_count if pg_count is not None else self._ch_count(ch, ch_db, table) or 0,
            cache.get(_watermark_key(table)) if ts_col else None,
            'full',
        )
        return 'full'

    def _rebuild_course_program_tables(self, ch, ch_db, atomic):
        """Load course-program agg + facet tables from PostgreSQL (one schedule pass)."""
        specs = (
            ('analytics_student_course_program', COURSE_PROGRAM_AGG_FROM_TEMP, COURSE_PROGRAM_AGG_COLUMNS),
            ('analytics_course_program_values', COURSE_PROGRAM_VALUES_FROM_TEMP, COURSE_PROGRAM_VALUES_COLUMNS),
        )
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout = 0')
                for idx_sql in COURSE_PROGRAM_CATALOG_INDEXES:
                    cursor.execute(idx_sql)
                t0 = time.perf_counter()
                cursor.execute(CREATE_MATCHED_TEMP_SQL)
                cursor.execute(MATCHED_TEMP_INDEX_SQL)
                cursor.execute('SELECT count(*) FROM _course_program_matched')
                matched_rows = cursor.fetchone()[0]
                self.stdout.write(
                    f'  course program: matched {matched_rows} schedule rows in '
                    f'{(time.perf_counter() - t0):.1f}s'
                )
                for table, select_sql, col_names in specs:
                    staging = f'{table}__sync'
                    target = staging if atomic else table
                    if atomic:
                        ch.command(f'CREATE TABLE IF NOT EXISTS {ch_db}.{staging} AS {ch_db}.{table}')
                        ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')
                    else:
                        ch.command(f'TRUNCATE TABLE {ch_db}.{table}')
                    insert_table = f'{ch_db}.{target}'
                    batch_size = 5_000
                    inserted = 0
                    t1 = time.perf_counter()
                    cursor.execute(select_sql)
                    while True:
                        rows = cursor.fetchmany(batch_size)
                        if not rows:
                            break
                        ch.insert(
                            insert_table,
                            [list(r) for r in rows],
                            column_names=list(col_names),
                        )
                        inserted += len(rows)
                    if atomic:
                        ch.command(f'EXCHANGE TABLES {ch_db}.{table} AND {ch_db}.{staging}')
                        ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')
                    count = self._ch_count(ch, ch_db, table) or 0
                    self._record_meta(ch, ch_db, table, count, None, 'derived-pg')
                    self.stdout.write(
                        f'  {table}: inserted {inserted} rows in {(time.perf_counter() - t1):.1f}s'
                    )
        cache.delete('analytics:course_program_ch_ready')

    def _rebuild_derived_table(self, ch, ch_db, table, atomic, pg_fn_kwargs=None):
        staging = f'{table}__sync'
        target = staging if atomic else table
        if atomic:
            # Recreate the staging table from the live schema every run. A plain
            # ``CREATE IF NOT EXISTS`` would keep a stale staging table across a
            # schema change (e.g. a newly added column), which then fails the
            # INSERT with a column-count mismatch.
            ch.command(f'DROP TABLE IF EXISTS {ch_db}.{staging}')
            ch.command(f'CREATE TABLE {ch_db}.{staging} AS {ch_db}.{table}')
        else:
            ch.command(f'TRUNCATE TABLE {ch_db}.{table}')
        ch.command(self._derived_insert_sql(ch_db, table, target, pg_fn_kwargs or {}))
        if atomic:
            ch.command(f'EXCHANGE TABLES {ch_db}.{table} AND {ch_db}.{staging}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')
        count = self._ch_count(ch, ch_db, table) or 0
        self._record_meta(ch, ch_db, table, count, None, 'derived')

    def _full_replace(self, ch, ch_db, table, pg_fn, atomic):
        staging = f'{table}__sync'
        target = staging if atomic else table
        if atomic:
            ch.command(f'CREATE TABLE IF NOT EXISTS {ch_db}.{staging} AS {ch_db}.{table}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')
        else:
            ch.command(f'TRUNCATE TABLE {ch_db}.{table}')
        ch.command(f'INSERT INTO {ch_db}.{target} SELECT * FROM {pg_fn}')
        if atomic:
            ch.command(f'EXCHANGE TABLES {ch_db}.{table} AND {ch_db}.{staging}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')

    def _full_replace_marks_from_alt(self, ch, ch_db, atomic):
        """Load ClickHouse student_marks_list from PostgreSQL student_marks_table_alt only."""
        table = 'student_marks_list'
        staging = f'{table}__sync'
        target = staging if atomic else table
        if atomic:
            ch.command(f'CREATE TABLE IF NOT EXISTS {ch_db}.{staging} AS {ch_db}.{table}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')
        else:
            ch.command(f'TRUNCATE TABLE {ch_db}.{table}')

        batch_size = 10_000
        insert_table = f'{ch_db}.{target}'
        col_names = list(STUDENT_MARKS_LIST_COLUMN_NAMES)
        # ClickHouse MergeTree String columns are non-Nullable in our schema.
        non_null_string_cols = {
            'marks_id', 'stu_annual_key', 'ssid', 'school_year', 'mark_period',
        }
        col_indexes = {name: idx for idx, name in enumerate(col_names)}

        def _sanitize_row(row):
            row = list(row)
            for col in non_null_string_cols:
                idx = col_indexes[col]
                if row[idx] is None:
                    row[idx] = ''
            return row

        with connection.cursor() as cursor:
            cursor.execute(STUDENT_MARKS_FROM_ALT_SELECT)
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                ch.insert(
                    insert_table,
                    [_sanitize_row(r) for r in rows],
                    column_names=col_names,
                )

        if atomic:
            ch.command(f'EXCHANGE TABLES {ch_db}.{table} AND {ch_db}.{staging}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')

    def _incremental_replace(self, ch, ch_db, table, ts_col, watermark, pg_fn, atomic):
        pk = TABLE_PKS[table]
        staging = f'{table}__sync'
        target = staging if atomic else table
        if atomic:
            ch.command(f'CREATE TABLE IF NOT EXISTS {ch_db}.{staging} AS {ch_db}.{table}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')
            ch.command(f"""
                INSERT INTO {ch_db}.{staging}
                SELECT * FROM {ch_db}.{table}
                WHERE {pk} NOT IN (
                    SELECT {pk} FROM {pg_fn}
                    WHERE {ts_col} > toDateTime64('{watermark}', 6)
                )
            """)
        else:
            ch.command(f"""
                ALTER TABLE {ch_db}.{table} DELETE WHERE {pk} IN (
                    SELECT {pk} FROM {pg_fn}
                    WHERE {ts_col} > toDateTime64('{watermark}', 6)
                )
            """)
            target = table
        ch.command(f"""
            INSERT INTO {ch_db}.{target}
            SELECT * FROM {pg_fn}
            WHERE {ts_col} > toDateTime64('{watermark}', 6)
        """)
        if atomic:
            ch.command(f'EXCHANGE TABLES {ch_db}.{table} AND {ch_db}.{staging}')
            ch.command(f'TRUNCATE TABLE {ch_db}.{staging}')

    def _derived_insert_sql(self, ch_db, table, target, pg_fn_kwargs=None):
        if table == 'results_with_tests':
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    r.test_id, r.ssid, r.local_student_id, r.school_year, r.school_code,
                    r.pl, r.ss, r.other_info, r.test_group_key,
                    t.test_name, t.subject, t.test_window
                FROM {ch_db}.results r
                INNER JOIN {ch_db}.tests t ON r.test_group_key = t.test_group_key
            """
        if table == 'analytics_student_profile':
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    cs.stu_annual_key, cs.ssid, cs.student_id, cs.student_name, cs.grade,
                    cs.school, cs.school_state_id, sch.short_name, cs.gender, cs.race,
                    cs.ell, cs.sed, cs.foster, cs.homeless, cs.sped, cs.gate, cs.in_504,
                    cs.dli, cs.atendace_rate, cs.suspension_indicator, cs.ltel, cs.status,
                    cs.school_year, cs.add_data,
                    se.first_name, se.middle_name, se.last_name, se.birth_date, se.enter_date,
                    se.leave_date, se.us_enter_date, se.state_enter_date, se.ell_status,
                    se.fep_date, se.instructional_setting, se.special_program_code, se.migrant,
                    att.attendance_percent, att.absent_fte, att.tardy_count, att.days_enrolled
                FROM {ch_db}.current_student cs
                LEFT JOIN (
                    SELECT
                        se.student_id,
                        se.school_year,
                        argMax(se.first_name, se.enter_date) AS first_name,
                        argMax(se.middle_name, se.enter_date) AS middle_name,
                        argMax(se.last_name, se.enter_date) AS last_name,
                        argMax(se.birth_date, se.enter_date) AS birth_date,
                        argMax(se.enter_date, se.enter_date) AS enter_date,
                        argMax(se.leave_date, se.enter_date) AS leave_date,
                        argMax(se.us_enter_date, se.enter_date) AS us_enter_date,
                        argMax(se.state_enter_date, se.enter_date) AS state_enter_date,
                        argMax(se.ell_status, se.enter_date) AS ell_status,
                        argMax(se.fep_date, se.enter_date) AS fep_date,
                        argMax(se.instructional_setting, se.enter_date) AS instructional_setting,
                        argMax(se.special_program_code, se.enter_date) AS special_program_code,
                        argMax(se.migrant, se.enter_date) AS migrant
                    FROM {ch_db}.student_enroll se
                    GROUP BY se.student_id, se.school_year
                ) se ON se.student_id = cs.student_id AND se.school_year = cs.school_year
                LEFT JOIN (
                    SELECT state_id, any(short_name) AS short_name
                    FROM {ch_db}.school
                    GROUP BY state_id
                ) sch ON sch.state_id = cs.school_state_id
                LEFT JOIN {ch_db}.student_attendance att ON att.stu_annual_key = cs.stu_annual_key
            """
        if table == 'analytics_student_results':
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    r.test_id, r.ssid, r.local_student_id, r.school_year, r.school_code,
                    r.pl, r.ss, r.other_info, r.test_group_key, r.test_name, r.subject,
                    r.test_window, cs.stu_annual_key, cs.student_name, cs.grade, cs.school,
                    cs.school_state_id, cs.status, cs.gender, cs.race, cs.ell, cs.sed, cs.sped
                FROM {ch_db}.results_with_tests r
                LEFT JOIN {ch_db}.current_student cs
                    ON cs.ssid = r.ssid AND cs.school_year = r.school_year
            """
        if table == 'analytics_student_marks':
            # sm comes from CH student_marks_list, itself loaded from PG student_marks_table_alt.
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    sm.marks_id, sm.stu_annual_key, sm.ssid, sm.school_year, sm.student_id,
                    sm.school_name, sm.state_school_id, sm.school_type, sm.subject,
                    sm.course_name, sm.staff_name, sm.staff_email, sm.staff_employee_id,
                    sm.mark_period, sm.mark, sm.created_at, sm.updated_at,
                    cs.student_name, cs.grade, cs.status, cs.gender, cs.school
                FROM {ch_db}.student_marks_list sm
                LEFT JOIN {ch_db}.current_student cs
                    ON cs.ssid = sm.ssid AND cs.school_year = sm.school_year
            """
        if table == 'analytics_test_metadata':
            # One row per (year, test, subject, window, info_key). info_key ''
            # represents the base SS/PL columns; each additional key (DFP,
            # condition codes, …) gets its own row. The current_student join
            # mirrors the read-path semantics (a result only counts when it maps
            # to a known student via local_student_id).
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT DISTINCT
                    r.school_year,
                    ifNull(r.test_name, ''),
                    ifNull(r.subject, ''),
                    ifNull(r.test_window, ''),
                    '' AS info_key
                FROM {ch_db}.results_with_tests r
                INNER JOIN {ch_db}.current_student cs
                    ON cs.student_id = r.local_student_id
                WHERE r.school_year IS NOT NULL AND r.test_name IS NOT NULL
                UNION ALL
                SELECT DISTINCT
                    r.school_year,
                    ifNull(r.test_name, ''),
                    ifNull(r.subject, ''),
                    ifNull(r.test_window, ''),
                    k AS info_key
                FROM {ch_db}.results_with_tests r
                INNER JOIN {ch_db}.current_student cs
                    ON cs.student_id = r.local_student_id
                ARRAY JOIN JSONExtractKeys(assumeNotNull(r.other_info)) AS k
                WHERE r.school_year IS NOT NULL AND r.test_name IS NOT NULL
                  AND r.other_info IS NOT NULL AND r.other_info != ''
            """
        if table == 'analytics_marks_metadata':
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT DISTINCT
                    sm.school_year,
                    ifNull(sm.subject, ''),
                    ifNull(sm.course_name, ''),
                    ifNull(sm.mark_period, '')
                FROM {ch_db}.student_marks_list sm
                INNER JOIN {ch_db}.current_student cs
                    ON cs.ssid = sm.ssid
                WHERE sm.school_year IS NOT NULL
            """
        pg = pg_fn_kwargs or {}
        if table == 'analytics_ap_results':
            r = _pg_table_fn(table='dataLoader_apexamresult', **pg)
            e = _pg_table_fn(table='dataLoader_apexamcode', **pg)
            il = _pg_table_fn(table='dataLoader_apimportlog', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    ifNull(r.ssid, ''), ifNull(r.admin_year, ''), ifNull(e.name, ''),
                    r.exam_grade, ifNull(toString(r.exam_id), '')
                FROM {r} AS r
                INNER JOIN {e} AS e ON r.exam_id = e.code
                INNER JOIN {il} AS il ON r.import_log_id = il.id
                WHERE il.is_published
            """
        if table == 'analytics_ap_awards':
            a = _pg_table_fn(table='dataLoader_apstudentaward', **pg)
            t = _pg_table_fn(table='dataLoader_apawardtype', **pg)
            il = _pg_table_fn(table='dataLoader_apimportlog', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    ifNull(a.ssid, ''), ifNull(a.award_year, ''), ifNull(t.description, '')
                FROM {a} AS a
                INNER JOIN {t} AS t ON a.award_type_id = t.code
                INNER JOIN {il} AS il ON a.import_log_id = il.id
                WHERE il.is_published
            """
        if table == 'analytics_sat_results':
            from dataLoader.models import SATSettings
            sat_settings = SATSettings.get_solo()
            regions = set(sat_settings.percentile_regions or [])
            metrics = set(sat_settings.percentile_metrics or [])
            result_mode = sat_settings.result_mode
            highest_metric = sat_settings.highest_metric

            def _pct(region, metric, inner_col):
                if region in regions and metric in metrics:
                    return inner_col
                return "CAST(NULL AS Nullable(Int32))"

            metric_cols = {
                'total': 'r.total_score',
                'rw': 'r.ebrw_score',
                'math': 'r.math_score',
            }
            if result_mode == 'highest':
                order_metric = metric_cols.get(highest_metric, 'r.total_score')
                order_expr = f"{order_metric} DESC, r.test_date DESC"
            else:
                order_expr = "r.test_date DESC"

            r = _pg_table_fn(table='dataLoader_satresult', **pg)
            il = _pg_table_fn(table='dataLoader_satimportlog', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    t.ssid, t.school_year, t.test_date, t.school_name,
                    t.total_score, t.ebrw_score, t.math_score,
                    {_pct('state', 'total', 't.percentile_state_total')},
                    {_pct('state', 'rw', 't.percentile_state_rw')},
                    {_pct('state', 'math', 't.percentile_state_math')},
                    {_pct('country', 'total', 't.percentile_country_total')},
                    {_pct('country', 'rw', 't.percentile_country_rw')},
                    {_pct('country', 'math', 't.percentile_country_math')},
                    {_pct('natrep', 'total', 't.percentile_natrep_total')},
                    {_pct('natrep', 'rw', 't.percentile_natrep_rw')},
                    {_pct('natrep', 'math', 't.percentile_natrep_math')},
                    {_pct('natuser', 'total', 't.percentile_natuser_total')},
                    {_pct('natuser', 'rw', 't.percentile_natuser_rw')},
                    {_pct('natuser', 'math', 't.percentile_natuser_math')}
                FROM (
                    SELECT
                        r.ssid AS ssid,
                        r.school_year AS school_year,
                        ifNull(toString(r.test_date), '') AS test_date,
                        ifNull(r.school_name, '') AS school_name,
                        r.total_score AS total_score,
                        r.ebrw_score AS ebrw_score,
                        r.math_score AS math_score,
                        r.percentile_state_total AS percentile_state_total,
                        r.percentile_state_rw AS percentile_state_rw,
                        r.percentile_state_math AS percentile_state_math,
                        r.percentile_country_total AS percentile_country_total,
                        r.percentile_country_rw AS percentile_country_rw,
                        r.percentile_country_math AS percentile_country_math,
                        r.percentile_natrep_total AS percentile_natrep_total,
                        r.percentile_natrep_rw AS percentile_natrep_rw,
                        r.percentile_natrep_math AS percentile_natrep_math,
                        r.percentile_natuser_total AS percentile_natuser_total,
                        r.percentile_natuser_rw AS percentile_natuser_rw,
                        r.percentile_natuser_math AS percentile_natuser_math,
                        row_number() OVER (
                            PARTITION BY r.ssid, r.school_year ORDER BY {order_expr}
                        ) AS _rn
                    FROM {r} AS r
                    INNER JOIN {il} AS il ON r.import_log_id = il.id
                    WHERE il.is_published AND r.ssid != ''
                ) AS t
                WHERE t._rn = 1
            """
        if table == 'analytics_survey_responses':
            sr = _pg_table_fn(table='dataLoader_surveyresponse', **pg)
            sq = _pg_table_fn(table='dataLoader_surveyquestion', **pg)
            sd = _pg_table_fn(table='dataLoader_surveydefinition', **pg)
            il = _pg_table_fn(table='dataLoader_importlog', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    ifNull(sr.student_id, ''), ifNull(sr.ssid, ''),
                    ifNull(sd.survey_type, ''), ifNull(sd.school_year, ''),
                    ifNull(sd.testing_window, ''), ifNull(sd.grade_range, ''),
                    toInt32(sq.`order`), sr.value
                FROM {sr} AS sr
                INNER JOIN {sq} AS sq ON sr.question_id = sq.id
                INNER JOIN {sd} AS sd ON sq.survey_definition_id = sd.id
                INNER JOIN {il} AS il ON sr.import_log_id = il.id
                WHERE il.ready_for_exploration AND il.status = 'COMPLETED' AND sd.is_active
            """
        if table == 'analytics_ccgi_favorite_career':
            ce = _pg_table_fn(table='ccgi_career_explo', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    l.ssid, l.stu_annual_key, l.student_name, l.grade, l.school,
                    l.school_state_id, l.status, l.sy AS school_year, career
                FROM (
                    SELECT
                        ssid,
                        argMax(stu_annual_key, school_year) AS stu_annual_key,
                        argMax(student_name, school_year) AS student_name,
                        argMax(grade, school_year) AS grade,
                        argMax(school, school_year) AS school,
                        argMax(school_state_id, school_year) AS school_state_id,
                        argMax(status, school_year) AS status,
                        max(school_year) AS sy
                    FROM {ch_db}.current_student
                    GROUP BY ssid
                ) AS l
                INNER JOIN (
                    SELECT ssid, JSONExtract(favorite_careers, 'Array(String)') AS careers
                    FROM {ce}
                    WHERE favorite_careers IS NOT NULL
                ) AS ce ON ce.ssid = l.ssid
                ARRAY JOIN ce.careers AS career
                WHERE career != ''
            """
        if table == 'analytics_ccgi_career_cluster':
            ce = _pg_table_fn(table='ccgi_career_explo', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    l.ssid, l.stu_annual_key, l.student_name, l.grade, l.school,
                    l.school_state_id, l.status, l.sy AS school_year,
                    replaceRegexpOne(kv.1, '^Cluster: ''(.*)''$', '\\1') AS cluster,
                    toInt32OrZero(kv.2) AS cluster_count
                FROM (
                    SELECT
                        ssid,
                        argMax(stu_annual_key, school_year) AS stu_annual_key,
                        argMax(student_name, school_year) AS student_name,
                        argMax(grade, school_year) AS grade,
                        argMax(school, school_year) AS school,
                        argMax(school_state_id, school_year) AS school_state_id,
                        argMax(status, school_year) AS status,
                        max(school_year) AS sy
                    FROM {ch_db}.current_student
                    GROUP BY ssid
                ) AS l
                INNER JOIN (
                    SELECT ssid, arrayFilter(x -> x.2 != '', JSONExtractKeysAndValues(career_clusters, 'String')) AS clusters
                    FROM {ce}
                    WHERE career_clusters IS NOT NULL
                ) AS ce ON ce.ssid = l.ssid
                ARRAY JOIN ce.clusters AS kv
            """
        if table == 'analytics_ccgi_college_financial':
            cf = _pg_table_fn(table='ccgi_college_financial', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    l.ssid, l.stu_annual_key, l.student_name, l.grade, l.school,
                    l.school_state_id, l.status, l.sy AS school_year,
                    toFloat64OrNull(NULLIF(JSONExtractString(cf.csu, 'gpa'), '')) AS csu_gpa,
                    JSONExtractString(cf.csu, 'eligibility_cmp') AS csu_eligibility,
                    toInt32OrDefault(JSONExtractString(cf.csu, 'completed_app_count')) AS csu_completed_apps,
                    length(JSONExtract(cf.csu, 'in_progress_applications', 'Array(String)')) AS csu_in_progress_apps,
                    toFloat64OrNull(NULLIF(JSONExtractString(cf.uc, 'gpa'), '')) AS uc_gpa,
                    JSONExtractString(cf.uc, 'eligibility_cmp') AS uc_eligibility,
                    toInt32OrDefault(JSONExtractString(cf.uc, 'completed_app_count')) AS uc_completed_apps,
                    length(JSONExtract(cf.uc, 'in_progress_applications', 'Array(String)')) AS uc_in_progress_apps,
                    toInt32OrDefault(JSONExtractString(cf.ccc, 'completed_app_count')) AS ccc_completed_apps,
                    arrayStringConcat(JSONExtract(cf.ccc, 'completed_applications', 'Array(String)'), ', ') AS ccc_completed_app,
                    JSONExtractString(cf.ccc, 'most_recent_completed_date') AS ccc_most_recent_completed_date,
                    ifNull(cf.financial_aid_application_status, 'Unknown') AS financial_aid_status,
                    NULLIF(JSONExtractString(cf.csu, 'eligibility_detail'), '') AS csu_eligibility_detail,
                    NULLIF(JSONExtractString(cf.uc, 'eligibility_detail'), '') AS uc_eligibility_detail,
                    toInt32(JSONExtractString(cf.csu, 'eligibility_cmp') = 'Requires Review') AS csu_requires_review,
                    toInt32(JSONExtractString(cf.uc, 'eligibility_cmp') = 'Requires Review') AS uc_requires_review,
                    multiIf(
                        JSONExtractString(cf.csu, 'eligibility_cmp') = 'Requires Review'
                            AND JSONExtractString(cf.uc, 'eligibility_cmp') = 'Requires Review', 'Both CSU & UC',
                        JSONExtractString(cf.csu, 'eligibility_cmp') = 'Requires Review', 'CSU only',
                        JSONExtractString(cf.uc, 'eligibility_cmp') = 'Requires Review', 'UC only',
                        NULL
                    ) AS review_scope,
                    NULLIF(JSONExtractString(cf.ccc, 'id'), '') AS ccc_apply_id
                FROM (
                    SELECT
                        ssid,
                        argMax(stu_annual_key, school_year) AS stu_annual_key,
                        argMax(student_name, school_year) AS student_name,
                        argMax(grade, school_year) AS grade,
                        argMax(school, school_year) AS school,
                        argMax(school_state_id, school_year) AS school_state_id,
                        argMax(status, school_year) AS status,
                        max(school_year) AS sy
                    FROM {ch_db}.current_student
                    GROUP BY ssid
                ) AS l
                INNER JOIN {cf} AS cf ON cf.ssid = l.ssid
            """
        if table == 'analytics_ccgi_college_application':
            cf = _pg_table_fn(table='ccgi_college_financial', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    l.ssid, l.stu_annual_key, l.student_name, l.grade, l.school,
                    l.school_state_id, l.status, l.sy AS school_year,
                    rec.1 AS system,
                    rec.2 AS application_status,
                    rec.3 AS college_name,
                    rec.4 AS most_recent_completed_date,
                    apps.csu_eligibility AS csu_eligibility,
                    apps.uc_eligibility AS uc_eligibility,
                    NULLIF(if(rec.1 = 'CCC', apps.ccc_id, ''), '') AS application_id,
                    apps.csu_gpa AS csu_gpa,
                    apps.uc_gpa AS uc_gpa
                FROM (
                    SELECT
                        ssid,
                        argMax(stu_annual_key, school_year) AS stu_annual_key,
                        argMax(student_name, school_year) AS student_name,
                        argMax(grade, school_year) AS grade,
                        argMax(school, school_year) AS school,
                        argMax(school_state_id, school_year) AS school_state_id,
                        argMax(status, school_year) AS status,
                        max(school_year) AS sy
                    FROM {ch_db}.current_student
                    GROUP BY ssid
                ) AS l
                INNER JOIN (
                    SELECT
                        ssid,
                        JSONExtractString(csu, 'eligibility_cmp') AS csu_eligibility,
                        JSONExtractString(uc, 'eligibility_cmp') AS uc_eligibility,
                        JSONExtractString(ccc, 'id') AS ccc_id,
                        toFloat64OrNull(NULLIF(JSONExtractString(csu, 'gpa'), '')) AS csu_gpa,
                        toFloat64OrNull(NULLIF(JSONExtractString(uc, 'gpa'), '')) AS uc_gpa,
                        arrayConcat(
                            arrayMap(x -> ('CSU', 'Completed', x, ''),
                                     JSONExtract(csu, 'completed_applications', 'Array(String)')),
                            arrayMap(x -> ('CSU', 'In Progress', x, ''),
                                     JSONExtract(csu, 'in_progress_applications', 'Array(String)')),
                            arrayMap(x -> ('UC', 'Completed', x, ''),
                                     JSONExtract(uc, 'completed_applications', 'Array(String)')),
                            arrayMap(x -> ('UC', 'In Progress', x, ''),
                                     JSONExtract(uc, 'in_progress_applications', 'Array(String)')),
                            arrayMap(x -> ('CCC', 'Completed', x,
                                           JSONExtractString(ccc, 'most_recent_completed_date')),
                                     JSONExtract(ccc, 'completed_applications', 'Array(String)'))
                        ) AS recs
                    FROM {cf}
                ) AS apps ON apps.ssid = l.ssid
                ARRAY JOIN apps.recs AS rec
                WHERE rec.3 != ''
            """
        if table == 'analytics_ccgi_application_status':
            cf = _pg_table_fn(table='ccgi_college_financial', **pg)
            return f"""
                INSERT INTO {ch_db}.{target}
                SELECT
                    l.ssid, l.stu_annual_key, l.student_name, l.grade, l.school,
                    l.school_state_id, l.status, l.sy AS school_year,
                    rec.1 AS system,
                    rec.2 AS application_status,
                    NULLIF(rec.3, '') AS eligibility,
                    apps.csu_gpa AS csu_gpa,
                    apps.uc_gpa AS uc_gpa
                FROM (
                    SELECT
                        ssid,
                        argMax(stu_annual_key, school_year) AS stu_annual_key,
                        argMax(student_name, school_year) AS student_name,
                        argMax(grade, school_year) AS grade,
                        argMax(school, school_year) AS school,
                        argMax(school_state_id, school_year) AS school_state_id,
                        argMax(status, school_year) AS status,
                        max(school_year) AS sy
                    FROM {ch_db}.current_student
                    GROUP BY ssid
                ) AS l
                INNER JOIN (
                    SELECT
                        ssid,
                        toFloat64OrNull(NULLIF(JSONExtractString(csu, 'gpa'), '')) AS csu_gpa,
                        toFloat64OrNull(NULLIF(JSONExtractString(uc, 'gpa'), '')) AS uc_gpa,
                        arrayConcat(
                            arrayMap(x -> ('CSU', x, JSONExtractString(csu, 'eligibility_cmp')), csu_statuses),
                            arrayMap(x -> ('UC', x, JSONExtractString(uc, 'eligibility_cmp')), uc_statuses),
                            arrayMap(x -> ('CCC', x, ''), ccc_statuses)
                        ) AS recs
                    FROM (
                        SELECT
                            ssid, csu, uc, ccc,
                            arrayConcat(
                                if(length(JSONExtract(csu, 'completed_applications', 'Array(String)')) > 0, ['Completed'], []),
                                if(length(JSONExtract(csu, 'in_progress_applications', 'Array(String)')) > 0, ['In Progress'], []),
                                if(length(JSONExtract(csu, 'completed_applications', 'Array(String)')) = 0
                                   AND length(JSONExtract(csu, 'in_progress_applications', 'Array(String)')) = 0, ['Not Applied'], [])
                            ) AS csu_statuses,
                            arrayConcat(
                                if(length(JSONExtract(uc, 'completed_applications', 'Array(String)')) > 0, ['Completed'], []),
                                if(length(JSONExtract(uc, 'in_progress_applications', 'Array(String)')) > 0, ['In Progress'], []),
                                if(length(JSONExtract(uc, 'completed_applications', 'Array(String)')) = 0
                                   AND length(JSONExtract(uc, 'in_progress_applications', 'Array(String)')) = 0, ['Not Applied'], [])
                            ) AS uc_statuses,
                            arrayConcat(
                                if(length(JSONExtract(ccc, 'completed_applications', 'Array(String)')) > 0, ['Completed'], []),
                                if(length(JSONExtract(ccc, 'completed_applications', 'Array(String)')) = 0, ['Not Applied'], [])
                            ) AS ccc_statuses
                        FROM {cf}
                    ) AS base
                ) AS apps ON apps.ssid = l.ssid
                ARRAY JOIN apps.recs AS rec
            """
        raise ValueError(f'Unknown derived table {table}')

    def _probe_counts(self, ch, ch_db, table, ts_col, watermark, pg_fn):
        pg_count = self._pg_count(table)
        ch_count = self._ch_count(ch, ch_db, table)
        changed_count = 0
        source_max_ts = None
        if ts_col:
            source_max_ts = self._pg_max_ts(table, ts_col)
            if watermark:
                changed_count = self._pg_changed_count(table, ts_col, watermark)
        return pg_count, ch_count, changed_count, source_max_ts

    def _pg_count(self, table):
        try:
            with connection.cursor() as cursor:
                if table == 'student_marks_list':
                    cursor.execute(STUDENT_MARKS_FROM_ALT_COUNT)
                else:
                    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                return int(cursor.fetchone()[0])
        except Exception:
            return None

    def _pg_max_ts(self, table, ts_col):
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT MAX("{ts_col}") FROM "{table}"')
                value = cursor.fetchone()[0]
                return value.isoformat() if value is not None else None
        except Exception:
            return None

    def _pg_changed_count(self, table, ts_col, watermark):
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{ts_col}" > %s',
                    [watermark],
                )
                return int(cursor.fetchone()[0])
        except Exception:
            return 0

    def _ch_count(self, ch, ch_db, table):
        try:
            result = ch.query(f'SELECT count() FROM {ch_db}.{table}')
            rows = result.result_rows if hasattr(result, 'result_rows') else list(result)
            return int(rows[0][0]) if rows else 0
        except Exception:
            return None

    def _reconcile(self, ch, ch_db):
        mismatches = []
        for table in SOURCE_TABLES:
            pg_count = self._pg_count(table)
            ch_count = self._ch_count(ch, ch_db, table)
            if pg_count is None or ch_count is None:
                continue
            if int(pg_count) != int(ch_count):
                mismatches.append(f'{table}: pg={pg_count} ch={ch_count}')
                self._record_meta(ch, ch_db, table, ch_count, None, 'mismatch')
        if mismatches:
            self.stderr.write(self.style.WARNING(
                'Row-count mismatches: ' + '; '.join(mismatches)
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Reconciliation: PostgreSQL and ClickHouse row counts match.'))

    def _record_meta(self, ch, ch_db, table, row_count, source_max_ts, mode):
        ts_sql = f"toDateTime64('{_ch_ts_literal(source_max_ts)}', 6)" if source_max_ts else 'NULL'
        version = cache.get('analytics:clickhouse_sync_version') or timezone.now().isoformat()
        try:
            ch.command(f"""
                INSERT INTO {ch_db}.analytics_sync_meta
                (table_name, last_sync, source_max_ts, row_count, sync_version, mode)
                VALUES (
                    '{table}', now64(), {ts_sql}, {int(row_count or 0)},
                    '{version}', '{mode}'
                )
            """)
        except Exception as exc:
            logger.warning('Failed to record sync meta for %s: %s', table, exc)
