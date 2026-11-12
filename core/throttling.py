"""Rate limits as DRF throttles: DRF calls them on every request and turns a refusal
into 429 with a Retry-After header. The counting itself is in core/ratelimit.py."""

from typing import TYPE_CHECKING

from django.conf import settings
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle

from .ratelimit import Limiter, fixed_window

if TYPE_CHECKING:
    # rest_framework.views imports this module (DEFAULT_THROTTLE_CLASSES) while it
    # is still loading, so it cannot be imported back at runtime.
    from rest_framework.views import APIView


class RateLimitThrottle(BaseThrottle):
    """PULSE_RATE_LIMIT_PER_MINUTE requests a minute for each client address."""

    scope = "api"
    limit_setting = "PULSE_RATE_LIMIT_PER_MINUTE"
    # The strategy. staticmethod: a plain function here would be bound as a method.
    limiter: Limiter = staticmethod(fixed_window)

    def allow_request(self, request: Request, view: "APIView") -> bool:
        # get_ident is DRF's: the connection's address, or the one a trusted proxy
        # reported when NUM_PROXIES says there is one.
        client = f"{self.scope}:{self.get_ident(request)}"
        decision = self.limiter(client, getattr(settings, self.limit_setting))
        self.retry_after = decision.retry_after
        return decision.allowed

    def wait(self) -> float:
        return self.retry_after


class AuthRateThrottle(RateLimitThrottle):
    """The stricter limit on /login and /signup: each request there costs a password
    hash, and /login is where passwords are guessed. Counted apart from the rest."""

    scope = "auth"
    limit_setting = "PULSE_AUTH_RATE_LIMIT_PER_MINUTE"
