from typing import Any

from django.db.models import Model, QuerySet
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import APIView


class DefaultPagination(LimitOffsetPagination):
    """?limit=20&offset=0 -> {"count": total, "next": url, "previous": url, "results": [...]}"""

    default_limit = 20
    max_limit = 100


class AfterIdPagination:
    """?after=<last id seen>&limit=20 -> {"next": url, "results": [...]}

    Keyset pagination, ordered by id: the next page starts after the last row of
    this one. Why it exists next to limit/offset: docs/adr/0011-keyset-pagination.md

    Same two methods as a DRF pagination class, but not one: it needs a queryset of
    model instances to filter in SQL, which is narrower than DRF's contract.
    """

    def paginate_queryset[M: Model](
        self, queryset: QuerySet[M], request: Request, view: APIView | None = None
    ) -> list[M]:
        self.request = request
        after = self._after(request)
        limit = DefaultPagination().get_limit(request)
        assert limit is not None, "DefaultPagination always has a default_limit"
        # One row more than asked for: if it is there, there is a next page. No COUNT.
        rows = list(queryset.filter(pk__gt=after).order_by("pk")[: limit + 1])
        self.next_after = rows[limit - 1].pk if len(rows) > limit else None
        return rows[:limit]

    def get_paginated_response(self, data: Any) -> Response:
        return Response({"next": self._next_link(), "results": data})

    def _next_link(self) -> str | None:
        if self.next_after is None:
            return None
        url = self.request.build_absolute_uri()
        return str(replace_query_param(url, "after", self.next_after))

    @staticmethod
    def _after(request: Request) -> int:
        try:
            after = int(request.query_params["after"])
        except ValueError:
            after = -1
        if after < 0:
            raise ValidationError(
                {"after": ["Must be the id of the last row seen, or 0 to start."]}
            )
        return after
