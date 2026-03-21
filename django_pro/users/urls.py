from django.urls import path
from . import views
from . import page_views

app_name = 'users'

urlpatterns = [
    path('manage/', views.manage_users, name='manage_users'),
    path('manage/bulk-update/', views.bulk_update_users, name='bulk_update_users'),
    path('manage/<int:user_id>/', views.user_detail, name='user_detail'),
    path('usage/', views.usage_report, name='usage_report'),
    path('switch-role/', views.switch_role, name='switch_role'),
    path('impersonate/<int:user_id>/', views.impersonate_user, name='impersonate_user'),
    path('stop-impersonating/', views.stop_impersonating, name='stop_impersonating'),
    
    # Page management
    path('pages/', page_views.manage_pages, name='manage_pages'),
    path('pages/add/', page_views.add_page, name='add_page'),
    path('pages/<int:page_id>/edit/', page_views.edit_page, name='edit_page'),
    path('pages/<int:page_id>/delete/', page_views.delete_page, name='delete_page'),
    path('pages/<int:page_id>/detail/', page_views.get_page_detail, name='get_page_detail'),
    path('pages/default-columns/', page_views.manage_default_columns, name='manage_default_columns'),
]