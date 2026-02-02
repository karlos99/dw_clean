from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Role, School

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

admin.site.register(User, CustomUserAdmin)
admin.site.register(Role)
admin.site.register(School)
