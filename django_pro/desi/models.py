from django.db import models
from django.conf import settings


class Accommodation(models.Model):
    """Master list of accommodations available for any assessment. Acts as the screening question."""
    name = models.CharField(max_length=255, help_text="e.g., Text-to-Speech")
    code = models.CharField(max_length=50, blank=True, help_text="Short code for reporting, e.g. TTS")
    column_letter = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text="Optional upload column letter this support maps to (e.g. 'B', 'AB')."
    )
    tag = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Source import tag (e.g. 'CAASPP', 'ELPAC') this support came from."
    )
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
    """An assessment config requiring accommodations (e.g. Smarter Balanced Math)."""
    name = models.CharField(max_length=255, help_text="e.g., Smarter Balanced Math")
    subject = models.CharField(max_length=255, blank=True, help_text="e.g., Math, ELA, Science")
    tag = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Source import tag (e.g. 'CAASPP', 'ELPAC') this assessment came from."
    )
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
        ordering = ['name']
        unique_together = ('name', 'subject')

    def __str__(self):
        return self.name


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
    selected_accommodations = models.ManyToManyField(
        Accommodation,
        blank=True,
        related_name='students_with_direct_support',
        help_text='Supports with no configured options, stored as simple checkbox selections.',
    )
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


class DesiSchoolAccess(models.Model):
    """Per-school teacher editing policy for DESI, managed by Testing Coordinators."""
    school_state_id = models.CharField(max_length=50, unique=True, db_index=True)
    teachers_can_edit = models.BooleanField(
        default=True,
        help_text="If unchecked, teachers at this school can view DESI but cannot save supports or completions.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='desi_school_access_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.school_state_id} - {'Edit' if self.teachers_can_edit else 'View only'}"


class DesiSeis(models.Model):
    """
    Read-only source record of SEIS assessment settings for a student.

    Maps to the externally-populated ``desi_seis`` table (not managed by
    Django). Each row belongs to one test program (``test_group``: caaspp /
    elpac), names the DESI assessments the settings apply to (``assessments``)
    and stores the selected support option codes per setting (``settings``).
    """
    id = models.CharField(max_length=64, primary_key=True)
    ssid = models.CharField(max_length=32, db_index=True)
    test_group = models.CharField(max_length=32)
    settings = models.JSONField(default=dict, blank=True)
    assessments = models.JSONField(default=list, blank=True)

    class Meta:
        managed = False
        db_table = 'desi_seis'

    def __str__(self):
        return f"SEIS settings for {self.ssid} ({self.test_group})"


class DesiSeisDisplaySettings(models.Model):
    """
    Project-level control over which assessments surface SEIS settings on the
    student workspace.

    Only assessments ticked here have their SEIS settings displayed (as locks
    on editable cards or as read-only sections for tests that are not active).
    This makes the selection explicit and independent of each assessment's
    ``is_active`` flag.  An empty selection means SEIS settings are not shown
    for any assessment.
    """
    assessments = models.ManyToManyField(
        Assessment,
        blank=True,
        related_name='seis_display_selected',
        help_text="Assessments whose SEIS settings should be displayed for students, "
                  "regardless of whether the assessment is active.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='desi_seis_display_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'SEIS Display Settings'
        verbose_name_plural = 'SEIS Display Settings'

    def __str__(self):
        count = self.assessments.count()
        suffix = '' if count == 1 else 's'
        return f"SEIS display settings ({count} assessment{suffix})"


class SupportRule(models.Model):
    """
    A named demographic criteria rule. Supports attached via ``supports`` are
    hidden for a student when this rule's criteria are not met.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    supports = models.ManyToManyField(
        Accommodation,
        blank=True,
        related_name='visibility_rules',
        help_text="Designated supports hidden when this rule's criteria fail.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class SupportRuleCriteria(models.Model):
    """One demographic condition belonging to a SupportRule (all conditions AND together)."""
    rule = models.ForeignKey(SupportRule, on_delete=models.CASCADE, related_name='criteria')
    field_name = models.CharField(max_length=50, choices=AssessmentCriteria.FIELD_CHOICES)
    operator = models.CharField(max_length=20, choices=AssessmentCriteria.OPERATOR_CHOICES)
    value = models.CharField(max_length=255, help_text="Comparison value (e.g. '03,04,05' or 'Y')")

    def __str__(self):
        return f"{self.get_field_name_display()} {self.get_operator_display()} '{self.value}'"


class AccommodationResource(models.Model):
    """Extra resource links (video/page/pdf) attached to a designated support."""
    KIND_CHOICES = [
        ('youtube', 'YouTube'),
        ('video', 'Video'),
        ('page', 'Web Page'),
        ('pdf', 'PDF / Document'),
    ]
    accommodation = models.ForeignKey(
        Accommodation,
        on_delete=models.CASCADE,
        related_name='resources',
    )
    label = models.CharField(max_length=255, blank=True, help_text="Short label shown on the button, e.g. 'Video guide'")
    url = models.URLField(max_length=500, help_text="Link to a video, page, or PDF document")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='page')
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        label = self.label or self.url
        return f"{label} ({self.get_kind_display()})"

    def get_embed_url(self):
        """Return an embeddable URL for youtube; other types embed as-is when possible."""
        if self.kind == 'youtube':
            import re
            patterns = [
                r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([^&\s]+)',
                r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([^\?\s]+)',
                r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([^\?\s]+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, self.url)
                if match:
                    return f"https://www.youtube.com/embed/{match.group(1)}"
            return None
        return self.url
