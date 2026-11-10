from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0023_dashboardchart_is_forced'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='folder',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.CreateModel(
            name='DataVizDatasetFolder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.CharField(max_length=500, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['path'],
            },
        ),
    ]
