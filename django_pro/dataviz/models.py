from django.conf import settings
from django.db import models


class SavedChart(models.Model):
    """A chart configuration saved by a user — can be powered by a Saved Query or raw SQL."""

    SOURCE_QUERY = 'query'
    SOURCE_SQL = 'sql'
    SOURCE_CHOICES = [
        (SOURCE_QUERY, 'Saved Query'),
        (SOURCE_SQL,   'SQL'),
    ]

    name         = models.CharField(max_length=200)
    description  = models.TextField(blank=True)
    chart_type   = models.CharField(max_length=50, default='bar')
    config       = models.JSONField(default=dict)          # full chart_config payload
    source_type  = models.CharField(max_length=20, default=SOURCE_QUERY, choices=SOURCE_CHOICES)
    source_id    = models.IntegerField(null=True, blank=True)   # SavedQuery pk when source_type=query
    source_sql   = models.TextField(blank=True)                 # raw SQL when source_type=sql
    source_filters = models.JSONField(default=dict)             # saved load-dataset filters
    column_defs  = models.JSONField(default=dict)               # snapshot of column_definitions
    owner        = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_charts',
    )
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            'id':          self.pk,
            'name':        self.name,
            'description': self.description,
            'chart_type':  self.chart_type,
            'config':      self.config,
            'source_type': self.source_type,
            'source_id':   self.source_id,
            'source_sql':  self.source_sql,
            'source_filters': self.source_filters,
            'column_defs': self.column_defs,
            'created_at':  self.created_at.isoformat(),
            'updated_at':  self.updated_at.isoformat(),
        }


class Dashboard(models.Model):
    """A named collection of saved charts displayed in a grid."""

    name        = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dashboards',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    def to_dict(self, include_charts=False):
        d = {
            'id':          self.pk,
            'name':        self.name,
            'description': self.description,
            'created_at':  self.created_at.isoformat(),
            'updated_at':  self.updated_at.isoformat(),
        }
        if include_charts:
            d['charts'] = [slot.to_dict() for slot in self.chart_slots.select_related('chart').all()]
        return d


class DashboardChart(models.Model):
    """Junction between a Dashboard and a SavedChart, with layout metadata.

    When ``element_type`` is *html*, the ``chart`` FK may be NULL and
    ``html_content`` stores arbitrary rich-text / HTML for the slot.
    ``slot_filters`` stores per-slot filter overrides that narrow the chart's
    data without touching the underlying SavedChart.
    """

    ELEMENT_CHART = 'chart'
    ELEMENT_HTML  = 'html'
    ELEMENT_TYPE_CHOICES = [
        (ELEMENT_CHART, 'Chart'),
        (ELEMENT_HTML,  'HTML element'),
    ]

    WIDTH_FULL  = 'full'
    WIDTH_HALF  = 'half'
    WIDTH_THIRD = 'third'
    WIDTH_CHOICES = [
        (WIDTH_FULL,  'Full width'),
        (WIDTH_HALF,  'Half width'),
        (WIDTH_THIRD, 'One third'),
    ]

    HEIGHT_SM   = 'sm'
    HEIGHT_MD   = 'md'
    HEIGHT_LG   = 'lg'
    HEIGHT_XL   = 'xl'
    HEIGHT_CHOICES = [
        (HEIGHT_SM,  'Small  (200px)'),
        (HEIGHT_MD,  'Medium (320px)'),
        (HEIGHT_LG,  'Large  (460px)'),
        (HEIGHT_XL,  'X-Large (600px)'),
    ]

    dashboard    = models.ForeignKey(Dashboard,  on_delete=models.CASCADE, related_name='chart_slots')
    chart        = models.ForeignKey(SavedChart, on_delete=models.CASCADE, related_name='dashboard_slots',
                                     null=True, blank=True)
    element_type = models.CharField(max_length=10, default=ELEMENT_CHART, choices=ELEMENT_TYPE_CHOICES)
    html_content = models.TextField(blank=True, default='')
    slot_filters = models.JSONField(default=list, blank=True)  # [{col, op, val, values}]
    position     = models.IntegerField(default=0)
    width_class  = models.CharField(max_length=10, default=WIDTH_HALF, choices=WIDTH_CHOICES)
    height_class = models.CharField(max_length=10, default=HEIGHT_MD, choices=HEIGHT_CHOICES)
    show_title   = models.BooleanField(default=True)

    class Meta:
        ordering = ['position']

    def to_dict(self):
        d = {
            'slot_id':      self.pk,
            'element_type': self.element_type,
            'position':     self.position,
            'width_class':  self.width_class,
            'height_class': self.height_class,
            'show_title':   self.show_title,
            'slot_filters': self.slot_filters or [],
            'html_content': self.html_content,
        }
        if self.chart:
            d['chart'] = self.chart.to_dict()
        else:
            d['chart'] = None
        return d
