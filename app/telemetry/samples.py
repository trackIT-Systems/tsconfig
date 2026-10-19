"""One reading of one collector, and its CSV, JSON and CBOR encodings.

All three encodings carry the same columns in the same order: `time`, `synced`,
then the collector's own columns. CBOR is a list without key names (smallest
over cellular); the column order is what the CSV header and the JSON keys show.
"""

import csv
import datetime
import io
import json
from dataclasses import dataclass
from typing import Any

import cbor2

LEADING_COLUMNS = ("time", "synced")


@dataclass(frozen=True)
class Sample:
    collector: str
    columns: tuple[str, ...]
    values: tuple[Any, ...]
    time: datetime.datetime
    # Whether the wall clock was synced when `time` was taken
    synced: bool
    # CLOCK_BOOTTIME at `time`; only used to re-time readings taken before the sync
    boottime: float

    @property
    def header(self) -> list[str]:
        return [*LEADING_COLUMNS, *self.columns]

    @property
    def row(self) -> list[Any]:
        return [self.time, self.synced, *self.values]


def make_values(columns: tuple[str, ...], reading: dict[str, Any]) -> tuple[Any, ...]:
    """The values of `reading` in column order; a missing column is None."""
    return tuple(reading.get(column) for column in columns)


def _text(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def csv_line(row: list[Any]) -> str:
    """One `;`-separated line without line ending; None becomes an empty field."""
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\n").writerow([_text(value) for value in row])
    return buffer.getvalue().rstrip("\n")


def encode_csv(sample: Sample) -> bytes:
    return csv_line(sample.row).encode()


def encode_json(sample: Sample) -> bytes:
    return json.dumps(dict(zip(sample.header, [_text(value) for value in sample.row]))).encode()


def encode_cbor(sample: Sample) -> bytes:
    # A timezone-aware datetime is encoded as tag 0, an RFC 3339 string with offset
    return cbor2.dumps(sample.row)


ENCODERS = {"csv": encode_csv, "json": encode_json, "cbor": encode_cbor}
