from django.db import models
from studata.models import CurrentStudent, StudentEnroll, School


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
    school_state_id = models.ForeignKey(
        School, on_delete=models.SET_NULL, blank=True, null=True,
        to_field='state_id', db_column='school_state_id', related_name='students')
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
        return self.school_state_id.short_name if self.school_state_id else None
