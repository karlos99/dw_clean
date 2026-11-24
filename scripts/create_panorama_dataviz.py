#!/usr/bin/env python
"""Seed DataViz datasets, charts, and pages for Panorama survey responses.

Source table: ``analytics_dbuilder_panorama_survey`` (one row per student
answer), enriched with grade / ELL / race / gender from ``current_student``.

The pages are organised by survey section, then by grade level and student
group, and finally by category -> question.  Every chart is drillable back to
the individual survey answers.

Run:
    docker compose exec django python /app/scripts/create_panorama_dataviz.py
Optional:
    --owner you@example.com
    --qa

Rerun after syncing new Panorama responses: chart data is live from the
dataset SQL, while focal year/window defaults and question ordering are
recomputed by this script.
"""
import argparse
import os
import sys
from pathlib import Path

PANORAMA_TABLE = 'analytics_dbuilder_panorama_survey'
DATASET_NAME = 'Panorama Survey Responses'
DATASET_FOLDER = 'Assessments / Panorama'
PAGE_FOLDER = 'Assessments / Panorama'
CHART_PREFIX = 'Panorama / '

# Survey sections -------------------------------------------------------------
SKILLS_CATEGORIES = [
    'Self-Management',
    'Social Awareness',
    'Growth Mindset',
    'Self-Efficacy',
    'Engagement',
    'Grit',
]
SUPPORTS_CATEGORIES = [
    'School Climate',
    'School Safety',
    'Sense of Belonging',
    'Teacher-Student Relationships',
    'Additional Questions',
]
EQUITY_CATEGORIES = [
    'Cultural Awareness and Action',
    'School Climate',
    'Sense of Belonging',
]
ALL_CATEGORIES = SKILLS_CATEGORIES + SUPPORTS_CATEGORIES + ['Cultural Awareness and Action']

SURVEY_TYPE_ORDER = [
    'Skills & Competencies',
    'Supports & Environment',
    'Equity Survey',
    'Equity & Inclusion',
    'Other',
]

# Response bands --------------------------------------------------------------
BAND_ORDER = [
    'Very Unfavorable',
    'Somewhat Unfavorable',
    'Not Favorable or Unfavorable',
    'Somewhat Favorable',
    'Very Favorable',
]
# The chart builder iterates stacks last-to-first. Keep this reversed so
# "Very Unfavorable" starts at the left/bottom of a horizontal/vertical stack.
BAND_STACK_ORDER = list(reversed(BAND_ORDER))
BAND_COLORS = {
    'Very Unfavorable': '#c00000',
    'Somewhat Unfavorable': '#ed7d31',
    'Not Favorable or Unfavorable': '#ffc000',
    'Somewhat Favorable': '#70ad47',
    'Very Favorable': '#5b9bd5',
}

GRADE_ORDER = [
    'Pre-K', 'Transitional K', 'Kindergarten',
    'Grade 1', 'Grade 2', 'Grade 3', 'Grade 4', 'Grade 5', 'Grade 6',
    'Grade 7', 'Grade 8', 'Grade 9', 'Grade 10', 'Grade 11', 'Grade 12',
    'Other',
]
GRADE_BAND_ORDER = ['Elementary', 'Middle School', 'High School', 'Other']
ELL_ORDER = ['EL', 'RFEP', 'IFEP', 'EO', 'TBD', 'ADEL']
RACE_ORDER = [
    'Hispanic', 'White', 'Asian', 'Black or African American', 'Filipino',
    'American Indian or Alaska Native', 'Hawaiian/Pacific Islander',
    'Multiple', 'Missing',
]
GENDER_ORDER = ['F', 'M', 'X']
SUSPENSION_ORDER = ['Not Suspended', 'Suspended']

DRILL_COLUMNS = [
    'ssid', 'student_name', 'grade_label', 'grade_band', 'school', 'category',
    'question', 'response', 'response_band', 'favorable_pct', 'school_year',
    'survey_window', 'window_label', 'ell', 'race', 'gender', 'suspension_group',
]

COLUMN_DEFS = {
    'ssid': 'Students',
    'student_name': 'Student',
    'grade_label': 'Grade',
    'grade_band': 'Grade Level',
    'school': 'School',
    'category': 'Category',
    'question': 'Question',
    'response': 'Response',
    'response_band': 'Response Band',
    'favorable_pct': '% Favorable',
    'school_year': 'School Year',
    'survey_window': 'Survey Window',
    'window_label': 'Survey Administration',
    'ell': 'ELL Status',
    'race': 'Race',
    'gender': 'Gender',
    'suspension_group': 'Suspension Status',
    'survey_type': 'Survey Section',
    'status': 'Status',
}

