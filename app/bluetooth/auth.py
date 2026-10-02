"""Operator authentication for the BLE gateway.

Link encryption (BlueZ pairing) is configured elsewhere; this module is the
application-level login on top of it. A BLE central proves it is the operator
by sending `pi`'s password (checked through PAM, exactly like the web login).
Until it does, every gated characteristic refuses to work for that connection.

State is per connection (BlueZ device object path) and in memory only:
disconnecting, an 8-hour timeout, or a gateway restart all require a fresh
login. This module deliberately imports nothing from dbus/GLib so it can be
unit-tested anywhere.
"""

import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from app.auth.pam_auth import OPERATOR_USER, authenticate_operator
from app.auth.session import (
    SESSION_TTL_SECONDS,
    login_retry_after,
    record_login_failure,
    record_login_success,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

# Error codes used in BLE responses (HTTP-style, like the rest of the protocol).
CODE_UNAUTHENTICATED = 401
CODE_TOO_MANY_ATTEMPTS = 429


# GATT permission flags -> their "link must be encrypted" counterparts (BlueZ
# gatt-api). Gated characteristics are exposed with these, so BlueZ forces LE
# pairing/encryption before anything - including the login itself - is accepted.
_ENCRYPTED_FLAGS = {
    "read": "encrypt-read",
    "write": "encrypt-write",
    "notify": "encrypt-notify",
    "indicate": "encrypt-indicate",
}


def secure_flags(flags):
    """Return `flags` with every read/write/notify/indicate made encryption-only."""
    return [_ENCRYPTED_FLAGS.get(flag, flag) for flag in flags]


class BleRequestError(Exception):
    """A request failure that carries an HTTP-style code (and extra fields) for
    the BLE error response, e.g. a failed login."""

    def __init__(self, message: str, code: int = 500, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


@dataclass
class LoginResult:
    ok: bool
    code: int = 200
    message: str = ""
    retry_after: float = 0.0


class BleAuthenticator:
    """Tracks which BLE connections have logged in as the operator."""

    def __init__(
        self,
        verify: Callable[[str, str], Awaitable[bool]] = authenticate_operator,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._verify = verify
        self._ttl = ttl_seconds
        self._clock = clock
        # device path -> time of login
        self._authenticated: Dict[str, float] = {}

    @staticmethod
    def _source(device: str) -> str:
        return f"ble:{device}"

    def is_authenticated(self, device: Optional[str]) -> bool:
        if not device:
            return False
        logged_in = self._authenticated.get(str(device))
        if logged_in is None:
            return False
        if self._clock() - logged_in > self._ttl:
            del self._authenticated[str(device)]
            return False
        return True

    async def login(self, device: Optional[str], password: str, username: str = OPERATOR_USER) -> LoginResult:
        """Attempt a login for `device` as the operator (`pi`) or staff (`root`)
        account. Failed attempts back off per device and device-wide, with
        stricter buckets for root (see app.auth.session); attempts made while
        backing off are refused without checking the password."""
        if not device:
            return LoginResult(False, CODE_UNAUTHENTICATED, "Unknown connection")
        if not isinstance(username, str):
            return LoginResult(False, CODE_UNAUTHENTICATED, "Wrong username or password")

        source = self._source(str(device))
        wait = login_retry_after(source, username)
        if wait > 0:
            return LoginResult(False, CODE_TOO_MANY_ATTEMPTS, "Too many failed attempts", retry_after=wait)

        if not isinstance(password, str) or not await self._verify(username, password):
            record_login_failure(source, username)
            logger.warning(f"Failed BLE login as {username!r} from {device}")
            return LoginResult(
                False,
                CODE_UNAUTHENTICATED,
                "Wrong username or password",
                retry_after=login_retry_after(source, username),
            )

        record_login_success(source, username)
        self._authenticated[str(device)] = self._clock()
        logger.info(f"BLE login succeeded as {username!r} for {device}")
        return LoginResult(True)

    def forget(self, device: str) -> None:
        """Drop a connection's login (called when BlueZ reports a disconnect)."""
        if self._authenticated.pop(str(device), None) is not None:
            logger.debug(f"Cleared BLE login for {device}")
