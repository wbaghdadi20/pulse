from rest_framework.pagination import LimitOffsetPagination


class DefaultPagination(LimitOffsetPagination):
    """?limit=20&offset=0 -> {"count": total, "next": url, "previous": url, "results": [...]}"""

    default_limit = 20
    max_limit = 100
