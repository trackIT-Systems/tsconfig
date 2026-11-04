"""One reading of one collector, and its CSV, JSON and CBOR encodings.

All three encodings carry the same columns in the same order: the sample's
`leading` columns, then the collector's own. CBOR is a list without key names
(smallest over cellular); the column order is what the JSON keys show.

Station readings lead with nothing: over MQTT they are a snapshot of now, and
the receiver stamps them on arrival (tsOS-base docs/time.md). Samples with a
time of their own lead with it: a GNSS fix with `time`, a journal entry with
`time` and `synced`. The daily files always lead with `time` (`file_header`,
`file_row`).
"""

import csv
import datetime
import io
import json
from dataclasses import dataclass
from typing import Any

import cbor2


@dataclass(frozen=True)
class Sample:
    collector: str
    columns: tuple[str, ...]
    values: tuple[Any, ...]
    time: datetime.datetime
    # Whether `time` can be trusted: the clock was synced, or the time is a GNSS fix's
    synced: bool
    # Columns before the collector's in the encodings: none, or some of "time" and "synced"
    leading: tuple[str, ...] = ()

    @property
    def header(self) -> list[str]:
        return [*self.leading, *self.columns]

    @property
    def row(self) -> list[Any]:
        stamp = {"time": self.time, "synced": self.synced}
        return [*(stamp[column] for column in self.leading), *self.values]

    @property
    def file_header(self) -> list[str]:
        return ["time", *self.columns]

    @property
    def file_row(self) -> list[Any]:
        return [self.time, *self.values]


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
