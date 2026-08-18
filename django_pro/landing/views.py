from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import connection
from django.core.cache import cache
from dataexploration.models import SavedQuery
import json


# Create your views here.
def index(request):
    if request.user.is_authenticated:
        from landing.utils import get_project_config
        cfg = get_project_config()
        home_charts_enabled = bool(getattr(cfg, 'enable_home_charts', True))

        home_dashboard_json = 'null'
        home_slots_json = '[]'
        slots = []
        active_school = getattr(request.user, 'active_school', None)
        active_school_id = active_school.state_id if active_school else ''
        if home_charts_enabled:
            from dataviz.models import Dashboard
            home_dashboard = Dashboard.get_home(request.user)
            slots = list(home_dashboard.chart_slots.select_related('chart').order_by('position'))

            # Pre-render each chart via the shared (cached) pipeline so the home
            # page renders charts exactly like the dataviz pages do. The user's
            # saved school (if any) narrows the visuals to that school.
            from dataviz.views.api.chart_api import _render_saved_chart
            for slot in slots:
                slot.rendered_html = ''
                slot.render_error = None
                if slot.element_type != 'chart' or not slot.chart_id:
                    continue
                try:
                    res = _render_saved_chart(
                        slot.chart_id, {},
                        drill_source='dashboard',
                        drill_source_id=slot.dashboard_id,
                        drill_source_name='My Charts',
                        is_embed=True,
                        request=request,
                        school_id=active_school_id,
                    )
                    slot.rendered_html = res.get('html', '')
                    slot.render_error = res.get('error')
                except Exception as exc:
                    slot.render_error = str(exc)
            home_dashboard_json = json.dumps(home_dashboard.to_dict())
            home_slots_json = json.dumps([s.to_dict() for s in slots])

        # Schools the user can filter home charts by
        user_school_ids = request.user.schools if isinstance(request.user.schools, list) else []
        user_schools = []
        if user_school_ids:
            from studata.models import School as StudataSchool
            user_schools = list(
                StudataSchool.objects
                .filter(state_id__in=user_school_ids)
                .order_by('school_name')
                .values('state_id', 'school_name', 'short_name')
            )
        else:
            # Unrestricted roles (superuser / Project / District Admin) get the full
            # school list so they can filter home charts by school.
            unrestricted = (
                request.user.is_superuser
                or request.user.roles.filter(name__in=['Project Admin', 'District Admin']).exists()
            )
            if unrestricted:
                from studata.models import School as StudataSchool
                user_schools = list(
                    StudataSchool.objects
                    .order_by('school_name')
                    .values('state_id', 'school_name', 'short_name')
                )

        context = {
            'saved_queries': SavedQuery.objects.filter(owner=request.user).order_by('-updated_at')[:5],
            'shared_queries': SavedQuery.objects.filter(shares__shared_with=request.user).order_by('-updated_at')[:5],
            'school_count': len(user_school_ids),
            'home_charts_enabled': home_charts_enabled,
            'home_dashboard_json': home_dashboard_json,
            'home_slots_json': home_slots_json,
            'home_slots': slots,
            'user_schools': user_schools,
            'active_school_id': active_school_id,
            'active_school_name': active_school.school_name if active_school else '',
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
        enabled_tables = request.POST.getlist('enabled_dataset_tables')

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
        cfg.enabled_dataset_tables = enabled_tables if enabled_tables else None
        cfg.enable_home_charts = request.POST.get('enable_home_charts') == 'on'
        cfg.disabled_school_years = request.POST.getlist('disabled_school_years')
        cfg.updated_by = request.user
        cfg.save()

        messages.success(request, 'Project settings saved.')
        return redirect('landing:project_settings')

    active_default = cfg.default_columns or DEFAULT_COLUMNS
    active_enabled = cfg.enabled_columns  # None = all enabled
    all_col_keys   = list(COLUMN_DEFINITIONS.keys())

    from dataviz.utils.compiler import get_db_tables
    db_tables = get_db_tables()
    TABLE_CHOICES = []
    for t in sorted(db_tables.keys()):
        label = t.replace('_', ' ').title()
        if label == 'Users User':
            label = 'Users/User'
        TABLE_CHOICES.append((t, label))

    context = {
        'cfg': cfg,
        'school_years': school_years,
        'column_definitions': COLUMN_DEFINITIONS,
        'column_categories': COLUMN_CATEGORIES,
        # JSON-safe strings for use in <script> blocks
        'active_default_columns_json': _json.dumps(active_default),
        'active_enabled_columns_json': _json.dumps(active_enabled),
        'all_column_keys_json':        _json.dumps(all_col_keys),
        'column_labels_json':          _json.dumps(COLUMN_DEFINITIONS),
        'dataset_tables':              TABLE_CHOICES,
        'enabled_dataset_tables':      cfg.enabled_dataset_tables or [],
        'enable_home_charts':          cfg.enable_home_charts,
        'disabled_school_years':       [str(sy) for sy in (cfg.disabled_school_years or [])],
    }
    return render(request, 'landing/project_settings.html', context)


