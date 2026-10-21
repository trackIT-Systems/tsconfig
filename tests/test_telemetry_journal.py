"""Tests for the journal source: parsing, limits, journalstats and following journalctl."""

import asyncio
import json
import os
import stat

import pytest

from app.telemetry.collectors import Unavailable
from app.telemetry.collectors.journalstats import JournalStatsCollector
from app.telemetry.journal import JournalSource, Limiter, journalctl_command, parse_entry, shorten, unit_of
from app.telemetry.settings import JournalSettings, JournalStatsSettings
from tests.telemetry_helpers import FakeClock


def line(message="Cannot read", priority=4, unit="tsschedule.service", cursor="c1", **fields) -> bytes:
    entry = {
        "MESSAGE": message,
        "PRIORITY": str(priority),
        "_SYSTEMD_UNIT": unit,
        "SYSLOG_IDENTIFIER": "tsscheduled",
        "_BOOT_ID": "fe2f906d74b34f3c95199a21c89d3d4f",
        "__REALTIME_TIMESTAMP": "1792598013100667",
        "__MONOTONIC_TIMESTAMP": "10541014914",
        "__CURSOR": cursor,
        **fields,
    }
    return json.dumps({key: value for key, value in entry.items() if value is not None}).encode() + b"\n"


def settings(**limits) -> JournalSettings:
    return JournalSettings.model_validate({"limits": limits})


@pytest.mark.parametrize(
    "fields, unit",
    [
        ({"_SYSTEMD_UNIT": "tsschedule.service"}, "tsschedule"),
        ({"_SYSTEMD_UNIT": "wg-quick@wireguard.service"}, "wg-quick@wireguard"),
        ({"_SYSTEMD_UNIT": "data.mount"}, "data.mount"),
        ({"_SYSTEMD_UNIT": "init.scope", "UNIT": "caddy-api.service"}, "caddy-api"),
        ({"_SYSTEMD_UNIT": "init.scope"}, "init.scope"),
        ({"SYSLOG_IDENTIFIER": "kernel"}, "kernel"),
        ({"_COMM": "odd"}, "odd"),
        ({}, "unknown"),
        ({"SYSLOG_IDENTIFIER": "a/b+c#"}, "a_b_c_"),
    ],
)
def test_unit_of(fields, unit):
    assert unit_of(fields) == unit


def test_parse_entry():
    entry = parse_entry(line())

    assert entry.unit == "tsschedule"
    assert entry.level == "warning"
    assert entry.identifier == "tsscheduled"
    assert entry.message == "Cannot read"
    assert entry.time.timestamp() == pytest.approx(1792598013.100667)
    assert entry.monotonic == pytest.approx(10541.014914)
    assert entry.cursor == "c1"


def test_parse_entry_with_binary_and_repeated_fields():
    assert parse_entry(line(message=list(b"bin\xffary"))).message == "bin�ary"
    assert parse_entry(line(SYSLOG_IDENTIFIER=["first", "second"])).identifier == "first"


@pytest.mark.parametrize("data", [line(priority=7), b"not json\n", line(cursor=None), b"[]\n"])
def test_parse_entry_skips_debug_and_garbage(data):
    assert parse_entry(data) is None


def test_shorten():
    traceback = "app.x: Failed\nTraceback (most recent call last):\n  File ...\nValueError: broken\n"
    assert shorten(traceback, 256) == "app.x: Failed … ValueError: broken"
    assert shorten("x" * 300, 256) == "x" * 255 + "…"
    assert shorten("", 256) == ""


def test_burst_then_rate():
    limiter = Limiter(settings(burst=2, rate=60, repeat_window=0))
    offer = lambda n, now: limiter.offer(parse_entry(line(message=f"m{n}")), now)

    assert [len(offer(n, 0)) for n in range(4)] == [1, 1, 0, 0]
    assert len(offer(4, 59)) == 0
    assert len(offer(5, 61)) == 1
    assert limiter.counts.take() == [{"unit": "tsschedule", "level": "warning", "entries": 6, "dropped": 3}]
    assert limiter.counts.take() == []


def test_budget_is_counted_per_level():
    journal = JournalSettings.model_validate(
        {"limits": {"repeat_window": 0, "burst": 100}, "levels": {"info": {"budget": 2}}}
    )
    limiter = Limiter(journal)

    infos = [limiter.offer(parse_entry(line(message=f"i{n}", priority=6, unit=f"u{n}")), 0) for n in range(5)]
    errors = [limiter.offer(parse_entry(line(message=f"e{n}", priority=3, unit=f"u{n}")), 0) for n in range(5)]

    assert sum(map(len, infos)) == 2
    assert sum(map(len, errors)) == 5


