from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0003_slot_filters_html_element'),
        ('users', '0007_page'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- SavedChart: drill-through fields ---
        migrations.AddField(
            model_name='savedchart',
            name='drill_through_columns',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='savedchart',
            name='drill_through_filter_map',
            field=models.JSONField(blank=True, default=dict),
        ),

        # --- DashboardChart: GridStack layout fields ---
        migrations.AddField(
            model_name='dashboardchart',
            name='gs_x',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dashboardchart',
            name='gs_y',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dashboardchart',
            name='gs_w',
            field=models.IntegerField(default=6),
        ),
        migrations.AddField(
            model_name='dashboardchart',
            name='gs_h',
            field=models.IntegerField(default=4),
        ),

        # --- DataVizPage ---
        migrations.CreateModel(
            name='DataVizPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title',       models.CharField(max_length=200)),
                ('slug',        models.SlugField(blank=True, max_length=200, unique=True)),
                ('description', models.TextField(blank=True)),
                ('status',      models.CharField(
                    choices=[('draft', 'Draft'), ('active', 'Active')],
                    default='draft',
                    max_length=20,
                )),
                ('page_filters',  models.JSONField(blank=True, default=list)),
                ('created_at',    models.DateTimeField(auto_now_add=True)),
                ('updated_at',    models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='dataviz_pages',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('linked_site_page', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='dataviz_page',
                    to='users.page',
                )),
            ],
            options={'ordering': ['-updated_at']},
        ),

        # --- DataVizPageSection ---
        migrations.CreateModel(
            name='DataVizPageSection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('element_type', models.CharField(
                    choices=[('dashboard', 'Dashboard'), ('text', 'Text / HTML'), ('divider', 'Divider')],
                    default='dashboard',
                    max_length=20,
                )),
                ('html_content', models.TextField(blank=True)),
                ('position',     models.IntegerField(default=0)),
                ('page', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sections',
                    to='dataviz.datavizpage',
                )),
                ('dashboard', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='page_sections',
                    to='dataviz.dashboard',
                )),
            ],
            options={'ordering': ['position']},
        ),
    ]
