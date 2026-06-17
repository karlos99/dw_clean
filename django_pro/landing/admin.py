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

    def has_add_permission(self, request):
        # Only one row ever; if it already exists, block new creation via Django admin
        return not ProjectConfig.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False

