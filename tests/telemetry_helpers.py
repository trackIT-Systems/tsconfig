"""A controllable clock for the telemetry tests."""

import datetime

from app.telemetry.samples import Sample


class FakeClock:
    """Wall clock and boot clock that only move when told to.

    `time` starts wrong (as on a station without RTC) until `sync()` sets it.
    """

    def __init__(self, time: float, boottime: float = 100.0, boot_id: str = "boot-a", synced: bool = False):
        self._time = time
        self._boottime = boottime
        self._boot_id = boot_id
        self._synced = synced

    def time(self) -> float:
        return self._time

    def boottime(self) -> float:
        return self._boottime

    def boot_id(self) -> str:
        return self._boot_id

    def synced(self) -> bool:
        return self._synced

    def advance(self, seconds: float) -> None:
        self._time += seconds
        self._boottime += seconds

    def sync(self, time: float) -> None:
        """chrony steps the wall clock; the boot clock is unaffected."""
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
        boottime=clock.boottime(),
    )
