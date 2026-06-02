# Generated migration: add school_filter_mode to SavedChart
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0008_page_container_width_show_header'),
    ]

    operations = [
        migrations.AddField(
            model_name='savedchart',
            name='school_filter_mode',
            field=models.CharField(
                blank=True,
                choices=[('enrolled', 'Enrolled School'), ('tested', 'Tested School')],
                default='enrolled',
                max_length=10,
            ),
        ),
    ]
