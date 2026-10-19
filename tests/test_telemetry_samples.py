"""CSV, JSON and CBOR carry the same columns in the same order."""

import datetime
import json

import cbor2

from app.telemetry.samples import Sample, encode_cbor, encode_csv, encode_json, make_values

TIME = datetime.datetime(2026, 10, 19, 14, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))


def make_sample(**kwargs) -> Sample:
    defaults = {
        "collector": "disk",
        "columns": ("mount", "used", "percent", "missing"),
        "values": ("/data", 11141120, 0.5, None),
        "time": TIME,
        "synced": True,
        "boottime": 12.5,
    }
    return Sample(**(defaults | kwargs))


def test_make_values_orders_by_column_and_fills_missing_with_none():
    assert make_values(("b", "a", "c"), {"a": 1, "b": 2, "extra": 3}) == (2, 1, None)


def test_csv_is_one_semicolon_separated_line_without_header():
    assert encode_csv(make_sample()) == b"2026-10-19T14:00:00+02:00;True;/data;11141120;0.5;"


def test_json_is_a_dict_in_column_order():
    data = json.loads(encode_json(make_sample()))
    assert list(data) == ["time", "synced", "mount", "used", "percent", "missing"]
    assert data["time"] == "2026-10-19T14:00:00+02:00"
    assert data["missing"] is None


def test_cbor_is_a_list_with_a_timezone_aware_time():
    row = cbor2.loads(encode_cbor(make_sample()))
    assert row == [TIME, True, "/data", 11141120, 0.5, None]
    assert row[0].utcoffset() == datetime.timedelta(hours=2)


def test_cbor_time_is_tag_0_with_offset():
    """Tag 0 is an RFC 3339 string, so the station's offset survives."""
    raw = encode_cbor(make_sample(columns=(), values=()))
    # array of 2, then tag 0 (0xc0) and the time as a text string
    assert raw[:2] == bytes([0x82, 0xC0])
    assert cbor2.loads(raw[2:-1]) == "2026-10-19T14:00:00+02:00"


def test_all_formats_agree():
    sample = make_sample()
    as_json = json.loads(encode_json(sample))
    as_cbor = cbor2.loads(encode_cbor(sample))
    as_csv = encode_csv(sample).decode().split(";")
    assert len(as_json) == len(as_cbor) == len(as_csv) == len(sample.header)
    assert list(as_json.values())[2:] == as_cbor[2:]
