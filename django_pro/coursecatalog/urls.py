from django.urls import path

from . import views

app_name = 'coursecatalog'

urlpatterns = [
    path('', views.overview, name='overview'),
    path('categories/', views.categories, name='categories'),
    path('cte/', views.cte_taxonomy, name='cte_taxonomy'),
    path('tags/create/', views.tag_create, name='tag_create'),
    path('tags/<int:tag_id>/update/', views.tag_update, name='tag_update'),
    path('tags/<int:tag_id>/delete/', views.tag_delete, name='tag_delete'),
    path('tags/<int:tag_id>/categories/create/', views.category_create, name='category_create'),
    path('categories/<int:category_id>/update/', views.category_update, name='category_update'),
    path('categories/<int:category_id>/delete/', views.category_delete, name='category_delete'),
    path('categories/<int:category_id>/disciplines/create/', views.discipline_create, name='discipline_create'),
    path('categories/<int:category_id>/disciplines/bulk/', views.disciplines_bulk_create, name='disciplines_bulk_create'),
    path('disciplines/<int:discipline_id>/update/', views.discipline_update, name='discipline_update'),
    path('disciplines/<int:discipline_id>/delete/', views.discipline_delete, name='discipline_delete'),
    path('levels/create/', views.level_create, name='level_create'),
    path('levels/<int:level_id>/update/', views.level_update, name='level_update'),
    path('levels/<int:level_id>/delete/', views.level_delete, name='level_delete'),
    path('placements/', views.placements, name='placements'),
    path('placements/bulk/', views.placement_bulk_create, name='placement_bulk_create'),
    path('placements/bulk-update/', views.placement_bulk_update, name='placement_bulk_update'),
    path('placements/<int:pk>/delete/', views.placement_delete, name='placement_delete'),
    path('paths/', views.paths, name='paths'),
    path('paths/create/', views.path_create, name='path_create'),
    path('paths/<int:path_id>/steps/create/', views.path_step_create, name='path_step_create'),
    path('enrollment/', views.enrollment_preview, name='enrollment'),
]
