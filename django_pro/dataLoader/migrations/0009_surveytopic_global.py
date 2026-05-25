"""
Migration: make SurveyTopic global (remove survey_definition FK).

Data migration strategy:
  - When multiple topics share the same name across different surveys, keep one
    and re-point all SurveyQuestion.topic FK references to that surviving row,
    then delete the duplicates.
"""
from django.db import migrations, models


def deduplicate_topics(apps, schema_editor):
    SurveyTopic = apps.get_model('dataLoader', 'SurveyTopic')
    SurveyQuestion = apps.get_model('dataLoader', 'SurveyQuestion')

    seen = {}  # name -> topic id to keep

    for topic in SurveyTopic.objects.order_by('id'):
        name_lower = topic.name.strip().lower()
        if name_lower not in seen:
            seen[name_lower] = topic.id
        else:
            # Re-point questions that reference this duplicate to the surviving topic
            SurveyQuestion.objects.filter(topic_id=topic.id).update(
                topic_id=seen[name_lower]
            )
            topic.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dataLoader', '0008_surveyresponse_school_code'),
    ]

    operations = [
        # Step 1: run data migration to deduplicate topic names
        migrations.RunPython(deduplicate_topics, migrations.RunPython.noop),

        # Step 2: remove the survey_definition FK column
        migrations.RemoveField(
            model_name='surveytopic',
            name='survey_definition',
        ),

        # Step 3: replace unique_together with a plain unique constraint on name
        migrations.AlterField(
            model_name='surveytopic',
            name='name',
            field=models.CharField(max_length=100, unique=True),
        ),
    ]
