from django.contrib import admin

from .models import (
    CatalogCategory,
    CatalogDiscipline,
    CatalogLevel,
    CatalogPath,
    CatalogPathStep,
    CatalogPlacement,
    CatalogTag,
    CourseCatalogEntry,
    CtePathwayEntry,
)


class CatalogCategoryInline(admin.TabularInline):
    model = CatalogCategory
    extra = 0
    fields = ('name', 'slug', 'order', 'is_active')


class CatalogDisciplineInline(admin.TabularInline):
    model = CatalogDiscipline
    extra = 0
    fields = ('name', 'order', 'is_active')


@admin.register(CatalogTag)
class CatalogTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [CatalogCategoryInline]


@admin.register(CatalogCategory)
class CatalogCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'tag', 'slug', 'order', 'is_active')
    list_filter = ('tag', 'is_active')
    search_fields = ('name', 'slug', 'tag__name')
    inlines = [CatalogDisciplineInline]


@admin.register(CatalogDiscipline)
class CatalogDisciplineAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'order', 'is_active')
    list_filter = ('category__tag', 'is_active')
    search_fields = ('name', 'category__name', 'category__tag__name')


@admin.register(CatalogLevel)
class CatalogLevelAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')


@admin.register(CatalogPlacement)
class CatalogPlacementAdmin(admin.ModelAdmin):
    list_display = ('catalog_entry', 'tag', 'category', 'discipline', 'level', 'notes')
    list_filter = ('tag', 'category', 'discipline', 'level')
    search_fields = ('catalog_entry__course_title', 'catalog_entry__course_key')
    autocomplete_fields = ('catalog_entry', 'tag', 'category', 'discipline', 'level')


class CatalogPathStepInline(admin.TabularInline):
    model = CatalogPathStep
    extra = 0
    fields = ('sequence', 'catalog_entry', 'label')
    autocomplete_fields = ('catalog_entry',)
    ordering = ('sequence',)


@admin.register(CatalogPath)
class CatalogPathAdmin(admin.ModelAdmin):
    list_display = ('name', 'tag', 'category', 'discipline', 'is_active')
    list_filter = ('tag', 'is_active')
    search_fields = ('name',)
    inlines = [CatalogPathStepInline]


@admin.register(CourseCatalogEntry)
class CourseCatalogEntryAdmin(admin.ModelAdmin):
    search_fields = (
        'course_key',
        'course_id',
        'course_title',
        'state_course_code',
        'subject_area_1_desc',
    )
    list_display = ('course_key', 'course_id', 'course_title', 'state_course_code', 'subject_area_1_desc')
    readonly_fields = [f.name for f in CourseCatalogEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CtePathwayEntry)
class CtePathwayEntryAdmin(admin.ModelAdmin):
    list_display = (
        'course_id',
        'course_title',
        'career_cluster',
        'cte_pathway',
        'cte_course_level',
        'state_course_code',
    )
    list_filter = ('career_cluster', 'cte_pathway', 'cte_course_level')
    search_fields = ('course_id', 'course_title', 'career_cluster', 'cte_pathway')
    readonly_fields = [f.name for f in CtePathwayEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
