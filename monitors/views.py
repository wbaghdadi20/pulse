"""HTTP layer only: parse and validate input, call a service, shape the response.

Domain errors raised by services (MonitorNotFound, MonitorNameTaken) become
404 / 409 in core/exception_handler.py, so there is no try/except here.
"""

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import DefaultPagination

from . import services
from .serializers import IncidentSerializer, MonitorSerializer, UptimeQuerySerializer


class MonitorListView(APIView):
    """/monitors/ : the collection."""

    def get(self, request: Request) -> Response:
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(services.list_monitors(), request, view=self)
        return paginator.get_paginated_response(MonitorSerializer(page, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = MonitorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        monitor = services.create_monitor(serializer.validated_data)
        return Response(MonitorSerializer(monitor).data, status=status.HTTP_201_CREATED)


class MonitorDetailView(APIView):
    """/monitors/<pk>/ : one monitor."""

    def get(self, request: Request, pk: int) -> Response:
        return Response(MonitorSerializer(services.get_monitor(pk)).data)

    def patch(self, request: Request, pk: int) -> Response:
        monitor = services.get_monitor(pk)  # 404 before validating the body
        serializer = MonitorSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        monitor = services.update_monitor(monitor, serializer.validated_data)
        return Response(MonitorSerializer(monitor).data)

    def delete(self, request: Request, pk: int) -> Response:
        services.delete_monitor(services.get_monitor(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class MonitorCheckView(APIView):
    """/monitors/<pk>/check/ : run one check now."""

    def post(self, request: Request, pk: int) -> Response:
        # Always 200: a down target is a result, not an error in Pulse.
        return Response(services.check_monitor(services.get_monitor(pk)))


class MonitorUptimeView(APIView):
    """/monitors/<pk>/uptime/?days=7|30|90"""

    def get(self, request: Request, pk: int) -> Response:
        monitor = services.get_monitor(pk)
        query = UptimeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.get_uptime(monitor, query.validated_data["days"]))


class MonitorIncidentListView(APIView):
    """/monitors/<pk>/incidents/ : open an incident by hand (e.g. planned outage)."""

    def post(self, request: Request, pk: int) -> Response:
        monitor = services.get_monitor(pk)
        serializer = IncidentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        incident = services.open_incident(
            monitor, serializer.validated_data.get("cause", "")
        )
        return Response(
            IncidentSerializer(incident).data, status=status.HTTP_201_CREATED
        )
