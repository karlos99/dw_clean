from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('manage/', views.manage_users, name='manage_users'),
    path('manage/bulk-update/', views.bulk_update_users, name='bulk_update_users'),
    path('manage/<int:user_id>/', views.user_detail, name='user_detail'),
    path('switch-role/', views.switch_role, name='switch_role'),
    path('impersonate/<int:user_id>/', views.impersonate_user, name='impersonate_user'),
    path('stop-impersonating/', views.stop_impersonating, name='stop_impersonating'),
]
