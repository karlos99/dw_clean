from django.db import models
from django.conf import settings


class Accommodation(models.Model):
    """Master list of accommodations available for any assessment. Acts as the screening question."""
    name = models.CharField(max_length=255, unique=True, help_text="e.g., Text-to-Speech")
    code = models.CharField(max_length=50, blank=True, help_text="Short code for reporting, e.g. TTS")
    category = models.CharField(max_length=100, blank=True, help_text="e.g., Presentation, Response, Setting, Timing")
    description = models.TextField(blank=True)
    
    question_text = models.CharField(
        max_length=255, 
        blank=True, 
        help_text="The question text shown to the teacher (e.g. 'Does this student require Text-to-Speech?')"
    )
    video_url = models.URLField(
        blank=True, 
        null=True, 
        help_text="YouTube video link explaining this support/question"
    )
    order = models.IntegerField(default=0, help_text="Order in which this accommodation is displayed")
    depends_on_accommodation = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dependent_accommodations',
        help_text="Optional parent designated support that controls visibility of this support."
    )
    depends_on_option = models.ForeignKey(
        'AccommodationOption',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dependent_accommodations',
        help_text="When this option is selected on the parent support, this support is hidden and its entry is removed."
    )
    dependency_assessments = models.ManyToManyField(
        'Assessment',
        blank=True,
        related_name='dependency_scoped_accommodations',
        help_text="Optional subset of assessments where this dependency rule applies. Leave empty to apply to all."
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'category', 'name']

    def __str__(self):
        if self.code:
            return f"{self.name} ({self.code})"
        return self.name

    def get_youtube_embed_url(self):
        """Helper to extract YouTube video ID and return embed URL."""
        if not self.video_url:
            return None
        url = self.video_url.strip()
        # Parse common youtube formats
        import re
        patterns = [
            r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([^&\s]+)',
            r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([^\?\s]+)',
            r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([^\?\s]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return f"https://www.youtube.com/embed/{match.group(1)}"
        return None

    def is_dependency_valid(self):
        if not self.depends_on_option_id:
            return True
        if not self.depends_on_accommodation_id:
            return False
        return self.depends_on_option.accommodation_id == self.depends_on_accommodation_id


class AccommodationOption(models.Model):
    """Specific options for an accommodation, e.g. Scientific for Calculator."""
    accommodation = models.ForeignKey(Accommodation, on_delete=models.CASCADE, related_name='options')
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, blank=True, help_text="Short option code, e.g. SCI")

    class Meta:
        ordering = ['name']
        unique_together = ('accommodation', 'name')

    def __str__(self):
        if self.code:
            return f"{self.accommodation.name} - {self.name} ({self.code})"
        return f"{self.accommodation.name} - {self.name}"


class Assessment(models.Model):
    """An assessment config requiring accommodations (e.g. SBAC Math 2026)."""
    name = models.CharField(max_length=255, help_text="e.g., Smarter Balanced Math")
    subject = models.CharField(max_length=255, blank=True, help_text="e.g., Math, ELA, Science")
    school_year = models.CharField(max_length=20, db_index=True, help_text="e.g., 2025-2026")
    description = models.TextField(blank=True)
    supported_options = models.ManyToManyField(
        AccommodationOption,
        blank=True,
        related_name='assessments',
        help_text="Accommodation options allowed for this assessment"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['school_year', 'name']
        unique_together = ('name', 'school_year')

    def __str__(self):
        return f"{self.name} ({self.school_year})"


class AssessmentCriteria(models.Model):
    """Eligibility rules applied dynamically to student demographics."""
    FIELD_CHOICES = [
        ('grade', 'Grade Level'),
        ('gender', 'Gender'),
        ('race', 'Race/Ethnicity'),
        ('ell', 'English Language Learner (ELL)'),
        ('sed', 'Socioeconomically Disadvantaged (SED)'),
        ('foster', 'Foster Youth'),
        ('homeless', 'Homeless'),
        ('sped', 'Special Education (SPED)'),
        ('gate', 'Gifted and Talented (GATE)'),
        ('in_504', 'Section 504 Plan'),
        ('dli', 'Dual Language Immersion (DLI)'),
    ]

    OPERATOR_CHOICES = [
        ('is', 'Equal To (Is)'),
        ('is_not', 'Not Equal To (IsNot)'),
        ('in', 'In List (Comma separated)'),
        ('not_in', 'Not In List (Comma separated)'),
    ]

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='criteria')
    field_name = models.CharField(max_length=50, choices=FIELD_CHOICES)
    operator = models.CharField(max_length=20, choices=OPERATOR_CHOICES)
    value = models.CharField(max_length=255, help_text="Comparison value (e.g. '03,04,05' or 'Y')")

    def __str__(self):
        return f"{self.get_field_name_display()} {self.get_operator_display()} '{self.value}'"


class StudentAccommodation(models.Model):
    """Student-level global accommodations. Assigned once, applies to all matching tests."""
    student_annual_key = models.CharField(max_length=32, unique=True, db_index=True)
    options = models.ManyToManyField(AccommodationOption, blank=True, related_name='students')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_accommodations_updated'
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Accommodations for Student {self.student_annual_key}"


class StudentAssessmentCompletion(models.Model):
    """Completion status tracking per assessment per student."""
    student_annual_key = models.CharField(max_length=32, db_index=True)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='completions')
    is_completed = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_completions_updated'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('student_annual_key', 'assessment')

    def __str__(self):
        return f"Completion for {self.student_annual_key} on {self.assessment.name}"


class DesiExportConfiguration(models.Model):
    """Stores reusable DESI export configurations for CSV exports."""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='desi_export_configurations',
    )
    config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'DESI Export Configuration'
        verbose_name_plural = 'DESI Export Configurations'

    def __str__(self):
        return self.name
