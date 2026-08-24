from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0019_navbarconfig_navbar_style'),
    ]

    operations = [
        migrations.AddField(
            model_name='page',
            name='is_menu_category',
            field=models.BooleanField(
                default=False,
                help_text='Use this page as a navigation category/container instead of a destination page',
                verbose_name='Menu Category',
            ),
        ),
        migrations.AlterField(
            model_name='page',
            name='url_path',
            field=models.CharField(
                blank=True,
                help_text='Internal URL path; optional when this page is only a menu category',
                max_length=500,
                verbose_name='URL Path',
            ),
        ),
    ]
