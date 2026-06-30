"""
Database router that keeps ClickHouse read-only for analytics.

- All migrations go to PostgreSQL only.
- ClickHouse is never used for writes or schema operations.
- Analytics queries use the QueryExecutor directly (raw SQL),
  not Django ORM, so model routing is minimal.
"""


class AnalyticsRouter:

    def db_for_read(self, model, **hints):
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'clickhouse_utils':
            return 'default'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == 'clickhouse':
            return False
        return None
