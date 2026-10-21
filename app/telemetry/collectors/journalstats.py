"""How many journal entries each unit logged per level, and how many the limits held back.

The counts come from the journal source (app.telemetry.journal), so this
collector needs it to run; with `journal.enabled` off it is skipped.
"""

from app.telemetry.collectors.base import Collector, Reading, Unavailable
from app.telemetry.journal import JournalCounts


class JournalStatsCollector(Collector):
    name = "journalstats"
    columns = ("unit", "level", "entries", "dropped")
    # Set by the daemon when it follows the journal
    counts: JournalCounts | None = None

    def check(self) -> None:
        if self.counts is None:
            raise Unavailable("the journal is not followed")

    def read(self) -> list[Reading]:
        return self.counts.take() if self.counts else []

    async def collect(self) -> list[Reading]:
        # The counts are updated on the event loop, so they are read there, not in a worker thread
        return self.read()
