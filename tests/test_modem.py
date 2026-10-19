"""ModemManager readout shared by GET /api/network/modem and the modem collector."""

import json
import subprocess

import pytest
from fastapi import HTTPException

from app.routers import network
from app.system import modem

MODEM = {
    "modem": {
        "generic": {
            "manufacturer": "Quectel",
            "model": "EG25",
            "equipment-identifier": "861234567890123",
            "signal-quality": {"value": "71", "recent": "yes"},
            "access-technologies": ["lte"],
            "state": "connected",
            "sim": "/org/freedesktop/ModemManager1/SIM/0",
        },
        "3gpp": {"operator-name": "Telekom.de"},
        "signal": {"lte": {"rssi": "-61"}},
        "bearers": ["/org/freedesktop/ModemManager1/Bearer/1"],
    }
}
ANSWERS = {
    "-L": {"modem-list": ["/org/freedesktop/ModemManager1/Modem/0"]},
    "-m": MODEM,
    "-b": {"bearer": {"ipv4-config": {"address": "10.0.0.2"}, "ipv6-config": {}}},
    "-i": {"sim": {"properties": {"iccid": "8949", "imsi": "26201", "operator-name": "Telekom"}}},
}


def fake_mmcli(answers=ANSWERS, error=None):
    async def run(cmd, **_kwargs):
        if error:
            raise error
        answer = answers.get(cmd[1])
        if answer is None:
            return subprocess.CompletedProcess(cmd, 1, "", "error")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(answer), "")

    return run


@pytest.mark.asyncio
async def test_read_modem_collects_modem_bearer_and_sim(monkeypatch):
    monkeypatch.setattr(modem, "run_subprocess_async", fake_mmcli())
    info = await modem.read_modem()
    assert info.model == "EG25"
    assert info.signal_strength_percent == 71 and info.signal_strength_dbm == -61
    assert info.access_technology == "lte"
    assert info.operator_name == "Telekom.de"
    assert info.ip_addresses == ["10.0.0.2"]
    assert (info.sim_iccid, info.sim_imsi) == ("8949", "26201")


@pytest.mark.asyncio
async def test_no_modem_is_none(monkeypatch):
    monkeypatch.setattr(modem, "run_subprocess_async", fake_mmcli({"-L": {"modem-list": []}}))
    assert await modem.read_modem() is None


@pytest.mark.asyncio
async def test_router_maps_missing_mmcli_to_503(monkeypatch):
    monkeypatch.setattr(modem, "run_subprocess_async", fake_mmcli(error=FileNotFoundError("mmcli")))
    with pytest.raises(HTTPException) as e:
        await network.get_modem_info()
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_router_maps_timeout_to_504(monkeypatch):
    monkeypatch.setattr(modem, "run_subprocess_async", fake_mmcli(error=TimeoutError()))
    with pytest.raises(HTTPException) as e:
        await network.get_modem_info()
    assert e.value.status_code == 504
