"""Shared mixins and helpers for dataviz views."""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse


def is_project_admin(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if hasattr(user, 'active_role') and user.active_role and user.active_role.name == 'Project Admin':
        return True
    if hasattr(user, 'roles') and user.roles.filter(name='Project Admin').exists():
        return True
    return False


FORBIDDEN = JsonResponse({'error': 'Forbidden'}, status=403)


class ProjectAdminMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restrict a class-based view to Project Admins only."""

    def test_func(self):
        return is_project_admin(self.request.user)
