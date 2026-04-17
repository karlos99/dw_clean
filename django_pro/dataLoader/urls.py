from django.urls import path
from . import views

app_name = 'dataLoader'

urlpatterns = [
    # Dashboard
    path('', views.DataLoaderDashboardView.as_view(), name='dashboard'),
    
    # Upload flow
    path('upload/', views.UploadView.as_view(), name='upload'),
    path('preview/', views.PreviewView.as_view(), name='preview'),
    
    # Import details and progress
    path('import/<int:pk>/', views.ImportDetailView.as_view(), name='import_detail'),
    path('import/<int:pk>/status/', views.check_import_status, name='import_status'),
    path('import/<int:pk>/progress/', views.ImportProgressView.as_view(), name='import_progress'),
    path('import/<int:pk>/delete/', views.delete_import, name='delete_import'),
    path('import/<int:pk>/quick-publish/', views.quick_toggle_publish_import, name='quick_toggle_publish_import'),
    
    # Global topics management
    path('topics/', views.topic_list, name='topic_list'),
    path('topics/<int:pk>/update/', views.update_topic, name='update_topic'),
    path('topics/<int:pk>/delete/', views.delete_topic, name='delete_topic'),

    # Survey definitions
    path('surveys/', views.SurveyDefinitionListView.as_view(), name='survey_list'),
    path('surveys/<int:pk>/', views.SurveyDefinitionDetailView.as_view(), name='survey_detail'),
    path('surveys/<int:pk>/delete/', views.delete_survey, name='delete_survey'),
    path('surveys/<int:pk>/publish/', views.toggle_publish, name='toggle_publish'),
    path('surveys/<int:pk>/quick-publish/', views.quick_toggle_publish, name='quick_toggle_publish'),
    path('surveys/<int:pk>/colors/', views.save_response_colors, name='save_response_colors'),
    path('surveys/<int:pk>/topics/', views.manage_survey_topics, name='survey_topics'),
    path('surveys/<int:pk>/topics/existing/', views.existing_survey_topics, name='survey_topics_existing'),
    path('surveys/<int:pk>/topics/<int:topic_pk>/delete/', views.delete_survey_topic, name='delete_survey_topic'),
    path('surveys/<int:pk>/questions/<int:question_pk>/topic/', views.assign_question_topic, name='assign_question_topic'),
]
