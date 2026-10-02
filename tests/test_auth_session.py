"""Tests for app.auth.session: signed session cookie, login backoff, the
trusted-local-request rule and the post-login redirect sanitizer."""

import base64
import time
from types import SimpleNamespace

import pytest

from app.auth import session
from app.auth.session import (
    BackoffLimiter,
    CredentialCache,
    create_session_token,
    is_trusted_local,
    parse_basic_auth,
    sanitize_next,
    session_needs_refresh,
    verify_session_token,
)


class TestSessionToken:
    def test_fresh_token_is_valid(self):
        token = create_session_token()
        assert verify_session_token(token) is not None

    def test_expires_after_ttl(self):
        now = 1_000_000
        token = create_session_token(now=now)
        assert verify_session_token(token, now=now + session.SESSION_TTL_SECONDS - 1) is not None
        assert verify_session_token(token, now=now + session.SESSION_TTL_SECONDS + 1) is None

    def test_rejects_tampered_signature_and_payload(self):
        token = create_session_token()
        issued, nonce, sig = token.split(".")
        assert verify_session_token(f"{issued}.{nonce}.{'0' * len(sig)}") is None
        assert verify_session_token(f"{int(issued) + 1}.{nonce}.{sig}") is None

    @pytest.mark.parametrize("bad", [None, "", "abc", "1.2", "a.b.c", "1.2.3.4", "x" * 500])
    def test_rejects_malformed(self, bad):
        assert verify_session_token(bad) is None

    def test_token_from_other_key_is_rejected(self, monkeypatch):
        token = create_session_token()
        monkeypatch.setattr(session, "_signing_key", b"another key entirely, 32 bytes long")
        assert verify_session_token(token) is None

    def test_refresh_only_when_older_than_threshold(self):
        now = time.time()
        assert not session_needs_refresh(int(now) - 10, now=now)
        assert session_needs_refresh(int(now) - session.SESSION_REFRESH_AFTER_SECONDS - 10, now=now)


def _conn(host, headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None, headers=headers or {})


class TestTrustedLocal:
    def test_loopback_without_proxy_headers_is_trusted(self):
        assert is_trusted_local(_conn("127.0.0.1"))
        assert is_trusted_local(_conn("::1"))

    @pytest.mark.parametrize(
        "header", ["x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip"]
    )
    def test_any_proxy_header_makes_it_untrusted(self, header):
        assert not is_trusted_local(_conn("127.0.0.1", {header: "127.0.0.1"}))

    def test_non_loopback_peer_is_untrusted(self):
        assert not is_trusted_local(_conn("192.168.1.5"))
        assert not is_trusted_local(_conn("testclient"))

    def test_missing_client_is_untrusted(self):
        assert not is_trusted_local(_conn(None))


class TestSanitizeNext:
    @pytest.mark.parametrize("ok", ["/", "/settings", "/data/foo?x=1", "/a/b%20c"])
    def test_accepts_relative_paths(self, ok):
        assert sanitize_next(ok) == ok

    @pytest.mark.parametrize(
        "bad",
        ["//evil.example", "http://evil.example/", "https://evil.example", "\\\\evil", "/\\evil", "javascript:alert(1)",
         "/%2Fevil.example", "/foo\r\nSet-Cookie: x=y", "evil", ""],
    )
    def test_rejects_everything_else(self, bad):
        assert sanitize_next(bad) == "/"

    def test_none_gives_default(self):
        assert sanitize_next(None) == "/"
        assert sanitize_next(None, default="/x") == "/x"


class TestBackoffLimiter:
    def test_free_attempts_then_exponential_up_to_cap(self):
        limiter = BackoffLimiter(free_attempts=2, cap_seconds=8)
        t = 100.0
        for _ in range(2):
            limiter.register_failure("a", now=t)
            assert limiter.retry_after("a", now=t) == 0
        delays = []
        for _ in range(6):
            limiter.register_failure("a", now=t)
            delays.append(limiter.retry_after("a", now=t))
        assert delays == [1, 2, 4, 8, 8, 8]

    def test_sources_are_independent_and_reset_clears(self):
        limiter = BackoffLimiter(free_attempts=0)
        limiter.register_failure("a", now=0)
        assert limiter.retry_after("a", now=0) > 0
        assert limiter.retry_after("b", now=0) == 0
        limiter.reset("a")
        assert limiter.retry_after("a", now=0) == 0

    def test_delay_expires(self):
        limiter = BackoffLimiter(free_attempts=0)
        limiter.register_failure("a", now=0)
        assert limiter.retry_after("a", now=0.5) == pytest.approx(0.5)
        assert limiter.retry_after("a", now=5) == 0

    def test_table_stays_bounded(self):
        limiter = BackoffLimiter(max_entries=10)
        for i in range(100):
            limiter.register_failure(f"src{i}", now=float(i))
        assert len(limiter._entries) <= 10


