"""OIDC authentication handler."""

import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from app.auth.oidc_config import oidc_config
from app.logging_config import get_logger

logger = get_logger(__name__)


class OIDCHandler:
    """OIDC authentication handler."""

    def __init__(self):
        # Lazy import of itsdangerous to avoid import errors when auth group is not installed
        try:
            from itsdangerous import URLSafeTimedSerializer
        except ImportError:
            raise ImportError(
                "Auth dependencies are not installed. Install them with: pdm install -G auth"
            )
        
        # Secret for signing state parameters (CSRF protection)
        # In production, this should be loaded from an environment variable
        self.state_secret = secrets.token_urlsafe(32)
        self.state_serializer = URLSafeTimedSerializer(self.state_secret)
        self._jwks_cache: Optional[Dict[str, Any]] = None

    def generate_state(self) -> str:
        """Generate a secure state parameter for CSRF protection."""
        state_data = {"nonce": secrets.token_urlsafe(16)}
        return self.state_serializer.dumps(state_data)

    def validate_state(self, state: str, max_age: int = 600) -> bool:
        """
        Validate the state parameter.

        Args:
            state: State parameter from callback
            max_age: Maximum age in seconds (default 10 minutes)

        Returns:
            bool: True if state is valid
        """
        try:
            self.state_serializer.loads(state, max_age=max_age)
            return True
        except Exception as e:
            logger.warning(f"State validation failed: {e}")
            return False

    def generate_pkce_pair(self) -> tuple[str, str]:
        """
        Generate PKCE code verifier and challenge.

        Returns:
            tuple: (code_verifier, code_challenge)
        """
        # Generate code verifier (43-128 characters)
        code_verifier = secrets.token_urlsafe(32)  # 43 characters base64url

        # For simplicity, we're using plain method
        # In production, you should use S256 method
        code_challenge = code_verifier

        return code_verifier, code_challenge

    async def initiate_login(self, return_to: Optional[str] = None) -> tuple[str, str, str]:
        """
        Initiate OIDC authorization code flow.

        Args:
            return_to: Optional URL to return to after authentication

        Returns:
            tuple: (authorization_url, state, code_verifier)
        """
        if not oidc_config.is_configured():
            raise ValueError("OIDC is not configured")

        # Generate state and PKCE parameters
        state = self.generate_state()
        code_verifier, code_challenge = self.generate_pkce_pair()

        # Build authorization URL
        auth_endpoint = await oidc_config.get_authorization_endpoint()

        params = {
            "client_id": oidc_config.client_id,
            "response_type": "code",
            "scope": oidc_config.scopes,
            "redirect_uri": oidc_config.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "plain",  # Using plain for simplicity
        }

        authorization_url = f"{auth_endpoint}?{urlencode(params)}"

        logger.debug("Generated authorization URL for OIDC login")
        return authorization_url, state, code_verifier

    async def handle_callback(self, code: str, state: str, code_verifier: str) -> Dict[str, Any]:
        """
        Handle OIDC callback and exchange code for tokens.

        Args:
            code: Authorization code from callback
            state: State parameter from callback
            code_verifier: PKCE code verifier

        Returns:
            dict: Token response containing access_token, id_token, etc.

        Raises:
            ValueError: If state validation fails or token exchange fails
        """
        # Validate state
        if not self.validate_state(state):
            raise ValueError("Invalid or expired state parameter")

        # Exchange code for tokens
        token_endpoint = await oidc_config.get_token_endpoint()

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": oidc_config.redirect_uri,
            "client_id": oidc_config.client_id,
            "code_verifier": code_verifier,
        }

        # Add client_secret if configured
        if oidc_config.client_secret:
            data["client_secret"] = oidc_config.client_secret

        logger.debug("Exchanging authorization code for tokens")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )

            if response.status_code != 200:
                logger.error(f"Token exchange failed: {response.status_code} {response.text}")
                raise ValueError(f"Token exchange failed: {response.status_code}")

            token_response = response.json()

        logger.info("Successfully exchanged authorization code for tokens")
        return token_response

    async def _get_jwks(self) -> Dict[str, Any]:
        """Fetch JWKS (JSON Web Key Set) from the issuer."""
        if self._jwks_cache is not None:
            return self._jwks_cache

        jwks_uri = await oidc_config.get_jwks_uri()
        logger.debug(f"Fetching JWKS from {jwks_uri}")

        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_uri, timeout=10.0)
            response.raise_for_status()
            self._jwks_cache = response.json()

        return self._jwks_cache

    def clear_jwks_cache(self):
        """Clear the cached JWKS."""
        self._jwks_cache = None

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate JWT token.

        Args:
            token: JWT token to validate

        Returns:
            dict: Decoded token claims

        Raises:
            ValueError: If token validation fails
        """
        # Lazy import of authlib to avoid import errors when auth group is not installed
        try:
            from authlib.jose import JsonWebKey, jwt
            from authlib.jose.errors import JoseError
        except ImportError:
            raise ValueError(
                "Auth dependencies are not installed. Install them with: pdm install -G auth"
            )
        
        try:
            # Get JWKS for signature validation
            jwks_data = await self._get_jwks()
            jwks = JsonWebKey.import_key_set(jwks_data)

            # Decode and validate token
            claims = jwt.decode(token, jwks)

            # Validate claims
            claims.validate()

            # Additional validation
            if claims.get("iss") != oidc_config.issuer_url:
                raise ValueError(f"Invalid issuer: {claims.get('iss')}")

            if claims.get("aud") != oidc_config.client_id:
                # Some providers use array for aud
                if isinstance(claims.get("aud"), list):
                    if oidc_config.client_id not in claims.get("aud"):
                        raise ValueError(f"Invalid audience: {claims.get('aud')}")
                else:
                    raise ValueError(f"Invalid audience: {claims.get('aud')}")

            logger.debug(f"Successfully validated token for user: {claims.get('sub')}")
            return dict(claims)

        except JoseError as e:
            logger.warning(f"Token validation failed: {e}")
            raise ValueError(f"Invalid token: {e}")
        except Exception as e:
            logger.warning(f"Token validation failed: {e}")
            raise ValueError(f"Token validation failed: {e}")

    def extract_user_claims(self, token_claims: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract user information from token claims.

        Args:
            token_claims: Decoded token claims

        Returns:
            dict: User information
        """
        return {
            "sub": token_claims.get("sub"),
            "email": token_claims.get("email"),
            "name": token_claims.get("name"),
            "preferred_username": token_claims.get("preferred_username"),
            "given_name": token_claims.get("given_name"),
            "family_name": token_claims.get("family_name"),
            "groups": token_claims.get("groups", []),
        }

    def validate_user_groups(self, user_info: Dict[str, Any], required_groups: list[str]) -> tuple[bool, Optional[str]]:
        """
        Validate that user belongs to at least one of the required groups.

        Args:
            user_info: User information containing groups
            required_groups: List of group names that grant access

        Returns:
            tuple: (is_authorized, error_message)
        """
        user_groups = user_info.get("groups", [])

        if not user_groups:
            return False, "User has no groups assigned"

        # Check if user is in any of the required groups
        for group in user_groups:
            if group in required_groups:
                logger.info(f"User authorized via group: {group}")
                return True, None

        return False, f"User is not in any required groups. Required: {required_groups}, User has: {user_groups}"


# Global instance - only created in server mode
_oidc_handler_instance: Optional[OIDCHandler] = None


def get_oidc_handler() -> Optional[OIDCHandler]:
    """
    Get the OIDC handler instance.
    
    Returns None in tracker mode, or the handler instance in server mode.
    """
    global _oidc_handler_instance
    
    if _oidc_handler_instance is None:
        from app.config_loader import config_loader
        if config_loader.is_server_mode():
            _oidc_handler_instance = OIDCHandler()
    
    return _oidc_handler_instance


# For backward compatibility, provide oidc_handler that can be None
# This allows existing code to check `if oidc_handler:` before using it
oidc_handler: Optional[OIDCHandler] = None
