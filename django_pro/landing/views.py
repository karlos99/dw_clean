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
        home_max_charts = 0
        home_forced_chart_ids = []
        active_school = getattr(request.user, 'active_school', None)
        active_school_id = active_school.state_id if active_school else ''
        if home_charts_enabled:
            # Sync system-forced charts so they appear (and render) server-side,
            # just like charts the user added themselves.
            from dataviz.views.api.dashboard_api import sync_home_dashboard
            home_dashboard, home_cfg = sync_home_dashboard(request.user)
            home_max_charts = int(getattr(home_cfg, 'home_max_charts', 0) or 0)
            home_forced_chart_ids = [int(c) for c in (home_cfg.home_forced_charts or [])]
            slots = list(home_dashboard.chart_slots.select_related('chart').order_by('position'))

            # Pre-render each chart via the shared (cached) pipeline so the home
            # page renders charts exactly like the dataviz pages do. Parallelized across
            # worker threads so homepage latency is max(single_chart) rather than sum.
            from clickhouse_utils.thread_pool import get_io_pool
            from dataviz.views.api.chart_api import _render_saved_chart

            for slot in slots:
                slot.rendered_html = ''
                slot.render_error = None

            def _render_slot(s):
                if s.element_type != 'chart' or not s.chart_id:
                    return
                try:
                    res = _render_saved_chart(
                        s.chart_id, {},
                        drill_source='dashboard',
                        drill_source_id=s.dashboard_id,
                        drill_source_name='My Charts',
                        is_embed=True,
                        request=request,
                        school_id=active_school_id,
                    )
                    s.rendered_html = res.get('html', '')
                    s.render_error = res.get('error')
                except Exception as exc:
                    s.render_error = str(exc)

            render_targets = [s for s in slots if s.element_type == 'chart' and s.chart_id]
            if len(render_targets) > 1:
                pool = get_io_pool()
                futures = [pool.submit(_render_slot, s) for s in render_targets]
                for f in futures:
                    f.result()
            elif len(render_targets) == 1:
                _render_slot(render_targets[0])
            home_dashboard_json = json.dumps(home_dashboard.to_dict())
            home_slots_json = json.dumps([s.to_dict() for s in slots])

        # Schools the user can filter home charts by
        from dataexploration.utils import is_unrestricted
        from studata.models import School as StudataSchool

        if is_unrestricted(request.user):
            user_schools = list(
                StudataSchool.objects
                .order_by('school_name')
                .values('state_id', 'school_name', 'short_name')
            )
            school_count = len(user_schools)
        else:
            user_school_ids = request.user.schools if isinstance(request.user.schools, list) else []
            user_schools = []
            if user_school_ids:
                user_schools = list(
                    StudataSchool.objects
                    .filter(state_id__in=user_school_ids)
                    .order_by('school_name')
                    .values('state_id', 'school_name', 'short_name')
                )
            school_count = len(user_school_ids)

        # Hide already-placed charts (incl. forced) from the dock so users only
        # see charts they can actually add.
        dock_charts = None
        if home_charts_enabled:
            try:
                from users.context_processors import _user_charts
                placed_ids = {s.chart_id for s in slots if s.chart_id}
                dock_charts = [c for c in _user_charts() if c['id'] not in placed_ids]
            except Exception:
                dock_charts = None

        context = {
            'saved_queries': SavedQuery.objects.filter(owner=request.user).order_by('-updated_at')[:5],
            'shared_queries': SavedQuery.objects.filter(shares__shared_with=request.user).order_by('-updated_at')[:5],
            'school_count': school_count,
            'home_charts_enabled': home_charts_enabled,
            'home_dashboard_json': home_dashboard_json,
            'home_slots_json': home_slots_json,
            'home_slots': slots,
            'home_max_charts': home_max_charts,
            'home_forced_chart_ids': home_forced_chart_ids,
            'home_forced_chart_ids_json': json.dumps(home_forced_chart_ids),
            'home_show_chart_location': bool(getattr(cfg, 'home_show_chart_location', True)),
            'user_schools': user_schools,
            'active_school_id': active_school_id,
            'active_school_name': active_school.school_name if active_school else '',
        }
        if dock_charts is not None:
            context['user_charts'] = dock_charts
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
    from dataexploration.catalog_columns import merge_course_program_columns

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
        cfg.home_show_chart_location = request.POST.get('home_show_chart_location') == 'on'
        cfg.disabled_school_years = request.POST.getlist('disabled_school_years')

        raw_forced = request.POST.get('home_forced_charts_json', '')
        try:
            forced = _json.loads(raw_forced) if raw_forced.strip() else []
        except (ValueError, TypeError):
            forced = []
        cfg.home_forced_charts = [int(c) for c in forced if str(c).strip()]
        try:
            cfg.home_max_charts = max(0, int(request.POST.get('home_max_charts') or 0))
        except (TypeError, ValueError):
            cfg.home_max_charts = 0
        cfg.enable_course_program_columns = request.POST.get('enable_course_program_columns') == 'on'

        # Student summary section visibility
        for field in (
            'summary_show_tests', 'summary_show_grades', 'summary_show_diploma',
            'summary_show_ag', 'summary_show_on_track', 'summary_show_cte',
            'summary_show_recognition', 'summary_show_flags',
        ):
            setattr(cfg, field, request.POST.get(field) == 'on')

        # Diploma on-track expected credits per subject per grade
        from dataexploration.view_modules.student_data_utils import DIPLOMA_ON_TRACK_SUBJECTS
        expected_credits = {}
        for index, subject in enumerate(DIPLOMA_ON_TRACK_SUBJECTS):
            years = []
            for year in range(4):
                raw = request.POST.get(f'de_{index}_{year}')
                try:
                    years.append(max(0, int(round(float(raw)))))
                except (TypeError, ValueError):
                    years.append(0)
            expected_credits[subject] = years
        cfg.diploma_expected_credits = expected_credits or None

        cfg.updated_by = request.user
        cfg.save()

        messages.success(request, 'Project settings saved.')
        return redirect('landing:project_settings')

    active_default = cfg.default_columns or DEFAULT_COLUMNS
    active_enabled = cfg.enabled_columns  # None = all enabled
    settings_defs, settings_cats = merge_course_program_columns(COLUMN_DEFINITIONS, COLUMN_CATEGORIES)
    all_col_keys = list(settings_defs.keys())

    from dataviz.utils.compiler import get_db_tables
    db_tables = get_db_tables()
    TABLE_CHOICES = []
    for t in sorted(db_tables.keys()):
        label = t.replace('_', ' ').title()
        if label == 'Users User':
            label = 'Users/User'
        TABLE_CHOICES.append((t, label))

    from dataviz.models import SavedChart
    chart_choices = list(
        SavedChart.objects.order_by('name').values('id', 'name', 'chart_type', 'is_user_menu')
    )
    home_forced = [int(c) for c in (cfg.home_forced_charts or [])]

    from dataexploration.view_modules.student_data_utils import (
        DIPLOMA_ON_TRACK_SUBJECTS, default_diploma_expected_credits,
    )
    _expected_defaults = default_diploma_expected_credits()
    _expected_configured = cfg.diploma_expected_credits or {}
    diploma_expected_rows = []
    for index, subject in enumerate(DIPLOMA_ON_TRACK_SUBJECTS):
        years = _expected_configured.get(subject) or _expected_defaults.get(subject) or [0, 0, 0, 0]
        years = (list(years) + [0, 0, 0, 0])[:4]
        row_years = []
        for value in years:
            try:
                row_years.append(max(0, int(round(float(value)))))
            except (TypeError, ValueError):
                row_years.append(0)
        diploma_expected_rows.append({'index': index, 'subject': subject, 'years': row_years})

    summary_sections = [
        ('Content tabs', [
            {'field': 'summary_show_tests', 'label': 'Tests', 'help': 'Assessment records (state tests, SAT, AP, …).', 'checked': bool(cfg.summary_show_tests)},
            {'field': 'summary_show_grades', 'label': 'Grades', 'help': 'Course grades / marks by year and semester.', 'checked': bool(cfg.summary_show_grades)},
            {'field': 'summary_show_diploma', 'label': 'Diploma', 'help': 'DSUSD diploma requirements and progress.', 'checked': bool(cfg.summary_show_diploma)},
            {'field': 'summary_show_ag', 'label': 'UC/CSU A-G', 'help': 'A-G course requirements.', 'checked': bool(cfg.summary_show_ag)},
            {'field': 'summary_show_on_track', 'label': 'On Track', 'help': 'Grade-level A-G and diploma progress benchmarks (grades 9–11).', 'checked': bool(cfg.summary_show_on_track)},
            {'field': 'summary_show_cte', 'label': 'CTE Pathways', 'help': 'Career & technical education pathways.', 'checked': bool(cfg.summary_show_cte)},
        ]),
        ('Header indicators', [
            {'field': 'summary_show_recognition', 'label': 'Academic recognition', 'help': 'Summa / Magna / Cum Laude badge from unweighted GPA.', 'checked': bool(cfg.summary_show_recognition)},
            {'field': 'summary_show_flags', 'label': 'Support flags', 'help': 'SPED, 504, ELL, GATE, foster, homeless, … badges.', 'checked': bool(cfg.summary_show_flags)},
        ]),
    ]

    context = {
        'cfg': cfg,
        'school_years': school_years,
        'column_definitions': settings_defs,
        'column_categories': settings_cats,
        'enable_course_program_columns': cfg.enable_course_program_columns,
        # JSON-safe strings for use in <script> blocks
        'active_default_columns_json': _json.dumps(active_default),
        'active_enabled_columns_json': _json.dumps(active_enabled),
        'all_column_keys_json':        _json.dumps(all_col_keys),
        'column_labels_json':          _json.dumps(settings_defs),
        'dataset_tables':              TABLE_CHOICES,
        'enabled_dataset_tables':      cfg.enabled_dataset_tables or [],
        'enable_home_charts':          cfg.enable_home_charts,
        'home_show_chart_location':    bool(getattr(cfg, 'home_show_chart_location', True)),
        'disabled_school_years':       [str(sy) for sy in (cfg.disabled_school_years or [])],
        'chart_choices':               chart_choices,
        'chart_choices_json':          _json.dumps(chart_choices),
        'home_forced_charts':          home_forced,
        'home_forced_charts_json':     _json.dumps(home_forced),
        'home_max_charts':             int(cfg.home_max_charts or 0),
        'summary_sections':            summary_sections,
        'diploma_expected_rows':       diploma_expected_rows,
    }
    return render(request, 'landing/project_settings.html', context)


