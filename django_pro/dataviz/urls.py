from django.urls import path

from .views import DataVizBuilderView, render_chart_api

app_name = 'dataviz'

urlpatterns = [
    path('', DataVizBuilderView.as_view(), name='builder'),
    path('api/render/', render_chart_api, name='render_chart'),
]
