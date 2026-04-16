from django.contrib import admin

from .models import SavedChart, Dashboard, DashboardChart, DataVizPage, DataVizPageSection


@admin.register(SavedChart)
class SavedChartAdmin(admin.ModelAdmin):
    list_display  = ('name', 'chart_type', 'owner', 'updated_at')
    list_filter   = ('chart_type',)
    search_fields = ('title', 'owner__username')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display  = ('name', 'owner', 'updated_at')
    search_fields = ('name', 'owner__username')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DashboardChart)
class DashboardChartAdmin(admin.ModelAdmin):
    list_display  = ('dashboard', 'chart', 'element_type', 'position', 'gs_x', 'gs_y', 'gs_w', 'gs_h')
    list_filter   = ('element_type',)


@admin.register(DataVizPage)
class DataVizPageAdmin(admin.ModelAdmin):
    list_display  = ('title', 'slug', 'status', 'owner', 'updated_at')
    list_filter   = ('status',)
    search_fields = ('title', 'slug', 'owner__username')
    readonly_fields = ('created_at', 'updated_at')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(DataVizPageSection)
class DataVizPageSectionAdmin(admin.ModelAdmin):
    list_display = ('page', 'element_type', 'position')
    list_filter  = ('element_type',)
