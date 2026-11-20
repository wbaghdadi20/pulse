"""HTTP layer only: parse and validate input, call a service, shape the response.

Domain errors raised by services (MonitorNotFound, MonitorNameTaken) become
404 / 409 in core/exception_handler.py, so there is no try/except here.
"""

import secrets

from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.utils.cache import get_conditional_response
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import current_membership
from core.pagination import AfterIdPagination, DefaultPagination
from core.views import PublicAPIView

from . import services, status_page
from .models import Monitor
from .serializers import IncidentSerializer, MonitorSerializer, UptimeQuerySerializer

# How often the live page asks. Most answers are a 304, so asking often is cheap.
LIVE_POLL_SECONDS = 5


def own_monitor(request: Request, pk: int) -> Monitor:
    """The caller's monitor with this id. 404 if there is none, or it is assigned to
    someone else."""
    return services.get_monitor(pk, current_membership(request))


class MonitorListView(APIView):
    """/monitors/ : the monitors assigned to the caller."""

    def get(self, request: Request) -> Response:
        paginator: AfterIdPagination | DefaultPagination = (
            AfterIdPagination()
            if "after" in request.query_params
            else DefaultPagination()
        )
        monitors = services.list_monitors(current_membership(request))
        page = paginator.paginate_queryset(monitors, request, view=self)
        return paginator.get_paginated_response(MonitorSerializer(page, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = MonitorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        monitor = services.create_monitor(
            current_membership(request), serializer.validated_data
        )
        return Response(MonitorSerializer(monitor).data, status=status.HTTP_201_CREATED)


class MonitorDetailView(APIView):
    """/monitors/<pk>/ : one monitor."""

    def get(self, request: Request, pk: int) -> Response:
        return Response(MonitorSerializer(own_monitor(request, pk)).data)

    def patch(self, request: Request, pk: int) -> Response:
        monitor = own_monitor(request, pk)  # 404 before validating the body
        serializer = MonitorSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        monitor = services.update_monitor(monitor, serializer.validated_data)
        return Response(MonitorSerializer(monitor).data)

    def delete(self, request: Request, pk: int) -> Response:
        services.delete_monitor(own_monitor(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class MonitorCheckView(APIView):
    """/monitors/<pk>/check/ : run one check now."""

    def post(self, request: Request, pk: int) -> Response:
        # Always 200: a down target is a result, not an error in Pulse.
        return Response(services.check_monitor(own_monitor(request, pk)))


class MonitorUptimeView(APIView):
    """/monitors/<pk>/uptime/?days=7|30|90"""

    def get(self, request: Request, pk: int) -> Response:
        monitor = own_monitor(request, pk)
        query = UptimeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.get_uptime(monitor, query.validated_data["days"]))


class MonitorIncidentListView(APIView):
    """/monitors/<pk>/incidents/ : open an incident by hand (e.g. planned outage)."""

    def post(self, request: Request, pk: int) -> Response:
        monitor = own_monitor(request, pk)
        serializer = IncidentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        incident = services.open_incident_for(monitor, **serializer.validated_data)
        return Response(
            IncidentSerializer(incident).data, status=status.HTTP_201_CREATED
        )


class StatusPageView(PublicAPIView):
    """/status : public. 304 with no body when the caller's copy still holds."""

    def get(self, request: Request) -> HttpResponseBase:
        page, etag = status_page.get_status_page()
        # Django's reading of If-None-Match: a list of tags, weak ones, and "*".
        response = get_conditional_response(request._request, etag=etag) or Response(
            page
        )
        response["ETag"] = etag
        # A copy may be kept, but has to be checked with Pulse before each use.
        response["Cache-Control"] = "no-cache"
        return response


@require_GET
def status_live(request: HttpRequest) -> HttpResponse:
    """/status/live : the status page for a browser. The HTML holds no data: its
    script asks /status every few seconds and redraws when the answer changes."""
    nonce = secrets.token_urlsafe(16)
    response = render(
        request,
        "monitors/status_live.html",
        {"nonce": nonce, "poll_seconds": LIVE_POLL_SECONDS},
    )
    # Only this page's own script and style run, and it may only call Pulse.
    response["Content-Security-Policy"] = (
        "default-src 'none'; connect-src 'self'; base-uri 'none'; "
        f"frame-ancestors 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'"
    )
    return response
