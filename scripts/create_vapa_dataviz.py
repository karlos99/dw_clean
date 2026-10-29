#!/usr/bin/env python
"""Create the VAPA DataViz datasets, charts, and page.

Does not edit charts that already exist. It only refreshes records whose
names start with "VAPA •" and the page slug ``vapa-programs``.

The datasets are read-only SQL over current VAPA course tags, so the page
follows tag and enrollment changes without a ClickHouse rebuild. Run inside
the Django container:

    docker compose exec django python /app/scripts/create_vapa_dataviz.py

If ``./scripts`` is not mounted in the container:

    docker cp scripts/create_vapa_dataviz.py dw_django:/tmp/
    docker compose exec django python /tmp/create_vapa_dataviz.py

Optional: ``--owner you@example.com`` (default: first superuser).
Re-running refreshes these VAPA records only.
"""
import argparse
import os
import sys
from pathlib import Path


def bootstrap_django() -> str:
    project_dir = os.environ.get("DJANGO_PROJECT_DIR", "").strip()
    if not project_dir or not (Path(project_dir) / "manage.py").exists():
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
    import django
    django.setup()
    return project_dir


# Active and Completed enrollments in a VAPA-tagged course, with MS/HS from grade.
_ENROLLMENT_CTE = """
WITH vapa_courses AS MATERIALIZED (
    SELECT
        s.ssid,
        s.school_year,
        s.stu_annual_key,
        NULLIF(BTRIM(d.name), '') AS discipline,
        COALESCE(
            NULLIF(BTRIM(s.course_name), ''),
            NULLIF(BTRIM(s.course_code), ''),
            'Unknown course'
        ) AS course_name,
        COALESCE(NULLIF(BTRIM(s.course_code), ''), '') AS course_code
    FROM schedule_list s
    JOIN courses_catalog cc
        ON cc.course_key = NULLIF(BTRIM(s.course_key), '')
    JOIN coursecatalog_catalogplacement pl
        ON pl.catalog_entry_id = cc.course_key
    JOIN coursecatalog_catalogtag tg
        ON tg.id = pl.tag_id AND tg.name = 'VAPA'
    LEFT JOIN coursecatalog_catalogdiscipline d
        ON d.id = pl.discipline_id
    WHERE s.schedule_status IN ('Active', 'Completed')
      AND s.ssid IS NOT NULL AND BTRIM(s.ssid) <> ''
      AND s.school_year IS NOT NULL AND BTRIM(s.school_year) <> ''
),
graded AS MATERIALIZED (
    SELECT DISTINCT
        v.ssid,
        COALESCE(cs.student_name, '') AS student_name,
        COALESCE(cs.grade, '') AS grade,
        COALESCE(cs.school, '') AS school,
        v.school_year,
        v.discipline,
        v.course_name,
        v.course_code,
        CASE
            WHEN cs.grade IN ('06', '07', '08', '6', '7', '8') THEN 'MS'
            WHEN cs.grade IN ('09', '10', '11', '12', '9', '10', '11', '12') THEN 'HS'
        END AS level,
        CASE
            WHEN cs.grade IN ('08', '8') THEN '08'
            WHEN cs.grade IN ('09', '9') THEN '09'
            ELSE ''
        END AS edge_grade
    FROM vapa_courses v
    JOIN current_student cs
        ON cs.stu_annual_key = v.stu_annual_key
       AND cs.school_year = v.school_year
)
"""


def _year_label(col='school_year'):
    return (
        f"substring({col} from 3 for 2) || '-' || substring({col} from 8 for 2)"
    )
def _sql(body: str, extra_ctes: str = '') -> str:
    sql = _ENROLLMENT_CTE.rstrip()
    if extra_ctes.strip():
        sql += ",\n" + extra_ctes.strip()
    return sql + "\n" + body.strip() + "\n"


_STUDENT_DRILL = ['ssid', 'student_name', 'grade', 'school']


def _years_bucket(years_col):
    return f"""
CASE
    WHEN {years_col} >= 4 THEN '4+ Years'
    WHEN {years_col} = 3 THEN '3 Years'
    WHEN {years_col} = 2 THEN '2 Years'
    ELSE '1 Year'
END
"""


