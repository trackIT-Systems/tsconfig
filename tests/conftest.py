"""Fixtures shared across test modules."""

import sys

import pytest

from tests.ble_helpers import install_fake_dbus


@pytest.fixture
def chars(monkeypatch):
    """app.bluetooth.gatt_characteristics, imported against a fake `dbus` package."""
    module = install_fake_dbus(monkeypatch)
    yield module
    sys.modules.pop("app.bluetooth.gatt_characteristics", None)
    sys.modules.pop("app.bluetooth.services", None)
