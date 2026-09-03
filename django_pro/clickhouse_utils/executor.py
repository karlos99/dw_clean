"""
QueryExecutor: executes analytics queries through ClickHouse, with an
explicit opt-in PostgreSQL fallback for legacy callers.

Usage:
    from clickhouse_utils.executor import QueryExecutor

    executor = QueryExecutor()
    result = executor.execute(sql, params)
    # result = {'columns': [...], 'rows': [...], 'row_count': N, 'error': None|str}

For cursor-compatible usage (matching Django's cursor.execute + fetchall):
    cursor = executor.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    # cursor.description → [(col_name,), ...]
"""

import logging
from decimal import Decimal
from django.conf import settings
from clickhouse_connect.driver.exceptions import DatabaseError

logger = logging.getLogger(__name__)

# Dataviz compiles wide queries (many joined columns, large IN-lists from
# multi-select filters) that can exceed ClickHouse's default max_query_size
# (256KiB, the parser buffer — not a data/row limit) and fail as a confusing
# "Syntax error" at the truncation point. Raised generously since it only
# affects parse-buffer size, not query performance.
_CH_QUERY_SETTINGS = {'max_query_size': 50_000_000}

# Tables mirrored into ClickHouse by `sync_to_clickhouse` (see
# clickhouse_utils/schema.py for the DDL). Keep in sync with both.
CLICKHOUSE_SYNCED_TABLES = frozenset({
    'current_student', 'student_enroll', 'results', 'tests',
    'results_with_tests', 'student_marks_list', 'nextyearlist_placement',
    'school', 'schedule_list', 'relative_year', 'mathr_recommendation',
    'student_attendance', 'student_incidents',
    'analytics_student_profile', 'analytics_student_results',
    'analytics_student_marks', 'analytics_ap_results', 'analytics_ap_awards',
    'analytics_survey_responses',
})

# Keys newly written to Postgres add_data can be missing from the ClickHouse
# mirror. JSONExtract on stale CH returns empty strings without error, so we
# keep those extracts on Postgres until CH is re-synced.
_PG_ADD_DATA_KEYS = ('dsusd_fep', 'DSUSD_FEP')


def _sql_uses_pg_add_data_key(sql: str) -> bool:
    text = sql or ''
    return any(k in text for k in _PG_ADD_DATA_KEYS)


def _sql_requires_postgres(sql: str) -> bool:
    """PostgreSQL-only idioms that must not go through the CH translator."""
    return 'jsonb_object_keys' in (sql or '').lower()


