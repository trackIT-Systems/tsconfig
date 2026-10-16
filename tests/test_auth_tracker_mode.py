"""End-to-end tests of tracker-mode operator authentication: the middleware,
the login/logout/check routes, rate limiting and the websocket guard, run
against a minimal app (the real middleware + auth router, plus probe routes)."""

import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import middleware as auth_middleware
from app.auth import session
from app.auth.middleware import AuthenticationMiddleware
from app.auth.session import SECURE_SESSION_COOKIE, SESSION_COOKIE, create_session_token, is_authorized
from app.config_loader import config_loader
from app.routers import auth as auth_router

REMOTE = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "http", "X-Forwarded-Host": "dev"}
HTML = {"Accept": "text/html"}


@pytest.fixture(autouse=True)
def tracker_mode_and_clean_limiters(monkeypatch):
    monkeypatch.setattr(config_loader, "is_server_mode", lambda: False)
    session.reset_login_state()
    yield


def make_app():
    app = FastAPI()
    app.include_router(auth_router.router)

    @app.get("/api/secret")
    async def secret():
        return {"ok": True}

    @app.websocket("/ws/probe")
    async def ws_probe(websocket: WebSocket):
        if not is_authorized(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app.add_middleware(AuthenticationMiddleware)
    return app


@pytest.fixture
def remote():
    """A client that looks like it arrived through Caddy from the network."""
    return TestClient(make_app(), follow_redirects=False, headers=REMOTE)


@pytest.fixture
def local():
    """A process on the device talking to 127.0.0.1:8000 directly."""
    return TestClient(make_app(), client=("127.0.0.1", 40000), follow_redirects=False)


def session_cookie():
    return {SESSION_COOKIE: create_session_token()}


class TestGate:
    def test_api_without_session_is_401(self, remote):
        r = remote.get("/api/secret")
        assert r.status_code == 401

    def test_browser_without_session_is_redirected_to_login_with_next(self, remote):
        r = remote.get("/api/secret?x=1", headers=HTML)
        assert r.status_code == 302
        assert r.headers["location"] == "/auth/login?next=%2Fapi%2Fsecret%3Fx%3D1"

    def test_valid_session_passes(self, remote):
        r = remote.get("/api/secret", cookies=session_cookie())
        assert r.status_code == 200

    def test_garbage_cookie_is_rejected(self, remote):
        assert remote.get("/api/secret", cookies={SESSION_COOKIE: "1.2.3"}).status_code == 401

    def test_local_process_needs_no_login(self, local):
        assert local.get("/api/secret").status_code == 200

    def test_spoofed_forwarded_header_does_not_make_loopback_trusted_remote(self):
        # A "local" peer that carries proxy headers did not come directly from a local process.
        client = TestClient(make_app(), client=("127.0.0.1", 1), headers=REMOTE, follow_redirects=False)
        assert client.get("/api/secret").status_code == 401

    def test_docs_and_openapi_are_not_public(self, remote):
        assert remote.get("/openapi.json").status_code == 401

    def test_login_page_is_public(self, remote):
        r = remote.get("/auth/login")
        assert r.status_code == 200
        assert "password" in r.text.lower()

    def test_sliding_cookie_is_reissued_when_old(self, remote):
        old = create_session_token(now=__import__("time").time() - session.SESSION_REFRESH_AFTER_SECONDS - 60)
        r = remote.get("/api/secret", cookies={SESSION_COOKIE: old})
        assert r.status_code == 200
        assert SESSION_COOKIE in r.headers.get("set-cookie", "")

    def test_fresh_cookie_is_not_reissued(self, remote):
        r = remote.get("/api/secret", cookies=session_cookie())
        assert "set-cookie" not in r.headers


class TestAuthCheck:
    """Caddy's forward_auth protects /data with this endpoint."""

    def test_unauthenticated_browser_is_sent_to_login_for_original_uri(self, remote):
        r = remote.get("/auth/check", headers={**HTML, "X-Forwarded-Uri": "/data/files/x.csv"})
        assert r.status_code == 302
        assert r.headers["location"] == "/auth/login?next=%2Fdata%2Ffiles%2Fx.csv"

    def test_unauthenticated_api_gets_401(self, remote):
        assert remote.get("/auth/check").status_code == 401

    def test_authenticated_gets_200(self, remote):
        assert remote.get("/auth/check", cookies=session_cookie()).status_code == 200

    def test_hostile_forwarded_uri_is_not_used_as_redirect_target(self, remote):
        r = remote.get("/auth/check", headers={**HTML, "X-Forwarded-Uri": "//evil.example/"})
        assert r.headers["location"] == "/auth/login?next=%2F"


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


DAV = {"X-Forwarded-Uri": "/data/dav/data/"}


class TestWebDavBasicAuth:
    """WebDAV clients cannot use the session cookie; Caddy's forward_auth passes
    their Basic credentials to /auth/check, which verifies them with PAM."""

    def check(self, remote, headers):
        return remote.get("/auth/check", headers={**DAV, **headers})

    def test_correct_password_is_accepted(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=True)) as pam:
            r = self.check(remote, basic("pi", "natur"))
        assert r.status_code == 200
        pam.assert_awaited_once_with("pi", "natur")

    def test_wrong_password_is_401_with_a_basic_challenge(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=False)):
            r = self.check(remote, basic("pi", "nope"))
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Basic")

    def test_missing_credentials_get_a_challenge_so_clients_prompt(self, remote):
        r = self.check(remote, {})
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Basic")

    @pytest.mark.parametrize(
        "header", ["Basic", "Basic !!!notbase64", "Bearer abc", "Basic " + base64.b64encode(b"nocolon").decode()]
    )
    def test_malformed_authorization_is_rejected_without_asking_pam(self, remote, header):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=True)) as pam:
            r = self.check(remote, {"Authorization": header})
        assert r.status_code == 401
        pam.assert_not_called()

    def test_other_usernames_never_succeed(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=False)) as pam:
            assert self.check(remote, basic("root", "natur")).status_code == 401
        pam.assert_awaited_once_with("root", "natur")  # the PAM wrapper itself refuses non-operator users

    def test_repeated_requests_with_the_same_password_hit_pam_once(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=True)) as pam:
            for _ in range(5):
                assert self.check(remote, basic("pi", "natur")).status_code == 200
        assert pam.await_count == 1

    def test_a_different_password_is_not_served_from_the_cache(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(side_effect=[True, False])):
            assert self.check(remote, basic("pi", "natur")).status_code == 200
            assert self.check(remote, basic("pi", "other")).status_code == 401

    def test_backs_off_after_repeated_failures_without_asking_pam(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=False)) as pam:
            codes = [self.check(remote, basic("pi", "x")).status_code for _ in range(6)]
            calls = pam.await_count
            blocked = self.check(remote, basic("pi", "x"))
        assert 429 in codes
        assert blocked.status_code == 429 and "Retry-After" in blocked.headers
        assert pam.await_count == calls

    def test_basic_is_not_accepted_for_other_urls(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=True)) as pam:
            assert (
                remote.get(
                    "/auth/check", headers={**basic("pi", "natur"), "X-Forwarded-Uri": "/data/files/x"}
                ).status_code
                == 401
            )
            assert remote.get("/auth/check", headers=basic("pi", "natur")).status_code == 401
            assert remote.get("/api/secret", headers={**basic("pi", "natur"), **DAV}).status_code == 401
        pam.assert_not_called()

    def test_lookalike_prefix_is_not_a_dav_url(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=True)) as pam:
            r = remote.get("/auth/check", headers={**basic("pi", "natur"), "X-Forwarded-Uri": "/data/davinci"})
        assert r.status_code == 401
        pam.assert_not_called()

    def test_session_cookie_still_works_for_dav_urls(self, remote):
        assert remote.get("/auth/check", headers=DAV, cookies=session_cookie()).status_code == 200


