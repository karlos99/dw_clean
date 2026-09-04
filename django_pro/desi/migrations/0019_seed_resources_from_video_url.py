from django.db import migrations


def seed_resources_from_video_url(apps, schema_editor):
    Accommodation = apps.get_model('desi', 'Accommodation')
    Resource = apps.get_model('desi', 'AccommodationResource')

    for accommodation in Accommodation.objects.exclude(video_url__isnull=True).exclude(video_url=''):
        if not Resource.objects.filter(accommodation=accommodation, kind='youtube').exists():
            Resource.objects.create(
                accommodation=accommodation,
                label='Video Guide',
                url=accommodation.video_url,
                kind='youtube',
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0018_accommodationresource_desischoolaccess_supportrule_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_resources_from_video_url, noop),
    ]
