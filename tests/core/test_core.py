import pytest
from rest_framework.test import APIClient

from core.db import PercentileCont


def test_health_is_public_and_ok():
    response = APIClient().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "broken on purpose"}


def test_health_rejects_other_methods():
    assert APIClient().post("/health").status_code == 405


@pytest.mark.parametrize("fraction", [-0.1, 1.5])
def test_percentile_fraction_must_be_between_0_and_1(fraction):
    with pytest.raises(ValueError):
        PercentileCont("latency_ms", fraction)
