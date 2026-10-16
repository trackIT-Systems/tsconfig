"""Pure ASGI middleware that reconstructs the public URL behind a reverse proxy."""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.logging_config import get_logger

logger = get_logger(__name__)


class ProxyHeadersMiddleware:
    """Handles the case where Caddy strips the base path prefix (e.g.
    /tsconfig) before forwarding to the app: it stores the public-facing URL
    components in the scope's state, for the redirect URLs of the
    authentication middleware.

    Sets `forwarded_proto`, `forwarded_host` and `public_url` on request.state
    if the proxy sent X-Forwarded-Proto and a host.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            self._store_public_url(scope)
        await self.app(scope, receive, send)

    @staticmethod
    def _store_public_url(scope: Scope) -> None:
        headers = Headers(scope=scope)
        forwarded_proto = headers.get("x-forwarded-proto")
        forwarded_host = headers.get("x-forwarded-host") or headers.get("host")

        logger.debug(f"ProxyHeadersMiddleware: proto={forwarded_proto}, host={forwarded_host}")
        if not (forwarded_proto and forwarded_host):
            logger.debug("ProxyHeadersMiddleware: no forwarded headers found, skipping URL reconstruction")
            return

        # scheme://host/root_path/path?query
        root_path = scope.get("root_path", "")
        path = scope.get("path", "/")
        query_string = scope.get("query_string", b"").decode()
        logger.debug(f"ProxyHeadersMiddleware: root_path='{root_path}', path='{path}', query='{query_string}'")

        public_url = f"{forwarded_proto}://{forwarded_host}{root_path}{path}"
        if query_string:
            public_url += f"?{query_string}"
        logger.debug(f"ProxyHeadersMiddleware: constructed public_url='{public_url}'")

        state = scope.setdefault("state", {})
        state["forwarded_proto"] = forwarded_proto
        state["forwarded_host"] = forwarded_host
        state["public_url"] = public_url
