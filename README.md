# DW Platform (Axum + React)

Independent rewrite of [dw_clean](https://github.com/karlos99/dw_clean): **Rust Axum API** + **React SPA**, pointed at the **same** PostgreSQL / ClickHouse / Redis.

Django under `django_pro/` remains the reference implementation during migration.

## Layout

```text
apps/api   # Axum JSON API (auth, nav, exploration slice)
apps/web   # React 19 + TanStack Router/Query/Table
docs/      # MIGRATION_PLAN.md
django_pro # Legacy Django (reference)
```

## Quick start (local)

### A) Smoke against a local seed DB (no prod secrets)

```bash
# requires local PostgreSQL 16
bash scripts/dev_db_setup.sh
cp .env.example .env   # defaults match scripts/dev_db_setup.sh (dw_dev / dw_dev)
cd apps/api && cargo run
# other terminal:
cd apps/web && npm install && npm run dev
```

Dev login: `dev@example.com` (button on the login page).

### B) Point at your existing dw_clean Postgres

1. Copy `.env.example` → `.env` and set `POSTGRES_*` or `DATABASE_URL`.
2. API: `cd apps/api && cargo run`
3. Web: `cd apps/web && npm install && npm run dev`

Open http://localhost:5173 — Vite proxies `/api` → `:8080`.

### Auth

- **Google:** set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`; callback `{API_PUBLIC_URL}/api/auth/google/callback`.
- **Dev login:** `ALLOW_DEV_LOGIN=true` and `POST /api/auth/dev-login` with a pre-provisioned `users_user.email`.

### First vertical slice

| Endpoint | Purpose |
|----------|---------|
| `GET /api/health` | DB ping |
| `GET /api/auth/google/start` | OAuth |
| `GET /api/auth/me` | Session user + roles |
| `GET /api/nav` | Active menu pages (`users_page`) |
| `POST /api/v1/exploration/students/query` | Simplified `current_student` query |

## Docker

With the legacy compose network already up (`postgres` healthy):

```bash
docker compose -f docker-compose.platform.yml up --build
```

## Plan

See [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md).