DATASET_SPECS = [
    {
        'name': 'VAPA • Enrollment by Level',
        'description': 'One row per student course in a VAPA class, labeled middle school (grades 6–8) or high school (grades 9–12).',
        'columns': _STUDENT_DRILL + ['school_year', 'level_group', 'discipline', 'course_name', 'course_code', 'ssid'],
        'sql': _sql(f"""
            SELECT DISTINCT
                ssid,
                student_name,
                grade,
                school,
                {_year_label('school_year')} AS school_year,
                CASE level
                    WHEN 'MS' THEN 'MS Students (6-8)'
                    ELSE 'HS Students (9-12)'
                END AS level_group,
                COALESCE(discipline, 'No discipline') AS discipline,
                course_name,
                course_code
            FROM graded
            WHERE level IS NOT NULL
            ORDER BY student_name, ssid, school_year, course_name
        """),
    },
    {
        'name': 'VAPA • MS Discipline Enrollment',
        'description': 'Middle-school VAPA course rows, one per student and course, with the discipline.',
        'columns': _STUDENT_DRILL + ['school_year', 'discipline', 'course_name', 'course_code'],
        'sql': _sql(f"""
            SELECT DISTINCT
                ssid, student_name, grade, school,
                {_year_label('school_year')} AS school_year,
                discipline, course_name, course_code
            FROM graded
            WHERE level = 'MS' AND discipline IS NOT NULL
            ORDER BY student_name, ssid, school_year, course_name
        """),
    },
    {
        'name': 'VAPA • HS Discipline Enrollment',
        'description': 'High-school VAPA course rows, one per student and course, with the discipline.',
        'columns': _STUDENT_DRILL + ['school_year', 'discipline', 'course_name', 'course_code'],
        'sql': _sql(f"""
            SELECT DISTINCT
                ssid, student_name, grade, school,
                {_year_label('school_year')} AS school_year,
                discipline, course_name, course_code
            FROM graded
            WHERE level = 'HS' AND discipline IS NOT NULL
            ORDER BY student_name, ssid, school_year, course_name
        """),
    },
    {
        'name': 'VAPA • Sustained Enrollment by Level',
        'description': 'Each VAPA course a student took, tagged with how many school years they stayed in VAPA at that level.',
        'columns': _STUDENT_DRILL + ['school_year', 'level', 'years_enrolled', 'discipline', 'course_name', 'course_code'],
        'sql': _sql(f"""
            SELECT DISTINCT
                g.ssid, g.student_name, g.grade, g.school,
                {_year_label('g.school_year')} AS school_year,
                g.level,
                {_years_bucket('span.n_years')} AS years_enrolled,
                COALESCE(g.discipline, 'No discipline') AS discipline,
                g.course_name,
                g.course_code
            FROM graded g
            JOIN (
                SELECT ssid, level, count(DISTINCT school_year) AS n_years
                FROM graded
                WHERE level IS NOT NULL
                GROUP BY ssid, level
            ) span ON span.ssid = g.ssid AND span.level = g.level
            WHERE g.level IS NOT NULL
            ORDER BY student_name, ssid, school_year, course_name
        """),
    },
    {
        'name': 'VAPA • Sustained Enrollment by Discipline',
        'description': 'Each course in a VAPA discipline, tagged with how many school years the student stayed in that discipline.',
        'columns': _STUDENT_DRILL + ['school_year', 'discipline', 'years_enrolled', 'course_name', 'course_code'],
        'sql': _sql(f"""
            SELECT DISTINCT
                g.ssid, g.student_name, g.grade, g.school,
                {_year_label('g.school_year')} AS school_year,
                g.discipline,
                {_years_bucket('span.n_years')} AS years_enrolled,
                g.course_name,
                g.course_code
            FROM graded g
            JOIN (
                SELECT ssid, discipline, count(DISTINCT school_year) AS n_years
                FROM graded
                WHERE level IS NOT NULL AND discipline IS NOT NULL
                GROUP BY ssid, discipline
            ) span ON span.ssid = g.ssid AND span.discipline = g.discipline
            WHERE g.level IS NOT NULL AND g.discipline IS NOT NULL
            ORDER BY student_name, ssid, school_year, course_name
        """),
    },
    {
        'name': 'VAPA • MS to HS Transition',
        'description': 'Grade 8 students in a VAPA discipline, the courses that placed them there, and the grade 9 courses in that discipline the next year.',
        'columns': [
            'ssid', 'student_name', 'school', 'school_year_transition', 'discipline',
            'grade_8_courses', 'continued', 'grade_9_courses', 'continued_pct',
        ],
        'sql': _sql("""
            SELECT
                g8.ssid,
                g8.student_name,
                g8.school,
                substring(g8.school_year from 3 for 2) || '-' || substring(g8.school_year from 8 for 2)
                    || ' to ' ||
                substring(g8.next_year from 3 for 2) || '-' || substring(g8.next_year from 8 for 2)
                    AS school_year_transition,
                g8.discipline,
                g8.grade_8_courses,
                CASE WHEN g9.ssid IS NOT NULL THEN 'Yes' ELSE 'No' END AS continued,
                COALESCE(g9.grade_9_courses, '') AS grade_9_courses,
                CASE WHEN g9.ssid IS NOT NULL THEN 100 ELSE 0 END AS continued_pct
            FROM g8
            JOIN known_years y ON y.school_year = g8.next_year
            LEFT JOIN g9
                ON g9.ssid = g8.ssid
               AND g9.discipline = g8.discipline
               AND g9.school_year = g8.next_year
            ORDER BY g8.student_name, g8.ssid, g8.school_year, g8.discipline
        """, extra_ctes="""
            g8 AS MATERIALIZED (
                SELECT
                    ssid,
                    max(student_name) AS student_name,
                    max(school) AS school,
                    school_year,
                    discipline,
                    (substring(school_year from 1 for 4)::int + 1)::text || '-' ||
                    (substring(school_year from 1 for 4)::int + 2)::text AS next_year,
                    string_agg(DISTINCT course_name, ', ' ORDER BY course_name) AS grade_8_courses
                FROM graded
                WHERE edge_grade = '08' AND level = 'MS' AND discipline IS NOT NULL
                GROUP BY ssid, school_year, discipline
            ),
            g9 AS MATERIALIZED (
                SELECT
                    ssid,
                    school_year,
                    discipline,
                    string_agg(DISTINCT course_name, ', ' ORDER BY course_name) AS grade_9_courses
                FROM graded
                WHERE edge_grade = '09' AND level = 'HS' AND discipline IS NOT NULL
                GROUP BY ssid, school_year, discipline
            ),
            known_years AS MATERIALIZED (
                SELECT DISTINCT school_year FROM graded
            )
        """),
    },
]

