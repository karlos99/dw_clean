"""
Course catalog organization.

Layer A: ``CourseCatalogEntry`` maps the external ``courses_catalog`` table (ETL-owned).
Layer B: tags → categories → disciplines, district-wide class levels,
and placements linking a course to tag + category + discipline + level.
"""
from django.core.exceptions import ValidationError
from django.db import models


class CourseCatalogEntry(models.Model):
    """Read-only district course master list (``courses_catalog`` table)."""

    course_key = models.TextField(primary_key=True)
    course_id = models.TextField(blank=True, null=True)
    state_course_code = models.TextField(blank=True, null=True)
    course_title = models.TextField(blank=True, null=True)
    state_course_name = models.TextField(blank=True, null=True)
    course_level = models.TextField(blank=True, null=True)
    credit = models.TextField(blank=True, null=True)
    subject_area_1_desc = models.TextField(blank=True, null=True)
    subject_area_2_desc = models.TextField(blank=True, null=True)
    university_area_1 = models.TextField(blank=True, null=True)
    university_area_2 = models.TextField(blank=True, null=True)
    college_area_1 = models.TextField(blank=True, null=True)
    college_area_2 = models.TextField(blank=True, null=True)
    is_cte = models.TextField(blank=True, null=True)
    cte_course_level = models.TextField(blank=True, null=True)
    ap_indicator = models.TextField(blank=True, null=True)
    ib_indicator = models.TextField(blank=True, null=True)
    dual_credit = models.TextField(blank=True, null=True)
    technical_course = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'courses_catalog'
        verbose_name = 'Course catalog entry'
        verbose_name_plural = 'Course catalog entries'

    def __str__(self):
        return self.display_title

    @property
    def display_title(self):
        return (self.course_title or self.state_course_name or self.course_key or '').strip()

    def canonical_schedule_match_values(self):
        values = []
        for val in (self.course_key, self.course_id, self.state_course_code):
            if val and str(val).strip():
                values.append(str(val).strip())
        return values


class CtePathwayEntry(models.Model):
    """Read-only CTE pathway rows (``coursecatalog_cte_pathways`` table, ETL-owned)."""

    pathway_row_key = models.TextField(primary_key=True)
    cte_program_key = models.TextField(blank=True, null=True)
    cte_pathway = models.TextField(blank=True, null=True)
    pathway_code = models.TextField(blank=True, null=True)
    pathway_state_code = models.TextField(blank=True, null=True)
    career_cluster_code = models.TextField(blank=True, null=True)
    career_cluster = models.TextField(blank=True, null=True)
    course_key = models.TextField(blank=True, null=True)
    course_id = models.TextField(blank=True, null=True)
    course_title = models.TextField(blank=True, null=True)
    state_course_code = models.TextField(blank=True, null=True)
    cte_course_level = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'coursecatalog_cte_pathways'
        verbose_name = 'CTE pathway row'
        verbose_name_plural = 'CTE pathway rows'

    def __str__(self):
        return f'{self.course_id} → {self.cte_pathway}'


