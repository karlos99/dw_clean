"""Seed the CCGI dashboards (datasets, saved charts, and DataVizPage records).

The CCGI source data lives in PostgreSQL (``ccgi_career_explo`` and
``ccgi_college_financial``) and is mirrored into ClickHouse as five flattened
analytics tables by ``sync_to_clickhouse``:

  * ``analytics_ccgi_favorite_career``  — one row per (student, favorite career)
  * ``analytics_ccgi_career_cluster``   — one row per (student, career cluster)
  * ``analytics_ccgi_college_financial``— one row per student (CSU/UC/CCC + aid)
  * ``analytics_ccgi_college_application`` — one row per (student, application)
  * ``analytics_ccgi_application_status``  — one row per (student, system, status)

This command wires those tables into the existing DataViz engine by creating
Datasets + SavedCharts and two publishable DataVizPage records:

  * CCGI Career Explorer    — favorite careers & career-cluster counts,
                              filterable by school and grade.
  * CCGI College & Financial — CSU / UC / CCC eligibility & applications plus
                              financial-aid application status, filterable by
                              school.

One dataset is created per analytics table.  Charts that need to show a single
college system (CSU / UC / CCC) narrow the shared applications/status dataset
with a chart-level ``source_filters`` entry instead of a dedicated dataset.

Every chart is drill-through enabled, so clicking a bar / slice opens the
student list for that category (joined to ``current_student`` for names,
grade, and school).  Drill columns come from ``DRILL_COLUMNS_BY_DATASET``
(keyed by dataset) and always lead with ``ssid`` then ``student_id``; the
internal ``stu_annual_key`` is never shown.

Usage:
    python manage.py seed_ccgi
    python manage.py seed_ccgi --owner admin@example.com
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dataviz.models import (
    Dataset,
    SavedChart,
    DataVizPage,
    DataVizPageRow,
    DataVizPageSection,
    DataVizPageTab,
)
from dataviz.utils.page_publish import publish_page

FAVORITE_CAREER_TABLE = 'analytics_ccgi_favorite_career'
CAREER_CLUSTER_TABLE = 'analytics_ccgi_career_cluster'
COLLEGE_FINANCIAL_TABLE = 'analytics_ccgi_college_financial'
COLLEGE_APPLICATION_TABLE = 'analytics_ccgi_college_application'
APPLICATION_STATUS_TABLE = 'analytics_ccgi_application_status'

CCGI_TABLES = (
    FAVORITE_CAREER_TABLE,
    CAREER_CLUSTER_TABLE,
    COLLEGE_FINANCIAL_TABLE,
    COLLEGE_APPLICATION_TABLE,
    APPLICATION_STATUS_TABLE,
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

COLLEGE_SYSTEMS = ('CSU', 'UC', 'CCC')

# One dataset per underlying analytics table.  Earlier versions also seeded a
# separate dataset per college system (CSU/UC/CCC) for the same two tables;
# those were redundant and have been collapsed into the shared
# 'CCGI College Applications' / 'CCGI Application Status' datasets.  Charts
# narrow to a single system with a chart-level ``source_filters`` entry.
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
]

# Datasets seeded by earlier versions that are now replaced by the shared
# datasets above.  Removed from the database on the next run.
OBSOLETE_DATASET_NAMES = [
    *[f'CCGI {system} Applications' for system in COLLEGE_SYSTEMS],
    *[f'CCGI {system} On-Track Status' for system in ('CSU', 'UC')],
]

# Drill-through columns per dataset.  ``ssid`` and ``student_id`` lead the
# table; the list otherwise mirrors the dataset columns but deliberately omits
# internal join keys — notably ``stu_annual_key`` — so the drill table never
# shows the annual key.  Deduplication still uses the key internally; it just
# isn't displayed.
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
}


def _base_cfg(chart_type, title, **overrides):
    """A minimal but complete chart config for the rendering engine."""
    cfg = {
        'chart_type': chart_type,
        'chart_title': title,
        'agg': 'count',
        'x_col': '',
        'y_col': '',
        'stack_col': '',
        'group_col': '',
        'series_col': '',
        'sort_col': '_value',
        'sort_dir': 'desc',
        # stacked_bar must actually stack. The engine's build_option only falls
        # back to a stacked layout when bar_layout is unset; an explicit
        # 'grouped' here would render the series side by side.
        'bar_layout': 'stacked' if chart_type == 'stacked_bar' else 'grouped',
        'bar_orientation': 'vertical',
        'bar_radial': False,
        'background': 'white',
        'legend_pos': 'bottom',
        'data_labels': True,
        'show_grid': True,
        'show_zoom': False,
        'show_tooltip': True,
        'animation': True,
        'palette': '',
        'color_map': {},
        'label_position': 'top',
        'x_label_rotation': 0,
        'x_label_font_size': 11,
    }
    cfg.update(overrides)
    return cfg


# Every chart's drill-through columns come from DRILL_COLUMNS_BY_DATASET (keyed
# by the chart's dataset), which excludes internal keys such as
# ``stu_annual_key``.  ``drill_through_filter_map`` stays the default empty map.
CHART_SPECS = [
    # ── Career Explorer ──────────────────────────────────────────────
    {
        'name': 'CCGI • Top Favorite Careers',
        'chart_type': 'bar',
        'dataset': 'CCGI Career Explorer',
        'config': _base_cfg(
            'bar', 'Top 20 Favorite Careers',
            x_col='career', agg='count', top_n=20,
        ),
    },
    {
        'name': 'CCGI • Students by Career Cluster',
        'chart_type': 'bar',
        'dataset': 'CCGI Career Clusters',
        'config': _base_cfg(
            'bar', 'Students Interested by Career Cluster',
            x_col='cluster', agg='count',
        ),
    },
    {
        'name': 'CCGI • Career Cluster Interest by Grade',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI Career Clusters',
        'config': _base_cfg(
            'stacked_bar', 'Career Cluster Interest Depth by Grade',
            x_col='cluster', stack_col='grade', series_col='grade',
            y_col='cluster_count', agg='sum', data_labels=False,
        ),
    },
    # ── College & Financial ──────────────────────────────────────────
    {
        'name': 'CCGI • CSU Eligibility',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'CSU Eligibility', x_col='csu_eligibility', agg='count',
        ),
    },
    {
        'name': 'CCGI • UC Eligibility',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'UC Eligibility', x_col='uc_eligibility', agg='count',
        ),
    },
    {
        'name': 'CCGI • CSU Eligibility by School',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'stacked_bar', 'CSU Eligibility by School',
            x_col='school', stack_col='csu_eligibility',
            series_col='csu_eligibility', agg='count', data_labels=False,
        ),
    },
    {
        'name': 'CCGI • UC Eligibility by School',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'stacked_bar', 'UC Eligibility by School',
            x_col='school', stack_col='uc_eligibility',
            series_col='uc_eligibility', agg='count', data_labels=False,
        ),
    },
    {
        'name': 'CCGI • CCC Applications Completed by School',
        'chart_type': 'bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'bar', 'CCC Applications Completed by School',
            x_col='school', y_col='ccc_completed_apps', agg='sum',
            data_labels=True,
        ),
    },
    {
        'name': 'CCGI • Financial Aid Application Status',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'Financial Aid Application Status',
            x_col='financial_aid_status', agg='count',
        ),
    },
    {
        # Eligibility "Requires Review" scope, organized across both systems.
        # review_scope is NULL for students not under review, so only review
        # students appear. Drill shows the CSU/UC eligibility_detail text.
        'name': 'CCGI • Requires Review Scope (CSU & UC)',
        'chart_type': 'donut',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'donut', 'Eligibility Requires Review (CSU vs UC)',
            x_col='review_scope', agg='count',
        ),
    },
    {
        'name': 'CCGI • Students Requiring Review by School',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI College & Financial',
        'config': _base_cfg(
            'stacked_bar', 'Students Requiring Review by School',
            x_col='school', stack_col='review_scope', series_col='review_scope',
            agg='count', data_labels=False,
        ),
    },
    # ── College applications (CSU / UC / CCC) ────────────────────────
    {
        'name': 'CCGI • Applications by Status & System',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI College Applications',
        'config': _base_cfg(
            'stacked_bar', 'College Applications by System',
            x_col='system', stack_col='application_status',
            series_col='application_status', agg='count',
        ),
    },
    {
        # System is applied as a chart-level source filter against the shared
        # 'CCGI College Applications' dataset (not a dedicated dataset).
        'name': 'CCGI • CSU Applications by College',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'source_filters': {'system': ['CSU']},
        'config': _base_cfg(
            'bar', 'CSU Applications by College', x_col='college_name', agg='count',
            top_n=20,
        ),
    },
    {
        'name': 'CCGI • UC Applications by College',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'source_filters': {'system': ['UC']},
        'config': _base_cfg(
            'bar', 'UC Applications by College', x_col='college_name', agg='count',
            top_n=20,
        ),
    },
    {
        'name': 'CCGI • CCC Applications by College',
        'chart_type': 'bar',
        'dataset': 'CCGI College Applications',
        'source_filters': {'system': ['CCC']},
        'config': _base_cfg(
            'bar', 'CCC Applications by College', x_col='college_name', agg='count',
            top_n=20,
        ),
    },
    {
        # KPI: unique students who applied to ANY system (CSU/UC/CCC). The
        # applications table only holds applicants, so a distinct count of ssid
        # across all rows is the union count; a student with applications in
        # multiple systems is counted once.
        'name': 'CCGI • Students Who Applied (Any System)',
        'chart_type': 'gauge',
        'dataset': 'CCGI College Applications',
        'config': _base_cfg(
            'gauge', 'Students Who Applied (Any System)',
            y_col='ssid', agg='count_unique',
        ),
    },
    {
        # On-Track students, stacked by application status including
        # "Not Applied" (from the status table) so not-yet-applied on-track
        # students are visible. Unique students per status. The system +
        # eligibility are chart-level source filters against the shared
        # 'CCGI Application Status' dataset.
        'name': 'CCGI • CSU On-Track Applicants by School',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI Application Status',
        'source_filters': {'system': ['CSU'], 'eligibility': ['On-Track']},
        'config': _base_cfg(
            'stacked_bar', 'CSU On-Track Applicants by School',
            x_col='school', stack_col='application_status',
            series_col='application_status', y_col='ssid', agg='count_unique',
            stack_order=['Not Applied', 'In Progress', 'Completed'],
            data_labels=True, label_position='inside',
        ),
    },
    {
        'name': 'CCGI • UC On-Track Applicants by School',
        'chart_type': 'stacked_bar',
        'dataset': 'CCGI Application Status',
        'source_filters': {'system': ['UC'], 'eligibility': ['On-Track']},
        'config': _base_cfg(
            'stacked_bar', 'UC On-Track Applicants by School',
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
                {'chart': 'CCGI • Top Favorite Careers', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'label': 'Career Cluster Interest', 'sections': [
                {'chart': 'CCGI • Students by Career Cluster', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI • Career Cluster Interest by Grade', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
        ],
    },
    {
        'title': 'CCGI College & Financial',
        'slug': 'ccgi-college-financial',
        'description': 'CSU, UC, and CCC eligibility and applications, plus '
                       'financial-aid application status. Filter by school and '
                       'drill into students from any chart.',
        'page_filters': [
            {
                'id': 'school', 'label': 'School', 'source_col': 'school',
                'source_table': COLLEGE_FINANCIAL_TABLE,
                'widget': 'select', 'default_val': '', 'hidden': False,
            },
        ],
        'tabs': ['Overview', 'CSU', 'UC', 'CCC'],
        'rows': [
            {'tab': 'Overview', 'label': 'Applicant Summary', 'sections': [
                {'chart': 'CCGI • Students Who Applied (Any System)', 'gs_x': 0, 'gs_w': 4, 'gs_h': 5},
            ]},
            {'tab': 'Overview', 'label': 'Applications by System', 'sections': [
                {'chart': 'CCGI • Applications by Status & System', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'Overview', 'label': 'Eligibility Requires Review', 'sections': [
                {'chart': 'CCGI • Requires Review Scope (CSU & UC)', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI • Students Requiring Review by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'Overview', 'label': 'Financial Aid', 'sections': [
                {'chart': 'CCGI • Financial Aid Application Status', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'CSU', 'label': 'CSU Eligibility', 'sections': [
                {'chart': 'CCGI • CSU Eligibility', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI • CSU Eligibility by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'CSU', 'label': 'CSU Applications', 'sections': [
                {'chart': 'CCGI • CSU Applications by College', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'CSU', 'label': 'CSU On-Track Applicants (Not Applied / In Progress / Completed)', 'sections': [
                {'chart': 'CCGI • CSU On-Track Applicants by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'UC', 'label': 'UC Eligibility', 'sections': [
                {'chart': 'CCGI • UC Eligibility', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI • UC Eligibility by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'UC', 'label': 'UC Applications', 'sections': [
                {'chart': 'CCGI • UC Applications by College', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'UC', 'label': 'UC On-Track Applicants (Not Applied / In Progress / Completed)', 'sections': [
                {'chart': 'CCGI • UC On-Track Applicants by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'CCC', 'label': 'CCC Applications', 'sections': [
                {'chart': 'CCGI • CCC Applications Completed by School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'CCGI • CCC Applications by College', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
        ],
    },
]


class Command(BaseCommand):
    help = 'Create/refresh the CCGI datasets, charts, and published DataViz pages.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--owner', type=str, default='',
            help='Username or email of the charts/pages owner (defaults to the first superuser).',
        )

    def handle(self, *args, **options):
        owner = self._resolve_owner(options.get('owner'))
        self.stdout.write(f'Using owner: {owner}')

        self._enable_dataset_tables()

        datasets = {}
        for spec in DATASET_SPECS:
            datasets[spec['name']] = self._upsert_dataset(spec, owner)

        charts = {}
        for spec in CHART_SPECS:
            charts[spec['name']] = self._upsert_chart(spec, datasets, owner)

        self._remove_obsolete_datasets()

        pages = []
        for spec in PAGE_SPECS:
            pages.append(self._upsert_page(spec, charts, owner))

        self._attach_to_menu(pages)
        self.stdout.write(self.style.SUCCESS('CCGI dashboards seeded.'))

    # ── helpers ──────────────────────────────────────────────────────

    def _resolve_owner(self, owner_ref):
        from users.models import User

        if owner_ref:
            user = (
                User.objects.filter(username=owner_ref).first()
                or User.objects.filter(email__iexact=owner_ref).first()
            )
            if not user:
                raise CommandError(f'No user found for "{owner_ref}".')
            return user

        user = User.objects.filter(is_superuser=True).order_by('id').first()
        if not user:
            user = User.objects.order_by('id').first()
        if not user:
            raise CommandError('No users exist to own the CCGI dashboards.')
        return user

    def _enable_dataset_tables(self):
        """Add the CCGI analytics tables to the project's dataset allowlist."""
        from landing.models import ProjectConfig

        cfg = ProjectConfig.get()
        enabled = list(cfg.enabled_dataset_tables or [])
        if not enabled:
            self.stdout.write('enabled_dataset_tables is empty; leaving unrestricted.')
            return
        missing = [t for t in CCGI_TABLES if t not in enabled]
        if missing:
            cfg.enabled_dataset_tables = enabled + missing
            cfg.save()
            self.stdout.write(f'Enabled datasets: {", ".join(missing)}')

    def _upsert_dataset(self, spec, owner):
        base = spec['base_table']
        columns = [f'{base}.{c}' for c in spec['columns']]
        mappings = {f'{base}.{c}': c for c in spec['columns']}
        dataset, _ = Dataset.objects.update_or_create(
            name=spec['name'],
            owner=owner,
            defaults={
                'source_type': Dataset.SOURCE_VISUAL,
                'base_table': base,
                'distinct': True,
                'columns': columns,
                'column_mappings': mappings,
                'joins': [],
                'filters': spec.get('filters', []),
                'status_filter': [],  # all statuses
                'order_by': [],
                'description': spec['description'],
            },
        )
        self.stdout.write(f'Dataset ready: {dataset.name} (#{dataset.pk})')
        return dataset

    def _remove_obsolete_datasets(self):
        """Delete the per-system datasets replaced by the shared CCGI datasets."""
        if not OBSOLETE_DATASET_NAMES:
            return
        qs = Dataset.objects.filter(name__in=OBSOLETE_DATASET_NAMES)
        count = qs.count()
        if count:
            qs.delete()
            self.stdout.write(
                f'Removed {count} obsolete dataset(s): '
                f'{", ".join(OBSOLETE_DATASET_NAMES)}'
            )

    def _upsert_chart(self, spec, datasets, owner):
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
                'description': spec['config'].get('chart_title', ''),
            },
        )
        self.stdout.write(f'  chart ready: {chart.name} (#{chart.pk})')
        return chart

    def _upsert_page(self, spec, charts, owner):
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
                'status': DataVizPage.STATUS_ACTIVE,
            },
        )
        # Rebuild layout (tabs + rows + sections) from the spec so reruns are
        # idempotent.
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
        self.stdout.write(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
        return page

    def _attach_to_menu(self, pages):
        """Place the CCGI pages under a shared 'CCGI' menu category."""
        from users.models_pages import Page

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
                'menu_order': 60,
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
        self.stdout.write('Menu category "CCGI" updated.')
