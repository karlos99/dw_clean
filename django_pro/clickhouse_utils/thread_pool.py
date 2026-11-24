"""Shared, bounded thread pools for ClickHouse/analytics I/O work.

Separates outer multi-chart batch orchestration (``get_chart_pool``) from leaf
sub-query fan-out (``get_io_pool``) so outer tasks waiting on inner enrichment
queries can never deadlock a single shared pool. A thread-local re-entrancy
flag (``is_in_io_pool``) also ensures callers already executing on a ``ch-io``
thread run sub-work inline instead of blocking on their own pool.

Each gunicorn worker process gets its own pool instances (imported fresh per
process). Keep workers × active queries under ClickHouse max_concurrent_queries
(100): 8 gunicorn workers × 6 chart + 6 io threads <= 96 peak.
"""

from concurrent.futures import ThreadPoolExecutor
import threading

_LOCAL_FLAGS = threading.local()


def _io_worker_initializer():
    _LOCAL_FLAGS.in_io_pool = True


def _chart_worker_initializer():
    _LOCAL_FLAGS.in_chart_pool = True


_IO_POOL = ThreadPoolExecutor(
    max_workers=6,
    thread_name_prefix='ch-io',
    initializer=_io_worker_initializer,
)
_CHART_POOL = ThreadPoolExecutor(
    max_workers=6,
    thread_name_prefix='ch-chart',
    initializer=_chart_worker_initializer,
)


def get_io_pool() -> ThreadPoolExecutor:
    return _IO_POOL


def get_chart_pool() -> ThreadPoolExecutor:
    return _CHART_POOL


def is_in_io_pool() -> bool:
    return bool(getattr(_LOCAL_FLAGS, 'in_io_pool', False))


def is_in_chart_pool() -> bool:
    return bool(getattr(_LOCAL_FLAGS, 'in_chart_pool', False))


def close_thread_db_connection():
    """Drop this thread's Django PostgreSQL connection after pool work.

    Pool worker threads are reused across HTTP requests. With CONN_MAX_AGE > 0,
    each thread otherwise keeps a backend open until max age expires, which can
    exhaust Postgres max_connections under parallel chart/home renders. Never
    closes a connection currently inside an atomic transaction block.
    """
    try:
        from django.db import connections

        conn = connections['default']
        if getattr(conn, 'in_atomic_block', False) is True:
            return
        conn.close()
    except Exception:
        pass
