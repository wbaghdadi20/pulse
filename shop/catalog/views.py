from django.http import JsonResponse

from .models import Product


def products(request):
    """The catalogue: one real query, so a dead database shows up here too."""
    rows = Product.objects.order_by("name").values("name", "price_cents", "in_stock")
    return JsonResponse({"products": list(rows)})
