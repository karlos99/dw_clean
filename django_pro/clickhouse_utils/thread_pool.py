"""Shared, bounded thread pool for ClickHouse-bound (I/O) work.

Concurrent ClickHouse queries are safe now that each thread gets its own
client (see client.py), but ad-hoc `ThreadPoolExecutor(...)` instances
created per-request have no shared bound — under load, many concurrent
requests each spinning up their own pool could create an unbounded number
of threads. One process-wide pool caps total concurrency per worker process
while still letting I/O-bound ClickHouse calls run in parallel, both within
a single request (e.g. fetching test + mark data together) and across a
batch of independent chart renders.

Each gunicorn worker process gets its own pool instance (this module is
imported fresh per process) — sizing is per-process, not system-wide.

Keep workers × max_workers under ClickHouse max_concurrent_queries (100).
8 gunicorn workers × 8 CH threads = 64.
"""

from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='ch-io')


def get_io_pool() -> ThreadPoolExecutor:
    return _POOL


def close_thread_db_connection():
    """Drop this thread's Django PostgreSQL connection after I/O pool work.

    Pool worker threads are reused across HTTP requests. With CONN_MAX_AGE > 0,
    each thread otherwise keeps a backend open until max age expires, which can
    exhaust Postgres max_connections under parallel chart/home renders.
    """
    try:
        from django.db import connections

        connections['default'].close()
    except Exception:
        pass
