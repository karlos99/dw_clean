from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Import Page model from models_pages
from .models_pages import Page

# Roles that see all students at row level (then scoped by school via user.schools /
# active_school). Membership-based so add-on roles work regardless of the active role.
UNRESTRICTED_ROLE_NAMES = frozenset({
    'Project Admin',
    'District Admin',
    'District View',
    'School Admin',
    'School Gr View',
    'School View',
    'Testing Coordinator',
    '504_Admin',
})

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
        return 'Project Admin' in self.role_names()

    def role_names(self):
        """Effective role names: all assigned roles plus the active role."""
        names = set(self.roles.values_list('name', flat=True))
        active_role = getattr(self, 'active_role', None)
        if active_role:
            names.add(active_role.name)
        return names

    def has_role(self, name):
        """Membership check - an add-on role works even when not the active role."""
        return name in self.role_names()

    def get_accessible_student_keys(self):
        """
        Returns a list of stu_annual_key that this user is allowed to access.
        Returns None if the user has school-wide (row-unrestricted) access, i.e.
        subject only to school filters. Teacher roster restrictions are applied
        only when the user holds no school-wide role.

        Membership-based: holding a school-wide role (e.g. Testing Coordinator)
        grants school-wide access regardless of which role is currently active.
        """
        if self.is_superuser:
            return None

        role_names = self.role_names()
        if any(r in UNRESTRICTED_ROLE_NAMES or 'District' in r for r in role_names):
            return None

        # Non-teacher, non-school-wide roles are not row-restricted.
        if 'Teacher' not in role_names:
            return None

        # Teacher-only users are restricted to their scheduled students.
        if not self.staff_id:
            return []

        # Import here to avoid circular dependencies
        from studata.models import Schedule, School as StudataSchool

        valid_schools = StudataSchool.objects.values('state_id')
        keys = Schedule.objects.filter(
            staff_employee_id=self.staff_id,
            state_school_id__in=valid_schools
        ).values_list('stu_annual_key', flat=True).distinct()

        return list(keys)

    _school_cache = {}

    @property
    def first_school_name(self):
        if not self.schools or not isinstance(self.schools, list):
            return '-'
        first_school_id = self.schools[0]
        if not first_school_id:
            return '-'
        if first_school_id not in User._school_cache:
            from studata.models import School as StudataSchool
            school_obj = StudataSchool.objects.filter(state_id=first_school_id).first()
            User._school_cache[first_school_id] = school_obj.school_name if school_obj else '-'
        return User._school_cache[first_school_id]

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
