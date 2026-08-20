"""Normalize missing-score placeholder values in the results tables.

Assessment data imports sometimes store ``'NaN'`` / ``'NS'`` / ``'N/A'`` in the
``pl`` and ``ss`` columns instead of leaving them NULL.  Those literal strings
then surface on charts and in the data explorer as odd characters.  This
command rewrites those placeholders to NULL in PostgreSQL, then re-syncs the
affected ClickHouse mirrors so analytics queries see the same clean data.

Usage:
    python manage.py clean_score_sentinels
"""

import logging
import os

from django.core.management.base import BaseCommand
from django.db import connection

from dataexploration.column_utils import SCORE_SENTINELS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Replace NaN/NS/N-A placeholder score values with NULL in results tables."

    def handle(self, *args, **options):
        sentinel_values = sorted(SCORE_SENTINELS - {''})

        with connection.cursor() as cursor:
            for col in ('pl', 'ss'):
                cursor.execute(
                    f"UPDATE results SET {col} = NULL WHERE LOWER({col}) = ANY(%s)",
                    [[s.lower() for s in sentinel_values]],
                )
                updated = cursor.rowcount
                self.stdout.write(self.style.SUCCESS(
                    f"Cleaned {updated} '{col}' placeholder value(s) in PostgreSQL."
                ))

        # Rebuild the ClickHouse mirrors so analytics queries match.
        try:
            from clickhouse_utils.client import get_clickhouse_client
            from clickhouse_utils.schema import CREATE_RESULTS, CREATE_RESULTS_WITH_TESTS
            from django.conf import settings

            ch = get_clickhouse_client()
            if ch is None:
                self.stderr.write(self.style.WARNING(
                    "ClickHouse client unavailable — PostgreSQL cleaned but ClickHouse "
                    "not synced. Run: python manage.py sync_to_clickhouse --tables results,results_with_tests"
                ))
                return

            ch_db = getattr(settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db')
            ch.command(CREATE_RESULTS)
            ch.command(CREATE_RESULTS_WITH_TESTS)

            pg_host = os.getenv('POSTGRES_HOST', 'postgres')
            pg_port = os.getenv('POSTGRES_PORT', '5432')
            pg_db = os.getenv('POSTGRES_DB', 'djago_db')
            pg_user = os.getenv('POSTGRES_USER', 'dsdata_user')
            pg_password = os.getenv('POSTGRES_PASSWORD', '')

            ch.command(f"TRUNCATE TABLE {ch_db}.results")
            ch.command(
                f"INSERT INTO {ch_db}.results SELECT * FROM postgresql("
                f"'{pg_host}:{pg_port}', '{pg_db}', 'results', '{pg_user}', '{pg_password}')"
            )
            ch.command(f"TRUNCATE TABLE {ch_db}.results_with_tests")
            ch.command(
                f"INSERT INTO {ch_db}.results_with_tests "
                "SELECT r.test_id, r.ssid, r.local_student_id, r.school_year, r.school_code, "
                "r.pl, r.ss, r.other_info, r.test_group_key, t.test_name, t.subject, t.test_window "
                f"FROM {ch_db}.results r INNER JOIN {ch_db}.tests t ON r.test_group_key = t.test_group_key"
            )
            self.stdout.write(self.style.SUCCESS(
                "Re-synced ClickHouse 'results' and 'results_with_tests'."
            ))
        except Exception as exc:  # pragma: no cover - env-dependent
            self.stderr.write(self.style.ERROR(f"ClickHouse sync failed: {exc}"))
            self.stderr.write(self.style.WARNING(
                "PostgreSQL cleaned. Run: python manage.py sync_to_clickhouse --tables results,results_with_tests"
            ))
