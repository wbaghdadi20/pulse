"""Tenant URLs: the middleware serves these under /t/<slug>/, in that organization's
schema. The rest, about the person or the process, are in config/urls_public.py."""

from django.urls import include, path

urlpatterns = [
    path("", include("monitors.urls")),
]
