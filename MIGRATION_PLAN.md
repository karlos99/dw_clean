# Migration Plan: Django → Rust (Rocket) + React

**Source:** [karlos99/dw_clean](https://github.com/karlos99/dw_clean) (Django 6 · HTMX · PostgreSQL · ClickHouse · Redis)  
**Target:** Independent monorepo — Rocket JSON API backend + React SPA frontend  
**Non-negotiable:** Same PostgreSQL / ClickHouse / Redis data; no loss of business logic; keep login behavior; keep browser JS charting (ECharts / Gridstack), do not replace them with Rust UI libs.

---

## 0. Goals and constraints

| Goal | Meaning |
|------|---------|
| Independent repo | New project root; **not** a long-lived branch of `dw_clean`. Copy/port logic and share *schemas/contracts*, not git history as the product lineage. |
| Same database | Point `DATABASE_URL` / env at the existing Postgres (and ClickHouse/Redis). App tables (`users_*`, `dataviz_*`, `placementcards_*`, unmanaged `studata` views, etc.) stay byte-compatible. |
| Simplified API | Replace HTMX HTML partials with a small, consistent JSON API. Logic moves behind services; surface area shrinks, behavior does not. |
| Rust for performance | Hot paths: analytics SQL (ClickHouse + PG fallback), filter/column compilation, large student list queries, chart data aggregation, CSV export streaming. |
| Keep login | Google OAuth + email-linked pre-provisioned users + session cookie + active role / school / impersonation. |
| Keep JS where it belongs | ECharts, Gridstack, Three.js, Alpine-equivalent UI stay in React/TS. Rust returns **data** (series, columns, layouts), never substitutes chart engines. |
| Apps, one project | Structurally separate applications in one monorepo, sharing types/contracts and infra. |

### Explicit non-goals

- Rewriting Django admin as a full Rust admin in v1 (optional later; Project Admin pages in React cover day-to-day ops).
- Replacing ECharts/Gridstack with Rust WASM chart crates.
- Changing warehouse table shapes or ClickHouse sync semantics.
- Big-bang cutover without parity tests.

---

## 1. Target monorepo layout

Structurally separate apps; shared contracts; one deployable product.

```text
dw-platform/                    # NEW independent repo
├── apps/
│   ├── api/                    # Rocket (Rust) — JSON API only
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── main.rs
│   │       ├── auth/           # OAuth, session, guards
│   │       ├── db/             # Postgres + ClickHouse + Redis clients
│   │       ├── domain/         # Shared domain types / ACL
│   │       └── modules/        # One module per product app
│   │           ├── users/
│   │           ├── landing/
│   │           ├── exploration/
│   │           ├── nextyearlist/
│   │           ├── dataviz/
│   │           ├── placement/
│   │           ├── mathr/
│   │           ├── desi/
│   │           └── dataloader/
│   └── web/                    # React (Vite + TypeScript)
│       ├── package.json
│       └── src/
│           ├── app/            # Router, auth shell, layout
│           ├── features/       # Mirrors backend modules
│           └── shared/         # UI primitives, API client, hooks
├── packages/
│   ├── openapi/                # Single source of truth for HTTP contracts
│   ├── sql/                    # Reference DDL / view notes (read-only docs)
│   └── shared-types/           # Optional: generated TS types from OpenAPI
├── docker/
│   ├── docker-compose.yml      # api + web + (optional) local deps; or attach to existing PG/CH/Redis
│   └── nginx.conf
├── scripts/
│   ├── parity/                 # Golden request/response fixtures from Django
│   └── sync-check/             # Schema drift checks vs live DB
└── docs/
    ├── MIGRATION_PLAN.md       # this document (or root)
    └── API_MAP.md              # Django route → Rocket route matrix
```

**Why this shape**

- `apps/api` and `apps/web` look like separate applications (own manifests, own CI, own containers).
- `packages/openapi` is the shared resource: both sides generate/validate against it.
- Module folders under Rocket + React feature folders preserve today’s Django app boundaries (`dataviz`, `desi`, …) without a monolith blob.

---

## 2. Shared resources to extract from `dw_clean` (not “fork the app”)

Port these as **contracts and reference implementations**, then discard Django runtime coupling.

| Resource | Source locations | How the new repo uses them |
|----------|------------------|----------------------------|
| Postgres schema / table names | Django models + migrations under each app | SQLx/Diesel map to **existing** tables; no rename. Prefer `sqlx` offline + compile-time checks against a schema dump. |
| Unmanaged warehouse models | `studata/models.py` | Same views/tables (`current_student`, `schedule_list`, …). Document as read models. |
| ClickHouse sync set | `clickhouse_utils/schema.py`, `QueryExecutor` | Port executor: CH-first, PG fallback on translation gap only (same rules as today). |
| Page ACL | `users/models_pages.py`, `decorators.py`, `PAGE_MANAGEMENT.md` | Shared middleware/guards + `GET /api/nav` for React shell. |
| Role / school / teacher scope | `User.get_accessible_student_keys`, middleware | Domain crate: active role, school filter, teacher `stu_annual_key` list. |
| Google login linking | `users/adapters.py` | Same rules: match by email, update name/avatar, connect social identity. |
| Chart option builders | `dataviz/utils/*`, `templates/static/js/dataviz_*` | Rust builds **series + option JSON**; React keeps ECharts `setOption`. |
| Column structures / filters | `dataexploration/column_utils.py`, constants | Shared OpenAPI schemas + one Rust service reused by exploration, nextyearlist, dataviz. |
| Docker/env contract | `docker-compose.yml`, `.env` keys | Same hostnames: `postgres`, `redis`, `clickhouse`; same env names where possible. |

---

## 3. Stack choices (Rust + React)

### Backend (Rocket)

| Concern | Library | Notes |
|---------|---------|-------|
| HTTP | [Rocket](https://rocket.rs) 0.5 | JSON APIs, fairings, request guards |
| Postgres | `sqlx` (preferred) or `diesel` | `sqlx` fits existing schema + raw analytics SQL |
| ClickHouse | `clickhouse` crate or HTTP via `reqwest` | Mirror `clickhouse-connect` usage; keep `max_query_size` setting |
| Redis | `redis` + `bb8-redis` / `deadpool-redis` | Cache keys compatible where TTLs matter for cutover |
| Auth / OAuth | `oauth2` + Google endpoints; Rocket private cookies or Redis-backed sessions | Emulate allauth email-link behavior |
| Passwords / Django hashes | `password-hash` / `django-hashers` equivalent only if password login kept | Primary path is Google; still read `users_user` rows |
| Serialization | `serde` / `serde_json` | OpenAPI-aligned DTOs |
| CSV / PDF | `csv` + stream; PDF via `printpdf`/`genpdf` **or** keep ReportLab-equivalent in a thin worker later | Placement/DESI print paths |
| Config | `figment` (Rocket native) | Map `.env` from current compose |
| Tracing | `tracing` + `tracing-subscriber` | Replace Django logging |

**Do not pull in** Rust charting/UI crates as replacements for ECharts/Gridstack. Optional: `polars` later for heavy in-process transforms if SQL cannot express them — only where Django today uses pandas.

### Frontend (React)

| Concern | Choice |
|---------|--------|
| Scaffold | Vite + React + TypeScript |
| Data | TanStack Query + generated OpenAPI client |
| Routing | React Router (paths aligned with current `Page.url_path` where possible) |
| Charts / layout | **ECharts** + **Gridstack** (port existing JS modules; do not rewrite in Rust) |
| Styling | Keep Tailwind; drop HTMX/Alpine in favor of React state |
| Auth UX | Redirect to `/api/auth/google`; cookie credentials `include` |

### Infra

- Nginx serves `apps/web` static build; proxies `/api/*` (and OAuth callbacks) to Rocket.
- Compose can run `api` + `web` against **existing** `postgres` / `redis` / `clickhouse` network, or full local stack.
- No Django, Gunicorn, or Whitenoise in the target deploy.

---

## 4. Auth and session (keep the logic)

Preserve behavior from `users` + allauth:

1. **Google OAuth** with email + profile scopes (PKCE ok).
2. **Pre-provisioned users only (preferred production rule):** if email matches `users_user.email`, attach/login; update `name` / `avatar` from Google — same as `MySocialAccountAdapter`.
3. **Session cookie** (HttpOnly, Secure, SameSite) — not JWT for browser SPA in v1 (matches current model; simpler CSRF story with same-site cookie + double-submit or Rocket fairing).
4. **Active role** + **active school** in session (`switch-role` equivalent).
5. **Impersonation** for Project Admin.
6. **Page ACL** from `users_page` (+ roles / allowed users / public / draft).
7. **Teacher student scope** via `schedule_list` + `staff_id`.
8. **Usage tracking** (`UsageSession` / `PageVisit`) — port after core auth is stable.

### Suggested auth endpoints (simplified, same behavior)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/auth/google/start` | Begin OAuth |
| GET | `/api/auth/google/callback` | Finish OAuth; set session |
| POST | `/api/auth/logout` | Clear session |
| GET | `/api/auth/me` | User, roles, schools, active role/school, impersonation |
| POST | `/api/auth/switch-role` | Active role + school |
| POST | `/api/auth/impersonate/{id}` | Start impersonation |
| POST | `/api/auth/stop-impersonate` | End impersonation |
| GET | `/api/nav` | Menu tree from `Page` rows |

Django’s `/accounts/*` HTML flows are replaced by these JSON/redirect endpoints; **login policy and linking rules stay**.

**DB note:** Prefer reading/writing the existing `users_*` (and optionally `socialaccount_*` / `account_*`) tables so flipping the app pointer does not require a user migration. Document any columns Rocket will ignore (Django-only).

---

## 5. API design principles (simplify without losing function)

1. **JSON only** from Rocket (plus file downloads for CSV/PDF).
2. **One resource model per domain concept** — e.g. student search returns `{ columns, rows, total, meta }` instead of HTML table partials.
3. **Collapse HTMX modal endpoints** into CRUD + one `GET …/form-options` where needed.
4. **Keep expensive query semantics** — filters, school-year, grade, status filters, column structures, CH routing rules.
5. **Version lightly** — `/api/v1/...` from day one so Django and Rocket can dual-run behind Nginx during cutover.
6. **OpenAPI first** for each module before UI port of that module.

### Example: exploration today vs target

| Today (Django) | Target (Rocket) |
|----------------|-----------------|
| `load_students` → HTMX HTML or `?format=json` | `POST /api/v1/exploration/students/query` → JSON only |
| Folder tree partials | `GET/POST/PATCH/DELETE /api/v1/exploration/folders` |
| Save query modal HTML | `POST /api/v1/exploration/queries` |
| Stats partial | `POST /api/v1/exploration/stats` |

Same services (column utils, access keys, Redis cache keys conceptually), thinner transport.

---

## 6. Multi-phase plan

Phases are ordered by dependency and risk. Each phase ends with: OpenAPI updated, parity tests green, Docker runnable against the shared DB.

### Phase 0 — Bootstrap independent repo & inventory freeze

**Deliverables**

- Create new empty GitHub repo (not a fork).
- Scaffold `apps/api` (Rocket hello + health), `apps/web` (Vite React), compose + Nginx skeleton.
- Export schema inventory: table list, unmanaged views, ClickHouse DDL list, Redis key prefixes.
- Produce `docs/API_MAP.md`: every Django URL → target Rocket route or “UI-only”.
- Capture golden fixtures from running Django for auth, one exploration query, one dataviz chart render payload.

**Exit criteria:** New repo builds; health checks; documented route map; fixture corpus started.

---

### Phase 1 — Core platform: DB clients, auth, ACL, shell

**Backend**

- Postgres pool (`sqlx`), Redis, ClickHouse client stub.
- Google OAuth + session + `/api/auth/*` + `/api/nav`.
- Request guards: `AuthenticatedUser`, `ActiveRole`, `PageAccess(slug)`.
- Port `get_accessible_student_keys` and school/grade scoping.

**Frontend**

- Auth gate, app shell, dynamic nav from `/api/nav`.
- Role/school switcher; impersonation UI for Project Admin.
- Landing home (read-only dashboard data via API).

**Exit criteria:** Point API at prod-like DB; Google login works for pre-provisioned users; nav matches Page ACL; no Django required for shell.

---

### Phase 2 — Analytics engine (shared performance core)

Port `clickhouse_utils` as the shared Rust module used by all read-heavy apps:

- SQL translator / dialect differences (as needed for current queries).
- `QueryExecutor`: CH when enabled; PG fallback **only** on translation gap; no silent fallback on CH execution errors (preserve current safety model).
- Redis caching helpers with deterministic keys.
- Column structure + filter-option services (shared by exploration / nextyearlist / dataviz).

**Exit criteria:** Benchmarked parity on representative student/tests queries; executor unit/integration tests.

---

### Phase 3 — Data Exploration + Next Year List

Highest shared logic; both apps are near-clones today.

**API modules:** `exploration`, `nextyearlist`  
**Port:** student query, columns, stats, CSV export, folders, saved queries, lists, shares, student summary.

**Frontend features:** table explorer, column picker, folder browser, save/load query — React ports of `student_tests_app.js` patterns without HTMX.

**Exit criteria:** Side-by-side parity suite (row counts, filter results, exports) against Django on same DB.

---

### Phase 4 — Dataviz (largest JSON surface; best Rust win)

**API:** charts, datasets, pages/rows/sections, drill-through, colormap presets, SQL lab, schema, cache clear — align with existing `dataviz/urls.py` API group, simplified naming under `/api/v1/dataviz/...`.

**Keep in JS/React:** ECharts renderer, Gridstack page builder, SQL lab editor UX.  
**Move to Rust:** query compile, data load, filter resolution, batch render **data**, CSV export.

**Exit criteria:** Existing saved charts/pages render identically (option JSON + data); builder save/load works.

---

### Phase 5 — Placement Cards, MathR, DESI

These are HTMX-heavy today; convert to JSON + React forms.

| Module | Preserve |
|--------|----------|
| Placement | Cards CRUD, classrooms, tags/questions, placement assign, print/PDF/CSV, access helpers |
| MathR | 8th-grade recommendations, period filters, completion, Redis mark cache |
| DESI | Accommodations, assessments, assign flows, export configuration |

**Exit criteria:** Teacher/admin workflows pass manual + automated API parity; print outputs match.

---

### Phase 6 — DataLoader + Project Admin surfaces

- Survey/AP upload, preview, import status (JSON progress via Redis as today).
- User manage / bulk update / usage report.
- Page management CRUD (replaces Django templates in `users/page_views`).
- `ProjectConfig` settings.

**Exit criteria:** Project Admin can operate without Django admin for daily tasks; imports complete with same DB side effects.

---

### Phase 7 — Dual-run cutover & hardening

1. Nginx: route `/api/v1/` → Rocket; keep Django for unmigrated paths if needed (temporary).
2. Flip SPA to Rocket-only when Phase 3–6 complete.
3. Load tests on exploration + dataviz batch render.
4. Security pass: CSRF/CORS, cookie flags, OAuth redirect allowlist, SQL lab sandboxing (same restrictions as Django).
5. Remove Django service from compose; archive `dw_clean` as reference only.

**Exit criteria:** Single stack (Rocket + React + NGINX + PG + CH + Redis) serves full product; Django offline.

---

### Phase 8 — Cleanup & ownership model

- Delete unused stub (`assesments` equivalent).
- CI: `cargo test`, `cargo clippy`, frontend lint/test, OpenAPI drift check, schema sync-check.
- Runbooks: env vars, OAuth consoles, CH sync job (port `sync_to_clickhouse` to a Rust binary or keep a small scheduled job).
- Performance budget doc: p95 for student query / chart batch render.

---

## 7. Database compatibility rules

1. **No table renames** in v1. Rocket maps to Django’s physical names (`users_user`, `dataviz_savedchart`, …).
2. **Migrations policy:** Prefer “DB is owned by existing schema.” If new columns are required, add forward-compatible migrations in a controlled tool (e.g. `sqlx migrate`) **only** with explicit approval; default is zero schema change.
3. **Unmanaged / ETL tables** stay read-mostly; writes only where Django already writes.
4. **ClickHouse** remains analytics mirror; sync job remains the write path into CH.
5. **Pointing the DB:** same env as today:

```text
POSTGRES_HOST / POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_PORT
CLICKHOUSE_* / USE_CLICKHOUSE_ANALYTICS
REDIS_URL (or host/port)
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
```

---

## 8. Parity & testing strategy

| Layer | Approach |
|-------|----------|
| Contract | OpenAPI + schemathesis / generated client types |
| Golden | Record Django JSON (or scraped DTOs) → assert Rocket equality |
| SQL | Snapshot compiled SQL for dataviz/exploration compilers |
| Auth | Fixture users with roles/schools/teacher staff_id |
| UI | Playwright on critical paths (login, explorer filter, chart view, placement save) |
| Perf | Criterion/bench on QueryExecutor; compare wall time to Django baselines |

---

## 9. Risk register

| Risk | Mitigation |
|------|------------|
| Django session/allauth table impedance | Own Rocket sessions; only require `users_user` (+ social link table if kept); document cutover “all users re-login once” |
| Subtle ACL bugs (teacher keys, grade, school) | Port ACL as first shared module; property tests against Django helper outputs |
| SQL dialect / CH translator gaps | Keep PG fallback rules identical; log translation misses |
| HTMX → React scope creep | API-first per module; UI can lag API by one phase |
| PDF/print fidelity | Freeze print templates; binary diff samples |
| Dual-write confusion during cutover | Single writer per domain during dual-run; feature flags by module |

---

## 10. Suggested first implementation slice (after plan approval)

Smallest vertical that proves the architecture:

1. New repo scaffold (Phase 0).
2. Rocket `GET /api/health` + `sqlx` select against existing `users_user`.
3. Google OAuth + `/api/auth/me`.
4. React shell that logs in and shows nav from `Page` rows.
5. One read-only exploration endpoint using QueryExecutor + ECharts-free table in React.

That slice validates: independent repo, same DB, login kept, separate apps, Rust on the query path, JS UI unchanged in role.

---

## 11. Phase checklist (summary)

| Phase | Focus | Primary apps |
|------:|-------|----------------|
| 0 | New monorepo, API map, fixtures | — |
| 1 | Auth, ACL, shell | users, landing |
| 2 | CH/PG executor, cache, columns | clickhouse_utils → `db`/`domain` |
| 3 | Student explorers | dataexploration, nextyearlist |
| 4 | Charts/pages/datasets | dataviz (+ keep ECharts/Gridstack in React) |
| 5 | Workflow UIs | placementcards, mathr, desi |
| 6 | Admin/import | dataLoader, users admin, ProjectConfig |
| 7 | Cutover, Nginx, load/security | infra |
| 8 | CI, sync job, docs | platform |

---

## 12. Success definition

The migration is done when:

1. The product lives in an **independent** monorepo with `apps/api` (Rocket) and `apps/web` (React).
2. Env points at the **same** Postgres/ClickHouse/Redis and all prior workflows work.
3. Google login + role/school/impersonation/page ACL behave as today.
4. APIs are simplified JSON, but domain rules (filters, CH routing, teacher scope, imports, prints) are preserved.
5. Charting/layout still use the **JavaScript** libraries; Rust accelerates data and query paths only.
