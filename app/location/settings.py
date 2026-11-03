"""Settings of the location daemon (/boot/firmware/tslocation.yml).

Set by the server, like envsense.yml: the mode and the intervals that decide
when the GNSS receiver runs. A missing file or key takes the default. Durations
are seconds, or a string such as `60s`, `1h`, `7d` (pytimeparse); distances
are metres.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

DEFAULT_CONFIG = Path("/boot/firmware/tslocation.yml")


def _seconds(value: Any) -> Any:
    """`3600`, `"3600"` or `"1h"` -> 3600.0; anything else is left to pydantic to reject."""
    if isinstance(value, str):
        from pytimeparse import parse

        seconds = parse(value)
        if seconds is None:
            raise ValueError(f"{value!r} is not a duration, e.g. 60, 60s, 1h, 7d")
        return float(seconds)
    return value


Duration = Annotated[float, BeforeValidator(_seconds)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationSettings(_Strict):
    mode: Literal["off", "stationary", "mobile"] = "stationary"
    receiver: Literal["modem"] = "modem"
    fix_duration: Duration = Field(60, ge=1, le=3600, description="How long a fine fix averages positions")
    fix_timeout: Duration = Field(300, ge=10, le=86400, description="How long GNSS may run without a fix")
    fix_max_age: Duration = Field(7 * 86400, ge=60, description="Fine fix at boot when the last one is older")
    check_interval: Duration = Field(3600, ge=60, description="Coarse check while running")
    threshold: float = Field(50, gt=0, description="Metres from the reference that count as deviating")
    recheck_interval: Duration = Field(6 * 3600, ge=60, description="Fine fix interval while deviating")
    settle_count: int = Field(3, ge=1, le=100, description="Agreeing fixes for a proposed reference")
    track_interval: Duration = Field(1, ge=1, le=86400, description="mobile: record a fix at least this often")
    track_distance: float = Field(10, gt=0, description="mobile: record a fix after this many metres")
    time_max_age: Duration = Field(
        24 * 3600, ge=60, description="Fine fix for the clock when chrony has had no source for longer"
    )


# The settings that are durations (seconds, or a string such as 7d)
DURATIONS = frozenset(
    name
    for name, field in LocationSettings.model_fields.items()
    if any(isinstance(meta, BeforeValidator) for meta in field.metadata)
)


def load_settings(path: Path) -> LocationSettings:
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
    return LocationSettings.model_validate(data or {})
