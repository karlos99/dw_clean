---
name: echarts-alpine-stack
description: The dataviz chart builder uses Alpine.js SPA + ECharts 6, with Django backend
metadata:
  type: reference
---

The **Dataviz chart builder** stack:
- **Frontend**: Alpine.js SPA in `templates/dataviz/charts/builder.html` (~3100 lines after overhaul). Uses `x-data="chartBuilder()"` with reactive state.
- **Chart rendering**: ECharts 6 (CDN: `echarts@6.0.0`). Backend builds ECharts option JSON via `dataviz/utils/chart_builder.py`.
- **Data loading**: `dataviz/utils/data_loader.py` — `run_saved_query()`, `run_raw_sql()`, `run_column_query()`, `run_saved_query_preview()`.
- **Models**: `SavedChart` (with `source_filters` JSON, `school_filter_mode`), `Dashboard`, `DataVizPage`, `DataVizPageRow`, `DataVizPageSection`.
- **Page builder**: `templates/dataviz/pages/builder.html` with GridStack.js for drag-and-drop layout.

**Chart types** (defined in `CHART_TYPES` in chart_builder.py):
Comparison: bar, assessment_compare, progress_growth, summary_table, waterfall
Trend: line, area, areaspline, stacked_area
Proportion: pie, donut, funnel, treemap, parliament
Relationship: scatter, bubble, heatmap
Distribution: histogram, box
Advanced: radar, gauge

**Key APIs**:
- `POST /dataviz/api/charts/preview/` — build chart from config
- `POST /dataviz/api/charts/save/` — save chart
- `POST /dataviz/api/charts/save-and-attach/` — save + attach to page
- `POST /dataviz/api/charts/recommend/` — chart type recommendations
- `POST /dataviz/api/charts/describe/` — auto-describe chart
- `GET  /dataviz/api/query/<id>/preview/` — query preview (5 rows)
- `GET  /dataviz/api/query/<id>/columns/` — column names from saved query
- `GET  /dataviz/api/query/<id>/filter-options/` — distinct filter values

**Color palettes**: default, blue, warm, earth, green, purple, mono, assessment, traffic. Also has SEMANTIC_COLORS for consistent color coding of performance levels, grades, gender, etc.
