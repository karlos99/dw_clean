from django.contrib import admin
from .models import StudentCombined, StudentWithTests


@admin.register(StudentCombined)
class StudentCombinedAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'ssid', 'student_id', 'grade', 'school', 'school_year')
    list_filter = ('school_year', 'grade', 'gender', 'race')
    search_fields = ('ssid', 'student_id', 'first_name', 'last_name')
    ordering = ('-school_year', 'last_name', 'first_name')


@admin.register(StudentWithTests)
class StudentWithTestsAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'ssid', 'grade', 'school_year', 'test_name', 'subject', 'pl', 'ss')
    list_filter = ('school_year', 'grade', 'test_name', 'subject', 'pl')
    search_fields = ('ssid', 'student_id', 'first_name', 'last_name', 'test_name')
    readonly_fields = ('id', 'stu_annual_key', 'test_id', 'test_group_key')
    ordering = ('-school_year', 'last_name', 'first_name', 'test_name')
    
    fieldsets = (
        ('Student Information', {
            'fields': ('stu_annual_key', 'ssid', 'student_id', 'first_name', 'middle_name', 
                      'last_name', 'grade', 'school', 'school_state_id', 'school_year')
        }),
        ('Demographics', {
            'fields': ('gender', 'race', 'birth_date', 'status'),
            'classes': ('collapse',)
        }),
        ('Programs & Services', {
            'fields': ('ell_status', 'esl', 'sped', 'gate', 'in_504', 'dli', 'ltel', 
                      'sed', 'foster', 'homeless', 'migrant'),
            'classes': ('collapse',)
        }),
        ('Test Information', {
            'fields': ('test_id', 'test_group_key', 'test_name', 'subject', 'test_window')
        }),
        ('Test Results', {
            'fields': ('pl', 'ss', 'other_info')
        }),
    )
