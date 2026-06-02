---
name: dataviz-ux-overhaul
description: Comprehensive Dataviz App UX Overhaul across 6 phases implemented on 2026-06-01
metadata:
  type: project
---

The Dataviz App UX Overhaul was implemented on 2026-06-01, covering all 6 phases:

**Phase 1**: Deleted legacy builder.html and 11 inline JS files. Added `school_filter_mode` field to SavedChart model. Created migration 0009.

**Phase 2**: Redesigned data source panel in charts/builder.html with three radio-card source types (Saved Query [Recommended], Pick Columns [Simple], Custom SQL [Advanced]). Added searchable query selector, query preview API (`/dataviz/api/query/<id>/preview/`), School Year Context dropdown, and query summary box.

**Phase 3**: Prominent "School Scope" toggle with visual badges (Enrolled School / Tested School). Added collapsible info box explaining the distinction. school_filter_mode is wired through data_loader.py via tested_school_codes parameter to _fetch_test_data().

**Phase 4**: Defaulted `dvYearMode` to `'relative'` (was `'specific'`). Added year alias labels (`year_alias_labels_json`, `alias_resolved_labels_json`) to ChartBuilderView context. `resolve_relative_column_key()` is already called in all code paths in _run_saved_query_sql().

**Phase 5**: Added "Save & Add to Page" button with modal picker. `chart_save_and_attach_api` endpoint saves chart and creates DataVizPageSection. Added "New Chart" button in pages/builder.html. Added cross-navigation links (Charts | Pages) in both builder templates.

**Phase 6**: Added guided mode banner (localStorage: `datavizGuidedMode`). Step indicators on data source header. Chart type recommendations via `recommend_chart_types()` in chart_builder.py, triggered after column load. Auto-description via `auto_describe_chart()`, triggered after preview.

**Why**: Teachers, school admins, and district admins found the old chart builder confusing. The redesign provides clear guidance, defaults to relative years for auto-updating charts, and unifies the chart/page workflow.

**How to apply**: The main template is `templates/dataviz/charts/builder.html` (Alpine.js + ECharts 6). Backend logic lives in `dataviz/utils/chart_builder.py` and `dataviz/utils/data_loader.py`. New API endpoints are in `dataviz/views/api/query_api.py` and `dataviz/views/api/chart_api.py`.
