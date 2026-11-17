"""Create the reusable student-pathway dataset (and optionally a Sankey chart).

The student_pathways extract is populated outside this Django project.  This
command wires it into the DataViz app without touching the source data:

    python manage.py seed_student_pathways
    python manage.py seed_student_pathways --chart
    python manage.py seed_student_pathways --owner carlos --chart

The dataset is created with ``distinct=False`` on purpose: every row is one
student, so duplicate pathway strings must still count as separate students.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from dataviz.models import (
    DataVizPage, DataVizPageRow, DataVizPageSection, Dataset, SavedChart,
)


PATHWAY_COLUMNS = [
    'student_pathways.school_and_college_history',
    'student_pathways.first_post_hs_college_name',
    'student_pathways.first_post_hs_college_type',
    'student_pathways.first_post_hs_college_control',
    'student_pathways.first_post_hs_college_state',
    'student_pathways.latest_college_name',
    'student_pathways.latest_college_type',
    'student_pathways.latest_college_control',
    'student_pathways.latest_college_state',
    'student_pathways.grade_5_school',
    'student_pathways.grade_8_school',
    'student_pathways.high_school_name',
    'student_pathways.cohort',
]

DATASET_NAME = 'Student Pathways'
CHART_NAME = 'Student Pathways — Elementary to College'
PAGE_TITLE = 'College Pathway'
ALL_COLLEGES_DATASET_NAME = 'Student Pathway Colleges'
ALL_COLLEGES_CHART_NAME = 'All Colleges — Student Pathway'

ALL_COLLEGES_SQL = """
SELECT
    COALESCE(NULLIF(BTRIM(elem.value->>'college'), ''), '(Not reported)') AS college,
    elem.ord AS college_sequence,
    sp.grade_5_school,
    sp.grade_8_school,
    sp.high_school_name,
    sp.cohort,
    COUNT(*) AS students
