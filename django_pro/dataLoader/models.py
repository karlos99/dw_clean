from django.db import models
from django.conf import settings
from django.utils.text import slugify
import hashlib


class TestingWindow(models.TextChoices):
    FALL = 'FALL', 'Fall'
    WINTER = 'WINTER', 'Winter'
    SPRING = 'SPRING', 'Spring'


class ImportStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'
    PARTIAL = 'PARTIAL', 'Partially Completed'


class SurveyDefinition(models.Model):
    """
    Represents a survey configuration (e.g., Panorama SEL Skills & Competencies).
    Each school year + testing window + survey type combination gets its own definition.
    """
    name = models.CharField(
        max_length=255,
        help_text="User-provided name for this survey upload (e.g., 'Panorama SEL Spring 2026 Gr 3-5')"
    )
    survey_type = models.CharField(
        max_length=100,
        help_text="Type of survey (e.g., 'Skills and Competencies', 'Support and Environment')"
    )
    school_year = models.CharField(
        max_length=9,
        help_text="School year in format YYYY-YYYY (e.g., '2025-2026')"
    )
    testing_window = models.CharField(
        max_length=10,
        choices=TestingWindow.choices,
        help_text="Testing window (Fall, Winter, Spring)"
    )
    grade_range = models.CharField(
        max_length=50,
        blank=True,
        help_text="Grade range covered (e.g., 'Grades 3-5', 'Grades 6-8')"
    )
    source_system = models.CharField(
        max_length=100,
        default='Panorama',
        help_text="Source system for the survey data"
    )
    description = models.TextField(
        blank=True,
        help_text="Additional description or notes about this survey"
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number for tracking survey changes"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this survey definition is active"
    )
    response_colors = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON with separate color mappings for different scales. E.g., {'scale_5': {'1': '#ff6b6b', ...}, 'scale_7': {'1': '#ff6b6b', ...}}"
    )
    ready_for_exploration = models.BooleanField(
        default=False,
        help_text="Whether this survey data is ready to be shown in the Data Exploration app"
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this survey was published for exploration"
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='published_surveys'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_survey_definitions'
    )

    class Meta:
        ordering = ['-school_year', '-created_at']
        unique_together = ['survey_type', 'school_year', 'testing_window', 'grade_range']
        verbose_name = 'Survey Definition'
        verbose_name_plural = 'Survey Definitions'

    def __str__(self):
        return f"{self.name} ({self.school_year} {self.testing_window})"

    @property
    def question_count(self):
        return self.questions.count()