class TestStaffLogin:
    """root + the root password logs in like the operator, with no extra rights
    and with its own, stricter throttling."""

    def post(self, remote, user, password="x"):
        return remote.post("/auth/login", data={"username": user, "password": password})

    def test_root_can_log_in_on_the_web_form(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)) as check:
            r = self.post(remote, "root", "rootpw")
        assert r.status_code == 303 and SESSION_COOKIE in r.headers["set-cookie"]
        check.assert_awaited_once_with("root", "rootpw")

    def test_a_root_session_is_the_same_session_with_the_same_rights(self, remote):
        # No identity in the cookie: both accounts get an identically-shaped token.
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            root_cookie = self.post(remote, "root").cookies.get(SESSION_COOKIE)
            pi_cookie = self.post(remote, "pi").cookies.get(SESSION_COOKIE)
        assert len(root_cookie.split(".")) == len(pi_cookie.split(".")) == 3
        assert remote.get("/api/secret", cookies={SESSION_COOKIE: root_cookie}).status_code == 200

    def test_wrong_root_password_is_refused(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            r = self.post(remote, "root")
        assert r.status_code == 401 and "Wrong username or password." in r.text
        assert "set-cookie" not in r.headers

    def test_root_backs_off_sooner_than_the_operator(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            root_codes = [self.post(remote, "root").status_code for _ in range(4)]
        assert root_codes == [401, 401, 401, 429]  # 2 free, third failure starts the delay
        session.reset_login_state()
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            pi_codes = [self.post(remote, "pi").status_code for _ in range(4)]
        assert pi_codes == [401, 401, 401, 401]  # the operator has 3 free attempts

    def test_a_root_flood_does_not_lock_the_operator_out(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            for _ in range(10):
                self.post(remote, "root")
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            assert self.post(remote, "pi", "natur").status_code == 303

    def test_root_attempts_while_backing_off_do_not_reach_the_password_check(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)) as check:
            for _ in range(3):
                self.post(remote, "root")
            calls = check.await_count
            blocked = self.post(remote, "root")
        assert blocked.status_code == 429 and "Retry-After" in blocked.headers
        assert check.await_count == calls

    def test_root_over_webdav_basic(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=True)) as check:
            r = remote.get("/auth/check", headers={**DAV, **basic("root", "rootpw")})
            again = remote.get("/auth/check", headers={**DAV, **basic("root", "rootpw")})
        assert r.status_code == again.status_code == 200
        check.assert_awaited_once_with("root", "rootpw")  # second request served from the credential cache

    def test_wrong_root_password_over_webdav_backs_off_early(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(return_value=False)):
            codes = [remote.get("/auth/check", headers={**DAV, **basic("root", f"x{i}")}).status_code for i in range(4)]
        assert codes == [401, 401, 401, 429]

    def test_cached_credentials_are_per_account(self, remote):
        with patch.object(auth_middleware, "authenticate_operator", AsyncMock(side_effect=[True, False])):
            assert remote.get("/auth/check", headers={**DAV, **basic("pi", "same")}).status_code == 200
            assert remote.get("/auth/check", headers={**DAV, **basic("root", "same")}).status_code == 401


class TestLoginPage:
    def page(self, remote, **kwargs):
        with patch.object(auth_router.socket, "gethostname", return_value="tsos-test-host"):
            return remote.get("/auth/login", **kwargs)

    def test_shows_the_hostname_in_title_and_navbar(self, remote):
        html = self.page(remote).text
        assert "<title>tsconfig | tsos-test-host</title>" in html
        assert 'class="navbar-brand"' in html and ">tsos-test-host</a>" in html

    def test_is_a_username_and_password_form_password_managers_can_save(self, remote):
        html = self.page(remote).text
        assert 'method="post"' in html and 'action="/auth/login"' in html
        assert 'name="username"' in html and 'autocomplete="username"' in html
        assert 'name="password"' in html and 'type="password"' in html and 'autocomplete="current-password"' in html
        assert 'value="pi"' in html  # the operator account is prefilled

    def test_navbar_entries_are_present_but_inactive(self, remote):
        html = self.page(remote).text
        for label in ("Status", "System Settings", "Radio Tracking", "Soundscapepipe", "Data"):
            assert label in html
        assert html.count('class="nav-link disabled"') == 5
        assert 'href="#' not in html  # nothing in the navbar is a live link
        assert 'id="globalExpertModeToggle" disabled' in html

    def test_uses_the_shared_footer(self, remote):
        html = self.page(remote).text
        assert "© trackIT Systems" in html and 'class="footer mt-3 bg-light"' in html

    def test_failed_login_keeps_the_username_and_does_not_say_which_part_was_wrong(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            r = remote.post("/auth/login", data={"username": "someone", "password": "x"})
        assert r.status_code == 401
        assert "Wrong username or password." in r.text
        assert 'value="someone"' in r.text

    def test_submitted_username_is_escaped(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            r = remote.post("/auth/login", data={"username": '"><script>alert(1)</script>', "password": "x"})
        assert "<script>alert(1)</script>" not in r.text


class TestLogin:
    def test_success_sets_session_cookie_and_redirects_to_next(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)) as pam:
            r = remote.post("/auth/login", data={"password": "natur", "next": "/settings"})
        assert r.status_code == 303
        assert r.headers["location"] == "/settings"
        assert SESSION_COOKIE in r.headers["set-cookie"]
        assert "httponly" in r.headers["set-cookie"].lower()
        pam.assert_awaited_once_with("pi", "natur")

    def test_session_from_login_opens_the_gate(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            r = remote.post("/auth/login", data={"password": "natur"})
        token = r.cookies.get(SESSION_COOKIE)
        assert remote.get("/api/secret", cookies={SESSION_COOKIE: token}).status_code == 200

    def test_open_redirect_in_next_is_neutralised(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            r = remote.post("/auth/login", data={"password": "x", "next": "//evil.example"})
        assert r.headers["location"] == "/"

    def test_wrong_password_is_401_without_cookie(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            r = remote.post("/auth/login", data={"password": "nope"})
        assert r.status_code == 401
        assert "set-cookie" not in r.headers

    def test_backoff_after_repeated_failures_and_pam_not_consulted_while_blocked(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)) as pam:
            codes = [remote.post("/auth/login", data={"password": "x"}).status_code for _ in range(6)]
            calls_before_block = pam.await_count
            blocked = remote.post("/auth/login", data={"password": "x"})
        assert 429 in codes
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers
        assert pam.await_count == calls_before_block  # blocked attempts never reach PAM

    def test_other_sources_are_not_blocked_by_one_noisy_source(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            for _ in range(6):
                remote.post("/auth/login", data={"password": "x"})
        other = TestClient(make_app(), follow_redirects=False, headers={**REMOTE, "X-Forwarded-For": "198.51.100.9"})
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            assert other.post("/auth/login", data={"password": "ok"}).status_code == 303

    def test_success_clears_the_source_backoff(self, remote):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=False)):
            for _ in range(3):
                remote.post("/auth/login", data={"password": "x"})
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            assert remote.post("/auth/login", data={"password": "ok"}).status_code == 303
        assert session.login_retry_after("203.0.113.7") == 0

    def test_logout_clears_cookie_and_goes_to_login(self, remote):
        r = remote.get("/auth/logout", cookies=session_cookie())
        assert r.status_code == 303
        assert r.headers["location"] == "/auth/login"
        assert SESSION_COOKIE in r.headers["set-cookie"]


class TestHttpsSession:
    """Over HTTPS (Caddy's X-Forwarded-Proto) the session uses a Secure __Host- cookie."""

    @pytest.fixture
    def https(self):
        return TestClient(make_app(), follow_redirects=False, headers={**REMOTE, "X-Forwarded-Proto": "https"})

    def login(self, client):
        with patch.object(auth_router, "authenticate_operator", AsyncMock(return_value=True)):
            return client.post("/auth/login", data={"password": "natur"})

    def test_https_login_sets_a_secure_host_cookie(self, https):
        cookie = self.login(https).headers["set-cookie"]
        assert cookie.startswith(f"{SECURE_SESSION_COOKIE}=")
        assert "secure" in cookie.lower() and "httponly" in cookie.lower()
        assert "domain" not in cookie.lower()

    def test_http_login_keeps_the_plain_cookie(self, remote):
        cookie = self.login(remote).headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=") and "secure" not in cookie.lower()

    def test_secure_cookie_opens_the_gate(self, https):
        token = create_session_token()
        assert https.get("/api/secret", cookies={SECURE_SESSION_COOKIE: token}).status_code == 200

    def test_plain_cookie_still_works_over_https(self, https):
        assert https.get("/api/secret", cookies=session_cookie()).status_code == 200

    def test_sliding_refresh_over_https_issues_the_secure_cookie(self, https):
        old = create_session_token(now=__import__("time").time() - session.SESSION_REFRESH_AFTER_SECONDS - 60)
        r = https.get("/api/secret", cookies={SESSION_COOKIE: old})
        assert r.headers["set-cookie"].startswith(f"{SECURE_SESSION_COOKIE}=")

    def test_logout_clears_both_cookies(self, https):
        r = https.get("/auth/logout", cookies=session_cookie())
        cookies = r.headers.get_list("set-cookie")
        assert any(c.startswith(f"{SESSION_COOKIE}=") for c in cookies)
        assert any(c.startswith(f"{SECURE_SESSION_COOKIE}=") and "secure" in c.lower() for c in cookies)


class TestWebsocket:
    def test_rejected_without_session(self):
        client = TestClient(make_app(), headers=REMOTE)
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/probe"):
            pass

    def test_allowed_with_session(self):
        client = TestClient(make_app(), headers=REMOTE, cookies=session_cookie())
        with client.websocket_connect("/ws/probe") as ws:
            assert ws.receive_text() == "hello"

    def test_allowed_for_local_process(self):
        client = TestClient(make_app(), client=("127.0.0.1", 1))
        with client.websocket_connect("/ws/probe") as ws:
            assert ws.receive_text() == "hello"
