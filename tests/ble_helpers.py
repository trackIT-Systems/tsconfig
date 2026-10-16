"""Shared helpers for the BLE characteristic tests. BlueZ/dbus are not available in
the test environment, so the characteristics are imported against a minimal fake
`dbus` package (see the `chars` fixture in conftest.py)."""

import importlib
import json
import sys
import types
from unittest.mock import AsyncMock

DEVICE = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
OTHER = "/org/bluez/hci0/dev_11_22_33_44_55_66"


def install_fake_dbus(monkeypatch):
    dbus = types.ModuleType("dbus")
    service = types.ModuleType("dbus.service")
    exceptions = types.ModuleType("dbus.exceptions")

    class Object:
        def __init__(self, bus=None, path=None):
            pass

    def method(*a, **k):
        return lambda f: f

    service.Object = Object
    service.method = method
    service.signal = method
    exceptions.DBusException = type("DBusException", (Exception,), {})
    dbus.service = service
    dbus.exceptions = exceptions
    dbus.Bus = object
    dbus.Array = lambda value, signature=None: list(value)
    dbus.ObjectPath = str
    dbus.Interface = lambda *a, **k: None
    for name, module in {"dbus": dbus, "dbus.service": service, "dbus.exceptions": exceptions}.items():
        monkeypatch.setitem(sys.modules, name, module)
    for name in ("app.bluetooth.gatt_characteristics", "app.bluetooth.services"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("app.bluetooth.gatt_characteristics")


class FakeService:
    path = "/org/bluez/tsconfig/app/service0001"

    def get_path(self):
        return self.path


def make_write_char(chars, authenticator, handler=None):
    handler = handler or AsyncMock(return_value={"ok": True})
    char = chars.WriteOnlyCharacteristic(
        None, 1, "uuid", FakeService(), types.SimpleNamespace(client=None), handler, authenticator
    )
    char.notifying = True
    sent = []
    char.send_notification = lambda data: sent.append(json.loads(data))
    char.send_chunked_response = lambda data, device=None: sent.append(json.loads(data))
    return char, handler, sent
