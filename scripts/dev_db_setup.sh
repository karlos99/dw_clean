#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${POSTGRES_DB:-dw_dev}"
DB_USER="${POSTGRES_USER:-dw_dev}"
DB_PASS="${POSTGRES_PASSWORD:-dw_dev}"
DB_HOST="${POSTGRES_HOST:-127.0.0.1}"
DB_PORT="${POSTGRES_PORT:-5432}"

if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" >/dev/null 2>&1; then
  if command -v pg_ctlcluster >/dev/null 2>&1; then
    sudo pg_ctlcluster 16 main start || true
    sleep 1
  fi
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  ELSE
    ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
\c ${DB_NAME}
GRANT ALL ON SCHEMA public TO ${DB_USER};
ALTER SCHEMA public OWNER TO ${DB_USER};
SQL

HBA="/etc/postgresql/16/main/pg_hba.conf"
if [ -f "$HBA" ] && ! sudo grep -q "dw_dev local smoke" "$HBA" 2>/dev/null; then
  echo "# dw_dev local smoke" | sudo tee -a "$HBA" >/dev/null
  echo "host    ${DB_NAME}    ${DB_USER}    127.0.0.1/32    scram-sha-256" | sudo tee -a "$HBA" >/dev/null
  echo "host    ${DB_NAME}    ${DB_USER}    ::1/128         scram-sha-256" | sudo tee -a "$HBA" >/dev/null
  sudo pg_ctlcluster 16 main reload || true
fi

PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$(dirname "$0")/dev_seed.sql"
echo "Dev DB ready: postgres://${DB_USER}:***@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo "Dev login email: dev@example.com"
