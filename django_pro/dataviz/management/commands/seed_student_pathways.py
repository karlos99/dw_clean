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

OUTCOMES_DATASET_NAME = 'College Pathway Outcomes'
OUTCOMES_CHART_NAME = 'High School to College Outcomes'
OUTCOMES_COLUMNS = [
    'hs_stage',
    'first_stage',
    'outcome_stage',
    'grade_5_school',
    'grade_8_school',
    'high_school_name',
    'cohort',
]
OUTCOMES_SQL = """
SELECT
    COALESCE(NULLIF(BTRIM(high_school_name), ''), 'Unknown High School') AS hs_stage,
    CASE
        WHEN upper(btrim(coalesce(enrolled_post_hs, ''))) = 'Y' THEN
            CASE
                WHEN btrim(coalesce(first_post_hs_college_type, ''))
                     IN ('2-Year', 'Less Than 2 Years') THEN '2-Year College'
                WHEN btrim(coalesce(first_post_hs_college_type, '')) = '4-Year'
                    THEN '4-Year College'
                ELSE 'Other College'
            END
        ELSE 'Never Enrolled'
    END AS first_stage,
    CASE
        WHEN upper(btrim(coalesce(enrolled_post_hs, ''))) <> 'Y' THEN ''
        WHEN position('bachelor' IN lower(btrim(coalesce(highest_degree_level, '')))) > 0
            THEN 'Bachelor''s Degree'
        WHEN position('associate' IN lower(btrim(coalesce(highest_degree_level, '')))) > 0
            THEN 'Associate Degree'
        WHEN position('certificate' IN lower(btrim(coalesce(highest_degree_level, '')))) > 0
            THEN 'Certificate'
        ELSE 'Attended, Not Completed'
    END AS outcome_stage,
    grade_5_school,
    grade_8_school,
    high_school_name,
    cohort
FROM student_pathways
"""

OUTCOMES_CONFIG = {
    'chart_type': 'sankey',
    'x_col': '',
    'sk_path_col': '',
    'sk_stage_cols': ['hs_stage', 'first_stage', 'outcome_stage'],
    'sk_stage_labels': {
        'hs_stage': 'High Schools',
        'first_stage': 'First Enrollment',
        'outcome_stage': 'Completion Outcome',
    },
    'sk_exclude_hs_from_grade8': False,
    'sk_blank_label': '',
    'sk_label_mode': 'percent',
    'sk_label_width': 190,
    'sk_label_box': True,
    'sk_label_position': 'auto',
    'sk_right_padding': 18,
    'sk_label_min_value': 0,
    'sk_color_mode': 'stage',
    'palette': 'blue',
    'sk_order_mode': 'value',
    'sk_link_color_mode': 'source',
    'sk_top_per_stage': 0,
    'sk_max_nodes': 0,
    'sk_min_link_value': 1,
    'sk_node_align': 'justify',
    'sk_orient': 'horizontal',
    'sk_layout_iterations': 0,
    'sk_node_width': 18,
    'sk_node_gap': 24,
    'sk_show_labels': True,
    'sk_show_stage_flow': True,
    'sk_curveness': 0.5,
    'sk_line_opacity': 0.32,
    'label_font_size': 11,
    'container_height': 900,
    'chart_title': 'High School to College Outcomes',
    'chart_subtitle': 'Each high school → first enrollment → completion outcome.',
    'show_title': True,
    'show_subtitle': True,
    'show_tooltip': True,
    'sk_series_name': 'High School to College Outcomes',
}

AVID_OUTCOMES_DATASET_NAME = 'AVID Pathway Outcomes'
AVID_OUTCOMES_CHART_NAME = 'AVID Student Outcomes'
AVID_OUTCOMES_COLUMNS = [
    'hs_stage',
    'first_stage',
    'outcome_stage',
    'grade_5_school',
    'grade_8_school',
    'high_school_name',
    'cohort',
]

