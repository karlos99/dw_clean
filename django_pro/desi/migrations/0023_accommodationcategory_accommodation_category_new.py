import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Step 1 of 3 for introducing the reusable AccommodationCategory model.

    This migration is intentionally non-destructive: it only adds the new
    ``AccommodationCategory`` table and a nullable ``category_new`` FK on
    ``Accommodation``. The existing free-text ``category`` column is left
    untouched so no data is lost.
    """

    dependencies = [
        ('desi', '0022_desiseis_displaysettings'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccommodationCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('order', models.IntegerField(default=0, help_text='Order in which this category is displayed')),
                ('description', models.TextField(blank=True)),
            ],
            options={
                'verbose_name': 'Designated Support Category',
                'verbose_name_plural': 'Designated Support Categories',
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='accommodation',
            name='category_new',
            field=models.ForeignKey(
                blank=True,
                help_text='Category used to group this support in the student workspace.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='accommodations',
                to='desi.accommodationcategory',
            ),
        ),
    ]
