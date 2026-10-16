"""Authentication endpoints for OIDC flow."""

import os
import socket
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.auth.dependencies import get_current_user, get_optional_user
from app.auth.oidc_config import oidc_config
from app.auth.pam_auth import OPERATOR_USER, authenticate_operator
from app.auth.session import (
    client_source,
    delete_session_cookies,
    has_valid_session,
    is_trusted_local,
    login_retry_after,
    record_login_failure,
    record_login_success,
    sanitize_next,
    set_session_cookie,
)
from app.config_loader import config_loader
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])
templates = Jinja2Templates(directory="app/templates")

# Store PKCE verifiers and return_to URLs temporarily (in production, use Redis or similar)
# Key: state, Value: dict with code_verifier and return_to (both can be None)
_pkce_store: dict[str, dict[str, str | None]] = {}


def _base_url() -> str:
    return os.environ.get("TSCONFIG_BASE_URL", "").rstrip("/")


def _render_login(
    request: Request,
    next_path: str,
    error: str | None = None,
    status_code: int = 200,
    retry_after: float = 0,
    username: str = OPERATOR_USER,
):
    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "base_url": _base_url(),
            "version": __version__,
            "hostname": socket.gethostname(),
            "next_path": next_path,
            "username": username,
            "error": error,
        },
        status_code=status_code,
    )
    if retry_after:
        response.headers["Retry-After"] = str(int(retry_after) + 1)
    return response


@router.get(
    "/login",
    summary="Login",
    description="Tracker mode: shows the operator login form. Server mode: redirects to the OIDC login page.",
    response_model=None,
)
async def login(
    request: Request,
    return_to: str | None = Query(None, description="URL to return to after successful login"),
    next: str | None = Query(None, description="Path to return to after a successful operator login"),
):
    """Operator login form (tracker mode) or OIDC authorization code flow (server mode)."""
    if not config_loader.is_server_mode():
        if has_valid_session(request) is not None or is_trusted_local(request):
            return RedirectResponse(url=f"{_base_url()}{sanitize_next(next)}", status_code=303)
        return _render_login(request, sanitize_next(next))

    # Check if OIDC is configured
    if not oidc_config.is_configured():
        raise HTTPException(
            status_code=503,
            detail="OIDC authentication is not configured",
        )

    try:
        # Lazy import to avoid loading auth modules in tracker mode
        from app.auth.oidc_handler import get_oidc_handler

        oidc_handler = get_oidc_handler()
        if oidc_handler is None:
            raise HTTPException(
                status_code=503,
                detail="OIDC handler is not available",
            )

        # Initiate login flow
        authorization_url, state, code_verifier = await oidc_handler.initiate_login(return_to)

        # Store code verifier and return_to for later use in callback
        _pkce_store[state] = {"code_verifier": code_verifier, "return_to": return_to}

        logger.info("Redirecting to OIDC provider for authentication")
        return RedirectResponse(url=authorization_url, status_code=302)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating OIDC login: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate login: {e!s}",
        )


@router.post(
    "/login",
    summary="Operator login (tracker mode)",
    description="Checks the operator password against PAM (the `pi` account) and sets a session cookie.",
    response_model=None,
)
async def operator_login(
    request: Request,
    password: str = Form(...),
    username: str = Form(OPERATOR_USER),
    next: str | None = Form(None),
):
    if config_loader.is_server_mode():
        raise HTTPException(status_code=404, detail="Operator login is only available in tracker mode")

    next_path = sanitize_next(next)
    source = client_source(request)

    wait = login_retry_after(source, username)
    if wait > 0:
        return _render_login(
            request,
            next_path,
            f"Too many failed attempts. Try again in {int(wait) + 1} seconds.",
            429,
            wait,
            username=username,
        )

    if not await authenticate_operator(username, password):
        record_login_failure(source, username)
        logger.warning(f"Failed login as {username!r} from {source}")
        wait = login_retry_after(source, username)
        return _render_login(request, next_path, "Wrong username or password.", 401, wait, username=username)

    record_login_success(source, username)
    logger.info(f"Logged in as {username!r} from {source}")
    response = RedirectResponse(url=f"{_base_url()}{next_path}", status_code=303)
    set_session_cookie(response, request)
    return response


