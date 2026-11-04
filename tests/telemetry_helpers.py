"""A controllable clock for the telemetry tests."""

import datetime

from app.telemetry.samples import Sample


class FakeClock:
    """A wall clock that only moves when told to.

    `time` starts wrong (as on a station without a trusted source) until `sync()` sets it.
    """

    def __init__(self, time: float, boot_id: str = "boot-a", synced: bool = False):
        self._time = time
        self._boot_id = boot_id
        self._synced = synced

    def time(self) -> float:
        return self._time

    def boot_id(self) -> str:
        return self._boot_id

    def synced(self) -> bool:
        return self._synced

    def advance(self, seconds: float) -> None:
        self._time += seconds

    def sync(self, time: float) -> None:
        """chrony steps the wall clock."""
        self._time = time
        self._synced = True


def local(*args) -> datetime.datetime:
    return datetime.datetime(*args).astimezone()


def sample(clock: FakeClock, collector: str = "system", values: tuple = (1.5, 2)) -> Sample:
    """A sample stamped like the daemon stamps it."""
    return Sample(
        collector=collector,
        columns=("a", "b"),
        values=values,
        time=datetime.datetime.fromtimestamp(round(clock.time())).astimezone(),
        synced=clock.synced(),
    )
