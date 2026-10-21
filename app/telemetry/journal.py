"""Journal entries over MQTT, per unit and level, rate-limited (docs/logging.md, "Forwarding over MQTT").

`journalctl --follow --output=json` runs as a child process; every entry from
`settings.level` up is published as `<host>/tsconfig/journal/<unit>/<level>/<format>`
with the columns `identifier`, `message` and `repeated`. Which units and levels
leave the station is up to the MQTT bridge. The limits here keep the local
volume bounded, counted per level, so `info` never uses up what `err` may send.

The cursor of the last entry read is kept in the state directory, so a restart
continues where the last run stopped, including entries logged while the
daemon was down.
"""

import asyncio
import datetime
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.telemetry.clock import Clock
from app.telemetry.samples import Sample
from app.telemetry.settings import LEVELS, JournalLimits, JournalSettings, Level

logger = logging.getLogger(__name__)

JOURNAL = "journal"
COLUMNS = ("identifier", "message", "repeated")
# __CURSOR, __REALTIME_TIMESTAMP, __MONOTONIC_TIMESTAMP and _BOOT_ID are always included
FIELDS = ("MESSAGE", "PRIORITY", "_SYSTEMD_UNIT", "UNIT", "SYSLOG_IDENTIFIER", "_COMM")

# Seconds between flushing merged repeats, and between saving the cursor
FLUSH_S = 5
SAVE_CURSOR_S = 60
# Seconds to wait before restarting journalctl after it ended
RESTART_S = 10
# A journalctl that ends this soon after starting at a cursor did not accept the cursor
CURSOR_REJECTED_S = 5
# Longest JSON line read from journalctl; MESSAGE is cut to the limits long before
MAX_LINE = 1024 * 1024
# Messages tracked for merging repeats; beyond this, new messages are only rate-limited
MAX_WINDOWS = 10000

# Characters an MQTT topic level must not contain
TOPIC_UNSAFE = str.maketrans({"/": "_", "+": "_", "#": "_"})


@dataclass(frozen=True)
class Entry:
    unit: str
    level: Level
    identifier: str
    message: str
    time: datetime.datetime
    boot_id: str
    # CLOCK_MONOTONIC at the entry, in seconds
    monotonic: float
    cursor: str


def _text(value: Any) -> str:
    """A field's value: journalctl gives binary values as a list of bytes, repeated fields as a list."""
    if value is None:
        return ""
    if isinstance(value, list):
        if value and all(isinstance(byte, int) for byte in value):
            return bytes(value).decode("utf-8", "replace")
        return _text(value[0]) if value else ""
    return str(value)


def unit_of(fields: dict[str, Any]) -> str:
    """The topic level for an entry's unit.

    systemd's own messages about a unit ("tsschedule.service: Failed with
    result ...") come from init.scope and name the unit in UNIT; they belong to
    that unit. Entries without a unit (the kernel) use their SYSLOG_IDENTIFIER.
    """
    unit = _text(fields.get("_SYSTEMD_UNIT"))
    if unit == "init.scope" and fields.get("UNIT"):
        unit = _text(fields["UNIT"])
    if not unit:
        unit = _text(fields.get("SYSLOG_IDENTIFIER")) or _text(fields.get("_COMM")) or "unknown"
    return unit.removesuffix(".service").translate(TOPIC_UNSAFE)


def parse_entry(line: bytes) -> Entry | None:
    """One line of `journalctl --output=json`; None for anything that is not an entry of levels 0..6."""
    try:
        fields = json.loads(line)
        if not isinstance(fields, dict):
            return None
        priority = int(_text(fields.get("PRIORITY")) or 6)
        realtime = int(fields["__REALTIME_TIMESTAMP"])
        monotonic = int(fields.get("__MONOTONIC_TIMESTAMP", 0))
        cursor = fields["__CURSOR"]
    except (ValueError, KeyError, TypeError):
        return None
    if not 0 <= priority < len(LEVELS):
        return None
    return Entry(
        unit=unit_of(fields),
        level=LEVELS[priority],
        identifier=_text(fields.get("SYSLOG_IDENTIFIER")) or _text(fields.get("_COMM")),
        message=_text(fields.get("MESSAGE")),
        time=datetime.datetime.fromtimestamp(realtime / 1e6).astimezone(),
        boot_id=_text(fields.get("_BOOT_ID")),
        monotonic=monotonic / 1e6,
        cursor=cursor,
    )


