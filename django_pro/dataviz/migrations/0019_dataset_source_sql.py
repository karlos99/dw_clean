from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0018_dashboard_is_home_savedchart_is_user_menu_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='source_type',
            field=models.CharField(
                choices=[('visual', 'Visual Join'), ('sql', 'Custom SQL')],
                default='visual',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='dataset',
            name='source_sql',
            field=models.TextField(blank=True),
        ),
    ]
