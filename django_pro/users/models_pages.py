from django.db import models
from django.utils.translation import gettext_lazy as _


class Page(models.Model):
    """Model to manage pages/views that users can access."""
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('draft', 'Draft'),
    ]
    
    name = models.CharField(_("Page Name"), max_length=255)
    slug = models.SlugField(_("URL Slug"), max_length=255, unique=True)
    app_reference = models.CharField(
        _("App Reference"),
        max_length=100,
        blank=True,
        default='',
        help_text="Primary Django app this page belongs to (e.g., placementcards, dataexploration)",
    )
    description = models.TextField(_("Description"), blank=True)
    url_path = models.CharField(_("URL Path"), max_length=500, help_text="Internal URL path for this page")
    icon = models.CharField(_("Icon Class"), max_length=100, blank=True, help_text="CSS class for icon (e.g., 'fa-dashboard')")
    
    status = models.CharField(_("Status"), max_length=20, choices=STATUS_CHOICES, default='active')
    
    # Access control
    required_roles = models.JSONField(
        _("Required Roles"), 
        default=list, 
        blank=True,
        help_text="List of role names that can access this page. Empty = all authenticated users"
    )
    
    allowed_users = models.ManyToManyField(
        'User',
        blank=True,
        related_name='allowed_pages',
        help_text="Specific users who can access this page"
    )
    
    is_public = models.BooleanField(_("Public Access"), default=False, help_text="Allow unauthenticated users")
    
    # Display options
    show_in_menu = models.BooleanField(_("Show in Menu"), default=True)
    menu_order = models.IntegerField(_("Menu Order"), default=0, help_text="Lower numbers appear first")
    menu_roles = models.JSONField(
        _("Menu Roles"),
        default=list,
        blank=True,
        help_text="Optional roles that can see this page in navigation (menu visibility only)",
    )
    menu_allowed_users = models.ManyToManyField(
        'User',
        blank=True,
        related_name='menu_visible_pages',
        help_text="Specific users that can see this page in navigation (menu visibility only)",
    )
    parent_page = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='children',
        help_text="Parent page for hierarchical menu structure"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'User', 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='pages_created'
    )
    
    class Meta:
        ordering = ['menu_order', 'name']
        verbose_name = _("Page")
        verbose_name_plural = _("Pages")
    
    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        return self.url_path
