#!/usr/bin/env python
"""One-time standalone seeder for the CCGI DataViz metadata.

This script lives *outside* the Django project (``django_pro/``) and is meant to
be run once, inside the Django Docker container, against a clone whose
ClickHouse analytics tables already exist but which has no CCGI datasets,
charts, or pages yet.

It assumes these five flat analytics tables are present and populated:

    * analytics_ccgi_favorite_career      (one row per student/career)
    * analytics_ccgi_career_cluster       (one row per student/cluster)
    * analytics_ccgi_college_financial    (one row per student)
    * analytics_ccgi_college_application  (one row per student/application)
    * analytics_ccgi_application_status   (one row per student/system/status)

and creates/refreshes only the DataViz metadata: Datasets, SavedCharts, and two
DataVizPage records (plus the "CCGI" menu category, nested under College &
Career Readiness when that category exists).

The runtime environment (Django settings, PostgreSQL and ClickHouse connection
details) is provided by the ``django`` service in docker-compose, so run the
script through Compose rather than ``docker exec`` so that environment applies:

    docker compose exec django python /app/scripts/create_ccgi_dataviz.py

Optional flags:

    --owner you@example.com     own the created records (default: first superuser)

If ``./scripts`` is not bind-mounted into the container, copy the file in first:

    docker cp scripts/create_ccgi_dataviz.py dw_django:/tmp/
    docker compose exec django python /tmp/create_ccgi_dataviz.py

The script bootstraps Django itself (no ``manage.py`` required) and is
idempotent, so re-running simply refreshes the datasets/charts/pages.
"""
import argparse
import os
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Django bootstrap (must happen before importing any models)
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_django() -> str:
    """Locate the Django project, add it to sys.path, and django.setup()."""
    project_dir = os.environ.get("DJANGO_PROJECT_DIR", "").strip()
    if not project_dir or not (Path(project_dir) / "manage.py").exists():
        # Search the script location and the current working directory upwards
        # for a folder that contains manage.py (directly or under django_pro/).
        start_dirs = [Path(__file__).resolve().parent, Path.cwd()]
        for start in start_dirs:
            for parent in [start, *start.parents]:
                if (parent / "manage.py").exists():
                    project_dir = str(parent)
                    break
                if (parent / "django_pro" / "manage.py").exists():
                    project_dir = str(parent / "django_pro")
                    break
            if project_dir:
                break
    if not project_dir:
        raise SystemExit(
            "Could not locate the Django project. Set DJANGO_PROJECT_DIR to the "
            "directory containing manage.py (e.g. /app/django_pro)."
        )

    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django  # noqa: WPS433 (import after sys.path is set up)

    django.setup()
    return project_dir


# ─────────────────────────────────────────────────────────────────────────────
# CCGI spec (mirrors ccgi.management.commands.seed_ccgi)
# ─────────────────────────────────────────────────────────────────────────────

FAVORITE_CAREER_TABLE = 'analytics_ccgi_favorite_career'
CAREER_CLUSTER_TABLE = 'analytics_ccgi_career_cluster'
COLLEGE_FINANCIAL_TABLE = 'analytics_ccgi_college_financial'
COLLEGE_APPLICATION_TABLE = 'analytics_ccgi_college_application'
APPLICATION_STATUS_TABLE = 'analytics_ccgi_application_status'
WEBGRANTS_TABLE = 'webgrants'

CCGI_TABLES = (
    FAVORITE_CAREER_TABLE,
    CAREER_CLUSTER_TABLE,
    COLLEGE_FINANCIAL_TABLE,
    COLLEGE_APPLICATION_TABLE,
    APPLICATION_STATUS_TABLE,
    WEBGRANTS_TABLE,
)

STUDENT_COLUMNS = [
    'ssid',
    'student_name',
    'grade',
    'school',
    'school_state_id',
    'status',
    'school_year',
]

APPLICATION_COLUMNS = STUDENT_COLUMNS + [
    'system', 'application_status', 'college_name',
    'most_recent_completed_date',
    'csu_eligibility', 'uc_eligibility', 'application_id',
    'csu_gpa', 'uc_gpa',
]
STATUS_COLUMNS = STUDENT_COLUMNS + [
    'system', 'application_status', 'eligibility', 'csu_gpa', 'uc_gpa',
]

