from django.contrib import admin

from .models import MathRecommendation


@admin.register(MathRecommendation)
class MathRecommendationAdmin(admin.ModelAdmin):
    list_display = ('ssid', 'math_selection', 'state_school_id', 'school_year', 'staff_employee_id', 'updated_at')
    list_filter = ('school_year', 'state_school_id', 'math_selection')
    search_fields = ('ssid', 'stu_annual_key', 'staff_employee_id')
    readonly_fields = ('created_at', 'updated_at')
