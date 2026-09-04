from django.contrib import admin
from .models import (
    Accommodation,
    AccommodationOption,
    AccommodationResource,
    Assessment,
    AssessmentCriteria,
    DesiExportConfiguration,
    DesiSchoolAccess,
    StudentAccommodation,
    StudentAssessmentCompletion,
    SupportRule,
    SupportRuleCriteria,
)


class AssessmentCriteriaInline(admin.TabularInline):
    model = AssessmentCriteria
    extra = 1


class AccommodationOptionInline(admin.TabularInline):
    model = AccommodationOption
    extra = 1


class AccommodationResourceInline(admin.TabularInline):
    model = AccommodationResource
    extra = 0


class SupportRuleCriteriaInline(admin.TabularInline):
    model = SupportRuleCriteria
    extra = 1


@admin.register(Accommodation)
class AccommodationAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'tag', 'column_letter', 'category', 'description', 'question_text', 'order')
    list_filter = ('category', 'tag')
    search_fields = ('name', 'code', 'tag', 'column_letter', 'category', 'description')
    inlines = [AccommodationOptionInline, AccommodationResourceInline]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'tag', 'is_active', 'created_at')
    list_filter = ('subject', 'tag', 'is_active')
    search_fields = ('name', 'subject', 'tag')
    filter_horizontal = ('supported_options',)
    inlines = [AssessmentCriteriaInline]


@admin.register(SupportRule)
class SupportRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    filter_horizontal = ('supports',)
    inlines = [SupportRuleCriteriaInline]


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


@admin.register(DesiSchoolAccess)
class DesiSchoolAccessAdmin(admin.ModelAdmin):
    list_display = ('school_state_id', 'teachers_can_edit', 'updated_at', 'updated_by')
    search_fields = ('school_state_id',)


@admin.register(DesiExportConfiguration)
class DesiExportConfigurationAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'updated_at')
    search_fields = ('name', 'owner__email')
