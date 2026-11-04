"""tslocationd with fakes for gpsd, ModemManager and chrony: fine fixes, outputs, runtime files."""

import asyncio
import datetime
import json
import subprocess
from pathlib import Path

import pytest

from app.location import modem
from app.location.daemon import Daemon
from app.location.geo import Position, geoclue_text
from app.location.gpsd import Gpsd, tpv_fix
from app.location.modem import Cell, ModemReceiver, ReceiverError, parse_cell
from app.location.outputs import DataFiles, MqttOutput, RuntimeFiles, cell_sample, fix_sample, state_sample
from app.location.server import RequestError
from app.location.settings import LocationSettings
from app.telemetry.samples import ENCODERS
from tests.telemetry_helpers import FakeClock, local

REFERENCE = Position(50.81, 8.77, 210.0, 5.0)
CELL = Cell("262", "01", "1A2B", "01234567")
NOW = local(2026, 10, 22, 14, 0).timestamp()


def tpv(lat=50.81, lon=8.77, mode=3, time="2026-10-22T12:00:00.000Z", **extra):
    return {"class": "TPV", "mode": mode, "time": time, "lat": lat, "lon": lon, "altMSL": 212.3, "eph": 4.1, **extra}


# --- gpsd


def test_tpv_without_a_fix_is_ignored():
    assert tpv_fix(tpv(mode=1), {}) is None
    assert tpv_fix({"class": "TPV", "mode": 3}, {}) is None


def test_tpv_with_sky_becomes_a_fix():
    fix = tpv_fix(tpv(), {"class": "SKY", "uSat": 9, "hdop": 0.9})
    assert (fix.lat, fix.lon, fix.alt, fix.accuracy, fix.sats, fix.hdop) == (50.81, 8.77, 212.3, 4.1, 9, 0.9)
    # gpsd gives UTC
    assert fix.time == datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.UTC)


def test_2d_fix_has_no_altitude():
    assert tpv_fix(tpv(mode=2), {}).alt is None


@pytest.mark.asyncio
async def test_gpsd_client_watches_and_yields_fixes():
    received = []

    async def serve(reader, writer):
        writer.write(b'{"class":"VERSION","release":"3.25"}\n')
        received.append(await reader.readline())
        # gpsd sends a TPV per NMEA sentence: several per GNSS second
        reports = (
            {"class": "SKY", "uSat": 7, "hdop": 1.2},
            tpv(mode=1),
            tpv(),
            tpv(),
            tpv(time="2026-10-22T12:00:01.000Z"),
        )
        for report in reports:
            writer.write(json.dumps(report).encode() + b"\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    fixes = []
    with pytest.raises(ConnectionResetError):
        async for fix in Gpsd("127.0.0.1", port).fixes():
            fixes.append(fix)
    server.close()
    assert received[0].startswith(b"?WATCH=")
    assert [(f.lat, f.sats, f.hdop) for f in fixes] == [(50.81, 7, 1.2), (50.81, 7, 1.2)]
    assert [f.time.second for f in fixes] == [0, 1]  # one fix per GNSS second


# --- ModemManager


LOCATION_GET = {
    "modem": {"location": {"3gpp": {"mcc": "262", "mnc": "01", "lac": "--", "tac": "1A2B", "cid": "01234567"}}}
}


def test_serving_cell_from_location_get():
    assert parse_cell(LOCATION_GET) == CELL
    assert parse_cell({"modem": {"location": {"3gpp": {"mcc": "--", "mnc": "--", "cid": "--"}}}}) is None


def test_lac_is_used_without_tac():
    location = {
        "modem": {"location": {"3gpp": {"mcc": "262", "mnc": "01", "lac": "00AB", "tac": "000000", "cid": "9"}}}
    }
    assert parse_cell(location) == Cell("262", "01", "00AB", "9")


def fake_mmcli(monkeypatch, capabilities=("3gpp-lac-ci", "gps-unmanaged"), enabled=(), modems=1):
    calls = []
    state = {"enabled": list(enabled)}

    async def run(cmd, **_kwargs):
        # actions are run without --output-json: they print a sentence (as mmcli does)
        args = cmd[1:-1] if cmd[-1] == "--output-json" else cmd[1:]
        calls.append(" ".join(args))
        if args == ["-L"]:
            answer = {"modem-list": [f"/org/freedesktop/ModemManager1/Modem/{i}" for i in range(modems)]}
        elif args[-1] == "--location-status":
            answer = {"modem": {"location": {"capabilities": list(capabilities), "enabled": list(state["enabled"])}}}
        elif args[-1] == "--location-get":
            answer = LOCATION_GET
        elif args[-1] == "--location-enable-gps-unmanaged":
            state["enabled"].append("gps-unmanaged")
            answer = None
        elif args[-1] == "--location-disable-gps-unmanaged":
            state["enabled"].remove("gps-unmanaged")
            answer = None
        elif args[-1] == "--location-enable-3gpp":
            state["enabled"].append("3gpp-lac-ci")
            answer = None
        else:
            return subprocess.CompletedProcess(cmd, 1, "", "error: unknown")
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps(answer) if answer else "successfully setup location\n", ""
        )

    monkeypatch.setattr(modem, "run_subprocess_async", run)
    return calls, state