DISCIPLINE_COLORS = {
    'Music': '#2a9d8f',
    'Visual Art': '#5c6bc0',
    'Dance': '#e9c46a',
    'Media': '#e76f51',
    'Theater': '#c73e8a',
}
# Painter draws the first series at the bottom / first in the legend, and the
# chart builder reverses stack_order, so list the preferred legend last-to-first.
DISCIPLINE_STACK = ['Theater', 'Media', 'Dance', 'Visual Art', 'Music']
YEAR_COLORS = {
    '1 Year': '#2a9d8f',
    '2 Years': '#4c78a8',
    '3 Years': '#f4a261',
    '4+ Years': '#e9c46a',
}
YEAR_STACK = ['4+ Years', '3 Years', '2 Years', '1 Year']
LEVEL_COLORS = {
    'MS Students (6-8)': '#e9c46a',
    'HS Students (9-12)': '#1d3557',
}
LEVEL_STACK = ['HS Students (9-12)', 'MS Students (6-8)']


def _base_cfg(chart_type, title, **overrides):
    cfg = {
        'chart_type': chart_type,
        'chart_title': title,
        'agg': 'count_unique',
        'x_col': '',
        'y_col': 'ssid',
        'stack_col': '',
        'group_col': '',
        'series_col': '',
        'sort_col': '',
        'sort_dir': 'asc',
        'bar_layout': 'stacked' if chart_type == 'stacked_bar' else 'grouped',
        'bar_orientation': 'vertical',
        'bar_radial': False,
        'background': 'white',
        'legend_pos': 'top',
        'legend_left': 'center',
        'legend_top': 36,
        'grid_top': 108,
        'grid_bottom': 36,
        'data_labels': False,
        'show_grid': True,
        'show_zoom': False,
        'show_tooltip': True,
        'show_points': True,
        'animation': True,
        'palette': '',
        'color_map': {},
        'label_position': 'top',
        'x_label_rotation': 0,
        'x_label_font_size': 11,
        'title_font_size': 16,
        'y_axis_min': 0,
    }
    cfg.update(overrides)
    return cfg


