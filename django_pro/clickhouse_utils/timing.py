"""Shared request IDs and elapsed timings for analytics hot paths."""

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

logger = logging.getLogger('analytics.timing')
_request_id = ContextVar('analytics_request_id', default='')
_request_start = ContextVar('analytics_request_start', default=0.0)
_query_counts = ContextVar('analytics_query_counts', default=None)


def current_request_id():
    rid = _request_id.get()
    if not rid:
        rid = uuid.uuid4().hex[:12]
        _request_id.set(rid)
    return rid


def start_request(request=None):
    """Begin a request scope: bind request id, reset counters, mark start time."""
    rid = ''
    if request is not None:
        rid = request.headers.get('X-Request-ID', '') if hasattr(request, 'headers') else ''
    _request_id.set(rid or uuid.uuid4().hex[:12])
    _query_counts.set({})
    _request_start.set(time.perf_counter())
    return _request_id.get()


def reset_request():
    _request_id.set('')
    _query_counts.set(None)
    _request_start.set(0.0)


def count_query(source):
    """Increment the per-request counter for *source* (e.g. 'clickhouse', 'postgres')."""
    counts = _query_counts.get()
    if counts is not None:
        counts[source] = counts.get(source, 0) + 1


def get_query_counts():
    return dict(_query_counts.get() or {})


def elapsed_ms():
    start = _request_start.get()
    if not start:
        return 0.0
    return (time.perf_counter() - start) * 1000


@contextmanager
def timed(op):
    rid = current_request_id()
    start = time.perf_counter()
    try:
        yield rid
    finally:
        logger.info(
            'request_id=%s op=%s elapsed_ms=%.1f',
            rid, op, (time.perf_counter() - start) * 1000,
        )


def timed_fn(op):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            with timed(op):
                return fn(*args, **kwargs)
        return wrapper
    return decorator
