"""FastAPI authentication dependencies."""

from typing import Optional

from fastapi import Cookie, Header, HTTPException, Request

from app.auth.oidc_config import oidc_config
from app.config_loader import config_loader
from app.logging_config import get_logger

logger = get_logger(__name__)


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    auth_token: Optional[str] = Cookie(None),
) -> Optional[dict]:
    """
    FastAPI dependency to get the current authenticated user.

    In tracker mode: Always returns None (no authentication required)
    In server mode: Validates JWT token and returns user claims

    Args:
        request: FastAPI request object
        authorization: Authorization header (Bearer token)
        auth_token: Authentication cookie

    Returns:
        dict: User claims if authenticated, None in tracker mode

    Raises:
        HTTPException: 401 if authentication is required but invalid/missing
    """
    # In tracker mode, no authentication required
    if not config_loader.is_server_mode():
        return None

    # In server mode, authentication is required
    if not oidc_config.is_configured():
        logger.error("OIDC is not configured but server mode is enabled")
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured",
        )

    # Extract token from Authorization header or cookie
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]  # Remove "Bearer " prefix
    elif auth_token:
        token = auth_token

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate token
    try:
        # Lazy import to avoid loading auth modules in tracker mode
        from app.auth.oidc_handler import get_oidc_handler
        
        oidc_handler = get_oidc_handler()
        if oidc_handler is None:
            logger.error("OIDC handler is not available")
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            )
        
        token_claims = await oidc_handler.validate_token(token)
        user_info = oidc_handler.extract_user_claims(token_claims)

        # Validate user groups
        required_groups = [f"tenant_{oidc_config.domain}", "ts_admin", "ts_staff"]
        is_authorized, error_msg = oidc_handler.validate_user_groups(user_info, required_groups)

        if not is_authorized:
            logger.warning(f"User {user_info.get('email')} authorization failed: {error_msg}")
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: {error_msg}",
            )

        return user_info
    except ValueError as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    auth_token: Optional[str] = Cookie(None),
) -> Optional[dict]:
    """
    FastAPI dependency to get the current user if authenticated, but don't require it.

    This is useful for endpoints that can work with or without authentication.

    Args:
        request: FastAPI request object
        authorization: Authorization header (Bearer token)
        auth_token: Authentication cookie

    Returns:
        dict: User claims if authenticated, None otherwise
    """
    try:
        return await get_current_user(request, authorization, auth_token)
    except HTTPException:
        return None