CHART_SPECS = [
    {
        'name': 'VAPA • MS to HS Transition Rate',
        'chart_type': 'line',
        'dataset': 'VAPA • MS to HS Transition',
        'drill_columns': [
            'ssid', 'student_name', 'school', 'school_year_transition', 'discipline',
            'grade_8_courses', 'continued', 'grade_9_courses',
        ],
        'column_defs': {
            'school_year_transition': 'School Year Transition',
            'discipline': 'Discipline',
            'continued_pct': 'Transition Rate (%)',
        },
        'config': _base_cfg(
            'line', 'MS → HS Transition Rate by Discipline by Year',
            x_col='school_year_transition', y_col='continued_pct',
            stack_col='discipline', series_col='discipline',
            stack_order=DISCIPLINE_STACK, color_map=DISCIPLINE_COLORS,
            agg='avg', agg_decimals=1, y_axis_max=100,
        ),
    },
    {
        'name': 'VAPA • Years of Sustained Enrollment by Level',
        'chart_type': 'stacked_bar',
        'dataset': 'VAPA • Sustained Enrollment by Level',
        'drill_columns': _STUDENT_DRILL + [
            'school_year', 'level', 'years_enrolled', 'discipline', 'course_name', 'course_code',
        ],
        'column_defs': {
            'level': 'Level',
            'years_enrolled': 'Years Enrolled',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'Years of Sustained Enrollment by Level',
            x_col='level', y_col='ssid', stack_col='years_enrolled',
            series_col='years_enrolled',
            sort_col='__manual__', category_order=['MS', 'HS'],
            stack_order=YEAR_STACK, color_map=YEAR_COLORS,
            show_percent=True, percent_decimals=0, y_axis_max=100,
        ),
    },
    {
        'name': 'VAPA • Years of Sustained Enrollment by Discipline',
        'chart_type': 'stacked_bar',
        'dataset': 'VAPA • Sustained Enrollment by Discipline',
        'drill_columns': _STUDENT_DRILL + [
            'school_year', 'discipline', 'years_enrolled', 'course_name', 'course_code',
        ],
        'column_defs': {
            'discipline': 'Discipline',
            'years_enrolled': 'Years Enrolled',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'Years of Sustained Enrollment by Discipline',
            x_col='discipline', y_col='ssid', stack_col='years_enrolled',
            series_col='years_enrolled',
            bar_orientation='horizontal',
            sort_col='__manual__',
            category_order=['Theater', 'Media', 'Dance', 'Visual Art', 'Music'],
            stack_order=YEAR_STACK, color_map=YEAR_COLORS,
            show_percent=True, percent_decimals=0, y_axis_max=100,
            grid_left=80,
        ),
    },
    {
        'name': 'VAPA • Total Enrollment Trends',
        'chart_type': 'area',
        'dataset': 'VAPA • Enrollment by Level',
        'drill_columns': _STUDENT_DRILL + [
            'school_year', 'level_group', 'discipline', 'course_name', 'course_code',
        ],
        'column_defs': {
            'school_year': 'School Year',
            'level_group': 'Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'area', 'Total Enrollment Trends Over Time',
            x_col='school_year', y_col='ssid',
            stack_col='level_group', series_col='level_group',
            stack_order=LEVEL_STACK, color_map=LEVEL_COLORS,
            line_layout='overlay',
        ),
    },
    {
        'name': 'VAPA • MS Discipline Enrollment',
        'chart_type': 'line',
        'dataset': 'VAPA • MS Discipline Enrollment',
        'drill_columns': _STUDENT_DRILL + [
            'school_year', 'discipline', 'course_name', 'course_code',
        ],
        'column_defs': {
            'school_year': 'School Year',
            'discipline': 'Discipline',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'line', 'MS Discipline Enrollment by Year',
            x_col='school_year', y_col='ssid',
            stack_col='discipline', series_col='discipline',
            stack_order=DISCIPLINE_STACK, color_map=DISCIPLINE_COLORS,
        ),
    },
    {
        'name': 'VAPA • HS Discipline Enrollment',
        'chart_type': 'line',
        'dataset': 'VAPA • HS Discipline Enrollment',
        'drill_columns': _STUDENT_DRILL + [
            'school_year', 'discipline', 'course_name', 'course_code',
        ],
        'column_defs': {
            'school_year': 'School Year',
            'discipline': 'Discipline',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'line', 'HS Discipline Enrollment by Year',
            x_col='school_year', y_col='ssid',
            stack_col='discipline', series_col='discipline',
            stack_order=DISCIPLINE_STACK, color_map=DISCIPLINE_COLORS,
        ),
    },
]

