from .models import Role, UsageSession, PageVisit
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta
from django.apps import apps
# Use apps.get_model to avoid potential circular imports during startup if safe
# form studata.models import School as StudataSchool - safer to do lazy load or try/except but typically okay here.
# Actually, let's use apps.get_model for safety inside the method or file level if we know app order.
# Users app is usually loaded early. Studata is loaded.

class ActiveRoleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # --- Impersonation Context ---
            if request.session.get('impersonator_id'):
                request.user.is_impersonated = True
            else:
                request.user.is_impersonated = False

            # --- Role Context ---
            active_role_id = request.session.get('active_role_id')
            
            if active_role_id:
                if active_role_id == 'all': # Explicit "All" selection
                    request.user.active_role = None
                else:
                    try:
                        # Verify the user actually possesses this role
                        role = request.user.roles.get(id=active_role_id)
                        request.user.active_role = role
                    except Role.DoesNotExist:
                        # Invalid role for this user, clear session
                        request.session.pop('active_role_id', None)
                        self._set_default_role(request)
            else:
                self._set_default_role(request)

            # --- School Context ---
            StudataSchool = apps.get_model('studata', 'School')
            
            # Ensure we treat it as a list of strings
            user_schools_list = request.user.schools if isinstance(request.user.schools, list) else []
            unrestricted = self._is_unrestricted(request.user)

            if user_schools_list or unrestricted:
                # Pre-fetch available schools for the UI
                # We order by name for the dropdown
                if user_schools_list:
                    request.user.available_schools = StudataSchool.objects.filter(state_id__in=user_schools_list).order_by('school_name')
                else:
                    request.user.available_schools = StudataSchool.objects.order_by('school_name')

                active_school_id = request.session.get('active_school_id')
                if active_school_id is None:
                    prefs = request.user.preferences if isinstance(request.user.preferences, dict) else {}
                    active_school_id = prefs.get('active_school_id')

                if active_school_id:
                     if active_school_id == 'all':
                          request.user.active_school = None
                     elif active_school_id in user_schools_list or unrestricted:
                          # It's in the allowed list, get the object
                          # We can find it from the prefetched queryset to save a DB hit if we iterate users.available_schools
                          # But simple filter is fine.
                          try:
                              request.user.active_school = (
                                  StudataSchool.objects
                                  .filter(state_id=active_school_id)
                                  .order_by('school_name', 'dw_schoolkey')
                                  .first()
                              )
                              if request.user.active_school is None:
                                  raise StudataSchool.DoesNotExist
                          except StudataSchool.DoesNotExist:
                              request.session.pop('active_school_id', None)
                              request.user.active_school = None
                     else:
                          # Security check: User tried to set a school they don't have access to
                          request.session.pop('active_school_id', None)
                          request.user.active_school = None
                else:
                     request.user.active_school = None
            else:
                request.user.available_schools = []
                request.user.active_school = None
        
        response = self.get_response(request)
        return response

    def _set_default_role(self, request):
        """Set the highest priority role as default active role."""
        roles = list(request.user.roles.all())
        if not roles:
            request.user.active_role = None
            return

        # Define priority (index 0 is highest)
        # Assuming typical hierarchy. Adjust as needed.
        PRIORITY = ['Project Admin', 'District Admin', 'School Admin', 'Teacher']
        
        best_role = None
        best_idx = 999
        
        for role in roles:
            if role.name in PRIORITY:
                idx = PRIORITY.index(role.name)
                if idx < best_idx:
                    best_idx = idx
                    best_role = role
            elif best_role is None:
                # If no priority match yet, take any role
                best_role = role
        
        if best_role:
             request.user.active_role = best_role
             # We do NOT save to session here, so it's a transient default.
             # If we save to session, 'All' becomes impossible to reach by default without explicit 'all' flag.
        else:
             request.user.active_role = None

    @staticmethod
    def _is_unrestricted(user):
        """Superusers and Project/District Admins can act across all schools."""
        if user.is_superuser:
            return True
        return user.roles.filter(name__in=['Project Admin', 'District Admin']).exists()


class UsageTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return response

        if request.session.get('impersonator_id'):
            try:
                if not request.session.session_key:
                    request.session.save()
                session_key = request.session.session_key
                now = timezone.now()
                usage_session = UsageSession.objects.filter(
                    user=request.user,
                    session_key=session_key,
                    logout_at__isnull=True,
                ).order_by('-login_at').first()
                if usage_session:
                    self._close_active_visit(request, usage_session, now)
                    usage_session.logout_at = now
                    usage_session.last_activity_at = now
                    usage_session.total_seconds = max(0, int((usage_session.logout_at - usage_session.login_at).total_seconds()))
                    usage_session.save(update_fields=['logout_at', 'last_activity_at', 'total_seconds'])
            except Exception:
                pass
            return response

        path = request.path or ''
        if self._is_ignored_path(path):
            return response

        try:
            if not request.session.session_key:
                request.session.save()
            session_key = request.session.session_key

            now = timezone.now()
            idle_minutes = getattr(settings, 'USAGE_TRACKING_IDLE_MINUTES', 30)
            idle_delta = timedelta(minutes=idle_minutes)

            usage_session = UsageSession.objects.filter(
                user=request.user,
                session_key=session_key,
                logout_at__isnull=True,
            ).order_by('-login_at').first()

            if usage_session and usage_session.last_activity_at and now - usage_session.last_activity_at > idle_delta:
                self._close_active_visit(request, usage_session, now)
                usage_session.logout_at = now
                usage_session.total_seconds = max(0, int((usage_session.logout_at - usage_session.login_at).total_seconds()))
                usage_session.save(update_fields=['logout_at', 'total_seconds'])
                usage_session = None

            if not usage_session:
                usage_session = UsageSession.objects.create(
                    user=request.user,
                    session_key=session_key,
                    login_at=now,
                    last_activity_at=now,
                )
                request.session['usage_visit_id'] = None
                request.session['usage_visit_path'] = None

            current_visit_id = request.session.get('usage_visit_id')
            current_visit_path = request.session.get('usage_visit_path')

            if current_visit_id and current_visit_path != path:
                self._close_active_visit(request, usage_session, now)
                current_visit_id = None
                current_visit_path = None

            if not current_visit_id:
                visit = PageVisit.objects.create(
                    session=usage_session,
                    path=path,
                    started_at=now,
                )
                request.session['usage_visit_id'] = visit.id
                request.session['usage_visit_path'] = path

            usage_session.last_activity_at = now
            usage_session.save(update_fields=['last_activity_at'])
        except Exception:
            # Best-effort tracking; do not break requests
            pass

        return response

    @staticmethod
    def _is_ignored_path(path: str) -> bool:
        if not path:
            return True
        ignored_prefixes = (
            '/static/',
            '/media/',
            '/admin/',
        )
        ignored_paths = (
            '/favicon.ico',
        )
        if path in ignored_paths:
            return True
        return path.startswith(ignored_prefixes)

    @staticmethod
    def _close_active_visit(request, usage_session, now):
        visit_id = request.session.get('usage_visit_id')
        if not visit_id:
            return
        visit = PageVisit.objects.filter(id=visit_id, session=usage_session).first()
        if not visit or visit.ended_at:
            return
        visit.ended_at = now
        visit.duration_seconds = max(0, int((visit.ended_at - visit.started_at).total_seconds()))
        visit.save(update_fields=['ended_at', 'duration_seconds'])


class RedirectForbiddenToHomeMiddleware:
    """Redirect full-page 403 responses for authenticated users to home.

    This avoids showing raw Forbidden pages after role switches while preserving
    403 behavior for API/AJAX/HTMX requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if response.status_code != 403:
            return response

        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return response

        if request.method != 'GET':
            return response

        # Preserve machine/client-side 403 handling.
        if request.headers.get('HX-Request') == 'true':
            return response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return response
        if request.path.startswith('/api/'):
            return response

        accept_header = (request.headers.get('Accept') or '').lower()
        if 'application/json' in accept_header and 'text/html' not in accept_header:
            return response

        messages.info(request, 'Your current role does not have access to that page. Redirected to home.')
        return redirect('landing:index')