class SurveyTopic(models.Model):
    """
    A global topic/category for grouping survey questions.
    Topics are shared across all survey definitions.
    """
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(
        max_length=7,
        default='#6366f1',
        help_text='Hex color code for this topic badge (e.g. #6366f1)'
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Survey Topic'
        verbose_name_plural = 'Survey Topics'

    def __str__(self):
        return self.name


class SurveyQuestion(models.Model):
    """
    Represents a question within a survey. Questions may change across years,
    so each survey definition has its own set of questions.
    """
    survey_definition = models.ForeignKey(
        SurveyDefinition,
        on_delete=models.CASCADE,
        related_name='questions'
    )
    question_key = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Normalized key for cross-year question matching (slugified question text)"
    )
    question_text = models.TextField(
        help_text="Full text of the question as it appears in the survey"
    )
    domain = models.CharField(
        max_length=100,
        blank=True,
        help_text="Legacy free-text domain/category. Use 'topic' for new assignments."
    )
    topic = models.ForeignKey(
        SurveyTopic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='questions',
        help_text='Topic/category for this question (replaces the domain field)'
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order of question within the survey"
    )
    response_scale_max = models.PositiveIntegerField(
        default=7,
        help_text="Maximum value in the response scale (5 or 7). Minimum is always 1."
    )
    value_column_name = models.CharField(
        max_length=500,
        help_text="Original CSV column header for the VALUES column"
    )
    text_column_name = models.CharField(
        max_length=500,
        help_text="Original CSV column header for the TEXT column"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['survey_definition', 'order']
        unique_together = ['survey_definition', 'question_key']
        verbose_name = 'Survey Question'
        verbose_name_plural = 'Survey Questions'

    def __str__(self):
        return f"{self.question_text[:50]}..."

    def save(self, *args, **kwargs):
        if not self.question_key:
            # Generate a normalized key from the question text
            self.question_key = self._generate_question_key(self.question_text)
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_question_key(question_text):
        """Generate a normalized, slugified key from question text."""
        # Remove common prefixes and clean up
        text = question_text.lower().strip()
        # Remove leading "how often", "how much", etc.
        for prefix in ['how often ', 'how much ', 'how well ', 'how likely ', 
                       'to what extent ', 'in your opinion ']:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        # Slugify and truncate
        slug = slugify(text)[:100]
        return slug


class ResponseScaleLabel(models.Model):
    """
    Reference table mapping numeric values to text labels for each question.
    E.g., for a 1-5 scale: 1="Almost never", 2="Once in a while", etc.
    This avoids storing duplicate text labels in every response row.
    """
    question = models.ForeignKey(
        SurveyQuestion,
        on_delete=models.CASCADE,
        related_name='scale_labels'
    )
    value = models.PositiveSmallIntegerField(
        help_text="Numeric value (1, 2, 3, etc.)"
    )
    label = models.CharField(
        max_length=100,
        help_text="Text label for this value (e.g., 'Almost always')"
    )

    class Meta:
        unique_together = ['question', 'value']
        ordering = ['question', 'value']
        verbose_name = 'Response Scale Label'
        verbose_name_plural = 'Response Scale Labels'

    def __str__(self):
        return f"{self.value}: {self.label}"


class ImportLog(models.Model):
    """
    Tracks each CSV import operation for auditing and debugging.
    """
    name = models.CharField(
        max_length=255,
        help_text="User-provided name for this import"
    )
    survey_definition = models.ForeignKey(
        SurveyDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='imports'
    )
    school_year = models.CharField(
        max_length=9,
        help_text="School year for this import"
    )
    testing_window = models.CharField(
        max_length=10,
        choices=TestingWindow.choices
    )
    filename = models.CharField(
        max_length=255,
        help_text="Original filename uploaded"
    )
    file_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 hash of file contents for duplicate detection"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='data_imports'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ImportStatus.choices,
        default=ImportStatus.PENDING
    )
    
    # Statistics
    total_rows = models.PositiveIntegerField(default=0)
    records_processed = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    records_skipped = models.PositiveIntegerField(default=0)
    students_matched = models.PositiveIntegerField(default=0)
    students_unmatched = models.PositiveIntegerField(default=0)
    questions_detected = models.PositiveIntegerField(default=0)
    
    # Error tracking
    error_count = models.PositiveIntegerField(default=0)
    error_log = models.JSONField(
        default=list,
        help_text="List of error messages and row numbers"
    )
    warnings = models.JSONField(
        default=list,
        help_text="List of warning messages"
    )
    
    # Publication control
    ready_for_exploration = models.BooleanField(
        default=False,
        help_text="Whether this import's data is ready to be shown in the Data Exploration app"
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this import was published for exploration"
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='published_imports'
    )

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Import Log'
        verbose_name_plural = 'Import Logs'

    def __str__(self):
        return f"{self.name} - {self.uploaded_at.strftime('%Y-%m-%d %H:%M')}"

    @staticmethod
    def compute_file_hash(file_content):
        """Compute SHA256 hash of file content."""
        if isinstance(file_content, str):
            file_content = file_content.encode('utf-8')
        return hashlib.sha256(file_content).hexdigest()


