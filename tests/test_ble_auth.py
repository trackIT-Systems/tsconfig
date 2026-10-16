"""Tests for the BLE operator login (app.bluetooth.auth) and for how the GATT
characteristics use it. BlueZ/dbus are not available in the test environment,
so the characteristic tests run against a minimal fake `dbus` package."""

import asyncio
import importlib
import json
import sys
import types
from unittest.mock import AsyncMock

import pytest

from app.auth import session
from app.bluetooth.auth import BleAuthenticator, secure_flags
from app.bluetooth.protocol import ResponseFormatter

DEVICE = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
OTHER = "/org/bluez/hci0/dev_11_22_33_44_55_66"


@pytest.fixture(autouse=True)
def clean_limiters():
    session.reset_login_state()
    yield


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestSecureFlags:
    def test_maps_every_access_flag_to_its_encrypted_variant(self):
        assert secure_flags(["read", "notify"]) == ["encrypt-read", "encrypt-notify"]
        assert secure_flags(["write", "notify"]) == ["encrypt-write", "encrypt-notify"]

    def test_leaves_other_flags_alone(self):
        assert secure_flags(["extended-properties"]) == ["extended-properties"]


class TestBleAuthenticator:
    @pytest.mark.asyncio
    async def test_login_unlocks_only_that_connection(self):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        assert not auth.is_authenticated(DEVICE)
        assert (await auth.login(DEVICE, "natur")).ok
        assert auth.is_authenticated(DEVICE)
        assert not auth.is_authenticated(OTHER)

    @pytest.mark.asyncio
    async def test_verifies_the_operator_account_with_the_given_password(self):
        verify = AsyncMock(return_value=True)
        await BleAuthenticator(verify=verify).login(DEVICE, "natur")
        verify.assert_awaited_once_with("pi", "natur")

    @pytest.mark.asyncio
    async def test_wrong_password_stays_locked_with_401(self):
        auth = BleAuthenticator(verify=AsyncMock(return_value=False))
        result = await auth.login(DEVICE, "nope")
        assert (result.ok, result.code) == (False, 401)
        assert not auth.is_authenticated(DEVICE)

    @pytest.mark.asyncio
    async def test_unknown_connection_cannot_log_in(self):
        verify = AsyncMock(return_value=True)
        result = await BleAuthenticator(verify=verify).login(None, "natur")
        assert not result.ok
        verify.assert_not_called()

    @pytest.mark.asyncio
    async def test_backs_off_after_repeated_failures_without_asking_pam(self):
        verify = AsyncMock(return_value=False)
        auth = BleAuthenticator(verify=verify)
        for _ in range(6):
            await auth.login(DEVICE, "nope")
        calls = verify.await_count
        blocked = await auth.login(DEVICE, "nope")
        assert blocked.code == 429 and blocked.retry_after > 0
        assert verify.await_count == calls

    @pytest.mark.asyncio
    async def test_correct_password_is_refused_while_backing_off(self):
        verify = AsyncMock(return_value=False)
        auth = BleAuthenticator(verify=verify)
        for _ in range(6):
            await auth.login(DEVICE, "nope")
        verify.return_value = True
        assert (await auth.login(DEVICE, "natur")).code == 429
        assert not auth.is_authenticated(DEVICE)

    @pytest.mark.asyncio
    async def test_other_connections_are_not_blocked_by_one_noisy_one(self):
        verify = AsyncMock(return_value=False)
        auth = BleAuthenticator(verify=verify)
        for _ in range(6):
            await auth.login(DEVICE, "nope")
        verify.return_value = True
        assert (await auth.login(OTHER, "natur")).ok

    @pytest.mark.asyncio
    async def test_disconnect_ends_the_login(self):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        await auth.login(DEVICE, "natur")
        auth.forget(DEVICE)
        assert not auth.is_authenticated(DEVICE)

    @pytest.mark.asyncio
    async def test_login_expires(self):
        clock = FakeClock()
        auth = BleAuthenticator(verify=AsyncMock(return_value=True), ttl_seconds=100, clock=clock)
        await auth.login(DEVICE, "natur")
        clock.now += 99
        assert auth.is_authenticated(DEVICE)
        clock.now += 2
        assert not auth.is_authenticated(DEVICE)

    def test_forgetting_an_unknown_device_is_harmless(self):
        BleAuthenticator().forget("/nonexistent")


