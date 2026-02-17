from django.contrib import admin
from .models import StudentCombined, StudentWithTests, DefaultColumnSelection, ReportFolder, FolderShare, SavedQuery, StudentList, StudentListItem, QueryShare


@admin.register(ReportFolder)
class ReportFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'folder_type', 'owner', 'school', 'parent', 'created_at')
    list_filter = ('folder_type', 'created_at')
    search_fields = ('name', 'description')

    class FolderShareInline(admin.TabularInline):
        model = FolderShare
        extra = 1
        autocomplete_fields = ['shared_with']

    inlines = [FolderShareInline]


@admin.register(SavedQuery)
class SavedQueryAdmin(admin.ModelAdmin):
    list_display = ('name', 'query_type', 'privacy', 'folder', 'owner', 'execution_count', 'updated_at')
    list_filter = ('query_type', 'privacy', 'updated_at', 'is_active')
    search_fields = ('name', 'description')
    raw_id_fields = ('folder', 'owner')

    class QueryShareInline(admin.TabularInline):
        model = QueryShare
        fk_name = 'query'
        extra = 1
        autocomplete_fields = ['shared_with', 'shared_by']

    inlines = [QueryShareInline]


@admin.register(QueryShare)
class QueryShareAdmin(admin.ModelAdmin):
    list_display = ('query', 'shared_with', 'can_edit', 'can_reshare', 'shared_by', 'shared_at')
    list_filter = ('can_edit', 'can_reshare', 'shared_at')
    search_fields = ('query__name', 'shared_with__email', 'shared_by__email')
    raw_id_fields = ('query', 'shared_with', 'shared_by')


@admin.register(StudentList)
class StudentListAdmin(admin.ModelAdmin):
    list_display = ('name', 'folder', 'owner', 'updated_at')
    list_filter = ('updated_at', 'folder__folder_type')
    search_fields = ('name', 'description')
    raw_id_fields = ('folder', 'owner')

    class StudentListItemInline(admin.TabularInline):
        model = StudentListItem
        extra = 1

    inlines = [StudentListItemInline]


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


@admin.register(DefaultColumnSelection)
class DefaultColumnSelectionAdmin(admin.ModelAdmin):
    list_display = ('id', 'updated_by', 'updated_at')
    readonly_fields = ('updated_at',)
