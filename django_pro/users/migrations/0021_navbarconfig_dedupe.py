from django.db import migrations


def dedupe_navbar_config(apps, schema_editor):
    """
    NavbarConfig is a singleton, but duplicate rows with the same id can appear
    if the table was ever restored without its primary/unique index. Keep the
    newest row for each id and drop the rest.
    """
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM users_navbarconfig a
             WHERE a.ctid <> (
                   SELECT b.ctid
                     FROM users_navbarconfig b
                    WHERE b.id = a.id
                    ORDER BY b.updated_at DESC NULLS LAST, b.ctid
                    LIMIT 1
                   )
            """
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0020_page_menu_category'),
    ]

    operations = [
        migrations.RunPython(dedupe_navbar_config, noop),
        migrations.RunSQL(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_navbarconfig_id_key ON users_navbarconfig (id)",
            reverse_sql="DROP INDEX IF EXISTS users_navbarconfig_id_key",
        ),
    ]
