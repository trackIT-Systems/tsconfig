"""Daily CSV files, written only once the clock is trusted."""

import logging

from app.telemetry.outputs import CsvFiles
from tests.telemetry_helpers import FakeClock, local, sample

HOST = "station"


def read(path):
    return path.read_text().splitlines()


def collector_dir(tmp_path, collector="system"):
    return tmp_path / HOST / "tsconfig" / collector


def test_synced_sample_goes_to_the_days_file_with_one_header(tmp_path):
    clock = FakeClock(local(2026, 10, 19, 12, 0).timestamp(), synced=True)
    files = CsvFiles(tmp_path, HOST)
    files.write(sample(clock))
    clock.advance(60)
    files.write(sample(clock))

    file = collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv"
    assert read(file) == [
        "time;a;b",
        f"{local(2026, 10, 19, 12, 0).isoformat()};1.5;2",
        f"{local(2026, 10, 19, 12, 1).isoformat()};1.5;2",
    ]


def test_a_new_day_starts_a_new_file(tmp_path):
    clock = FakeClock(local(2026, 10, 19, 23, 59).timestamp(), synced=True)
    files = CsvFiles(tmp_path, HOST)
    files.write(sample(clock))
    clock.advance(60)
    files.write(sample(clock))

    assert sorted(path.name for path in collector_dir(tmp_path).iterdir()) == [
        f"{HOST}_2026-10-19_system.csv",
        f"{HOST}_2026-10-20_system.csv",
    ]
    assert read(collector_dir(tmp_path) / f"{HOST}_2026-10-20_system.csv")[0] == "time;a;b"


def test_existing_files_are_appended_to(tmp_path):
    clock = FakeClock(local(2026, 10, 19, 12, 0).timestamp(), synced=True)
    file = collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv"
    file.parent.mkdir(parents=True)
    file.write_text("time;a;b\nearlier\n")
    CsvFiles(tmp_path, HOST).write(sample(clock))
    assert read(file)[:2] == ["time;a;b", "earlier"]
    assert len(read(file)) == 3


def test_rows_without_a_trusted_time_are_dropped(tmp_path):
    clock = FakeClock(local(1970, 1, 1, 0, 5).timestamp())
    files = CsvFiles(tmp_path, HOST)
    files.write(sample(clock))

    assert not (tmp_path / HOST).exists()


def test_rows_are_written_from_the_sync_on(tmp_path):
    clock = FakeClock(local(1970, 1, 1, 0, 5).timestamp())
    files = CsvFiles(tmp_path, HOST)
    files.write(sample(clock, values=(1, 1)))
    clock.sync(local(2026, 10, 19, 12, 0).timestamp())
    files.write(sample(clock, values=(2, 2)))

    assert read(collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv") == [
        "time;a;b",
        f"{local(2026, 10, 19, 12, 0).isoformat()};2;2",
    ]


def test_dropping_is_logged_when_it_starts_and_ends(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    clock = FakeClock(0.0)
    files = CsvFiles(tmp_path, HOST)
    for collector in ("system", "disk", "system"):
        files.write(sample(clock, collector=collector))
    clock.sync(local(2026, 10, 19, 12).timestamp())
    for collector in ("system", "disk", "system"):
        files.write(sample(clock, collector=collector))

    messages = [r.getMessage() for r in caplog.records]
    assert len([m for m in messages if "not trusted yet" in m]) == 1
    assert len([m for m in messages if "The clock is trusted" in m]) == 1
