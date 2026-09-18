from django.urls import path

from . import views

app_name = 'studata'

urlpatterns = [
    path('picture/<str:ssid>/', views.student_picture, name='picture'),
]
