from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dataLoader', '0005_add_response_colors'),
    ]

    operations = [
        migrations.CreateModel(
            name='SurveyTheme',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('color', models.CharField(default='#6366f1', help_text='Hex color code for this theme badge (e.g. #6366f1)', max_length=7)),
                ('survey_definition', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='themes',
                    to='dataLoader.surveydefinition',
                )),
            ],
            options={
                'verbose_name': 'Survey Theme',
                'verbose_name_plural': 'Survey Themes',
                'ordering': ['name'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='surveytheme',
            unique_together={('survey_definition', 'name')},
        ),
        migrations.AlterField(
            model_name='surveyquestion',
            name='domain',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text="Legacy free-text domain/category. Use 'theme' for new assignments.",
            ),
        ),
        migrations.AddField(
            model_name='surveyquestion',
            name='theme',
            field=models.ForeignKey(
                blank=True,
                help_text='Theme/category for this question (replaces the domain field)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='questions',
                to='dataLoader.surveytheme',
            ),
        ),
    ]
