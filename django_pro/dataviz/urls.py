from django.urls import path

from .views import (
    # Page-level views
    chart_index, chart_builder, chart_view,
    page_index, page_builder, page_view,
    dataset_index, dataset_builder,
    drill_table,
    # Chart API
    chart_preview_api, chart_save_api, chart_detail_api, chart_duplicate_api, chart_move_api, chart_render_api, chart_batch_render_api, chart_list_partial,
    chart_save_and_attach_api, chart_recommend_api, chart_describe_api,
    # Query / SQL API
    query_run_api, query_columns_api, query_filter_options_api, query_preview_api, sql_lab_api, sql_schema_api,
    # Column Picker (Explorer) API
    column_structures_api, column_filter_options_api,
    # Page API
    page_save_api, page_duplicate_api, page_delete_api, page_sections_api, page_filter_options_api,
    page_filter_columns_api, section_detail_api, clear_cache_api,
    # Row API
    row_detail_api,
    # Drill-through API
    drill_rows_api, drill_columns_api, drill_filter_values_api,
    drill_export_csv_api, drill_available_columns_api,
    # Dataset API
    dataset_save_api, dataset_detail_api, dataset_list_api, dataset_preview_api, api_column_unique_values, dataset_filter_options_api, dataset_duplicate_api, dataset_export_csv_api,
    # Color-mapping preset API
    colormap_preset_save_api, colormap_preset_detail_api, colormap_preset_list_api,
)

app_name = 'dataviz'

urlpatterns = [
    # ── Charts ──────────────────────────────────────────────────────────
    path('',                               chart_index,    name='index'),
    path('charts/new/',                    chart_builder,  name='chart_new'),
    path('charts/<int:chart_id>/edit/',    chart_builder,  name='chart_edit'),
    path('charts/<int:chart_id>/view/',    chart_view,     name='chart_view'),



    # ── Pages ────────────────────────────────────────────────────────────
    path('pages/',              page_index,  name='page_index'),
    path('pages/new/',          page_builder, name='page_new'),
    path('pages/<slug>/edit/',  page_builder, name='page_edit'),
    path('pages/<slug>/',       page_view,    name='page_view'),

    # ── Drill-through ────────────────────────────────────────────────────
    path('drill/<int:chart_id>/', drill_table, name='drill_table'),

    # ── Chart API ────────────────────────────────────────────────────────
    path('api/charts/preview/',          chart_preview_api,  name='chart_preview'),
    path('api/charts/save/',             chart_save_api,     name='chart_save'),
    path('api/charts/save-and-attach/',  chart_save_and_attach_api, name='chart_save_and_attach'),
    path('api/charts/recommend/',        chart_recommend_api,      name='chart_recommend'),
    path('api/charts/describe/',         chart_describe_api,       name='chart_describe'),
    path('api/charts/list/',             chart_list_partial, name='chart_list'),
    path('api/charts/<int:chart_id>/duplicate/', chart_duplicate_api, name='chart_duplicate'),
    path('api/charts/<int:chart_id>/move/',      chart_move_api,      name='chart_move'),
    path('api/charts/<int:chart_id>/render/', chart_render_api,   name='chart_render'),
    path('api/charts/batch-render/',     chart_batch_render_api, name='chart_batch_render'),
    path('api/charts/<int:chart_id>/',   chart_detail_api,   name='chart_detail'),

    # ── Color-mapping preset API ──────────────────────────────────────────
    path('api/colormap-presets/save/',        colormap_preset_save_api,   name='colormap_preset_save'),
    path('api/colormap-presets/list/',        colormap_preset_list_api,   name='colormap_preset_list'),
    path('api/colormap-presets/<int:preset_id>/', colormap_preset_detail_api, name='colormap_preset_detail'),

    # ── Dataset pages ─────────────────────────────────────────────────────
    path('datasets/',                   dataset_index,  name='dataset_index'),
    path('datasets/new/',               dataset_builder, name='dataset_new'),
    path('datasets/<int:dataset_id>/edit/', dataset_builder, name='dataset_edit'),

    # ── Dataset API ────────────────────────────────────────────────────────
    path('api/datasets/save/',          dataset_save_api,   name='dataset_save'),
    path('api/datasets/preview/',       dataset_preview_api, name='dataset_preview'),
    path('api/datasets/export-csv/',    dataset_export_csv_api, name='dataset_export_csv'),
    path('api/datasets/list/',          dataset_list_api,   name='dataset_list'),
    path('api/datasets/<int:dataset_id>/', dataset_detail_api, name='dataset_detail'),
    path('api/datasets/<int:dataset_id>/duplicate/', dataset_duplicate_api, name='dataset_duplicate'),
    path('api/datasets/<int:dataset_id>/filter-options/', dataset_filter_options_api, name='dataset_filter_options'),
    path('api/column/unique-values/',   api_column_unique_values, name='api_column_unique_values'),

    # ── Query / SQL API ──────────────────────────────────────────────────
    path('api/query/run/',                     query_run_api,      name='query_run'),
    path('api/query/<int:query_id>/columns/',   query_columns_api,  name='query_columns'),
    path('api/query/<int:query_id>/filter-options/', query_filter_options_api, name='query_filter_options'),
    path('api/query/<int:query_id>/preview/',   query_preview_api,  name='query_preview'),
    path('api/query/sql/',                      sql_lab_api,        name='sql_lab'),
    path('api/schema/',                         sql_schema_api,     name='sql_schema'),

    # ── Column Picker (Explorer) API ─────────────────────────────────────────
    path('api/columns/structures/',              column_structures_api,      name='column_structures'),
    path('api/columns/filter-options/',           column_filter_options_api,  name='column_filter_options'),

    # ── Row API ────────────────────────────────────────────────────────────
    path('api/rows/<int:row_id>/',                        row_detail_api,       name='row_detail'),

    # ── Page API ─────────────────────────────────────────────────────────
    path('api/pages/save/',                                      page_save_api,           name='page_save'),
    path('api/pages/<slug>/duplicate/',                         page_duplicate_api,     name='page_duplicate'),
    path('api/pages/<slug>/delete/',                             page_delete_api,         name='page_delete'),
    path('api/pages/<slug>/sections/',                           page_sections_api,       name='page_sections'),
    path('api/pages/filter-columns/',                             page_filter_columns_api, name='page_filter_columns'),
    path('api/pages/<slug>/filters/<str:filter_id>/',            page_filter_options_api, name='page_filter_options'),
    path('api/sections/<int:section_id>/',                       section_detail_api,      name='section_detail'),
    path('api/cache/clear/',                                     clear_cache_api,         name='clear_cache'),

    # ── Drill API ────────────────────────────────────────────────────────
    path('api/drill/<int:chart_id>/rows/',           drill_rows_api,           name='drill_rows'),
    path('api/drill/<int:chart_id>/columns/',        drill_columns_api,        name='drill_columns'),
    path('api/drill/<int:chart_id>/filter-values/',  drill_filter_values_api,  name='drill_filter_values'),
    path('api/drill/<int:chart_id>/export-csv/',     drill_export_csv_api,     name='drill_export_csv'),
    path('api/drill/<int:chart_id>/available-columns/', drill_available_columns_api, name='drill_available_columns'),
]
