"""
Create saved color palettes from the response-color palettes already configured
on existing surveys.

Each distinct palette found is saved as a reusable ``SurveyColorPalette`` named
``default_1``, ``default_2``, ... Surveys created today are ignored by default so
newly-imported surveys (which usually have no colors yet) do not dilute the list.

Usage:
    python manage.py create_default_color_palettes
    python manage.py create_default_color_palettes --include-today
    python manage.py create_default_color_palettes --dry-run
"""
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from dataLoader.models import SurveyDefinition, SurveyColorPalette


class Command(BaseCommand):
    help = (
        "Create saved color palettes (default_1, default_2, ...) from the distinct "
        "response-color palettes on existing surveys."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-today',
            action='store_true',
            help='Also include surveys created today (default: exclude them).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without writing to the database.',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        include_today = options['include_today']
        dry_run = options['dry_run']

        # Collect distinct palettes keyed by their normalized JSON so duplicates
        # shared across surveys collapse into one saved palette.
        found = {}
        for sd in SurveyDefinition.objects.order_by('id'):
            created = timezone.localtime(sd.created_at).date()
            if not include_today and created == today:
                continue

            rc = sd.response_colors if isinstance(sd.response_colors, dict) else {}
            palette = {k: rc[k] for k in ('scale_5', 'scale_7') if rc.get(k)}
            if not palette:
                continue

            key = json.dumps(palette, sort_keys=True)
            entry = found.setdefault(key, {'colors': palette, 'surveys': []})
            entry['surveys'].append(sd.id)

        if not found:
            self.stdout.write("No survey color palettes found; nothing to create.")
            return

        existing_names = set(SurveyColorPalette.objects.values_list('name', flat=True))
        created_count = 0

        for index, data in enumerate(found.values(), start=1):
            name = f'default_{index}'
            if name in existing_names:
                self.stdout.write(self.style.WARNING(
                    f'{name} already exists; skipped (surveys {data["surveys"]}).'
                ))
                continue

            if dry_run:
                self.stdout.write(
                    f'[dry-run] would create {name} from surveys {data["surveys"]}: '
                    f'{json.dumps(data["colors"])}'
                )
                continue

            SurveyColorPalette.objects.create(name=name, colors=data['colors'])
            created_count += 1
            self.stdout.write(self.style.SUCCESS(
                f'Created {name} from surveys {data["surveys"]}.'
            ))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'Done. {created_count} palette(s) created, {len(found)} distinct found.'
            ))
