from django.contrib import admin
from .models import NextYearPlacement


@admin.register(NextYearPlacement)
class NextYearPlacementAdmin(admin.ModelAdmin):
    list_display = ('ssid', 'student_id', 'school_year', 'next_year_school_state_id', 'next_year_school_name')
    list_filter = ('school_year', 'next_year_school_state_id')
    search_fields = ('ssid', 'student_id', 'next_year_school_name')
    ordering = ('-school_year', 'ssid')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
