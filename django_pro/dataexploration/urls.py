from django.urls import path
from .views import (
    StudentWithTestsListView,
    StudentWithTestsDetailView,
    StudentTestSummaryView,
    load_students,
    load_column_structures,
)

app_name = 'dataexploration'

urlpatterns = [
    path('student-tests/', StudentWithTestsListView.as_view(), name='student_tests_list'),
    path('student-tests/<str:pk>/', StudentWithTestsDetailView.as_view(), name='student_test_detail'),
    path('student-summary/<str:ssid>/', StudentTestSummaryView.as_view(), name='student_test_summary'),
    path('api/students/', load_students, name='load_students'),
    path('api/column-structures/', load_column_structures, name='load_column_structures'),
]
