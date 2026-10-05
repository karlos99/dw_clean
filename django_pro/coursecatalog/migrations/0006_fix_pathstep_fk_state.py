# Repair migration state: path steps must reference CatalogPath, not deleted legacy model.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coursecatalog', '0005_taxonomy_tag_category_discipline'),
    ]

    operations = [
        migrations.AlterField(
            model_name='catalogpathstep',
            name='path',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='steps',
                to='coursecatalog.catalogpath',
            ),
        ),
    ]
