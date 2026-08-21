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


class NavbarConfig(models.Model):
    """Singleton navbar settings managed from Page Management."""

    NAVBAR_STYLE_CHOICES = [
        ('classic', 'Classic Dark'),
        ('meter', 'Meter.com Inspired'),
    ]

    LABEL_CASE_CHOICES = [
        ('uppercase', 'Uppercase'),
        ('normal', 'Normal Case'),
    ]

    SUBMENU_STYLE_CHOICES = [
        ('dropdown', 'Classic Dropdown'),
        ('megamenu', 'Mega Menu'),
    ]

    sticky = models.BooleanField(_("Sticky Navbar"), default=True, help_text="Keep the navbar fixed to the top of the viewport while scrolling")
    navbar_style = models.CharField(
        _("Navbar Style"),
        max_length=20,
        choices=NAVBAR_STYLE_CHOICES,
        default='classic',
        help_text="Classic Dark matches the current site look; Meter.com Inspired uses a clean light bar whose submenus toggle open/closed when you click their parent item",
    )
    label_case = models.CharField(
        _("Label Case"),
        max_length=20,
        choices=LABEL_CASE_CHOICES,
        default='uppercase',
        help_text="Uppercase matches the classic site look; Normal Case is the cleaner, inspiration-based style",
    )
    submenu_style = models.CharField(
        _("Submenu Style"),
        max_length=20,
        choices=SUBMENU_STYLE_CHOICES,
        default='dropdown',
        help_text="Classic Dropdown shows a small menu below the item; Mega Menu shows a full-width panel with descriptions",
    )

    updated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='navbar_config_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Navbar Configuration")
        verbose_name_plural = _("Navbar Configuration")

    def __str__(self):
        return f'Navbar Config — {self.get_navbar_style_display()} / {"sticky" if self.sticky else "static"} / {self.get_label_case_display()} / {self.get_submenu_style_display()}'

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete('landing:navbar_config')

    @classmethod
    def get(cls):
        """Return the singleton, creating it (with defaults) if it doesn't exist yet."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
