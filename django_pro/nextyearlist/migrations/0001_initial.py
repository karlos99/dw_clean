from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='NextYearPlacement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ssid', models.CharField(db_index=True, max_length=20)),
                ('student_id', models.CharField(blank=True, db_index=True, max_length=20)),
                ('stu_annual_key', models.CharField(blank=True, db_index=True, max_length=32)),
                ('school_year', models.CharField(db_index=True, max_length=20)),
                ('next_year_school_state_id', models.CharField(db_index=True, max_length=20)),
                ('next_year_school_name', models.CharField(blank=True, max_length=255)),
            ],
            options={
                'verbose_name': 'Next Year Placement',
                'verbose_name_plural': 'Next Year Placements',
                'db_table': 'nextyearlist_placement',
            },
        ),
        migrations.AlterUniqueTogether(
            name='nextyearplacement',
            unique_together={('ssid', 'school_year')},
        ),
        migrations.AddIndex(
            model_name='nextyearplacement',
            index=models.Index(fields=['school_year', 'next_year_school_state_id'], name='nyl_year_school_idx'),
        ),
    ]
