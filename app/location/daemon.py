"""tslocationd: measured positions and tracks from GNSS, next to the configured reference.

Runs as its own service (tslocation.service), like tstelemetry. gpsd reads the
receiver and ModemManager switches it; this daemon decides when the receiver
runs, compares positions and publishes the results. It never changes the
reference (/boot/firmware/geolocation). See tsOS-base docs/location.md.
"""

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from app import __version__
from app.bluetooth.supervisor import sd_notify
from app.location import chrony
from app.location.geo import Fix, Position, average, distance, read_geoclue
from app.location.gpsd import Gpsd
from app.location.modem import Cell, ModemReceiver, ReceiverError
from app.location.outputs import DataFiles, MqttOutput, RuntimeFiles, cell_sample, fix_sample, state_sample
from app.location.server import SOCKET, ControlServer, RequestError
from app.location.settings import DEFAULT_CONFIG, LocationSettings, load_settings
from app.location.tracker import NO_FIX, TRACKING, UNKNOWN, Tracker
from app.logging_config import setup_logging
from app.telemetry.clock import Clock
from app.telemetry.samples import Sample

logger = logging.getLogger(__name__)

REFERENCE = Path("/boot/firmware/geolocation")
RUNTIME_DIR = Path("/run/tslocation")
STATE_DIR = Path("/var/lib/tslocation")
DATA_DIR = Path("/data")
# A `watch` client that does not read is dropped after this many unsent changes
WATCH_QUEUE = 100
WATCHDOG_PING_S = 20
# How often mobile mode looks at the state without a new fix (fix_timeout, the reference),
# and how long it waits before it retries a receiver or gpsd that failed
TICK_S = 1
RETRY_S = 60


class Receiver(Protocol):
    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def serving_cell(self) -> Cell | None: ...


class Output(Protocol):
    def write(self, sample: Sample) -> None: ...