@router.get(
    "/check",
    summary="Authentication check",
    description="Returns 200 if the request is authenticated. Used by the reverse proxy (forward_auth) to protect /data.",
)
async def auth_check():
    # The authentication middleware has already decided: unauthenticated
    # requests never reach this handler.
    return Response(status_code=200)


@router.get(
    "/callback",
    summary="OIDC callback handler",
    description="Handles the callback from OIDC after user authentication.",
    response_class=HTMLResponse,
)
async def callback(
    request: Request,
    code: str = Query(..., description="Authorization code from OIDC provider"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    return_to: str | None = Query(None, description="URL to return to after login"),
):
    """Handle OIDC callback and exchange code for tokens."""
    # Only available in server mode
    if not config_loader.is_server_mode():
        raise HTTPException(
            status_code=404,
            detail="Authentication is only available in server mode",
        )

    # Retrieve code verifier and return_to from store
    stored_data = _pkce_store.pop(state, None)
    if not stored_data:
        logger.warning("State data not found for state parameter")
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter",
        )

    code_verifier = stored_data.get("code_verifier")
    return_to = stored_data.get("return_to")

    if not code_verifier:
        logger.warning("Code verifier not found in stored data")
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter",
        )

    try:
        # Lazy import to avoid loading auth modules in tracker mode
        from app.auth.oidc_handler import get_oidc_handler

        oidc_handler = get_oidc_handler()
        if oidc_handler is None:
            raise HTTPException(
                status_code=503,
                detail="OIDC handler is not available",
            )

        # Exchange code for tokens
        token_response = await oidc_handler.handle_callback(code, state, code_verifier)

        # Use ID token for authentication (it has the correct audience)
        # Access tokens in OIDC often have aud="account" instead of the client_id
        id_token = token_response.get("id_token")
        access_token = token_response.get("access_token")
        token = id_token or access_token

        if not token:
            raise ValueError("No token received from OIDC provider")

        # Validate token
        token_claims = await oidc_handler.validate_token(token)
        user_info = oidc_handler.extract_user_claims(token_claims)

        # Validate user groups
        required_groups = [f"tenant_{oidc_config.domain}", "ts_admin", "ts_staff"]
        is_authorized, error_msg = oidc_handler.validate_user_groups(user_info, required_groups)

        if not is_authorized:
            logger.warning(f"User {user_info.get('email')} authorization failed: {error_msg}")

            # Render forbidden page with user info
            base_url = os.environ.get("TSCONFIG_BASE_URL", "").rstrip("/")
            return templates.TemplateResponse(
                request,
                "forbidden.html",
                {
                    "base_url": base_url,
                    "user_email": user_info.get("email"),
                    "required_groups": required_groups,
                    "user_groups": user_info.get("groups", []),
                },
                status_code=403,
            )

        logger.info(f"User {user_info.get('email')} successfully authenticated and authorized")

        # Get base URL
        base_url = os.environ.get("TSCONFIG_BASE_URL", "").rstrip("/")

        # Determine redirect URL
        if return_to:
            redirect_url = return_to
        else:
            redirect_url = f"{base_url}/" if base_url else "/"

        # Set secure cookie with token
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.set_cookie(
            key="auth_token",
            value=token,
            httponly=True,
            secure=True,  # Only send over HTTPS
            samesite="lax",  # CSRF protection
            max_age=token_response.get("expires_in", 3600),  # Token lifetime
        )

        # Store refresh token if available (for token refresh)
        refresh_token = token_response.get("refresh_token")
        if refresh_token:
            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                httponly=True,
                secure=True,  # Only send over HTTPS
                samesite="lax",  # CSRF protection
                max_age=30 * 24 * 60 * 60,  # 30 days
            )
            logger.debug("Stored refresh token in secure cookie")

        return response

    except ValueError as e:
        logger.error(f"Authentication failed: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Authentication failed: {e!s}",
        )
    except HTTPException:
        # Re-raise HTTPExceptions (including our 403 from above)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during authentication: {e}")
        raise HTTPException(
            status_code=500,
            detail="Authentication failed due to server error",
        )