def shorten(message: str, max_length: int) -> str:
    """One line of at most `max_length` characters: a traceback keeps its first and last line."""
    lines = [line for line in message.splitlines() if line.strip()]
    if len(lines) > 1:
        message = f"{lines[0]} … {lines[-1].strip()}"
    elif lines:
        message = lines[0]
    if len(message) > max_length:
        message = message[: max_length - 1] + "…"
    return message


class TokenBucket:
    """Up to `capacity` at once, refilled by one every `seconds_per_token`."""

    def __init__(self, capacity: float, seconds_per_token: float, now: float):
        self.capacity = capacity
        self.seconds_per_token = seconds_per_token
        self.tokens = capacity
        self.last = now

    def available(self, now: float) -> bool:
        self.tokens = min(self.capacity, self.tokens + (now - self.last) / self.seconds_per_token)
        self.last = now
        return self.tokens >= 1

    def take(self) -> None:
        self.tokens -= 1


class JournalCounts:
    """Entries read and dropped per unit and level since the last `take()`, for the journalstats collector."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], list[int]] = {}

    def add(self, unit: str, level: str, entries: int = 0, dropped: int = 0) -> None:
        counts = self._counts.setdefault((unit, level), [0, 0])
        counts[0] += entries
        counts[1] += dropped

    def take(self) -> list[dict[str, Any]]:
        counts, self._counts = self._counts, {}
        keys = sorted(counts, key=lambda key: (key[0], LEVELS.index(key[1])))
        return [
            {"unit": unit, "level": level, "entries": counts[unit, level][0], "dropped": counts[unit, level][1]}
            for unit, level in keys
        ]


@dataclass
class _Window:
    end: float
    last: Entry
    repeats: int = 0


@dataclass
class Limiter:
    """Merges repeats and applies the rate limits of each level; see docs/logging.md, "Limits".

    `offer()` and `flush()` return what to publish now, as (entry, repeated)
    pairs: a new message with 0, or the last of a merged run with its count.
    """

    settings: JournalSettings
    counts: JournalCounts = field(default_factory=JournalCounts)
    _windows: dict[tuple[str, str, str], _Window] = field(default_factory=dict)
    _rates: dict[tuple[str, str], TokenBucket] = field(default_factory=dict)
    _budgets: dict[str, TokenBucket] = field(default_factory=dict)

    def _limits(self, level: Level) -> JournalLimits:
        return self.settings.limits_for(level)

    def _admit(self, entry: Entry, now: float, entries: int) -> bool:
        """Whether the rate of the entry's unit and the budget of its level allow one more message."""
        limits = self._limits(entry.level)
        rate = self._rates.get((entry.unit, entry.level))
        if rate is None:
            rate = self._rates[(entry.unit, entry.level)] = TokenBucket(limits.burst, limits.rate, now)
        budget = self._budgets.get(entry.level)
        if budget is None:
            budget = self._budgets[entry.level] = TokenBucket(limits.budget, 3600 / max(limits.budget, 1), now)
        if rate.available(now) and budget.available(now):
            rate.take()
            budget.take()
            return True
        self.counts.add(entry.unit, entry.level, dropped=entries)
        return False

    def flush(self, now: float) -> list[tuple[Entry, int]]:
        """The merged runs whose window has ended."""
        published = []
        for key, window in list(self._windows.items()):
            if now < window.end:
                continue
            del self._windows[key]
            if window.repeats and self._admit(window.last, now, window.repeats):
                published.append((window.last, window.repeats))
        return published

    def offer(self, entry: Entry, now: float) -> list[tuple[Entry, int]]:
        published = self.flush(now)
        self.counts.add(entry.unit, entry.level, entries=1)
        limits = self._limits(entry.level)
        entry = replace(entry, message=shorten(entry.message, limits.max_length))
        key = (entry.unit, entry.level, entry.message)
        window = self._windows.get(key)
        if window is not None:
            window.repeats += 1
            window.last = entry
            return published
        if limits.repeat_window and len(self._windows) < MAX_WINDOWS:
            self._windows[key] = _Window(end=now + limits.repeat_window, last=entry)
        if self._admit(entry, now, 1):
            published.append((entry, 0))
        return published


