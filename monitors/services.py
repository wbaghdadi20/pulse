"""Business logic for monitors.

No HTTP in here: views call these functions today, Celery tasks will from D6.
Convention: get_monitor() turns an id into a monitor (or raises MonitorNotFound);
every other function takes the monitor itself.
"""

from . import store
from .checks import run_check
from .errors import MonitorNameTaken, MonitorNotFound


def list_monitors() -> list[dict]:
    return list(store.MONITORS.values())


def get_monitor(pk: int) -> dict:
    try:
        return store.MONITORS[pk]
    except KeyError:
        raise MonitorNotFound(pk) from None


def create_monitor(data: dict) -> dict:
    _ensure_name_free(data["name"])
    monitor = {"id": store.next_id(), **data}
    store.MONITORS[monitor["id"]] = monitor
    return monitor


def update_monitor(monitor: dict, changes: dict) -> dict:
    if "name" in changes:
        _ensure_name_free(changes["name"], exclude_id=monitor["id"])
    monitor.update(changes)
    return monitor


def delete_monitor(monitor: dict) -> None:
    del store.MONITORS[monitor["id"]]


def check_monitor(monitor: dict) -> dict:
    return run_check(monitor)


def _ensure_name_free(name: str, exclude_id: int | None = None) -> None:
    if store.name_taken(name, exclude_id):
        raise MonitorNameTaken(name)
