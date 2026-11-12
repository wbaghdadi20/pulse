import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Needs an empty monitors table: a monitor from before accounts has no owner to get."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("monitors", "0008_monitor_next_check_at"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="monitor",
            name="uq_monitor_name",
        ),
        migrations.AddField(
            model_name="monitor",
            name="is_public",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="monitor",
            name="owner",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="monitors",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="monitor",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"), name="uq_monitor_owner_name"
            ),
        ),
    ]
