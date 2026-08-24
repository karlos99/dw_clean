from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0008_accommodation_dependencies'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentaccommodation',
            name='selected_accommodations',
            field=models.ManyToManyField(
                blank=True,
                help_text='Supports with no configured options, stored as simple checkbox selections.',
                related_name='students_with_direct_support',
                to='desi.accommodation',
            ),
        ),
    ]