class SurveyResponse(models.Model):
    """
    Stores individual student responses to survey questions.
    One row per student per question. Only stores what's needed for analysis.
    Text labels for response values are stored in ResponseScaleLabel.
    """
    # Student identification (enough to link back to studata)
    student_id = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Local Student ID from the CSV"
    )
    ssid = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="State Student ID (if matched to studata.CurrentStudent)"
    )
    school_code = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="School ID from the CSV (references school.sis_key)"
    )
    
    # Link to question (survey_definition accessible via question.survey_definition)
    question = models.ForeignKey(
        SurveyQuestion,
        on_delete=models.CASCADE,
        related_name='responses'
    )
    
    # The response value - this is what matters for analysis
    value = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Numeric response value (1-5 or 1-7)"
    )
    
    # Import tracking
    import_log = models.ForeignKey(
        ImportLog,
        on_delete=models.SET_NULL,
        null=True,
        related_name='responses'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['question__order']
        # One response per student per question
        unique_together = ['student_id', 'question']
        indexes = [
            models.Index(fields=['student_id']),
            models.Index(fields=['ssid']),
            models.Index(fields=['question', 'value']),
        ]
        verbose_name = 'Survey Response'
        verbose_name_plural = 'Survey Responses'

    def __str__(self):
        return f"{self.student_id}: Q{self.question.order + 1}={self.value}"
    
    @property
    def text_label(self):
        """Get the text label for this response value from the scale labels."""
        if self.value is None:
            return None
        label = self.question.scale_labels.filter(value=self.value).first()
        return label.label if label else str(self.value)


class APImportStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'
    PARTIAL = 'PARTIAL', 'Partially Completed'


class APExamCode(models.Model):
    """Reference table for College Board AP exam codes."""
    code = models.CharField(max_length=4, primary_key=True, help_text="Two-digit College Board exam code (e.g., '07')")
    name = models.CharField(max_length=255, help_text="Full exam name (e.g., 'United States History')")

    class Meta:
        ordering = ['code']
        verbose_name = 'AP Exam Code'
        verbose_name_plural = 'AP Exam Codes'

    def __str__(self):
        return f"{self.code}: {self.name}"


class APAwardType(models.Model):
    """Reference table for College Board AP award types."""
    code = models.CharField(max_length=4, primary_key=True, help_text="Two-digit College Board award code (e.g., '01')")
    description = models.CharField(max_length=255, help_text="Award description (e.g., 'AP Scholar')")

    class Meta:
        ordering = ['code']
        verbose_name = 'AP Award Type'
        verbose_name_plural = 'AP Award Types'

    def __str__(self):
        return f"{self.code}: {self.description}"


class APImportLog(models.Model):
    """Tracks each AP Student Datafile import operation."""
    name = models.CharField(max_length=255, help_text="User-provided name for this import")
    school_year = models.CharField(max_length=9, help_text="School year (e.g., '2025-2026')")
    filename = models.CharField(max_length=255, help_text="Original filename uploaded")
    file_hash = models.CharField(max_length=64, blank=True, help_text="SHA256 hash for duplicate detection")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ap_data_imports'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=APImportStatus.choices,
        default=APImportStatus.PENDING
    )

    total_students = models.PositiveIntegerField(default=0)
    exams_detected = models.PositiveIntegerField(default=0, help_text="Total exam results found in CSV")
    exams_created = models.PositiveIntegerField(default=0, help_text="New exam results inserted into DB")
    exams_skipped = models.PositiveIntegerField(default=0, help_text="Exam results that already existed")
    awards_detected = models.PositiveIntegerField(default=0, help_text="Total awards found in CSV")
    awards_created = models.PositiveIntegerField(default=0, help_text="New awards inserted into DB")
    awards_skipped = models.PositiveIntegerField(default=0, help_text="Awards that already existed")
    students_matched = models.PositiveIntegerField(default=0)
    students_unmatched = models.PositiveIntegerField(default=0)
    records_processed = models.PositiveIntegerField(default=0, help_text="Student rows processed")

    manual_overrides = models.JSONField(
        default=dict,
        help_text="Manually-entered SSID overrides (e.g., {'ap_number': 'ssid'})"
    )
    error_count = models.PositiveIntegerField(default=0)
    error_log = models.JSONField(default=list, help_text="List of error messages")
    warnings = models.JSONField(default=list, help_text="List of warning messages")

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'AP Import Log'
        verbose_name_plural = 'AP Import Logs'

    def __str__(self):
        return f"{self.name} - {self.uploaded_at.strftime('%Y-%m-%d %H:%M')}"

    @staticmethod
    def compute_file_hash(file_content):
        if isinstance(file_content, str):
            file_content = file_content.encode('utf-8')
        return hashlib.sha256(file_content).hexdigest()


class APStudentAward(models.Model):
    """Awards earned by a student during an AP test year."""
    import_log = models.ForeignKey(
        APImportLog, on_delete=models.CASCADE, related_name='awards'
    )
    ssid = models.CharField(max_length=50, db_index=True, blank=True, help_text="Matched State Student ID")
    ap_number = models.CharField(max_length=50, db_index=True)
    award_type = models.ForeignKey(APAwardType, on_delete=models.PROTECT)
    award_year = models.CharField(max_length=9, help_text="School year (e.g., '2025-2026')")

    class Meta:
        unique_together = ['ap_number', 'award_type', 'award_year']
        indexes = [
            models.Index(fields=['ssid']),
            models.Index(fields=['ap_number']),
        ]
        verbose_name = 'AP Student Award'
        verbose_name_plural = 'AP Student Awards'

    def __str__(self):
        return f"{self.ap_number}: {self.award_type.description} ({self.award_year})"


class APExamResult(models.Model):
    """Individual AP exam result linked to a student by SSID."""
    import_log = models.ForeignKey(
        APImportLog, on_delete=models.CASCADE, related_name='exam_results'
    )
    ssid = models.CharField(max_length=50, db_index=True, blank=True, help_text="Matched State Student ID")
    ap_number = models.CharField(max_length=50, db_index=True)
    exam = models.ForeignKey(APExamCode, on_delete=models.PROTECT)
    admin_year = models.CharField(max_length=9, help_text="School year of exam administration")
    exam_grade = models.CharField(max_length=10, help_text="Exam grade (1-5, or blank if not yet scored)")
    school_code = models.CharField(max_length=50, blank=True, help_text="AI institution school code")
    ai_institution_name = models.CharField(max_length=255, blank=True)
    student_grade_level = models.CharField(max_length=50, blank=True)

    class Meta:
        unique_together = ['ap_number', 'exam', 'admin_year']
        indexes = [
            models.Index(fields=['ssid']),
            models.Index(fields=['ap_number']),
            models.Index(fields=['exam', 'admin_year']),
        ]
        verbose_name = 'AP Exam Result'
        verbose_name_plural = 'AP Exam Results'

    def __str__(self):
        return f"{self.ap_number}: {self.exam.name} ({self.admin_year}) = {self.exam_grade}"
