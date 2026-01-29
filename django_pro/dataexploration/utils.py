from django.db.models import Q
from .models import ReportFolder

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
    user_school_ids = user.schools.values_list('id', flat=True)
    q_school = Q(folder_type='SCHOOL', school__id__in=user_school_ids)
    
    # Also check hierarchy: If can view parent, can view child? 
    # Usually yes, but sticking to basic scope for now.
    
    return ReportFolder.objects.filter(q_owner | q_shared | q_district | q_school).distinct()

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
        
    role_name = getattr(user.role, 'name', '') if user.role else ''
    
    if folder.folder_type == 'DISTRICT':
        # Only admins can edit root district folder
        if folder.parent is None:
            return role_name in ['Project Admin', 'District Admin']
        # Subfolders? Maybe department heads?
        if role_name in ['Project Admin', 'District Admin']:
            return True
        # If a non-USER district subfolder tracks an owner (creator), allow them to manage it.
        return folder.owner == user
        
    if folder.folder_type == 'SCHOOL':
        # Only admins can edit root school folder
        if folder.parent is None:
            if role_name in ['Project Admin', 'District Admin']:
                return True
            # School Admin?
            # Assuming 'School Admin' exists and user is assigned to this school
            if role_name == 'School Admin' and folder.school in user.schools.all():
                return True
            return False
            
        # Subfolders in school: allow school staff to manage folders within their school.
        # This matches the UI behavior (delete/rename buttons shown) and avoids locking out creators
        # when historical folders were created without an owner.
        if folder.school and folder.school in user.schools.all():
            return True
        return folder.owner == user

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
        
    role_name = getattr(user.role, 'name', '') if user.role else ''
    
    # District / School folders
    # "departments will be able to create department specific queries"
    # This implies standard users (or Dept heads) can create items in these shared folders.
    
    if folder.folder_type == 'DISTRICT':
        # Allow Project Admin and District Admin to create subfolders
        if role_name in ['Project Admin', 'District Admin']:
            return True
        # Allow others? Maybe not at root level.
        return False
        
    if folder.folder_type == 'SCHOOL':
        if role_name in ['Project Admin', 'District Admin']:
            return True
        # If user user belongs to school, allow them to create?
        if folder.school in user.schools.all():
            return True # Allow school staff to create subfolders/queries
            
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