@pytest.mark.asyncio
async def test_receiver_switches_gps_unmanaged(monkeypatch):
    calls, state = fake_mmcli(monkeypatch)
    receiver = ModemReceiver()
    await receiver.start()
    assert state["enabled"] == ["gps-unmanaged"]
    await receiver.stop()
    assert state["enabled"] == []
    assert "-m 0 --location-enable-gps-unmanaged" in calls


@pytest.mark.asyncio
async def test_receiver_enables_3gpp_for_the_serving_cell(monkeypatch):
    _, state = fake_mmcli(monkeypatch)
    assert await ModemReceiver().serving_cell() == CELL
    assert state["enabled"] == ["3gpp-lac-ci"]


@pytest.mark.asyncio
async def test_receiver_without_modem_or_support_fails(monkeypatch):
    fake_mmcli(monkeypatch, modems=0)
    with pytest.raises(ReceiverError, match="no modem"):
        await ModemReceiver().start()
    fake_mmcli(monkeypatch, capabilities=("3gpp-lac-ci",))
    with pytest.raises(ReceiverError, match="gps-unmanaged"):
        await ModemReceiver().start()


# --- the daemon


class FakeReceiver:
    name = "modem"

    def __init__(self, cell=CELL, fail=False):
        self.cell = cell
        self.fail = fail
        self.on = False
        self.switches = 0

    async def start(self):
        if self.fail:
            raise ReceiverError("no modem")
        self.on = True
        self.switches += 1

    async def stop(self):
        self.on = False

    async def serving_cell(self):
        if self.fail:
            raise ReceiverError("no modem")
        return self.cell


class FakeGpsd:
    """Yields `fixes`, then waits forever (a receiver that has no more to say)."""

    def __init__(self, fixes=()):
        self.fixes_ = list(fixes)

    async def fixes(self):
        for fix in self.fixes_:
            yield fix
        await asyncio.Event().wait()


class Recorder:
    def __init__(self):
        self.samples = []

    def write(self, sample):
        self.samples.append(sample)

    def kinds(self):
        return [s.collector for s in self.samples]


def gnss_fix(lat=50.81, lon=8.77):
    return tpv_fix(tpv(lat=lat, lon=lon, time="2026-10-22T11:59:00.000Z"), {"uSat": 9, "hdop": 0.9})


def make_daemon(tmp_path, gpsd_fixes=(), receiver=None, chrony_age=3600.0, reference=REFERENCE, **settings):
    if reference is not None:
        (tmp_path / "geolocation").write_text(geoclue_text(reference))
    recorder = Recorder()

    async def chrony_update():
        return NOW - chrony_age

    # fix_duration is at least 1 s in the settings file; much shorter here, so the tests are quick
    settings = LocationSettings(**{"fix_timeout": 10, **settings}).model_copy(update={"fix_duration": 0.05})
    daemon = Daemon(
        settings,
        receiver or FakeReceiver(),
        FakeGpsd(gpsd_fixes),
        FakeClock(NOW, synced=True),
        [recorder],
        RuntimeFiles(tmp_path / "run"),
        tmp_path / "state.json",
        reference_path=tmp_path / "geolocation",
        chrony_update=chrony_update,
    )
    return daemon, recorder


