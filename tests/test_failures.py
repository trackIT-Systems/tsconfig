"""Failures that repeat are logged once per change of state (app/utils/failures.py) where they repeat."""

import asyncio
import logging
import subprocess
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.utils.failures import FailureLog, describe

LOGGER = "test.failures"


def levels(caplog, name=None):
    """(level name, message) of the records above DEBUG."""
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.levelno > logging.DEBUG and (name is None or record.name == name)
    ]


@pytest.fixture
def failures():
    return FailureLog(logging.getLogger(LOGGER))


class TestFailureLog:
    def test_a_repeating_failure_is_logged_once_and_its_recovery_once(self, failures, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            for _ in range(3):
                failures.failed("Modem readout", OSError("no modem"))
            failures.succeeded("Modem readout")
            failures.succeeded("Modem readout")
        assert levels(caplog) == [
            ("WARNING", "Modem readout failed: OSError: no modem"),
            ("INFO", "Modem readout works again"),
        ]
        repeats = [r for r in caplog.records if r.getMessage() == "Modem readout failed again: OSError: no modem"]
        assert len(repeats) == 2 and all(r.levelno == logging.DEBUG for r in repeats)

    def test_a_changed_error_is_logged_again(self, failures, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER):
            failures.failed("timedatectl status", "timed out")
            failures.failed("timedatectl status", "timed out")
            failures.failed("timedatectl status", "exit status 1")
        assert levels(caplog) == [
            ("WARNING", "timedatectl status failed: timed out"),
            ("WARNING", "timedatectl status failed: exit status 1"),
        ]

    def test_keys_are_independent(self, failures, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER):
            failures.failed("a", "broken")
            failures.failed("b", "broken")
            failures.succeeded("a")
        assert levels(caplog) == [
            ("WARNING", "a failed: broken"),
            ("WARNING", "b failed: broken"),
            ("INFO", "a works again"),
        ]
        assert failures.failing("b") and not failures.failing("a")

    def test_the_level_can_be_chosen(self, caplog):
        failures = FailureLog(logging.getLogger(LOGGER), level=logging.ERROR)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            failures.failed("a", "broken")
            failures.failed("b", "broken", level=logging.WARNING)
        assert [level for level, _ in levels(caplog)] == ["ERROR", "WARNING"]

    def test_a_traceback_is_only_logged_at_debug(self, failures, caplog):
        try:
            raise ValueError("bad")
        except ValueError as e:
            error = e
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            failures.failed("a", error)
        with_traceback = [r for r in caplog.records if r.exc_info]
        assert len(with_traceback) == 1 and with_traceback[0].levelno == logging.DEBUG

    def test_success_without_a_failure_logs_nothing(self, failures, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            failures.succeeded("a")
        assert caplog.records == []

    def test_describe(self):
        assert describe(TimeoutError("slow")) == "TimeoutError: slow"
        assert describe("exit status 1") == "exit status 1"


@pytest.mark.asyncio
class TestStatusPage:
    """The status page polls these every few seconds."""

    @pytest.fixture(autouse=True)
    def fresh_state(self, monkeypatch):
        from app import main

        monkeypatch.setattr(main, "_status_failures", FailureLog(main.logger))
        monkeypatch.setattr(main, "_tsupdate_status_cache", None)

    async def test_a_failing_tsupdate_status_is_warned_about_once(self, caplog):
        from app import main

        failing = AsyncMock(return_value=subprocess.CompletedProcess(["tsupdate"], 1, stdout="", stderr="boom\n"))
        working = AsyncMock(
            return_value=subprocess.CompletedProcess(["tsupdate"], 0, stdout='{"active_partition": "p2"}', stderr="")
        )
        with caplog.at_level(logging.INFO, logger="app.main"):
            with patch.object(main, "run_subprocess_async", new=failing):
                for _ in range(3):
                    assert await main._get_tsupdate_status() is None
            with patch.object(main, "run_subprocess_async", new=working):
                assert await main._get_tsupdate_status() is not None
        assert levels(caplog, "app.main") == [
            ("WARNING", "tsupdate status failed: boom"),
            ("INFO", "tsupdate status works again"),
        ]

    async def test_a_failing_timedatectl_is_warned_about_once(self, caplog):
        from app import main

        def run(argv, **_kwargs):
            if argv[1] == "--version":
                return subprocess.CompletedProcess(argv, 0, stdout="systemd 257", stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Failed to connect to bus\n")

        with (
            caplog.at_level(logging.INFO, logger="app.main"),
            patch.object(main, "run_subprocess_async", new=AsyncMock(side_effect=run)),
        ):
            for _ in range(3):
                await main.get_timedatectl_status()
        # The readout's own "timedatectl status failed: " is not repeated
        assert levels(caplog, "app.main") == [("WARNING", "timedatectl status failed: Failed to connect to bus")]


@pytest.mark.asyncio
async def test_a_failing_modem_readout_is_logged_once(monkeypatch, caplog):
    from app.routers import network

    monkeypatch.setattr(network, "_modem_failures", FailureLog(network.logger))
    read = AsyncMock(side_effect=RuntimeError("mmcli output changed"))
    monkeypatch.setattr(network.modem, "read_modem", read)
    with caplog.at_level(logging.INFO, logger="app.routers.network"):
        for _ in range(3):
            assert await network.get_modem_info() is None
        read.side_effect = None
        read.return_value = None
        assert await network.get_modem_info() is None
    assert levels(caplog, "app.routers.network") == [
        ("ERROR", "Modem readout failed: RuntimeError: mmcli output changed"),
        ("INFO", "Modem readout works again"),
    ]


@pytest.mark.asyncio
async def test_a_failing_systemctl_show_is_warned_about_once(monkeypatch, caplog):
    from app.routers import systemd

    monkeypatch.setattr(systemd, "_service_failures", FailureLog(systemd.logger))
    monkeypatch.setattr(systemd, "run_subprocess_async", AsyncMock(side_effect=TimeoutError()))
    with caplog.at_level(logging.INFO, logger="app.routers.systemd"):
        for _ in range(3):
            info = await systemd.get_service_info(systemd.ServiceConfig(name="radiotracking"))
            assert info.status == "timeout"
    assert levels(caplog, "app.routers.systemd") == [("WARNING", "systemctl show radiotracking failed: timed out")]


@pytest.mark.asyncio
async def test_an_unreachable_api_is_logged_once_by_the_ble_gateway(caplog):
    from app.bluetooth.api_client import TsConfigApiClient

    up = False

    def handler(request):
        if not up:
            raise httpx.ConnectError("All connection attempts failed")
        return httpx.Response(200, json={})

    api = TsConfigApiClient()
    api.client = httpx.AsyncClient(base_url="http://localhost:8000", transport=httpx.MockTransport(handler))
    api._client_loop = asyncio.get_running_loop()
    with caplog.at_level(logging.INFO, logger="app.bluetooth.api_client"):
        for request in (api.get_system_status, api.get_systemd_services, api.get_system_status):
            with pytest.raises(httpx.ConnectError):
                await request()
        up = True
        await api.get_system_status()
    await api.close()
    assert levels(caplog, "app.bluetooth.api_client") == [
        ("ERROR", "tsconfig API failed: ConnectError: All connection attempts failed"),
        ("INFO", "tsconfig API works again"),
    ]


@pytest.mark.asyncio
async def test_an_error_answer_is_logged_once_per_request_by_the_ble_gateway(caplog):
    from app.bluetooth.api_client import TsConfigApiClient

    api = TsConfigApiClient()
    api.client = httpx.AsyncClient(
        base_url="http://localhost:8000", transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    api._client_loop = asyncio.get_running_loop()
    with caplog.at_level(logging.INFO, logger="app.bluetooth.api_client"):
        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await api.get_system_status()
    await api.close()
    assert levels(caplog, "app.bluetooth.api_client") == [
        ("WARNING", "GET /api/system-status failed: HTTP 500 Internal Server Error"),
    ]


def test_a_refused_mqtt_connection_is_warned_about_once(caplog):
    from app.telemetry.outputs import MqttOutput
    from app.telemetry.settings import BrokerSettings

    class Client:
        def will_set(self, *args, **kwargs):
            pass

        def publish(self, *args, **kwargs):
            pass

    class Refused:
        is_failure = True

        def __str__(self):
            return "Not authorized"

    client = Client()
    output = MqttOutput("station", BrokerSettings(), ["json"], client=client)
    with caplog.at_level(logging.INFO, logger="app.telemetry.outputs"):
        for _ in range(3):
            output._on_connect(client, None, None, Refused())
        output._on_connect(client, None, None, 0)
        output._on_connect(client, None, None, Refused())
    assert [level for level, _ in levels(caplog, "app.telemetry.outputs")] == ["WARNING", "INFO", "WARNING"]
