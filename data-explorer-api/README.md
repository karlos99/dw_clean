# Data Explorer API

Standalone **Rust / Rocket** service that reads student analytics from **ClickHouse** and exposes a JSON API compatible with the Data Explorer grid.

This project is intentionally independent: it has its own dependencies, Docker setup, and configuration. It does not require Django, PostgreSQL, or Redis.

## Features

- `GET /health` — service and ClickHouse connectivity check
- `GET /api/students` — student grid JSON (same shape as Django `load_students?format=json`)
  - Static `current_student` columns
  - Dynamic `test_*` columns (from `results` + `tests`)
  - Dynamic `mark_*` columns (from `student_marks_list`)
  - Pagination, search, sorting, basic column filters

## Requirements

- Rust 1.75+ (for local development), **or** Docker
- ClickHouse 24+ with synced tables:
  - `current_student`
  - `results`
  - `tests`
  - `student_marks_list`
  - (optional) `relative_year`, `school`, etc.

## Quick start (Docker)

```bash
cd data-explorer-api
cp .env.example .env
# Edit .env if needed

docker compose up --build
```

Test:

```bash
curl http://localhost:8080/health
curl "http://localhost:8080/api/students?school_year=2025-2026&columns=%5B%22student_name%22%2C%22ssid%22%2C%22grade%22%5D"
```

## Quick start (local Rust)

```bash
cd data-explorer-api
cp .env.example .env

# Point CLICKHOUSE_URL at your ClickHouse instance
export $(grep -v '^#' .env | xargs)

cargo run
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CLICKHOUSE_URL` | ClickHouse HTTP URL | required |
| `CLICKHOUSE_DB` | Database name | required |
| `CLICKHOUSE_USER` | Username | `default` |
| `CLICKHOUSE_PASSWORD` | Password | empty |
| `HOST` | Bind address | `0.0.0.0` |
| `PORT` | HTTP port | `8080` |
| `DEFAULT_SCHOOL_YEAR` | Default filter | `2025-2026` |
| `DEFAULT_STATUS` | Default status filter | `Active` |
| `DEFAULT_PAGE_LIMIT` | Page size | `50` |
| `MAX_PAGE_LIMIT` | Max page size | `500` |

## API

### `GET /api/students`

Query parameters:

| Param | Example |
|-------|---------|
| `page` | `1` |
| `limit` | `50` |
| `columns` | `["student_name","ssid","grade"]` |
| `filters` | `{"gender":["M"]}` |
| `search` | `smith` |
| `search_ids` | `["1234567890"]` |
| `school_year` | `2025-2026` |
| `grade` | `05` |
| `status` | `Active` |
| `sort_by` | `student_name` |
| `sort_dir` | `asc` |

Example:

```bash
curl -G "http://localhost:8080/api/students" \
  --data-urlencode 'school_year=2025-2026' \
  --data-urlencode 'columns=["student_name","ssid","grade","gender"]' \
  --data-urlencode 'filters={"gender":["M","F"]}' \
  --data-urlencode 'sort_by=student_name' \
  --data-urlencode 'sort_dir=asc'
```

Response fields match the Django Data Explorer JSON payload:

```json
{
  "students": [],
  "selected_columns": [],
  "total": 0,
  "page": 1,
  "total_pages": 0,
  "column_definitions": {},
  "filter_options": {},
  "filters": {},
  "search": "",
  "school_year": "2025-2026",
  "grade": "",
  "status": "Active",
  "sort_by": "",
  "sort_dir": "asc",
  "survey_colors": {}
}
```

## Connect to an existing ClickHouse instance

If ClickHouse already runs elsewhere (for example from another project on port 8123), use a minimal compose file or run the binary directly:

```env
CLICKHOUSE_URL=http://host.docker.internal:8123
CLICKHOUSE_DB=fastapi_clickhouse_db
CLICKHOUSE_USER=clickhouse_user
CLICKHOUSE_PASSWORD=your_password
```

Run only the API container:

```bash
docker build -t data-explorer-api .
docker run --rm -p 8080:8080 --env-file .env data-explorer-api
```

## Project layout

```
data-explorer-api/
├── Cargo.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── src/
    ├── main.rs
    ├── config.rs
    ├── error.rs
    ├── models.rs
    ├── columns.rs
    ├── clickhouse/mod.rs
    ├── query/
    │   ├── builder.rs
    │   ├── test_columns.rs
    │   └── mark_columns.rs
    └── routes/
        └── students.rs
```

## Notes

- `survey_*` columns are not available (survey data is not stored in ClickHouse).
- `saved_query` / role-based access control are not implemented in this standalone service.
- For production, put the API behind your reverse proxy and add authentication as needed.
