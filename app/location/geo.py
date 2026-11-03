"""Positions, fixes, distances and the geoclue file format."""

import datetime
import math
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

EARTH_RADIUS_M = 6371008.8


@dataclass(frozen=True)
class Position:
    lat: float
    lon: float
    alt: float | None = None
    # Radius in metres
    accuracy: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Fix(Position):
    """A position measured by GNSS, at GNSS time."""

    time: datetime.datetime | None = None
    sats: int | None = None
    hdop: float | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["time"] = self.time.isoformat() if self.time else None
        return {"time": data.pop("time"), **data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fix":
        time = data.get("time")
        return cls(
            lat=data["lat"],
            lon=data["lon"],
            alt=data.get("alt"),
            accuracy=data.get("accuracy"),
            time=datetime.datetime.fromisoformat(time) if time else None,
            sats=data.get("sats"),
            hdop=data.get("hdop"),
        )


def distance(a: Position, b: Position) -> float:
    """Great-circle distance in metres (haversine)."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _round(value: float | None, digits: int) -> float | None:
    """Rounded to what the measurement can tell (7 decimal degrees are about 1 cm)."""
    return None if value is None else round(value, digits)


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def average(fixes: Sequence[Fix]) -> Fix:
    """The mean of `fixes`, at the time of the last one.

    The accuracy is the mean of theirs, not smaller: GNSS errors over a minute
    or between fixes of one site are correlated, so averaging does not remove them.
    """
    if not fixes:
        raise ValueError("no fixes to average")
    sats = [f.sats for f in fixes if f.sats is not None]
    return Fix(
        lat=round(sum(f.lat for f in fixes) / len(fixes), 7),
        lon=round(sum(f.lon for f in fixes) / len(fixes), 7),
        alt=_round(_mean([f.alt for f in fixes]), 1),
        accuracy=_round(_mean([f.accuracy for f in fixes]), 1),
        time=fixes[-1].time,
        sats=max(sats) if sats else None,
        hdop=_round(_mean([f.hdop for f in fixes]), 2),
    )


def agree(fixes: Sequence[Position]) -> bool:
    """Whether every two of `fixes` are within their accuracies of each other."""
    for i, a in enumerate(fixes):
        for b in fixes[i + 1 :]:
            if distance(a, b) > (a.accuracy or 0) + (b.accuracy or 0):
                return False
    return True


def read_geoclue(path: Path) -> Position | None:
    """The position in a geoclue file (lat, lon, alt, accuracy, one per line, `#` comments); None if it is missing.

    Raises:
        ValueError: The file is not in geoclue format.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    values = [line.split("#")[0].strip() for line in text.splitlines()]
    values = [v for v in values if v]
    if len(values) != 4:
        raise ValueError(f"{path}: expected 4 values, got {len(values)}")
    lat, lon, alt, accuracy = (float(v) for v in values)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{path}: {lat}, {lon} is not a position")
    return Position(lat, lon, alt, accuracy)


def geoclue_text(position: Position) -> str:
    return (
        f"{position.lat} # latitude\n"
        f"{position.lon} # longitude\n"
        f"{position.alt or 0.0} # altitude\n"
        f"{position.accuracy or 0.0} # accuracy radius\n"
    )


def write_atomic(path: Path, text: str) -> None:
    """Write a temporary file next to `path` and rename it, so readers never see half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    tmp.replace(path)