class CatalogTag(models.Model):
    """Top-level grouping (e.g. VAPA)."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class CatalogCategory(models.Model):
    """Mid-level grouping under a tag (e.g. Performing Arts under VAPA)."""

    tag = models.ForeignKey(
        CatalogTag,
        on_delete=models.CASCADE,
        related_name='categories',
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Catalog categories'
        constraints = [
            models.UniqueConstraint(
                fields=['tag', 'slug'],
                name='uniq_catalog_category_tag_slug',
            ),
            models.UniqueConstraint(
                fields=['tag', 'name'],
                name='uniq_catalog_category_tag_name',
            ),
        ]

    def __str__(self):
        return f'{self.tag.name} → {self.name}'


class CatalogDiscipline(models.Model):
    """Fine-grained grouping under a category (e.g. Dance under Performing Arts)."""

    category = models.ForeignKey(
        CatalogCategory,
        on_delete=models.CASCADE,
        related_name='disciplines',
    )
    name = models.CharField(max_length=100)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'name'],
                name='uniq_catalog_discipline_category_name',
            ),
        ]

    def __str__(self):
        return f'{self.category.tag.name} → {self.category.name} → {self.name}'

    @property
    def tag(self):
        return self.category.tag


class CatalogLevel(models.Model):
    """District-wide class level labels (independent of tag, category, discipline)."""

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']
        constraints = [
            models.UniqueConstraint(fields=['name'], name='uniq_catalog_level_name'),
        ]

    def __str__(self):
        return self.name


class CatalogPlacement(models.Model):
    catalog_entry = models.ForeignKey(
        CourseCatalogEntry,
        on_delete=models.CASCADE,
        related_name='placements',
        db_constraint=False,
    )
    tag = models.ForeignKey(
        CatalogTag,
        on_delete=models.CASCADE,
        related_name='placements',
    )
    category = models.ForeignKey(
        CatalogCategory,
        on_delete=models.CASCADE,
        related_name='placements',
        blank=True,
        null=True,
    )
    discipline = models.ForeignKey(
        CatalogDiscipline,
        on_delete=models.CASCADE,
        related_name='placements',
        blank=True,
        null=True,
    )
    level = models.ForeignKey(
        CatalogLevel,
        on_delete=models.CASCADE,
        related_name='placements',
        blank=True,
        null=True,
    )
    notes = models.TextField(blank=True)
    effective_from = models.CharField(max_length=20, blank=True)
    effective_to = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['catalog_entry', 'tag', 'category', 'discipline', 'level'],
                name='uniq_placement_entry_taxonomy_level',
                nulls_distinct=False,
            ),
        ]

    def taxonomy_path_parts(self) -> list[str]:
        parts = [self.tag.name]
        if self.category_id:
            parts.append(self.category.name)
        if self.discipline_id:
            parts.append(self.discipline.name)
        if self.level_id:
            parts.append(self.level.name)
        return parts

    def __str__(self):
        return f'{self.catalog_entry} → {" → ".join(self.taxonomy_path_parts())}'

    def clean(self):
        if not self.tag_id:
            return
        if self.discipline_id and not self.category_id:
            raise ValidationError('Discipline requires a category.')
        if self.category_id and self.category.tag_id != self.tag_id:
            raise ValidationError('Category must belong to the selected tag.')
        if self.discipline_id and self.discipline.category_id != self.category_id:
            raise ValidationError('Discipline must belong to the selected category.')


class CatalogPath(models.Model):
    tag = models.ForeignKey(
        CatalogTag,
        on_delete=models.CASCADE,
        related_name='paths',
    )
    category = models.ForeignKey(
        CatalogCategory,
        on_delete=models.CASCADE,
        related_name='paths',
    )
    discipline = models.ForeignKey(
        CatalogDiscipline,
        on_delete=models.CASCADE,
        related_name='paths',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['tag__order', 'category__order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['tag', 'category', 'discipline', 'name'],
                name='uniq_catalog_path_scope_name',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if not self.tag_id or not self.category_id or not self.discipline_id:
            return
        if self.category.tag_id != self.tag_id:
            raise ValidationError('Category must belong to the selected tag.')
        if self.discipline.category_id != self.category_id:
            raise ValidationError('Discipline must belong to the selected category.')


class CatalogPathColumn(models.Model):
    """A named, ordered column/stage in a path (e.g. Introduction, Capstone).

    Custom paths may define any number of columns; the CTE stage names are only
    used to seed a new path.
    """

    path = models.ForeignKey(
        CatalogPath,
        on_delete=models.CASCADE,
        related_name='columns',
    )
    name = models.CharField(max_length=100)
    order = models.IntegerField(default=0)
    year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text='Optional progression year shown on the timeline.',
    )

    class Meta:
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['path', 'name'],
                name='uniq_catalog_path_column_name',
            ),
        ]

    def __str__(self):
        return f'{self.path.name} · {self.name}'


class CatalogPathStep(models.Model):
    path = models.ForeignKey(
        CatalogPath,
        on_delete=models.CASCADE,
        related_name='steps',
    )
    column = models.ForeignKey(
        CatalogPathColumn,
        on_delete=models.CASCADE,
        related_name='steps',
        blank=True,
        null=True,
    )
    catalog_entry = models.ForeignKey(
        CourseCatalogEntry,
        on_delete=models.CASCADE,
        related_name='path_steps',
        db_constraint=False,
    )
    sequence = models.PositiveIntegerField(default=1)
    label = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['sequence', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['path', 'column', 'sequence'],
                name='uniq_catalog_path_step_sequence',
                nulls_distinct=False,
            ),
            models.UniqueConstraint(
                fields=['path', 'column', 'catalog_entry'],
                name='uniq_catalog_path_step_entry',
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        return f'{self.path.name} #{self.sequence}: {self.catalog_entry}'
