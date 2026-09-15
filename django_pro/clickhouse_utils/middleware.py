"""Request-scoped timing and query-count logging for analytics endpoints."""

import logging

from .timing import (
    current_request_id,
    elapsed_ms,
    get_query_counts,
    reset_request,
    start_request,
)

logger = logging.getLogger('analytics.timing')

# Paths that benefit from per-request timing/counts. Everything else is skipped
# so the middleware stays effectively free for static/admin traffic.
_TIMED_PREFIXES = (
    '/dataexploration/',
    '/dataviz/',
)


class AnalyticsTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ''
        if not any(path.startswith(p) for p in _TIMED_PREFIXES):
            return self.get_response(request)

        start_request(request)
        status = '-'
        try:
            response = self.get_response(request)
            status = getattr(response, 'status_code', '-')
            return response
        finally:
            counts = get_query_counts()
            logger.info(
                'request_id=%s op=request path=%s status=%s elapsed_ms=%.1f '
                'clickhouse_queries=%s postgres_queries=%s',
                current_request_id(),
                path,
                status,
                elapsed_ms(),
                counts.get('clickhouse', 0),
                counts.get('postgres', 0),
            )
            reset_request()
