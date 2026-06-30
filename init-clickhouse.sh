#!/bin/bash
set -e

echo "Initializing ClickHouse analytics database..."

clickhouse-client --user "${CLICKHOUSE_USER}" --password "${CLICKHOUSE_PASSWORD}" --query "
CREATE DATABASE IF NOT EXISTS ${CLICKHOUSE_DB}
ENGINE = Atomic
"

echo "ClickHouse database '${CLICKHOUSE_DB}' ready."
