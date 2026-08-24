from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0021_datavizpage_folder'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataVizPageFolder',
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