PAGE_SPEC = {
    'title': 'VAPA Programs',
    'slug': 'vapa-programs',
    'description': (
        'Enrollment, middle-to-high-school continuation, and years in program '
        'for courses tagged VAPA. Middle school is grades 6–8 and high school '
        'is grades 9–12. Counts use Active and Completed enrollments. A course '
        'tagged VAPA with no discipline is included in the totals and left off '
        'the discipline charts. The transition rate is the share of grade 8 '
        'students in a discipline who take that same discipline in grade 9 '
        'the next year. Click a point to list the students and the courses '
        'that placed them there.'
    ),
    'tabs': ['Transition & Persistence', 'Enrollment'],
    'rows': [
        {'tab': 'Transition & Persistence', 'sections': [
            {'chart': 'VAPA • MS to HS Transition Rate', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Transition & Persistence', 'sections': [
            {'chart': 'VAPA • Years of Sustained Enrollment by Level', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': 'VAPA • Years of Sustained Enrollment by Discipline', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'Enrollment', 'sections': [
            {'chart': 'VAPA • Total Enrollment Trends', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Enrollment', 'sections': [
            {'chart': 'VAPA • MS Discipline Enrollment', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': 'VAPA • HS Discipline Enrollment', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
    ],
}


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
        raise SystemExit('No users exist to own the VAPA page.')
    return user


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset

    mappings = {c: c for c in spec['columns']}
    dataset, _ = Dataset.objects.update_or_create(
        name=spec['name'],
        owner=owner,
        defaults={
            'source_type': Dataset.SOURCE_SQL,
            'source_sql': spec['sql'],
            'base_table': 'courses_catalog',
            'distinct': False,
            'columns': spec['columns'],
            'column_mappings': mappings,
            'joins': [],
            'filters': [],
            'status_filter': [],
            'order_by': [],
            'description': spec['description'],
        },
    )
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    if not spec['name'].startswith('VAPA • '):
        raise SystemExit(f'Refusing to write a chart outside the VAPA set: {spec["name"]}')
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
            'source_filters': {},
            'column_defs': spec.get('column_defs', {}),
            'school_filter_mode': 'enrolled',
            'drill_through_columns': list(spec.get('drill_columns') or []),
            'drill_through_filter_map': {},
            'description': spec['config'].get('chart_title', ''),
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
            'folder': 'VAPA',
            'container_width': DataVizPage.CONTAINER_WIDE,
            'show_header': True,
            'owner': owner,
            'page_filters': [],
            'status': DataVizPage.STATUS_ACTIVE,
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
                    show_title=False,
                    gs_x=sec_spec.get('gs_x', 0),
                    gs_y=0,
                    gs_w=sec_spec.get('gs_w', 6),
                    gs_h=sec_spec.get('gs_h', 6),
                    position=s_idx,
                )
    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def attach_to_menu(page):
    from users.models_pages import Page

    category, _ = Page.objects.update_or_create(
        slug='vapa',
        defaults={
            'name': 'VAPA',
            'app_reference': 'vapa',
            'description': 'Visual and performing arts program charts',
            'url_path': '',
            'status': 'active',
            'is_menu_category': True,
            'show_in_menu': True,
            'menu_order': 55,
            'required_roles': [],
            'is_public': False,
        },
    )
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = 0
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print('  menu category "VAPA" updated.')


def main():
    parser = argparse.ArgumentParser(description='Create the VAPA DataViz page.')
    parser.add_argument('--owner', default='', help='Username or email of the owner.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in CHART_SPECS}
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_to_menu(page)
    print('VAPA page ready.')


if __name__ == '__main__':
    main()
