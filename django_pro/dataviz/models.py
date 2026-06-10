from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Dataset(models.Model):
    """A visual dataset built by joining tables and applying filter rules."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    base_table = models.CharField(max_length=100)  # E.g. 'current_student'
    distinct = models.BooleanField(default=True)
    
    # joins: [{"table": "results", "join_type": "left", "conditions": [{"on_left": "current_student.ssid", "on_right": "results.ssid"}]}]
    joins = models.JSONField(default=list, blank=True)
    
    # filters: [{"column": "current_student.grade", "operator": "exact", "value": "12"}]
    filters = models.JSONField(default=list, blank=True)
    
    # columns: ["current_student.ssid", "current_student.student_name", "results.test_name", "results.ss"]
    columns = models.JSONField(default=list, blank=True)
    
    sql_preview = models.TextField(blank=True)
    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='datasets'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            'id': self.pk,
            'name': self.name,
            'description': self.description,
            'base_table': self.base_table,
            'distinct': self.distinct,
            'joins': self.joins or [],
            'filters': self.filters or [],
            'columns': self.columns or [],
            'sql_preview': self.sql_preview,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class SavedChart(models.Model):
    """A chart configuration saved by a user, powered by a Dataset, SavedQuery, or Raw SQL."""

    SOURCE_DATASET = 'dataset'
    SOURCE_QUERY = 'query'
    SOURCE_SQL = 'sql'
    SOURCE_CHOICES = [
        (SOURCE_DATASET, 'Visual Dataset'),
        (SOURCE_QUERY, 'Saved Query'),
        (SOURCE_SQL, 'Raw SQL'),
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    chart_type = models.CharField(max_length=50, default='bar')  # bar, line, pie, scatter, radar, funnel, gauge, heatmap
    
    source_type = models.CharField(max_length=20, default=SOURCE_DATASET, choices=SOURCE_CHOICES)
    dataset = models.ForeignKey(Dataset, on_delete=models.SET_NULL, null=True, blank=True, related_name='charts')
    query_id = models.IntegerField(null=True, blank=True)  # SavedQuery pk when source_type=query
    source_sql = models.TextField(blank=True)  # raw SQL when source_type=sql
    
    # config: x_axis_column, y_axis_column, y_axis_aggregation, series_column, colors, labels, legends, stacked
    config = models.JSONField(default=dict, blank=True)
    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_charts',
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            'id': self.pk,
            'name': self.name,
            'description': self.description,
            'chart_type': self.chart_type,
            'source_type': self.source_type,
            'dataset_id': self.dataset_id,
            'query_id': self.query_id,
            'source_sql': self.source_sql,
            'config': self.config or {},
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class DataVizPage(models.Model):
    """A publishable layout page that embeds charts + rich text grids."""

    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_ACTIVE, 'Active'),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default=STATUS_DRAFT, choices=STATUS_CHOICES)
    
    # Global page filters: [{"id": "school", "label": "School", "column": "current_student.school_state_id", "type": "select"}]
    page_filters = models.JSONField(default=list, blank=True)
    
    # Link to global page routing / navigation structure
    linked_site_page = models.OneToOneField(
        'users.Page',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dataviz_page',
    )
    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dataviz_pages',
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def to_dict(self, include_sections=False):
        d = {
            'id': self.pk,
            'title': self.title,
            'slug': self.slug,
            'description': self.description,
            'status': self.status,
            'page_filters': self.page_filters or [],
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
        if include_sections:
            d['sections'] = [s.to_dict() for s in self.sections.all()]
        return d


class DataVizPageSection(models.Model):
    """An ordered section / widget block within a DataVizPage, layout via GridStack."""

    TYPE_CHART = 'chart'
    TYPE_HTML = 'html'
    TYPE_DIVIDER = 'divider'
    TYPE_CHOICES = [
        (TYPE_CHART, 'Chart'),
        (TYPE_HTML, 'Text / HTML'),
        (TYPE_DIVIDER, 'Divider'),
    ]

    page = models.ForeignKey(DataVizPage, on_delete=models.CASCADE, related_name='sections')
    element_type = models.CharField(max_length=20, default=TYPE_CHART, choices=TYPE_CHOICES)
    
    # Widget contents
    chart = models.ForeignKey(
        SavedChart,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='page_sections',
    )
    html_content = models.TextField(blank=True, default='')
    
    # GridStack layout (12-column grid coordinate positions)
    gs_x = models.IntegerField(default=0)
    gs_y = models.IntegerField(default=0)
    gs_w = models.IntegerField(default=6)
    gs_h = models.IntegerField(default=4)
    
    position = models.IntegerField(default=0)

    class Meta:
        ordering = ['position']

    def __str__(self):
        return f"{self.page.title} — {self.element_type} #{self.position}"

    def to_dict(self):
        d = {
            'id': self.pk,
            'element_type': self.element_type,
            'html_content': self.html_content,
            'gs_x': self.gs_x,
            'gs_y': self.gs_y,
            'gs_w': self.gs_w,
            'gs_h': self.gs_h,
            'position': self.position,
        }
        if self.chart:
            d['chart'] = self.chart.to_dict()
        else:
            d['chart'] = None
        return d