# WebGrants is a PostgreSQL table with no school/grade columns, so the SQL
# dataset joins it to current_student for the current school year.
WEBGRANTS_SQL = """
SELECT
    w.ssid AS ssid,
    coalesce(
        cs.student_name,
        trim(coalesce(w.last_name, '') || ', ' || coalesce(w.first_name, '')),
        ''
    ) AS student_name,
    coalesce(cs.grade, '') AS grade,
    coalesce(cs.school, 'Non-DS School') AS school,
    coalesce(cs.school_state_id, '') AS school_state_id,
    coalesce(cs.status, '') AS status,
    coalesce(cs.school_year, '{{This School Year}}') AS school_year,
    coalesce(w.fin_aid_app_submitted, '') AS fin_aid_app_submitted,
    coalesce(w.awarded_cal_grant, '') AS awarded_cal_grant,
    coalesce(w.action_needed, '') AS action_needed,
    coalesce(w.calkids_claimed, '') AS calkids_claimed,
    coalesce(w.gpa_uploaded, '') AS gpa_uploaded,
    coalesce(w.gpa_matched, '') AS gpa_matched,
    coalesce(w.sai, '') AS sai,
    coalesce(w.enrl_date, '') AS enrl_date,
    coalesce(w.withdrawal_date, '') AS withdrawal_date,
    1 AS student_count
FROM webgrants w
LEFT JOIN current_student cs
    ON cs.ssid = w.ssid
   AND cs.school_year = '{{This School Year}}'
WHERE w.ssid IS NOT NULL AND btrim(w.ssid) <> ''
"""
WEBGRANTS_COLUMNS = [
    'ssid', 'student_name', 'grade', 'school', 'school_state_id', 'status',
    'school_year', 'fin_aid_app_submitted', 'awarded_cal_grant',
    'action_needed', 'calkids_claimed', 'gpa_uploaded', 'gpa_matched',
    'sai', 'enrl_date', 'withdrawal_date', 'student_count',
]

COLLEGE_SYSTEMS = ('CSU', 'UC', 'CCC')

CAL_GRANT_AWARD_ORDER = ['Yes', 'No', 'Not ready']
ACTION_NEEDED_ORDER = [
    'Outreach to submit app and/or missing contributor',
    'Outreach to submit fin aid app & upload GPA',
    'Outreach to resolve fin aid app errors or missing contributor',
    'Manually match GPA',
    'Upload GPA',
    'Have student verify dependency status with college FAO',
    'No Action Needed',
]

# One dataset per underlying analytics table.  Charts that need a single
# college system narrow the shared applications/status dataset with a
# chart-level ``source_filters`` entry instead of a dedicated dataset.
DATASET_SPECS = [
    {
        'name': 'CCGI Career Explorer',
        'base_table': FAVORITE_CAREER_TABLE,
        'columns': STUDENT_COLUMNS + ['career'],
        'description': 'Flattened CCGI favorite-career selections, one row per '
                       '(student, career), enriched with school/grade.',
    },
    {
        'name': 'CCGI Career Clusters',
        'base_table': CAREER_CLUSTER_TABLE,
        'columns': STUDENT_COLUMNS + ['cluster', 'cluster_count'],
        'description': 'Flattened CCGI career-cluster interest, one row per '
                       '(student, cluster) with the number of selected careers '
                       'in that cluster.',
    },
    {
        'name': 'CCGI College & Financial',
        'base_table': COLLEGE_FINANCIAL_TABLE,
        'columns': STUDENT_COLUMNS + [
            'csu_gpa', 'csu_eligibility', 'csu_completed_apps', 'csu_in_progress_apps',
            'uc_gpa', 'uc_eligibility', 'uc_completed_apps', 'uc_in_progress_apps',
            'ccc_completed_apps', 'ccc_completed_app',
            'ccc_most_recent_completed_date', 'financial_aid_status',
            'csu_eligibility_detail', 'uc_eligibility_detail',
            'csu_requires_review', 'uc_requires_review', 'review_scope',
            'ccc_apply_id',
        ],
        'description': 'Per-student CCGI CSU/UC/CCC eligibility, applications, '
                       'and financial-aid application status.',
    },
    {
        'name': 'CCGI College Applications',
        'base_table': COLLEGE_APPLICATION_TABLE,
        'columns': APPLICATION_COLUMNS,
        'description': 'One row per CCGI college application across CSU, UC, and '
                       'CCC, including the application status (Completed / In '
                       'Progress) and the destination college.',
    },
    {
        'name': 'CCGI Application Status',
        'base_table': APPLICATION_STATUS_TABLE,
        'columns': STATUS_COLUMNS,
        'description': 'One row per (student, system, status) where status is '
                       'Completed, In Progress, or Not Applied — includes on-track '
                       'students who have not yet applied.',
    },
    {
        'name': 'CCGI WebGrants',
        'source_type': 'sql',
        'base_table': WEBGRANTS_TABLE,
        'columns': WEBGRANTS_COLUMNS,
        'sql': WEBGRANTS_SQL,
        'description': 'WebGrants financial-aid application, Cal Grant award, '
                       'GPA upload/match, and action-needed rows joined to the '
                       'current student school and grade.',
    },
]