# Derived SQL dataset ---------------------------------------------------------
# Keep this query free of write keywords: run_raw_sql allows SELECT/WITH only.
PANORAMA_SQL = f"""
WITH p AS (
    SELECT
        ssid, school, category, question, response, response_value,
        school_year, survey_window
    FROM {PANORAMA_TABLE}
    WHERE school_year != ''
      AND question != ''
      AND response_value IS NOT NULL
),
qmax AS (
    SELECT question, max(response_value) AS max_v
    FROM p
    GROUP BY question
),
flags AS (
    SELECT
        school_year,
        survey_window,
        max(if(category = 'Cultural Awareness and Action', 1, 0)) AS has_equity
    FROM p
    GROUP BY school_year, survey_window
),
cs AS (
    SELECT
        ssid,
        school_year,
        any(grade) AS grade,
        any(student_name) AS student_name,
        any(ell) AS ell,
        any(race) AS race,
        any(gender) AS gender,
        any(suspension_indicator) AS suspension_indicator,
        any(status) AS status
    FROM current_student
    WHERE ssid != ''
    GROUP BY ssid, school_year
)
SELECT
    t.*,
    if(t.response_band IN ('Somewhat Favorable', 'Very Favorable'), 100.0, 0.0) AS favorable_pct
FROM (
    SELECT
        p.ssid AS ssid,
        ifNull(cs.student_name, '') AS student_name,
        p.school AS school,
        p.category AS category,
        p.question AS question,
        if(
            p.response = '', '',
            if(match(p.response, '^[0-9]+$'), concat('Response ', p.response), p.response)
        ) AS response,
        p.school_year AS school_year,
        p.survey_window AS survey_window,
        concat(p.survey_window, ' ', p.school_year) AS window_label,
        ifNull(cs.grade, '') AS grade,
        multiIf(
            upper(trim(ifNull(cs.grade, ''))) IN ('KN', 'K', 'KG'), 'Kindergarten',
            upper(trim(ifNull(cs.grade, ''))) = 'TK', 'Transitional K',
            upper(trim(ifNull(cs.grade, ''))) = 'PK', 'Pre-K',
            match(trim(ifNull(cs.grade, '')), '^0[1-9]$'),
            concat('Grade ', substring(trim(cs.grade), 2, 1)),
            match(trim(ifNull(cs.grade, '')), '^[1-9][0-9]?$'),
            concat('Grade ', trim(cs.grade)),
            'Other'
        ) AS grade_label,
        multiIf(
            upper(trim(ifNull(cs.grade, ''))) IN ('PK', 'TK', 'KN', 'K', 'KG', '01', '02', '03', '04', '05'),
            'Elementary',
            upper(trim(ifNull(cs.grade, ''))) IN ('06', '07', '08'), 'Middle School',
            upper(trim(ifNull(cs.grade, ''))) IN ('09', '10', '11', '12'), 'High School',
            'Other'
        ) AS grade_band,
        ifNull(cs.ell, '') AS ell,
        ifNull(cs.race, '') AS race,
        ifNull(cs.gender, '') AS gender,
        if(toInt32OrZero(ifNull(cs.suspension_indicator, '')) > 0, 'Suspended', 'Not Suspended') AS suspension_group,
        ifNull(cs.status, '') AS status,
        multiIf(
            q.max_v <= 4 AND p.response_value = 1, 'Very Unfavorable',
            q.max_v <= 4 AND p.response_value = 2, 'Somewhat Unfavorable',
            q.max_v <= 4 AND p.response_value = 3, 'Somewhat Favorable',
            q.max_v <= 4 AND p.response_value = 4, 'Very Favorable',
            q.max_v = 5 AND p.response_value = 1, 'Very Unfavorable',
            q.max_v = 5 AND p.response_value = 2, 'Somewhat Unfavorable',
            q.max_v = 5 AND p.response_value = 3, 'Not Favorable or Unfavorable',
            q.max_v = 5 AND p.response_value = 4, 'Somewhat Favorable',
            q.max_v = 5 AND p.response_value = 5, 'Very Favorable',
            q.max_v >= 6 AND p.response_value <= 2, 'Very Unfavorable',
            q.max_v >= 6 AND p.response_value = 3, 'Somewhat Unfavorable',
            q.max_v >= 6 AND p.response_value = 4, 'Not Favorable or Unfavorable',
            q.max_v >= 6 AND p.response_value <= 6, 'Somewhat Favorable',
            q.max_v >= 6 AND p.response_value >= 7, 'Very Favorable',
            ''
        ) AS response_band,
        multiIf(
            f.has_equity = 1 AND p.category = 'Cultural Awareness and Action',
            'Equity & Inclusion',
            f.has_equity = 1 AND p.category IN (
                'School Climate', 'School Safety', 'Sense of Belonging',
                'Teacher-Student Relationships', 'Additional Questions'
            ),
            'Equity Survey',
            p.category IN (
                'Self-Management', 'Social Awareness', 'Growth Mindset',
                'Self-Efficacy', 'Engagement', 'Grit'
            ),
            'Skills & Competencies',
            p.category IN (
                'School Climate', 'School Safety', 'Sense of Belonging',
                'Teacher-Student Relationships', 'Additional Questions'
            ),
            'Supports & Environment',
            'Other'
        ) AS survey_type
    FROM p
    LEFT JOIN qmax q ON q.question = p.question
    LEFT JOIN flags f ON f.school_year = p.school_year AND f.survey_window = p.survey_window
    LEFT JOIN cs ON cs.ssid = p.ssid AND cs.school_year = p.school_year
) AS t
"""


def bootstrap_django() -> str:
    project_dir = os.environ.get('DJANGO_PROJECT_DIR', '').strip()
    if not project_dir or not (Path(project_dir) / 'manage.py').exists():
        start_dirs = [Path(__file__).resolve().parent, Path.cwd()]
        for start in start_dirs:
            for parent in [start, *start.parents]:
                if (parent / 'manage.py').exists():
                    project_dir = str(parent)
                    break
                if (parent / 'django_pro' / 'manage.py').exists():
                    project_dir = str(parent / 'django_pro')
                    break
            if project_dir:
                break
    if not project_dir:
        raise SystemExit('Could not locate Django project (manage.py).')
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    return project_dir


def _esc(value) -> str:
    return str(value).replace('\\', '\\\\').replace("'", "''")


