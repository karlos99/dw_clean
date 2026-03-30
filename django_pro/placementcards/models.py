from django.conf import settings
from django.db import models

from studata.models import School


class PlacementTag(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='placement_tags')
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=20, default='bg-slate-500')
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='placement_tags_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [
            models.UniqueConstraint(fields=['school', 'name'], name='uniq_placement_tag_school_name'),
        ]
        indexes = [
            models.Index(fields=['school', 'is_active', 'display_order']),
            models.Index(fields=['school', 'name']),
        ]

    def __str__(self):
        return f'{self.school.school_name}: {self.name}'


class QuestionTemplate(models.Model):
    CATEGORY_CHOICES = [
        ('academic', 'Academic'),
        ('behavior', 'Behavior & Study Habits'),
        ('physical', 'Physical & Health'),
        ('attendance', 'Attendance'),
        ('parent', 'Parent Involvement'),
        ('additional', 'Additional Information'),
    ]

    TYPE_CHOICES = [
        ('text', 'Text'),
        ('textarea', 'Textarea'),
        ('checkbox', 'Checkbox'),
        ('choice', 'Choice'),
        ('multiselect', 'Multi Select'),
        ('student_list', 'Student List'),
    ]

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='placement_question_templates')
    question_category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    question_text = models.CharField(max_length=255)
    question_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    field_options = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    requires_detail = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='placement_questions_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['question_category', 'display_order', 'id']
        indexes = [
            models.Index(fields=['school', 'is_active', 'display_order']),
        ]

    def __str__(self):
        return f'{self.school.school_name}: {self.question_text}'


class PlacementCard(models.Model):
    stu_annual_key = models.CharField(max_length=32)
    ssid = models.CharField(max_length=20, db_index=True)
    school_year = models.CharField(max_length=20)
    responses = models.JSONField(default=dict, blank=True)
    tags = models.ManyToManyField(PlacementTag, blank=True, related_name='placement_cards')
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_placement_cards')
    is_complete = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_updated']
        constraints = [
            models.UniqueConstraint(fields=['stu_annual_key', 'school_year'], name='uniq_placement_card_student_year'),
        ]
        indexes = [
            models.Index(fields=['stu_annual_key', 'school_year']),
            models.Index(fields=['completed_by']),
        ]

    def __str__(self):
        return f'{self.ssid} - {self.school_year}'


class NextYearClassroom(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='next_year_classrooms')
    school_year = models.CharField(max_length=20)
    grade = models.CharField(max_length=50)
    classroom_name = models.CharField(max_length=255)
    assigned_teacher_name = models.CharField(max_length=255, blank=True)
    max_capacity = models.IntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='classrooms_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['grade', 'classroom_name']
        constraints = [
            models.UniqueConstraint(fields=['school', 'school_year', 'grade', 'classroom_name'], name='uniq_classroom_school_year_grade_name'),
        ]
        indexes = [
            models.Index(fields=['school', 'school_year', 'grade']),
        ]

    def __str__(self):
        return f'{self.school.school_name} {self.school_year} {self.grade} {self.classroom_name}'


class StudentPlacement(models.Model):
    student_ssid = models.CharField(max_length=20, db_index=True)
    school_year = models.CharField(max_length=20)
    classroom = models.ForeignKey(NextYearClassroom, on_delete=models.CASCADE, related_name='placements')
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='student_placements_made')
    placed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-placed_at']
        constraints = [
            models.UniqueConstraint(fields=['student_ssid', 'school_year'], name='uniq_student_placement_year'),
        ]
        indexes = [
            models.Index(fields=['classroom', 'placed_at']),
        ]

    def __str__(self):
        return f'{self.student_ssid} -> {self.classroom.classroom_name}'


class ClassroomStatisticPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='classroom_stat_prefs')
    preferred_metrics = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user'], name='uniq_classroom_stats_pref_user'),
        ]

    def __str__(self):
        return f'Preference for {self.user.email}'