# AVID is a high-school course in schedule_table_alt.  Keep every student with
# at least one AVID course in their high-school schedule (tutor sections do not
# count as the AVID class), then join to student_pathways for outcomes.
AVID_OUTCOMES_SQL = """
WITH avid_students AS (
    SELECT DISTINCT ssid
    FROM schedule_table_alt
    WHERE school_type = 'High School'
      AND school_year IN ('2023-2024', '2024-2025', '2025-2026')
      AND upper(left(btrim(course_name), 4)) = 'AVID'
      AND position('tutor' in lower(course_name)) = 0
)
SELECT
    COALESCE(NULLIF(BTRIM(sp.high_school_name), ''), 'Unknown High School') AS hs_stage,
    CASE
        WHEN upper(btrim(coalesce(sp.enrolled_post_hs, ''))) = 'Y' THEN
            CASE
                WHEN btrim(coalesce(sp.first_post_hs_college_type, ''))
                     IN ('2-Year', 'Less Than 2 Years') THEN '2-Year College'
                WHEN btrim(coalesce(sp.first_post_hs_college_type, '')) = '4-Year'
                    THEN '4-Year College'
                ELSE 'Other College'
            END
        ELSE 'Never Enrolled'
    END AS first_stage,
    CASE
        WHEN upper(btrim(coalesce(sp.enrolled_post_hs, ''))) <> 'Y' THEN ''
        WHEN position('bachelor' IN lower(btrim(coalesce(sp.highest_degree_level, '')))) > 0
            THEN 'Bachelor''s Degree'
        WHEN position('associate' IN lower(btrim(coalesce(sp.highest_degree_level, '')))) > 0
            THEN 'Associate Degree'
        WHEN position('certificate' IN lower(btrim(coalesce(sp.highest_degree_level, '')))) > 0
            THEN 'Certificate'
        ELSE 'Attended, Not Completed'
    END AS outcome_stage,
    sp.grade_5_school,
    sp.grade_8_school,
    sp.high_school_name,
    sp.cohort
FROM student_pathways sp
JOIN avid_students avid ON avid.ssid = sp.state_student_id
"""

AVID_OUTCOMES_CONFIG = {
    'chart_type': 'sankey',
    'x_col': '',
    'sk_path_col': '',
    'sk_stage_cols': ['hs_stage', 'first_stage', 'outcome_stage'],
    'sk_stage_labels': {
        'hs_stage': 'High Schools',
        'first_stage': 'First Enrollment',
        'outcome_stage': 'Completion Outcome',
    },
    'sk_exclude_hs_from_grade8': False,
    'sk_blank_label': '',
    'sk_label_mode': 'percent',
    'sk_label_width': 190,
    'sk_label_box': True,
    'sk_label_position': 'auto',
    'sk_right_padding': 18,
    'sk_label_min_value': 0,
    'sk_color_mode': 'stage',
    'palette': 'blue',
    'sk_order_mode': 'value',
    'sk_link_color_mode': 'source',
    'sk_top_per_stage': 0,
    'sk_max_nodes': 0,
    'sk_min_link_value': 1,
    'sk_node_align': 'justify',
    'sk_orient': 'horizontal',
    'sk_layout_iterations': 0,
    'sk_node_width': 18,
    'sk_node_gap': 24,
    'sk_show_labels': True,
    'sk_show_stage_flow': True,
    'sk_curveness': 0.5,
    'sk_line_opacity': 0.32,
    'label_font_size': 11,
    'container_height': 900,
    'chart_title': 'AVID Student Outcomes',
    'chart_subtitle': (
        'Students with an AVID course in their high-school schedule → first '
        'college enrollment → completion outcome.'
    ),
    'show_title': True,
    'show_subtitle': True,
    'show_tooltip': True,
    'sk_series_name': 'AVID Student Outcomes',
}

