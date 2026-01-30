# Django models for student data
from django.db import models


class CurrentStudent(models.Model):
    """Model for current student data from K12INTEL_DW"""
    stu_annual_key = models.CharField(
        max_length=32, primary_key=True, help_text="MD5 hash of ssid + school_year")
    ssid = models.CharField(max_length=20, db_index=True,
                            help_text="State Student ID")
    student_id = models.CharField(
        max_length=20, db_index=True, help_text="Local Student ID")
    student_name = models.CharField(max_length=255, blank=True, null=True)
    grade = models.CharField(max_length=50, blank=True, null=True)
    school = models.CharField(max_length=255, blank=True, null=True)
    school_state_id = models.CharField(max_length=20, blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    race = models.CharField(max_length=50, blank=True, null=True)
    ell = models.CharField(max_length=10, blank=True, null=True,
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

    class Meta:
        db_table = 'current_student'
        ordering = ['school_year', 'student_name']
        indexes = [
            models.Index(fields=['school_year', 'school_state_id']),
            models.Index(fields=['ssid', 'school_year']),
        ]

    def __str__(self):
        return f"{self.student_name} ({self.ssid}) - {self.school_year}"


class StudentEnroll(models.Model):
    """Model for student enrollment data from SIS"""
    stu_enrollment_key = models.CharField(
        max_length=32, primary_key=True, help_text="MD5 hash of student_id + school_year + enter_date + leave_date")
    stu_annual_key = models.CharField(
        max_length=32, db_index=True, blank=True, null=True, help_text="MD5 hash of student_id + school_year")
    student_id = models.CharField(max_length=20, db_index=True)
    ssid = models.CharField(max_length=20, db_index=True, blank=True, null=True,
                            help_text="State Student ID")
    last_name = models.CharField(max_length=100, blank=True, null=True)
    middle_name = models.CharField(max_length=100, blank=True, null=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    birth_date = models.DateTimeField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    race = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, blank=True, null=True)
    enter_date = models.DateTimeField(blank=True, null=True)
    leave_date = models.DateTimeField(blank=True, null=True)
    us_enter_date = models.DateTimeField(blank=True, null=True)
    state_enter_date = models.DateTimeField(blank=True, null=True)
    ell_status = models.CharField(
        max_length=10, blank=True, null=True, help_text="English Language Learner status")
    fep_date = models.DateTimeField(
        blank=True, null=True, help_text="Fluent English Proficient date")
    instructional_setting = models.CharField(
        max_length=10, blank=True, null=True)
    grade = models.CharField(max_length=50, blank=True, null=True)
    special_program_code = models.CharField(
        max_length=50, blank=True, null=True)
    school_name = models.CharField(max_length=255, blank=True, null=True)
    state_school_code = models.CharField(max_length=20, blank=True, null=True)
    migrant = models.CharField(max_length=10, blank=True, null=True)
    homeless = models.CharField(max_length=10, blank=True, null=True)
    foster = models.CharField(max_length=10, blank=True, null=True)
    school_year = models.CharField(max_length=20, db_index=True)

    class Meta:
        db_table = 'student_enroll'
        ordering = ['school_year', 'last_name', 'first_name']
        indexes = [
            models.Index(fields=['school_year', 'state_school_code']),
            models.Index(fields=['ssid', 'school_year']),
            models.Index(fields=['student_id', 'school_year']),
            models.Index(fields=['enter_date', 'leave_date']),
        ]

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip()
        return f"{full_name} ({self.ssid}) - {self.school_year}"

    @property
    def full_name(self):
        """Returns the full name of the student"""
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join([p for p in parts if p])


class School(models.Model):
    """Model for school reference data"""
    dw_schoolkey = models.IntegerField(
        primary_key=True, help_text="Data warehouse school key")
    state_id = models.CharField(
        max_length=20, db_index=True, blank=True, null=True, help_text="State school identifier")
    sis_key = models.CharField(
        max_length=50, db_index=True, blank=True, null=True, help_text="SIS school key")
    assessment_school_code = models.CharField(
        max_length=20, blank=True, null=True, help_text="Assessment system school code")
    short_name = models.CharField(
        max_length=100, blank=True, null=True, help_text="Short school name")
    school_name = models.CharField(
        max_length=255, help_text="Full school name")
    school_group = models.CharField(
        max_length=50, blank=True, null=True, help_text="School type/group (Elementary, Middle, High, Special)")

    class Meta:
        db_table = 'school'
        ordering = ['school_name']
        indexes = [
            models.Index(fields=['state_id']),
            models.Index(fields=['sis_key']),
            models.Index(fields=['school_group']),
        ]

    def __str__(self):
        return f"{self.school_name} ({self.state_id})"


class Test(models.Model):
    """
    Model representing test groups/configurations.
    """
    test_group_key = models.CharField(max_length=255, primary_key=True)
    test_name = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    school_year = models.CharField(max_length=255)
    test_window = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'tests'
        verbose_name = 'Test'
        verbose_name_plural = 'Tests'

    def __str__(self):
        return f"{self.test_name} - {self.subject} ({self.school_year})"


class Result(models.Model):
    """
    Model representing individual test results for students.
    """
    test_id = models.CharField(max_length=255, primary_key=True)
    test_group_key = models.CharField(max_length=255)
    ssid = models.CharField(max_length=255)
    local_student_id = models.CharField(max_length=255)
    school_year = models.CharField(max_length=255)
    school_code = models.CharField(max_length=255)
    pl = models.CharField(max_length=255, null=True, blank=True)
    ss = models.CharField(max_length=255, null=True, blank=True)
    other_info = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'results'
        verbose_name = 'Result'
        verbose_name_plural = 'Results'
        indexes = [
            models.Index(fields=['ssid']),
            models.Index(fields=['school_year']),
            models.Index(fields=['school_code']),
        ]

    def __str__(self):
        return f"Result {self.test_id} - SSID: {self.ssid}"


class Schedule(models.Model):
    """Model for student course schedules"""
    schedule_id = models.CharField(max_length=255, primary_key=True, db_index=True)
    stu_annual_key = models.CharField(max_length=255, db_index=True)
    ssid = models.CharField(max_length=50, db_index=True)
    school_year = models.CharField(max_length=20)
    student_id = models.CharField(max_length=50, blank=True, null=True)
    school_name = models.CharField(max_length=255, blank=True, null=True)
    state_school_id = models.CharField(max_length=50, blank=True, null=True)
    course_state_equivalence_code = models.CharField(max_length=50, blank=True, null=True)
    course_code = models.CharField(max_length=50, blank=True, null=True)
    course_key = models.CharField(max_length=255, blank=True, null=True)
    course_subject = models.CharField(max_length=255, blank=True, null=True)
    course_name = models.CharField(max_length=255, blank=True, null=True)
    period = models.CharField(max_length=20, blank=True, null=True)
    staff_name = models.CharField(max_length=255, blank=True, null=True)
    staff_email = models.EmailField(blank=True, null=True)
    staff_employee_id = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    schedule_status = models.CharField(max_length=50, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'schedule_list'
        verbose_name = 'Schedule'
        verbose_name_plural = 'Schedules'
        indexes = [
            models.Index(fields=['ssid', 'school_year']),
            models.Index(fields=['staff_employee_id', 'school_year']),
        ]

    def __str__(self):
        return f"{self.schedule_id} - {self.student_id} - {self.course_name}"


class StudentMarks(models.Model):
    """Model for student marks/grades"""
    marks_id = models.CharField(max_length=255, primary_key=True, db_index=True)
    stu_annual_key = models.CharField(max_length=255, db_index=True)
    ssid = models.CharField(max_length=50, db_index=True)
    school_year = models.CharField(max_length=20)
    student_id = models.CharField(max_length=50, blank=True, null=True)
    school_name = models.CharField(max_length=255, blank=True, null=True)
    state_school_id = models.CharField(max_length=50, blank=True, null=True)
    subject = models.CharField(max_length=255, blank=True, null=True)
    course_name = models.CharField(max_length=255, blank=True, null=True)
    staff_name = models.CharField(max_length=255, blank=True, null=True)
    staff_employee_id = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    mark_period = models.CharField(max_length=50, blank=True, null=True)
    mark = models.CharField(max_length=10, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'student_marks_list'
        verbose_name = 'Student Mark'
        verbose_name_plural = 'Student Marks'
        indexes = [
            models.Index(fields=['ssid', 'school_year']),
            models.Index(fields=['staff_employee_id', 'school_year']),
            models.Index(fields=['mark_period']),
        ]

    def __str__(self):
        return f"{self.marks_id} - {self.student_id} - {self.mark}"


class TeacherAccess(models.Model):
    """Model for teacher access to student records"""
    access_id = models.CharField(max_length=255, primary_key=True, db_index=True)
    schedule_id = models.CharField(max_length=255, db_index=True)
    stu_annual_key = models.CharField(max_length=255, db_index=True)
    ssid = models.CharField(max_length=50, db_index=True)
    school_year = models.CharField(max_length=20)
    student_id = models.CharField(max_length=50, blank=True, null=True)
    school_name = models.CharField(max_length=255, blank=True, null=True)
    state_school_id = models.CharField(max_length=50, blank=True, null=True)
    staff_name = models.CharField(max_length=255, blank=True, null=True)
    staff_email = models.EmailField(blank=True, null=True)
    staff_employee_id = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'teacher_access_list'
        verbose_name = 'Teacher Access'
        verbose_name_plural = 'Teacher Accesses'
        indexes = [
            models.Index(fields=['schedule_id']),
            models.Index(fields=['staff_employee_id', 'school_year']),
            models.Index(fields=['ssid', 'school_year']),
        ]

    def __str__(self):
        return f"{self.access_id} - {self.staff_name} - {self.student_id}"




