"""Tenant URLs: django-tenants serves these under /t/<slug>/ (config/urls_public.py: the rest)."""

from django.urls import include, path

urlpatterns = [
    path("", include("monitors.urls")),
]
