from django.core.management.base import BaseCommand
from django.db import connection

from ca_dash.services.indicators.registry import INDICATORS


class Command(BaseCommand):
    help = 'List dash_indi_* tables, row counts, and reporting year ranges.'

    def handle(self, *args, **options):
        for key, ind in INDICATORS.items():
            table = f'dash_indi_{key}'
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = %s
                    """,
                    [table],
                )
                if not cursor.fetchone():
                    self.stdout.write(self.style.WARNING(f"MISSING: {table} ({ind.label})"))
                    continue
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                count = cursor.fetchone()[0]
                cursor.execute(
                    f"""
                    SELECT
                        MIN(NULLIF(regexp_replace(trim(reportingyear::text), '[^0-9]', '', 'g'), '')::int),
                        MAX(NULLIF(regexp_replace(trim(reportingyear::text), '[^0-9]', '', 'g'), '')::int)
                    FROM "{table}"
                    """
                )
                yr_min, yr_max = cursor.fetchone()
            self.stdout.write(
                f"OK: {table} — {count:,} rows, years {yr_min}–{yr_max} ({ind.label})"
            )