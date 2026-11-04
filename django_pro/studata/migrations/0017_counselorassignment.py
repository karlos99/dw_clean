from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('studata', '0016_studentrpc'),
    ]

    operations = [
        migrations.CreateModel(
            name='CounselorAssignment',
            fields=[
                ('assignment_row_key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('school_year_number', models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ('school_year', models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ('student_id', models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ('ssid', models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ('student_last_name', models.CharField(blank=True, max_length=255, null=True)),
                ('student_first_name', models.CharField(blank=True, max_length=255, null=True)),
                ('student_middle_name', models.CharField(blank=True, max_length=255, null=True)),
                ('school_name', models.CharField(blank=True, max_length=255, null=True)),
                ('grade', models.CharField(blank=True, max_length=50, null=True)),
                ('counselor_name', models.CharField(blank=True, max_length=255, null=True)),
                ('counselor_email', models.CharField(blank=True, db_index=True, max_length=255, null=True)),
            ],
            options={
                'verbose_name': 'Counselor Assignment',
                'verbose_name_plural': 'Counselor Assignments',
                'db_table': 'counselor_assigments',
                'managed': False,
                'ordering': ['-school_year_number', 'counselor_name', 'student_last_name', 'student_first_name'],
            },
        ),
    ]
