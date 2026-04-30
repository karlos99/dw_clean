from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('dataLoader', '0006_add_survey_theme'),
    ]

    operations = [
        # Rename the model class (renames the DB table)
        migrations.RenameModel(
            old_name='SurveyTheme',
            new_name='SurveyTopic',
        ),
        # Rename the FK field on SurveyQuestion
        migrations.RenameField(
            model_name='surveyquestion',
            old_name='theme',
            new_name='topic',
        ),
    ]