# Per-system datasets from an earlier schema, removed on run if present.
OBSOLETE_DATASET_NAMES = [
    *[f'CCGI {system} Applications' for system in COLLEGE_SYSTEMS],
    *[f'CCGI {system} On-Track Status' for system in ('CSU', 'UC')],
]

OBSOLETE_CHART_NAMES = [
    'CCGI/Cal Grant Award & Action Needed',
]

# Drill-through columns per dataset.  ``ssid`` and ``student_id`` lead the
# table; internal join keys such as ``stu_annual_key`` are intentionally absent.
DRILL_COLUMNS_BY_DATASET = {
    'CCGI Career Explorer': [
        'ssid', 'student_id', 'student_name', 'grade', 'school', 'career',
        'school_year',
    ],
    'CCGI Career Clusters': [
        'ssid', 'student_id', 'student_name', 'grade', 'school', 'cluster',
        'cluster_count', 'school_year',
    ],
    'CCGI College & Financial': [
        'ssid', 'student_id', 'student_name', 'grade', 'school',
        'csu_gpa', 'csu_eligibility', 'csu_eligibility_detail',
        'uc_gpa', 'uc_eligibility', 'uc_eligibility_detail',
        'review_scope',
        'ccc_completed_app', 'ccc_apply_id', 'financial_aid_status',
        'school_year',
    ],
    'CCGI College Applications': [
        'ssid', 'student_id', 'student_name', 'grade', 'school',
        'system', 'application_status', 'college_name',
        'most_recent_completed_date', 'application_id',
        'csu_gpa', 'uc_gpa', 'school_year',
    ],
    'CCGI Application Status': [
        'ssid', 'student_id', 'student_name', 'grade', 'school',
        'system', 'application_status', 'eligibility',
        'csu_gpa', 'uc_gpa', 'school_year',
    ],
    'CCGI WebGrants': [
        'ssid', 'student_name', 'grade', 'school',
        'fin_aid_app_submitted', 'awarded_cal_grant', 'action_needed',
        'calkids_claimed', 'gpa_uploaded', 'gpa_matched', 'sai',
        'enrl_date', 'withdrawal_date', 'school_year',
    ],
}


