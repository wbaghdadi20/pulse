from django.db import migrations, models


def seed(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.bulk_create(
        [
            Product(name="coffee beans 1kg", price_cents=1899),
            Product(name="french press", price_cents=2950),
            Product(name="grinder", price_cents=6400, in_stock=False),
        ]
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("price_cents", models.PositiveIntegerField()),
                ("in_stock", models.BooleanField(default=True)),
            ],
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
