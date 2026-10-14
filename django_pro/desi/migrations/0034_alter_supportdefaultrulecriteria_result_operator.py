from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0033_supportdefaultrulecriteria_result_other_info_key'),
    ]

    operations = [
        migrations.AlterField(
            model_name='supportdefaultrulecriteria',
            name='result_operator',
            field=models.CharField(blank=True, choices=[('in', 'In List'), ('not_in', 'Not In List'), ('is', 'Equals'), ('is_not', 'Does Not Equal'), ('between', 'Between (min-max)'), ('gt', 'Greater Than'), ('gte', 'Greater or Equal'), ('lt', 'Less Than'), ('lte', 'Less or Equal')], default='in', max_length=20),
        ),
    ]
