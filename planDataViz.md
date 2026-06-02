# Dataviz App UX Overhaul — Implementation Plan

## Context

The dataviz app (at `django_pro/dataviz/`) allows project admins to build charts, dashboards, and publishable pages from student data. The current chart builder (`templates/dataviz/charts/builder.html`) is a 2800+ line Alpine.js SPA that, while functional, presents a steep learning curve for non-technical users. The data selection flow mixes three source types (Saved Query, SQL Lab, Column Picker) without clear guidance. The distinction between "enrollment school" and "assessment school" is buried in the UI. Relative school year aliases (`__sy_this__`, `__sy_prev1__`, `__sy_prev2__`) exist in the backend but aren't clearly exposed to users.

Additionally, there are **two competing builder UIs**: the legacy `builder.html` (1594 lines, 11 inline JS files, Highcharts-based) and the newer Alpine.js `charts/builder.html` (ECharts 6-based). The legacy one should be retired.

The goal is to make chart and page creation friendly for teachers, school admins, and district admins — while keeping the proven backend (chart_builder.py, data_loader.py) and all custom chart types intact.

---

## Design Decisions

1. **Three source types, clearly differentiated** — The chart builder offers three data source options, each with clear guidance about what they're for:
   - **Saved Query** (Recommended): Use a pre-built query from Data Explorer. Best for most users.
   - **Simple Column Picker**: Pick columns directly without saving a query first. The UI makes it clear what kind of query is being built (which school years, enrollment matching, assessment types).
   - **Custom SQL** (Advanced): Write raw SQL. For power users only.

2. **Retire the old `builder.html`** — Standardize entirely on the Alpine.js `charts/builder.html`. Delete the 1594-line legacy builder and its 11 inline JS files.

3. **Relative years as the default** — The "Year Mode" toggle defaults to Relative, so saved charts auto-update when `ProjectConfig.default_school_year` changes.

4. **Enrollment vs. Assessment school as a first-class concept** — A new `school_filter_mode` field on `SavedChart` and a prominent UI toggle make this distinction clear.

5. **Flexible controls over preset scenarios** — Users get clear building blocks (school year context, relative years, school filter mode) rather than rigid scenario templates. The controls themselves explain what kind of query is being built.

6. **No new major models** — The existing `SavedChart.source_filters` JSON field is flexible enough. Only one small field (`school_filter_mode`) is added.

7. **Incremental delivery starts with Phase 2** — Phase 2 (simplified data source selection) is the highest priority and delivers the core UX improvement. Phase 1 (cleanup) is a prerequisite.

---

## Phases

### Phase 1: Clean Up and Consolidate (Foundation)

**Remove the legacy builder and standardize on ECharts 6.**

| Action | File |
|--------|------|
| Delete `templates/dataviz/builder.html` (old SPA, 1594 lines) | DELETE |
| Delete 11 inline JS files: `templates/static/js/dataviz_app_*.js` | DELETE |
| Remove Highcharts CDN scripts from `charts/builder.html` (already ECharts 6) | Edit |
| Add `school_filter_mode` field to `SavedChart` model | `dataviz/models.py` |
| Create and run migration | `dataviz/migrations/` |
| Verify `ChartBuilderView` already uses `dataviz/charts/builder.html` (confirmed: line 79) | No change needed |

**New model field:**
```python
# dataviz/models.py — SavedChart
school_filter_mode = models.CharField(
    max_length=10,
    default='enrolled',
    choices=[('enrolled', 'Enrolled School'), ('tested', 'Tested School')],
    blank=True,
)
```

---

### Phase 2: Simplify Data Source Selection ⭐ HIGHEST PRIORITY

**Redesign the data source panel so users understand what kind of query they're building.**

Three source types, clearly presented with guidance:

