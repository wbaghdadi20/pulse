import itertools

# In-memory storage for D2. Replaced by Postgres in D3.
MONITORS: dict[int, dict] = {}
_ids = itertools.count(1)


def next_id() -> int:
    return next(_ids)


def name_taken(name: str, exclude_id: int | None = None) -> bool:
    """True if another monitor already uses this name."""
    return any(
        monitor["name"] == name and monitor["id"] != exclude_id
        for monitor in MONITORS.values()
    )
