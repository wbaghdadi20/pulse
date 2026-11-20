from django.db import migrations


def create_public_tenant(apps, schema_editor):
    """The row django-tenants knows the public schema by. Its slug is reserved, so
    no organization can take it."""
    Organization = apps.get_model("orgs", "Organization")
    Organization.objects.get_or_create(
        schema_name="public", defaults={"name": "public", "slug": "public"}
    )


class Migration(migrations.Migration):
    dependencies = [
        ("orgs", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_public_tenant, migrations.RunPython.noop),
    ]