class QueryExecutor:
    """
    Executes a SQL query, routing to ClickHouse when available and enabled.

    Falls back to PostgreSQL on:
      - ClickHouse unavailable
      - USE_CLICKHOUSE_ANALYTICS=False
      - SQL translation failure (the query uses syntax with no ClickHouse
        equivalent — a capability gap, not a data-consistency concern)

    Does NOT fall back on a ClickHouse execution error (connection issue,
    missing table, timeout, etc.) — those propagate as errors so callers
    don't silently serve data from a different, possibly inconsistent
    source. See the "truncate ClickHouse tables before syncing" fix for
    the kind of divergence this is meant to prevent.

    All public methods return results in the same format as the existing
    PostgreSQL-based code for seamless drop-in compatibility.
    """

    def __init__(self, analytics=False):
        """Create an executor.

        ``analytics=True`` is used by interactive analytics reads.  Those
        requests must be served by the ClickHouse read model; silently
        switching to PostgreSQL makes latency unpredictable and can return a
        different snapshot while ClickHouse is being synchronized.  The
        default remains compatible for legacy/admin callers that intentionally
        support PostgreSQL fallback.
        """
        self.analytics = bool(analytics)
        self._ch_client = None
        self._pg_connection = None

    @property
    def use_clickhouse(self):
        return getattr(settings, 'USE_CLICKHOUSE_ANALYTICS', False)

    @property
    def ch_client(self):
        if self._ch_client is None and self.use_clickhouse:
            from .client import get_clickhouse_client
            self._ch_client = get_clickhouse_client()
        return self._ch_client

    @property
    def pg_connection(self):
        if self._pg_connection is None:
            from django.db import connection
            self._pg_connection = connection
        return self._pg_connection

    def execute(self, sql, params=None):
        """
        Execute a query. Returns:
            {'columns': [...], 'rows': [dict, ...], 'row_count': N, 'error': None|str}
        """
        from .timing import timed
        with timed('clickhouse_execute' if self.analytics else 'query_execute'):
            return self._execute(sql, params)

    def _execute(self, sql, params=None):
        if self.analytics and self.ch_client is None:
            return {'columns': [], 'rows': [], 'row_count': 0,
                    'error': 'ClickHouse analytics service is unavailable'}

        if self.ch_client is not None:
            from .translator import SQLTranslationError
            if _sql_requires_postgres(sql):
                return self._execute_postgres(sql, params)
            if _sql_uses_pg_add_data_key(sql):
                return self._execute_postgres(sql, params)
            try:
                return self._execute_clickhouse(sql, params)
            except SQLTranslationError as exc:
                if self.analytics:
                    return {'columns': [], 'rows': [], 'row_count': 0,
                            'error': f'Analytics query is not supported by ClickHouse: {exc}'}
                from .timing import current_request_id
                logger.warning(
                    "request_id=%s unexpected PostgreSQL fallback: SQL not translatable to ClickHouse: %s",
                    current_request_id(), exc,
                )
                return self._execute_postgres(sql, params)
            except Exception as exc:
                if self.analytics:
                    logger.exception("ClickHouse analytics query failed")
                    return {'columns': [], 'rows': [], 'row_count': 0,
                            'error': str(exc)}
                from .timing import current_request_id
                logger.warning(
                    "request_id=%s unexpected PostgreSQL fallback after ClickHouse error: %s",
                    current_request_id(), exc,
                )
                return self._execute_postgres(sql, params)

        return self._execute_postgres(sql, params)

    def execute_fetchall(self, sql, params=None):
        """
        Execute and return raw (columns, rows) tuple like PG cursor.fetchall().
        rows is a list of tuples (not dicts).
        """
        if self.analytics and self.ch_client is None:
            raise RuntimeError('ClickHouse analytics service is unavailable')

        if self.ch_client is not None:
            from .translator import SQLTranslationError
            if _sql_requires_postgres(sql):
                return self._execute_postgres_fetchall(sql, params)
            if _sql_uses_pg_add_data_key(sql):
                return self._execute_postgres_fetchall(sql, params)
            try:
                result = self._execute_clickhouse(sql, params)
                if result['error']:
                    raise Exception(result['error'])
                cols = result['columns']
                rows = [tuple(r.get(c) for c in cols) for r in result['rows']]
                return cols, rows
            except SQLTranslationError as exc:
                if self.analytics:
                    raise RuntimeError(f'Analytics query is not supported by ClickHouse: {exc}') from exc
                from .timing import current_request_id
                logger.warning(
                    "request_id=%s unexpected PostgreSQL fallback: SQL not translatable to ClickHouse",
                    current_request_id(),
                )
                return self._execute_postgres_fetchall(sql, params)
            except Exception as exc:
                if self.analytics:
                    raise
                from .timing import current_request_id
                logger.warning(
                    "request_id=%s unexpected PostgreSQL fallback after ClickHouse error: %s",
                    current_request_id(), exc,
                )
                return self._execute_postgres_fetchall(sql, params)

        return self._execute_postgres_fetchall(sql, params)

    def _execute_clickhouse(self, sql, params):
        if not sql:
            raise Exception("Empty or None SQL")

        from .translator import translate_pg_to_clickhouse, SQLTranslationError

        try:
            ch_sql, ch_params = translate_pg_to_clickhouse(sql, params or [])
        except SQLTranslationError:
            logger.debug("SQL translation not possible, will fall back to PG")
            raise

        result = self.ch_client.query(ch_sql, parameters=ch_params, settings=_CH_QUERY_SETTINGS)
        columns = result.column_names if result.column_names else []
        raw = result.result_set if result.result_set else []

        rows = []
        for row in raw:
            row_dict = dict(zip(columns, row))
            for k, v in list(row_dict.items()):
                if isinstance(v, Decimal):
                    row_dict[k] = float(v)
            rows.append(row_dict)

        return {
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'error': None,
        }

    def _execute_postgres(self, sql, params):
        from django.db import connection

        try:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN READ ONLY")
                try:
                    cursor.execute(sql, params or [])
                    if cursor.description is None:
                        return {'columns': [], 'rows': [], 'row_count': 0, 'error': None}
                    columns = [col[0] for col in cursor.description]
                    raw = cursor.fetchall()
                    rows = []
                    for row in raw:
                        row_dict = dict(zip(columns, row))
                        for k, v in list(row_dict.items()):
                            if isinstance(v, Decimal):
                                row_dict[k] = float(v)
                        rows.append(row_dict)
                finally:
                    cursor.execute("ROLLBACK")
        except Exception as exc:
            logger.exception("PostgreSQL query failed")
            return {'columns': [], 'rows': [], 'row_count': 0, 'error': str(exc)}

        return {
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'error': None,
        }

    def _execute_postgres_fetchall(self, sql, params):
        from django.db import connection

        try:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN READ ONLY")
                try:
                    cursor.execute(sql, params or [])
                    if cursor.description is None:
                        return [], []
                    columns = [col[0] for col in cursor.description]
                    rows = cursor.fetchall()
                finally:
                    cursor.execute("ROLLBACK")
        except Exception as exc:
            logger.exception("PostgreSQL fetchall failed")
            raise

        return columns, rows

    def cursor(self):
        """
        Return a wrapper that mimics Django's cursor interface
        with execute, fetchall, fetchmany, and description.
        """
        return _CursorWrapper(self)

    def pg_cursor(self):
        """
        Explicit PostgreSQL cursor (bypasses ClickHouse).
        """
        from django.db import connection

        class _PGCursorWrapper:
            def __enter__(self):
                self.cursor = connection.cursor()
                self.cursor.execute("BEGIN READ ONLY")
                return self

            def __exit__(self, *args):
                try:
                    self.cursor.execute("ROLLBACK")
                except Exception:
                    pass
                self.cursor.close()

            def execute(self, sql, params=None):
                self.cursor.execute(sql, params or [])

            def fetchall(self):
                return self.cursor.fetchall()

            def fetchmany(self, size):
                return self.cursor.fetchmany(size)

            @property
            def description(self):
                return self.cursor.description

        return _PGCursorWrapper()


class _CursorWrapper:
    """
    Mimics Django's database cursor with execute / fetchall / fetchmany / description.
    Analytics cursors propagate ClickHouse errors instead of falling back.
    Routes through QueryExecutor for ClickHouse support with PG fallback.
    """

    def __init__(self, executor):
        self.executor = executor
        self._description = None
        self._rows_raw = None
        self._result = None

    def execute(self, sql, params=None):
        self._result = self.executor.execute(sql, params)
        if self._result['error']:
            raise Exception(self._result['error'])

        self._rows_raw = []
        cols = self._result['columns']
        for row_dict in self._result['rows']:
            self._rows_raw.append(tuple(row_dict.get(c) for c in cols))

        self._description = [(c,) for c in cols]

    def fetchall(self):
        return self._rows_raw or []

    def fetchone(self):
        if self._rows_raw is None or not self._rows_raw:
            return None
        return self._rows_raw.pop(0)

    def fetchmany(self, size):
        if self._rows_raw is None:
            return []
        result = self._rows_raw[:size]
        self._rows_raw = self._rows_raw[size:]
        return result

    @property
    def description(self):
        return self._description

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
