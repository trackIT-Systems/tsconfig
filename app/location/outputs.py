"""What tslocationd publishes: /run/tslocation, MQTT and daily CSV files.

* `/run/tslocation/location.json` and `/run/tslocation/geolocation` (geoclue
  format, the effective position), written atomically on every change.
* `<host>/tslocation/{fix,track,state,cell}/{csv,json,cbor}` over MQTT, QoS 0;
  `state` is retained.
* `/data/<host>/tslocation/{fix,track,cell}/<host>_<YYYY-MM-DD>_<kind>.csv`,
  through tstelemetry's CsvFiles (same clock handling). `state` has no file.

The columns follow the telemetry conventions: `time` and `synced` first. A fix
carries its GNSS time, so it is always `synced`.
"""

import datetime
import json
import logging
from pathlib import Path
from typing import Any

from app.location.geo import Fix, Position, geoclue_text, write_atomic
from app.location.modem import Cell
from app.telemetry.clock import Clock
from app.telemetry.outputs import CsvFiles
from app.telemetry.samples import ENCODERS, Sample

logger = logging.getLogger(__name__)

APP = "tslocation"

FIX_COLUMNS = ("source", "lat", "lon", "alt", "accuracy", "sats", "hdop", "deviation_m")
STATE_COLUMNS = (
    "mode",
    "state",
    "effective_lat",
    "effective_lon",
    "deviation_m",
    "proposed_lat",
    "proposed_lon",
    "proposed_alt",
    "proposed_accuracy",
)
CELL_COLUMNS = ("mcc", "mnc", "tac", "cell_id", "known")


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _now(clock: Clock) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(round(clock.time())).astimezone()


def fix_sample(kind: str, fix: Fix, source: str, deviation: float | None, clock: Clock) -> Sample:
    """A fix as a `fix` or `track` row, at its GNSS time (the clock's if it has none)."""
    time = fix.time.replace(microsecond=0) if fix.time else _now(clock)
    values = (
        source,
        round(fix.lat, 7),
        round(fix.lon, 7),
        _round(fix.alt, 1),
        _round(fix.accuracy, 1),
        fix.sats,
        _round(fix.hdop, 2),
        _round(deviation, 1),
    )
    return Sample(kind, FIX_COLUMNS, values, time, fix.time is not None or clock.synced(), clock.boottime())


def cell_sample(cell: Cell, known: bool, clock: Clock) -> Sample:
    values = (cell.mcc, cell.mnc, cell.tac, cell.cell_id, known)
    return Sample("cell", CELL_COLUMNS, values, _now(clock), clock.synced(), clock.boottime())


def state_sample(location: dict[str, Any], clock: Clock) -> Sample:
    effective = location["effective"] or {}
    proposed = location["proposed"] or {}
    values = (
        location["mode"],
        location["state"],
        effective.get("lat"),
        effective.get("lon"),
        location["deviation_m"],
        proposed.get("lat"),
        proposed.get("lon"),
        proposed.get("alt"),
        proposed.get("accuracy"),
    )
    return Sample("state", STATE_COLUMNS, values, _now(clock), clock.synced(), clock.boottime())


def topic(host: str, kind: str, fmt: str) -> str:
    return f"{host}/{APP}/{kind}/{fmt}"


class MqttOutput:
    """Publishes samples in every format, QoS 0; `state` retained.

    The client connects in the background and reconnects by itself, so the
    daemon also runs while the broker is down (messages are dropped then).
    """

    def __init__(self, host: str, broker_host: str = "127.0.0.1", broker_port: int = 1883, client: Any = None):
        self.host = host
        self.broker = (broker_host, broker_port)
        if client is None:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{host}-tslocationd")
        self.client = client
        self.client.on_connect = self._on_connect
        self._refused: str | None = None
        # The last state, published again after a reconnect (a retained message the broker may have lost)
        self._state: Sample | None = None

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if getattr(reason_code, "is_failure", False):
            if str(reason_code) != self._refused:
                logger.warning("MQTT connection refused: %s", reason_code)
            else:
                logger.debug("MQTT connection refused again: %s", reason_code)
            self._refused = str(reason_code)
            return
        self._refused = None
        logger.info("Connected to MQTT broker %s:%d", *self.broker)
        if self._state is not None:
            self.write(self._state)

    def start(self) -> None:
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.connect_async(*self.broker, keepalive=60)
        self.client.loop_start()

    def write(self, sample: Sample) -> None:
        retain = sample.collector == "state"
        if retain:
            self._state = sample
        for fmt, encode in ENCODERS.items():
            self.client.publish(topic(self.host, sample.collector, fmt), encode(sample), qos=0, retain=retain)

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


class DataFiles(CsvFiles):
    """Daily CSV files of fixes, tracks and cells; the state only goes over MQTT."""

    KINDS = ("fix", "track", "cell")

    def __init__(self, path: Path, host: str, clock: Clock):
        super().__init__(path, host, clock, app=APP)

    def write(self, sample: Sample) -> None:
        if sample.collector in self.KINDS:
            super().write(sample)


class RuntimeFiles:
    """`location.json` and `geolocation` in /run/tslocation, for other services."""

    def __init__(self, directory: Path):
        self.dir = directory
        self._last: tuple[str, Position | None] | None = None

    def write(self, location: dict[str, Any], effective: Position | None) -> bool:
        """Write both files if `location` changed; whether it did."""
        text = json.dumps(location, indent=1) + "\n"
        if (text, effective) == self._last:
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        if effective is not None:
            write_atomic(self.dir / "geolocation", geoclue_text(effective))
        else:
            (self.dir / "geolocation").unlink(missing_ok=True)
        write_atomic(self.dir / "location.json", text)
        self._last = (text, effective)
        return True
