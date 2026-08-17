from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Role, School
from .models_pages import Page, NavbarConfig

class CustomUserAdmin(UserAdmin):
    model = User
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('name', 'job_title', 'staff_id', 'roles', 'schools', 'preferences')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('name', 'job_title', 'staff_id', 'roles', 'schools', 'preferences', 'email')}),
    )
    list_display = ['username', 'email', 'name', 'staff_id', 'get_roles', 'is_staff']
    
    def get_roles(self, obj):
        return ", ".join([r.name for r in obj.roles.all()])
    get_roles.short_description = 'Roles'

class PageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'url_path', 'status', 'icon')
    list_filter = ('status',)
    search_fields = ('name', 'slug', 'description')
    
    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Check if user is Project Admin
        # IMPORTANT: This assumes the role 'Project Admin' exists and is assigned. 
        # Safely checking since 'roles' is M2M
        if hasattr(request.user, 'roles') and request.user.roles.filter(name='Project Admin').exists():
            return True
        return False
        
    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_model_perms(self, request):
        if self.has_module_permission(request):
            return super().get_model_perms(request)
        return {}

admin.site.register(User, CustomUserAdmin)
admin.site.register(Role)
admin.site.register(School)
admin.site.register(Page, PageAdmin)


@admin.register(NavbarConfig)
class NavbarConfigAdmin(admin.ModelAdmin):
    list_display = ('pk', 'sticky', 'label_case', 'submenu_style', 'updated_by', 'updated_at')
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        return not NavbarConfig.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False
