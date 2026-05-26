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

    @classmethod
    def get(cls):
        """Return the singleton, creating it (empty) if it doesn't exist yet."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
