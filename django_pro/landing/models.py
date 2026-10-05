from django.db import models
from django.conf import settings


class ProjectConfig(models.Model):
    """
    Singleton project-wide configuration managed by Project Admins.

    Only one row ever exists (pk=1).  Access via landing.utils.get_project_config()
    which caches the result in Redis.
    """

    # ── School Year ───────────────────────────────────────────────────────────
    default_school_year = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text=(
            "The reference school year for the project (e.g. '2025-2026').  "
            "Used to resolve school-year aliases (This Year / Last Year / Two Years Prior)."
        ),
    )

    # ── Data Explorer columns ─────────────────────────────────────────────────
    default_columns = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Ordered list of column keys shown by default in the Data Explorer.  "
            "Null means fall back to the DefaultColumnSelection table, then constants."
        ),
    )

    enabled_columns = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Allowlist of column keys that are available in the Data Explorer.  "
            "Null means ALL columns are available (no restriction).  "
            "Supply a list to hide specific columns from all users globally."
        ),
    )

    enabled_dataset_tables = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Allowlist of database tables that can be used in the Dataset Builder.  "
            "Null means ALL whitelisted tables are allowed."
        ),
    )

    # ── Home dashboard ──────────────────────────────────────────────────────
    enable_home_charts = models.BooleanField(
        default=True,
        help_text="Allow users to build a personalized charts dashboard on the home page (drag charts from the User Charts panel).",
    )

    home_forced_charts = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of SavedChart IDs that are forced onto every user's home "
            "dashboard. Users cannot delete these charts, but they can rearrange "
            "and resize them alongside charts they add themselves."
        ),
    )

    home_max_charts = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Maximum number of charts a user may add to their own home dashboard. "
            "Forced charts do not count toward this limit. 0 = unlimited."
        ),
    )

    home_show_chart_location = models.BooleanField(
        default=True,
        help_text=(
            "Show the chart name bar (location) on every user's home dashboard. "
            "When off, home charts show only the visual and its in-chart title."
        ),
    )

    # ── Available school years ──────────────────────────────────────────────
    disabled_school_years = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "School years to hide from the Data Explorer. Students enrolled in these "
            "years will not be available. Empty means all school years are available."
        ),
    )

    enable_course_program_columns = models.BooleanField(
        default=False,
        help_text=(
            "Show Course Programs columns in the Data Explorer (catalog placements "
            "and CTE pathways derived from student schedules)."
        ),
    )

    # ── Audit ──────────────────────────────────────────────────────────────────
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_config_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Project Configuration'
        verbose_name_plural = 'Project Configuration'

    def __str__(self):
        yr = self.default_school_year or '(not set)'
        return f'Project Config — default year: {yr}'

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)
        # Bust cache so get_project_config() immediately sees new values
        from django.core.cache import cache
        cache.delete('landing:project_config')
        cache.delete('dataviz:schema_json')

        # Update RelativeYear mappings relative to the new default school year
        try:
            from dataviz.models import RelativeYear
            default_year = self.default_school_year
            if default_year and '-' in default_year:
                current_start = int(default_year.split('-')[0])
                mappings = [
                    {
                        'offset': 0,
                        'alias': '__sy_cur__',
                        'label': 'This School Year',
                        'is_current': True
                    },
                    {
                        'offset': -1,
                        'alias': '__sy_prev1__',
                        'label': 'Last School Year',
                        'is_current': False
                    },
                    {
                        'offset': -2,
                        'alias': '__sy_prev2__',
                        'label': 'Two Years Prior',
                        'is_current': False
                    },
                    {
                        'offset': -3,
                        'alias': '__sy_prev3__',
                        'label': 'Three Years Prior',
                        'is_current': False
                    },
                    {
                        'offset': -4,
                        'alias': '__sy_prev4__',
                        'label': 'Four Years Prior',
                        'is_current': False
                    },
                    {
                        'offset': -5,
                        'alias': '__sy_prev5__',
                        'label': 'Five Years Prior',
                        'is_current': False
                    }
                ]
                # Re-populate relative_year table
                RelativeYear.objects.all().delete()
                for m in mappings:
                    year_start = current_start + m['offset']
                    year_str = f"{year_start}-{year_start + 1}"
                    RelativeYear.objects.create(
                        school_year=year_str,
                        relative_alias=m['alias'],
                        relative_label=m['label'],
                        offset_years=m['offset'],
                        is_current=m['is_current']
                    )
                # Keep the ClickHouse relative_year mirror in sync — dataset
                # charts resolve relative-year filters against it, so a stale
                # mirror would leave charts on the old current-year mapping.
                # Push rows directly from Django (not the postgresql() table
                # function) so both databases update in the same save().
                try:
                    from clickhouse_utils.client import get_clickhouse_client
                    from dataviz.models import RelativeYear as _RelYear
                    _ch = get_clickhouse_client()
                    if _ch is not None:
                        from django.conf import settings as _settings
                        _ch_db = getattr(_settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db')
                        _ch.command(f"TRUNCATE TABLE {_ch_db}.relative_year")
                        _rows = [
                            (r.school_year, r.relative_alias, r.relative_label,
                             r.offset_years, r.is_current)
                            for r in _RelYear.objects.order_by('offset_years')
                        ]
                        if _rows:
                            _ch.insert(
                                f"{_ch_db}.relative_year",
                                _rows,
                                column_names=['school_year', 'relative_alias',
                                              'relative_label', 'offset_years', 'is_current'],
                            )
                except Exception:
                    import logging
                    logging.getLogger('landing').exception(
                        'Failed to sync relative_year to ClickHouse'
                    )
        except Exception:
            # Prevent failure during initial migrations/schema changes before table exists
            pass

    @classmethod
    def get(cls):
        """Return the singleton, creating it (empty) if it doesn't exist yet."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
