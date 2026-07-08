from django.db import migrations


def seed_accommodations(apps, schema_editor):
    Accommodation = apps.get_model('desi', 'Accommodation')
    
    supports = [
        {
            'name': 'Text-to-Speech',
            'code': 'TTS',
            'category': 'Presentation',
            'description': 'Text is read aloud to the student via screen-reading software.'
        },
        {
            'name': 'Extra Time 1.5x',
            'code': 'ET1.5',
            'category': 'Timing',
            'description': 'Student is allowed up to 1.5 times the standard testing time.'
        },
        {
            'name': 'Extra Time 2.0x',
            'code': 'ET2.0',
            'category': 'Timing',
            'description': 'Student is allowed up to 2.0 times the standard testing time.'
        },
        {
            'name': 'Separate Setting',
            'code': 'SS',
            'category': 'Setting',
            'description': 'Student takes the test in a separate room to minimize distractions.'
        },
        {
            'name': 'Simplified Directions',
            'code': 'SD',
            'category': 'Presentation',
            'description': 'Test directions are simplified and paraphrased for clarity.'
        },
        {
            'name': 'Read Aloud (Human Reader)',
            'code': 'RA',
            'category': 'Presentation',
            'description': 'A human reader reads test questions and prompts aloud to the student.'
        },
        {
            'name': 'Bilingual Dictionary',
            'code': 'BD',
            'category': 'Response',
            'description': 'Student is allowed a bilingual word-to-word dictionary (for ELLs).'
        },
        {
            'name': 'Calculator',
            'code': 'CALC',
            'category': 'Response',
            'description': 'Student is allowed a calculator on non-calculator sections where appropriate.'
        },
    ]

    for s in supports:
        Accommodation.objects.get_or_create(
            name=s['name'],
            defaults={
                'code': s['code'],
                'category': s['category'],
                'description': s['description']
            }
        )


def remove_accommodations(apps, schema_editor):
    Accommodation = apps.get_model('desi', 'Accommodation')
    Accommodation.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_accommodations, remove_accommodations),
    ]
