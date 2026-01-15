from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('manage/', views.manage_users, name='manage_users'),
    path('manage/<int:user_id>/', views.user_detail, name='user_detail'),
]