def runtime(tmp_path):
    return json.loads((tmp_path / "run" / "location.json").read_text())


@pytest.mark.asyncio
async def test_first_check_makes_a_fine_fix_and_confirms(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [gnss_fix(), gnss_fix(50.8101)])
    await daemon.check()
    # the state before the fine fix (unknown), the fix, the state after it
    assert recorder.kinds() == ["cell", "state", "fix", "state"]
    fix = recorder.samples[2]
    assert fix.synced
    assert dict(zip(fix.columns, fix.values))["source"] == "modem"
    location = runtime(tmp_path)
    assert location["state"] == "confirmed"
    assert location["effective"]["source"] == "reference"
    assert location["fix"]["lat"] == pytest.approx(50.81005)
    assert location["deviation_m"] == pytest.approx(5.6, abs=0.1)
    assert (tmp_path / "run" / "geolocation").read_text() == geoclue_text(REFERENCE)
    assert not daemon.receiver.on
    assert json.loads((tmp_path / "state.json").read_text())["cells"] == [CELL.as_dict()]


@pytest.mark.asyncio
async def test_no_fine_fix_when_nothing_is_due(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [gnss_fix()])
    await daemon.check()
    recorder.samples.clear()
    await daemon.check()
    assert recorder.kinds() == []
    assert daemon.receiver.switches == 1


@pytest.mark.asyncio
async def test_a_new_cell_triggers_a_fine_fix(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [gnss_fix()])
    await daemon.check()
    daemon.receiver.cell = Cell("262", "01", "1A2B", "99")
    recorder.samples.clear()
    await daemon.check()
    assert recorder.kinds() == ["cell", "fix"]
    assert recorder.samples[0].values[-1] is False  # not known
    assert len(daemon.tracker.cells) == 2


@pytest.mark.asyncio
async def test_a_stale_clock_triggers_a_fine_fix(tmp_path):
    daemon, _ = make_daemon(tmp_path, [gnss_fix()], chrony_age=2 * 86400)
    await daemon.check()
    daemon.receiver.switches = 0
    await daemon.check()
    assert daemon.receiver.switches == 1


@pytest.mark.asyncio
async def test_no_fix_within_the_timeout(tmp_path):
    daemon, _ = make_daemon(tmp_path, [], fix_timeout=10)
    daemon.settings = daemon.settings.model_copy(update={"fix_timeout": 0.05})
    await daemon.check()
    assert runtime(tmp_path)["state"] == "no_fix"
    assert runtime(tmp_path)["effective"]["source"] == "reference"
    assert not daemon.receiver.on


@pytest.mark.asyncio
async def test_without_modem_there_is_no_fix_and_no_cell(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [gnss_fix()], receiver=FakeReceiver(fail=True))
    await daemon.check()
    assert recorder.kinds() == ["state"]
    assert runtime(tmp_path)["state"] == "unknown"


@pytest.mark.asyncio
async def test_without_reference_the_fix_is_effective(tmp_path):
    daemon, _ = make_daemon(tmp_path, [gnss_fix()], reference=None, settle_count=1)
    await daemon.check()
    location = runtime(tmp_path)
    assert location["reference"] is None
    assert location["effective"]["source"] == "gnss"
    assert location["state"] == "proposed"
    assert location["proposed"]["lat"] == pytest.approx(50.81)


@pytest.mark.asyncio
async def test_a_changed_reference_is_picked_up(tmp_path):
    daemon, _ = make_daemon(tmp_path, [gnss_fix(50.812)], settle_count=1)
    await daemon.check()
    assert runtime(tmp_path)["state"] == "proposed"
    (tmp_path / "geolocation").write_text(geoclue_text(Position(50.812, 8.77, 210.0, 5.0)))
    await daemon.check()
    assert runtime(tmp_path)["state"] == "confirmed"


