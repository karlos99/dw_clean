"""
Access control decorators and mixins for dataLoader app.
Only Project Admin role should have access.
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


def project_admin_required(view_func):
    """
    Decorator that checks if user has Project Admin role.
    Redirects to home with error message if not authorized.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('account_login')
        
        if not _is_project_admin(request.user):
            messages.error(request, "Access denied. This feature requires Project Admin role.")
            return redirect('landing:home')
        
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view


class ProjectAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    Mixin for class-based views that require Project Admin role.
    """
    
    def test_func(self):
        return _is_project_admin(self.request.user)
    
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        messages.error(self.request, "Access denied. This feature requires Project Admin role.")
        return redirect('landing:home')


def _is_project_admin(user) -> bool:
    """
    Check if user has Project Admin role.
    
    Returns True if:
    - User is superuser
    - User's active_role is 'Project Admin'
    - User has 'Project Admin' in their roles
    """
    if user.is_superuser:
        return True
    
    # Check active role (set by middleware)
    if hasattr(user, 'active_role') and user.active_role:
        if user.active_role.name == 'Project Admin':
            return True
    
    # Check all roles
    if user.roles.filter(name='Project Admin').exists():
        return True
    
    return False
