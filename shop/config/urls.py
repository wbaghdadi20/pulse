"""Three endpoints, three depths: see core/views.py (shallow) and core/checks.py (deep)."""

from django.urls import path
from health_check.views import HealthCheckView

from catalog.views import products
from core.checks import CHECKS
from core.views import health

urlpatterns = [
    path("", products, name="products"),
    path("health", health, name="health"),
    path("ht/", HealthCheckView.as_view(checks=CHECKS), name="deep-health"),
]