def _in_list(values) -> str:
    return ', '.join(f"'{_esc(v)}'" for v in values)


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
        raise SystemExit('No users exist to own the Panorama pages.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig
    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        return
    added = []
    for table in (PANORAMA_TABLE, 'current_student'):
        if table not in enabled:
            enabled.append(table)
            added.append(table)
    if added:
        cfg.enabled_dataset_tables = enabled
        cfg.save()
        print(f'Enabled dataset tables: {", ".join(added)}')


# Query helpers ---------------------------------------------------------------
def run_query(sql: str) -> list[dict]:
    from dataviz.utils.data_loader import run_raw_sql
    res = run_raw_sql(sql)
    if res.get('error'):
        raise RuntimeError(res['error'])
    return res.get('rows') or []


def survey_window_order() -> list[str]:
    rows = run_query(f"""
        SELECT v FROM (
            SELECT DISTINCT
                concat(survey_window, ' ', school_year) AS v,
                school_year AS sy,
                multiIf(survey_window = 'Fall', 1, survey_window = 'Winter', 2,
                        survey_window = 'Spring', 3, 4) AS k
            FROM {PANORAMA_TABLE}
            WHERE survey_window != '' AND school_year != ''
        )
        ORDER BY sy, k
    """)
    return [r['v'] for r in rows]


def latest_school_year() -> str:
    rows = run_query(f"""
        SELECT max(school_year) AS v
        FROM {PANORAMA_TABLE}
        WHERE school_year != ''
    """)
    if rows and rows[0].get('v'):
        return rows[0]['v']
    return ''


def pick_focal_year(categories=None) -> str:
    where = ''
    if categories:
        where = f"WHERE category IN ({_in_list(categories)})"
    rows = run_query(f"""
        SELECT school_year, uniqExact(category) AS ncat
        FROM {PANORAMA_TABLE}
        {where}
        GROUP BY school_year
        ORDER BY ncat DESC, school_year DESC
        LIMIT 1
    """)
    if rows:
        return rows[0]['school_year']
    try:
        from dataexploration.school_year_utils import resolve_school_year
        return resolve_school_year('__sy_this__') or ''
    except Exception:
        return ''


def pick_focal_window(school_year: str, categories=None) -> str:
    """Most common survey window in a year (Spring for 2025-26, Fall for 2026-27)."""
    if not school_year:
        return ''
    cat_where = ''
    if categories:
        cat_where = f"AND category IN ({_in_list(categories)})"
    rows = run_query(f"""
        SELECT survey_window, count() AS n
        FROM {PANORAMA_TABLE}
        WHERE school_year = '{_esc(school_year)}'
          AND survey_window != ''
          {cat_where}
        GROUP BY survey_window
        ORDER BY n DESC
        LIMIT 1
    """)
    return rows[0]['survey_window'] if rows else ''


def question_order(category: str, school_year: str) -> list[str]:
    """Questions sorted weakest -> strongest for the focal administration."""
    rows = run_query(f"""
        SELECT p.question AS question, avg(p.response_value / q.max_v) AS score
        FROM {PANORAMA_TABLE} AS p
        INNER JOIN (
            SELECT question, max(response_value) AS max_v
            FROM {PANORAMA_TABLE}
            WHERE response_value IS NOT NULL
            GROUP BY question
        ) AS q ON q.question = p.question
        WHERE p.category = '{_esc(category)}'
          AND p.school_year = '{_esc(school_year)}'
          AND p.response_value IS NOT NULL
        GROUP BY p.question
        ORDER BY score ASC, question ASC
    """)
    return [r['question'] for r in rows]


# Chart helpers ---------------------------------------------------------------
def _base_cfg(chart_type, title, **overrides):
    cfg = {
        'chart_type': chart_type,
        'chart_title': title,
        'agg': 'count',
        'x_col': '',
        'y_col': '',
        'stack_col': '',
        'group_col': '',
        'series_col': '',
        'sort_col': '',
        'sort_dir': 'asc',
        'bar_layout': 'grouped',
        'bar_orientation': 'vertical',
        'background': 'white',
        'legend_pos': 'bottom',
        'data_labels': True,
        'show_grid': True,
        'show_tooltip': True,
        'animation': True,
        'palette': '',
        'color_map': {},
        'label_position': 'top',
        'label_font_size': 10,
        'x_label_rotation': 0,
        'y_axis_min': 0,
    }
    cfg.update(overrides)
    return cfg


def _chart(key, name, chart_type, config, source_filters=None, drill_map=None):
    return {
        'key': key,
        'name': name,
        'chart_type': chart_type,
        'config': config,
        'source_filters': source_filters or {},
        'drill_columns': list(DRILL_COLUMNS),
        'drill_through_filter_map': drill_map or {},
        'column_defs': dict(COLUMN_DEFS),
    }


def fav_chart(key, title, x_col, *, category_order=None, source_filters=None,
              orientation='horizontal', skip=None, **overrides):
    cfg = _base_cfg(
        'bar', title,
        x_col=x_col,
        y_col='favorable_pct',
        agg='avg',
        agg_decimals=0,
        bar_orientation=orientation,
        bar_layout='grouped',
        data_labels=True,
        data_label_suffix='%',
        data_label_decimals=0,
        label_position='right' if orientation == 'horizontal' else 'top',
        percent_decimals=0,
        show_percent_symbol=True,
        y_axis_min=0,
        y_axis_max=100,
        sort_col='__manual__' if category_order else '_value',
        sort_dir='asc',
    )
    if category_order:
        cfg['category_order'] = list(category_order)
    if skip:
        cfg['skip_page_filters'] = list(skip)
    cfg.update(overrides)
    return _chart(
        key, CHART_PREFIX + title, 'bar', cfg, source_filters,
        {'x_col': x_col},
    )


def count_chart(key, title, x_col, *, category_order=None, source_filters=None,
                orientation='horizontal', skip=None, **overrides):
    cfg = _base_cfg(
        'bar', title,
        x_col=x_col,
        y_col='ssid',
        agg='count_unique',
        bar_orientation=orientation,
        bar_layout='grouped',
        data_labels=True,
        label_position='right' if orientation == 'horizontal' else 'top',
        sort_col='__manual__' if category_order else '_value',
        sort_dir='asc',
    )
    if category_order:
        cfg['category_order'] = list(category_order)
    if skip:
        cfg['skip_page_filters'] = list(skip)
    cfg.update(overrides)
    return _chart(
        key, CHART_PREFIX + title, 'bar', cfg, source_filters,
        {'x_col': x_col},
    )


def pct_chart(key, title, x_col, *, category_order=None, source_filters=None,
              orientation='horizontal', skip=None, **overrides):
    cfg = _base_cfg(
        'stacked_bar', title,
        x_col=x_col,
        stack_col='response_band',
        series_col='response_band',
        agg='count',
        bar_layout='stacked',
        bar_orientation=orientation,
        show_percent=True,
        percent_basis='responses',
        percent_decimals=0,
        show_percent_symbol=True,
        y_axis_min=0,
        y_axis_max=100,
        stack_order=list(BAND_STACK_ORDER),
        color_map=dict(BAND_COLORS),
        data_labels=True,
        label_position='inside',
        label_font_size=9,
        legend_pos='bottom',
        sort_col='__manual__' if category_order else '',
        x_label_max_length=70,
    )
    if category_order:
        cfg['category_order'] = list(category_order)
    if skip:
        cfg['skip_page_filters'] = list(skip)
    cfg.update(overrides)
    return _chart(
        key, CHART_PREFIX + title, 'stacked_bar', cfg, source_filters,
        {'x_col': x_col, 'series_col': 'response_band'},
    )


def _composite_order(categories, values):
    return [f'{cat} · {val}' for cat in categories for val in values]


def build_shared_charts(window_order):
    charts = []
    charts.append(count_chart(
        'students_by_window', 'Students Responding by Survey Window',
        'window_label', category_order=window_order,
        orientation='vertical', skip=['school_year', 'survey_window'],
        x_label_max_length=22, x_label_rotation=20,
    ))
    charts.append(fav_chart(
        'fav_by_category', 'Favorability by Category',
        'category', sort_dir='asc',
    ))
    charts.append(pct_chart(
        'dist_by_category', 'Response Distribution by Category',
        'category', category_order=ALL_CATEGORIES,
    ))
    charts.append(count_chart(
        'students_by_category', 'Students Responding by Category',
        'category', category_order=ALL_CATEGORIES,
    ))
    charts.append(fav_chart(
        'fav_by_survey_type', 'Favorability by Survey Section',
        'survey_type', category_order=SURVEY_TYPE_ORDER,
    ))
    charts.append(pct_chart(
        'dist_by_survey_type', 'Response Distribution by Survey Section',
        'survey_type', category_order=SURVEY_TYPE_ORDER,
        orientation='vertical', x_label_rotation=20,
    ))
    charts.append(fav_chart(
        'fav_by_grade', 'Favorability by Grade',
        'grade_label', category_order=GRADE_ORDER,
    ))
    charts.append(count_chart(
        'students_by_grade', 'Students Responding by Grade',
        'grade_label', category_order=GRADE_ORDER,
    ))
    charts.append(pct_chart(
        'dist_by_grade', 'Response Distribution by Grade',
        'grade_label', category_order=GRADE_ORDER,
    ))
    charts.append(fav_chart(
        'fav_by_category_grade', 'Favorability by Category and Grade Level',
        'category,grade_band',
        category_order=_composite_order(ALL_CATEGORIES, GRADE_BAND_ORDER),
        orientation='vertical', sort_col='__manual__',
        x_label_rotation=0, x_label_max_length=34,
        group_gap_size=2, center_group_labels=True,
        force_show_all_labels=True,
    ))
    charts.append(fav_chart(
        'fav_by_ell', 'Favorability by ELL Status',
        'ell', category_order=ELL_ORDER,
    ))
    charts.append(fav_chart(
        'fav_by_race', 'Favorability by Race',
        'race', category_order=RACE_ORDER,
    ))
    charts.append(fav_chart(
        'fav_by_gender', 'Favorability by Gender',
        'gender', category_order=GENDER_ORDER,
    ))
    charts.append(fav_chart(
        'fav_by_suspension', 'Favorability by Suspension Status',
        'suspension_group', category_order=SUSPENSION_ORDER,
    ))
    charts.append(pct_chart(
        'dist_by_ell', 'Response Distribution by ELL Status',
        'ell', category_order=ELL_ORDER,
    ))
    charts.append(pct_chart(
        'dist_by_race', 'Response Distribution by Race',
        'race', category_order=RACE_ORDER,
    ))
    charts.append(pct_chart(
        'dist_by_gender', 'Response Distribution by Gender',
        'gender', category_order=GENDER_ORDER,
    ))
    charts.append(pct_chart(
        'dist_by_suspension', 'Response Distribution by Suspension Status',
        'suspension_group', category_order=SUSPENSION_ORDER,
    ))
    charts.append(fav_chart(
        'fav_by_category_ell', 'Favorability by Category and ELL Status',
        'category,ell',
        category_order=_composite_order(ALL_CATEGORIES, ELL_ORDER),
        orientation='horizontal', sort_col='__manual__',
        x_label_max_length=34,
        group_gap_size=2, center_group_labels=True,
        force_show_all_labels=True,
    ))
    charts.append(fav_chart(
        'fav_by_window', 'Overall Favorability by Survey Window',
        'window_label', category_order=window_order,
        orientation='vertical',
        skip=['school_year', 'survey_window'],
        x_label_max_length=24, x_label_rotation=20,
    ))
    charts.append(pct_chart(
        'dist_by_window', 'Response Distribution by Survey Window',
        'window_label', category_order=window_order,
        skip=['school_year', 'survey_window'],
        x_label_max_length=24,
    ))
    # Highlight the focal (latest) administration while keeping every
    # year/window visible.
    current_win = window_order[-1] if window_order else ''
    prior_wins = window_order[:-1] if window_order else []
    window_colors = {}
    for cat in ALL_CATEGORIES:
        for win in window_order:
            window_colors[f'{cat} · {win}'] = '#1d4ed8' if win == current_win else '#94a3b8'
    charts.append(fav_chart(
        'fav_by_category_window', 'Category Favorability by Survey Window',
        'category,window_label',
        category_order=_composite_order(ALL_CATEGORIES, window_order),
        orientation='vertical', sort_col='__manual__',
        skip=['school_year', 'survey_window'],
        x_label_rotation=25, x_label_max_length=40,
        group_gap_size=2, center_group_labels=True,
        color_map=window_colors,
    ))
    return charts, current_win, prior_wins


def build_category_question_charts(category, focal_year):
    slug = (
        category.lower()
        .replace(' & ', '-')
        .replace(' ', '-')
        .replace('/', '-')
    )
    order = question_order(category, focal_year)
    dist = pct_chart(
        f'dist_question_{slug}', f'Question Distribution — {category}',
        'question', category_order=order,
        source_filters={'category': [category]},
        orientation='horizontal',
        x_label_max_length=62,
    )
    fav = fav_chart(
        f'fav_question_{slug}', f'Question Favorability — {category}',
        'question', source_filters={'category': [category]},
        sort_col='_value', sort_dir='asc',
        orientation='horizontal', x_label_max_length=42,
    )
    return [dist, fav]


def build_equity_comparison_charts(window_order):
    charts = []
    charts.append(fav_chart(
        'equity_fav_by_category_window',
        'Equity Categories — Favorability by Survey Window',
        'category,window_label',
        category_order=_composite_order(EQUITY_CATEGORIES, window_order),
        source_filters={'category': EQUITY_CATEGORIES},
        orientation='vertical', sort_col='__manual__',
        skip=['survey_type', 'school_year', 'survey_window'],
        x_label_rotation=25, x_label_max_length=40,
        group_gap_size=2, center_group_labels=True,
    ))
    charts.append(pct_chart(
        'equity_dist_by_window',
        'Equity Categories — Response Distribution by Survey Window',
        'window_label', category_order=window_order,
        source_filters={'category': EQUITY_CATEGORIES},
        skip=['survey_type', 'school_year', 'survey_window'],
        x_label_max_length=24,
    ))
    return charts


# Page helpers ----------------------------------------------------------------
def _sec(chart, x=0, w=6, h=7):
    return {'chart': chart, 'gs_x': x, 'gs_w': w, 'gs_h': h}


def _html(content, h=3):
    return {'html': content, 'gs_x': 0, 'gs_w': 12, 'gs_h': h}


def page_filters(focal_year, focal_window='', hidden_survey_types=None,
                 include_survey_type_filter=False):
    filters = [
        {
            'id': 'school_year', 'label': 'School Year',
            'source_col': 'school_year', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                f'SELECT DISTINCT school_year AS v FROM {PANORAMA_TABLE} '
                "WHERE school_year != '' ORDER BY v"
            ),
            'widget': 'select', 'default_val': focal_year, 'hidden': False,
        },
        {
            'id': 'survey_window', 'label': 'Survey Window',
            'source_col': 'survey_window', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT v FROM ('
                f'SELECT DISTINCT survey_window AS v, '
                "multiIf(survey_window = 'Fall', 1, survey_window = 'Winter', 2, "
                "survey_window = 'Spring', 3, 4) AS k "
                f"FROM {PANORAMA_TABLE} WHERE survey_window != ''"
                ') ORDER BY k'
            ),
            'widget': 'multi', 'default_val': focal_window, 'hidden': False,
        },
        {
            'id': 'grade_label', 'label': 'Grade',
            'source_col': 'grade_label', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT v FROM ('
                'SELECT DISTINCT multiIf('
                "upper(trim(grade)) IN ('KN','K','KG'), 'Kindergarten', "
                "upper(trim(grade)) = 'TK', 'Transitional K', "
                "upper(trim(grade)) = 'PK', 'Pre-K', "
                "match(trim(grade), '^0[1-9]$'), concat('Grade ', substring(trim(grade), 2, 1)), "
                "match(trim(grade), '^[1-9][0-9]?$'), concat('Grade ', trim(grade)), 'Other'"
                ') AS v, '
                "multiIf(upper(trim(grade)) = 'PK', 0, upper(trim(grade)) = 'TK', 1, "
                "upper(trim(grade)) IN ('KN','K','KG'), 2, "
                "match(trim(grade), '^0[1-9]$'), 10 + toInt32OrZero(substring(trim(grade), 2, 1)), "
                "match(trim(grade), '^[1-9][0-9]?$'), 10 + toInt32OrZero(trim(grade)), 99) AS k "
                'FROM current_student WHERE trim(grade) != \'\''
                ') ORDER BY k, v'
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade_band', 'label': 'Grade Level',
            'source_col': 'grade_band', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT v FROM ('
                'SELECT DISTINCT multiIf('
                "upper(trim(grade)) IN ('PK','TK','KN','K','KG','01','02','03','04','05'), 'Elementary', "
                "upper(trim(grade)) IN ('06','07','08'), 'Middle School', "
                "upper(trim(grade)) IN ('09','10','11','12'), 'High School', 'Other'"
                ') AS v, '
                "multiIf(v = 'Elementary', 1, v = 'Middle School', 2, v = 'High School', 3, 4) AS k "
                'FROM current_student WHERE trim(grade) != \'\''
                ') ORDER BY k, v'
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'school', 'label': 'School',
            'source_col': 'school', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                f'SELECT DISTINCT school AS v FROM {PANORAMA_TABLE} '
                "WHERE trim(school) != '' ORDER BY v"
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'ell', 'label': 'ELL Status',
            'source_col': 'ell', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT DISTINCT ell AS v FROM current_student '
                "WHERE trim(ell) != '' ORDER BY v"
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'race', 'label': 'Race',
            'source_col': 'race', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT DISTINCT race AS v FROM current_student '
                "WHERE trim(race) != '' ORDER BY v"
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'gender', 'label': 'Gender',
            'source_col': 'gender', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT DISTINCT gender AS v FROM current_student '
                "WHERE trim(gender) != '' ORDER BY v"
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'suspension_group', 'label': 'Suspension Status',
            'source_col': 'suspension_group', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                'SELECT DISTINCT '
                "if(toInt32OrZero(ifNull(suspension_indicator, '')) > 0, "
                "'Suspended', 'Not Suspended') AS v "
                'FROM current_student ORDER BY v'
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
    ]
    if hidden_survey_types:
        filters.append({
            'id': 'survey_type', 'label': 'Survey Section',
            'source_col': 'survey_type', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': '',
            'widget': 'multi',
            'default_val': ','.join(hidden_survey_types),
            'hidden': True,
        })
    elif include_survey_type_filter:
        filters.append({
            'id': 'survey_type', 'label': 'Survey Section',
            'source_col': 'survey_type', 'source_table': PANORAMA_TABLE,
            'source_distinct_sql': (
                "SELECT arrayJoin(["
                "'Skills & Competencies', 'Supports & Environment', "
                "'Equity Survey', 'Equity & Inclusion'"
                ']) AS v'
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        })
    return filters


def _intro(title, focal_year, focal_window, body):
    focal_label = f'{focal_window} {focal_year}'.strip()
    return (
        '<div data-pb-p="1" style="padding:2px 4px 8px">'
        f'<p style="margin:0 0 6px;font-size:15px"><strong>{title}</strong></p>'
        f'<p style="margin:0 0 6px;color:#475569">Focal administration: '
        f'<strong>{focal_label}</strong>. '
        f'{body}</p>'
        '<p style="margin:0;color:#64748b;font-size:12px">Filters apply to every chart '
        'on the page. Time-comparison charts (participation by window and the '
        '<em>Survey Windows</em> tab) intentionally ignore the year/window filters so '
        'each administration is shown separately and the focal year stays identifiable.</p>'
        '</div>'
    )


# Page layout -----------------------------------------------------------------
def overview_page(focal_year, focal_window):
    tabs = ['Overview', 'By Grade Level', 'Student Groups', 'Survey Windows']
    rows = [
        {'tab': 'Overview', 'label': '', 'sections': [
            _html(_intro(
                'Panorama Survey — District Overview', focal_year, focal_window,
                'Responses are grouped by survey section, category, grade level, and '
                'student group. Use the filters to narrow to a school, grade, or '
                'student group.',
            ), h=3),
        ]},
        {'tab': 'Overview', 'label': 'Participation', 'sections': [
            _sec('students_by_window', 0, 6, 7),
            _sec('students_by_category', 6, 6, 7),
        ]},
        {'tab': 'Overview', 'label': 'Category results', 'sections': [
            _sec('fav_by_category', 0, 6, 8),
            _sec('dist_by_category', 6, 6, 8),
        ]},
        {'tab': 'Overview', 'label': 'Survey sections', 'sections': [
            _sec('fav_by_survey_type', 0, 6, 7),
            _sec('dist_by_survey_type', 6, 6, 7),
        ]},
        {'tab': 'By Grade Level', 'label': 'Favorability by grade', 'sections': [
            _sec('fav_by_grade', 0, 6, 9),
            _sec('students_by_grade', 6, 6, 9),
        ]},
        {'tab': 'By Grade Level', 'label': 'Response distribution by grade', 'sections': [
            _sec('dist_by_grade', 0, 12, 9),
        ]},
        {'tab': 'By Grade Level', 'label': 'Category results by grade level', 'sections': [
            _sec('fav_by_category_grade', 0, 12, 10),
        ]},
        {'tab': 'Student Groups', 'label': 'ELL status', 'sections': [
            _sec('fav_by_ell', 0, 6, 7),
            _sec('dist_by_ell', 6, 6, 7),
        ]},
        {'tab': 'Student Groups', 'label': 'Race and ethnicity', 'sections': [
            _sec('fav_by_race', 0, 6, 8),
            _sec('dist_by_race', 6, 6, 8),
        ]},
        {'tab': 'Student Groups', 'label': 'Gender', 'sections': [
            _sec('fav_by_gender', 0, 6, 6),
            _sec('dist_by_gender', 6, 6, 6),
        ]},
        {'tab': 'Student Groups', 'label': 'Suspension status', 'sections': [
            _sec('fav_by_suspension', 0, 6, 6),
            _sec('dist_by_suspension', 6, 6, 6),
        ]},
        {'tab': 'Student Groups', 'label': 'Category results by ELL status', 'sections': [
            _sec('fav_by_category_ell', 0, 12, 11),
        ]},
        {'tab': 'Survey Windows', 'label': '', 'sections': [
            _html(
                '<div data-pb-p="1" style="padding:2px 4px 8px">'
                '<p style="margin:0;color:#475569">This tab compares every available '
                'survey administration (time of year + school year). Each bar is a '
                'specific administration, so a Fall response is never mixed with a '
                'Spring response from another year. The latest district-wide '
                'administration is highlighted in blue; earlier administrations stay '
                'in context in grey.</p></div>',
                h=3,
            ),
        ]},
        {'tab': 'Survey Windows', 'label': 'Participation and overall trend', 'sections': [
            _sec('students_by_window', 0, 6, 7),
            _sec('fav_by_window', 6, 6, 7),
        ]},
        {'tab': 'Survey Windows', 'label': 'Category results by survey window', 'sections': [
            _sec('fav_by_category_window', 0, 12, 10),
        ]},
        {'tab': 'Survey Windows', 'label': 'Response mix by survey window', 'sections': [
            _sec('dist_by_window', 0, 12, 8),
        ]},
    ]
    return {
        'title': 'Panorama Survey Overview',
        'slug': 'panorama-survey',
        'description': (
            'District-wide Panorama survey results by survey section, category, '
            'grade level, student group, and survey administration.'
        ),
        'folder': PAGE_FOLDER,
        'focal_year': focal_year,
        'focal_window': focal_window,
        'page_filters': page_filters(
            focal_year, focal_window=focal_window,
            include_survey_type_filter=True,
        ),
        'tabs': tabs,
        'rows': rows,
        'menu_order': 11,
    }


def survey_page(slug, title, description, focal_year, focal_window, hidden_types,
                question_categories, include_equity_windows=False, intro_note=''):
    tabs = ['Overview', 'By Grade Level', 'Student Groups',
            'Questions by Category', 'Survey Windows']
    intro_body = (
        'The <em>Questions by Category</em> tab breaks each category down to '
        'its individual questions; click a bar or segment to open the '
        'underlying responses.'
    )
    if intro_note:
        intro_body += ' ' + intro_note
    rows = [
        {'tab': 'Overview', 'label': '', 'sections': [
            _html(_intro(title, focal_year, focal_window, intro_body), h=3),
        ]},
        {'tab': 'Overview', 'label': 'Participation', 'sections': [
            _sec('students_by_window', 0, 6, 7),
            _sec('students_by_category', 6, 6, 7),
        ]},
        {'tab': 'Overview', 'label': 'Category results', 'sections': [
            _sec('fav_by_category', 0, 6, 8),
            _sec('dist_by_category', 6, 6, 8),
        ]},
        {'tab': 'Overview', 'label': 'Survey sections', 'sections': [
            _sec('fav_by_survey_type', 0, 6, 7),
            _sec('dist_by_survey_type', 6, 6, 7),
        ]},
        {'tab': 'By Grade Level', 'label': 'Favorability by grade', 'sections': [
            _sec('fav_by_grade', 0, 6, 9),
            _sec('students_by_grade', 6, 6, 9),
        ]},
        {'tab': 'By Grade Level', 'label': 'Response distribution by grade', 'sections': [
            _sec('dist_by_grade', 0, 12, 9),
        ]},
        {'tab': 'By Grade Level', 'label': 'Category results by grade level', 'sections': [
            _sec('fav_by_category_grade', 0, 12, 10),
        ]},
        {'tab': 'Student Groups', 'label': 'ELL status', 'sections': [
            _sec('fav_by_ell', 0, 6, 7),
            _sec('dist_by_ell', 6, 6, 7),
        ]},
        {'tab': 'Student Groups', 'label': 'Race and ethnicity', 'sections': [
            _sec('fav_by_race', 0, 6, 8),
            _sec('dist_by_race', 6, 6, 8),
        ]},
        {'tab': 'Student Groups', 'label': 'Gender', 'sections': [
            _sec('fav_by_gender', 0, 6, 6),
            _sec('dist_by_gender', 6, 6, 6),
        ]},
        {'tab': 'Student Groups', 'label': 'Suspension status', 'sections': [
            _sec('fav_by_suspension', 0, 6, 6),
            _sec('dist_by_suspension', 6, 6, 6),
        ]},
        {'tab': 'Student Groups', 'label': 'Category results by ELL status', 'sections': [
            _sec('fav_by_category_ell', 0, 12, 11),
        ]},
    ]
    for cat in question_categories:
        slug_cat = (
            cat.lower().replace(' & ', '-').replace(' ', '-').replace('/', '-')
        )
        row_h = 6 if cat == 'Additional Questions' else 10
        rows.append({
            'tab': 'Questions by Category', 'label': cat,
            'sections': [
                _sec(f'dist_question_{slug_cat}', 0, 7, row_h),
                _sec(f'fav_question_{slug_cat}', 7, 5, row_h),
            ],
        })
    rows.append({'tab': 'Survey Windows', 'label': '', 'sections': [
        _html(
            '<div data-pb-p="1" style="padding:2px 4px 8px">'
            '<p style="margin:0;color:#475569">Compare the time of year and school '
            'year without combining them: every bar is one survey administration. '
            'The latest district-wide administration is highlighted in blue; '
            'earlier administrations stay in context in grey.</p></div>',
            h=3,
        ),
    ]})
    if include_equity_windows:
        rows.append({'tab': 'Survey Windows', 'label': 'Equity survey across administrations', 'sections': [
            _sec('equity_fav_by_category_window', 0, 12, 10),
            _sec('equity_dist_by_window', 0, 12, 8),
        ]})
    rows.extend([
        {'tab': 'Survey Windows', 'label': 'Participation and overall trend', 'sections': [
            _sec('students_by_window', 0, 6, 7),
            _sec('fav_by_window', 6, 6, 7),
        ]},
        {'tab': 'Survey Windows', 'label': 'Category results by survey window', 'sections': [
            _sec('fav_by_category_window', 0, 12, 10),
        ]},
        {'tab': 'Survey Windows', 'label': 'Response mix by survey window', 'sections': [
            _sec('dist_by_window', 0, 12, 8),
        ]},
    ])
    return {
        'title': title,
        'slug': slug,
        'description': description,
        'folder': PAGE_FOLDER,
        'focal_year': focal_year,
        'focal_window': focal_window,
        'page_filters': page_filters(
            focal_year, focal_window=focal_window,
            hidden_survey_types=hidden_types,
        ),
        'tabs': tabs,
        'rows': rows,
        'menu_order': 0,
    }


# Persistence -----------------------------------------------------------------
def upsert_dataset(owner):
    from dataviz.models import Dataset, DataVizDatasetFolder

    dataset = Dataset.objects.filter(name=DATASET_NAME).order_by('id').first()
    if dataset is None:
        dataset = Dataset(name=DATASET_NAME, owner=owner)
    else:
        dataset.owner = owner
    dataset.folder = DATASET_FOLDER
    dataset.source_type = Dataset.SOURCE_SQL
    dataset.source_sql = PANORAMA_SQL
    dataset.base_table = PANORAMA_TABLE
    dataset.distinct = False
    dataset.columns = [
        'ssid', 'student_name', 'school', 'category', 'question', 'response',
        'response_band', 'favorable_pct', 'school_year', 'survey_window',
        'window_label', 'grade', 'grade_label', 'grade_band', 'ell', 'race',
        'gender', 'suspension_group', 'status', 'survey_type',
    ]
    dataset.column_mappings = {c: c for c in dataset.columns}
    dataset.joins = []
    dataset.filters = []
    dataset.status_filter = []
    dataset.order_by = []
    dataset.description = (
        'One row per Panorama survey answer, enriched with student grade, grade '
        'band, ELL status, race, and gender. Includes a 5-band favorable scale '
        'and a 0-100 favorability measure for drillable survey charts.'
    )
    dataset.save()
    for depth in range(1, len(DATASET_FOLDER.split(' / ')) + 1):
        DataVizDatasetFolder.objects.get_or_create(
            path=' / '.join(DATASET_FOLDER.split(' / ')[:depth])
        )
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def upsert_charts(specs, dataset, owner):
    from dataviz.models import SavedChart

    saved = {}
    for spec in specs:
        chart = SavedChart.objects.filter(name=spec['name']).order_by('id').first()
        if chart is None:
            chart = SavedChart(name=spec['name'])
        chart.owner = owner
        chart.chart_type = spec['chart_type']
        chart.config = spec['config']
        chart.source_type = SavedChart.SOURCE_DATASET
        chart.source_id = dataset.pk
        chart.source_sql = ''
        chart.source_filters = spec.get('source_filters', {})
        chart.column_defs = spec.get('column_defs', {})
        chart.school_filter_mode = 'enrolled'
        chart.drill_through_columns = list(spec.get('drill_columns') or DRILL_COLUMNS)
        chart.drill_through_filter_map = spec.get('drill_through_filter_map') or {}
        chart.description = spec['config'].get('chart_title', '')
        chart.save()
        saved[spec['key']] = chart
        print(f'  chart ready:   {chart.name} (#{chart.pk})')
    return saved


def prune_stale_charts(keep_names):
    from dataviz.models import SavedChart
    stale = SavedChart.objects.filter(name__startswith=CHART_PREFIX).exclude(
        name__in=list(keep_names)
    )
    count = stale.count()
    if count:
        stale.delete()
        print(f'  removed {count} stale Panorama chart(s)')


def ensure_page_folder():
    from dataviz.models import DataVizPageFolder
    parts = [p.strip() for p in PAGE_FOLDER.split(' / ') if p.strip()]
    for depth in range(1, len(parts) + 1):
        DataVizPageFolder.objects.get_or_create(path=' / '.join(parts[:depth]))


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
            'folder': spec['folder'],
            'container_width': DataVizPage.CONTAINER_WIDE,
            'show_header': True,
            'owner': owner,
            'page_filters': spec['page_filters'],
            'status': DataVizPage.STATUS_ACTIVE,
        },
    )
    with transaction.atomic():
        page.rows.all().delete()
        page.tabs.all().delete()
        tab_map = {}
        for idx, tab_name in enumerate(spec['tabs']):
            tab_map[tab_name] = DataVizPageTab.objects.create(
                page=page, name=tab_name, position=idx,
            )
        for r_idx, row_spec in enumerate(spec['rows']):
            row = DataVizPageRow.objects.create(
                page=page, label=row_spec.get('label', ''),
                position=r_idx, tab=tab_map.get(row_spec.get('tab')),
            )
            for s_idx, sec in enumerate(row_spec['sections']):
                if 'html' in sec:
                    DataVizPageSection.objects.create(
                        page=page, row=row,
                        element_type=DataVizPageSection.TYPE_HTML,
                        html_content=sec['html'],
                        show_title=False,
                        gs_x=sec.get('gs_x', 0), gs_y=0,
                        gs_w=sec.get('gs_w', 12), gs_h=sec.get('gs_h', 3),
                        position=s_idx,
                    )
                else:
                    DataVizPageSection.objects.create(
                        page=page, row=row,
                        element_type=DataVizPageSection.TYPE_CHART,
                        chart=charts[sec['chart']],
                        show_title=False,
                        gs_x=sec.get('gs_x', 0), gs_y=0,
                        gs_w=sec.get('gs_w', 6), gs_h=sec.get('gs_h', 7),
                        position=s_idx,
                    )
    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def attach_to_menu(page, order):
    from users.models_pages import Page

    assessments, _ = Page.objects.update_or_create(
        slug='assessments',
        defaults={
            'name': 'Assessments',
            'app_reference': 'assessments',
            'description': 'State and local assessment dashboards',
            'url_path': '', 'status': 'active', 'is_menu_category': True,
            'show_in_menu': True, 'menu_order': 54,
            'required_roles': [], 'is_public': False,
        },
    )
    # Keep the four Panorama pages together as a submenu inside Assessments.
    panorama, _ = Page.objects.update_or_create(
        slug='panorama-survey-menu',
        defaults={
            'name': 'Panorama Survey',
            'app_reference': 'assessments',
            'description': 'Panorama survey responses and analytics',
            'url_path': '', 'status': 'active', 'is_menu_category': True,
            'show_in_menu': True, 'menu_order': 11,
            'parent_page': assessments,
            'required_roles': [], 'is_public': False,
        },
    )
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = panorama
        site.menu_order = order
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print(f'  menu: Assessments -> Panorama Survey -> {page.title} (order {order})')


# QA --------------------------------------------------------------------------
def qa_dataviz(charts):
    from dataviz.utils.data_loader import run_raw_sql

    failures = []

    def fail(msg):
        failures.append(msg)
        print(f'  FAIL {msg}')

    print('=== QA: dataset ===')
    res = run_raw_sql(f"""
        SELECT school_year, survey_window,
               count() AS n_answers,
               uniqExact(ssid) AS n_respondents,
               uniqExact(category) AS n_categories,
               countIf(survey_type = '') AS missing_survey_type,
               countIf(response_band = '') AS missing_band
        FROM ({PANORAMA_SQL})
        GROUP BY school_year, survey_window
        ORDER BY school_year, survey_window
    """)
    if res.get('error'):
        fail(f'dataset SQL: {res["error"]}')
    else:
        for row in res.get('rows') or []:
            print(
                f'  ok  {row["survey_window"]} {row["school_year"]}: '
                f'{row["n_answers"]} answers / {row["n_respondents"]} students / '
                f'{row["n_categories"]} categories'
            )
            if row['missing_survey_type']:
                fail(f'{row["school_year"]} {row["survey_window"]}: rows without survey_type')
            if row['missing_band']:
                fail(f'{row["school_year"]} {row["survey_window"]}: rows without response_band')

    print('=== QA: chart columns ===')
    available = set(COLUMN_DEFS)
    for spec in charts:
        refs = set(spec.get('drill_columns') or [])
        for key in ('x_col', 'x_cols', 'y_col', 'stack_col', 'series_col', 'group_col'):
            val = spec['config'].get(key)
            if isinstance(val, str) and val.strip():
                refs.update(p.strip() for p in val.split(',') if p.strip())
            elif isinstance(val, list):
                refs.update(str(v).strip() for v in val if str(v).strip())
        missing = sorted(refs - available)
        if missing:
            fail(f'{spec["name"]}: unknown columns {missing}')

    if failures:
        print(f'QA completed with {len(failures)} failure(s).')
        return 1
    print('QA passed.')
    return 0


# Main ------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Create Panorama survey DataViz content.')
    parser.add_argument('--owner', default='', help='Owner username or email.')
    parser.add_argument('--qa', action='store_true', help='Run QA after seeding.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()
    dataset = upsert_dataset(owner)

    windows = survey_window_order()
    if not windows:
        raise SystemExit('No Panorama survey data found.')
    print(f'  survey windows: {", ".join(windows)}')

    latest_year = latest_school_year() or pick_focal_year()
    skills_year = pick_focal_year(SKILLS_CATEGORIES)
    supports_year = pick_focal_year(SUPPORTS_CATEGORIES)
    equity_year = pick_focal_year(EQUITY_CATEGORIES)
    overview_window = pick_focal_window(latest_year)
    skills_window = pick_focal_window(skills_year, SKILLS_CATEGORIES)
    supports_window = pick_focal_window(supports_year, SUPPORTS_CATEGORIES)
    equity_window = pick_focal_window(equity_year, EQUITY_CATEGORIES)
    print(
        '  focal administrations: '
        f'overview={overview_window} {latest_year}, '
        f'skills={skills_window} {skills_year}, '
        f'supports={supports_window} {supports_year}, '
        f'equity={equity_window} {equity_year}'
    )

    shared, current_win, prior_wins = build_shared_charts(windows)
    specs = list(shared)

    question_years = {}
    question_years.update({c: skills_year for c in SKILLS_CATEGORIES})
    question_years.update({c: supports_year for c in SUPPORTS_CATEGORIES})
    question_years.update({c: equity_year for c in ['Cultural Awareness and Action']})
    for cat in SKILLS_CATEGORIES + SUPPORTS_CATEGORIES + ['Cultural Awareness and Action']:
        specs.extend(build_category_question_charts(cat, question_years[cat]))

    specs.extend(build_equity_comparison_charts(windows))

    # De-duplicate by key while keeping the first definition.
    seen = set()
    unique_specs = []
    for spec in specs:
        if spec['key'] in seen:
            continue
        seen.add(spec['key'])
        unique_specs.append(spec)
    specs = unique_specs

    charts = upsert_charts(specs, dataset, owner)
    prune_stale_charts({s['name'] for s in specs})
    ensure_page_folder()

    pages = [
        overview_page(latest_year, overview_window),
        survey_page(
            'panorama-skills-competencies',
            'Panorama — Skills & Competencies',
            'Panorama SEL skills and competencies by category, grade level, '
            'student group, and individual question.',
            skills_year, skills_window,
            ['Skills & Competencies'],
            SKILLS_CATEGORIES,
            intro_note=(
                'The Skills & Competencies section was last administered in '
                'Spring 2025-2026; the 2026-2027 Equity Survey does not include '
                'these categories.'
            ),
        ),
        survey_page(
            'panorama-supports-environment',
            'Panorama — Supports & Environment',
            'Panorama school climate, safety, belonging, teacher-student '
            'relationships, and facilities questions by category, grade level, '
            'student group, and individual question.',
            supports_year, supports_window,
            ['Supports & Environment', 'Equity Survey'],
            SUPPORTS_CATEGORIES,
            intro_note=(
                'Spring 2025-2026 contains the full supports section. The '
                '2026-2027 Equity Survey contains School Climate and Sense of '
                'Belonging only, so switching the year may show fewer categories.'
            ),
        ),
        survey_page(
            'panorama-equity-inclusion',
            'Panorama — Equity & Inclusion',
            'Panorama Equity Survey results: Cultural Awareness and Action, '
            'School Climate, and Sense of Belonging by category, grade level, '
            'student group, and individual question.',
            equity_year, equity_window,
            ['Equity & Inclusion', 'Equity Survey'],
            ['Cultural Awareness and Action'],
            include_equity_windows=True,
            intro_note=(
                'The Equity Survey was first administered in Fall 2026-2027; '
                'earlier school years have no equity responses.'
            ),
        ),
    ]

    for order, spec in enumerate(pages, start=1):
        spec['menu_order'] = order
        page = upsert_page(spec, charts, owner)
        attach_to_menu(page, order)

    print()
    print(f'Done. {len(specs)} charts and {len(pages)} pages.')
    print(f'  Latest survey window: {current_win}')
    if prior_wins:
        print(f'  Prior windows: {", ".join(prior_wins)}')
    print('  Pages:')
    for spec in pages:
        print(f'    /dataviz/pages/{spec["slug"]}/  ({spec["focal_year"]})')

    if args.qa:
        raise SystemExit(qa_dataviz(specs))


if __name__ == '__main__':
    main()