class TestBleStaffLogin:
    @pytest.mark.asyncio
    async def test_root_login_verifies_the_root_account(self):
        verify = AsyncMock(return_value=True)
        auth = BleAuthenticator(verify=verify)
        assert (await auth.login(DEVICE, "rootpw", "root")).ok
        verify.assert_awaited_once_with("root", "rootpw")
        assert auth.is_authenticated(DEVICE)

    @pytest.mark.asyncio
    async def test_username_defaults_to_the_operator(self):
        verify = AsyncMock(return_value=True)
        await BleAuthenticator(verify=verify).login(DEVICE, "natur")
        verify.assert_awaited_once_with("pi", "natur")

    @pytest.mark.asyncio
    async def test_non_string_username_is_refused_without_a_check(self):
        verify = AsyncMock(return_value=True)
        result = await BleAuthenticator(verify=verify).login(DEVICE, "x", 5)
        assert not result.ok
        verify.assert_not_called()

    @pytest.mark.asyncio
    async def test_root_backs_off_sooner_and_independently_of_the_operator(self):
        verify = AsyncMock(return_value=False)
        auth = BleAuthenticator(verify=verify)
        for _ in range(3):
            await auth.login(DEVICE, "x", "root")
        assert (await auth.login(DEVICE, "x", "root")).code == 429
        verify.return_value = True
        assert (await auth.login(DEVICE, "natur", "pi")).ok  # the operator is unaffected

    @pytest.mark.asyncio
    async def test_the_same_device_can_log_in_as_either_account(self):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        assert (await auth.login(DEVICE, "a", "pi")).ok and (await auth.login(OTHER, "b", "root")).ok
        assert auth.is_authenticated(DEVICE) and auth.is_authenticated(OTHER)


class TestFormatter:
    def test_auth_required_is_401(self):
        assert json.loads(ResponseFormatter.auth_required()) == {"error": "Authentication required", "code": 401}

    def test_error_can_carry_extra_fields(self):
        assert json.loads(ResponseFormatter.error("slow down", 429, retry_after=7)) == {
            "error": "slow down",
            "code": 429,
            "retry_after": 7,
        }


# --- characteristic wiring, against a fake dbus ---------------------------


def _install_fake_dbus(monkeypatch):
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


@pytest.fixture
def chars(monkeypatch):
    module = _install_fake_dbus(monkeypatch)
    yield module
    sys.modules.pop("app.bluetooth.gatt_characteristics", None)
    sys.modules.pop("app.bluetooth.services", None)


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


class TestCharacteristicGating:
    def test_open_characteristics_are_not_encrypted_and_always_authorized(self, chars):
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, AsyncMock())
        assert char.flags == ["read", "notify"]
        assert char.is_authorized({})

    def test_gated_characteristics_require_an_encrypted_link(self, chars):
        char, _, _ = make_write_char(chars, BleAuthenticator())
        assert char.flags == ["encrypt-write", "encrypt-notify"]

    def test_no_authenticator_means_open_and_unencrypted(self, chars):
        char, _, _ = make_write_char(chars, None)
        assert char.flags == ["write", "notify"]

    def test_write_before_login_is_refused_and_handler_not_called(self, chars):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        char, handler, sent = make_write_char(chars, auth)
        char.WriteValue(list(b'{"service": "x"}'), {"device": DEVICE})
        assert sent == [{"error": "Authentication required", "code": 401}]
        handler.assert_not_called()

    def test_write_after_login_reaches_the_handler(self, chars):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        asyncio.run(auth.login(DEVICE, "natur"))
        char, handler, _sent = make_write_char(chars, auth)
        char.WriteValue(list(b'{"service": "x"}'), {"device": DEVICE})
        handler.assert_awaited_once_with({"service": "x"})

    def test_another_connections_login_does_not_unlock_this_one(self, chars):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        asyncio.run(auth.login(OTHER, "natur"))
        char, handler, sent = make_write_char(chars, auth)
        char.WriteValue(list(b'{"service": "x"}'), {"device": DEVICE})
        handler.assert_not_called()
        assert sent[0]["code"] == 401

    def test_write_without_device_info_is_refused(self, chars):
        auth = BleAuthenticator(verify=AsyncMock(return_value=True))
        asyncio.run(auth.login(DEVICE, "natur"))
        char, handler, _sent = make_write_char(chars, auth)
        char.WriteValue(list(b"{}"), {})
        handler.assert_not_called()

    def test_gated_read_returns_error_metadata_without_calling_the_handler(self, chars):
        handler = AsyncMock(return_value={"services": []})
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, handler, BleAuthenticator())
        char.notifying = True
        replies = []
        char.ReadValue({"device": DEVICE}, replies.append, replies.append)
        handler.assert_not_called()
        import cbor2

        decoded = cbor2.loads(bytes(replies[0]))
        assert "Authentication required" in json.dumps(decoded, default=str)


