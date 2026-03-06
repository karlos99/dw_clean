from django.conf import settings
from django.db import models


class MathRecommendation(models.Model):
    """Stores a teacher's math course recommendation for an 8th-grade student."""

    stu_annual_key = models.CharField(max_length=100)
    ssid = models.CharField(max_length=20)
    school_year = models.CharField(max_length=20)
    state_school_id = models.CharField(max_length=20, db_index=True)
    staff_employee_id = models.CharField(max_length=50, blank=True, db_index=True)
    math_selection = models.CharField(max_length=100)
    selected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='math_recommendations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mathr_recommendation'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['stu_annual_key', 'school_year'],
                name='unique_mathr_per_student_year',
            ),
        ]
        indexes = [
            models.Index(fields=['school_year', 'state_school_id']),
            models.Index(fields=['stu_annual_key', 'school_year']),
        ]

    def __str__(self) -> str:
        return f'{self.ssid} → {self.math_selection} ({self.school_year})'
