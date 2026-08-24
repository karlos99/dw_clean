from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0020_merge_0007_page_tabs_0019_dataset_source_sql'),
    ]

    operations = [
        migrations.AddField(
            model_name='datavizpage',
            name='folder',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