class TestAuthenticateCharacteristic:
    def make(self, chars, verify):
        auth = BleAuthenticator(verify=verify)
        char = chars.AuthenticateCharacteristic(None, 0, FakeService(), types.SimpleNamespace(client=None), auth)
        char.notifying = True
        sent = []
        char.send_notification = lambda data: sent.append(json.loads(data))
        char.send_chunked_response = lambda data, device=None: sent.append(json.loads(data))
        return auth, char, sent

    def test_is_reachable_without_login_but_needs_an_encrypted_link(self, chars):
        _, char, _ = self.make(chars, AsyncMock())
        assert char.authenticator is None
        assert char.flags == ["encrypt-write", "encrypt-notify"]

    def test_correct_password_logs_the_connection_in(self, chars):
        auth, char, sent = self.make(chars, AsyncMock(return_value=True))
        char.WriteValue(list(b'{"password": "natur"}'), {"device": DEVICE})
        assert sent == [{"authenticated": True}]
        assert auth.is_authenticated(DEVICE)

    def test_wrong_password_is_a_401_and_stays_locked(self, chars):
        auth, char, sent = self.make(chars, AsyncMock(return_value=False))
        char.WriteValue(list(b'{"password": "nope"}'), {"device": DEVICE})
        assert sent[0]["code"] == 401 and "authenticated" not in sent[0]
        assert not auth.is_authenticated(DEVICE)

    def test_backoff_is_reported_with_retry_after(self, chars):
        _auth, char, sent = self.make(chars, AsyncMock(return_value=False))
        for _ in range(8):
            char.WriteValue(list(b'{"password": "nope"}'), {"device": DEVICE})
        assert sent[-1]["code"] == 429 and sent[-1]["retry_after"] >= 1

    def test_username_root_is_passed_through(self, chars):
        verify = AsyncMock(return_value=True)
        _auth, char, sent = self.make(chars, verify)
        char.WriteValue(list(b'{"username": "root", "password": "rootpw"}'), {"device": DEVICE})
        assert sent == [{"authenticated": True}]
        verify.assert_awaited_once_with("root", "rootpw")

    def test_username_defaults_to_pi(self, chars):
        verify = AsyncMock(return_value=True)
        _, char, _ = self.make(chars, verify)
        char.WriteValue(list(b'{"password": "natur"}'), {"device": DEVICE})
        verify.assert_awaited_once_with("pi", "natur")

    @pytest.mark.parametrize("body", [b'{"password": "x", "username": 5}', b'{"password": "x", "username": null}'])
    def test_malformed_username_is_an_error_not_a_login(self, chars, body):
        auth, char, sent = self.make(chars, AsyncMock(return_value=True))
        char.WriteValue(list(body), {"device": DEVICE})
        assert "error" in sent[0] and not auth.is_authenticated(DEVICE)

    @pytest.mark.parametrize("body", [b"{}", b'{"password": ""}', b'{"password": 5}'])
    def test_malformed_requests_are_errors_not_logins(self, chars, body):
        auth, char, sent = self.make(chars, AsyncMock(return_value=True))
        char.WriteValue(list(body), {"device": DEVICE})
        assert "error" in sent[0]
        assert not auth.is_authenticated(DEVICE)

    def test_current_device_is_not_left_set_after_a_write(self, chars):
        _, char, _ = self.make(chars, AsyncMock(return_value=True))
        char.WriteValue(list(b'{"password": "natur"}'), {"device": DEVICE})
        assert char.current_device is None


