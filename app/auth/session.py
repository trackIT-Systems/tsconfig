"""Operator sessions for tracker mode: signed cookie, login backoff, and the
"trusted local request" rule.

Everything here is in-memory by design: the signing key is generated when the
process starts and never written to disk, so a tsconfig restart or reboot logs
everybody out (which also invalidates sessions after a config bundle changed
the `pi` password, since that takes effect at reboot).
"""

import base64
import binascii
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import unquote, urlsplit

from starlette.requests import HTTPConnection
from starlette.responses import Response

from app.identity import OPERATOR_USER, STAFF_USER

SESSION_COOKIE = "tsconfig_session"
# Issued instead of SESSION_COOKIE when the login arrived over HTTPS. `Secure`
# keeps the browser from ever sending it over plain HTTP (which stays
# available), and the __Host- prefix keeps a plain-HTTP response from setting
# or overwriting it.
SECURE_SESSION_COOKIE = "__Host-tsconfig_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
# The cookie is re-issued (sliding expiry) when it is older than this.
SESSION_REFRESH_AFTER_SECONDS = 5 * 60

_signing_key = secrets.token_bytes(32)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
# Headers Caddy (the only reverse proxy in front of tsconfig) always adds to
# requests it forwards. A request carrying any of them did not come directly
# from a local process.
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip")


def _sign(payload: str) -> str:
    return hmac.new(_signing_key, payload.encode(), sha256).hexdigest()


