import threading
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def get_clickhouse_client():
    global _client
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception:
            logger.warning("ClickHouse ping failed, reconnecting", exc_info=True)
            with _lock:
                _client = None

    with _lock:
        if _client is not None:
            try:
                _client.ping()
                return _client
            except Exception:
                pass

        try:
            from clickhouse_connect import get_client
        except ImportError:
            logger.error("clickhouse-connect not installed. Install with: pip install clickhouse-connect")
            return None

        try:
            _client = get_client(
                host=getattr(settings, 'CLICKHOUSE_HOST', 'localhost'),
                port=getattr(settings, 'CLICKHOUSE_PORT', 8123),
                username=getattr(settings, 'CLICKHOUSE_USER', 'clickhouse_user'),
                password=getattr(settings, 'CLICKHOUSE_PASSWORD', ''),
                database=getattr(settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db'),
            )
            _client.ping()
            logger.info("ClickHouse client connected successfully")
            return _client
        except Exception as exc:
            logger.warning("Cannot connect to ClickHouse: %s", exc)
            _client = None
            return None
