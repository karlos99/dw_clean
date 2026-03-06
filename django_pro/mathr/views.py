from .view_modules.student_views import mathr_home, mathr_student_list, mathr_teacher_periods
from .view_modules.recommendation_views import save_recommendation
from .view_modules.completion_views import completion_by_school, completion_by_teacher
from .view_modules.detail_views import student_marks_detail

__all__ = [
    'mathr_home',
    'mathr_student_list',
    'mathr_teacher_periods',
    'save_recommendation',
    'completion_by_school',
    'completion_by_teacher',
    'student_marks_detail',
]
