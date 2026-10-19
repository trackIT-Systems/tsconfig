"""The interface every collector implements."""

import asyncio
from typing import Any, ClassVar

from pydantic import BaseModel

Reading = dict[str, Any]


class Unavailable(Exception):
    """This device cannot provide the collector's data (e.g. no WittyPi board)."""


class Collector:
    """Reads one kind of data; the daemon calls `collect()` every `settings.interval` seconds.

    `columns` fixes the order (and the CSV header) of what `read()` returns: one
    reading per row, e.g. one per disk. A key missing from a reading is published
    as null; keys not in `columns` are not published.
    """

    name: ClassVar[str]
    columns: ClassVar[tuple[str, ...]]

    def __init__(self, settings: BaseModel):
        self.settings = settings

    def check(self) -> None:
        """Raise `Unavailable` when the device lacks what this collector reads. Called once, at start."""

    def read(self) -> list[Reading]:
        """Take the readings; may block, it runs in a worker thread."""
        raise NotImplementedError

    async def collect(self) -> list[Reading]:
        return await asyncio.to_thread(self.read)
