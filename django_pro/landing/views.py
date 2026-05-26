from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import connection
from django.core.cache import cache
from dataexploration.models import SavedQuery


# Create your views here.
def index(request):
    if request.user.is_authenticated:
        # Dashboard data
        context = {
            'saved_queries': SavedQuery.objects.filter(owner=request.user).order_by('-updated_at')[:5],
            'shared_queries': SavedQuery.objects.filter(shares__shared_with=request.user).order_by('-updated_at')[:5],
            'school_count': len(request.user.schools) if isinstance(request.user.schools, list) else 0,
        }
        return render(request, 'landing_dashboard.html', context)
            
    return render(request, 'landing.html')


def project_settings(request):
    """Project-admin-only: configure default school year and column settings."""
    from dataviz.views.mixins import is_project_admin

    if not request.user.is_authenticated or not is_project_admin(request.user):
        return redirect('landing:index')

    import json as _json
    from landing.models import ProjectConfig
    from dataexploration.constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS

    cfg = ProjectConfig.get()

    # Fetch available school years from DB for the dropdown
    school_years = cache.get('dataexp:school_years')
    if not school_years:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT school_year FROM current_student "
                    "WHERE school_year IS NOT NULL ORDER BY school_year DESC"
                )
                school_years = [row[0] for row in cursor.fetchall()]
                cache.set('dataexp:school_years', school_years, 3600)
        except Exception:
            school_years = []

    if request.method == 'POST':
        default_sy = request.POST.get('default_school_year', '').strip()

        raw_default_cols = request.POST.get('default_columns_json', '')
        raw_enabled_cols = request.POST.get('enabled_columns_json', '')

        try:
            default_columns = _json.loads(raw_default_cols) if raw_default_cols.strip() else None
        except (ValueError, TypeError):
            default_columns = None

        try:
            enabled_columns = _json.loads(raw_enabled_cols) if raw_enabled_cols.strip() else None
        except (ValueError, TypeError):
            enabled_columns = None

        cfg.default_school_year = default_sy
        cfg.default_columns = default_columns if default_columns else None
        cfg.enabled_columns = enabled_columns if enabled_columns else None
        cfg.updated_by = request.user
        cfg.save()

        messages.success(request, 'Project settings saved.')
        return redirect('landing:project_settings')

    active_default = cfg.default_columns or DEFAULT_COLUMNS
    active_enabled = cfg.enabled_columns  # None = all enabled
    all_col_keys   = list(COLUMN_DEFINITIONS.keys())

    context = {
        'cfg': cfg,
        'school_years': school_years,
        'column_definitions': COLUMN_DEFINITIONS,
        'column_categories': COLUMN_CATEGORIES,
        # JSON-safe strings for use in <script> blocks
        'active_default_columns_json': _json.dumps(active_default),
        'active_enabled_columns_json': _json.dumps(active_enabled),
        'all_column_keys_json':        _json.dumps(all_col_keys),
    }
    return render(request, 'landing/project_settings.html', context)