class Daemon:
    def __init__(
        self,
        settings: LocationSettings,
        receiver: Receiver,
        gpsd: Gpsd,
        clock: Clock,
        outputs: list[Output],
        files: RuntimeFiles,
        state_path: Path,
        reference_path: Path = REFERENCE,
        config_path: Path = DEFAULT_CONFIG,
        chrony_update: Callable[[], Awaitable[float | None]] = chrony.last_update,
    ):
        self.settings = settings
        self.receiver = receiver
        self.gpsd = gpsd
        self.clock = clock
        self.outputs = outputs
        self.files = files
        self.state_path = state_path
        self.reference_path = reference_path
        self.config_path = config_path
        self.chrony_update = chrony_update
        self.tracker = Tracker.load(settings, state_path)
        self.reference: Position | None = None
        self._reference_read = False
        self.cell: Cell | None = None
        # mobile mode: when the receiver was switched on, the latest fix and when it arrived (monotonic)
        self.mobile_since: float | None = None
        self.latest: Fix | None = None
        self.latest_at: float | None = None
        self.new_fix = asyncio.Event()
        # What the state topic last carried
        self._state_key: Any = None
        # A fine fix asked for over the control socket, the requests waiting for its result,
        # and whether one runs now
        self.requested = asyncio.Event()
        self.fix_waiters: list[asyncio.Future] = []
        self.fixing = False
        # `watch` clients, and what they were last sent
        self.watchers: set[asyncio.Queue] = set()
        self._watched: dict[str, Any] | None = None
        self.started = time.monotonic()
        self.next_check: float | None = None
        # What last failed, so a failure repeating every check is logged as a warning once (and its recovery once)
        self._failures: dict[str, str] = {}

    # --- logging once per change of state, as tstelemetry does

    def _failed(self, key: str, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        if self._failures.get(key) != message:
            logger.warning("%s failed: %s", key, message)
        else:
            logger.debug("%s failed again: %s", key, message)
        self._failures[key] = message

    def _succeeded(self, key: str) -> None:
        if self._failures.pop(key, None) is not None:
            logger.info("%s works again", key)

    def write(self, sample: Sample) -> None:
        for output in self.outputs:
            key = type(output).__name__
            try:
                output.write(sample)
            except Exception as e:  # noqa: BLE001 - a failing output must not stop the other one
                self._failed(key, e)
            else:
                self._succeeded(key)

    # --- positions

    def read_reference(self) -> bool:
        """Re-read the reference; whether it changed."""
        try:
            reference = read_geoclue(self.reference_path)
        except (OSError, ValueError) as e:
            self._failed("Reading the reference", e)
            reference = None
        else:
            self._succeeded("Reading the reference")
        if reference == self.reference and self._reference_read:
            return False
        if self._reference_read:
            logger.info("The reference position changed to %s", reference)
        self.reference, self._reference_read = reference, True
        return True

    def mobile_fix(self) -> Fix | None:
        """The latest fix in mobile mode while it is younger than fix_timeout."""
        if self.latest is None or self.latest_at is None:
            return None
        if time.monotonic() - self.latest_at > self.settings.fix_timeout:
            return None
        return self.latest

    def effective(self) -> tuple[Position | None, str | None]:
        if self.settings.mode == "mobile" and (fix := self.mobile_fix()) is not None:
            return fix, "gnss"
        if self.reference is not None:
            return self.reference, "reference"
        fix = self.latest if self.settings.mode == "mobile" else self.tracker.fix
        if fix is not None:
            return fix, "gnss"
        return None, None

    def location(self) -> dict[str, Any]:
        """The content of location.json."""
        effective, source = self.effective()
        if self.settings.mode == "mobile":
            fix = self.latest
            proposed = None
            since = self.latest_at or self.mobile_since
            if self.mobile_fix() is not None:
                state = TRACKING
            elif since is not None and time.monotonic() - since > self.settings.fix_timeout:
                state = NO_FIX
            else:
                state = UNKNOWN
        else:
            fix = self.tracker.fix
            state = self.tracker.state
            proposed = self.tracker.proposed
        deviation = distance(fix, self.reference) if fix is not None and self.reference is not None else None
        return {
            "mode": self.settings.mode,
            "state": state,
            "effective": (
                {
                    **Position(effective.lat, effective.lon, effective.alt, effective.accuracy).as_dict(),
                    "source": source,
                }
                if effective is not None
                else None
            ),
            "reference": self.reference.as_dict() if self.reference else None,
            "fix": fix.as_dict() if fix else None,
            "deviation_m": round(deviation, 1) if deviation is not None else None,
            "fixing": self.fixing,
            "proposed": (
                Position(proposed.lat, proposed.lon, proposed.alt, proposed.accuracy).as_dict() if proposed else None
            ),
        }

    def publish_location(self) -> None:
        """Write the runtime files and, on a change, the state topic."""
        location = self.location()
        effective, _ = self.effective()
        try:
            self.files.write(location, effective)
        except OSError as e:
            self._failed("Writing the runtime files", e)
        else:
            self._succeeded("Writing the runtime files")
        # In mobile mode the effective position moves with every fix: the state topic only
        # follows the mode and state there, the track and fix topics carry the positions
        if self.settings.mode == "mobile":
            key: Any = (location["mode"], location["state"])
        else:
            key = {k: v for k, v in location.items() if k != "fixing"}
        if key != self._state_key and self.settings.mode != "off":
            self.write(state_sample(location, self.clock))
            self._state_key = key
        if location != self._watched:
            self._watched = location
            for queue in list(self.watchers):
                try:
                    queue.put_nowait(location)
                except asyncio.QueueFull:
                    self.watchers.discard(queue)

    def save(self) -> None:
        try:
            self.tracker.save(self.state_path)
        except OSError as e:
            self._failed("Saving the state", e)
        else:
            self._succeeded("Saving the state")

    # --- the receiver

    async def fine_fix(self) -> Fix | None:
        """Switch the receiver on, average the fixes for fix_duration, switch it off; None without a fix.

        Raises:
            ReceiverError: The receiver cannot be switched on (logged), so GNSS did not run.
        """
        try:
            await self.receiver.start()
        except ReceiverError as e:
            self._failed("Switching the receiver on", e)
            raise
        self._succeeded("Switching the receiver on")
        try:
            fixes = await self.collect()
        except OSError as e:
            self._failed("Reading gpsd", e)
            return None
        finally:
            try:
                await self.receiver.stop()
            except ReceiverError as e:
                self._failed("Switching the receiver off", e)
            else:
                self._succeeded("Switching the receiver off")
        self._succeeded("Reading gpsd")
        if not fixes:
            logger.warning("No fix within %d s", self.settings.fix_timeout)
            return None
        fix = average(fixes)
        logger.info("Fine fix from %d positions: %s, %s ± %s m", len(fixes), fix.lat, fix.lon, fix.accuracy)
        return fix

    async def collect(self) -> list[Fix]:
        """The fixes gpsd reports in fix_duration after the first, which must come within fix_timeout."""
        fixes: list[Fix] = []
        stream = self.gpsd.fixes()
        try:
            try:
                async with asyncio.timeout(self.settings.fix_timeout):
                    fixes.append(await anext(stream))
            except TimeoutError:
                return []
            loop = asyncio.get_running_loop()
            end = loop.time() + self.settings.fix_duration
            while (left := end - loop.time()) > 0:
                try:
                    async with asyncio.timeout(left):
                        fixes.append(await anext(stream))
                except TimeoutError:
                    break
        finally:
            await stream.aclose()
        return fixes

    async def coarse_check(self) -> Cell | None:
        """The serving cell; published when it changed. None without a modem or cell."""
        try:
            cell = await self.receiver.serving_cell()
        except ReceiverError as e:
            self._failed("Reading the serving cell", e)
            return None
        self._succeeded("Reading the serving cell")
        if cell is not None and cell != self.cell:
            logger.debug("Serving cell %s, known: %s", cell, self.tracker.known(cell))
            if self.settings.mode != "off":
                self.write(cell_sample(cell, self.tracker.known(cell), self.clock))
        self.cell = cell
        return cell

    async def time_due(self) -> str | None:
        """Why the clock needs a fine fix, or None."""
        try:
            update = await self.chrony_update()
        except chrony.ChronyError as e:
            self._failed("Reading chrony's state", e)
            return None
        self._succeeded("Reading chrony's state")
        if update is None:
            return "chrony has had no time source since it started"
        age = self.clock.time() - update
        if age > self.settings.time_max_age:
            return f"chrony has had no time source for {age / 3600:.1f} h"
        return None

    # --- the modes

    # --- the control socket (app/location/server.py, the `tslocation` CLI)

    def handlers(self) -> dict[str, Any]:
        return {
            "status": self.cmd_status,
            "fix": self.cmd_fix,
            "cells": self.cmd_cells,
            "config": self.cmd_config,
            "watch": self.cmd_watch,
        }

    async def cmd_status(self, args: dict[str, Any]) -> dict[str, Any]:
        next_check = None
        if self.next_check is not None and self.settings.mode != "mobile":
            next_check = max(0, round(self.next_check - time.monotonic()))
        return {
            "version": __version__,
            "uptime_s": round(time.monotonic() - self.started),
            "receiver": self.receiver.name,
            "next_check_in_s": next_check,
            "failures": dict(self._failures),
            "location": self.location(),
        }

    async def cmd_fix(self, args: dict[str, Any]) -> dict[str, Any]:
        """A fine fix now; with `wait` (default) the location after it, else at once."""
        if self.settings.mode != "stationary":
            raise RequestError(
                "not_available", f"Fine fixes are made in stationary mode, the daemon runs in {self.settings.mode} mode"
            )
        logger.info("Fine fix requested")
        if not args.get("wait", True):
            self.requested.set()
            return {"requested": True}
        future = asyncio.get_running_loop().create_future()
        self.fix_waiters.append(future)
        self.requested.set()
        return await future

    def _fix_done(self, error: RequestError | None = None) -> None:
        """Answer the requests waiting for the fine fix that just ended."""
        waiters, self.fix_waiters = self.fix_waiters, []
        for future in waiters:
            if future.done():
                continue
            if error is None:
                future.set_result(self.location())
            else:
                future.set_exception(error)

    async def cmd_cells(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "current": self.cell.as_dict() if self.cell else None,
            "current_known": self.tracker.known(self.cell) if self.cell else None,
            "known": [cell.as_dict() for cell in self.tracker.cells],
        }

    async def cmd_config(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"path": str(self.config_path), "settings": self.settings.model_dump()}

    async def cmd_watch(self, args: dict[str, Any]) -> Any:
        """Every change of location.json, starting with the current one."""
        queue: asyncio.Queue = asyncio.Queue(WATCH_QUEUE)
        queue.put_nowait(self.location())
        self.watchers.add(queue)

        async def events():
            try:
                while queue in self.watchers or not queue.empty():
                    yield await queue.get()
            finally:
                self.watchers.discard(queue)

        return events()

    async def check(self, requested: bool = False) -> None:
        """One round of `stationary` mode: coarse check, and a fine fix when one is due or `requested`."""
        # Judged at every check, not only when the file changes: it may have been adopted while
        # the daemon was not running
        self.read_reference()
        before = self.tracker.state
        self.tracker.on_reference(self.reference)
        if self.tracker.state != before:
            logger.info("State %s -> %s with the reference %s", before, self.tracker.state, self.reference)
            self.save()
        cell = await self.coarse_check()
        reason = "it was requested" if requested else self.tracker.due(self.clock.time())
        if reason is None and cell is not None and not self.tracker.known(cell):
            reason = "the serving cell is new at this position"
        if reason is None:
            reason = await self.time_due()
        if reason is not None:
            logger.info("Fine fix: %s", reason)
            before = self.tracker.state
            self.fixing = True
            self.publish_location()
            try:
                fix = await self.fine_fix()
            except ReceiverError as e:
                # no receiver: the state stays what it was (unknown without any fix)
                self.fixing = False
                self.publish_location()
                self._fix_done(RequestError("no_receiver", str(e)))
                return
            self.fixing = False
            if fix is None:
                self.tracker.on_no_fix()
                self._fix_done(RequestError("no_fix", f"No fix within {self.settings.fix_timeout:g} s"))
            else:
                self.tracker.on_fix(fix, self.reference, cell)
                self.write(
                    fix_sample("fix", fix, self.receiver.name, self.tracker.deviation(self.reference), self.clock)
                )
            if self.tracker.state != before:
                logger.info("State %s -> %s", before, self.tracker.state)
            self.save()
        self.publish_location()
        self._fix_done()

    async def run_stationary(self, stop: asyncio.Event) -> None:
        requested = False
        while not stop.is_set():
            self.requested.clear()
            await self.check(requested)
            self.next_check = time.monotonic() + self.settings.check_interval
            requested = await _sleep(stop, self.settings.check_interval, wake=self.requested)

    async def run_off(self, stop: asyncio.Event) -> None:
        """No position checks and no reports; a fine fix only when the clock needs one."""
        while not stop.is_set():
            self.read_reference()
            self.publish_location()
            reason = await self.time_due()
            if reason is not None:
                logger.info("Fine fix for the clock: %s", reason)
                try:
                    await self.fine_fix()
                except ReceiverError:
                    pass
            await _sleep(stop, self.settings.check_interval)

    def track_due(self, fix: Fix, tracked: Fix | None, tracked_at: float) -> bool:
        """Whether `fix` goes to the track: track_interval after the last point (by GNSS time), or track_distance away."""
        if tracked is None:
            return True
        if fix.time is not None and tracked.time is not None:
            elapsed = (fix.time - tracked.time).total_seconds()
        else:
            elapsed = time.monotonic() - tracked_at
        # GNSS seconds are whole: 1 s after the last point is due, not 0.999 s
        return elapsed >= self.settings.track_interval - 0.001 or distance(fix, tracked) >= self.settings.track_distance

    async def run_mobile(self, stop: asyncio.Event) -> None:
        """GNSS stays on; every fix track_interval apart (GNSS time) goes to the track, file and MQTT."""
        reader = asyncio.create_task(self._read_mobile(stop))
        tracked: Fix | None = None
        tracked_at = -float("inf")
        try:
            while not stop.is_set():
                self.read_reference()
                fix = self.mobile_fix()
                if fix is not None and fix is not tracked and self.track_due(fix, tracked, tracked_at):
                    deviation = distance(fix, self.reference) if self.reference else None
                    self.write(fix_sample("track", fix, self.receiver.name, deviation, self.clock))
                    tracked, tracked_at = fix, time.monotonic()
                # tracking, and no_fix after fix_timeout
                self.publish_location()
                # woken by the next fix (once per GNSS second), or after TICK_S without one
                self.new_fix.clear()
                await _sleep(stop, TICK_S, wake=self.new_fix)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            try:
                await self.receiver.stop()
            except ReceiverError as e:
                logger.debug("Cannot switch the receiver off: %s", e)

    async def _read_mobile(self, stop: asyncio.Event) -> None:
        """Keeps the receiver on and `latest` current; retries both after a failure."""
        while not stop.is_set():
            try:
                await self.receiver.start()
            except ReceiverError as e:
                self._failed("Switching the receiver on", e)
                await _sleep(stop, RETRY_S)
                continue
            self._succeeded("Switching the receiver on")
            if self.mobile_since is None:
                self.mobile_since = time.monotonic()
            try:
                async for fix in self.gpsd.fixes():
                    self._succeeded("Reading gpsd")
                    self.latest, self.latest_at = fix, time.monotonic()
                    self.new_fix.set()
            except OSError as e:
                self._failed("Reading gpsd", e)
            await _sleep(stop, RETRY_S)

    async def run(self, stop: asyncio.Event) -> None:
        async def watchdog():
            while not stop.is_set():
                sd_notify("WATCHDOG=1")
                await _sleep(stop, WATCHDOG_PING_S)

        mode = {"off": self.run_off, "stationary": self.run_stationary, "mobile": self.run_mobile}
        task = asyncio.create_task(mode[self.settings.mode](stop))
        dog = asyncio.create_task(watchdog())
        await stop.wait()
        # a fine fix can take minutes: cancel it (the receiver is switched off on the way out)
        task.cancel()
        await asyncio.gather(task, dog, return_exceptions=True)


async def _sleep(stop: asyncio.Event, seconds: float, wake: asyncio.Event | None = None) -> bool:
    """Sleep for `seconds` or until `stop` (or `wake`) is set; whether `wake` ended it."""
    events = [stop] if wake is None else [stop, wake]
    waiters = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
    return wake is not None and wake.is_set() and not stop.is_set()


def _settings(path: Path) -> LocationSettings:
    """The settings from `path`; a broken file falls back to the defaults."""
    try:
        return load_settings(path)
    except ValueError as e:
        logger.error("Ignoring %s, using the defaults: %s", path, e)
        return LocationSettings()


def _dir(variable: str, default: Path) -> Path:
    """A directory systemd made for the unit (RuntimeDirectory=, StateDirectory=), else the default."""
    value = os.environ.get(variable)
    return Path(value.split(":")[0]) if value else default


async def _serve(settings: LocationSettings, config_path: Path) -> None:
    host = socket.gethostname()
    runtime_dir = _dir("RUNTIME_DIRECTORY", RUNTIME_DIR)
    clock = Clock(runtime_dir)
    outputs: list[Output] = []
    files = DataFiles(DATA_DIR, host, clock)
    try:
        files.recover()
    except OSError as e:
        logger.error("Cannot recover earlier spools in %s: %s", files.spool_dir, e)
    outputs.append(files)
    mqtt = MqttOutput(host)
    mqtt.start()
    outputs.append(mqtt)

    daemon = Daemon(
        settings,
        ModemReceiver(),
        Gpsd(),
        clock,
        outputs,
        RuntimeFiles(runtime_dir),
        _dir("STATE_DIRECTORY", STATE_DIR) / "state.json",
        config_path=config_path,
    )
    # The files exist as soon as the daemon has started (from the reference until there is a fix)
    daemon.read_reference()
    daemon.publish_location()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    server = ControlServer(runtime_dir / SOCKET.name, daemon.handlers())
    await server.start()

    logger.info("Running in %s mode, state %s", settings.mode, daemon.tracker.state)
    sd_notify("READY=1")
    try:
        await daemon.run(stop)
    finally:
        sd_notify("STOPPING=1")
        # `watch` clients end with the daemon
        daemon.watchers.clear()
        await server.stop()
        mqtt.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("tslocationd", description="Measure the station's position with GNSS")
    parser.add_argument(
        "-c", "--config", type=Path, default=DEFAULT_CONFIG, help="settings file (default: %(default)s)"
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more output (-vv for debug)")
    args = parser.parse_args(argv)

    setup_logging(verbosity=args.verbose, identifier="tslocationd")
    asyncio.run(_serve(_settings(args.config), args.config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
