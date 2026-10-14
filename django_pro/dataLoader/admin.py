from django.contrib import admin
from django.utils.html import format_html
from .models import (
    SurveyDefinition, SurveyQuestion, SurveyResponse, ImportLog,
    ResponseScaleLabel, SurveyTopic, SurveyColorPalette, APExamCode, APAwardType,
    APImportLog, APStudentAward, APExamResult,
    SATImportLog, SATResult, SATSettings,
)


@admin.register(SurveyTopic)
class SurveyTopicAdmin(admin.ModelAdmin):
    list_display = ['name', 'color']
    search_fields = ['name']


@admin.register(SurveyColorPalette)
class SurveyColorPaletteAdmin(admin.ModelAdmin):
    list_display = ['name', 'updated_at']
    search_fields = ['name']


@admin.register(SurveyDefinition)
class SurveyDefinitionAdmin(admin.ModelAdmin):
    list_display = ['name', 'survey_type', 'school_year', 'testing_window', 
                    'grade_range', 'question_count', 'is_active', 'created_at']
    list_filter = ['school_year', 'testing_window', 'survey_type', 'is_active']
    search_fields = ['name', 'survey_type', 'description']
    readonly_fields = ['created_at', 'updated_at', 'created_by']
    ordering = ['-school_year', '-created_at']
    
    fieldsets = (
        (None, {
            'fields': ('name', 'survey_type', 'source_system')
        }),
        ('Time Period', {
            'fields': ('school_year', 'testing_window', 'grade_range')
        }),
        ('Details', {
            'fields': ('description', 'version', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at', 'created_by'),
            'classes': ('collapse',)
        }),
    )


@admin.register(SurveyQuestion)
class SurveyQuestionAdmin(admin.ModelAdmin):
    list_display = ['short_question', 'survey_definition', 'topic', 'order', 
                    'response_scale', 'is_active']
    list_filter = ['survey_definition__school_year', 'survey_definition', 'topic', 'is_active']
    search_fields = ['question_text', 'question_key']
    readonly_fields = ['question_key', 'created_at']
    ordering = ['survey_definition', 'order']
    
    def short_question(self, obj):
        return obj.question_text[:60] + '...' if len(obj.question_text) > 60 else obj.question_text
    short_question.short_description = 'Question'
    
    def response_scale(self, obj):
        return f"1-{obj.response_scale_max}"
    response_scale.short_description = 'Scale'


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ['name', 'school_year', 'testing_window', 'status_badge', 
                    'match_rate', 'uploaded_by', 'uploaded_at']
    list_filter = ['status', 'school_year', 'testing_window', 'uploaded_at']
    search_fields = ['name', 'filename', 'uploaded_by__username']
    readonly_fields = ['uploaded_at', 'completed_at', 'file_hash', 
                       'total_rows', 'records_processed', 'records_created', 
                       'records_updated', 'records_skipped', 'students_matched',
                       'students_unmatched', 'questions_detected', 'error_count']
    ordering = ['-uploaded_at']
    
    fieldsets = (
        (None, {
            'fields': ('name', 'filename', 'survey_definition')
        }),
        ('Time Period', {
            'fields': ('school_year', 'testing_window')
        }),
        ('Upload Info', {
            'fields': ('uploaded_by', 'uploaded_at', 'completed_at', 'status', 'file_hash')
        }),
        ('Statistics', {
            'fields': ('total_rows', 'records_processed', 'records_created', 
                       'records_updated', 'records_skipped', 'students_matched',
                       'students_unmatched', 'questions_detected')
        }),
        ('Errors', {
            'fields': ('error_count', 'error_log', 'warnings'),
            'classes': ('collapse',)
        }),
    )
    
    def status_badge(self, obj):
        colors = {
            'PENDING': 'orange',
            'PROCESSING': 'blue',
            'COMPLETED': 'green',
            'FAILED': 'red',
            'PARTIAL': 'yellow',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; padding: 3px 8px; border-radius: 3px; color: white;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    
    def match_rate(self, obj):
        total = obj.students_matched + obj.students_unmatched
        if total == 0:
            return '-'
        rate = (obj.students_matched / total) * 100
        return f"{rate:.1f}%"
    match_rate.short_description = 'Match Rate'


@admin.register(SurveyResponse)
class SurveyResponseAdmin(admin.ModelAdmin):
    list_display = ['student_id', 'ssid', 'short_question', 'value', 
                    'text_label', 'school_year']
    list_filter = ['question__survey_definition__school_year', 'question__survey_definition', 'import_log']
    search_fields = ['student_id', 'ssid', 'question__question_text']
    readonly_fields = ['created_at']
    raw_id_fields = ['question', 'import_log']
    ordering = ['-created_at']
    
    def short_question(self, obj):
        text = obj.question.question_text
        return text[:40] + '...' if len(text) > 40 else text
    short_question.short_description = 'Question'
    
    def text_label(self, obj):
        """Get text label from ResponseScaleLabel for display."""
        if obj.value is None:
            return '-'
        label = ResponseScaleLabel.objects.filter(
            question=obj.question, value=obj.value
        ).first()
        return label.label if label else '-'
    text_label.short_description = 'Text'
    
    def school_year(self, obj):
        return obj.import_log.school_year if obj.import_log else '-'
    school_year.short_description = 'School Year'


@admin.register(ResponseScaleLabel)
class ResponseScaleLabelAdmin(admin.ModelAdmin):
    list_display = ['question', 'value', 'label']
    list_filter = ['question__survey_definition', 'value']
    search_fields = ['question__question_text', 'label']
    ordering = ['question', 'value']


@admin.register(APExamCode)
class APExamCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['code', 'name']
    ordering = ['code']


@admin.register(APAwardType)
class APAwardTypeAdmin(admin.ModelAdmin):
    list_display = ['code', 'description']
    search_fields = ['code', 'description']
    ordering = ['code']


@admin.register(APImportLog)
class APImportLogAdmin(admin.ModelAdmin):
    list_display = ['name', 'school_year', 'status_badge', 'students_matched',
                    'exams_created', 'exams_skipped', 'uploaded_by', 'uploaded_at']
    list_filter = ['status', 'school_year', 'uploaded_at']
    search_fields = ['name', 'filename', 'uploaded_by__username']
    readonly_fields = ['uploaded_at', 'completed_at', 'file_hash',
                       'total_students', 'exams_detected', 'exams_created',
                       'exams_skipped', 'awards_detected', 'awards_created',
                       'awards_skipped', 'students_matched', 'students_unmatched',
                       'records_processed', 'error_count']
    ordering = ['-uploaded_at']

    fieldsets = (
        (None, {
            'fields': ('name', 'filename', 'school_year')
        }),
        ('Upload Info', {
            'fields': ('uploaded_by', 'uploaded_at', 'completed_at', 'status', 'file_hash')
        }),
        ('Statistics', {
            'fields': ('total_students', 'students_matched', 'students_unmatched',
                       'exams_detected', 'exams_created', 'exams_skipped',
                       'awards_detected', 'awards_created', 'awards_skipped')
        }),
        ('Manual Overrides', {
            'fields': ('manual_overrides',),
            'classes': ('collapse',)
        }),
        ('Errors', {
            'fields': ('error_count', 'error_log', 'warnings'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'PENDING': 'orange',
            'PROCESSING': 'blue',
            'COMPLETED': 'green',
            'FAILED': 'red',
            'PARTIAL': 'yellow',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; padding: 3px 8px; border-radius: 3px; color: white;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(APStudentAward)
class APStudentAwardAdmin(admin.ModelAdmin):
    list_display = ['ap_number', 'ssid', 'award_type', 'award_year', 'import_log']
    list_filter = ['award_year', 'award_type', 'import_log__school_year']
    search_fields = ['ap_number', 'ssid']
    raw_id_fields = ['import_log', 'award_type']
    ordering = ['-award_year', 'ap_number']


@admin.register(APExamResult)
class APExamResultAdmin(admin.ModelAdmin):
    list_display = ['ap_number', 'ssid', 'exam', 'admin_year', 'exam_grade', 'import_log']
    list_filter = ['admin_year', 'exam', 'exam_grade', 'import_log__school_year']
    search_fields = ['ap_number', 'ssid', 'ai_institution_name']
    raw_id_fields = ['import_log', 'exam']
    ordering = ['-admin_year', 'ap_number']


@admin.register(SATImportLog)
class SATImportLogAdmin(admin.ModelAdmin):
    list_display = ['name', 'school_year', 'status_badge', 'students_matched',
                    'records_created', 'records_skipped', 'uploaded_by', 'uploaded_at']
    list_filter = ['status', 'school_year', 'uploaded_at']
    search_fields = ['name', 'filename', 'uploaded_by__username']
    readonly_fields = ['uploaded_at', 'completed_at', 'file_hash',
                       'total_students', 'records_detected', 'records_created',
                       'records_skipped', 'students_matched', 'students_unmatched',
                       'records_processed', 'error_count']
    ordering = ['-uploaded_at']

    fieldsets = (
        (None, {'fields': ('name', 'filename', 'school_year')}),
        ('Upload Info', {'fields': ('uploaded_by', 'uploaded_at', 'completed_at', 'status', 'file_hash')}),
        ('Statistics', {'fields': ('total_students', 'students_matched', 'students_unmatched',
                                   'records_detected', 'records_created', 'records_skipped')}),
        ('Publishing', {'fields': ('is_published', 'published_at', 'published_by')}),
        ('Manual Overrides', {'fields': ('manual_overrides',), 'classes': ('collapse',)}),
        ('Errors', {'fields': ('error_count', 'error_log', 'warnings'), 'classes': ('collapse',)}),
    )

    def status_badge(self, obj):
        colors = {
            'PENDING': 'orange', 'PROCESSING': 'blue', 'COMPLETED': 'green',
            'FAILED': 'red', 'PARTIAL': 'yellow',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; padding: 3px 8px; border-radius: 3px; color: white;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = 'Status'


@admin.register(SATResult)
class SATResultAdmin(admin.ModelAdmin):
    list_display = ['last_name', 'first_name', 'ssid', 'state_student_id', 'test_date',
                    'total_score', 'ebrw_score', 'math_score', 'school_name']
    list_filter = ['school_year', 'test_date', 'import_log__school_year']
    search_fields = ['last_name', 'first_name', 'ssid', 'state_student_id', 'cb_id']
    raw_id_fields = ['import_log']
    ordering = ['-test_date', 'last_name']


@admin.register(SATSettings)
class SATSettingsAdmin(admin.ModelAdmin):
    list_display = ['result_mode', 'highest_metric', 'updated_at', 'updated_by']
