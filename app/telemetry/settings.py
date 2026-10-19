"""Settings of the telemetry daemon (/etc/tsconfig/tstelemetry.yml).

Build-time data installed with the image, root-owned and not on the boot
partition: operators do not configure telemetry. The file holds data only:
which collectors run, how often, and where results go. There is no way to
supply code, unlike mqttutil.conf before it. Every collector has its own
settings class, so its default interval also applies when the file sets only
`enabled`.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG = Path("/etc/tsconfig/tstelemetry.yml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectorSettings(_Strict):
    enabled: bool = True
    interval: int = Field(60, ge=10, le=86400, description="Seconds between two readings")


class HourlyCollectorSettings(CollectorSettings):
    interval: int = Field(3600, ge=10, le=86400, description="Seconds between two readings")


class DiskSettings(CollectorSettings):
    mounts: list[str] = Field(default_factory=lambda: ["/", "/data"], description="Mount points to report")


class NetworkSettings(CollectorSettings):
    interval: int = Field(600, ge=10, le=86400, description="Seconds between two readings")
    interfaces: list[str] = Field(
        default_factory=lambda: ["wireguard", "eth0", "wlan0", "wwan0"],
        description="Interfaces to report, also when they are missing",
    )


class CollectorsSettings(_Strict):
    system: CollectorSettings = Field(default_factory=CollectorSettings)
    thermal: CollectorSettings = Field(default_factory=CollectorSettings)
    disk: DiskSettings = Field(default_factory=DiskSettings)
    pmic: CollectorSettings = Field(default_factory=CollectorSettings)
    throttled: CollectorSettings = Field(default_factory=CollectorSettings)
    clocks: CollectorSettings = Field(default_factory=CollectorSettings)
    voltages: CollectorSettings = Field(default_factory=CollectorSettings)
    wittypi: CollectorSettings = Field(default_factory=CollectorSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    modem: HourlyCollectorSettings = Field(default_factory=HourlyCollectorSettings)
    configs: HourlyCollectorSettings = Field(default_factory=HourlyCollectorSettings)
    release: HourlyCollectorSettings = Field(default_factory=HourlyCollectorSettings)


class BrokerSettings(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(1883, ge=1, le=65535)


class FileSettings(_Strict):
    """Daily CSV files, one per collector."""

    enabled: bool = True
    path: str = "/data"


class TelemetrySettings(_Strict):
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    # Payload formats published over MQTT, each on its own topic; empty means no MQTT
    formats: list[Literal["csv", "json", "cbor"]] = Field(default_factory=lambda: ["csv", "json", "cbor"])
    files: FileSettings = Field(default_factory=FileSettings)
    collectors: CollectorsSettings = Field(default_factory=CollectorsSettings)


def load_settings(path: Path) -> TelemetrySettings:
    """Read the settings file; a missing or empty file means the defaults.

    Raises:
        ValueError: The file is not valid YAML or does not match the schema.
    """
    try:
        data: Any = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        data = None
    except yaml.YAMLError as e:
        raise ValueError(f"{path} is not valid YAML: {e}") from e
    return TelemetrySettings.model_validate(data or {})
