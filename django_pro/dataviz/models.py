from django.conf import settings
from django.db import models
from django.utils.text import slugify


class SavedChart(models.Model):
    """A chart configuration saved by a user — can be powered by a Saved Query or raw SQL."""

    SOURCE_QUERY = 'query'
    SOURCE_SQL = 'sql'
    SOURCE_COLUMNS = 'columns'
    SOURCE_CHOICES = [
        (SOURCE_QUERY,   'Saved Query'),
        (SOURCE_SQL,     'SQL'),
        (SOURCE_COLUMNS, 'Column Picker'),
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
    # Drill-through config
    drill_through_columns    = models.JSONField(default=list, blank=True)  # column keys to show in drill table
    drill_through_filter_map = models.JSONField(default=dict, blank=True)  # {x_col: 'grade', series_col: 'race'}
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
            'drill_through_columns':    self.drill_through_columns,
            'drill_through_filter_map': self.drill_through_filter_map,
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
    # GridStack layout (x, y in grid units; w, h in grid units; 12-column grid)
    gs_x = models.IntegerField(default=0)
    gs_y = models.IntegerField(default=0)
    gs_w = models.IntegerField(default=6)   # default half-width on 12-col grid
    gs_h = models.IntegerField(default=4)   # default height in row units

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
        d['gs_x'] = self.gs_x
        d['gs_y'] = self.gs_y
        d['gs_w'] = self.gs_w
        d['gs_h'] = self.gs_h
        if self.chart:
            d['chart'] = self.chart.to_dict()
        else:
            d['chart'] = None
        return d


# ─── Page system ──────────────────────────────────────────────────────────────

class DataVizPage(models.Model):
    """A publishable page that embeds dashboards + text, with filter controls."""

    STATUS_DRAFT  = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_CHOICES = [
        (STATUS_DRAFT,  'Draft'),
        (STATUS_ACTIVE, 'Active'),
    ]

    CONTAINER_FULL  = 'full'
    CONTAINER_WIDE  = 'wide'
    CONTAINER_XL    = 'xl'
    CONTAINER_LG    = 'lg'
    CONTAINER_MD    = 'md'
    CONTAINER_CHOICES = [
        (CONTAINER_FULL, 'Full width'),
        (CONTAINER_WIDE, 'Wide (1536px)'),
        (CONTAINER_XL,   'Standard (1280px)'),
        (CONTAINER_LG,   'Narrow (1024px)'),
        (CONTAINER_MD,   'Compact (768px)'),
    ]

    title            = models.CharField(max_length=200)
    slug             = models.SlugField(max_length=200, unique=True, blank=True)
    description      = models.TextField(blank=True)
    container_width  = models.CharField(max_length=10, default=CONTAINER_XL, choices=CONTAINER_CHOICES)
    show_header      = models.BooleanField(default=True)
    owner            = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dataviz_pages',
    )
    status         = models.CharField(max_length=20, default=STATUS_DRAFT, choices=STATUS_CHOICES)
    # Filters applied to every chart on this page
    # [{id, label, source_col, widget: 'select'|'multi'|'text', default_val}]
    page_filters   = models.JSONField(default=list, blank=True)
    # FK to users.Page auto-created when published
    linked_site_page = models.OneToOneField(
        'users.Page',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dataviz_page',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

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
            'id':               self.pk,
            'title':            self.title,
            'slug':             self.slug,
            'description':      self.description,
            'status':           self.status,
            'container_width':  self.container_width,
            'show_header':      self.show_header,
            'page_filters':     self.page_filters or [],
            'created_at':       self.created_at.isoformat(),
            'updated_at':       self.updated_at.isoformat(),
        }
        if include_sections:
            d['sections'] = [s.to_dict() for s in self.sections.select_related('dashboard').all()]
        return d


class DataVizPageRow(models.Model):
    """A layout row within a DataVizPage.

    Rows stack vertically and each row contains its own 12-column GridStack
    sub-grid.  On the published page each row renders as a responsive CSS
    grid that collapses to a single column on small screens.
    """

    page     = models.ForeignKey(DataVizPage, on_delete=models.CASCADE, related_name='rows')
    label    = models.CharField(max_length=200, blank=True, default='')
    position = models.IntegerField(default=0)
    bg_color = models.CharField(max_length=30, blank=True, default='')  # e.g. '#f8fafc', 'bg-slate-50'
    collapsed = models.BooleanField(default=False)  # allow collapsible rows

    class Meta:
        ordering = ['position']

    def __str__(self):
        return f"{self.page.title} — row #{self.position}"

    def to_dict(self):
        return {
            'id':        self.pk,
            'label':     self.label,
            'position':  self.position,
            'bg_color':  self.bg_color,
            'collapsed': self.collapsed,
            'sections':  [s.to_dict() for s in self.sections.select_related('chart', 'dashboard').all()],
        }


class DataVizPageSection(models.Model):
    """An ordered section within a DataVizPage.

    Supports four element types:
      - **chart**:     a single SavedChart laid out on a GridStack grid
      - **html**:      an HTML/text block on the GridStack grid
      - **divider**:   a visual separator (not on the grid)
      - **dashboard**: (legacy) embeds an entire Dashboard
    """

    TYPE_CHART     = 'chart'
    TYPE_HTML      = 'html'
    TYPE_DIVIDER   = 'divider'
    TYPE_DASHBOARD = 'dashboard'
    TYPE_CHOICES = [
        (TYPE_CHART,     'Chart'),
        (TYPE_HTML,      'Text / HTML'),
        (TYPE_DIVIDER,   'Divider'),
        (TYPE_DASHBOARD, 'Dashboard (legacy)'),
    ]

    page         = models.ForeignKey(DataVizPage, on_delete=models.CASCADE, related_name='sections')
    row          = models.ForeignKey(
        DataVizPageRow,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sections',
    )
    element_type = models.CharField(max_length=20, default=TYPE_CHART, choices=TYPE_CHOICES)

    # Chart slot (element_type='chart')
    chart        = models.ForeignKey(
        SavedChart,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='page_sections',
    )
    show_title   = models.BooleanField(default=True)
    slot_filters = models.JSONField(default=list, blank=True)

    # GridStack layout (element_type='chart' or 'html')
    gs_x = models.IntegerField(default=0)
    gs_y = models.IntegerField(default=0)
    gs_w = models.IntegerField(default=6)
    gs_h = models.IntegerField(default=4)

    # Legacy dashboard embed (element_type='dashboard')
    dashboard    = models.ForeignKey(
        Dashboard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='page_sections',
    )

    html_content = models.TextField(blank=True)
    position     = models.IntegerField(default=0)

    class Meta:
        ordering = ['position']

    def __str__(self):
        return f"{self.page.title} — {self.element_type} #{self.position}"

    def to_dict(self):
        d = {
            'id':           self.pk,
            'row_id':       self.row_id,
            'element_type': self.element_type,
            'position':     self.position,
            'html_content': self.html_content,
            'show_title':   self.show_title,
            'slot_filters': self.slot_filters or [],
            'gs_x':         self.gs_x,
            'gs_y':         self.gs_y,
            'gs_w':         self.gs_w,
            'gs_h':         self.gs_h,
        }
        if self.chart:
            d['chart'] = self.chart.to_dict()
        else:
            d['chart'] = None
        if self.dashboard:
            d['dashboard'] = self.dashboard.to_dict(include_charts=True)
        else:
            d['dashboard'] = None
        return d
