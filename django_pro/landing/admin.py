from django import forms
from django.contrib import admin
from .models import ProjectConfig

class ProjectConfigAdminForm(forms.ModelForm):
    TABLE_CHOICES = []
    
    enabled_tables = forms.MultipleChoiceField(
        choices=TABLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Allowed Dataset Tables",
        help_text="Select the tables that are allowed to be used in the Dataset Builder. Leave all unchecked to allow all tables."
    )

    class Meta:
        model = ProjectConfig
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from dataviz.utils.compiler import get_db_tables
        try:
            db_tables = get_db_tables()
            choices = []
            for t in sorted(db_tables.keys()):
                label = t.replace('_', ' ').title()
                if label == 'Users User':
                    label = 'Users/User'
                choices.append((t, label))
            self.fields['enabled_tables'].choices = choices
        except Exception:
            pass

        if self.instance and self.instance.enabled_dataset_tables:
            self.fields['enabled_tables'].initial = self.instance.enabled_dataset_tables
        else:
            self.fields['enabled_tables'].initial = []

    def save(self, commit=True):
        instance = super().save(commit=False)
        selected = self.cleaned_data.get('enabled_tables')
        # If no tables are checked, save as None (allows all)
        instance.enabled_dataset_tables = selected if selected else None
        if commit:
            instance.save()
        return instance


@admin.register(ProjectConfig)
class ProjectConfigAdmin(admin.ModelAdmin):
    form = ProjectConfigAdminForm
    list_display = ('pk', 'default_school_year', 'updated_by', 'updated_at')
    readonly_fields = ('updated_at',)

    fieldsets = (
        ('School Year', {
            'fields': ('default_school_year', 'disabled_school_years'),
        }),
        ('Data Explorer', {
            'fields': (
                'default_columns', 'enabled_columns',
                'enable_course_program_columns',
            ),
        }),
        ('Home Dashboard', {
            'fields': (
                'enable_home_charts', 'home_page_width', 'home_show_chart_location',
                'home_forced_charts', 'home_max_charts',
            ),
        }),
        ('Student Summary — Content tabs', {
            'fields': (
                'summary_show_tests', 'summary_show_grades',
                'summary_show_diploma', 'summary_show_ag',
                'summary_show_on_track', 'summary_show_cte',
            ),
        }),
        ('Student Summary — Header indicators', {
            'fields': ('summary_show_recognition', 'summary_show_flags'),
        }),
        ('Dataset Builder', {
            'fields': ('enabled_tables',),
        }),
        ('Audit', {
            'classes': ('collapse',),
            'fields': ('updated_by', 'updated_at'),
        }),
    )

    def has_add_permission(self, request):
        # Only one row ever; if it already exists, block new creation via Django admin
        return not ProjectConfig.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False

