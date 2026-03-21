from django.db.models import Q
from .models import ReportFolder

def get_user_roles(user):
    """
    Get the effective roles for the user, respecting active_role context.
    """
    if hasattr(user, 'active_role') and user.active_role:
        return {user.active_role.name}
    return set(user.roles.values_list('name', flat=True))

def _user_has_access_to_user_school(user, user_school):
    """
    Check if user has access to the given users.models.School object.
    Handles both legacy ManyToManyField and new JSONField (list of state_ids).
    """
    if not user_school:
        return False
        
    if isinstance(user.schools, list):
        # New JSONField storage: list of state_ids
        from studata.models import School as StudataSchool
        # Match by name since users.School lacks state_id
        return StudataSchool.objects.filter(state_id__in=user.schools, school_name=user_school.name).exists()
    
    if hasattr(user.schools, 'all'):
        # Legacy M2M
        return user_school in user.schools.all()
        
    return False

def get_user_folders(user):
    """
    Return queryset of folders a user can VIEW.
    """
    if not user.is_authenticated:
        return ReportFolder.objects.none()

    if user.is_superuser:
        return ReportFolder.objects.all()
        
    # User can view:
    # 1. Own folders (USER type, owner=user)
    # 2. District folders (DISTRICT type)
    # 3. School folders (SCHOOL type) if user.schools contains the folder.school
    
    # 1. My folders
    q_owner = Q(owner=user)
    
    # 2. Shared folders
    q_shared = Q(shares__shared_with=user)
    
    # 3. District folders
    q_district = Q(folder_type='DISTRICT')
    
    # 4. School folders
    user_roles = get_user_roles(user)
    unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}
    
    if user_roles & unrestricted_roles:
         # Admins and District View can see all school folders
         q_school = Q(folder_type='SCHOOL')
    else:
        user_school_ids = []
        if isinstance(user.schools, list):
             # Map state_ids from user.schools to users.models.School ids (best effort)
             from studata.models import School as StudataSchool
             from users.models import School as UserSchool
             
             # Fetch school names from studata
             studata_names = StudataSchool.objects.filter(state_id__in=user.schools).values_list('school_name', flat=True)
             # Find matching UserSchools
             user_school_ids = UserSchool.objects.filter(name__in=studata_names).values_list('id', flat=True)
        elif hasattr(user.schools, 'values_list'):
             # Fallback for old ManyToManyField (if not fully migrated)
             user_school_ids = user.schools.values_list('id', flat=True)
             
        q_school = Q(folder_type='SCHOOL', school__id__in=user_school_ids)
    
    # Also check hierarchy: If can view parent, can view child? 
    # Usually yes, but sticking to basic scope for now.
    
    return ReportFolder.objects.filter(q_owner | q_shared | q_district | q_school).distinct()

def can_user_view_folder(user, folder):
    """
    Check if user can view the specific folder.
    This logic MUST match get_user_folders query logic.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # 1. Own folders
    if folder.owner == user:
        return True
        
    # 2. Shared folders (check prefetched shares if available to save DB hits)
    # Using .all() calculates on prefetched data if available
    for share in folder.shares.all():
        if share.shared_with_id == user.id:
            return True
            
    # 3. District folders - accessible to all authenticated users currently
    if folder.folder_type == 'DISTRICT':
        return True
        
    # 4. School folders
    if folder.folder_type == 'SCHOOL':
        user_roles = get_user_roles(user)
        unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}
        
        if user_roles & unrestricted_roles:
            return True
            
        # Check if user has access to this specific school
        return _user_has_access_to_user_school(user, folder.school)
        
    # USER folders that are not owned and not shared are hidden
    return False


def can_user_edit_folder(user, folder):
    """
    Check if user can edit this folder (rename, change desc) OR add items to it.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    if folder.folder_type == 'USER':
        if folder.owner == user:
            return True
        # Check if shared with edit permission
        if folder.shares.filter(shared_with=user, can_edit=True).exists():
            return True
        return False
        
    user_roles = get_user_roles(user)
    
    if folder.folder_type == 'DISTRICT':
        # Only admins can edit root district folder
        if folder.parent is None:
            return bool({'Project Admin', 'District Admin'} & user_roles)
        # Subfolders? Maybe department heads?
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        # If a non-USER district subfolder tracks an owner (creator), allow them to manage it.
        return folder.owner == user
        
    if folder.folder_type == 'SCHOOL':
        # Project Admin and District Admin can edit ALL school folders
        if {'Project Admin', 'District Admin'} & user_roles:
            return True

        # User must have access to the school to even consider editing
        has_school_access = folder.school and _user_has_access_to_user_school(user, folder.school)
        if not has_school_access:
            return False

        # School Admin can edit folders in their school
        if 'School Admin' in user_roles:
            # Cannot edit the root school folder (legacy name of the school)
            if folder.parent is None:
                return False
            return True

        # Non-admins (Teachers) cannot edit school folders
        return False
        
    return False

    return False