def _base_cfg(chart_type, title, **overrides):
    """A complete chart config matching the saved CCGI charts."""
    cfg = {'agg': 'count',
 'limit': None,
 'top_n': None,
 'x_col': '',
 'y_col': '',
 'smooth': False,
 'opacity': 0.9,
 'palette': '',
 'ds_lines': [],
 'grid_top': None,
 'pl_scale': [],
 'sort_col': '_value',
 'sort_dir': 'desc',
 'animation': True,
 'bar_polar': False,
 'color_map': {},
 'grid_left': None,
 'group_col': '',
 'group_gap': 0,
 'line_fill': False,
 'show_grid': True,
 'show_zoom': False,
 'stack_col': '',
 'title_top': None,
 'background': 'white',
 'bar_layout': 'grouped',
 'bar_radial': False,
 'chart_type': 'bar',
 'grid_right': None,
 'legend_pos': 'bottom',
 'legend_top': None,
 'pg_display': 'stacked_bar',
 'series_col': '',
 'show_title': True,
 'st_col_col': '',
 'st_row_col': '',
 'title_left': None,
 'y_axis_max': None,
 'y_axis_min': None,
 'chart_title': '',
 'compare_gap': 30,
 'data_labels': True,
 'ds_date_col': '',
 'grid_bottom': None,
 'legend_left': None,
 'line_layout': 'standard',
 'show_points': False,
 'st_agg_func': 'count',
 'st_col_cols': [],
 'st_col_mode': 'values',
 'st_row_cols': [],
 'stack_order': [],
 'x_value_map': {},
 'agg_decimals': None,
 'dyn_agg_func': 'count',
 'dyn_col_cols': [],
 'dyn_col_mode': 'pivot',
 'dyn_row_cols': [],
 'pg_after_col': '',
 'show_percent': False,
 'show_toolbox': True,
 'show_tooltip': True,
 'st_value_col': '',
 'ac_include_pl': [],
 'dyn_value_col': '',
 'legend_offset': 0,
 'pg_before_col': '',
 'show_subtitle': False,
 'x_date_bucket': '',
 'ac_col_filters': {},
 'category_order': [],
 'chart_subtitle': '',
 'ds_date_bucket': 'day',
 'dyn_drill_calc': '',
 'group_gap_size': 0,
 'label_distance': None,
 'label_position': 'top',
 'pg_after_label': '',
 'ac_include_cats': [],
 'bar_orientation': 'vertical',
 'compare_pl_cols': [],
 'container_width': None,
 'dyn_totals_only': False,
 'label_font_size': 10,
 'legend_item_gap': 10,
 'parliament_rows': None,
 'pg_before_label': '',
 'show_categories': True,
 'st_agg_decimals': None,
 'title_font_size': 16,
 'bar_category_gap': '20',
 'container_height': None,
 'dyn_agg_decimals': None,
 'dyn_calculations': [],
 'dyn_display_type': 'table',
 'legend_font_size': 11,
 'legend_icon_size': 10,
 'parliament_shape': 'semi-circle',
 'percent_decimals': 1,
 'show_axis_labels': True,
 'show_axis_titles': True,
 'show_group_total': False,
 'st_show_tooltips': True,
 'tooltip_show_pct': True,
 'x_label_rotation': 0,
 'bar_border_radius': 0,
 'dyn_show_tooltips': True,
 'group_total_label': 'Total',
 'tooltip_value_pct': False,
 'x_label_font_size': 11,
 'col_header_aliases': {},
 'parliament_show_ss': False,
 'st_show_col_totals': True,
 'st_show_row_totals': True,
 'tooltip_show_count': True,
 'x_label_max_length': 0,
 'center_group_labels': True,
 'dyn_show_col_totals': True,
 'dyn_show_row_totals': True,
 'show_percent_symbol': True,
 'compare_cat_rotation': 0,
 'parliament_dot_scale': 1.0,
 'compare_cat_font_size': 12,
 'force_show_all_labels': False,
 'one_row_per_stu_annual_key': False}
    cfg['chart_type'] = chart_type
    cfg['chart_title'] = title
    cfg.update(overrides)
    return cfg


