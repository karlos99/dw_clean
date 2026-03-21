from django.urls import path
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
    path('api/column-structures/', load_column_structures, name='load_column_structures'),
]
