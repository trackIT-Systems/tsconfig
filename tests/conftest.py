"""Fixtures shared across test modules."""

import subprocess
import sys

import pytest

from app.configs import support
from tests.ble_helpers import install_fake_dbus


@pytest.fixture
def chars(monkeypatch):
    """app.bluetooth.gatt_characteristics, imported against a fake `dbus` package."""
    module = install_fake_dbus(monkeypatch)
    yield module
    sys.modules.pop("app.bluetooth.gatt_characteristics", None)
    sys.modules.pop("app.bluetooth.services", None)


@pytest.fixture(autouse=True)
def missing_units(monkeypatch):
    """Answer app.configs.support's `systemctl show` with every unit present,
    so tests do not depend on the host's units. Add a unit name (e.g.
    "soundscapepipe") to the returned set to make it missing."""
    missing = set()

    async def systemctl_show(cmd, **_kwargs):
        units = [arg.removesuffix(".service") for arg in cmd if arg.endswith(".service")]
        states = ["not-found" if unit in missing else "loaded" for unit in units]
        return subprocess.CompletedProcess(cmd, 0, "\n\n".join(f"LoadState={state}" for state in states) + "\n", "")

    monkeypatch.setattr(support, "run_subprocess_async", systemctl_show)
    return missing
