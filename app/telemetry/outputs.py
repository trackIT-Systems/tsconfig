"""Where samples go: MQTT (CSV, JSON and CBOR topics) and daily CSV files.

Topics are `<host>/tsconfig/<collector>/<format>`, QoS 0 and not retained:
a reading that gets through is current, one that does not is lost. Files are
`<path>/<host>/tsconfig/<collector>/<host>_<YYYY-MM-DD>_<collector>.csv`.

File rows are only written with a trusted time: a wrong clock would put
readings into the wrong day's file (or a file dated 1970), with a time nobody
can correct. Until chrony has a source (NTP, GNSS or the checked RTC), readings
only go over MQTT (tsOS-base docs/time.md). Files are never deleted.
"""

import datetime
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.telemetry.samples import ENCODERS, Sample, csv_line
from app.telemetry.settings import BrokerSettings

logger = logging.getLogger(__name__)

APP = "tsconfig"


def sample_topic(host: str, collector: str, fmt: str) -> str:
    return f"{host}/{APP}/{collector}/{fmt}"


class MqttOutput:
    """Publishes every sample in the configured formats, not retained, QoS 0.

    The client connects in the background and reconnects by itself, so the
    daemon also runs while the broker is down (samples are dropped then).
    """

    def __init__(self, host: str, broker: BrokerSettings, formats: Iterable[str], client: Any = None):
        self.host = host
        self.broker = broker
        self.formats = [fmt for fmt in formats if fmt in ENCODERS]
        if client is None:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{host}-tstelemetry")
        self.client = client
        self.client.on_connect = self._on_connect
        # The client retries a refused connection every minute: logged once per reason
        self._refused: str | None = None

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if getattr(reason_code, "is_failure", False):
            if str(reason_code) != self._refused:
                logger.warning("MQTT connection refused: %s", reason_code)
            else:
                logger.debug("MQTT connection refused again: %s", reason_code)
            self._refused = str(reason_code)
            return
        self._refused = None
        logger.info("Connected to MQTT broker %s:%d", self.broker.host, self.broker.port)

    def start(self) -> None:
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.connect_async(self.broker.host, self.broker.port, keepalive=60)
        self.client.loop_start()

    def write(self, sample: Sample) -> None:
        for fmt in self.formats:
            self.client.publish(sample_topic(self.host, sample.collector, fmt), ENCODERS[fmt](sample), qos=0)

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


class CsvFiles:
    """Daily CSV files per collector; rows without a trusted time are dropped.

    `app` names the directory; tslocationd writes to `<path>/<host>/tslocation`.
    """

    def __init__(self, path: Path, host: str, app: str = APP):
        self.host = host
        self.dir = path / host / app
        # Collectors whose rows are being dropped, to log when dropping starts and ends.
        # Per collector: a GNSS fix has a trusted time while the clock has not.
        self._dropping: set[str] = set()

    def daily_file(self, collector: str, time: datetime.datetime) -> Path:
        return self.dir / collector / f"{self.host}_{time:%Y-%m-%d}_{collector}.csv"

    @staticmethod
    def _append(file: Path, header: list[str], row: list[Any]) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        with open(file, "a", newline="") as f:
            if f.tell() == 0:
                f.write(csv_line(header) + "\n")
            f.write(csv_line(row) + "\n")

    def write(self, sample: Sample) -> None:
        if not sample.synced:
            if not self._dropping:
                logger.info("The clock is not trusted yet: readings go to %s once it is", self.dir)
            self._dropping.add(sample.collector)
            return
        if sample.collector in self._dropping:
            self._dropping.discard(sample.collector)
            if not self._dropping:
                logger.info("The clock is trusted: writing readings to %s", self.dir)
        self._append(self.daily_file(sample.collector, sample.time), sample.file_header, sample.file_row)
