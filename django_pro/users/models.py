from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
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
    # When true, the role grants access only to the students explicitly listed
    # on each user's role assignment (by SSID and/or local student ID).
    is_student_limited = models.BooleanField(
        default=False,
        help_text="Restrict this role to the students listed on each user's role assignment.",
    )

    def __str__(self):
        return self.name


class RoleAssignment(models.Model):
    """Intermediate model for ``User.roles`` carrying per-assignment metadata.

    Expiration and fallback are optional.  When ``expires_at`` passes, the
    assignment stops granting the role and the user falls back to
    ``fallback_role`` (if one was selected).  ``limited_ssids`` /
    ``limited_student_ids`` are only used by roles flagged
    ``is_student_limited``.
    """

    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='role_assignments',
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='assignments',
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Optional date/time when this role assignment stops being effective.",
    )
    fallback_role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fallback_assignments',
        help_text="Optional role to switch back to when this assignment expires.",
    )
    limited_ssids = models.JSONField(
        default=list,
        blank=True,
        help_text="State Student IDs (SSID) accessible through this limited role.",
    )
    limited_student_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Local student IDs accessible through this limited role.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'users_user_roles'
        unique_together = (('user', 'role'),)

    def __str__(self):
        return f"{self.user.email} → {self.role.name}"

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def is_effective(self):
        return not self.is_expired


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
        related_name="users",
        through='RoleAssignment',
        through_fields=('user', 'role'),
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

    # ------------------------------------------------------------------
    # Role assignment helpers
    # ------------------------------------------------------------------
    def effective_role_assignments(self):
        """Assignments that are currently in effect (not expired)."""
        now = timezone.now()
        return self.role_assignments.select_related('role', 'fallback_role').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

    def effective_roles(self):
        """Roles currently granted to this user (expired assignments excluded)."""
        role_ids = self.effective_role_assignments().values('role_id')
        return Role.objects.filter(id__in=role_ids).distinct()

    def role_names(self):
        """Effective role names: all non-expired assigned roles plus the active role."""
        assignments = list(self.effective_role_assignments())
        names = {assignment.role.name for assignment in assignments}
        active_role = getattr(self, 'active_role', None)
        if active_role and any(
            assignment.role_id == active_role.id for assignment in assignments
        ):
            # Never resurrect an expired role through the active-role context.
            names.add(active_role.name)
        return names

    def has_role(self, name):
        """Membership check - an add-on role works even when not the active role."""
        return name in self.role_names()

    def sync_expired_role_assignments(self):
        """Remove expired assignments and apply their fallback role.

        Returns a mapping of ``expired_role_id -> fallback_role_id`` (or
        ``None`` when no fallback was configured) so callers can repair an
        active-role selection stored in the session.
        """
        now = timezone.now()
        expired = list(
            self.role_assignments.filter(expires_at__lte=now)
            .select_related('role', 'fallback_role')
        )
        if not expired:
            return {}

        expired_role_ids = {assignment.role_id for assignment in expired}
        for assignment in expired:
            assignment.delete()

        changed = {}
        for assignment in expired:
            expired_role_id = assignment.role_id
            fallback_role = assignment.fallback_role
            if (
                fallback_role
                and fallback_role.id != expired_role_id
                and fallback_role.id not in expired_role_ids
            ):
                still_active = self.role_assignments.filter(
                    role=fallback_role,
                ).filter(
                    Q(expires_at__isnull=True) | Q(expires_at__gt=now)
                ).exists()
                if not still_active:
                    self.role_assignments.create(role=fallback_role)
                changed[expired_role_id] = fallback_role.id
            else:
                changed[expired_role_id] = None
        return changed

    def _limited_role_assignment(self):
        """Return the limited-student assignment that governs access, if any."""
        assignments = list(self.effective_role_assignments())
        limited_assignments = [
            assignment for assignment in assignments
            if assignment.role.is_student_limited
        ]
        if not limited_assignments:
            return None

        active_role = getattr(self, 'active_role', None)
        if active_role:
            for assignment in limited_assignments:
                if assignment.role_id == active_role.id:
                    return assignment
            return None

        # No explicit active role ("All roles"): only restrict when the user's
        # entire effective role set consists of limited roles.
        all_role_ids = {assignment.role_id for assignment in assignments}
        limited_role_ids = {assignment.role_id for assignment in limited_assignments}
        if all_role_ids and all_role_ids == limited_role_ids:
            return limited_assignments[0]
        return None

    def _limited_student_keys(self, assignment):
        cache_key = f'_limited_student_keys_{assignment.pk}'
        if hasattr(self, cache_key):
            return getattr(self, cache_key)

        ssids = assignment.limited_ssids or []
        student_ids = assignment.limited_student_ids or []

        if not ssids and not student_ids:
            keys = []
        else:
            # Import here to avoid circular dependencies
            from studata.models import CurrentStudent, StudentEnroll

            query = Q()
            if ssids:
                query |= Q(ssid__in=ssids)
            if student_ids:
                query |= Q(student_id__in=student_ids)

            key_set = set(
                CurrentStudent.objects.filter(query)
                .values_list('stu_annual_key', flat=True)
                .distinct()
            )
            # Include historical enrollment keys so the same student remains
            # visible when older school years are selected.
            key_set.update(
                StudentEnroll.objects.filter(query)
                .exclude(stu_annual_key__isnull=True)
                .exclude(stu_annual_key='')
                .values_list('stu_annual_key', flat=True)
                .distinct()
            )
            keys = sorted(key_set)

        setattr(self, cache_key, keys)
        return keys

    def get_accessible_student_keys(self):
        """
        Returns a list of stu_annual_key that this user is allowed to access.
        Returns None if the user has school-wide (row-unrestricted) access, i.e.
        subject only to school filters. Teacher roster restrictions are applied
        only when the user holds no school-wide role.

        Membership-based: holding a school-wide role (e.g. Testing Coordinator)
        grants school-wide access regardless of which role is currently active.
        Limited-student roles take precedence while they are the active role.
        """
        if self.is_superuser:
            return None

        limited_assignment = self._limited_role_assignment()
        if limited_assignment is not None:
            return self._limited_student_keys(limited_assignment)

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
    def assigned_schools_list(self):
        """Returns a list of dicts [{'state_id': sid, 'school_name': name}] for all schools the user belongs to."""
        if not self.schools or not isinstance(self.schools, list):
            return []
        missing_ids = [sid for sid in self.schools if sid and sid not in User._school_cache]
        if missing_ids:
            from studata.models import School as StudataSchool
            for s in StudataSchool.objects.filter(state_id__in=missing_ids).values('state_id', 'school_name'):
                User._school_cache[s['state_id']] = s['school_name']
        res = []
        for sid in self.schools:
            if not sid:
                continue
            name = User._school_cache.get(sid) or sid
            res.append({'state_id': sid, 'school_name': name})
        return res

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
        return self.effective_role_assignments().filter(
            role__name='District Admin'
        ).exists()

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
