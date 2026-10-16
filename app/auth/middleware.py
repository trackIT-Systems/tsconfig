"""Authentication middleware for FastAPI."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from fastapi.templating import Jinja2Templates
from starlette.requests import HTTPConnection
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from app import __version__
from app.auth.oidc_config import oidc_config
from app.auth.pam_auth import LOGIN_USERS, authenticate_operator
from app.auth.session import (
    client_source,
    has_valid_session,
    is_cross_origin,
    is_trusted_local,
    login_retry_after,
    parse_basic_auth,
    record_login_failure,
    record_login_success,
    sanitize_next,
    session_needs_refresh,
    set_session_cookie,
    verified_basic_credentials,
)
from app.config_loader import config_loader
from app.logging_config import get_logger

logger = get_logger(__name__)
templates = Jinja2Templates(directory="app/templates")

# WebDAV clients (Filebrowser's /data/dav/) cannot use the session cookie; they
# send the operator's password as HTTP Basic. Caddy's forward_auth passes that
# header to /auth/check together with the original URI, and only for requests
# under this prefix is Basic accepted.
DAV_PATH_PREFIX = "/data/dav"

# Methods that change state. A browser sends their requests from another site's
# page with the session cookie only if SameSite allows it; checking Origin as
# well does not depend on that.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="tsOS"'}


@dataclass
class Proceed:
    """The request may go on to the application. `decorate`, if set, adds
    cookies to the application's response (sliding session expiry, refreshed
    OIDC tokens)."""

    decorate: Callable[[Response], None] | None = None


def _decorating_send(send: Send, decorate: Callable[[Response], None]) -> Send:
    """Wrap `send` so the cookies `decorate` sets on a Response are added to
    the application's response."""

    async def send_with_cookies(message: Message) -> None:
        if message["type"] == "http.response.start":
            cookies = Response()
            decorate(cookies)
            # Only the cookies: the empty Response also carries a content-length
            extra = [(name, value) for name, value in cookies.raw_headers if name == b"set-cookie"]
            message = {**message, "headers": [*message.get("headers", []), *extra]}
        await send(message)

    return send_with_cookies