CHART_SPECS = [
    # ── Career Explorer ──────────────────────────────────────────────
    {
        'name': 'CCGI/Top Favorite Careers',
        'chart_type': 'bar',
        'dataset': 'CCGI Career Explorer',
        'config': _base_cfg(
            'bar', 'Top 20 Favorite Careers',
            x_col='career', agg='count', top_n=20,
        ),
    },
    {
        'name': 'CCGI/Students by Career Cluster',
        'chart_type': 'bar',
        'dataset': 'CCGI Career Clusters',
        'config': _base_cfg(
            'bar', 'Students Interested by Career Cluster',
            x_col='cluster', agg='count',
        ),
    },
    {
        'name': 'CCGI/Career Cluster Interest by Grade',
        'chart_type': 'bar',
        'dataset': 'CCGI Career Clusters',
        'config': _base_cfg(
            'bar', 'Career Cluster Interest Depth by Grade', bar_layout='stacked',
            x_col='cluster', stack_col='grade', series_col='grade',
            y_col='cluster_count', agg='sum', data_labels=False,
        ),
    },
    # ── College & Financial ──────────────────────────────────────────
    {
        'name': 'CCGI/CSU Eligibility',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'CSU Eligibility', x_col='csu_eligibility', agg='count',
        ),
    },
    {
        'name': 'CCGI/UC Eligibility',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'UC Eligibility', x_col='uc_eligibility', agg='count',
        ),
    },
    {
        'name': 'CCGI/CSU Eligibility by School',
        'chart_type': 'bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'bar', 'CSU Eligibility by School', bar_layout='stacked',
            x_col='school', stack_col='csu_eligibility',
            series_col='csu_eligibility', agg='count', data_labels=False,
        ),
    },
    {
        'name': 'CCGI/UC Eligibility by School',
        'chart_type': 'bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'bar', 'UC Eligibility by School', bar_layout='stacked',
            x_col='school', stack_col='uc_eligibility',
            series_col='uc_eligibility', agg='count', data_labels=False,
        ),
    },
    {
        'name': 'CCGI/CCC Applications Completed by School',
        'chart_type': 'bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'bar', 'CCC Applications Completed by School',
            x_col='school', y_col='ccc_completed_apps', agg='sum',
            data_labels=True,
        ),
    },
    {
        'name': 'CCGI/Financial Aid Application Status',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'Financial Aid Application Status',
            x_col='financial_aid_status', agg='count',
        ),
    },
    {
        'name': 'CCGI/Financial Aid Application Submitted',
        'chart_type': 'donut',
        'dataset': 'CCGI WebGrants',
        'config': _base_cfg(
            'donut', 'Financial Aid Application Submitted',
            x_col='fin_aid_app_submitted', y_col='ssid', agg='count_unique',
        ),
    },
    {
        'name': 'CCGI/Awarded Cal Grant',
        'chart_type': 'donut',
        'dataset': 'CCGI WebGrants',
        'config': _base_cfg(
            'donut', 'Awarded Cal Grant',
            x_col='awarded_cal_grant', y_col='ssid', agg='count_unique',
            category_order=list(CAL_GRANT_AWARD_ORDER), sort_col='__manual__',
        ),
    },
    {
        'name': 'CCGI/Action Needed',
        'chart_type': 'bar',
        'dataset': 'CCGI WebGrants',
        'config': _base_cfg(
            'bar', 'Action Needed',
            x_col='action_needed', y_col='ssid', agg='count_unique',
            bar_orientation='horizontal', data_labels=True, top_n=20,
            sort_col='_value', sort_dir='desc',
        ),
    },
    {
        'name': 'CCGI/Requires Review Scope (CSU & UC)',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'Eligibility Requires Review (CSU vs UC)',
            x_col='review_scope', agg='count',
        ),
    },
    {
        'name': 'CCGI/Students Requiring Review by School',
        'chart_type': 'bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'bar', 'Students Requiring Review by School', bar_layout='stacked',
            x_col='school', stack_col='review_scope', series_col='review_scope',
            agg='count', data_labels=False,
        ),
    },
    # ── College applications (CSU / UC / CCC) ────────────────────────
    {
        'name': 'CCGI/Applications by Status & System',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'config': _base_cfg(
            'bar', 'College Applications by System', bar_layout='stacked',
            x_col='system', stack_col='application_status',
            series_col='application_status', agg='count',
        ),
    },
    {
        'name': 'CCGI/CSU Applications by College',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'source_filters': {'system': ['CSU']},
        'config': _base_cfg(
            'bar', 'CSU Applications by College', x_col='college_name', agg='count',
            top_n=20,
        ),
    },
    {
        'name': 'CCGI/UC Applications by College',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'source_filters': {'system': ['UC']},
        'config': _base_cfg(
            'bar', 'UC Applications by College', x_col='college_name', agg='count',
            top_n=20,
        ),
    },
    {
        'name': 'CCGI/CCC Applications by College',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'source_filters': {'system': ['CCC']},
        'config': _base_cfg(
            'bar', 'CCC Applications by College', x_col='college_name', agg='count',
            top_n=20,
        ),
    },
    {
        'name': 'CCGI/Students Who Applied (Any System)',
        'chart_type': 'gauge',
        'dataset': 'CCGI College Applications',
        'config': _base_cfg(
            'gauge', 'Students Who Applied (Any System)',
            y_col='ssid', agg='count_unique',
        ),
    },
    {
        'name': 'CCGI/CSU On-Track Applicants by School',
        'chart_type': 'bar',
        'dataset': 'CCGI Application Status',
        'source_filters': {'system': ['CSU'], 'eligibility': ['On-Track']},
        'config': _base_cfg(
            'bar', 'CSU On-Track Applicants by School', bar_layout='stacked',
            x_col='school', stack_col='application_status',
            series_col='application_status', y_col='ssid', agg='count_unique',
            stack_order=['Not Applied', 'In Progress', 'Completed'],
            data_labels=True, label_position='inside',
        ),
    },
    {
        'name': 'CCGI/UC On-Track Applicants by School',
        'chart_type': 'bar',
        'dataset': 'CCGI Application Status',
        'source_filters': {'system': ['UC'], 'eligibility': ['On-Track']},
        'config': _base_cfg(
            'bar', 'UC On-Track Applicants by School', bar_layout='stacked',
            x_col='school', stack_col='application_status',
            series_col='application_status', y_col='ssid', agg='count_unique',
            stack_order=['Not Applied', 'In Progress', 'Completed'],
            data_labels=True, label_position='inside',
        ),
    },
]