CTE_LEVEL_OUTCOMES_DATASET_NAME = 'CTE Pathway Level Outcomes'
CTE_LEVEL_OUTCOMES_CHART_NAME = 'CTE Pathway Level Outcomes'
CTE_LEVEL_OUTCOMES_COLUMNS = [
    'hs_stage',
    'cte_level_stage',
    'first_stage',
    'outcome_stage',
    'grade_5_school',
    'grade_8_school',
    'high_school_name',
    'cohort',
]

# Each student's highest CTE level across all their pathway records.  A
# completer record wins; otherwise a met capstone, then a met concentration,
# then introductory participation.  CTE details are PostgreSQL-only, so this
# dataset intentionally joins there and keeps only tracked student_pathways
# students (the 2024-25 cohort has the CTE records).
CTE_LEVEL_OUTCOMES_SQL = """
WITH cte_levels AS (
    SELECT
        state_student_id,
        MAX(
            CASE
                WHEN pathway_status = 'CTE Completer' THEN 4
                WHEN btrim(coalesce(cap_status, '')) = 'Met' THEN 3
                WHEN btrim(coalesce(con_status, '')) = 'Met' THEN 2
                ELSE 1
            END
        ) AS level
    FROM coursecatalog_cte_details
    WHERE school_year IN ('2024', '2025', '2026')
    GROUP BY state_student_id
)
SELECT
    COALESCE(NULLIF(BTRIM(sp.high_school_name), ''), 'Unknown High School') AS hs_stage,
    CASE cte.level
        WHEN 4 THEN 'Completer'
        WHEN 3 THEN 'Capstone'
        WHEN 2 THEN 'Concentrator'
        ELSE 'Intro'
    END AS cte_level_stage,
    CASE
        WHEN upper(btrim(coalesce(sp.enrolled_post_hs, ''))) = 'Y' THEN
            CASE
                WHEN btrim(coalesce(sp.first_post_hs_college_type, ''))
                     IN ('2-Year', 'Less Than 2 Years') THEN '2-Year College'
                WHEN btrim(coalesce(sp.first_post_hs_college_type, '')) = '4-Year'
                    THEN '4-Year College'
                ELSE 'Other College'
            END
        ELSE 'Never Enrolled'
    END AS first_stage,
    CASE
        WHEN upper(btrim(coalesce(sp.enrolled_post_hs, ''))) <> 'Y' THEN ''
        WHEN position('bachelor' IN lower(btrim(coalesce(sp.highest_degree_level, '')))) > 0
            THEN 'Bachelor''s Degree'
        WHEN position('associate' IN lower(btrim(coalesce(sp.highest_degree_level, '')))) > 0
            THEN 'Associate Degree'
        WHEN position('certificate' IN lower(btrim(coalesce(sp.highest_degree_level, '')))) > 0
            THEN 'Certificate'
        ELSE 'Attended, Not Completed'
    END AS outcome_stage,
    sp.grade_5_school,
    sp.grade_8_school,
    sp.high_school_name,
    sp.cohort
FROM student_pathways sp
JOIN cte_levels cte ON cte.state_student_id = sp.state_student_id
"""

