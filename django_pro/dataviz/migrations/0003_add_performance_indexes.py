from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0002_dataset_distinct'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='savedchart',
            index=models.Index(fields=['source_type', 'dataset_id'], name='dv_sc_source_ds_idx'),
        ),
        migrations.AddIndex(
            model_name='savedchart',
            index=models.Index(fields=['owner_id', 'source_type'], name='dv_sc_owner_src_idx'),
        ),
        migrations.AddIndex(
            model_name='datavizpagesection',
            index=models.Index(fields=['page_id', 'element_type'], name='dv_dvps_page_el_idx'),
        ),
        migrations.AddIndex(
            model_name='datavizpage',
            index=models.Index(fields=['status', 'slug'], name='dv_dvp_status_slug_idx'),
        ),
    ]
