"""URLs outside /t/<slug>/: about the person or the process, not a tenant."""

from django.urls import include, path

from core.views import HealthView, ReadyView

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("ready", ReadyView.as_view(), name="ready"),
    path("", include("accounts.urls")),
]