class AuthenticationMiddleware:
    """
    Pure ASGI middleware to enforce authentication, for HTTP requests and
    websocket handshakes alike (a refused handshake is closed before accept).

    In tracker mode: requires the operator's session cookie (PAM login of `pi`),
    except for requests made directly by a process on the device itself
    (loopback without proxy headers - mqttutil, tsconfig-ble) and the public
    paths below
    In server mode: Validates JWT token for all non-public endpoints

    This is not a BaseHTTPMiddleware: that one does not see websockets and
    runs the application in a separate task per request, with its own
    receive channel, so the application's response and its client-disconnect
    detection (the log stream) are not talking to the server directly. The
    decision is made before the application is called; whatever must be added
    to its response goes through a wrapped `send`.
    """

    def __init__(self, app: ASGIApp, public_paths: list[str] | None = None):
        self.app = app
        # Get base URL from environment (for generating redirect URLs)
        base_url = os.environ.get("TSCONFIG_BASE_URL", "").rstrip("/")
        if not base_url:
            base_url = ""

        # Default public paths
        # Note: When root_path is set, request.url.path INCLUDES the root_path
        # So if root_path="/tsconfig", a request to /tsconfig/static/... will have path="/tsconfig/static/..."
        default_public = [
            "/auth/login",
            "/auth/callback",
            "/auth/status",  # Needed by auth.js to check authentication status
            "/api/server-mode",  # Needed by auth.js to check if OIDC is enabled
            "/docs",
            "/redoc",
            "/openapi.json",
            "/static",
        ]

        # If we have a base_url (root_path), also add versions with the prefix
        if base_url:
            default_public.extend([f"{base_url}{path}" for path in default_public.copy()])

        # Use public paths as-is
        self.public_paths = public_paths or default_public

        # Tracker mode: the login form and its assets, nothing else. Unlike
        # server mode, the API docs are not public (they describe a surface
        # that is now behind the login).
        tracker_public = ["/auth/login", "/auth/logout", "/auth/status", "/api/server-mode", "/static"]
        if base_url:
            tracker_public.extend([f"{base_url}{path}" for path in tracker_public.copy()])
        self.tracker_public_paths = tracker_public

        self.base_url = base_url
        logger.debug(f"AuthenticationMiddleware initialized with base_url: {base_url}")
        logger.debug(f"Public paths: {self.public_paths}")

    def _is_public_path(self, path: str) -> bool:
        """Check if the request path is public (doesn't require authentication)."""
        for public_path in self.public_paths:
            if path.startswith(public_path):
                return True
        return False

    def _is_tracker_public_path(self, path: str) -> bool:
        return any(path.startswith(public_path) for public_path in self.tracker_public_paths)

    def _login_redirect_url(self, request: HTTPConnection) -> str:
        """Login URL carrying the page to return to. Behind Caddy's
        forward_auth the original URI is in X-Forwarded-Uri."""
        original = request.headers.get("x-forwarded-uri")
        if original is None:
            original = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        next_path = sanitize_next(original)
        return f"{self.base_url}/auth/login?next={quote(next_path, safe='')}"

    def _is_dav_auth_check(self, request: HTTPConnection) -> bool:
        """Is this Caddy's forward_auth subrequest for a WebDAV URL?"""
        if request.url.path != f"{self.base_url}/auth/check":
            return False
        original = request.headers.get("x-forwarded-uri", "")
        return original == DAV_PATH_PREFIX or original.startswith(DAV_PATH_PREFIX + "/")

    async def _check_basic_credentials(self, request: HTTPConnection) -> Response:
        """Verify `Authorization: Basic pi:<password>` for a WebDAV request."""
        credentials = parse_basic_auth(request.headers.get("authorization"))
        if credentials is None:
            return Response(
                content='{"detail":"Authentication required"}',
                status_code=401,
                media_type="application/json",
                headers=BASIC_CHALLENGE,
            )
        username, password = credentials

        if username in LOGIN_USERS and verified_basic_credentials.is_verified(username, password):
            return Response(status_code=200)

        source = client_source(request)
        wait = login_retry_after(source, username)
        if wait > 0:
            return Response(
                content='{"detail":"Too many failed attempts"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(int(wait) + 1)},
            )

        if not await authenticate_operator(username, password):
            record_login_failure(source, username)
            logger.warning(f"Failed WebDAV login as {username!r} from {source}")
            return Response(
                content='{"detail":"Wrong credentials"}',
                status_code=401,
                media_type="application/json",
                headers=BASIC_CHALLENGE,
            )

        record_login_success(source, username)
        verified_basic_credentials.remember(username, password)
        return Response(status_code=200)

    async def _decide_tracker(self, request: HTTPConnection) -> Response | Proceed:
        """Tracker mode: operator session cookie or trusted local request."""
        method = request.scope.get("method", "GET")  # websocket scopes have none
        if (method in UNSAFE_METHODS or request.scope["type"] == "websocket") and is_cross_origin(request):
            logger.warning(f"Refused cross-origin {method} {request.url.path} from {request.headers.get('origin')!r}")
            return Response(
                content='{"detail":"Cross-origin request refused"}',
                status_code=403,
                media_type="application/json",
            )
        if self._is_tracker_public_path(request.url.path) or is_trusted_local(request):
            return Proceed()

        issued = has_valid_session(request)
        if issued is None:
            if self._is_dav_auth_check(request):
                return await self._check_basic_credentials(request)
            if self._is_browser_request(request):
                return RedirectResponse(url=self._login_redirect_url(request), status_code=302)
            return Response(
                content='{"detail":"Authentication required"}',
                status_code=401,
                media_type="application/json",
            )

        if session_needs_refresh(issued):
            # Sliding expiry: re-issue the cookie while the operator is active.
            return Proceed(decorate=lambda response: set_session_cookie(response, request))
        return Proceed()

    def _is_browser_request(self, request: HTTPConnection) -> bool:
        """
        Detect if request is from a browser (vs API client).

        Browser requests should be redirected to login page.
        API requests should receive 401 response.
        """
        accept = request.headers.get("accept", "")
        # If request accepts HTML, it's likely a browser
        if "text/html" in accept:
            return True
        # Check for common API indicators
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return False
        if "application/json" in accept:
            return False
        # Check for fetch requests (they typically have Accept: */* or specific types)
        # If no Accept header or Accept: */*, treat as API to avoid CORS issues
        # Default to browser for other cases (like Accept: image/*, etc.)
        return bool(accept) and accept.strip() not in ("*/*", "*")

    async def _get_token_from_request(self, request: HTTPConnection) -> str | None:
        """Extract authentication token from request (header or cookie)."""
        # Try Authorization header first
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]  # Remove "Bearer " prefix

        # Try cookie
        auth_cookie = request.cookies.get("auth_token")
        if auth_cookie:
            return auth_cookie

        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        decision = await self._decide(HTTPConnection(scope))
        if isinstance(decision, Response):
            if scope["type"] == "websocket":
                # Closing before accept rejects the handshake
                await WebSocketClose(code=1008)(scope, receive, send)
            else:
                await decision(scope, receive, send)
            return

        if decision.decorate is not None and scope["type"] == "http":
            send = _decorating_send(send, decision.decorate)
        await self.app(scope, receive, send)

    async def _decide(self, request: HTTPConnection) -> Response | Proceed:
        """Decide whether the request may go on, or answer it right here."""
        # Tracker mode: operator login (PAM) instead of OIDC
        if not config_loader.is_server_mode():
            return await self._decide_tracker(request)

        # In server mode, check if OIDC is configured
        if not oidc_config.is_configured():
            # OIDC not configured, but server mode is enabled
            # This is a configuration error, but we'll allow the request
            # The endpoints themselves can decide if they need auth
            logger.warning("Server mode enabled but OIDC not configured")
            return Proceed()

        # Check if path is public (doesn't require authentication)
        path = request.url.path
        if self._is_public_path(path):
            return Proceed()

        # For all other paths, require authentication
        token = await self._get_token_from_request(request)

        if not token:
            # No token provided
            if self._is_browser_request(request):
                # Redirect to login page
                login_url = f"{self.base_url}/auth/login"
                # Include return_to parameter to redirect back after login
                return_to = getattr(request.state, "public_url", str(request.url))
                redirect_url = f"{login_url}?return_to={return_to}"
                return RedirectResponse(url=redirect_url, status_code=302)
            else:
                # API request, return 401
                return Response(
                    content='{"detail":"Authentication required"}',
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        # Validate token
        try:
            # Lazy import to avoid loading auth modules in tracker mode
            from app.auth.oidc_handler import get_oidc_handler

            oidc_handler = get_oidc_handler()
            if oidc_handler is None:
                logger.error("OIDC handler is not available")
                return Response(
                    content='{"detail":"Authentication service unavailable"}',
                    status_code=503,
                    media_type="application/json",
                )

            token_claims = await oidc_handler.validate_token(token)
            user_info = oidc_handler.extract_user_claims(token_claims)

            # Validate user groups
            required_groups = [f"tenant_{oidc_config.domain}", "ts_admin", "ts_staff"]
            is_authorized, error_msg = oidc_handler.validate_user_groups(user_info, required_groups)

            if not is_authorized:
                logger.warning(f"User {user_info.get('email')} authorization failed: {error_msg}")

                if self._is_browser_request(request):
                    # Render forbidden page for browser
                    return templates.TemplateResponse(
                        request,
                        "forbidden.html",
                        {
                            "base_url": self.base_url,
                            "user_email": user_info.get("email"),
                            "required_groups": required_groups,
                            "user_groups": user_info.get("groups", []),
                            "version": __version__,
                            "is_server_mode": config_loader.is_server_mode(),
                            "config_group": None,
                        },
                        status_code=403,
                    )
                else:
                    # API request, return 403
                    return Response(
                        content=f'{{"detail":"Forbidden: {error_msg}"}}',
                        status_code=403,
                        media_type="application/json",
                    )

            # Attach user info to request state for use in endpoints
            request.state.user = user_info

            # Token is valid and user is authorized, proceed with request
            return Proceed()

        except ValueError as e:
            # Token validation failed - try to refresh before giving up
            logger.warning(f"Token validation failed: {e}")

            # Check if we have a refresh token to attempt refresh
            refresh_token = request.cookies.get("refresh_token")

            if refresh_token:
                try:
                    # Attempt to refresh the token
                    from app.auth.oidc_handler import get_oidc_handler

                    oidc_handler = get_oidc_handler()
                    if oidc_handler:
                        token_response = await oidc_handler.refresh_access_token(refresh_token)

                        # Get new token
                        id_token = token_response.get("id_token")
                        access_token = token_response.get("access_token")
                        new_token = id_token or access_token

                        if new_token:
                            # Validate the new token
                            token_claims = await oidc_handler.validate_token(new_token)
                            user_info = oidc_handler.extract_user_claims(token_claims)

                            # Validate user groups
                            required_groups = [f"tenant_{oidc_config.domain}", "ts_admin", "ts_staff"]
                            is_authorized, error_msg = oidc_handler.validate_user_groups(user_info, required_groups)

                            if is_authorized:
                                # Success! Attach user info to request state
                                request.state.user = user_info

                                new_refresh_token = token_response.get("refresh_token", refresh_token)
                                expires_in = token_response.get("expires_in", 3600)

                                def set_refreshed_cookies(response: Response) -> None:
                                    response.set_cookie(
                                        key="auth_token",
                                        value=new_token,
                                        httponly=True,
                                        secure=True,
                                        samesite="lax",
                                        max_age=expires_in,
                                    )
                                    # The refresh token may have been rotated
                                    response.set_cookie(
                                        key="refresh_token",
                                        value=new_refresh_token,
                                        httponly=True,
                                        secure=True,
                                        samesite="lax",
                                        max_age=30 * 24 * 60 * 60,
                                    )

                                logger.debug(f"Successfully refreshed token for user: {user_info.get('email')}")
                                return Proceed(decorate=set_refreshed_cookies)
                            else:
                                logger.warning(f"User authorization failed after token refresh: {error_msg}")
                except Exception as refresh_error:
                    logger.warning(f"Token refresh failed in middleware: {refresh_error}", exc_info=True)
                    # Fall through to normal error handling

            # Refresh failed or no refresh token available - redirect/return error
            if self._is_browser_request(request):
                # Redirect to login page
                login_url = f"{self.base_url}/auth/login"
                # Use public URL from proxy middleware if available, otherwise use request.url
                return_to = getattr(request.state, "public_url", str(request.url))
                redirect_url = f"{login_url}?return_to={return_to}"
                return RedirectResponse(url=redirect_url, status_code=302)
            else:
                # API request, return 401
                return Response(
                    content='{"detail":"Invalid authentication token"}',
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": "Bearer"},
                )
