from core.errors import ConflictError, NotFoundError


class MonitorNotFound(NotFoundError):
    def __init__(self, pk: int) -> None:
        super().__init__(f"Monitor {pk} not found.")
        self.pk = pk


class MonitorNameTaken(ConflictError):
    def __init__(self, name: str) -> None:
        super().__init__(f'A monitor named "{name}" already exists.')
        self.name = name


class IncidentAlreadyOpen(ConflictError):
    def __init__(self, monitor_id: int) -> None:
        super().__init__(f"Monitor {monitor_id} already has an open incident.")
        self.monitor_id = monitor_id