def create_session_token(now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = f"{issued}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload)}"


def verify_session_token(token: str | None, now: float | None = None) -> int | None:
    """Return the token's issue time if it is valid and unexpired, else None."""
    if not token or len(token) > 256:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    issued_s, nonce, signature = parts
    if not issued_s.isdigit():
        return None
    if not hmac.compare_digest(signature, _sign(f"{issued_s}.{nonce}")):
        return None
    issued = int(issued_s)
    current = now if now is not None else time.time()
    if issued > current + 60 or current - issued > SESSION_TTL_SECONDS:
        return None
    return issued


def session_needs_refresh(issued: int, now: float | None = None) -> bool:
    current = now if now is not None else time.time()
    return current - issued > SESSION_REFRESH_AFTER_SECONDS


def is_trusted_local(conn: HTTPConnection) -> bool:
    """True for requests made directly by a process on this device.

    Rule: the TCP peer is loopback *and* the request carries none of the
    forwarding headers Caddy adds. Remote traffic always reaches tsconfig
    through Caddy, which sets (and overwrites any client-supplied)
    X-Forwarded-For, so a remote client cannot produce a request that satisfies
    this. Note uvicorn's own proxy-header handling may rewrite `conn.client` to
    the real client address for proxied requests; that only makes them *less*
    likely to look local, and the header check applies regardless.
    """
    client = conn.client
    if client is None or client.host not in LOOPBACK_HOSTS:
        return False
    return not any(h in conn.headers for h in _PROXY_HEADERS)


def is_cross_origin(conn: HTTPConnection) -> bool:
    """True when a browser sent this request from a page of another site.

    Browsers send Origin with every POST/PUT/PATCH/DELETE (fetch and forms) and
    every WebSocket handshake. It must name the host the request was sent to,
    which Caddy passes through unchanged as Host. The scheme is not compared,
    so the same device over HTTP and HTTPS counts as one. Requests without
    Origin do not come from a web page (curl, mqttutil, the BLE gateway).
    """
    origin = conn.headers.get("origin")
    if origin is None:
        return False
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return True
    return not netloc or netloc.lower() != conn.headers.get("host", "").lower()


def has_valid_session(conn: HTTPConnection) -> int | None:
    for name in (SECURE_SESSION_COOKIE, SESSION_COOKIE):
        issued = verify_session_token(conn.cookies.get(name))
        if issued is not None:
            return issued
    return None


def is_https(conn: HTTPConnection) -> bool:
    """Whether the client reached Caddy over HTTPS (Caddy sets X-Forwarded-Proto)."""
    return conn.headers.get("x-forwarded-proto", "").lower() == "https"


def set_session_cookie(response: Response, conn: HTTPConnection) -> None:
    """Issue a fresh session cookie, the Secure one if the request came over HTTPS."""
    secure = is_https(conn)
    response.set_cookie(
        key=SECURE_SESSION_COOKIE if secure else SESSION_COOKIE,
        value=create_session_token(),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )


def delete_session_cookies(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/", samesite="lax")
    # Browsers only honour this over HTTPS; over HTTP the Secure cookie is never sent anyway.
    response.delete_cookie(key=SECURE_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def is_authorized(conn: HTTPConnection) -> bool:
    """Whether a tracker-mode websocket may proceed (it carries no CSRF token,
    so the handshake must also come from tsconfig's own pages)."""
    if is_cross_origin(conn):
        return False
    return is_trusted_local(conn) or has_valid_session(conn) is not None


def client_source(conn: HTTPConnection) -> str:
    """Rate-limiting key for a request: the client address Caddy reports."""
    forwarded = conn.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or "unknown"
    return conn.client.host if conn.client else "unknown"


_SAFE_NEXT = re.compile(r"^/(?!/)[^\\\r\n]*$")


def sanitize_next(value: str | None, default: str = "/") -> str:
    """Only allow same-site relative paths as post-login redirect targets."""
    if not value:
        return default
    decoded = unquote(value)
    if not _SAFE_NEXT.match(value) or not _SAFE_NEXT.match(decoded):
        return default
    return value


# --- HTTP Basic credentials (WebDAV) -------------------------------------

MAX_BASIC_LENGTH = 1024


def parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    """Return (username, password) from an `Authorization: Basic ...` header,
    or None if it is absent or malformed."""
    if not header or len(header) > MAX_BASIC_LENGTH:
        return None
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded.strip():
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


class CredentialCache:
    """Remembers recently verified Basic credentials for a short time.

    A WebDAV client sends its credentials with every request, and a directory
    listing or sync is hundreds of requests; without this each one would run a
    PAM password check. Only the keyed digest of user+password is kept, never
    the password, only in memory, and a changed password stops working after
    at most `ttl_seconds`.
    """

    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 64):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._verified: dict[str, float] = {}

    @staticmethod
    def _digest(username: str, password: str) -> str:
        return hmac.new(_signing_key, f"{username}\0{password}".encode(), sha256).hexdigest()

    def is_verified(self, username: str, password: str, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        expires = self._verified.get(self._digest(username, password))
        return expires is not None and expires > current

    def remember(self, username: str, password: str, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        if len(self._verified) >= self._max:
            self._verified = {k: v for k, v in self._verified.items() if v > current}
            while len(self._verified) >= self._max:
                self._verified.pop(min(self._verified, key=self._verified.get))
        self._verified[self._digest(username, password)] = current + self._ttl

    def clear(self) -> None:
        self._verified.clear()


verified_basic_credentials = CredentialCache()


@dataclass
class _Entry:
    failures: int = 0
    next_allowed: float = 0.0
    last_failure: float = 0.0


@dataclass
class BackoffLimiter:
    """Exponential backoff for failed logins, no hard lockout.

    The first `free_attempts` failures cost nothing; after that each further
    failure doubles the wait (1 s, 2 s, 4 s, ...) up to `cap_seconds`. Attempts
    made while blocked are rejected without being counted, so an attacker cannot
    extend the delay for the legitimate operator beyond `cap_seconds`.
    """

    free_attempts: int = 3
    cap_seconds: float = 30.0
    max_entries: int = 1024
    _entries: dict[str, _Entry] = field(default_factory=dict)

    def retry_after(self, key: str, now: float | None = None) -> float:
        current = now if now is not None else time.monotonic()
        entry = self._entries.get(key)
        if entry is None:
            return 0.0
        return max(0.0, entry.next_allowed - current)

    def register_failure(self, key: str, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        self._prune(current)
        entry = self._entries.setdefault(key, _Entry())
        entry.failures += 1
        entry.last_failure = current
        over = entry.failures - self.free_attempts
        if over > 0:
            entry.next_allowed = current + min(self.cap_seconds, 2 ** (over - 1))

    def reset(self, key: str) -> None:
        self._entries.pop(key, None)

    def _prune(self, current: float) -> None:
        if len(self._entries) < self.max_entries:
            return
        stale = [k for k, e in self._entries.items() if current - e.last_failure > 3600]
        for k in stale:
            del self._entries[k]
        # Still full of recent entries: drop the oldest rather than grow without bound.
        while len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].last_failure)
            del self._entries[oldest]


# Per-source limiter, plus a looser device-wide one that caps how fast anybody
# collectively can guess (spoofable/rotating sources cannot dodge it).
source_limiter = BackoffLimiter(free_attempts=3, cap_seconds=30.0)
global_limiter = BackoffLimiter(free_attempts=10, cap_seconds=5.0)
GLOBAL_KEY = "global"

# The staff (root) login gets its own, stricter buckets, independent of the
# operator's: root's password - if the image has one - is the more valuable
# secret and is also tried over SSH, and a flood of operator guesses must not
# lock staff out (or the other way round).
staff_source_limiter = BackoffLimiter(free_attempts=2, cap_seconds=120.0)
staff_global_limiter = BackoffLimiter(free_attempts=5, cap_seconds=60.0)


def _limiters(username: str) -> tuple[BackoffLimiter, BackoffLimiter]:
    if username == STAFF_USER:
        return staff_source_limiter, staff_global_limiter
    return source_limiter, global_limiter


def login_retry_after(source: str, username: str = OPERATOR_USER) -> float:
    per_source, device_wide = _limiters(username)
    return max(per_source.retry_after(source), device_wide.retry_after(GLOBAL_KEY))


def record_login_failure(source: str, username: str = OPERATOR_USER) -> None:
    per_source, device_wide = _limiters(username)
    per_source.register_failure(source)
    device_wide.register_failure(GLOBAL_KEY)


def record_login_success(source: str, username: str = OPERATOR_USER) -> None:
    per_source, device_wide = _limiters(username)
    per_source.reset(source)
    device_wide.reset(GLOBAL_KEY)


def reset_login_state() -> None:
    """Forget all failures and remembered credentials (used by tests)."""
    for limiter in (source_limiter, global_limiter, staff_source_limiter, staff_global_limiter):
        limiter._entries.clear()
    verified_basic_credentials.clear()
