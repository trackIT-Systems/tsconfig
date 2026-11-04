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
    }
    return Sample(**(defaults | kwargs))


def test_make_values_orders_by_column_and_fills_missing_with_none():
    assert make_values(("b", "a", "c"), {"a": 1, "b": 2, "extra": 3}) == (2, 1, None)


def test_a_station_reading_carries_no_time():
    """The receiver stamps station readings on arrival (tsOS-base docs/time.md)."""
    assert encode_csv(make_sample()) == b"/data;11141120;0.5;"
    assert list(json.loads(encode_json(make_sample()))) == ["mount", "used", "percent", "missing"]
    assert cbor2.loads(encode_cbor(make_sample())) == ["/data", 11141120, 0.5, None]


def test_csv_with_time_and_synced_leads_with_them():
    sample = make_sample(leading=("time", "synced"))
    assert encode_csv(sample) == b"2026-10-19T14:00:00+02:00;True;/data;11141120;0.5;"


def test_json_is_a_dict_in_column_order():
    data = json.loads(encode_json(make_sample(leading=("time",))))
    assert list(data) == ["time", "mount", "used", "percent", "missing"]
    assert data["time"] == "2026-10-19T14:00:00+02:00"
    assert data["missing"] is None


def test_cbor_is_a_list_with_a_timezone_aware_time():
    row = cbor2.loads(encode_cbor(make_sample(leading=("time", "synced"))))
    assert row == [TIME, True, "/data", 11141120, 0.5, None]
    assert row[0].utcoffset() == datetime.timedelta(hours=2)


def test_cbor_time_is_tag_0_with_offset():
    """Tag 0 is an RFC 3339 string, so the station's offset survives."""
    raw = encode_cbor(make_sample(columns=(), values=(), leading=("time", "synced")))
    # array of 2, then tag 0 (0xc0) and the time as a text string
    assert raw[:2] == bytes([0x82, 0xC0])
    assert cbor2.loads(raw[2:-1]) == "2026-10-19T14:00:00+02:00"


def test_all_formats_agree():
    for leading in ((), ("time",), ("time", "synced")):
        sample = make_sample(leading=leading)
        as_json = json.loads(encode_json(sample))
        as_cbor = cbor2.loads(encode_cbor(sample))
        as_csv = encode_csv(sample).decode().split(";")
        assert len(as_json) == len(as_cbor) == len(as_csv) == len(sample.header)
        assert list(as_json.values())[len(leading) :] == as_cbor[len(leading) :]


def test_file_rows_always_lead_with_the_time():
    sample = make_sample(leading=("time", "synced"))
    assert sample.file_header == ["time", "mount", "used", "percent", "missing"]
    assert sample.file_row == [TIME, "/data", 11141120, 0.5, None]
