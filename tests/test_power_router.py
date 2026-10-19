"""Tests for app.routers.power: a read-only proxy to the power daemons' control
sockets. Requests are forwarded as they come and replies returned unchanged;
only `status`, `cached` and `read` reach a daemon, and a daemon that is absent
or silent gives null.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import power

CACHED_REPLY = {
    "id": 1,
    "ok": True,
    "result": {
        "device": "HQ22294ZGEU",
        "blocks": {
            "advert": {
                "enabled": True,
                "time": "2026-10-19T09:58:30.862Z",
                "age_s": 0.2,
                "stale": False,
                "values": [{"name": "battery_voltage", "friendly": "Battery voltage", "unit": "V", "value": 13.3}],
            },
        },
    },
}


@pytest.fixture
def short_dir():
    """A directory for sockets: pytest's tmp_path is too long for AF_UNIX on macOS (104 bytes)."""
    with tempfile.TemporaryDirectory(dir="/tmp") as path:
        yield Path(path)


@pytest.fixture
def daemon(short_dir, monkeypatch):
    """A fake daemon on a unix socket: records each request line, answers with `reply`."""
    socket_path = short_dir / "smartsolar.sock"
    monkeypatch.setitem(power.BACKENDS, "smartsolar", socket_path)
    state = {"received": [], "reply": CACHED_REPLY, "silent": False}

    async def handle(reader, writer):
        line = await reader.readline()
        state["received"].append(json.loads(line))
        if state["silent"]:
            await reader.read()  # until the client gives up and hangs up
            writer.close()
            return
        writer.write(json.dumps(state["reply"]).encode() + b"\n")
        await writer.drain()
        writer.close()

    async def start():
        state["server"] = await asyncio.start_unix_server(handle, path=socket_path)
        return state

    return start


@pytest.mark.asyncio
async def test_cached_request_is_forwarded_and_the_reply_returned_unchanged(daemon):
    state = await daemon()
    async with state["server"]:
        reply = await power.power_request("smartsolar", power.DaemonRequest(cmd="cached", args={"block": "all"}))
    assert reply == CACHED_REPLY
    assert state["received"] == [{"id": 1, "cmd": "cached", "args": {"block": "all"}}]


@pytest.mark.asyncio
async def test_status_is_forwarded_without_args(daemon):
    state = await daemon()
    async with state["server"]:
        await power.power_request("smartsolar", power.DaemonRequest(cmd="status"))
    assert state["received"] == [{"id": 1, "cmd": "status"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(("cmd", "timeout"), [("status", 2.0), ("cached", 2.0), ("read", 120.0)])
async def test_read_commands_are_forwarded_with_their_timeout(monkeypatch, cmd, timeout):
    calls = []

    async def fake_forward(socket_path, request, timeout):
        calls.append((request, timeout))
        return {"id": 1, "ok": True, "result": {}}

    monkeypatch.setattr(power, "forward", fake_forward)
    await power.power_request("solarlife", power.DaemonRequest(cmd=cmd, args={"block": "status"}))
    assert calls == [({"id": 1, "cmd": cmd, "args": {"block": "status"}}, timeout)]


@pytest.mark.asyncio
async def test_a_daemon_error_is_returned_unchanged(daemon):
    state = await daemon()
    state["reply"] = {"id": 1, "ok": False, "error": {"type": "no_data", "message": "nothing read yet"}}
    async with state["server"]:
        reply = await power.power_request("smartsolar", power.DaemonRequest(cmd="cached"))
    assert reply == state["reply"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["write", "scan", "rescan", "reconnect", "fetch_key", "settings"])
async def test_other_commands_never_reach_the_daemon(daemon, cmd):
    state = await daemon()
    async with state["server"]:
        with pytest.raises(HTTPException) as exc_info:
            await power.power_request("smartsolar", power.DaemonRequest(cmd=cmd, args={"name": "x", "value": 1}))
    assert exc_info.value.status_code == 400
    assert state["received"] == []


@pytest.mark.asyncio
async def test_unknown_source_is_404():
    with pytest.raises(HTTPException) as exc_info:
        await power.power_request("victron", power.DaemonRequest(cmd="status"))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_no_socket_gives_null(tmp_path, monkeypatch):
    monkeypatch.setitem(power.BACKENDS, "solarlife", tmp_path / "missing.sock")
    assert await power.power_request("solarlife", power.DaemonRequest(cmd="cached")) is None


@pytest.mark.asyncio
async def test_a_silent_daemon_gives_null_after_the_timeout(daemon):
    state = await daemon()
    state["silent"] = True
    async with state["server"]:
        reply = await power.forward(power.BACKENDS["smartsolar"], {"id": 1, "cmd": "status"}, timeout=0.1)
    assert reply is None


@pytest.mark.asyncio
async def test_a_reply_that_is_not_json_gives_null(daemon):
    state = await daemon()
    state["reply"] = "not an object"
    async with state["server"]:
        assert await power.power_request("smartsolar", power.DaemonRequest(cmd="status")) is None
