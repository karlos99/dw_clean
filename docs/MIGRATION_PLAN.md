# Migration Plan: Django → Rust (Axum) + React

**Source:** [karlos99/dw_clean](https://github.com/karlos99/dw_clean) (Django 6 · HTMX · PostgreSQL · ClickHouse · Redis)  
**Target:** Independent monorepo — **Axum** JSON API backend + **React 19** SPA frontend  
**Non-negotiable:** Same PostgreSQL / ClickHouse / Redis data; no loss of business logic; keep login behavior; keep browser chart/layout engines that already define product output (ECharts / Gridstack).

> **Framework note:** Rocket was considered for ergonomics. For this size of API (streaming CSV, Redis sessions, tower middleware, ClickHouse async, OpenAPI), **Axum + Tower** is the stronger 2026 default and is the plan baseline. Behavior and routes stay the same either way; only the HTTP framework changes.

---

## 0. Goals and constraints

| Goal | Meaning |
|------|---------|
| Independent repo | New project root; **not** a long-lived branch of `dw_clean`. Copy/port logic and share *schemas/contracts*, not git history as the product lineage. |
| Same database | Point env at the existing Postgres (and ClickHouse/Redis). App tables stay byte-compatible. |
| Simplified API | Replace HTMX HTML partials with a small, consistent JSON API. Logic moves behind services; surface area shrinks, behavior does not. |
| Rust for performance | Hot paths: analytics SQL (ClickHouse + PG fallback), filter/column compilation, large student list queries, chart data aggregation, CSV export streaming. |
| Keep login | Google OAuth + email-linked pre-provisioned users + session cookie + active role / school / impersonation. |
| Better libs, same function | Prefer modern Rust/React libraries **only when** they can reproduce today’s behavior (ACL, chart option JSON, grid layouts, exports). |
| Keep JS where it belongs | ECharts, Gridstack, Three.js stay in React/TS. Rust returns **data** (series, columns, layouts), never substitutes chart engines. |
| Apps, one project | Structurally separate applications in one monorepo, sharing types/contracts and infra. |

### Explicit non-goals

- Rewriting Django admin as a full Rust admin in v1 (Project Admin pages in React cover day-to-day ops).
- Replacing ECharts/Gridstack with Recharts/Nivo/Rust WASM (would break saved chart option JSON and page layouts).
- Changing warehouse table shapes or ClickHouse sync semantics.
- Big-bang cutover without parity tests.

### Library selection rule

Upgrade a library when it improves DX, type safety, or performance **and** can still:

1. Talk to the same DB tables / Redis keys / CH tables  
2. Emit the same chart `option` + series payloads ECharts already consumes  
3. Preserve Gridstack layout coordinates already stored on pages  
4. Preserve Google email-link login + role/school/impersonation + page ACL  

If a “nicer” library would force a product behavior change, **do not use it**.

---

## 1. Target monorepo layout

```text
dw-platform/                    # NEW independent repo
├── apps/
│   ├── api/                    # Axum (Rust) — JSON API only
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── main.rs
│   │       ├── auth/           # OAuth, sessions, extractors
│   │       ├── db/             # Postgres + ClickHouse + Redis
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
│   └── web/                    # React 19 (Vite + TypeScript)
│       ├── package.json
│       └── src/
│           ├── app/            # TanStack Router, auth shell, layout
│           ├── features/       # Mirrors backend modules
│           └── shared/         # shadcn/ui, API client, hooks
├── packages/
│   ├── openapi/                # Spec generated from utoipa (or checked-in)
│   ├── sql/                    # Reference DDL / view notes
│   └── api-client/             # Generated TS client (openapi-typescript / Orval)
├── docker/
│   ├── docker-compose.yml
│   └── nginx.conf
├── scripts/
│   ├── parity/
│   └── sync-check/
└── docs/
    ├── MIGRATION_PLAN.md
    └── API_MAP.md
```

**Why this shape**

- `apps/api` and `apps/web` are separate applications (own manifests, CI, containers).
- OpenAPI is the shared contract; TS types are generated, not hand-duplicated.
- Module folders preserve today’s Django app boundaries without a monolith blob.

---

## 2. Shared resources to extract from `dw_clean`

| Resource | Source | How the new repo uses them |
|----------|--------|----------------------------|
| Postgres schema / table names | Django models + migrations | `sqlx` maps to **existing** tables; compile-time checks against a schema dump |
| Unmanaged warehouse models | `studata/models.py` | Same views/tables; documented as read models |
| ClickHouse sync set | `clickhouse_utils/*` | Port `QueryExecutor` rules exactly (CH-first; PG fallback only on translation gap) |
| Page ACL | `users/models_pages.py`, decorators | Axum extractors/middleware + `GET /api/nav` |
| Role / school / teacher scope | `User.get_accessible_student_keys` | Shared domain module |
| Google login linking | `users/adapters.py` | Same email match + name/avatar update |
| Chart option builders | `dataviz/utils/*`, `dataviz_*` JS | Rust builds **series + option JSON**; React `echarts.setOption` |
| Column structures / filters | exploration column utils | One Rust service reused by exploration, nextyearlist, dataviz |
| Docker/env contract | compose + `.env` | Same hostnames/env names where possible |

---

## 3. Recommended stack (better libraries, same function)

### 3.1 Backend — Rust

| Concern | Choose | Why (vs alternatives) | Parity impact |
|---------|--------|------------------------|---------------|
| HTTP | **[Axum](https://github.com/tokio-rs/axum)** + `tower-http` | Best Tokio/Tower ecosystem fit for sessions, compression, CORS, tracing, timeouts; better long-term than Rocket fairings for this API size | None — same routes/JSON |
| Runtime | **Tokio** | De facto async runtime for sqlx / clickhouse / redis | None |
| Postgres | **`sqlx`** (tokio + postgres) | Raw SQL + offline checking against real schema; better fit than Diesel for warehouse SQL and existing table names | Same SQL semantics |
| ClickHouse | **`clickhouse`** (official async crate) | Native async queries; keep `max_query_size` and CH→PG fallback policy | Same executor rules |
| Redis | **`fred`** or `redis` + **`bb8-redis`** | Connection pooling; sessions + cache. Prefer one client for both | Compatible key/TTL strategy |
| Sessions | **`tower-sessions`** + Redis store | Cookie sessions without inventing a store; replaces Django session for the SPA | Users re-login once at cutover |
| OAuth | **`oauth2`** + Google auth code + PKCE | Standard; implement allauth email-link rules in our adapter code | Same login policy |
| Cookies / CSRF | `axum-extra` cookies + double-submit or synchronizer token | SPA-safe; Secure / HttpOnly / SameSite | Same security posture |
| OpenAPI | **`utoipa`** (+ swagger UI in dev) | Spec generated from Rust types → feeds TS client | Contract source of truth |
| Validation | **`serde`** + **`validator`** / typed newtypes | Reject bad payloads early | Same business rules in handlers |
| Errors | **`thiserror`** (domain) + **`anyhow`** (boundaries) | Clear API error mapping | Stable error JSON shape |
| Observability | **`tracing`** + `tracing-subscriber` (+ OTEL later) | Tower-native; replaces Django logging | Ops only |
| Config | **`figment`** or `dotenvy` + typed `Settings` | Map existing `.env` keys | Same deploy config |
| CSV streaming | **`csv`** + Axum body stream | Large exports without buffering whole file | Same CSV columns/rows |
| PDF / print | **`printpdf`** / `lopdf` **or** headless HTML→PDF only if pixel parity fails | Placement/DESI prints must match samples | Binary parity tests gate the choice |
| In-process cache | **`moka`** (optional) beside Redis | Hot column-structure metadata | Must not diverge from Redis invalidation rules |
| Heavy transforms | **`polars`** only where Django uses pandas today | Don’t add for speculative ETL | Same outputs as current pandas paths |
| Rate limit | **`tower-governor`** (optional, edge) | Protect SQL lab / export | Configurable; off by default in parity mode |
| Authz extractors | Custom Axum extractors (`AuthUser`, `PageAccess`) | Replaces `@page_access_required` / middleware | Same ACL matrix |

**Rejected for this project (and why)**

| Library | Rejected because |
|---------|------------------|
| Rocket as default | Smaller Tower ecosystem; weaker fit for Redis sessions + streaming at this scale (acceptable only if team hard-requires macros) |
| Diesel as primary ORM | Migrations/ownership model fights “DB already exists”; awkward for dynamic analytics SQL |
| JWT-only browser auth | Changes security model vs current cookie sessions; use cookies for SPA v1 |
| Rust chart crates / WASM UI | Would replace ECharts/Gridstack and break saved artifacts |

### 3.2 Frontend — React

| Concern | Choose | Why (vs alternatives) | Parity impact |
|---------|--------|------------------------|---------------|
| Scaffold | **Vite** + **React 19** + TypeScript | Fast HMR; SPA behind Nginx talking to Axum | None |
| Routing | **TanStack Router** | Typed routes + **typed search params** (critical for explorer filters/URLs) | Paths can still match `Page.url_path` |
| Server state | **TanStack Query** | Cache/mutations for API; replaces ad-hoc HTMX refresh | Same endpoints |
| Tables | **TanStack Table** | Headless; rebuild exploration/placement grids without losing sort/filter/column features | Render same columns/rows from API |
| Forms | **TanStack Form** + **Zod** | Complex DESI/placement/admin forms with shared schemas | Same validation rules (mirrored from API) |
| API client | **openapi-typescript** + `openapi-fetch` (or **Orval**) | Types from `utoipa` spec; no hand-written DTO drift | Contract parity |
| Charts | **Apache ECharts** via `echarts` + `echarts-for-react` | **Required for parity** — existing saved chart option JSON targets ECharts | Identical `setOption` payloads |
| Page layout | **Gridstack** (`gridstack` + React wrapper) | **Required for parity** — stored grid coordinates / page builder | Same page layouts |
| 3D / landing | **Three.js** (only where landing already uses it) | Keep existing landing motion | Same pages |
| UI kit | **Tailwind CSS v4** + **shadcn/ui** (Radix) | Accessible primitives; not a redesign mandate — restyle to match current product look | Visual polish only; workflows unchanged |
| Client UI state | **Zustand** (shell only: sidebar, role switcher UI) | Thin; server truth stays in Query | None |
| Code editor (SQL lab) | **CodeMirror 6** | Better maintained than legacy editors for SQL lab | Same SQL lab behavior |
| E2E | **Playwright** | Login, explorer, chart view, placement save | Gates cutover |
| Unit / component | **Vitest** + Testing Library | Feature-level UI tests | — |

**Rejected for this project (and why)**

| Library | Rejected because |
|---------|------------------|
| Recharts / Nivo / Chart.js as primary | Cannot consume existing ECharts option JSON; visual/feature regression risk |
| React Router as primary | Weaker typed search-param story for filter-heavy explorers |
| Next.js / TanStack Start as required host | Extra SSR surface; product is an authenticated SPA + separate API (Vite SPA is enough). Can revisit later without changing Axum |
| HTMX / Alpine in the new app | Replaced by React state + Query |
| Material UI as default kit | Heavier; shadcn/Radix fits Tailwind already in the product |

### 3.3 Infra

| Concern | Choose |
|---------|--------|
| Reverse proxy | Nginx — static `apps/web` dist; `/api/*` → Axum |
| Containers | Compose: `api` + `web` (+ attach to existing `postgres` / `redis` / `clickhouse`) |
| Spec packaging | CI fails if `utoipa` export ≠ committed OpenAPI / generated client |

---

## 4. Auth and session (keep the logic)

Preserve behavior from `users` + allauth:

1. **Google OAuth** with email + profile scopes (PKCE).
2. **Pre-provisioned users:** email match on `users_user`; update `name` / `avatar` — same as `MySocialAccountAdapter`.
3. **Session cookie** via `tower-sessions` (HttpOnly, Secure, SameSite) — not JWT for the browser SPA in v1.
4. **Active role** + **active school** in session.
5. **Impersonation** for Project Admin.
6. **Page ACL** from `users_page`.
7. **Teacher student scope** via `schedule_list` + `staff_id`.
8. **Usage tracking** after core auth is stable.

### Auth endpoints (simplified, same behavior)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/auth/google/start` | Begin OAuth |
| GET | `/api/auth/google/callback` | Finish OAuth; set session |
| POST | `/api/auth/logout` | Clear session |
| GET | `/api/auth/me` | User, roles, schools, active context, impersonation |
| POST | `/api/auth/switch-role` | Active role + school |
| POST | `/api/auth/impersonate/{id}` | Start impersonation |
| POST | `/api/auth/stop-impersonate` | End impersonation |
| GET | `/api/nav` | Menu tree from `Page` rows |

Django `/accounts/*` HTML is replaced; **login policy stays**. Prefer existing `users_*` tables so flipping the app pointer needs no user migration.

---

## 5. API design principles

1. **JSON only** from Axum (plus streamed file downloads).
2. **One resource model per domain concept** — e.g. `{ columns, rows, total, meta }` instead of HTML partials.
3. **Collapse HTMX modal endpoints** into CRUD + `GET …/form-options` where needed.
4. **Keep query semantics** — filters, school-year, grade, status filters, column structures, CH routing.
5. **`/api/v1/...`** from day one for dual-run behind Nginx.
6. **utoipa-first** per module before UI port of that module.

| Today (Django) | Target (Axum) |
|----------------|---------------|
| `load_students` → HTMX or `?format=json` | `POST /api/v1/exploration/students/query` → JSON |
| Folder tree partials | `GET/POST/PATCH/DELETE /api/v1/exploration/folders` |
| Save query modal HTML | `POST /api/v1/exploration/queries` |
| Stats partial | `POST /api/v1/exploration/stats` |
| Chart render API | Same ECharts option/series JSON shape React already uses |

---

## 6. Multi-phase plan

Each phase ends with: OpenAPI updated, generated client green, parity tests green, Docker runnable against the shared DB.

### Phase 0 — Bootstrap independent repo & inventory freeze

- Create new empty GitHub repo (not a fork).
- Scaffold `apps/api` (Axum + `utoipa` health), `apps/web` (Vite + React 19 + TanStack Router/Query + Tailwind/shadcn).
- Schema inventory + `docs/API_MAP.md`.
- Golden fixtures from Django: auth, one exploration query, one dataviz chart render payload (**include raw ECharts option JSON**).

**Exit:** builds, health checks, route map, fixture corpus started.

### Phase 1 — Core platform: DB clients, auth, ACL, shell

**Backend:** `sqlx` pool, Redis, ClickHouse stub; Google OAuth + `tower-sessions`; extractors for auth/page ACL; port student-key scoping.  
**Frontend:** auth gate, shell, nav from `/api/nav`, role/school switcher, impersonation, landing.

**Exit:** Google login against real DB; nav matches Page ACL; no Django for shell.

### Phase 2 — Analytics engine (shared performance core)

Port `QueryExecutor` (CH-first, PG fallback rules identical), Redis helpers, column/filter services shared by exploration / nextyearlist / dataviz.

**Exit:** parity on representative queries; executor tests.

### Phase 3 — Data Exploration + Next Year List

APIs for student query, columns, stats, CSV stream, folders, saved queries, lists, shares, summary.  
UI: TanStack Table explorers + TanStack Form save/load — behavior of `student_tests_app.js` without HTMX.

**Exit:** row counts, filters, exports match Django on same DB.

### Phase 4 — Dataviz (largest JSON surface; best Rust win)

APIs under `/api/v1/dataviz/...` aligned with today’s chart/dataset/page/drill/SQL lab surface.  
**React keeps ECharts + Gridstack.**  
**Rust owns** compile, data load, filter resolution, batch render **data**, CSV stream.

**Exit:** saved charts/pages render identically (option JSON + data); builder save/load works.

### Phase 5 — Placement Cards, MathR, DESI

HTMX → JSON + TanStack Form/Table UIs. Preserve cards/classrooms/tags/print, MathR recommendations/completion, DESI accommodations/export.

**Exit:** workflow + print/PDF parity.

### Phase 6 — DataLoader + Project Admin

Survey/AP import progress (Redis), user manage/bulk update/usage, page CRUD, `ProjectConfig`.

**Exit:** Project Admin daily ops without Django; imports same DB side effects.

### Phase 7 — Dual-run cutover & hardening

1. Nginx: `/api/v1/` → Axum; Django only for any leftover paths.  
2. SPA Axum-only when Phases 3–6 done.  
3. Load tests (exploration + chart batch render).  
4. Security: CSRF/CORS, cookies, OAuth allowlist, SQL lab sandbox.  
5. Remove Django from compose.

**Exit:** Axum + React + Nginx + PG + CH + Redis only.

### Phase 8 — Cleanup & ownership

CI (`cargo test`/`clippy`, Vitest, Playwright smoke, OpenAPI drift, schema sync-check), CH sync binary/job, perf budgets.

---

## 7. Database compatibility rules

1. **No table renames** in v1 (`users_user`, `dataviz_savedchart`, …).
2. Default **zero schema change**; `sqlx migrate` only with explicit approval.
3. Unmanaged/ETL tables stay read-mostly.
4. ClickHouse remains analytics mirror; sync job writes CH.
5. Same env keys as today (`POSTGRES_*`, `CLICKHOUSE_*`, `USE_CLICKHOUSE_ANALYTICS`, Redis, `GOOGLE_CLIENT_*`).

---

## 8. Parity & testing strategy

| Layer | Approach |
|-------|----------|
| Contract | `utoipa` → OpenAPI → generated TS client; CI drift check |
| Golden | Django fixtures (incl. ECharts option JSON) → Axum equality |
| SQL | Snapshot compiled SQL for compilers |
| Auth | Fixture users (roles/schools/teacher `staff_id`) |
| UI | Playwright: login, explorer filters, chart view, placement save |
| Perf | Criterion benches on QueryExecutor vs Django baselines |
| Print | Binary/sample diffs for PDF/CSV |

---

## 9. Risk register

| Risk | Mitigation |
|------|------------|
| Framework choice churn (Rocket vs Axum) | Freeze on Axum; handlers are thin over domain services so a swap stays possible |
| Session/allauth impedance | Own `tower-sessions`; require `users_user` (+ social link if kept); expect one re-login |
| ACL regressions | Port ACL first; property-test against Django helper outputs |
| CH translator gaps | Identical fallback rules; log translation misses |
| Chart/layout drift | **Mandate ECharts + Gridstack**; golden option/layout fixtures |
| “Better UI kit” redesign creep | shadcn is primitives only; match existing workflows/IA |
| HTMX → React scope | API-first per module; UI may lag API by one phase |
| Dual-run confusion | Single writer per domain; feature flags by module |

---

## 10. First implementation slice

1. New repo scaffold (Phase 0).  
2. Axum `GET /api/health` + `sqlx` read of `users_user`.  
3. Google OAuth + `tower-sessions` + `/api/auth/me` (utoipa-documented).  
4. React shell (TanStack Router/Query) login + nav from `Page`.  
5. One exploration query endpoint + TanStack Table (no charts yet).

Validates: independent repo, same DB, login kept, separate apps, better libs, Rust on the query path, JS still owns presentation.

---

## 11. Phase checklist

| Phase | Focus | Primary apps |
|------:|-------|----------------|
| 0 | Monorepo, API map, fixtures | — |
| 1 | Auth, ACL, shell | users, landing |
| 2 | CH/PG executor, cache, columns | shared `db`/`domain` |
| 3 | Student explorers | dataexploration, nextyearlist |
| 4 | Charts/pages/datasets | dataviz (ECharts + Gridstack in React) |
| 5 | Workflow UIs | placementcards, mathr, desi |
| 6 | Admin/import | dataLoader, users admin, ProjectConfig |
| 7 | Cutover, Nginx, load/security | infra |
| 8 | CI, sync job, docs | platform |

---

## 12. Success definition

1. Independent monorepo: `apps/api` (**Axum**) + `apps/web` (**React 19**).  
2. Same Postgres/ClickHouse/Redis; all prior workflows work.  
3. Google login + role/school/impersonation/page ACL behave as today.  
4. Simplified JSON APIs; domain rules preserved.  
5. Charts/layouts still **ECharts + Gridstack**; Rust accelerates data/query/export only.  
6. Stack uses the stronger 2026 libraries above without changing product behavior.
