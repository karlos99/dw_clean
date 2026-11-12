"""
Report PostgreSQL connection usage and optionally free stale idle backends.

Use when the app shows "sorry, too many clients already" or to watch headroom
before hitting max_connections.

Examples:
    python manage.py postgres_connections
    python manage.py postgres_connections --by-client
    python manage.py postgres_connections --terminate-idle 10
    python manage.py postgres_connections --warn-pct 80
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'PostgreSQL connection diagnostics (counts, grouping, optional idle cleanup).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--by-client',
            action='store_true',
            help='Print backends grouped by user, application, client address, and state.',
        )
        parser.add_argument(
            '--terminate-idle',
            type=int,
            metavar='MINUTES',
            help='Terminate idle backends for this DB user older than MINUTES (not replication).',
        )
        parser.add_argument(
            '--warn-pct',
            type=int,
            metavar='PCT',
            help='Exit with code 2 when active backends are at or above PCT of max_connections.',
        )

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            cur.execute('SHOW max_connections')
            max_conn = int(cur.fetchone()[0])

            cur.execute('SELECT count(*) FROM pg_stat_activity')
            total = int(cur.fetchone()[0])

            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            )
            on_db = int(cur.fetchone()[0])

        pct = round(100.0 * total / max_conn, 1) if max_conn else 0
        self.stdout.write(f'max_connections={max_conn} active_backends={total} ({pct}%)')
        self.stdout.write(f'backends_on_current_database={on_db}')

        if options['by_client']:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT usename, application_name, client_addr::text, state, count(*)
                    FROM pg_stat_activity
                    GROUP BY 1, 2, 3, 4
                    ORDER BY 5 DESC
                    """
                )
                for row in cur.fetchall():
                    self.stdout.write('  ' + ' | '.join(str(x) for x in row))

        if options['terminate_idle'] is not None:
            minutes = options['terminate_idle']
            with connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE pid <> pg_backend_pid()
                      AND datname = current_database()
                      AND usename = current_user
                      AND state = 'idle'
                      AND state_change < now() - make_interval(mins => %s)
                      AND backend_type = 'client backend'
                    """,
                    [minutes],
                )
                terminated = sum(1 for (ok,) in cur.fetchall() if ok)
            self.stdout.write(self.style.WARNING(f'Terminated {terminated} idle backend(s) older than {minutes}m'))

        warn_pct = options['warn_pct']
        if warn_pct is not None and pct >= warn_pct:
            self.stderr.write(
                self.style.ERROR(
                    f'Connection usage {pct}% is at or above warn threshold {warn_pct}%'
                )
            )
            raise SystemExit(2)