class TestHashLogin:
    PI_HASH = "$6$salt$digest"

    def make(self, chars, verify=None):
        verify_hash = lambda user, token: user == "pi" and token == self.PI_HASH
        auth = BleAuthenticator(verify=verify or AsyncMock(return_value=True), verify_hash=verify_hash)
        char = chars.AuthenticateCharacteristic(None, 0, FakeService(), types.SimpleNamespace(client=None), auth)
        char.notifying = True
        sent = []
        char.send_chunked_response = lambda data, device=None: sent.append(json.loads(data))
        return auth, char, sent

    def write(self, char, body):
        char.WriteValue(list(json.dumps(body).encode()), {"device": DEVICE})

    def test_matching_hash_logs_the_connection_in(self, chars):
        auth, char, sent = self.make(chars)
        self.write(char, {"hash": self.PI_HASH})
        assert sent == [{"authenticated": True}]
        assert auth.is_authenticated(DEVICE)

    def test_explicit_pi_username_is_accepted(self, chars):
        _auth, char, sent = self.make(chars)
        self.write(char, {"username": "pi", "hash": self.PI_HASH})
        assert sent == [{"authenticated": True}]

    def test_wrong_hash_is_a_401_and_stays_locked(self, chars):
        auth, char, sent = self.make(chars)
        self.write(char, {"hash": "$6$salt$other"})
        assert sent[0]["code"] == 401
        assert not auth.is_authenticated(DEVICE)

    def test_failed_hash_logins_back_off_the_password_login_too(self, chars):
        verify = AsyncMock(return_value=True)
        _auth, char, sent = self.make(chars, verify)
        for _ in range(8):
            self.write(char, {"hash": "$6$salt$other"})
        self.write(char, {"password": "natur"})
        assert sent[-1]["code"] == 429 and sent[-1]["retry_after"] >= 1
        verify.assert_not_awaited()

    @pytest.mark.parametrize(
        "body",
        [
            {"username": "root", "hash": "$6$salt$digest"},
            {"hash": "$6$salt$digest", "password": "natur"},
            {"hash": 5},
            {"hash": ""},
            {"hash": None},
        ],
    )
    def test_invalid_hash_requests_are_400_and_check_nothing(self, chars, body):
        verify = AsyncMock(return_value=True)
        auth, char, sent = self.make(chars, verify)
        self.write(char, body)
        assert sent[0]["code"] == 400
        assert not auth.is_authenticated(DEVICE)
        verify.assert_not_awaited()

    def test_password_login_is_unchanged(self, chars):
        verify = AsyncMock(return_value=True)
        _auth, char, sent = self.make(chars, verify)
        self.write(char, {"username": "root", "password": "rootpw"})
        assert sent == [{"authenticated": True}]
        verify.assert_awaited_once_with("root", "rootpw")


class TestHashLoginAuthenticator:
    @pytest.mark.asyncio
    async def test_checks_pis_hash_only(self):
        seen = []
        auth = BleAuthenticator(verify=AsyncMock(), verify_hash=lambda user, token: seen.append((user, token)) or True)
        assert (await auth.login_with_hash(DEVICE, "$6$x$y")).ok
        assert seen == [("pi", "$6$x$y")]

    @pytest.mark.asyncio
    async def test_unknown_connection_cannot_log_in(self):
        auth = BleAuthenticator(verify=AsyncMock(), verify_hash=lambda user, token: True)
        result = await auth.login_with_hash(None, "$6$x$y")
        assert not result.ok and result.code == 401