| Action | File |
|--------|------|
| Redesign source type selector as 3 clearly labeled tabs/radio cards: (1) Saved Query [Recommended], (2) Simple Column Picker, (3) Custom SQL [Advanced] | `templates/dataviz/charts/builder.html` |
| **Saved Query tab**: Make selector searchable with query name, type badge, folder context. Add "Open in Data Explorer" link. Add "Preview Data" button showing first 5 rows. | Same file |
| **Simple Column Picker tab**: Simplified version that focuses on WHAT the user is querying, not HOW. Clearly shows: which school year(s) of enrollment, which assessment results to include. Guide text: "You are selecting students enrolled in [chosen years] and their assessment results." | Same file |
| Add a query summary box that updates in real-time: "This query will return: students enrolled in 2025-2026, with iReady Reading/Math results from 2024-2025 and 2025-2026, filtered by enrolled school" | Same file |
| Add query preview API endpoint (`GET /dataviz/api/queries/<id>/preview/`) — returns first 5 rows + column list | `dataviz/views/api/query_api.py` |
| Add `run_saved_query_preview()` to data_loader (reuses existing SQL, LIMIT 5) | `dataviz/utils/data_loader.py` |
| Add URL route for preview endpoint | `dataviz/urls.py` |
| Replace buried "School Year" multi-select with a prominent "School Year Context" dropdown at the top of the source panel (choices: This Year, Last Year, Two Years Prior, Custom) | `templates/dataviz/charts/builder.html` |
| Store `school_year_context` in `source_filters` (values: `__sy_this__`, `__sy_prev1__`, `__sy_prev2__`, or `null` for custom) | Same file (Alpine.js state → save payload) |