def _basic(raw: bytes) -> str:
    return "Basic " + base64.b64encode(raw).decode()


class TestParseBasicAuth:
    def test_splits_on_the_first_colon_only(self):
        assert parse_basic_auth(_basic(b"pi:pass:word")) == ("pi", "pass:word")

    def test_scheme_is_case_insensitive_and_unicode_works(self):
        assert parse_basic_auth("basic " + base64.b64encode("pi:pässwörd".encode()).decode()) == ("pi", "pässwörd")

    @pytest.mark.parametrize(
        "header",
        [None, "", "Basic", "Basic ", "Bearer abc", "Basic !!!", _basic(b"nocolon"), _basic(b"\xff\xfe:x"), "Basic " + "A" * 2000],
    )
    def test_rejects_anything_else(self, header):
        assert parse_basic_auth(header) is None


class TestCredentialCache:
    def test_remembers_for_the_ttl_only(self):
        cache = CredentialCache(ttl_seconds=60)
        cache.remember("pi", "natur", now=100)
        assert cache.is_verified("pi", "natur", now=159)
        assert not cache.is_verified("pi", "natur", now=161)

    def test_only_the_exact_credentials_match(self):
        cache = CredentialCache()
        cache.remember("pi", "natur")
        assert not cache.is_verified("pi", "natur2")
        assert not cache.is_verified("root", "natur")

    def test_does_not_store_the_password(self):
        cache = CredentialCache()
        cache.remember("pi", "supersecret")
        assert "supersecret" not in repr(cache.__dict__)

    def test_stays_bounded(self):
        cache = CredentialCache(max_entries=4)
        for i in range(20):
            cache.remember("pi", f"p{i}", now=float(i))
        assert len(cache._verified) <= 4
        assert cache.is_verified("pi", "p19", now=19.5)


class TestStaffThrottling:
    """root gets its own, stricter buckets, independent of the operator's."""

    @pytest.fixture(autouse=True)
    def clean(self):
        session.reset_login_state()
        yield
        session.reset_login_state()

    def test_staff_has_fewer_free_attempts_than_the_operator(self):
        for _ in range(2):
            session.record_login_failure("src", "root")
        assert session.login_retry_after("src", "root") == 0  # still free
        session.record_login_failure("src", "root")
        assert session.login_retry_after("src", "root") > 0
        for _ in range(3):
            session.record_login_failure("src", "pi")
        assert session.login_retry_after("src", "pi") == 0  # the operator still has 3 free

    def test_staff_backoff_reaches_a_longer_cap(self):
        limiter = session.staff_source_limiter
        for _ in range(12):
            limiter.register_failure("src", now=0.0)
        assert limiter.retry_after("src", now=0.0) == 120
        assert session.source_limiter.cap_seconds == 30

    def test_operator_failures_do_not_block_staff(self):
        for _ in range(40):
            session.record_login_failure("src", "pi")
        assert session.login_retry_after("src", "pi") > 0
        assert session.login_retry_after("src", "root") == 0

    def test_staff_failures_do_not_block_the_operator(self):
        for _ in range(40):
            session.record_login_failure("src", "root")
        assert session.login_retry_after("src", "root") > 0
        assert session.login_retry_after("other-source", "root") > 0  # root-wide bucket
        assert session.login_retry_after("src", "pi") == 0
        assert session.login_retry_after("other-source", "pi") == 0

    def test_unknown_usernames_count_against_the_operator_buckets_not_root(self):
        for _ in range(40):
            session.record_login_failure("src", "somebody")
        assert session.login_retry_after("src", "root") == 0

    def test_success_clears_only_that_accounts_buckets(self):
        for _ in range(10):
            session.record_login_failure("src", "root")
            session.record_login_failure("src", "pi")
        session.record_login_success("src", "root")
        assert session.login_retry_after("src", "root") == 0
        assert session.login_retry_after("src", "pi") > 0
