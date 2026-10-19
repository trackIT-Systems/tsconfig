"""Daily CSV files, and the spool that holds readings until the clock is synced."""

from app.telemetry.outputs import CsvFiles
from tests.telemetry_helpers import FakeClock, local, sample

HOST = "station"


def read(path):
    return path.read_text().splitlines()


def collector_dir(tmp_path, collector="system"):
    return tmp_path / HOST / "tsconfig" / collector


def test_synced_sample_goes_to_the_days_file_with_one_header(tmp_path):
    clock = FakeClock(local(2026, 10, 19, 12, 0).timestamp(), synced=True)
    files = CsvFiles(tmp_path, HOST, clock)
    files.write(sample(clock))
    clock.advance(60)
    files.write(sample(clock))

    file = collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv"
    assert read(file) == [
        "time;synced;a;b",
        f"{local(2026, 10, 19, 12, 0).isoformat()};True;1.5;2",
        f"{local(2026, 10, 19, 12, 1).isoformat()};True;1.5;2",
    ]


def test_a_new_day_starts_a_new_file(tmp_path):
    clock = FakeClock(local(2026, 10, 19, 23, 59).timestamp(), synced=True)
    files = CsvFiles(tmp_path, HOST, clock)
    files.write(sample(clock))
    clock.advance(60)
    files.write(sample(clock))

    assert sorted(path.name for path in collector_dir(tmp_path).iterdir()) == [
        f"{HOST}_2026-10-19_system.csv",
        f"{HOST}_2026-10-20_system.csv",
    ]
    assert read(collector_dir(tmp_path) / f"{HOST}_2026-10-20_system.csv")[0] == "time;synced;a;b"


def test_existing_files_are_appended_to(tmp_path):
    clock = FakeClock(local(2026, 10, 19, 12, 0).timestamp(), synced=True)
    file = collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv"
    file.parent.mkdir(parents=True)
    file.write_text("time;synced;a;b\nearlier\n")
    CsvFiles(tmp_path, HOST, clock).write(sample(clock))
    assert read(file)[:2] == ["time;synced;a;b", "earlier"]
    assert len(read(file)) == 3


def test_unsynced_samples_wait_in_the_spool(tmp_path):
    clock = FakeClock(local(1970, 1, 1, 0, 5).timestamp())
    files = CsvFiles(tmp_path, HOST, clock)
    files.write(sample(clock))

    assert files.spool.exists()
    assert not collector_dir(tmp_path).exists()


def test_sync_retimes_the_spool_by_boottime_and_writes_it_first(tmp_path):
    # Booted with a clock in 1970; two readings 60 s apart, then chrony syncs
    clock = FakeClock(local(1970, 1, 1, 0, 5).timestamp(), boottime=100)
    files = CsvFiles(tmp_path, HOST, clock)
    files.write(sample(clock, values=(1, 1)))
    clock.advance(60)
    files.write(sample(clock, values=(2, 2)))
    clock.advance(30)  # boottime 190
    clock.sync(local(2026, 10, 19, 12, 0, 30).timestamp())
    files.write(sample(clock, values=(3, 3)))

    assert not files.spool.exists()
    lines = read(collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv")
    assert lines == [
        "time;synced;a;b",
        # 90 s and 30 s before the sync, marked as not taken with a synced clock
        f"{local(2026, 10, 19, 11, 59).isoformat()};False;1;1",
        f"{local(2026, 10, 19, 12, 0).isoformat()};False;2;2",
        f"{local(2026, 10, 19, 12, 0, 30).isoformat()};True;3;3",
    ]


def test_a_spool_spanning_midnight_is_split_by_day(tmp_path):
    clock = FakeClock(0.0, boottime=10)
    files = CsvFiles(tmp_path, HOST, clock)
    files.write(sample(clock))
    clock.advance(120)
    files.write(sample(clock))
    clock.sync(local(2026, 10, 20, 0, 1).timestamp())
    files.flush_spool()

    first = read(collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv")
    second = read(collector_dir(tmp_path) / f"{HOST}_2026-10-20_system.csv")
    assert first[1].startswith(local(2026, 10, 19, 23, 59).isoformat())
    assert second[1].startswith(local(2026, 10, 20, 0, 1).isoformat())


def test_a_restart_before_the_sync_continues_the_same_spool(tmp_path):
    clock = FakeClock(0.0)
    CsvFiles(tmp_path, HOST, clock).write(sample(clock))
    clock.advance(60)
    files = CsvFiles(tmp_path, HOST, clock)
    files.recover()
    files.write(sample(clock))
    assert len(read(files.spool)) == 2


def test_spools_of_earlier_boots_become_unsynced_files(tmp_path):
    old = FakeClock(local(1970, 1, 1).timestamp(), boot_id="boot-a")
    CsvFiles(tmp_path, HOST, old).write(sample(old))
    CsvFiles(tmp_path, HOST, old).write(sample(old, collector="disk"))

    files = CsvFiles(tmp_path, HOST, FakeClock(0.0, boot_id="boot-b"))
    files.recover()

    assert not list(files.spool_dir.glob("*.jsonl"))
    lines = read(collector_dir(tmp_path) / f"{HOST}_unsynced_boot-a_system.csv")
    assert lines == ["time;synced;a;b", f"{local(1970, 1, 1).isoformat()};False;1.5;2"]
    assert (collector_dir(tmp_path, "disk") / f"{HOST}_unsynced_boot-a_disk.csv").exists()


def test_an_unreadable_spool_line_is_skipped(tmp_path):
    clock = FakeClock(0.0)
    files = CsvFiles(tmp_path, HOST, clock)
    files.write(sample(clock))
    with open(files.spool, "a") as f:
        f.write('{"collector": "sys')  # cut off by a power loss
    clock.sync(local(2026, 10, 19, 12).timestamp())
    files.write(sample(clock))
    assert len(read(collector_dir(tmp_path) / f"{HOST}_2026-10-19_system.csv")) == 3
