from django.db import models
from django.conf import settings
from studata.models import CurrentStudent, StudentEnroll, School
from users.models import School as UserSchool


class StudentCombined(models.Model):
    """
    Combined student data view joining CurrentStudent (left) and StudentEnroll on stu_annual_key.
    This model represents a database view or query result.
    """
    # Primary key from CurrentStudent
    stu_annual_key = models.CharField(
        max_length=32, primary_key=True, help_text="MD5 hash of ssid + school_year")

    # Fields from CurrentStudent (left table)
    ssid = models.CharField(max_length=20, db_index=True,
                            help_text="State Student ID")
    student_id = models.CharField(
        max_length=20, db_index=True, help_text="Local Student ID")
    grade = models.CharField(max_length=50, blank=True, null=True)
    school = models.CharField(max_length=255, blank=True, null=True)
    school_state_id = models.CharField(max_length=20, blank=True, null=True, 
                                       help_text="State school identifier")
    gender = models.CharField(max_length=20, blank=True, null=True)
    race = models.CharField(max_length=50, blank=True, null=True)
    esl = models.CharField(max_length=10, blank=True, null=True,
                           help_text="English as Second Language status")
    sed = models.CharField(max_length=10, blank=True, null=True,
                           help_text="Socioeconomically Disadvantaged")
    foster = models.CharField(max_length=10, blank=True, null=True)
    homeless = models.CharField(max_length=10, blank=True, null=True)
    sped = models.CharField(max_length=10, blank=True,
                            null=True, help_text="Special Education")
    gate = models.CharField(max_length=10, blank=True,
                            null=True, help_text="Gifted and Talented Education")
    in_504 = models.CharField(
        max_length=10, blank=True, null=True, help_text="Section 504 Plan")
    dli = models.CharField(max_length=10, blank=True,
                           null=True, help_text="Dual Language Immersion")
    atendace_rate = models.DecimalField(
        max_digits=5, decimal_places=2, blank=True, null=True, help_text="Attendance Rate")
    suspension_indicator = models.CharField(
        max_length=10, blank=True, null=True)
    ltel = models.CharField(max_length=10, blank=True,
                            null=True, help_text="Long-Term English Learner")
    status = models.CharField(max_length=20, blank=True, null=True)
    school_year = models.CharField(max_length=20, db_index=True)

    # Additional fields from StudentEnroll (non-duplicate fields, excluding enter_date and leave_date)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    middle_name = models.CharField(max_length=100, blank=True, null=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    birth_date = models.DateTimeField(blank=True, null=True)
    us_enter_date = models.DateTimeField(blank=True, null=True)
    state_enter_date = models.DateTimeField(blank=True, null=True)
    ell_status = models.CharField(
        max_length=10, blank=True, null=True, help_text="English Language Learner status")
    fep_date = models.DateTimeField(
        blank=True, null=True, help_text="Fluent English Proficient date")
    instructional_setting = models.CharField(
        max_length=10, blank=True, null=True)
    special_program_code = models.CharField(
        max_length=50, blank=True, null=True)
    migrant = models.CharField(max_length=10, blank=True, null=True)

    class Meta:
        managed = False  # This is a view, not a managed table
        db_table = 'student_combined'
        ordering = ['school_year', 'last_name', 'first_name']

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip(
        ) if self.first_name or self.last_name else "Unknown"
        return f"{full_name} ({self.ssid}) - {self.school_year}"

    @property
    def full_name(self):
        """Returns the full name of the student"""
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join([p for p in parts if p])

    @property
    def school_short_name(self):
        """Returns the short name of the school"""
        try:
            school = School.objects.filter(state_id=self.school_state_id).first()
            return school.short_name if school else None
        except:
            return None


