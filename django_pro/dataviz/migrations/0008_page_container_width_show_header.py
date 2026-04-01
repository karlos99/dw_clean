from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0007_add_source_columns_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='datavizpage',
            name='container_width',
            field=models.CharField(
                choices=[
                    ('full', 'Full width'),
                    ('wide', 'Wide (1536px)'),
                    ('xl', 'Standard (1280px)'),
                    ('lg', 'Narrow (1024px)'),
                    ('md', 'Compact (768px)'),
                ],
                default='xl',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='datavizpage',
            name='show_header',
            field=models.BooleanField(default=True),
        ),
    ]