@pytest.mark.asyncio
async def test_a_reference_adopted_while_the_daemon_was_down_is_picked_up(tmp_path):
    daemon, _ = make_daemon(tmp_path, [gnss_fix(50.812)], settle_count=1)
    await daemon.check()
    (tmp_path / "geolocation").write_text(geoclue_text(Position(50.812, 8.77, 210.0, 5.0)))
    restarted, _ = make_daemon(tmp_path, reference=None)
    restarted.read_reference()  # as _serve does before the first check
    await restarted.check()
    assert runtime(tmp_path)["state"] == "confirmed"


@pytest.mark.asyncio
async def test_a_requested_fine_fix_runs_at_once_and_answers_with_its_result(tmp_path):
    daemon, _ = make_daemon(tmp_path, [gnss_fix()], check_interval=3600)
    stop = asyncio.Event()
    task = asyncio.create_task(daemon.run_stationary(stop))
    await asyncio.sleep(0.3)
    assert daemon.receiver.switches == 1  # the first fine fix
    assert runtime(tmp_path)["fixing"] is False
    location = await asyncio.wait_for(daemon.cmd_fix({}), 5)
    stop.set()
    await task
    assert daemon.receiver.switches == 2  # not due, but requested
    assert location["state"] == "confirmed"
    assert location["fix"]["lat"] == pytest.approx(50.81)


@pytest.mark.asyncio
async def test_a_requested_fine_fix_without_receiver_answers_with_an_error(tmp_path):
    daemon, _ = make_daemon(tmp_path, [gnss_fix()], receiver=FakeReceiver(fail=True))
    stop = asyncio.Event()
    task = asyncio.create_task(daemon.run_stationary(stop))
    await asyncio.sleep(0.1)
    with pytest.raises(RequestError) as e:
        await asyncio.wait_for(daemon.cmd_fix({}), 5)
    stop.set()
    await task
    assert e.value.type == "no_receiver"


@pytest.mark.asyncio
async def test_a_fine_fix_is_only_requested_in_stationary_mode(tmp_path):
    daemon, _ = make_daemon(tmp_path, mode="mobile")
    with pytest.raises(RequestError) as e:
        await daemon.cmd_fix({})
    assert e.value.type == "not_available"
    assert not daemon.requested.is_set()


