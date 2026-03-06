from django.urls import path

from .views import (
    completion_by_school,
    completion_by_teacher,
    mathr_home,
    mathr_student_list,
    mathr_teacher_periods,
    save_recommendation,
    student_marks_detail,
)

app_name = 'mathr'

urlpatterns = [
    # Full page
    path('', mathr_home, name='home'),

    # HTMX partials / API
    path('api/students/', mathr_student_list, name='student-list'),
    path('api/periods/', mathr_teacher_periods, name='teacher-periods'),
    path('api/save/', save_recommendation, name='save-recommendation'),
    path('api/student/<str:stu_annual_key>/', student_marks_detail, name='student-detail'),
    path('api/completion/', completion_by_school, name='completion-by-school'),
    path('api/completion/<str:state_school_id>/', completion_by_teacher, name='completion-by-teacher'),
]
