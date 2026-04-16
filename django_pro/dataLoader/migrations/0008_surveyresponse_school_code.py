from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataLoader', '0007_rename_theme_to_topic'),
    ]

    operations = [
        migrations.AddField(
            model_name='surveyresponse',
            name='school_code',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='School ID from the CSV (references school.sis_key)',
                max_length=50,
            ),
            preserve_default=False,
        ),
    ]
