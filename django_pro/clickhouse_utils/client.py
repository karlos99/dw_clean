import threading
import logging
import time
from django.conf import settings

logger = logging.getLogger(__name__)

# clickhouse-connect's Client is not safe for concurrent queries from multiple
# threads on the same instance — sharing one globally raises "Attempt to
# execute concurrent queries within the same session". Each thread gets and
# reuses its own connection instead: this keeps the performance benefit of
# connection reuse (no repeat handshake per request) while staying
# concurrency-safe under Django's threaded dev server / thread-pooled workers.
_local = threading.local()
_PING_INTERVAL_SECONDS = 30.0


def get_clickhouse_client():
    client = getattr(_local, 'client', None)
    if client is not None:
        last_ping = getattr(_local, 'last_ping', 0.0)
        if time.monotonic() - last_ping < _PING_INTERVAL_SECONDS:
            return client
        try:
            client.ping()
            _local.last_ping = time.monotonic()
            return client
        except Exception:
            logger.warning("ClickHouse ping failed, reconnecting", exc_info=True)
            _local.client = None
            _local.last_ping = 0.0

    try:
        from clickhouse_connect import get_client
    except ImportError:
        logger.error("clickhouse-connect not installed. Install with: pip install clickhouse-connect")
        return None

    try:
        client = get_client(
            host=getattr(settings, 'CLICKHOUSE_HOST', 'localhost'),
            port=getattr(settings, 'CLICKHOUSE_PORT', 8123),
            username=getattr(settings, 'CLICKHOUSE_USER', 'clickhouse_user'),
            password=getattr(settings, 'CLICKHOUSE_PASSWORD', ''),
            database=getattr(settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db'),
            compress=True,
            connect_timeout=10,
            send_receive_timeout=60,
        )
        client.ping()
        logger.info("ClickHouse client connected successfully (thread %s)", threading.get_ident())
        _local.client = client
        _local.last_ping = time.monotonic()
        return client
    except Exception as exc:
        logger.warning("Cannot connect to ClickHouse: %s", exc)
        _local.client = None
        _local.last_ping = 0.0
        return None


def get_analytics_sync_version() -> str:
    """Return composite analytics cache version (ClickHouse sync + DataLoader imports)."""
    try:
        from django.core.cache import cache

        vals = cache.get_many([
            'analytics:clickhouse_sync_version',
            'analytics:dataloader_version',
        ])
        ch_v = vals.get('analytics:clickhouse_sync_version') or 'legacy'
        dl_v = vals.get('analytics:dataloader_version')
        return f'{ch_v}:dl{dl_v}' if dl_v else ch_v
    except Exception:
        return 'legacy'


def bump_dataloader_sync_version() -> str:
    """Advance DataLoader cache version so long-lived chart/filter caches invalidate on publish."""
    version = str(time.time_ns())
    try:
        from django.core.cache import cache

        cache.set('analytics:dataloader_version', version, None)
    except Exception:
        logger.debug("Could not bump analytics:dataloader_version in Redis", exc_info=True)
    return version
