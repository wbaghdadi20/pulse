from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=100, unique=True)
    price_cents = models.PositiveIntegerField()
    in_stock = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name