def test_repeats_are_merged_and_counted():
    limiter = Limiter(settings(repeat_window=600))

    assert len(limiter.offer(parse_entry(line(cursor="c1")), 0)) == 1
    assert limiter.offer(parse_entry(line(cursor="c2")), 10) == []
    assert limiter.offer(parse_entry(line(cursor="c3")), 20) == []
    assert limiter.flush(599) == []

    [(entry, repeated)] = limiter.flush(600)
    assert (entry.cursor, repeated) == ("c3", 2)
    # The window is closed: the same message is new again
    assert limiter.offer(parse_entry(line(cursor="c4")), 601)[0][1] == 0


def test_messages_are_merged_after_shortening():
    limiter = Limiter(settings(max_length=16))

    limiter.offer(parse_entry(line(message="same start, then A")), 0)
    assert limiter.offer(parse_entry(line(message="same start, then B")), 1) == []


def test_limits_per_level_override_the_default():
    journal = JournalSettings.model_validate({"limits": {"burst": 5}, "levels": {"info": {"budget": 7}}})

    assert journal.limits_for("info").budget == 7
    assert journal.limits_for("info").burst == 5
    assert journal.limits_for("err") == journal.limits


def test_journalctl_command():
    assert journalctl_command("info", None)[-1] == "--lines=0"
    assert journalctl_command("warning", "s=1")[-1] == "--after-cursor=s=1"
    assert "--priority=warning" in journalctl_command("warning", None)


@pytest.mark.asyncio
async def test_journalstats_needs_the_journal():
    collector = JournalStatsCollector(JournalStatsSettings())
    with pytest.raises(Unavailable):
        collector.check()

    limiter = Limiter(settings())
    collector.counts = limiter.counts
    collector.check()
    limiter.offer(parse_entry(line()), 0)
    assert await collector.collect() == [{"unit": "tsschedule", "level": "warning", "entries": 1, "dropped": 0}]


@pytest.fixture
def fake_journalctl(tmp_path, monkeypatch):
    """A `journalctl` on PATH that records its arguments and prints the given lines."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def install(*lines: bytes, exit_code: int = 0) -> None:
        (tmp_path / "out").write_bytes(b"".join(lines))
        script = bin_dir / "journalctl"
        script.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/args\ncat {tmp_path}/out\nexit {exit_code}\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    return install


@pytest.mark.asyncio
async def test_follow_publishes_and_saves_the_cursor(fake_journalctl, tmp_path):
    fake_journalctl(line(cursor="c1"), line(message="Other", cursor="c2"), b"garbage\n")
    published, failures = [], []
    clock = FakeClock(1792598013, boot_id="fe2f906d-74b3-4f3c-9519-9a21c89d3d4f", synced=True)
    source = JournalSource(
        JournalSettings(),
        clock,
        publish=published.append,
        failed=lambda key, error: failures.append((key, str(error))),
        succeeded=lambda key: None,
        state_dir=tmp_path,
    )

    await source._follow(asyncio.Event())
    source.save_cursor()

    assert [(s.collector, s.values) for s in published] == [
        ("journal/tsschedule/warning", ("tsscheduled", "Cannot read", 0)),
        ("journal/tsschedule/warning", ("tsscheduled", "Other", 0)),
    ]
    assert all(s.synced for s in published)
    assert "--lines=0" in (tmp_path / "args").read_text()
    assert (tmp_path / "journal.cursor").read_text() == "c2\n"
    # journalctl ending is a failure, reported once by the daemon
    assert failures[0][0] == "journal"

    # The next run continues after the saved cursor
    restarted = JournalSource(JournalSettings(), clock, published.append, lambda *_: None, lambda _: None, tmp_path)
    await restarted._follow(asyncio.Event())
    assert "--after-cursor=c2" in (tmp_path / "args").read_text()


@pytest.mark.asyncio
async def test_a_rejected_cursor_is_dropped(fake_journalctl, tmp_path):
    fake_journalctl(exit_code=1)
    (tmp_path / "journal.cursor").write_text("gone\n")
    source = JournalSource(JournalSettings(), FakeClock(0), lambda _: None, lambda *_: None, lambda _: None, tmp_path)

    await source._follow(asyncio.Event())

    assert source.cursor is None


@pytest.mark.asyncio
async def test_entries_of_an_earlier_boot_are_not_synced(fake_journalctl, tmp_path):
    fake_journalctl(line(_BOOT_ID="boot-old"))
    published = []
    source = JournalSource(
        JournalSettings(), FakeClock(0, synced=True), published.append, lambda *_: None, lambda _: None
    )

    await source._follow(asyncio.Event())

    assert [s.synced for s in published] == [False]