@router.post(
    "/refresh",
    summary="Refresh access token",
    description="Exchange refresh token for new access and ID tokens.",
)
async def refresh_token(
    refresh_token: str | None = Cookie(None),
):
    """Refresh access token using refresh token."""
    # Only available in server mode
    if not config_loader.is_server_mode():
        raise HTTPException(
            status_code=404,
            detail="Authentication is only available in server mode",
        )

    if not refresh_token:
        logger.warning("Token refresh requested but no refresh token provided")
        raise HTTPException(
            status_code=401,
            detail="No refresh token available",
        )

    try:
        # Lazy import to avoid loading auth modules in tracker mode
        from app.auth.oidc_handler import get_oidc_handler

        oidc_handler = get_oidc_handler()
        if oidc_handler is None:
            raise HTTPException(
                status_code=503,
                detail="OIDC handler is not available",
            )

        # Refresh the token
        token_response = await oidc_handler.refresh_access_token(refresh_token)

        # Use ID token for authentication (it has the correct audience)
        id_token = token_response.get("id_token")
        access_token = token_response.get("access_token")
        new_token = id_token or access_token

        if not new_token:
            raise ValueError("No token received from OIDC provider")

        # Validate the new token
        token_claims = await oidc_handler.validate_token(new_token)
        user_info = oidc_handler.extract_user_claims(token_claims)

        # Validate user groups
        required_groups = [f"tenant_{oidc_config.domain}", "ts_admin", "ts_staff"]
        is_authorized, error_msg = oidc_handler.validate_user_groups(user_info, required_groups)

        if not is_authorized:
            logger.warning(f"User {user_info.get('email')} authorization failed after refresh: {error_msg}")
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: {error_msg}",
            )

        logger.debug(f"Successfully refreshed token for user: {user_info.get('email')}")

        # Prepare response with updated cookies
        response = JSONResponse(content={"success": True, "user": user_info})

        # Update auth_token cookie
        response.set_cookie(
            key="auth_token",
            value=new_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=token_response.get("expires_in", 3600),
        )

        # Update refresh token if provider rotates it (some OIDC providers do this)
        new_refresh_token = token_response.get("refresh_token", refresh_token)
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=30 * 24 * 60 * 60,  # 30 days
        )

        return response

    except ValueError as e:
        logger.warning(f"Token refresh failed: {e}")
        raise HTTPException(
            status_code=401,
            detail="Token refresh failed",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during token refresh: {e}")
        raise HTTPException(
            status_code=500,
            detail="Token refresh failed due to server error",
        )


@router.get(
    "/logout",
    summary="Logout",
    description="Clears authentication cookie and ends OIDC SSO session.",
)
async def logout(
    request: Request,
    auth_token: str | None = Cookie(None),
):
    """Logout and clear authentication cookie, ending OIDC SSO session."""
    # Tracker mode: just drop the operator session
    if not config_loader.is_server_mode():
        response = RedirectResponse(url=f"{_base_url()}/auth/login", status_code=303)
        delete_session_cookies(response)
        return response

    # Get base URL
    base_url = os.environ.get("TSCONFIG_BASE_URL", "").rstrip("/")
    logout_url = f"{base_url}/" if base_url else "/"

    try:
        if oidc_config.is_configured() and auth_token:
            end_session_endpoint = await oidc_config.get_end_session_endpoint()
            if end_session_endpoint:
                # Build post-logout redirect URL
                # Extract base URL from redirect_uri
                if oidc_config.redirect_uri:
                    # Remove /auth/callback from redirect_uri to get base
                    redirect_base = oidc_config.redirect_uri.rsplit("/auth/callback", 1)[0]
                    post_logout_redirect = f"{redirect_base}/"
                else:
                    # Fallback to constructing from request
                    scheme = request.url.scheme
                    host = request.headers.get("host", "localhost")
                    post_logout_redirect = f"{scheme}://{host}{base_url}/"

                # URL encode the redirect URI
                encoded_redirect = quote(post_logout_redirect, safe="")

                # Build OIDC logout URL
                logout_url = (
                    f"{end_session_endpoint}?post_logout_redirect_uri={encoded_redirect}&id_token_hint={auth_token}"
                )
                logger.info(f"Redirecting to OIDC logout with post_logout_redirect_uri: {post_logout_redirect}")
    except Exception as e:
        logger.warning(f"Could not configure OIDC logout: {e}")

    # Clear authentication cookies
    response = RedirectResponse(url=logout_url, status_code=302)
    response.delete_cookie(key="auth_token", path="/", samesite="lax")
    response.delete_cookie(key="refresh_token", path="/", samesite="lax")

    logger.info("User logged out")
    return response


@router.get(
    "/frontchannel-logout",
    summary="OIDC Front-Channel Logout",
    description="Handles front-channel logout requests from OIDC provider via iframe.",
    response_class=HTMLResponse,
)
async def frontchannel_logout(
    request: Request,
    iss: str | None = Query(None, description="Issuer identifier"),
    sid: str | None = Query(None, description="Session ID"),
):
    """
    Handle OIDC front-channel logout.

    This endpoint is called by the OIDC provider via iframe when a user logs out
    from another service. It clears the local authentication cookie.

    Per OIDC Front-Channel Logout 1.0 spec:
    https://openid.net/specs/openid-connect-frontchannel-1_0.html
    """
    # Only available in server mode
    if not config_loader.is_server_mode():
        raise HTTPException(
            status_code=404,
            detail="Authentication is only available in server mode",
        )

    # Validate issuer if provided
    if iss:
        if not oidc_config.is_configured():
            logger.warning("Front-channel logout called but OIDC is not configured")
            raise HTTPException(
                status_code=503,
                detail="OIDC is not configured",
            )

        if iss != oidc_config.issuer_url:
            logger.warning(f"Front-channel logout called with invalid issuer: {iss}")
            raise HTTPException(
                status_code=400,
                detail="Invalid issuer",
            )

    # Log the logout event
    logger.info(f"Front-channel logout triggered for session: {sid if sid else 'unknown'}")

    # Clear the authentication cookies
    # Return minimal HTML with Set-Cookie header
    response = HTMLResponse(
        content="""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Logout</title>
</head>
<body>
    <!-- Front-channel logout successful -->
</body>
</html>""",
        status_code=200,
    )
    response.delete_cookie(key="auth_token", path="/", samesite="lax")
    response.delete_cookie(key="refresh_token", path="/", samesite="lax")

    return response


@router.get(
    "/userinfo",
    summary="Get current user information",
    description="Returns information about the currently authenticated user.",
)
async def userinfo(user: dict | None = Depends(get_current_user)):
    """Get current user information."""
    # Only available in server mode
    if not config_loader.is_server_mode():
        raise HTTPException(
            status_code=404,
            detail="Authentication is only available in server mode",
        )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return user


@router.get(
    "/status",
    summary="Get authentication status",
    description="Returns authentication configuration and status.",
)
async def auth_status(request: Request, user: dict | None = Depends(get_optional_user)):
    """Get authentication status."""
    is_server_mode = config_loader.is_server_mode()
    is_configured = oidc_config.is_configured()

    return {
        "server_mode": is_server_mode,
        "oidc_configured": is_configured,
        "authenticated": user is not None
        or (not is_server_mode and (has_valid_session(request) is not None or is_trusted_local(request))),
        "user": user if user else None,
    }
