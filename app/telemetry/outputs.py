"""Where samples go: MQTT (CSV, JSON and CBOR topics) and daily CSV files.

Topics are `<host>/tsconfig/<collector>/<format>`, files are
`<path>/<host>/tsconfig/<collector>/<host>_<YYYY-MM-DD>_<collector>.csv`.

Files are only written with a synced clock, because a wrong clock would put
readings into the wrong day's file (or a file dated 1970). Until then samples
go to a spool per boot, and are re-timed by their CLOCK_BOOTTIME when the clock
is synced. A spool left from an earlier boot cannot be re-timed; it becomes
`<host>_unsynced_<boot_id>_<collector>.csv` instead. Nothing is ever deleted.
"""

import datetime
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.telemetry.clock import Clock
from app.telemetry.samples import ENCODERS, LEADING_COLUMNS, Sample, csv_line
from app.telemetry.settings import BrokerSettings

logger = logging.getLogger(__name__)

APP = "tsconfig"


def state_topic(host: str) -> str:
    return f"{host}/{APP}/state"


def sample_topic(host: str, collector: str, fmt: str) -> str:
    return f"{host}/{APP}/{collector}/{fmt}"


class MqttOutput:
    """Publishes every sample in the configured formats, not retained, QoS 0.

    `<host>/tsconfig/state` is retained: online while connected, offline as the
    last will. The client connects in the background and reconnects by itself, so
    the daemon also runs while the broker is down (samples are dropped then).
    """

    def __init__(self, host: str, broker: BrokerSettings, formats: Iterable[str], client: Any = None):
        self.host = host
        self.broker = broker
        self.formats = [fmt for fmt in formats if fmt in ENCODERS]
        if client is None:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{host}-tstelemetry")
        self.client = client
        self.client.will_set(state_topic(host), json.dumps({"state": "offline"}), qos=1, retain=True)
        self.client.on_connect = self._on_connect

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if getattr(reason_code, "is_failure", False):
            logger.warning("MQTT connection refused: %s", reason_code)
            return
        logger.info("Connected to MQTT broker %s:%d", self.broker.host, self.broker.port)
        client.publish(state_topic(self.host), json.dumps({"state": "online"}), qos=1, retain=True)

    def start(self) -> None:
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.connect_async(self.broker.host, self.broker.port, keepalive=60)
        self.client.loop_start()

    def write(self, sample: Sample) -> None:
        for fmt in self.formats:
            self.client.publish(sample_topic(self.host, sample.collector, fmt), ENCODERS[fmt](sample), qos=0)

    def stop(self) -> None:
        info = self.client.publish(state_topic(self.host), json.dumps({"state": "offline"}), qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            pass  # not connected
        self.client.disconnect()
        self.client.loop_stop()


class CsvFiles:
    """Daily CSV files per collector, with a spool for readings before the clock sync."""

    def __init__(self, path: Path, host: str, clock: Clock):
        self.host = host
        self.clock = clock
        self.dir = path / host / APP
        self.spool_dir = self.dir / ".spool"
        self.boot_id = clock.boot_id()
        self.spool = self.spool_dir / f"{self.boot_id}.jsonl"

    def daily_file(self, collector: str, time: datetime.datetime) -> Path:
        return self.dir / collector / f"{self.host}_{time:%Y-%m-%d}_{collector}.csv"

    def unsynced_file(self, collector: str, boot_id: str) -> Path:
        return self.dir / collector / f"{self.host}_unsynced_{boot_id}_{collector}.csv"

    @staticmethod
    def _append(file: Path, header: list[str], row: list[Any]) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        with open(file, "a", newline="") as f:
            if f.tell() == 0:
                f.write(csv_line(header) + "\n")
            f.write(csv_line(row) + "\n")

    def write(self, sample: Sample) -> None:
        if not sample.synced:
            self._spool(sample)
            return
        if self.spool.exists():
            self.flush_spool()
        self._append(self.daily_file(sample.collector, sample.time), sample.header, sample.row)

    def _spool(self, sample: Sample) -> None:
        entry = {
            "collector": sample.collector,
            "columns": sample.columns,
            "values": sample.values,
            "time": sample.time.isoformat(),
            "boottime": sample.boottime,
        }
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        with open(self.spool, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    @staticmethod
    def _read_spool(spool: Path) -> Iterable[dict[str, Any]]:
        with open(spool) as f:
            for number, line in enumerate(f, 1):
                try:
                    entry = json.loads(line)
                    yield entry
                except json.JSONDecodeError:
                    # e.g. the last line of a spool cut off by a power loss
                    logger.warning("Skipping unreadable line %d of %s", number, spool)

    def flush_spool(self) -> None:
        """Re-time this boot's spooled samples with the now synced clock and write them out."""
        offset = self.clock.time() - self.clock.boottime()
        count = 0
        for entry in self._read_spool(self.spool):
            time = datetime.datetime.fromtimestamp(round(offset + entry["boottime"])).astimezone()
            header = [*LEADING_COLUMNS, *entry["columns"]]
            self._append(self.daily_file(entry["collector"], time), header, [time, False, *entry["values"]])
            count += 1
        self.spool.unlink()
        logger.info("Clock synced: wrote %d spooled readings to the daily files", count)

    def recover(self) -> None:
        """Turn spools of earlier boots into `_unsynced_` files; their times cannot be corrected."""
        if not self.spool_dir.is_dir():
            return
        for spool in sorted(self.spool_dir.glob("*.jsonl")):
            if spool == self.spool:
                continue
            boot_id = spool.stem
            count = 0
            for entry in self._read_spool(spool):
                header = [*LEADING_COLUMNS, *entry["columns"]]
                row = [entry["time"], False, *entry["values"]]
                self._append(self.unsynced_file(entry["collector"], boot_id), header, row)
                count += 1
            spool.unlink()
            logger.warning("Boot %s never had a synced clock: wrote %d readings to its unsynced files", boot_id, count)
