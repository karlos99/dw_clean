from django.urls import path

from .views import (
    DataVizBuilderView,
    chart_detail_api,
    chart_list_api,
    dashboard_add_chart_api,
    dashboard_detail_api,
    dashboard_list_api,
    dashboard_slot_api,
    render_chart_api,
    save_chart_api,
    sql_lab_api,
    sql_schema_api,
)

app_name = 'dataviz'

urlpatterns = [
    # Main builder (Explore + SQL Lab + Dashboard mode via JS tabs)
    path('', DataVizBuilderView.as_view(), name='builder'),

    # Chart rendering (existing)
    path('api/render/', render_chart_api, name='render_chart'),

    # SQL Lab
    path('api/sql/',    sql_lab_api,    name='sql_lab'),
    path('api/schema/', sql_schema_api, name='sql_schema'),

    # Saved charts CRUD
    path('api/charts/',               chart_list_api,   name='chart_list'),
    path('api/charts/save/',          save_chart_api,   name='save_chart'),
    path('api/charts/<int:chart_id>/', chart_detail_api, name='chart_detail'),

    # Dashboards CRUD
    path('api/dashboards/',                              dashboard_list_api,      name='dashboard_list'),
    path('api/dashboards/<int:dashboard_id>/',           dashboard_detail_api,    name='dashboard_detail'),
    path('api/dashboards/<int:dashboard_id>/charts/',    dashboard_add_chart_api, name='dashboard_add_chart'),
    path('api/slots/<int:slot_id>/',                     dashboard_slot_api,      name='dashboard_slot'),
]
