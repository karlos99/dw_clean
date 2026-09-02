"""Shared request IDs and elapsed timings for analytics hot paths."""

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

logger = logging.getLogger('analytics.timing')
_request_id = ContextVar('analytics_request_id', default='')


def current_request_id():
    rid = _request_id.get()
    if not rid:
        rid = uuid.uuid4().hex[:12]
        _request_id.set(rid)
    return rid


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
