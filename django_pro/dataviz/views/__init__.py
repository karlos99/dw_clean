"""dataviz views package — re-exports all callable views for URL routing."""

# Page-level views
from .chart_views import chart_index, chart_builder, chart_view
from .page_views import page_index, page_builder, page_view
from .dataset_views import dataset_index, dataset_builder
from .drill_views import drill_table

# API views
from .api.chart_api import (
    chart_preview_api,
    chart_save_api,
    chart_save_and_attach_api,
    chart_recommend_api,
    chart_describe_api,
    chart_detail_api,
    chart_duplicate_api,
    chart_move_api,
    chart_render_api,
    chart_list_partial,
)
from .api.query_api import (
    query_run_api, query_columns_api, query_filter_options_api, sql_lab_api, sql_schema_api,
    column_structures_api, column_filter_options_api, query_preview_api,
)
from .api.page_api import (
    page_save_api,
    page_delete_api,
    page_sections_api,
    page_filter_options_api,
    page_filter_columns_api,
    section_detail_api,
    row_detail_api,
    clear_cache_api,
)
from .api.drill_api import (
    drill_rows_api, drill_columns_api, drill_filter_values_api,
    drill_export_csv_api, drill_available_columns_api,
)
from .dataset_views import (
    dataset_save_api, dataset_detail_api, dataset_list_api, dataset_preview_api, api_column_unique_values, dataset_filter_options_api, dataset_duplicate_api,
)
from .api.colormap_api import (
    colormap_preset_save_api, colormap_preset_detail_api, colormap_preset_list_api,
)

__all__ = [
    'chart_index', 'chart_builder',
    'page_index', 'page_builder', 'page_view',
    'dataset_index', 'dataset_builder',
    'drill_table',
    'chart_preview_api', 'chart_save_api', 'chart_save_and_attach_api', 'chart_recommend_api',
    'chart_describe_api', 'chart_detail_api', 'chart_duplicate_api', 'chart_move_api',
    'chart_render_api', 'chart_list_partial',
    'query_run_api', 'query_columns_api', 'query_filter_options_api', 'sql_lab_api', 'sql_schema_api',
    'column_structures_api', 'column_filter_options_api', 'query_preview_api',
    'page_save_api', 'page_delete_api', 'page_sections_api', 'page_filter_options_api',
    'page_filter_columns_api', 'section_detail_api', 'row_detail_api',
    'drill_rows_api', 'drill_columns_api', 'drill_filter_values_api',
    'drill_export_csv_api', 'drill_available_columns_api',
    'dataset_save_api', 'dataset_detail_api', 'dataset_list_api', 'dataset_preview_api', 'api_column_unique_values', 'dataset_filter_options_api', 'dataset_duplicate_api',
    'colormap_preset_save_api', 'colormap_preset_detail_api', 'colormap_preset_list_api',
]
