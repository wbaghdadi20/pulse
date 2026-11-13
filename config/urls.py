from django.urls import include, path

from core.views import HealthView, ReadyView

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("ready", ReadyView.as_view(), name="ready"),
    path("", include("accounts.urls")),
    path("", include("monitors.urls")),
]
