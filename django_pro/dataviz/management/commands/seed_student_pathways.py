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

from dataviz.models import Dataset, SavedChart


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

SANKEY_CONFIG = {
    'chart_type': 'sankey',
    'x_col': 'school_and_college_history',
    'sk_path_col': 'school_and_college_history',
    'sk_path_format': 'auto',
    'sk_stages': [],
    'sk_stage_labels': {
        'grade_5': 'Elementary',
        'grade_8': 'Middle School',
        'high_school': 'High School',
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

        if options.get('chart'):
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
                        'Elementary → Middle School → High School → College trajectory. '
                        'Filter by college type (2-Year / 4-Year) and UC/CSU system in the chart config.'
                    ),
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if chart_created else 'Updated'} chart #{chart.pk} '{chart.name}'."
            ))
