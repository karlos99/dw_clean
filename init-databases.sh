#!/bin/bash
set -e

echo "Restoring database from backup..."
pg_restore -v --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB" /backups/django_db_2026-02-12.dump || echo "Restore failed or finished with errors"
echo "Database restore completed."