FROM student_pathways sp
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(
        CASE
            WHEN sp.school_and_college_history IS NULL
                 OR BTRIM(sp.school_and_college_history) = '' THEN '[]'::jsonb
            WHEN LEFT(BTRIM(sp.school_and_college_history), 1) = '{'
                THEN BTRIM(sp.school_and_college_history)::jsonb->'colleges'
            ELSE '[]'::jsonb
        END,
        '[]'::jsonb
    )
) WITH ORDINALITY AS elem(value, ord)
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY students DESC, college
"""

ALL_COLLEGES_CONFIG = {
    'chart_type': 'summary_table',
    'st_row_col': 'college',
    'st_col_col': 'college_sequence',
    'st_col_mode': 'values',
    'st_agg_func': 'sum',
    'st_value_col': 'students',
    'st_show_row_totals': True,
    'st_show_col_totals': True,
    'st_show_tooltips': True,
    'chart_title': 'All Colleges',
    'show_title': True,
    'container_height': 900,
}

SANKEY_CONFIG = {
    'chart_type': 'sankey',
    'x_col': 'school_and_college_history',
    'sk_path_col': 'school_and_college_history',
    'sk_path_format': 'auto',
    'sk_stages': [],
    'sk_stage_cols': [
        'grade_5_school',
        'grade_8_school',
        'high_school_name',
    ],
    'sk_exclude_hs_from_grade8': True,
    'sk_stage_labels': {
        'grade_5': 'Elementary',
        'grade_8': 'Grade 8',
        'high_school': 'High School',
        'grade_5_school': 'Elementary',
        'grade_8_school': 'Grade 8',
        'high_school_name': 'High School',
    },
    'sk_colleges_key': 'colleges',
    'sk_college_name_key': 'college',
    'sk_max_college_steps': 4,
    'sk_blank_label': '(Not reported)',
    'sk_college_types': [],
    'sk_college_systems': [],
    'sk_college_filter_scope': 'first',
    'sk_min_link_value': 2,
    'sk_max_nodes': 300,
    'sk_color_mode': 'node',
    'sk_order_mode': 'value',
    'sk_link_color_mode': 'source',
    'sk_top_per_stage': 12,
    'sk_group_after_stage': 3,
    'sk_label_min_value': 5,
    'sk_show_stage_flow': True,
    'sk_node_align': 'justify',
    'sk_orient': 'horizontal',
    'sk_layout_iterations': 0,
    'sk_node_width': 14,
    'sk_node_gap': 14,
    'sk_label_mode': 'value',
    'sk_show_labels': True,
    'sk_curveness': 0.35,
    'sk_line_opacity': 0.28,
    'container_height': 900,
    'chart_title': 'Student Pathways',
    'show_title': True,
    'show_tooltip': True,
}


class Command(BaseCommand):
    help = 'Create the Student Pathways dataset (and optionally a Sankey chart).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--owner',
            help='Username that will own the dataset/chart. Defaults to the first Project Admin.',
        )
        parser.add_argument(
            '--chart',
            action='store_true',
            help='Also create/update a ready-to-use Sankey chart on the dataset.',
        )
        parser.add_argument(
            '--page',
            action='store_true',
            help='Also create/update the "College Pathway" page with the chart on it.',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        owner = None
        if options.get('owner'):
            owner = User.objects.filter(username=options['owner']).first()
            if owner is None:
                raise CommandError(f"User '{options['owner']}' not found.")
        else:
            owner = User.objects.filter(roles__name='Project Admin').order_by('pk').first()
            if owner is None:
                owner = User.objects.filter(is_superuser=True).order_by('pk').first()
            if owner is None:
                raise CommandError(
                    'No Project Admin/superuser found. Pass --owner <username>.'
                )

        dataset, created = Dataset.objects.update_or_create(
            name=DATASET_NAME,
            owner=owner,
            defaults={
                'source_type': Dataset.SOURCE_VISUAL,
                'base_table': 'student_pathways',
                # One row per student: never collapse identical pathway rows.
                'distinct': False,
                'columns': PATHWAY_COLUMNS,
                'description': (
                    'Student pathway extract: elementary/middle/high school plus the '
                    'college trajectory stored in school_and_college_history.'
                ),
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} dataset #{dataset.pk} '{dataset.name}' "
            f"for {owner.username}."
        ))

        chart = None
        colleges_chart = None
        if options.get('chart') or options.get('page'):
            chart, chart_created = SavedChart.objects.update_or_create(
                name=CHART_NAME,
                owner=owner,
                defaults={
                    'chart_type': 'sankey',
                    'config': dict(SANKEY_CONFIG),
                    'source_type': SavedChart.SOURCE_DATASET,
                    'source_id': dataset.pk,
                    'source_filters': {},
                    'column_defs': {},
                    'description': (
                        'Elementary → Grade 8 → High School → College trajectory. '
                        'Filter by college type (2-Year / 4-Year) and UC/CSU system in the chart config.'
                    ),
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if chart_created else 'Updated'} chart #{chart.pk} '{chart.name}'."
            ))

            # Companion table: every college in the trajectory, not just the
            # top nodes visible in the Sankey.
            colleges_dataset, colleges_ds_created = Dataset.objects.update_or_create(
                name=ALL_COLLEGES_DATASET_NAME,
                owner=owner,
                defaults={
                    'source_type': Dataset.SOURCE_SQL,
                    'source_sql': ALL_COLLEGES_SQL,
                    'base_table': 'student_pathways',
                    'distinct': False,
                    'columns': [
                        'college', 'college_sequence', 'grade_5_school',
                        'grade_8_school', 'high_school_name', 'cohort', 'students',
                    ],
                    'column_mappings': {
                        'college': 'college',
                        'college_sequence': 'college_sequence',
                        'grade_5_school': 'grade_5_school',
                        'grade_8_school': 'grade_8_school',
                        'high_school_name': 'high_school_name',
                        'cohort': 'cohort',
                        'students': 'students',
                    },
                    'description': (
                        'Every college in school_and_college_history, with student counts '
                        'grouped by college sequence.'
                    ),
                },
            )
            colleges_chart, colleges_chart_created = SavedChart.objects.update_or_create(
                name=ALL_COLLEGES_CHART_NAME,
                owner=owner,
                defaults={
                    'chart_type': 'summary_table',
                    'config': dict(ALL_COLLEGES_CONFIG),
                    'source_type': SavedChart.SOURCE_DATASET,
                    'source_id': colleges_dataset.pk,
                    'source_filters': {},
                    'column_defs': {},
                    'description': (
                        'All colleges in the student pathway, with counts by college '
                        'sequence (1 = first college after high school).'
                    ),
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if colleges_ds_created else 'Updated'} dataset "
                f"#{colleges_dataset.pk} '{colleges_dataset.name}'."
            ))
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if colleges_chart_created else 'Updated'} chart "
                f"#{colleges_chart.pk} '{colleges_chart.name}'."
            ))

        if options.get('page'):
            if chart is None or colleges_chart is None:
                raise CommandError('The page needs both charts; pass --chart as well.')

            page = DataVizPage.objects.filter(slug='college-pathway').first()
            if page is not None and page.owner_id != owner.pk:
                raise CommandError(
                    "A page with slug 'college-pathway' already exists for another user."
                )
            if page is None:
                page = DataVizPage.objects.filter(title=PAGE_TITLE, owner=owner).first()
            if page is None:
                page = DataVizPage(
                    title=PAGE_TITLE,
                    owner=owner,
                    status=DataVizPage.STATUS_DRAFT,
                )
            page.title = PAGE_TITLE
            page.owner = owner
            page.description = (
                'Student flow from elementary school through grade 8 and high school '
                'into the college trajectory. Filter by elementary, grade 8, or high '
                'school and cohort year at the top. Click any college node to trace '
                'the elementary, grade 8, and high schools that feed it. Click an '
                'Other node to expand its grouped colleges directly in the chart.'
            )
            page.container_width = DataVizPage.CONTAINER_FULL
            page.show_header = True
            page.page_filters = [
                {
                    'id': 'elem_school',
                    'label': 'Elementary School',
                    'source_table': 'student_pathways',
                    'source_col': 'grade_5_school',
                    'widget': 'multi',
                    'default_val': '',
                },
                {
                    'id': 'grade8_school',
                    'label': 'Grade 8 School',
                    'source_table': 'student_pathways',
                    'source_col': 'grade_8_school',
                    'widget': 'multi',
                    'default_val': '',
                },
                {
                    'id': 'high_school',
                    'label': 'High School',
                    'source_table': 'student_pathways',
                    'source_col': 'high_school_name',
                    'widget': 'multi',
                    'default_val': '',
                },
                {
                    'id': 'cohort',
                    'label': 'Cohort Year',
                    'source_table': 'student_pathways',
                    'source_col': 'cohort',
                    'widget': 'multi',
                    'default_val': '',
                },
            ]
            page_created = page.pk is None
            page.save()

            def _page_row(position, label=''):
                row = DataVizPageRow.objects.filter(page=page, position=position).first()
                if row is None:
                    row = DataVizPageRow.objects.create(
                        page=page, position=position, label=label,
                    )
                else:
                    row.label = label
                    row.save(update_fields=['label'])
                return row

            def _page_section(row, section_chart, height):
                section = DataVizPageSection.objects.filter(
                    page=page, row=row, position=0,
                ).first()
                created_section = section is None
                if section is None:
                    section = DataVizPageSection(page=page, row=row, position=0)
                section.element_type = DataVizPageSection.TYPE_CHART
                section.chart = section_chart
                section.show_title = True
                section.gs_x = 0
                section.gs_y = 0
                section.gs_w = 12
                section.gs_h = height
                section.save()
                return created_section

            sankey_row = _page_row(0)
            _page_section(sankey_row, chart, 12)
            colleges_row = _page_row(1, 'All Colleges')
            _page_section(colleges_row, colleges_chart, 10)

            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if page_created else 'Updated'} page #{page.pk} "
                f"'{page.title}' (slug: {page.slug}, status: {page.status}) "
                f"with the Sankey + all-colleges table."
            ))
