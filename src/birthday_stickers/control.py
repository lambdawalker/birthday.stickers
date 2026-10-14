"""Cooperative controls checked between durable pipeline stages."""

from threading import Event


class RunHalted(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


class RunControl:
    def __init__(self):
        self.requested = Event()
        self.status = "paused"

    def request(self, status: str = "paused") -> None:
        self.status = status
        self.requested.set()

    def check(self) -> None:
        if self.requested.is_set():
            raise RunHalted(self.status)
