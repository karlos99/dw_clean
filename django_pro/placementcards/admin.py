from django.contrib import admin

from .models import (
    ClassroomStatisticPreference,
    NextYearClassroom,
    PlacementCard,
    PlacementTag,
    QuestionTemplate,
    StudentPlacement,
)


@admin.register(PlacementTag)
class PlacementTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'school', 'color', 'is_active', 'display_order', 'created_at')
    list_filter = ('school', 'is_active')
    search_fields = ('name', 'description', 'school__school_name')


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ('question_text', 'school', 'question_category', 'question_type', 'is_active', 'display_order')
    list_filter = ('school', 'question_category', 'question_type', 'is_active')
    search_fields = ('question_text',)


@admin.register(PlacementCard)
class PlacementCardAdmin(admin.ModelAdmin):
    list_display = ('ssid', 'school_year', 'is_complete', 'completed_by', 'last_updated')
    list_filter = ('school_year', 'is_complete')
    search_fields = ('ssid', 'stu_annual_key')
    filter_horizontal = ('tags',)


class StudentPlacementInline(admin.TabularInline):
    model = StudentPlacement
    extra = 0
    readonly_fields = ('student_ssid', 'placed_by', 'placed_at')


@admin.register(NextYearClassroom)
class NextYearClassroomAdmin(admin.ModelAdmin):
    list_display = ('classroom_name', 'school', 'school_year', 'grade', 'assigned_teacher_name', 'max_capacity')
    list_filter = ('school', 'school_year', 'grade')
    search_fields = ('classroom_name', 'assigned_teacher_name')
    inlines = [StudentPlacementInline]


@admin.register(StudentPlacement)
class StudentPlacementAdmin(admin.ModelAdmin):
    list_display = ('student_ssid', 'school_year', 'classroom', 'placed_by', 'placed_at')
    list_filter = ('school_year', 'classroom__school')
    search_fields = ('student_ssid',)


@admin.register(ClassroomStatisticPreference)
class ClassroomStatisticPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'updated_at')
