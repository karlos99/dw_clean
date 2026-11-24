from django.urls import path

from ca_dash import views, views_api

app_name = 'ca_dash'

urlpatterns = [
    path('', views.performance_map, name='performance_map'),
    path('api/config/', views_api.api_config, name='api_config'),
    path('api/indicators/', views_api.api_indicators, name='api_indicators'),
    path('api/schools/', views_api.api_schools, name='api_schools'),
    path('api/subgroups/', views_api.api_subgroups, name='api_subgroups'),
    path('api/report/', views_api.api_report, name='api_report'),
    path('api/box-entities/', views_api.api_box_entities, name='api_box_entities'),
    path('api/school-detail/', views_api.api_school_detail, name='api_school_detail'),
]