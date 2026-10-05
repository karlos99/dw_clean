from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0028_alter_assessment_options_assessment_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='accommodation',
            name='hide_from_student_list',
            field=models.BooleanField(
                default=False,
                help_text='Hide this support on the student workspace matrix; export still includes it.',
            ),
        ),
    ]
