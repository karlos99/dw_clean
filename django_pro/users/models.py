from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Import Page model from models_pages
from .models_pages import Page

class School(models.Model):
    name = models.CharField(max_length=255)

    
    # Add other fields as necessary
    
    def __str__(self):
        return self.name

class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class User(AbstractUser):
    # AbstractUser already has:
    # username, first_name, last_name, email, is_staff, is_active, date_joined
    
    name = models.CharField(_("Full Name"), max_length=255, blank=True)
    job_title = models.CharField(_("Job Title"), max_length=255, blank=True)
    staff_id = models.CharField(_("Staff ID"), max_length=50, blank=True, null=True, unique=True, db_index=True)
    
    # Role can be dynamic as per requirement "create roles"
    roles = models.ManyToManyField(
        Role, 
        blank=True, 
        related_name="users"
    )
    
    # Store a list of school state IDs from studata.models.School
    schools = models.JSONField(default=list, blank=True)
    
    # Store grade levels user can access (e.g., ['KG', '01', '02', '03'])
    grade_levels = models.JSONField(default=list, blank=True)
    
    # Preferences placeholder
    preferences = models.JSONField(default=dict, blank=True)

    # Make email unique and required as it is key for Google Auth
    email = models.EmailField(_("email address"), unique=True)
    
    avatar = models.URLField(max_length=500, blank=True, null=True)

    @property
    def is_project_admin(self):
        if hasattr(self, 'active_role') and self.active_role:
             return self.active_role.name == 'Project Admin'
        return False

    def get_accessible_student_keys(self):
        """
        Returns a list of stu_annual_keys that this user is allowed to access.
        Returns None if user has full access (subject to school restrictions).
        Only applies teacher-level restrictions when user is actively in Teacher role.
        """
        # Determine if we need to restrict based on role
        # Unrestricted roles:
        if self.is_superuser:
            return None
            
        # Check active role if it exists (set by middleware in request context)
        active_role_name = None
        if hasattr(self, 'active_role') and self.active_role:
            active_role_name = self.active_role.name
        
        # If user has only one role, use that
        if active_role_name is None and self.roles.count() == 1:
            active_role_name = self.roles.first().name
            
        # Unrestricted roles get full access
        unrestricted_roles = ['Project Admin', 'District Admin', 'School Admin', 'School Gr View', 'School View']
        if active_role_name and (active_role_name in unrestricted_roles or 'District' in active_role_name):
            return None
        
        # Only apply teacher restrictions if explicitly in Teacher role
        # If user has Teacher role but is using another role context, don't restrict
        if active_role_name and active_role_name != 'Teacher':
            return None
        
        # At this point, user is in Teacher role or has only Teacher role
        # Apply the specific restrictions for teachers
        if not self.staff_id:
             # If no staff_id, teacher has no access
             return []
             
        # Import here to avoid circular dependencies
        from studata.models import Schedule, School as StudataSchool
        
        # Query: select DISTINCT sl.stu_annual_key ... 
        # from schedule_list sl 
        # inner join schools sch on sch.state_id = sl.state_school_id hiding students from invalid schools
        
        # We need filtering where state_school_id is valid (exists in School table) & staff_id matches.
        
        # Subquery for valid schools
        valid_schools = StudataSchool.objects.values('state_id')
        
        keys = Schedule.objects.filter(
            staff_employee_id=self.staff_id,
            state_school_id__in=valid_schools
        ).values_list('stu_annual_key', flat=True).distinct()
        
        return list(keys)

        return self.roles.filter(name='Project Admin').exists()

    @property
    def is_district_admin(self):
        if hasattr(self, 'active_role') and self.active_role:
             return self.active_role.name == 'District Admin'
        return self.roles.filter(name='District Admin').exists()

    def __str__(self):
        return self.email


class UsageSession(models.Model):
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='usage_sessions')
    session_key = models.CharField(max_length=64, db_index=True)
    login_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    logout_at = models.DateTimeField(null=True, blank=True)
    total_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'session_key']),
            models.Index(fields=['login_at']),
        ]

    def __str__(self):
        return f"{self.user.email} @ {self.login_at}"


class PageVisit(models.Model):
    session = models.ForeignKey(UsageSession, on_delete=models.CASCADE, related_name='page_visits')
    path = models.TextField()
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=['started_at']),
        ]

    def __str__(self):
        return f"{self.path} ({self.session_id})"
