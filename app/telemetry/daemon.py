"""tstelemetry: publish the device's health over MQTT and into daily CSV files.

Runs as its own service, apart from the web UI, so telemetry continues while
tsconfig is down or being updated. Every collector runs on its own interval,
aligned to the wall clock (a 60 s collector reads at every full minute), so
rows of different collectors line up.
"""

import argparse
import asyncio
import datetime
import logging
import math
import os
import signal
import socket
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from app.bluetooth.supervisor import sd_notify
from app.logging_config import setup_logging
from app.telemetry.clock import Clock
from app.telemetry.collectors import COLLECTORS, Collector, Unavailable
from app.telemetry.outputs import CsvFiles, MqttOutput
from app.telemetry.samples import Sample, encode_json, make_values
from app.telemetry.settings import DEFAULT_CONFIG, TelemetrySettings, load_settings

logger = logging.getLogger("tstelemetry")

# A collection taking longer than this is abandoned (the worker thread is not killed)
MAX_COLLECT_S = 60
WATCHDOG_PING_S = 20


class Output(Protocol):
    def write(self, sample: Sample) -> None: ...


def next_tick(now: float, interval: int) -> float:
    """The next multiple of `interval` (Unix time) after `now`.

    Waking up a little before the tick counts as being at the tick, so an early
    wakeup does not read twice.
    """
    return (math.floor((now + 0.5) / interval) + 1) * interval


def enabled_collectors(settings: TelemetrySettings) -> list[Collector]:
    collectors = []
    for cls in COLLECTORS:
        collector_settings = getattr(settings.collectors, cls.name)
        if collector_settings.enabled:
            collectors.append(cls(collector_settings))
    return collectors


async def available_collectors(collectors: Iterable[Collector]) -> list[Collector]:
    """The collectors whose `check()` passes; the others are logged once and left out."""
    available = []
    for collector in collectors:
        try:
            await asyncio.to_thread(collector.check)
        except Unavailable as e:
            logger.info("Not collecting %s: %s", collector.name, e)
            continue
        except Exception as e:  # one broken collector must not stop the daemon
            logger.warning("Not collecting %s, its check failed: %s", collector.name, e, exc_info=True)
            continue
        available.append(collector)
    return available


class Daemon:
    def __init__(self, collectors: list[Collector], outputs: list[Output], clock: Clock):
        self.collectors = collectors
        self.outputs = outputs
        self.clock = clock
        # What last failed, by collector or output, so a failure repeating every
        # minute is logged as a warning once (and its recovery once)
        self._failures: dict[str, str] = {}

    def _failed(self, key: str, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        if self._failures.get(key) != message:
            logger.warning("%s failed: %s", key, message)
        else:
            logger.debug("%s failed again: %s", key, message)
        logger.debug("%s traceback", key, exc_info=error)
        self._failures[key] = message

    def _succeeded(self, key: str) -> None:
        if self._failures.pop(key, None) is not None:
            logger.info("%s works again", key)

    async def collect(self, collector: Collector) -> list[Sample]:
        """Read one collector now, as samples stamped with the current time."""
        boottime = self.clock.boottime()
        time = datetime.datetime.fromtimestamp(round(self.clock.time())).astimezone()
        synced = self.clock.synced()
        try:
            readings = await asyncio.wait_for(collector.collect(), timeout=MAX_COLLECT_S)
        except Exception as e:  # noqa: BLE001 - a failing collector must not stop the others
            self._failed(collector.name, e)
            return []
        self._succeeded(collector.name)
        return [
            Sample(
                collector=collector.name,
                columns=collector.columns,
                values=make_values(collector.columns, reading),
                time=time,
                synced=synced,
                boottime=boottime,
            )
            for reading in readings
        ]

    async def tick(self, collector: Collector) -> None:
        for sample in await self.collect(collector):
            for output in self.outputs:
                key = type(output).__name__
                try:
                    output.write(sample)
                except Exception as e:  # noqa: BLE001 - a failing output must not stop the other one
                    self._failed(key, e)
                else:
                    self._succeeded(key)

    async def run_collector(self, collector: Collector, stop: asyncio.Event) -> None:
        interval = collector.settings.interval
        while True:
            delay = next_tick(self.clock.time(), interval) - self.clock.time()
            try:
                await asyncio.wait_for(stop.wait(), max(delay, 0))
                return
            except TimeoutError:
                pass
            await self.tick(collector)

    async def run(self, stop: asyncio.Event) -> None:
        async def watchdog():
            while not stop.is_set():
                sd_notify("WATCHDOG=1")
                try:
                    await asyncio.wait_for(stop.wait(), WATCHDOG_PING_S)
                except TimeoutError:
                    pass

        await asyncio.gather(watchdog(), *(self.run_collector(c, stop) for c in self.collectors))


def _settings(path: Path) -> TelemetrySettings:
    """The settings from `path`; a broken file falls back to the defaults, so the station is not silenced."""
    try:
        return load_settings(path)
    except ValueError as e:
        logger.error("Ignoring %s, using the defaults: %s", path, e)
        return TelemetrySettings()


async def _once(collectors: list[Collector], clock: Clock) -> None:
    daemon = Daemon(collectors, [], clock)
    for collector in collectors:
        for sample in await daemon.collect(collector):
            print(f"{sample.collector}: {encode_json(sample).decode()}")


async def _serve(settings: TelemetrySettings, collectors: list[Collector], clock: Clock) -> None:
    host = socket.gethostname()
    outputs: list[Output] = []
    files = None
    if settings.files.enabled:
        files = CsvFiles(Path(settings.files.path), host, clock)
        try:
            files.recover()
        except OSError as e:
            logger.error("Cannot recover earlier spools in %s: %s", files.spool_dir, e)
        outputs.append(files)
    mqtt = None
    if settings.formats:
        mqtt = MqttOutput(host, settings.broker, settings.formats)
        mqtt.start()
        outputs.append(mqtt)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    logger.info(
        "Publishing %s to %s",
        ", ".join(f"{c.name} every {c.settings.interval} s" for c in collectors) or "nothing",
        ", ".join(type(o).__name__ for o in outputs) or "nowhere",
    )
    sd_notify("READY=1")
    try:
        await Daemon(collectors, outputs, clock).run(stop)
    finally:
        sd_notify("STOPPING=1")
        if mqtt:
            mqtt.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("tstelemetry", description="Publish the device's health over MQTT and to files")
    parser.add_argument(
        "-c", "--config", type=Path, default=DEFAULT_CONFIG, help="settings file (default: %(default)s)"
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more output (-vv for debug)")
    parser.add_argument("--once", action="store_true", help="read every collector once, print as JSON and exit")
    args = parser.parse_args(argv)

    setup_logging(verbosity=args.verbose)
    settings = _settings(args.config)
    # systemd's RuntimeDirectory= (cleared at boot) keeps the clock-synced marker
    runtime_dir = os.environ.get("RUNTIME_DIRECTORY")
    clock = Clock(Path(runtime_dir) if runtime_dir else None)

    async def run() -> None:
        collectors = await available_collectors(enabled_collectors(settings))
        if args.once:
            await _once(collectors, clock)
        else:
            await _serve(settings, collectors, clock)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