class StudentWithTests(models.Model):
    """
    Combined student data with test results.
    Joins StudentCombined with Test and Result tables by ssid.
    """
    # Composite primary key fields
    id = models.CharField(max_length=255, primary_key=True,
                         help_text="Composite key: stu_annual_key + test_id")
    
    # Student fields from StudentCombined
    stu_annual_key = models.CharField(max_length=32, help_text="MD5 hash of ssid + school_year")
    ssid = models.CharField(max_length=20, db_index=True, help_text="State Student ID")
    student_id = models.CharField(max_length=20, db_index=True, help_text="Local Student ID")
    grade = models.CharField(max_length=50, blank=True, null=True)
    school = models.CharField(max_length=255, blank=True, null=True)
    school_state_id = models.CharField(max_length=20, blank=True, null=True,
                                       help_text="State school identifier")
    gender = models.CharField(max_length=20, blank=True, null=True)
    race = models.CharField(max_length=50, blank=True, null=True)
    esl = models.CharField(max_length=10, blank=True, null=True)
    sed = models.CharField(max_length=10, blank=True, null=True)
    foster = models.CharField(max_length=10, blank=True, null=True)
    homeless = models.CharField(max_length=10, blank=True, null=True)
    sped = models.CharField(max_length=10, blank=True, null=True)
    gate = models.CharField(max_length=10, blank=True, null=True)
    in_504 = models.CharField(max_length=10, blank=True, null=True)
    dli = models.CharField(max_length=10, blank=True, null=True)
    atendace_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    suspension_indicator = models.CharField(max_length=10, blank=True, null=True)
    ltel = models.CharField(max_length=10, blank=True, null=True)
    status = models.CharField(max_length=20, blank=True, null=True)
    school_year = models.CharField(max_length=20, db_index=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    middle_name = models.CharField(max_length=100, blank=True, null=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    birth_date = models.DateTimeField(blank=True, null=True)
    us_enter_date = models.DateTimeField(blank=True, null=True)
    state_enter_date = models.DateTimeField(blank=True, null=True)
    ell_status = models.CharField(max_length=10, blank=True, null=True)
    fep_date = models.DateTimeField(blank=True, null=True)
    instructional_setting = models.CharField(max_length=10, blank=True, null=True)
    special_program_code = models.CharField(max_length=50, blank=True, null=True)
    migrant = models.CharField(max_length=10, blank=True, null=True)
    
    # Test fields from Test table
    test_group_key = models.CharField(max_length=255)
    test_name = models.CharField(max_length=255, blank=True, null=True)
    subject = models.CharField(max_length=255, blank=True, null=True)
    test_window = models.CharField(max_length=255, blank=True, null=True)
    
    # Result fields from Result table
    test_id = models.CharField(max_length=255)
    local_student_id = models.CharField(max_length=255, blank=True, null=True)
    school_code = models.CharField(max_length=255, blank=True, null=True)
    pl = models.CharField(max_length=255, blank=True, null=True, help_text="Performance Level")
    ss = models.CharField(max_length=255, blank=True, null=True, help_text="Scale Score")
    other_info = models.JSONField(blank=True, null=True)

    class Meta:
        managed = False  # This is a view, not a managed table
        db_table = 'student_with_tests'
        ordering = ['school_year', 'last_name', 'first_name', 'test_name']

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip() if self.first_name or self.last_name else "Unknown"
        return f"{full_name} - {self.test_name} ({self.school_year})"

    @property
    def full_name(self):
        """Returns the full name of the student"""
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join([p for p in parts if p])

    @property
    def school_short_name(self):
        """Returns the short name of the school"""
        try:
            school = School.objects.filter(state_id=self.school_state_id).first()
            return school.short_name if school else None
        except:
            return None


class DefaultColumnSelection(models.Model):
    """Stores the default columns shown for the student tests list."""
    columns = models.JSONField(default=list)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_column_selections',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Default Column Selection"
        verbose_name_plural = "Default Column Selections"

    def __str__(self):
        return f"Default columns ({len(self.columns)} selected)"


class ReportFolder(models.Model):
    TYPE_CHOICES = (
        ('USER', 'My Folder'),
        ('SCHOOL', 'School Folder'),
        ('DISTRICT', 'District Folder'),
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    folder_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    
    # Hierarchy
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='subfolders')
    
    # Ownership / Access Scope
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    school = models.ForeignKey(UserSchool, null=True, blank=True, on_delete=models.CASCADE, help_text="If set, accessible by users of this school")
    
    # Permissions
    is_public_to_scope = models.BooleanField(default=False, help_text="Visible to everyone in this scope (e.g. all School staff)")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_folder_type_display()}: {self.name}"


class FolderShare(models.Model):
    """Manages sharing of folders with specific users."""
    folder = models.ForeignKey(ReportFolder, on_delete=models.CASCADE, related_name='shares')
    shared_with = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shared_folders')
    can_edit = models.BooleanField(default=False)
    
    class Meta:
        unique_together = ('folder', 'shared_with')


class SavedQuery(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    folder = models.ForeignKey(ReportFolder, on_delete=models.CASCADE, related_name='queries')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    
    # Logic Configuration
    config = models.JSONField(default=dict) 
    # Example: { "columns": ["ssid", "test_math_23"], "filters": {"grade": "10"}, "sort": "last_name" }

    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name


class StudentList(models.Model):
    name = models.CharField(max_length=255)
    folder = models.ForeignKey(ReportFolder, on_delete=models.CASCADE, related_name='student_lists')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    description = models.TextField(blank=True)
    
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class StudentListItem(models.Model):
    student_list = models.ForeignKey(StudentList, on_delete=models.CASCADE, related_name='items')
    ssid = models.CharField(max_length=20, db_index=True)
    
    class Meta:
        unique_together = ('student_list', 'ssid')

    def __str__(self):
        return f"{self.ssid} in {self.student_list.name}"
