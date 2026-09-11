from django.contrib import admin
from .models import (
    Accommodation,
    AccommodationCategory,
    AccommodationOption,
    AccommodationResource,
    Assessment,
    AssessmentCriteria,
    DesiExportConfiguration,
    DesiSchoolAccess,
    DesiSeis,
    DesiSeisDisplaySettings,
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


@admin.register(AccommodationCategory)
class AccommodationCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'order', 'description')
    list_editable = ('order',)
    search_fields = ('name', 'description')


@admin.register(Accommodation)
class AccommodationAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'tag', 'column_letter', 'category', 'section_category', 'description', 'question_text', 'order')
    list_filter = ('category', 'section_category', 'tag')
    search_fields = ('name', 'code', 'tag', 'column_letter', 'category', 'section_category__name', 'description')
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


@admin.register(DesiSeis)
class DesiSeisAdmin(admin.ModelAdmin):
    list_display = ('ssid', 'test_group', 'id')
    search_fields = ('ssid', 'id')
    list_filter = ('test_group',)
    readonly_fields = ('id', 'ssid', 'test_group', 'settings', 'assessments')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DesiSeisDisplaySettings)
class DesiSeisDisplaySettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'updated_by', 'updated_at')
    filter_horizontal = ('assessments',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DesiSchoolAccess)
class DesiSchoolAccessAdmin(admin.ModelAdmin):
    list_display = ('school_state_id', 'teachers_can_edit', 'updated_at', 'updated_by')
    search_fields = ('school_state_id',)


@admin.register(DesiExportConfiguration)
class DesiExportConfigurationAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'updated_at')
    search_fields = ('name', 'owner__email')
