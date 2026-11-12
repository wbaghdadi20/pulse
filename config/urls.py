from django.urls import include, path

from core.views import health

urlpatterns = [
    path("health", health, name="health"),
    path("", include("monitors.urls")),
]
