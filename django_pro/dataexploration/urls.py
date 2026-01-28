from django.urls import path
from . import views
from .views import (
    StudentWithTestsListView,
    StudentTestSummaryView,
    StudentTestSummaryPartialView,
    warm_student_test_summary_cache,
    load_students,
    load_column_structures,
    manage_default_columns,
)

app_name = 'dataexploration'

urlpatterns = [
    path('student-tests/', StudentWithTestsListView.as_view(), name='student_tests_list'),
    path('student-tests/default-columns/', manage_default_columns, name='manage_default_columns'),
    path('student-summary/<str:ssid>/', StudentTestSummaryView.as_view(), name='student_test_summary'),
    path('student-summary/<str:ssid>/partial/', StudentTestSummaryPartialView.as_view(), name='student_test_summary_partial'),
    path('student-summary/warm/', warm_student_test_summary_cache, name='student_test_summary_warm'),
    path('api/students/', load_students, name='load_students'),
    path('api/students/export-csv/', views.export_students_csv, name='export_students_csv'),
    path('api/column-structures/', load_column_structures, name='load_column_structures'),
    path('api/folders/', views.folder_browser, name='folder_browser'),
    path('api/save-query/', views.save_query, name='save_query'),
    path('api/save-list/', views.save_student_list, name='save_student_list'),
    path('api/modals/save-query/', views.save_query_modal, name='open_save_query_modal'),
    path('api/modals/save-list/', views.save_student_list_modal, name='open_save_list_modal'),
    path('api/query/<int:query_id>/delete/', views.delete_query, name='delete_query'),
    path('api/query/<int:query_id>/rename/', views.rename_query, name='rename_query'),
    path('api/query/<int:query_id>/share/', views.share_query, name='share_query'),
    path('api/list/<int:list_id>/delete/', views.delete_student_list, name='delete_student_list'),
    path('api/list/<int:list_id>/rename/', views.rename_student_list, name='rename_student_list'),
    path('api/modals/rename/', views.rename_modal, name='rename_modal'),
    path('api/modals/share/', views.share_modal, name='share_modal'),
    path('api/modals/create-folder/', views.create_folder_modal, name='create_folder_modal'),
    path('api/modals/create-folder/<int:parent_id>/', views.create_folder_modal, name='create_folder_modal_with_parent'),
    path('api/folder/create/', views.create_folder, name='create_folder'),
]
