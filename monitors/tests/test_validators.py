import pytest
from django.core.exceptions import ValidationError

from monitors.validators import validate_http_url


@pytest.mark.parametrize(
    "url",
    [
        "http://django:8000/ht/",  # Docker service name, no dot: must be accepted
        "https://example.com/path?q=1",
        "http://localhost:9",
        "http://10.0.0.5/",
    ],
)
def test_accepts_http_urls_including_docker_hostnames(url):
    validate_http_url(url)  # no exception


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.com", "http://"),
        ("example.com", "http://"),  # no scheme at all
        ("http://", "hostname"),
        ("http:///path", "hostname"),
        ("http://host:abc/", "port"),
    ],
)
def test_rejects_bad_urls_with_a_clear_message(url, message):
    with pytest.raises(ValidationError, match=message):
        validate_http_url(url)
