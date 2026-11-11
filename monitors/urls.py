from django.urls import path

from .views import (
    MonitorCheckView,
    MonitorDetailView,
    MonitorIncidentListView,
    MonitorListView,
    MonitorUptimeView,
    StatusPageView,
)

urlpatterns = [
    path("status", StatusPageView.as_view(), name="status-page"),
    path("monitors/", MonitorListView.as_view(), name="monitor-list"),
    path("monitors/<int:pk>/", MonitorDetailView.as_view(), name="monitor-detail"),
    path("monitors/<int:pk>/check/", MonitorCheckView.as_view(), name="monitor-check"),
    path(
        "monitors/<int:pk>/uptime/", MonitorUptimeView.as_view(), name="monitor-uptime"
    ),
    path(
        "monitors/<int:pk>/incidents/",
        MonitorIncidentListView.as_view(),
        name="monitor-incidents",
    ),
]
