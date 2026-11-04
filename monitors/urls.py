from django.urls import path

from .views import (
    MonitorCheckView,
    MonitorDetailView,
    MonitorListView,
    MonitorUptimeView,
)

urlpatterns = [
    path("monitors/", MonitorListView.as_view(), name="monitor-list"),
    path("monitors/<int:pk>/", MonitorDetailView.as_view(), name="monitor-detail"),
    path("monitors/<int:pk>/check/", MonitorCheckView.as_view(), name="monitor-check"),
    path(
        "monitors/<int:pk>/uptime/", MonitorUptimeView.as_view(), name="monitor-uptime"
    ),
]
