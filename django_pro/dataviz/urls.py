from django.urls import path
from . import views

app_name = 'dataviz'

urlpatterns = [
    # Dashboard
    path('', views.dashboard_home, name='dashboard_home'),
    
    # Datasets
    path('datasets/', views.dataset_list, name='dataset_list'),
    path('datasets/new/', views.dataset_builder, name='dataset_create'),
    path('datasets/<int:dataset_id>/edit/', views.dataset_builder, name='dataset_edit'),
    path('datasets/save/', views.dataset_save, name='dataset_save'),
    path('datasets/<int:dataset_id>/save/', views.dataset_save, name='dataset_save_id'),
    path('datasets/<int:dataset_id>/delete/', views.dataset_delete, name='dataset_delete'),
    
    # Charts
    path('charts/', views.chart_list, name='chart_list'),
    path('charts/new/', views.chart_builder, name='chart_create'),
    path('charts/<int:chart_id>/edit/', views.chart_builder, name='chart_edit'),
    path('charts/save/', views.chart_save, name='chart_save'),
    path('charts/<int:chart_id>/save/', views.chart_save, name='chart_save_id'),
    path('charts/<int:chart_id>/delete/', views.chart_delete, name='chart_delete'),
    
    # Pages
    path('pages/', views.page_list, name='page_list'),
    path('pages/new/', views.page_builder, name='page_create'),
    path('pages/<int:page_id>/edit/', views.page_builder, name='page_edit'),
    path('pages/save/', views.page_save, name='page_save'),
    path('pages/<int:page_id>/save/', views.page_save, name='page_save_id'),
    path('pages/<int:page_id>/layout/', views.page_layout_save, name='page_layout_save'),
    path('pages/<int:page_id>/delete/', views.page_delete, name='page_delete'),
    path('pages/<int:page_id>/publish/', views.page_publish_toggle, name='page_publish_toggle'),
    
    # Public views
    path('page/<slug:slug>/', views.public_page_view, name='public_page_view'),
    
    # API endpoints
    path('api/dataset/preview/', views.api_dataset_preview, name='api_dataset_preview'),
    path('api/chart/columns/', views.api_chart_columns, name='api_chart_columns'),
    path('api/chart/preview/', views.api_chart_preview, name='api_chart_preview'),
    path('api/column/unique-values/', views.api_column_unique_values, name='api_column_unique_values'),
]