**Wireframe of new data source panel:**
```
┌─────────────────────────────────────────┐
│ DATA SOURCE                             │
│                                         │
│ School Year Context: [★ This Year (25-26) ▼]│
│  → Students enrolled in selected years   │
│  → Assessment results matched per year   │
│                                         │
│ ┌─ Source Type ───────────────────────┐ │
│ │ (•) Saved Query  [Recommended]      │ │
│ │ ( ) Pick Columns  [Simple]          │ │
│ │ ( ) Custom SQL    [Advanced]        │ │
│ └────────────────────────────────────┘ │
│                                         │
│ ┌─ Saved Query (selected) ────────────┐ │
│ │ 🔍 Search queries…                  │ │
│ │ ┌─────────────────────────────────┐ │ │
│ │ │ 📋 Spring Assessments  [Filter]   │ │
│ │ │ 📋 Enrollment Demographics        │ │
│ │ │ 📋 Current Year EL Status         │ │
│ │ └─────────────────────────────────┘ │ │
│ │         [📂 Open in Data Explorer]   │ │
│ │         [👁 Preview Data (5 rows)]   │ │
│ └────────────────────────────────────┘ │
│                                         │
│ ┌─ Query Summary ────────────────────┐ │
│ │ 📊 This query returns:              │ │
│ │  • Students enrolled in 2025-2026   │ │
│ │  • iReady Reading + Math results    │ │
│ │  • Filtered by: Enrolled School     │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

**For the Simple Column Picker tab:**
```
┌─ Pick Columns (Simple) ──────────────┐
│                                       │
│ What students do you want to see?     │
│                                       │
│ Enrollment year(s):                   │
│ [✓] This Year (2025-2026)             │
│ [✓] Last Year (2024-2025)            │
│ [ ] Two Years Prior (2023-2024)      │
│                                       │
│ Which assessment results?             │
│ ┌───────────────────────────────────┐ │
│ │ [+ Add Assessment]                │ │
│ │ iReady Reading — SS, PL           │ │
│ │ iReady Math — SS, PL              │ │
│ │ [Remove]                          │ │
│ └───────────────────────────────────┘ │
│                                       │
│ School to filter by:                  │
│ (•) Enrolled School                   │
│ ( ) Assessment School                 │
│                                       │
│ Student filters (optional):           │
│ Grade: [All ▼]  Status: [Active ▼]   │
│                                       │
│ [Apply & Load Columns]                │
└───────────────────────────────────────┘
```

---

### Phase 3: Enrollment vs. Assessment School Distinction

**Make it obvious whether data is filtered by enrollment school or assessment school.**

| Action | File |
|--------|------|
| Move the "School Filter Mode" toggle from its buried position to a prominent location directly below the data source | `templates/dataviz/charts/builder.html` |
| Add visual indicators: colored badges showing "Enrolled School: X" or "Tested School: X" | Same file |
| Add explanatory text in a collapsible info box explaining the difference | Same file |
| Wire `school_filter_mode` through `data_loader.py`: when mode is `tested`, filter `results` by `r.school_code` instead of `cs.school_state_id` | `dataviz/utils/data_loader.py` |
| Ensure the tested_school_codes parameter in `_fetch_test_data()` receives the correct value | Same file (~line 621) |
| Surface the mode in chart save/load: include in `source_filters` and read back when editing | `templates/dataviz/charts/builder.html` (Alpine.js state) |

**UI for school filter mode:**
```
┌─────────────────────────────────────────┐
│ SCHOOL SCOPE                            │
│                                         │
│ Filter students by:                     │
│ (•) Enrolled School — school where      │
│     student is/was enrolled             │
│ ( ) Tested School   — school where      │
│     the assessment was taken (may       │
│     differ from enrollment)             │
│                                         │
│ [!] Assessment results show the school  │
│     where each test was administered.   │
│     A student enrolled at Lincoln may   │
│     have test results from Washington.  │
└─────────────────────────────────────────┘
```

---

### Phase 4: Relative Year Defaults and Column Resolution

**Ensure relative year aliases are fully wired and are the default experience.**

| Action | File |
|--------|------|
| Default `dvYearMode` to `'relative'` (currently defaults to `'specific'` at ~line 186) | `templates/dataviz/charts/builder.html` |
| Add alias year options to the "School Year Context" dropdown: This Year (`__sy_this__`), Last Year (`__sy_prev1__`), Two Prior (`__sy_prev2__`) alongside concrete years | Same file |
| Show resolved year label next to alias (e.g., "This Year → 2025-2026") | Same file |
| Verify `resolve_relative_column_key()` is called for ALL code paths in data_loader.py (saved query, SQL lab, and column picker paths) | `dataviz/utils/data_loader.py` |
| Verify `get_year_labels_map()` is used consistently for year display labels | `dataviz/views/chart_views.py` (already passes `year_labels` to context) |
| Pass `yearAliasLabels` to the frontend as JSON for alias display in dropdowns | `dataviz/views/chart_views.py` |

**School Year Context dropdown with aliases:**
```
┌──────────────────────────────────┐
│ School Year Context           ▼  │
├──────────────────────────────────┤
│ ★ This School Year  → 2025-2026 │
│ ★ Last School Year → 2024-2025 │
│ ★ Two Years Prior  → 2023-2024 │
│ ─────────────────────────────── │
│   2025-2026                     │
│   2024-2025                     │
│   2023-2024                     │
│   2022-2023                     │
│   …                             │
│ ─────────────────────────────── │
│   Custom selection…             │
└──────────────────────────────────┘
```

**Handling the two data scenarios:**
- **Enrollment-driven (year by year):** User picks a concrete School Year Context (e.g., "2023-2024"). The query returns students enrolled in that year with their assessments from the same year. This is the default when a specific year is selected.
- **Current enrollment + historical tests:** User picks "This School Year" (relative) as the enrollment filter, but selects test columns from multiple years (e.g., `test___sy_this___iReady_Reading_SS`, `test___sy_prev1___iReady_Reading_SS`). The relative aliases resolve each test column to its appropriate year independently.

---

### Phase 5: Unify Chart Builder and Page Builder

**Make charts and pages feel like one workflow instead of two separate tools.**

| Action | File |
|--------|------|
| In chart builder save modal: add "Save and Add to Page…" button alongside "Save Chart" | `templates/dataviz/charts/builder.html` |
| "Save and Add to Page" opens a picker: existing pages or "Create New Page" | Same file |
| In page builder: add "Create New Chart" button next to the existing "Add Chart" picker | `templates/dataviz/pages/builder.html` |
| "Create New Chart" opens the chart builder in a modal/drawer within the page builder context | Same file |
| Add `chart_save_and_attach_api` endpoint (saves chart + creates section in target page) | `dataviz/views/api/chart_api.py` |
| Add cross-navigation in the top bar: "My Charts | Pages" with quick-create buttons | Both builder templates |
| Add URL routes for new endpoints | `dataviz/urls.py` |

---

### Phase 6: Polish — Guided Mode and Smart Recommendations

**Add progressive disclosure and AI-assisted configuration for first-time users.**

| Action | File |
|--------|------|
| Add `show_guided_mode` flag (localStorage). When enabled, shows step tooltips: (1) Pick a query, (2) Choose chart type, (3) Set X/Y axes, (4) Run Preview | `templates/dataviz/charts/builder.html` |
| Add `recommend_chart_types(columns)` function to chart_builder.py — suggests chart types based on selected column types (e.g., assessment PL columns → Assessment Compare, time dimension + metric → Line) | `dataviz/utils/chart_builder.py` |
| Add chart recommendation API endpoint (`POST /dataviz/api/charts/recommend/`) | `dataviz/views/api/chart_api.py` |
| Add auto-generated plain-English chart description: "A bar chart showing count of students by Grade for This School Year, grouped by Race, at Lincoln Elementary" | `dataviz/utils/chart_builder.py` + API |
| Show the auto-description below the chart canvas so users can verify their config | `templates/dataviz/charts/builder.html` |
| Guide toggle: "New to charts? [Start Guided Mode]" banner that appears on first visit | Same file |
| Add URL routes | `dataviz/urls.py` |

---

## Files Summary

### Files to Delete
- `templates/dataviz/builder.html`
- `templates/static/js/dataviz_app_core.js`
- `templates/static/js/dataviz_app_chart.js`
- `templates/static/js/dataviz_app_data.js`
- `templates/static/js/dataviz_app_columns.js`
- `templates/static/js/dataviz_app_explore.js`
- `templates/static/js/dataviz_app_sql.js`
- `templates/static/js/dataviz_app_save.js`
- `templates/static/js/dataviz_app_dash.js`
- `templates/static/js/dataviz_app_helpers.js`
- `templates/static/js/dataviz_utils.js`
- `templates/static/js/dataviz_chart_renderer.js` (Highcharts legacy — keep only if referenced elsewhere)

### Files to Modify (primary)
- `django_pro/dataviz/models.py` — add `school_filter_mode` field
- `django_pro/dataviz/urls.py` — add new API routes
- `django_pro/dataviz/views/chart_views.py` — pass year alias labels, relative year context
- `django_pro/dataviz/views/api/query_api.py` — add query preview endpoint
- `django_pro/dataviz/views/api/chart_api.py` — add save-and-attach, recommend, describe endpoints
- `django_pro/dataviz/utils/data_loader.py` — wire school_filter_mode, tested_school_codes, verify relative column resolution in all paths
- `django_pro/dataviz/utils/chart_builder.py` — add `recommend_chart_types()`, auto-describe helper
- `django_pro/templates/dataviz/charts/builder.html` — **main UX surface**: data source panel redesign, school filter mode UI, relative year defaults, save-and-add-to-page, guided mode
- `django_pro/templates/dataviz/pages/builder.html` — inline chart creation, cross-linking

### New Files
- `django_pro/dataviz/migrations/XXXX_add_school_filter_mode.py` — migration

---

## Backward Compatibility

- All three `source_type` values (`query`, `columns`, `sql`) remain fully supported in the model and API
- Existing `SavedChart` records get `school_filter_mode='enrolled'` as default, preserving current behavior
- The old `builder.html` URL is already unused (ChartBuilderView points to `charts/builder.html`)
- SQL Lab remains available as the "Advanced" tab for power users
- The Column Picker is simplified but stays — existing column-picker charts continue to work
- All existing chart types, palettes, and rendering continue to work unchanged

---

## Verification

1. **Phase 1:** After deleting old files, verify `ChartBuilderView` loads without errors. Run `makemigrations` + `migrate`. Confirm existing charts still render via `chart_render_api`.

2. **Phase 2:** Create a new chart using a saved query. Verify the query preview shows correct data. Verify school year context dropdown changes the resolved year in the executed query.

3. **Phase 3:** Create a chart with `school_filter_mode='tested'`. Verify the SQL filters on `r.school_code` instead of `cs.school_state_id`. Compare results with enrolled mode to confirm they differ when students took tests at different schools.

4. **Phase 4:** Save a chart with "This School Year" context. Change `ProjectConfig.default_school_year` and verify the chart auto-updates. Verify relative test columns (e.g., `test___sy_prev1___iReady_Reading_SS`) resolve correctly for each year.

5. **Phase 5:** Save a chart → "Save and Add to Page" → verify it appears in the page builder. From page builder → "Create New Chart" → verify the new chart appears in the page after saving.

6. **Phase 6:** Enable guided mode → verify step tooltips appear. Select assessment PL columns → verify Assessment Compare is recommended. Verify auto-description matches the actual chart configuration.

7. **End-to-end:** Create a chart showing "Currently enrolled students' reading scores from the past 3 years, filtered by tested school." Verify the chart renders correctly and the drill-through shows the right students.
