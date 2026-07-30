from django.contrib import admin
from .models import (
    Accommodation,
    AccommodationOption,
    Assessment,
    AssessmentCriteria,
    StudentAccommodation,
    StudentAssessmentCompletion,
)


class AssessmentCriteriaInline(admin.TabularInline):
    model = AssessmentCriteria
    extra = 1


class AccommodationOptionInline(admin.TabularInline):
    model = AccommodationOption
    extra = 1


@admin.register(Accommodation)
class AccommodationAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'category', 'description', 'question_text', 'order')
    list_filter = ('category',)
    search_fields = ('name', 'code', 'category', 'description')
    inlines = [AccommodationOptionInline]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'school_year', 'is_active', 'created_at')
    list_filter = ('school_year', 'subject', 'is_active')
    search_fields = ('name', 'subject', 'school_year')
    filter_horizontal = ('supported_options',)
    inlines = [AssessmentCriteriaInline]


@admin.register(StudentAccommodation)
class StudentAccommodationAdmin(admin.ModelAdmin):
    list_display = ('student_annual_key', 'updated_at', 'updated_by')
    search_fields = ('student_annual_key',)
    filter_horizontal = ('options',)


@admin.register(StudentAssessmentCompletion)
class StudentAssessmentCompletionAdmin(admin.ModelAdmin):
    list_display = ('student_annual_key', 'assessment', 'is_completed', 'updated_at', 'updated_by')
    list_filter = ('is_completed', 'assessment')
    search_fields = ('student_annual_key',)
