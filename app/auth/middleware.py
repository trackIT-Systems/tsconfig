"""Authentication middleware for FastAPI."""

import os
from typing import Callable

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app import __version__
from app.auth.oidc_config import oidc_config
from app.config_loader import config_loader
from app.logging_config import get_logger

logger = get_logger(__name__)
templates = Jinja2Templates(directory="app/templates")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce authentication in server mode.

    In tracker mode: All requests pass through without authentication
    In server mode: Validates JWT token for all non-public endpoints
    """

    def __init__(self, app, public_paths: list[str] | None = None):
        super().__init__(app)
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

        self.base_url = base_url
        logger.debug(f"AuthenticationMiddleware initialized with base_url: {base_url}")
        logger.debug(f"Public paths: {self.public_paths}")

    def _is_public_path(self, path: str) -> bool:
        """Check if the request path is public (doesn't require authentication)."""
        for public_path in self.public_paths:
            if path.startswith(public_path):
                return True
        return False

    def _is_browser_request(self, request: Request) -> bool:
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
        if not accept or accept.strip() in ["*/*", "*"]:
            return False
        # Default to browser for other cases (like Accept: image/*, etc.)
        return True

    async def _get_token_from_request(self, request: Request) -> str | None:
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

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process the request and enforce authentication if needed."""
        # In tracker mode, pass through all requests without authentication
        if not config_loader.is_server_mode():
            return await call_next(request)

        # In server mode, check if OIDC is configured
        if not oidc_config.is_configured():
            # OIDC not configured, but server mode is enabled
            # This is a configuration error, but we'll allow the request
            # The endpoints themselves can decide if they need auth
            logger.warning("Server mode enabled but OIDC not configured")
            return await call_next(request)

        # Check if path is public (doesn't require authentication)
        path = request.url.path
        if self._is_public_path(path):
            return await call_next(request)

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
                        "forbidden.html",
                        {
                            "request": request,
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
            return await call_next(request)

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
                                
                                # Continue with the request
                                response = await call_next(request)
                                
                                # Update cookies in the response
                                response.set_cookie(
                                    key="auth_token",
                                    value=new_token,
                                    httponly=True,
                                    secure=True,
                                    samesite="lax",
                                    max_age=token_response.get("expires_in", 3600),
                                )
                                
                                # Update refresh token if rotated
                                new_refresh_token = token_response.get("refresh_token", refresh_token)
                                response.set_cookie(
                                    key="refresh_token",
                                    value=new_refresh_token,
                                    httponly=True,
                                    secure=True,
                                    samesite="lax",
                                    max_age=30 * 24 * 60 * 60,
                                )
                                
                                logger.info(f"Successfully refreshed token for user: {user_info.get('email')}")
                                return response
                            else:
                                logger.warning(f"User authorization failed after token refresh: {error_msg}")
                except Exception as refresh_error:
                    logger.warning(f"Token refresh failed in middleware: {refresh_error}")
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
