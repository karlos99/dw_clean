from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0032_alter_supportdefaultrule_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='supportdefaultrulecriteria',
            name='result_other_info_key',
            field=models.CharField(blank=True, help_text="results.other_info JSON key when result_field is 'additional_info'", max_length=255),
        ),
        migrations.AlterField(
            model_name='supportdefaultrulecriteria',
            name='result_field',
            field=models.CharField(blank=True, choices=[('pl', 'Performance Level'), ('ss', 'Scale Score'), ('additional_info', 'Additional Info')], default='pl', max_length=20),
        ),
    ]