@pytest.fixture
def socket_dir():
    """A short directory: Unix socket paths are limited to about 100 characters (macOS: 104)."""
    import shutil
    import tempfile

    directory = Path(tempfile.mkdtemp(dir="/tmp", prefix="tsl"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.asyncio
async def test_control_socket_and_cli(tmp_path, socket_dir, capsys):
    from app.location import cli
    from app.location.server import ControlServer, events, request

    daemon, _ = make_daemon(tmp_path, [gnss_fix()])
    await daemon.check()
    daemon.cell = CELL
    server = ControlServer(socket_dir / "tslocation.sock", daemon.handlers())
    await server.start()
    try:
        status = await request(server.path, "status")
        assert status["location"]["state"] == "confirmed"
        assert (await request(server.path, "cells"))["known"] == [CELL.as_dict()]
        assert (await request(server.path, "config"))["settings"]["mode"] == "stationary"
        with pytest.raises(RequestError) as e:
            await request(server.path, "nonsense")
        assert e.value.type == "unknown_command"

        # watch: the current location, then every change
        stream = events(server.path, "watch")
        first = await anext(stream)
        assert first["state"] == "confirmed"
        daemon.fixing = True
        daemon.publish_location()
        assert (await asyncio.wait_for(anext(stream), 2))["fixing"] is True
        await stream.aclose()

        # the CLI is synchronous (asyncio.run): run it in a thread next to the server
        socket = str(server.path)
        assert await asyncio.to_thread(cli.main, ["--socket", socket, "status"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "confirmed" in out and "50.8100000, 8.7700000" in out
        assert await asyncio.to_thread(cli.main, ["--socket", socket, "cells"]) == cli.EXIT_OK
        assert "01234567" in capsys.readouterr().out
        assert await asyncio.to_thread(cli.main, ["--socket", socket, "config", "--json"]) == cli.EXIT_OK
        assert json.loads(capsys.readouterr().out)["settings"]["fix_max_age"] == 7 * 86400
    finally:
        await server.stop()


def test_cli_without_daemon(socket_dir, capsys):
    from app.location import cli

    assert cli.main(["--socket", str(socket_dir / "missing.sock"), "status"]) == cli.EXIT_UNREACHABLE
    assert "cannot reach tslocationd" in capsys.readouterr().err
    # without a command: status
    assert cli.main(["--socket", str(socket_dir / "missing.sock")]) == cli.EXIT_UNREACHABLE
    assert "cannot reach tslocationd" in capsys.readouterr().err


def test_cli_formats_settings_like_the_file():
    from app.location.cli import fmt_setting

    assert fmt_setting("fix_max_age", 604800) == "7d"
    assert fmt_setting("track_interval", 1) == "1s"
    assert fmt_setting("threshold", 50.0) == "50"


@pytest.mark.asyncio
async def test_off_mode_reports_nothing(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [gnss_fix()], mode="off", chrony_age=2 * 86400)
    stop = asyncio.Event()
    task = asyncio.create_task(daemon.run_off(stop))
    await asyncio.sleep(1.5)
    stop.set()
    await task
    assert recorder.kinds() == []
    assert daemon.receiver.switches == 1  # the fine fix for the clock
    assert runtime(tmp_path)["mode"] == "off"
    assert runtime(tmp_path)["effective"]["source"] == "reference"


@pytest.mark.asyncio
async def test_mobile_mode_is_unknown_until_the_first_fix(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [], mode="mobile")
    stop = asyncio.Event()
    task = asyncio.create_task(daemon.run_mobile(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await task
    assert runtime(tmp_path)["state"] == "unknown"  # until a fix, or fix_timeout without one
    assert recorder.kinds() == ["state"]


@pytest.mark.asyncio
async def test_mobile_mode_tracks_and_publishes(tmp_path):
    daemon, recorder = make_daemon(tmp_path, [gnss_fix(), gnss_fix(50.8102)], mode="mobile")
    stop = asyncio.Event()
    task = asyncio.create_task(daemon.run_mobile(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await task
    assert "track" in recorder.kinds()
    assert "fix" not in recorder.kinds()  # fine fixes only; the track goes out on its own topic
    location = runtime(tmp_path)
    assert location["state"] == "tracking"
    assert location["effective"]["source"] == "gnss"
    assert location["effective"]["lat"] == pytest.approx(50.8102)
    assert not daemon.receiver.on


def test_gnss_fix_before_the_clock_sync_goes_to_its_file(tmp_path):
    """A fix carries GNSS time: it goes to its day's file, while the clock's cells are dropped."""
    clock = FakeClock(local(1970, 1, 1, 0, 5).timestamp())
    files = DataFiles(tmp_path, "station")
    files.write(cell_sample(CELL, False, clock))
    files.write(fix_sample("fix", gnss_fix(), "modem", None, clock))
    assert not (tmp_path / "station" / "tslocation" / "cell").exists()
    fix_files = list((tmp_path / "station" / "tslocation" / "fix").iterdir())
    assert [f.name for f in fix_files] == ["station_2026-10-22_fix.csv"]


def test_over_mqtt_only_fixes_carry_a_time():
    clock = FakeClock(NOW, synced=True)
    fix = fix_sample("fix", gnss_fix(), "modem", None, clock)
    cell = cell_sample(CELL, False, clock)
    assert fix.header[0] == "time"
    assert "time" not in cell.header and "synced" not in cell.header


def test_mqtt_retains_nothing():
    class Client:
        def __init__(self):
            self.published = []

        def publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, retain))

    client = Client()
    output = MqttOutput("station", client=client)
    clock = FakeClock(NOW, synced=True)
    output.write(
        state_sample(
            {"mode": "off", "state": "unknown", "effective": None, "deviation_m": None, "proposed": None}, clock
        )
    )
    assert [topic for topic, _ in client.published] == [f"station/tslocation/state/{fmt}" for fmt in ENCODERS]
    assert not any(retain for _, retain in client.published)


def test_state_has_no_file(tmp_path):
    clock = FakeClock(NOW, synced=True)
    files = DataFiles(tmp_path, "station")
    files.write(
        state_sample(
            {"mode": "off", "state": "unknown", "effective": None, "deviation_m": None, "proposed": None}, clock
        )
    )
    assert not (tmp_path / "station" / "tslocation" / "state").exists()


def test_runtime_files_are_written_only_on_change(tmp_path):
    files = RuntimeFiles(tmp_path)
    location = {"mode": "stationary", "state": "unknown"}
    assert files.write(location, REFERENCE)
    assert not files.write(location, REFERENCE)
    assert files.write(location, None)
    assert not (tmp_path / "geolocation").exists()


# --- tsconfig: /api/location and tslocation.yml


@pytest.mark.asyncio
async def test_api_location_returns_the_runtime_file(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "LOCATION_FILE", tmp_path / "location.json")
    assert await main.get_location() is None
    (tmp_path / "location.json").write_text('{"mode": "stationary", "state": "confirmed"}')
    assert await main.get_location() == {"mode": "stationary", "state": "confirmed"}


def test_tslocation_config_validates_like_the_daemon(tmp_path):
    from app.configs.registry import RECOGNIZED_CONFIG_FILES, SERVICE_MAPPING
    from app.configs.tslocation import TslocationConfig

    assert RECOGNIZED_CONFIG_FILES["tslocation.yml"] == "tslocation"
    assert SERVICE_MAPPING["tslocation"] == "tslocation"
    config = TslocationConfig(tmp_path)
    assert config.load() == {}
    assert config.validate({"mode": "mobile", "fix_max_age": "7d"}) == []
    assert config.validate({"mode": "driving"})[0].startswith("mode:")
    config.save({"mode": "off", "fix_max_age": "7d"})
    assert config.load() == {"mode": "off", "fix_max_age": "7d"}


@pytest.mark.asyncio
async def test_api_requests_a_fine_fix_over_the_socket(monkeypatch):
    from app import main

    async def answer(path, cmd, args, timeout):
        if answer.error:
            raise answer.error
        return {"requested": True, "cmd": cmd, "args": args}

    monkeypatch.setattr(main.location_server, "request", answer)
    answer.error = None
    assert await main.request_location_fix() == {"requested": True, "cmd": "fix", "args": {"wait": False}}
    answer.error = main.location_server.RequestError("not_available", "not in mobile mode")
    assert (await main.request_location_fix()).status_code == 409
    answer.error = FileNotFoundError("no socket")
    assert (await main.request_location_fix()).status_code == 503


@pytest.mark.asyncio
async def test_settings_page_api(monkeypatch):
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.routers import tslocation

    defaults = await tslocation.tslocation_defaults()
    assert defaults["mode"] == "stationary" and defaults["fix_max_age"] == 7 * 86400

    written = []

    async def write_config(config_type, config):
        written.append((config_type, config))

    async def unsupported(_types):
        return {}

    monkeypatch.setattr("app.routers.base.write_config", write_config)
    monkeypatch.setattr("app.routers.base.support.unsupported_config_types", unsupported)
    update = tslocation.TslocationConfigUpdate(mode="mobile", fix_max_age="7d", threshold=25)
    await tslocation.update_tslocation(update, None)
    assert written == [("tslocation", {"mode": "mobile", "fix_max_age": "7d", "threshold": 25.0})]

    with pytest.raises(HTTPException) as e:
        await tslocation.update_tslocation(tslocation.TslocationConfigUpdate(fix_max_age="soon"), None)
    assert e.value.status_code == 400
    with pytest.raises(ValidationError):
        tslocation.TslocationConfigUpdate(unknown=1)


def test_track_points_follow_gnss_seconds(tmp_path):
    daemon, _ = make_daemon(tmp_path, mode="mobile", track_interval="2s", track_distance=100)
    first = tpv_fix(tpv(time="2026-10-22T12:00:00.000Z"), {})
    assert daemon.track_due(first, None, 0)
    assert not daemon.track_due(tpv_fix(tpv(time="2026-10-22T12:00:01.000Z"), {}), first, 0)
    assert daemon.track_due(tpv_fix(tpv(time="2026-10-22T12:00:02.000Z"), {}), first, 0)
    assert daemon.track_due(tpv_fix(tpv(lat=50.812, time="2026-10-22T12:00:01.000Z"), {}), first, 0)


def test_publish_interval_is_gone():
    assert "publish_interval" not in LocationSettings.model_fields
    assert LocationSettings().track_interval == 1
