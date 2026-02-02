from .models import Role
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
            
            if user_schools_list:
                # Pre-fetch available schools for the UI
                # We order by name for the dropdown
                request.user.available_schools = StudataSchool.objects.filter(state_id__in=user_schools_list).order_by('school_name')
                
                active_school_id = request.session.get('active_school_id')
                
                if active_school_id:
                     if active_school_id == 'all':
                          request.user.active_school = None
                     elif active_school_id in user_schools_list:
                          # It's in the allowed list, get the object
                          # We can find it from the prefetched queryset to save a DB hit if we iterate users.available_schools
                          # But simple filter is fine.
                          try:
                              request.user.active_school = StudataSchool.objects.get(state_id=active_school_id)
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
