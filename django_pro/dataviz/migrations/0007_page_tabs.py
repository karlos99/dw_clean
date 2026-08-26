from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0006_add_page_row_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataVizPageTab',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('position', models.IntegerField(default=0)),
                ('page', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tabs', to='dataviz.datavizpage')),
            ],
            options={'ordering': ['position', 'name']},
        ),
        migrations.AddField(
            model_name='datavizpagerow',
            name='tab',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rows', to='dataviz.datavizpagetab'),
        ),
    ]
