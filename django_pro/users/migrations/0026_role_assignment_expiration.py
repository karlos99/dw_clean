import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def create_limited_student_role(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Role.objects.get_or_create(
        name='Limited Student Access',
        defaults={
            'description': (
                'Grants access only to the students explicitly listed on the user\'s '
                'role assignment (by SSID or local student ID). This role can be '
                'time-boxed with an optional expiration date.'
            ),
            'is_student_limited': True,
        },
    )


def remove_limited_student_role(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Role.objects.filter(name='Limited Student Access').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0025_navbarconfig_submenu_opacity_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='is_student_limited',
            field=models.BooleanField(
                default=False,
                help_text="Restrict this role to the students listed on each user's role assignment.",
            ),
        ),
        # The original implicit M2M table is already called ``users_user_roles``.
        # Reuse it as the through table: only state is created here, then the
        # metadata columns are added to the existing table below.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='RoleAssignment',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('user', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='role_assignments',
                            to='users.user',
                        )),
                        ('role', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='assignments',
                            to='users.role',
                        )),
                    ],
                    options={
                        'db_table': 'users_user_roles',
                        'unique_together': {('user', 'role')},
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='user',
                    name='roles',
                    field=models.ManyToManyField(
                        blank=True,
                        related_name='users',
                        through='users.RoleAssignment',
                        through_fields=('user', 'role'),
                        to='users.role',
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.AddField(
            model_name='roleassignment',
            name='expires_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text='Optional date/time when this role assignment stops being effective.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='roleassignment',
            name='fallback_role',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional role to switch back to when this assignment expires.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fallback_assignments',
                to='users.role',
            ),
        ),
        migrations.AddField(
            model_name='roleassignment',
            name='limited_ssids',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='State Student IDs (SSID) accessible through this limited role.',
            ),
        ),
        migrations.AddField(
            model_name='roleassignment',
            name='limited_student_ids',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Local student IDs accessible through this limited role.',
            ),
        ),
        migrations.AddField(
            model_name='roleassignment',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='roleassignment',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.RunPython(create_limited_student_role, remove_limited_student_role),
    ]
