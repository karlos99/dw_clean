from django.contrib import admin
from .models import ProjectConfig


@admin.register(ProjectConfig)
class ProjectConfigAdmin(admin.ModelAdmin):
    list_display = ('pk', 'default_school_year', 'updated_by', 'updated_at')
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        # Only one row ever; if it already exists, block new creation via Django admin
        return not ProjectConfig.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False

