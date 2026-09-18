import threading
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# clickhouse-connect's Client is not safe for concurrent queries from multiple
# threads on the same instance — sharing one globally raises "Attempt to
# execute concurrent queries within the same session". Each thread gets and
# reuses its own connection instead: this keeps the performance benefit of
# connection reuse (no repeat handshake per request) while staying
# concurrency-safe under Django's threaded dev server / thread-pooled workers.
_local = threading.local()


def get_clickhouse_client():
    client = getattr(_local, 'client', None)
    if client is not None:
        try:
            client.ping()
            return client
        except Exception:
            logger.warning("ClickHouse ping failed, reconnecting", exc_info=True)
            _local.client = None

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
        return client
    except Exception as exc:
        logger.warning("Cannot connect to ClickHouse: %s", exc)
        _local.client = None
        return None
