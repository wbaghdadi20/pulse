"""The open-incident rule is per (monitor, kind), and Pulse knows one kind so far."""

import pytest
from django.urls import reverse

from monitors.errors import IncidentAlreadyOpen
from monitors.models import Incident
from monitors.services import open_incident_for

pytestmark = pytest.mark.django_db


def test_the_same_kind_cannot_be_open_twice(monitor):
    open_incident_for(monitor, kind=Incident.Kind.AVAILABILITY)

    with pytest.raises(IncidentAlreadyOpen) as exc_info:
        open_incident_for(monitor, kind=Incident.Kind.AVAILABILITY)

    assert exc_info.value.kind == Incident.Kind.AVAILABILITY


def test_open_incidents_of_different_kinds_coexist(monitor):
    """Schema test: the partial unique index covers (monitor, kind), not monitor alone.

    Django enforces `choices` only in full_clean() and serializers; create() and
    Postgres accept any string. A made-up kind proves the index shape without
    pretending Pulse can open such an incident.
    """
    open_incident_for(monitor, kind=Incident.Kind.AVAILABILITY)
    open_incident_for(monitor, kind="not-a-real-kind")

    assert (
        Incident.objects.filter(monitor=monitor, resolved_at__isnull=True).count() == 2
    )


def test_unknown_kind_is_rejected_by_the_api(api, monitor):
    url = reverse("monitor-incidents", args=[monitor.pk])
    response = api.post(url, {"kind": "latency"}, format="json")

    assert response.status_code == 400
    assert "kind" in response.json()
    assert not Incident.objects.filter(monitor=monitor).exists()


def test_only_availability_exists_until_detection_is_per_kind():
    """Tripwire. Adding a kind means evaluate_incidents() must learn to detect it:
    today it hardcodes AVAILABILITY and reads one signal, Check.ok. Teach it the
    new kind's signal, then update this list."""
    assert Incident.Kind.values == ["availability"]
