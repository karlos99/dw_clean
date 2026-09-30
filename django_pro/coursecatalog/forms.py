from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .form_widgets import (
    CC_CODE_NARROW,
    CC_INPUT,
    CC_NUMBER_NARROW,
    CC_SELECT,
    CC_TEXTAREA,
)
from .models import (
    CatalogCategory,
    CatalogDiscipline,
    CatalogLevel,
    CatalogPath,
    CatalogPathStep,
    CatalogTag,
    CourseCatalogEntry,
)


def _apply_cc_field_classes(form):
    for field in form.fields.values():
        w = field.widget
        if isinstance(w, forms.Textarea):
            w.attrs['class'] = CC_TEXTAREA
        elif isinstance(w, (forms.Select, forms.SelectMultiple)):
            w.attrs['class'] = CC_SELECT
        elif isinstance(w, forms.CheckboxInput):
            w.attrs['class'] = 'rounded border border-slate-300 text-blue-600 focus:ring-blue-500'
        elif isinstance(w, forms.NumberInput):
            w.attrs.setdefault('class', CC_NUMBER_NARROW)
        elif isinstance(w, forms.TextInput):
            w.attrs.setdefault('class', CC_INPUT)


class CatalogTagForm(forms.ModelForm):
    """Staff taxonomy UI only submits name + slug; other columns use model defaults."""

    class Meta:
        model = CatalogTag
        fields = ('name', 'slug')
        widgets = {
            'name': forms.TextInput(attrs={'class': CC_INPUT}),
            'slug': forms.TextInput(attrs={'class': CC_INPUT, 'placeholder': 'auto from name if empty'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False

    def clean_slug(self):
        slug = self.cleaned_data.get('slug') or slugify(self.cleaned_data.get('name', ''))
        if not slug:
            raise ValidationError('Enter a name or slug.')
        return slug

    def save(self, commit=True):
        tag = super().save(commit=False)
        if not tag.pk:
            tag.order = 0
            tag.is_active = True
        if commit:
            tag.save()
        return tag


class CatalogCategoryForm(forms.ModelForm):
    class Meta:
        model = CatalogCategory
        fields = ('name', 'slug')
        widgets = {
            'name': forms.TextInput(attrs={'class': CC_INPUT}),
            'slug': forms.TextInput(attrs={'class': CC_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False

    def clean_slug(self):
        slug = self.cleaned_data.get('slug') or slugify(self.cleaned_data.get('name', ''))
        if not slug:
            raise ValidationError('Enter a name or slug.')
        return slug

    def save(self, commit=True):
        category = super().save(commit=False)
        if not category.pk:
            category.order = 0
            category.is_active = True
        if commit:
            category.save()
        return category


class CatalogCategoryEditForm(CatalogCategoryForm):
    """Category edit includes sort order."""

    class Meta(CatalogCategoryForm.Meta):
        fields = ('name', 'slug', 'order')
        widgets = {
            **CatalogCategoryForm.Meta.widgets,
            'order': forms.NumberInput(attrs={'class': CC_NUMBER_NARROW}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['order'].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('order') in (None, ''):
            cleaned['order'] = self.instance.order if self.instance.pk else 0
        return cleaned


class CatalogDisciplineForm(forms.ModelForm):
    class Meta:
        model = CatalogDiscipline
        fields = ('name', 'order', 'is_active')
        widgets = {
            'name': forms.TextInput(
                attrs={'class': CC_INPUT, 'placeholder': 'e.g. Dance'},
            ),
            'order': forms.NumberInput(attrs={'class': CC_NUMBER_NARROW}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['order'].required = False
        self.fields['is_active'].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('order') in (None, ''):
            cleaned['order'] = self.instance.order if self.instance.pk else 0
        if 'is_active' not in self.data and self.instance.pk:
            cleaned['is_active'] = self.instance.is_active
        elif 'is_active' not in self.data:
            cleaned['is_active'] = True
        return cleaned


class CatalogLevelForm(forms.ModelForm):
    class Meta:
        model = CatalogLevel
        fields = ('name', 'code', 'order')
        widgets = {
            'name': forms.TextInput(
                attrs={'class': CC_INPUT, 'placeholder': 'e.g. Intro, Advanced, AP'},
            ),
            'code': forms.TextInput(attrs={'class': CC_CODE_NARROW}),
            'order': forms.NumberInput(attrs={'class': CC_NUMBER_NARROW}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['order'].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('order') in (None, ''):
            cleaned['order'] = self.instance.order if self.instance.pk else 0
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.pk:
            instance.is_active = True
        if commit:
            instance.save()
        return instance


def _clean_placement_taxonomy(cleaned: dict) -> dict:
    tag = cleaned.get('tag')
    category = cleaned.get('category')
    discipline = cleaned.get('discipline')
    if discipline and not category:
        raise ValidationError('Choose a category before selecting a discipline.')
    if category and tag and category.tag_id != tag.id:
        raise ValidationError('Category must belong to the chosen tag.')
    if discipline and category and discipline.category_id != category.id:
        raise ValidationError('Discipline must belong to the chosen category.')
    return cleaned


class BulkCatalogPlacementForm(forms.Form):
    tag = forms.ModelChoiceField(
        queryset=CatalogTag.objects.filter(is_active=True).order_by('order', 'name'),
    )
    category = forms.ModelChoiceField(
        queryset=CatalogCategory.objects.filter(is_active=True).order_by('tag__order', 'order', 'name'),
        required=False,
    )
    discipline = forms.ModelChoiceField(
        queryset=CatalogDiscipline.objects.filter(is_active=True).order_by(
            'category__tag__order', 'category__order', 'order', 'name',
        ),
        required=False,
    )
    level = forms.ModelChoiceField(
        queryset=CatalogLevel.objects.filter(is_active=True).order_by('order', 'name'),
        required=False,
    )
    course_keys = forms.MultipleChoiceField(required=True)

    def __init__(self, *args, course_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course_keys'].choices = course_choices or []
        for name in ('category', 'discipline', 'level'):
            self.fields[name].empty_label = '— Optional —'
        _apply_cc_field_classes(self)

    def clean(self):
        cleaned = super().clean()
        return _clean_placement_taxonomy(cleaned)


class BulkCatalogPlacementUpdateForm(forms.Form):
    placement_ids = forms.MultipleChoiceField(required=True)
    tag = forms.ModelChoiceField(
        queryset=CatalogTag.objects.filter(is_active=True).order_by('order', 'name'),
    )
    category = forms.ModelChoiceField(
        queryset=CatalogCategory.objects.filter(is_active=True).order_by('tag__order', 'order', 'name'),
        required=False,
    )
    discipline = forms.ModelChoiceField(
        queryset=CatalogDiscipline.objects.filter(is_active=True).order_by(
            'category__tag__order', 'category__order', 'order', 'name',
        ),
        required=False,
    )
    level = forms.ModelChoiceField(
        queryset=CatalogLevel.objects.filter(is_active=True).order_by('order', 'name'),
        required=False,
    )

    def __init__(self, *args, placement_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['placement_ids'].choices = placement_choices or []
        for name in ('category', 'discipline', 'level'):
            self.fields[name].empty_label = '— Optional —'
        _apply_cc_field_classes(self)

    def clean(self):
        cleaned = super().clean()
        return _clean_placement_taxonomy(cleaned)


class CatalogPathForm(forms.ModelForm):
    class Meta:
        model = CatalogPath
        fields = ('name', 'tag', 'category', 'discipline', 'description', 'is_active')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tag'].queryset = CatalogTag.objects.filter(is_active=True).order_by('order', 'name')
        self.fields['category'].queryset = CatalogCategory.objects.filter(is_active=True).order_by('tag__order', 'order', 'name')
        self.fields['discipline'].queryset = CatalogDiscipline.objects.filter(is_active=True).order_by(
            'category__tag__order', 'category__order', 'order', 'name',
        )
        _apply_cc_field_classes(self)


class CatalogPathStepForm(forms.ModelForm):
    class Meta:
        model = CatalogPathStep
        fields = ('sequence', 'catalog_entry', 'label')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['catalog_entry'].queryset = CourseCatalogEntry.objects.all().order_by('course_title', 'course_key')
        _apply_cc_field_classes(self)
