"""AuthenticationMiddleware and ProxyHeadersMiddleware are pure ASGI
middleware (not BaseHTTPMiddleware): streamed responses reach the client
while the application is still producing them, websocket handshakes are
covered by the same login check as HTTP requests, and cookies the middleware
adds (sliding session, refreshed OIDC tokens) go onto the application's own
response."""

import asyncio
import importlib
from unittest.mock import patch

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import middleware as auth_middleware
from app.auth import session
from app.auth.middleware import AuthenticationMiddleware
from app.auth.session import SESSION_COOKIE, create_session_token
from app.config_loader import config_loader
from app.proxy_headers import ProxyHeadersMiddleware

REMOTE = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "http", "X-Forwarded-Host": "dev"}


@pytest.fixture(autouse=True)
def tracker_mode(monkeypatch):
    monkeypatch.setattr(config_loader, "is_server_mode", lambda: False)
    session.reset_login_state()


def make_app():
    app = FastAPI()

    @app.get("/api/secret")
    async def secret():
        return {"ok": True}

    @app.websocket("/ws/unchecked")
    async def unchecked(websocket: WebSocket):
        # No login check of its own: the middleware has to provide it
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app.add_middleware(AuthenticationMiddleware)
    return app


class TestWebsocketHandshake:
    def test_without_a_session_the_handshake_is_refused_by_the_middleware(self):
        client = TestClient(make_app(), headers=REMOTE)
        with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("/ws/unchecked"):
            pass
        assert exc_info.value.code == 1008

    def test_with_a_session_it_goes_through(self):
        client = TestClient(make_app(), headers=REMOTE, cookies={SESSION_COOKIE: create_session_token()})
        with client.websocket_connect("/ws/unchecked") as ws:
            assert ws.receive_text() == "hello"

    def test_a_cross_origin_handshake_is_refused_even_with_a_session(self):
        headers = {**REMOTE, "Origin": "https://evil.example"}
        client = TestClient(make_app(), headers=headers, cookies={SESSION_COOKIE: create_session_token()})
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/unchecked"):
            pass

    def test_a_local_process_needs_no_login(self):
        client = TestClient(make_app(), client=("127.0.0.1", 40000))
        with client.websocket_connect("/ws/unchecked") as ws:
            assert ws.receive_text() == "hello"


class TestSlidingCookie:
    def test_only_the_cookie_is_added_to_the_applications_response(self):
        stale = create_session_token(now=1)  # old enough to be re-issued, but still valid
        with patch.object(auth_middleware, "session_needs_refresh", return_value=True):
            client = TestClient(make_app(), headers=REMOTE, cookies={SESSION_COOKIE: stale})
            with patch.object(session, "verify_session_token", return_value=1):
                response = client.get("/api/secret")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert SESSION_COOKIE in response.headers["set-cookie"]
        # the helper Response the cookie is set on has a content-length of its own
        assert len(response.headers.get_list("content-length")) == 1


@pytest.mark.asyncio
async def test_a_streamed_response_is_not_buffered():
    """The first chunk reaches the client before the application has finished."""
    release = asyncio.Event()
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await release.wait()
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    middleware = AuthenticationMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/stream",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 40000),  # a local process: no login needed
    }

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(middleware(scope, receive, send))
    for _ in range(100):
        if any(m.get("body") == b"first" for m in sent):
            break
        await asyncio.sleep(0.01)
    assert any(m.get("body") == b"first" for m in sent)
    assert not task.done()
    release.set()
    await task


class TestServerModeTokenRefresh:
    """A request with an expired token and a refresh token is answered by the
    application, and the new tokens ride along as cookies."""

    class FakeHandler:
        def __init__(self):
            self.valid = {"new-token"}

        async def validate_token(self, token):
            if token not in self.valid:
                raise ValueError("expired")
            return {"sub": "u"}

        def extract_user_claims(self, claims):
            return {"email": "u@example.org", "groups": ["ts_admin"]}

        def validate_user_groups(self, user_info, required):
            return True, None

        async def refresh_access_token(self, refresh_token):
            return {"id_token": "new-token", "refresh_token": "rotated", "expires_in": 600}

    def test_refreshed_tokens_are_set_as_cookies_on_the_applications_response(self, monkeypatch):
        monkeypatch.setattr(config_loader, "is_server_mode", lambda: True)
        monkeypatch.setattr(auth_middleware.oidc_config, "is_configured", lambda: True)
        monkeypatch.setattr(auth_middleware.oidc_config, "domain", "example.org", raising=False)
        # app.auth rebinds the name oidc_handler, so go through sys.modules
        oidc_handler_module = importlib.import_module("app.auth.oidc_handler")
        monkeypatch.setattr(oidc_handler_module, "get_oidc_handler", lambda: self.FakeHandler())

        app = FastAPI()

        @app.get("/api/secret")
        async def secret():
            return {"ok": True}

        app.add_middleware(AuthenticationMiddleware)
        client = TestClient(app, headers={"Authorization": "Bearer old"}, cookies={"refresh_token": "r"})
        response = client.get("/api/secret", headers={"Authorization": "Bearer old"})

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        cookies = response.headers.get_list("set-cookie")
        assert any(c.startswith("auth_token=new-token") for c in cookies)
        assert any(c.startswith("refresh_token=rotated") for c in cookies)


class TestProxyHeaders:
    def test_public_url_is_built_from_the_forwarded_headers_and_the_root_path(self):
        app = FastAPI(root_path="/tsconfig")
        seen = {}

        @app.get("/page")
        async def page(request_: None = None):
            return {}

        @app.middleware("http")
        async def capture(request, call_next):
            seen.update(request.state._state)
            return await call_next(request)

        app.add_middleware(ProxyHeadersMiddleware)
        client = TestClient(app, headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "dev.example.org"})
        client.get("/page?x=1")
        assert seen["public_url"] == "https://dev.example.org/tsconfig/page?x=1"
        assert seen["forwarded_proto"] == "https"

    def test_without_forwarded_headers_nothing_is_stored(self):
        app = FastAPI()
        seen = {}

        @app.get("/page")
        async def page():
            return {}

        @app.middleware("http")
        async def capture(request, call_next):
            seen.update(request.state._state)
            return await call_next(request)

        app.add_middleware(ProxyHeadersMiddleware)
        TestClient(app, headers={"Host": ""}).get("/page")
        assert "public_url" not in seen