def can_create_in_folder(user, folder):
    """
    Check if user can create Query/List/Subfolder inside 'folder'.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True

    # My Folder - always yes
    if folder.folder_type == 'USER' and folder.owner == user:
        return True
    
    # Check roles
    # user_roles = {r.name for r in user.roles.all()} 
    
    # Actually just:
    user_roles = get_user_roles(user)
    
    # District / School folders
    # "departments will be able to create department specific queries"
    # This implies standard users (or Dept heads) can create items in these shared folders.
    
    if folder.folder_type == 'DISTRICT':
        # Allow Project Admin and District Admin to create subfolders
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        # Allow others? Maybe not at root level.
        return False
        
    if folder.folder_type == 'SCHOOL':
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        # If user user belongs to school, allow them to create?
        if 'School Admin' in user_roles and _user_has_access_to_user_school(user, folder.school):
            return True # Allow school admin to create subfolders/queries
            
    return False


def can_user_view_query(user, query):
    """
    Check if user can view/access a saved query.
    Considers ownership, privacy settings, and direct shares.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # Owner can always view
    if query.owner == user:
        return True
        
    # Public queries are visible to all authenticated users
    if query.privacy == 'public':
        return True
        
    # Check if directly shared with user
    if query.shares.filter(shared_with=user).exists():
        return True
        
    # If query has a folder, check folder access
    if query.folder:
        # User can view if they have folder access
        user_folders = get_user_folders(user)
        if query.folder in user_folders:
            return True
    
    return False


def can_user_manage_query(user, query):
    """
    Check if user can edit/delete a saved query.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # Owner can always manage
    if query.owner == user:
        return True
        
    # Check if user has edit permission via QueryShare
    if query.shares.filter(shared_with=user, can_edit=True).exists():
        return True
        
    # Check if user has edit permission on the folder (if folder exists)
    if query.folder:
        return can_user_edit_folder(user, query.folder)
    
    return False


def can_user_manage_list(user, student_list):
    """
    Check if user can edit/delete a student list.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # Owner can always manage
    if student_list.owner == user:
        return True
        
    # Check if user has edit permission on the folder
    return can_user_edit_folder(user, student_list.folder)

def _apply_de_school_filter(where_clauses, params, user):
    """Apply the active school context or enforce school permissions."""
    is_unrestricted = False
    if user.is_staff:
        is_unrestricted = True
    elif hasattr(user, 'active_role') and user.active_role and 'District' in user.active_role.name:
        is_unrestricted = True

    if hasattr(user, 'active_school') and user.active_school:
        where_clauses.append("cs.school_state_id = %s")
        params.append(user.active_school.state_id)
    else:
        if not is_unrestricted:
            if hasattr(user, 'schools') and isinstance(user.schools, list) and user.schools:
                where_clauses.append("cs.school_state_id = ANY(%s)")
                params.append(user.schools)
            else:
                where_clauses.append("1=0")