PAGE_SPECS = [
    {
        'title': 'CCGI Career Explorer',
        'slug': 'ccgi-favorite-careers',
        'description': 'Favorite careers and career-cluster interest. Filter by '
                       'school and grade, then click any bar to drill into the '
                       'matching students.',
        'page_filters': [
            {
                'id': 'school', 'label': 'School', 'source_col': 'school',
                'source_table': FAVORITE_CAREER_TABLE,
                'widget': 'select', 'default_val': '', 'hidden': False,
            },
            {
                'id': 'grade', 'label': 'Grade', 'source_col': 'grade',
                'source_table': FAVORITE_CAREER_TABLE,
                'widget': 'multi', 'default_val': '', 'hidden': False,
            },
        ],
        'rows': [
            {'label': 'Top Favorite Careers', 'sections': [
                {'chart': 'CCGI/Top Favorite Careers', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'label': 'Career Cluster Interest', 'sections': [
                {'chart': 'CCGI/Students by Career Cluster', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI/Career Cluster Interest by Grade', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
        ],
    },
    {
        'title': 'CCGI College & Financial',
        'slug': 'ccgi-college-financial',
        'description': 'CSU, UC, and CCC eligibility and applications, plus '
                       'financial-aid application status, WebGrants financial-aid '
                       'application submission, and Cal Grant award/action-needed '
                       'data. Filter by school and drill into students from any chart.',
        'page_filters': [
            {
                'id': 'school', 'label': 'School', 'source_col': 'school',
                'source_table': COLLEGE_FINANCIAL_TABLE,
                'widget': 'select', 'default_val': '', 'hidden': False,
            },
        ],
        'tabs': ['Overview', 'WebGrants', 'CSU', 'UC', 'CCC'],
        'rows': [
            {'tab': 'Overview', 'label': 'Applicant Summary', 'sections': [
                {'chart': 'CCGI/Students Who Applied (Any System)', 'gs_x': 0, 'gs_w': 4, 'gs_h': 5},
            ]},
            {'tab': 'Overview', 'label': 'Applications by System', 'sections': [
                {'chart': 'CCGI/Applications by Status & System', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'Overview', 'label': 'Eligibility Requires Review', 'sections': [
                {'chart': 'CCGI/Requires Review Scope (CSU & UC)', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI/Students Requiring Review by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'Overview', 'label': 'Financial Aid', 'sections': [
                {'chart': 'CCGI/Financial Aid Application Status', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'WebGrants', 'label': 'Financial Aid Application and Cal Grant Award', 'sections': [
                {'chart': 'CCGI/Financial Aid Application Submitted', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI/Awarded Cal Grant', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'WebGrants', 'label': 'Action Needed', 'sections': [
                {'chart': 'CCGI/Action Needed', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
            ]},
            {'tab': 'CSU', 'label': 'CSU Eligibility', 'sections': [
                {'chart': 'CCGI/CSU Eligibility', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI/CSU Eligibility by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'CSU', 'label': 'CSU Applications', 'sections': [
                {'chart': 'CCGI/CSU Applications by College', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'CSU', 'label': 'CSU On-Track Applicants (Not Applied / In Progress / Completed)', 'sections': [
                {'chart': 'CCGI/CSU On-Track Applicants by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'UC', 'label': 'UC Eligibility', 'sections': [
                {'chart': 'CCGI/UC Eligibility', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI/UC Eligibility by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'UC', 'label': 'UC Applications', 'sections': [
                {'chart': 'CCGI/UC Applications by College', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'UC', 'label': 'UC On-Track Applicants (Not Applied / In Progress / Completed)', 'sections': [
                {'chart': 'CCGI/UC On-Track Applicants by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'CCC', 'label': 'CCC Applications', 'sections': [
                {'chart': 'CCGI/CCC Applications Completed by School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI/CCC Applications by College', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Creation logic (mirrors the seed_ccgi management command)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_owner(owner_ref):
    from users.models import User

    if owner_ref:
        user = (
            User.objects.filter(username=owner_ref).first()
            or User.objects.filter(email__iexact=owner_ref).first()
        )
        if not user:
            raise SystemExit(f'No user found for "{owner_ref}".')
        return user

    user = User.objects.filter(is_superuser=True).order_by('id').first()
    if not user:
        user = User.objects.order_by('id').first()
    if not user:
        raise SystemExit('No users exist to own the CCGI dashboards.')
    return user


def enable_dataset_tables():
    """Add the CCGI analytics tables to the project's dataset allowlist."""
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        print('enabled_dataset_tables is empty; leaving unrestricted.')
        return
    missing = [t for t in CCGI_TABLES if t not in enabled]
    if missing:
        cfg.enabled_dataset_tables = enabled + missing
        cfg.save()
        print(f'Enabled datasets: {", ".join(missing)}')


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset

    dataset = (
        Dataset.objects.filter(name=spec['name'], owner=owner)
        .order_by('id')
        .first()
    )
    if dataset is None:
        dataset = Dataset(name=spec['name'], owner=owner)

    source_type = spec.get('source_type', Dataset.SOURCE_VISUAL)
    dataset.source_type = source_type
    dataset.description = spec['description']
    dataset.joins = []
    dataset.filters = spec.get('filters', [])
    dataset.status_filter = []  # all statuses
    dataset.order_by = []

    if source_type == Dataset.SOURCE_SQL:
        dataset.source_sql = spec['sql']
        dataset.base_table = spec['base_table']
        dataset.distinct = False
        dataset.columns = list(spec['columns'])
        dataset.column_mappings = {c: c for c in spec['columns']}
    else:
        base = spec['base_table']
        dataset.source_sql = ''
        dataset.base_table = base
        dataset.distinct = True
        dataset.columns = [f'{base}.{c}' for c in spec['columns']]
        dataset.column_mappings = {f'{base}.{c}': c for c in spec['columns']}

    dataset.save()
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def remove_obsolete_datasets():
    from dataviz.models import Dataset

    qs = Dataset.objects.filter(name__in=OBSOLETE_DATASET_NAMES)
    count = qs.count()
    if count:
        qs.delete()
        print(f'  removed {count} obsolete dataset(s): {", ".join(OBSOLETE_DATASET_NAMES)}')


def remove_obsolete_charts():
    from dataviz.models import SavedChart, DataVizPageSection

    qs = SavedChart.objects.filter(name__in=OBSOLETE_CHART_NAMES)
    attached = set(
        DataVizPageSection.objects.filter(chart__in=qs)
        .values_list('chart_id', flat=True)
    )
    qs = qs.exclude(pk__in=attached)
    count = qs.count()
    if count:
        qs.delete()
    print(f'  removed {count} obsolete chart(s)')


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    dataset = datasets[spec['dataset']]
    chart, _ = SavedChart.objects.update_or_create(
        name=spec['name'],
        owner=owner,
        defaults={
            'chart_type': spec['chart_type'],
            'config': spec['config'],
            'source_type': SavedChart.SOURCE_DATASET,
            'source_id': dataset.pk,
            'source_sql': '',
            'source_filters': spec.get('source_filters', {}),
            'column_defs': {},
            'school_filter_mode': 'enrolled',
            'drill_through_columns': spec.get(
                'drill_through_columns',
                DRILL_COLUMNS_BY_DATASET.get(spec['dataset'], []),
            ),
            'drill_through_filter_map': spec.get('drill_through_filter_map', {}),
            'description': '',
        },
    )
    print(f'  chart ready:   {chart.name} (#{chart.pk})')
    return chart


def upsert_page(spec, charts, owner):
    from django.db import transaction
    from dataviz.models import (
        DataVizPage, DataVizPageRow, DataVizPageSection, DataVizPageTab,
    )
    from dataviz.utils.page_publish import publish_page

    page, _ = DataVizPage.objects.update_or_create(
        slug=spec['slug'],
        defaults={
            'title': spec['title'],
            'description': spec['description'],
            'folder': 'CCGI',
            'container_width': DataVizPage.CONTAINER_XL,
            'show_header': True,
            'owner': owner,
            'page_filters': spec['page_filters'],
            'status': DataVizPage.STATUS_DRAFT,
        },
    )
    with transaction.atomic():
        page.rows.all().delete()
        page.tabs.all().delete()

        tab_map = {}
        for t_idx, tab_name in enumerate(spec.get('tabs', [])):
            tab_map[tab_name] = DataVizPageTab.objects.create(
                page=page, name=tab_name, position=t_idx,
            )

        for r_idx, row_spec in enumerate(spec['rows']):
            row = DataVizPageRow.objects.create(
                page=page,
                label=row_spec.get('label', ''),
                position=r_idx,
                tab=tab_map.get(row_spec.get('tab')),
            )
            for s_idx, sec_spec in enumerate(row_spec['sections']):
                DataVizPageSection.objects.create(
                    page=page,
                    row=row,
                    element_type=DataVizPageSection.TYPE_CHART,
                    chart=charts[sec_spec['chart']],
                    show_title=True,
                    gs_x=sec_spec.get('gs_x', 0),
                    gs_y=sec_spec.get('gs_y', 0),
                    gs_w=sec_spec.get('gs_w', 6),
                    gs_h=sec_spec.get('gs_h', 6),
                    position=s_idx,
                )

    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def attach_to_menu(pages):
    """Place the CCGI pages under a shared 'CCGI' menu category."""
    from users.models_pages import Page

    parent = Page.objects.filter(slug='college-career-readiness').first()
    category, _ = Page.objects.update_or_create(
        slug='ccgi',
        defaults={
            'name': 'CCGI',
            'app_reference': 'ccgi',
            'description': 'College & Career Guidance Initiative dashboards',
            'url_path': '',
            'status': 'active',
            'is_menu_category': True,
            'show_in_menu': True,
            'parent_page': parent,
            'menu_order': 1 if parent else 60,
            'required_roles': [],
            'is_public': False,
        },
    )
    for order, page in enumerate(pages):
        if not page.linked_site_page_id:
            continue
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = order
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print('  menu category "CCGI" updated.')


def main():
    parser = argparse.ArgumentParser(
        description='Create the CCGI DataViz datasets, charts, and pages.',
    )
    parser.add_argument(
        '--owner', default='',
        help='Username or email of the records owner (default: first superuser).',
    )
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')

    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()

    datasets = {}
    for spec in DATASET_SPECS:
        datasets[spec['name']] = upsert_dataset(spec, owner)

    charts = {}
    for spec in CHART_SPECS:
        charts[spec['name']] = upsert_chart(spec, datasets, owner)

    remove_obsolete_datasets()

    pages = []
    for spec in PAGE_SPECS:
        pages.append(upsert_page(spec, charts, owner))

    attach_to_menu(pages)
    remove_obsolete_charts()

    print()
    print(f'Done. {len(datasets)} datasets, {len(charts)} charts, '
          f'{len(pages)} pages created/refreshed.')


if __name__ == '__main__':
    main()