CTE_LEVEL_OUTCOMES_CONFIG = {
    'chart_type': 'sankey',
    'x_col': '',
    'sk_path_col': '',
    'sk_stage_cols': [
        'hs_stage',
        'cte_level_stage',
        'first_stage',
        'outcome_stage',
    ],
    'sk_stage_labels': {
        'hs_stage': 'High Schools',
        'cte_level_stage': 'Highest CTE Level',
        'first_stage': 'First Enrollment',
        'outcome_stage': 'Completion Outcome',
    },
    'sk_exclude_hs_from_grade8': False,
    'sk_blank_label': '',
    'sk_label_mode': 'percent',
    'sk_label_width': 190,
    'sk_label_box': True,
    'sk_label_position': 'auto',
    'sk_right_padding': 18,
    'sk_label_min_value': 0,
    'sk_color_mode': 'stage',
    'palette': 'green',
    'color_map': {
        'Intro': '#94a3b8',
        'Concentrator': '#5b9bd5',
        'Capstone': '#0d9488',
        'Completer': '#16a34a',
    },
    'sk_order_mode': 'value',
    'sk_link_color_mode': 'source',
    'sk_top_per_stage': 0,
    'sk_max_nodes': 0,
    'sk_min_link_value': 1,
    'sk_node_align': 'justify',
    'sk_orient': 'horizontal',
    'sk_layout_iterations': 0,
    'sk_node_width': 18,
    'sk_node_gap': 24,
    'sk_show_labels': True,
    'sk_show_stage_flow': True,
    'sk_curveness': 0.5,
    'sk_line_opacity': 0.32,
    'label_font_size': 11,
    'container_height': 900,
    'chart_title': 'CTE Pathway Level Outcomes',
    'chart_subtitle': (
        'Highest CTE level achieved → first college enrollment → completion '
        'outcome, 2024-25 cohort.'
    ),
    'show_title': True,
    'show_subtitle': True,
    'show_tooltip': True,
    'sk_series_name': 'CTE Pathway Level Outcomes',
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
    'sk_other_split_systems': True,
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
        outcomes_chart = None
        avid_outcomes_chart = None
        cte_level_chart = None
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

            # High-level outcome Sankey: HS graduates → first enrollment →
            # completion outcome (the summary version of the pathway).
            outcomes_dataset, outcomes_ds_created = Dataset.objects.update_or_create(
                name=OUTCOMES_DATASET_NAME,
                owner=owner,
                defaults={
                    'source_type': Dataset.SOURCE_SQL,
                    'source_sql': OUTCOMES_SQL,
                    'base_table': 'student_pathways',
                    'distinct': False,
                    'columns': OUTCOMES_COLUMNS,
                    'column_mappings': {c: c for c in OUTCOMES_COLUMNS},
                    'description': (
                        'One row per student with first-enrollment and completion-'
                        'outcome stages, plus school/cohort columns for page filters.'
                    ),
                },
            )
            outcomes_chart, outcomes_chart_created = SavedChart.objects.update_or_create(
                name=OUTCOMES_CHART_NAME,
                owner=owner,
                defaults={
                    'chart_type': 'sankey',
                    'config': dict(OUTCOMES_CONFIG),
                    'source_type': SavedChart.SOURCE_DATASET,
                    'source_id': outcomes_dataset.pk,
                    'source_filters': {},
                    'column_defs': {},
                    'description': (
                        'High school graduates → first enrollment (2-year / 4-year / '
                        'never) → completion outcome.'
                    ),
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if outcomes_ds_created else 'Updated'} dataset "
                f"#{outcomes_dataset.pk} '{outcomes_dataset.name}'."
            ))
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if outcomes_chart_created else 'Updated'} chart "
                f"#{outcomes_chart.pk} '{outcomes_chart.name}'."
            ))

            # AVID-only outcome chart: high schools → first enrollment →
            # completion outcome for students with an AVID course in high school.
            avid_dataset, avid_ds_created = Dataset.objects.update_or_create(
                name=AVID_OUTCOMES_DATASET_NAME,
                owner=owner,
                defaults={
                    'source_type': Dataset.SOURCE_SQL,
                    'source_sql': AVID_OUTCOMES_SQL,
                    'base_table': 'student_pathways',
                    'distinct': False,
                    'columns': AVID_OUTCOMES_COLUMNS,
                    'column_mappings': {c: c for c in AVID_OUTCOMES_COLUMNS},
                    'description': (
                        'Students with at least one AVID course in their high-'
                        'school schedule, joined to student_pathways outcomes.'
                    ),
                },
            )
            avid_outcomes_chart, avid_chart_created = SavedChart.objects.update_or_create(
                name=AVID_OUTCOMES_CHART_NAME,
                owner=owner,
                defaults={
                    'chart_type': 'sankey',
                    'config': dict(AVID_OUTCOMES_CONFIG),
                    'source_type': SavedChart.SOURCE_DATASET,
                    'source_id': avid_dataset.pk,
                    'source_filters': {},
                    'column_defs': {},
                    'description': (
                        'AVID students by high school → first enrollment → '
                        'completion outcome.'
                    ),
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if avid_ds_created else 'Updated'} dataset "
                f"#{avid_dataset.pk} '{avid_dataset.name}'."
            ))
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if avid_chart_created else 'Updated'} chart "
                f"#{avid_outcomes_chart.pk} '{avid_outcomes_chart.name}'."
            ))

            # CTE-only outcome chart: high schools → highest CTE level
            # (Intro / Concentrator / Capstone / Completer) → enrollment →
            # completion outcome.
            cte_dataset, cte_ds_created = Dataset.objects.update_or_create(
                name=CTE_LEVEL_OUTCOMES_DATASET_NAME,
                owner=owner,
                defaults={
                    'source_type': Dataset.SOURCE_SQL,
                    'source_sql': CTE_LEVEL_OUTCOMES_SQL,
                    'base_table': 'student_pathways',
                    'distinct': False,
                    'columns': CTE_LEVEL_OUTCOMES_COLUMNS,
                    'column_mappings': {c: c for c in CTE_LEVEL_OUTCOMES_COLUMNS},
                    'description': (
                        'Each CTE participant\'s highest pathway level, joined to '
                        'student_pathways outcomes.'
                    ),
                },
            )
            cte_level_chart, cte_chart_created = SavedChart.objects.update_or_create(
                name=CTE_LEVEL_OUTCOMES_CHART_NAME,
                owner=owner,
                defaults={
                    'chart_type': 'sankey',
                    'config': dict(CTE_LEVEL_OUTCOMES_CONFIG),
                    'source_type': SavedChart.SOURCE_DATASET,
                    'source_id': cte_dataset.pk,
                    'source_filters': {},
                    'column_defs': {},
                    'description': (
                        'CTE participants → highest CTE level → first enrollment '
                        '→ completion outcome, with school and cohort filters.'
                    ),
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if cte_ds_created else 'Updated'} dataset "
                f"#{cte_dataset.pk} '{cte_dataset.name}'."
            ))
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if cte_chart_created else 'Updated'} chart "
                f"#{cte_level_chart.pk} '{cte_level_chart.name}'."
            ))

        if options.get('page'):
            if (
                chart is None or colleges_chart is None
                or outcomes_chart is None or avid_outcomes_chart is None
                or cte_level_chart is None
            ):
                raise CommandError('The page needs all five charts; pass --chart as well.')

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
                'The top chart summarizes each high school\'s graduates → first '
                'college enrollment → completion outcome. Below it, one chart '
                'follows CTE participants through their highest pathway level '
                '(Intro, Concentrator, Capstone, Completer), and a separate chart '
                'follows students who took an AVID course in high school. The '
                'detailed Sankey then shows the full elementary → grade 8 → high '
                'school → college pathway. Filter by elementary, grade 8, high '
                'school, and cohort year at the top. Click any college node to '
                'trace its upstream schools; click an Other node to expand its '
                'grouped colleges in the chart.'
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

            outcomes_row = _page_row(0)
            _page_section(outcomes_row, outcomes_chart, 12)
            cte_row = _page_row(1)
            _page_section(cte_row, cte_level_chart, 10)
            avid_row = _page_row(2)
            _page_section(avid_row, avid_outcomes_chart, 10)
            sankey_row = _page_row(3)
            _page_section(sankey_row, chart, 12)
            colleges_row = _page_row(4, 'All Colleges')
            _page_section(colleges_row, colleges_chart, 10)

            # Remove any stale rows left below the current five-section layout.
            DataVizPageRow.objects.filter(page=page, position__gt=4).delete()

            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if page_created else 'Updated'} page #{page.pk} "
                f"'{page.title}' (slug: {page.slug}, status: {page.status}) "
                f"with outcomes + CTE levels + AVID + Sankey + all-colleges table."
            ))