def journalctl_command(level: Level, cursor: str | None) -> list[str]:
    command = [
        "journalctl",
        "--follow",
        "--output=json",
        f"--priority={level}",
        f"--output-fields={','.join(FIELDS)}",
    ]
    # Without a cursor, start with what is logged from now on
    command.append(f"--after-cursor={cursor}" if cursor else "--lines=0")
    return command


class JournalSource:
    """Follows the journal and hands every entry the limits let through to `publish`."""

    def __init__(
        self,
        settings: JournalSettings,
        clock: Clock,
        publish: Callable[[Sample], None],
        failed: Callable[[str, Exception], None],
        succeeded: Callable[[str], None],
        state_dir: Path | None = None,
        monotonic: Callable[[], float] | None = None,
    ):
        self.settings = settings
        self.clock = clock
        self.publish = publish
        self._failed = failed
        self._succeeded = succeeded
        self.limiter = Limiter(settings)
        self.cursor_file = state_dir / "journal.cursor" if state_dir else None
        self.cursor = self._load_cursor()
        self._saved_cursor = self.cursor
        self._monotonic = monotonic or asyncio.get_running_loop().time

    @property
    def counts(self) -> JournalCounts:
        return self.limiter.counts

    def _load_cursor(self) -> str | None:
        if self.cursor_file is None:
            return None
        try:
            return self.cursor_file.read_text().strip() or None
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning("Cannot read %s, following the journal from now on: %s", self.cursor_file, e)
            return None

    def save_cursor(self) -> None:
        if self.cursor_file is None or self.cursor == self._saved_cursor:
            return
        temporary = self.cursor_file.with_suffix(".tmp")
        try:
            temporary.write_text(f"{self.cursor}\n")
            temporary.replace(self.cursor_file)
        except OSError as e:
            self._failed("journal cursor", e)
            return
        self._succeeded("journal cursor")
        self._saved_cursor = self.cursor

    def sample(self, entry: Entry, repeated: int) -> Sample:
        # Only an entry of this boot can have been logged with the clock this daemon sees as synced
        # (journald writes the boot ID without the dashes /proc has)
        synced = entry.boot_id == self.clock.boot_id().replace("-", "") and self.clock.synced()
        return Sample(
            collector=f"{JOURNAL}/{entry.unit}/{entry.level}",
            columns=COLUMNS,
            values=(entry.identifier, entry.message, repeated),
            time=entry.time,
            synced=synced,
            boottime=entry.monotonic,
        )

    def handle(self, line: bytes) -> None:
        entry = parse_entry(line)
        if entry is None:
            return
        self.cursor = entry.cursor
        for published, repeated in self.limiter.offer(entry, self._monotonic()):
            self.publish(self.sample(published, repeated))

    def flush(self) -> None:
        for published, repeated in self.limiter.flush(self._monotonic()):
            self.publish(self.sample(published, repeated))

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                await self._follow(stop)
                try:
                    await asyncio.wait_for(stop.wait(), RESTART_S)
                except TimeoutError:
                    pass
        finally:
            self.save_cursor()

    async def _follow(self, stop: asyncio.Event) -> None:
        """Run journalctl until it ends or `stop` is set."""
        cursor = self.cursor
        command = journalctl_command(self.settings.level, cursor)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE,
            )
        except OSError as e:
            self._failed(JOURNAL, e)
            return
        started = self._monotonic()
        last_save = started
        assert process.stdout is not None
        try:
            while not stop.is_set():
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), FLUSH_S)
                except TimeoutError:
                    line = None
                except ValueError:  # a line longer than MAX_LINE; the rest of it is lost
                    logger.debug("Skipping a journal entry longer than %d bytes", MAX_LINE)
                    continue
                if line == b"":
                    break
                if line:
                    self.handle(line)
                    self._succeeded(JOURNAL)
                self.flush()
                if self._monotonic() - last_save >= SAVE_CURSOR_S:
                    self.save_cursor()
                    last_save = self._monotonic()
        finally:
            if process.returncode is None:
                process.terminate()
            stderr = (await process.stderr.read()).decode(errors="replace").strip() if process.stderr else ""
            await process.wait()
        if stop.is_set():
            return
        if cursor and self.cursor == cursor and self._monotonic() - started < CURSOR_REJECTED_S:
            # e.g. the journal file holding the cursor was vacuumed
            logger.warning("journalctl did not continue at the saved cursor, following from now on: %s", stderr)
            self.cursor = None
        self._failed(JOURNAL, RuntimeError(f"journalctl ended with {process.returncode}: {stderr}"))
