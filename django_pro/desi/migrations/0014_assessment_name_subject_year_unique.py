from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0013_merge_20260821_1714'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='assessment',
            unique_together={('name', 'subject', 'school_year')},
        ),
    ]
