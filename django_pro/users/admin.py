from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Role, School, RoleAssignment
from .models_pages import Page, NavbarConfig


class RoleAssignmentInline(admin.TabularInline):
    model = RoleAssignment
    fk_name = 'user'
    extra = 0
    autocomplete_fields = ('role', 'fallback_role')
    fields = (
        'role',
        'expires_at',
        'fallback_role',
        'limited_ssids',
        'limited_student_ids',
    )
    verbose_name_plural = 'Role assignments (expiration, fallback, limited students)'


class CustomUserAdmin(UserAdmin):
    model = User
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('name', 'job_title', 'staff_id', 'schools', 'preferences')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('name', 'job_title', 'staff_id', 'schools', 'preferences', 'email')}),
    )
    list_display = ['username', 'email', 'name', 'staff_id', 'get_roles', 'get_school', 'is_staff']

    def get_inlines(self, request, obj):
        # Role assignments live in the inline so expiration/fallback/limited
        # student metadata is never wiped by a plain M2M save.
        return [RoleAssignmentInline]

    def get_roles(self, obj):
        return ", ".join([r.name for r in obj.effective_roles()])
    get_roles.short_description = 'Roles'

    def get_school(self, obj):
        return obj.first_school_name
    get_school.short_description = 'School'

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

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_student_limited', 'description')
    list_filter = ('is_student_limited',)
    search_fields = ('name',)


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'expires_at', 'fallback_role', 'is_student_limited')
    list_filter = ('role', 'expires_at')
    search_fields = ('user__email', 'user__name', 'role__name')
    autocomplete_fields = ('user', 'role', 'fallback_role')

    @admin.display(boolean=True, description='Limited students')
    def is_student_limited(self, obj):
        return obj.role.is_student_limited


admin.site.register(User, CustomUserAdmin)
admin.site.register(School)
admin.site.register(Page, PageAdmin)


@admin.register(NavbarConfig)
class NavbarConfigAdmin(admin.ModelAdmin):
    list_display = ('pk', 'navbar_style', 'sticky', 'label_case', 'submenu_style', 'updated_by', 'updated_at')
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        return not NavbarConfig.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False
