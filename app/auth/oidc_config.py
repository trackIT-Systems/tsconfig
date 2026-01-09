"""OIDC configuration management."""

import os
from typing import Any, Dict, Optional

import httpx

from app.logging_config import get_logger

logger = get_logger(__name__)


class OIDCConfig:
    """OIDC configuration manager."""

    def __init__(self):
        self._discovery_cache: Optional[Dict[str, Any]] = None
        self.issuer_url: Optional[str] = None
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.redirect_uri: Optional[str] = None
        self.scopes: str = "openid profile email"
        self._load_from_env()

    def _load_from_env(self):
        """Load OIDC settings from environment variables."""
        self.domain = os.environ.get("DOMAIN")
        self.issuer_url = f"https://auth.trackit.systems/application/o/{self.domain.replace('.', '-')}-tsconfig/"
        self.client_id = f"{self.domain}/tsconfig"
        self.client_secret = os.environ.get("TSCONFIG_OAUTH_CLIENT_SECRET")
        self.redirect_uri = f"https://{self.domain}/tsconfig/auth/callback"
        self.scopes = "openid profile email groups"

    def is_configured(self) -> bool:
        """Check if OIDC is properly configured."""
        return bool(self.issuer_url and self.client_id and self.redirect_uri)

    def validate(self) -> tuple[bool, Optional[str]]:
        """
        Validate OIDC configuration.

        Returns:
            tuple: (is_valid, error_message)
        """
        if not self.issuer_url:
            return False, "OIDC_ISSUER_URL environment variable is not set"
        if not self.client_id:
            return False, "OIDC_CLIENT_ID environment variable is not set"
        if not self.redirect_uri:
            return False, "OIDC_REDIRECT_URI environment variable is not set"

        # Validate issuer URL format
        if not self.issuer_url.startswith(("http://", "https://")):
            return False, "OIDC_ISSUER_URL must be a valid HTTP(S) URL"

        return True, None

    async def get_discovery_document(self) -> Dict[str, Any]:
        """
        Fetch OIDC discovery document from the issuer.

        Returns:
            dict: OIDC discovery document

        Raises:
            ValueError: If OIDC is not configured
            httpx.HTTPError: If the discovery document cannot be fetched
        """
        if not self.is_configured():
            raise ValueError("OIDC is not properly configured")

        if self._discovery_cache is not None:
            return self._discovery_cache

        discovery_url = f"{self.issuer_url}.well-known/openid-configuration"
        logger.debug(f"Fetching OIDC discovery document from {discovery_url}")

        async with httpx.AsyncClient() as client:
            response = await client.get(discovery_url, timeout=10.0)
            response.raise_for_status()
            self._discovery_cache = response.json()

        logger.info(f"Successfully loaded OIDC discovery document from {self.issuer_url}")
        return self._discovery_cache

    def clear_cache(self):
        """Clear the cached discovery document."""
        self._discovery_cache = None

    async def get_authorization_endpoint(self) -> str:
        """Get the authorization endpoint URL from the discovery document."""
        discovery = await self.get_discovery_document()
        return discovery["authorization_endpoint"]

    async def get_token_endpoint(self) -> str:
        """Get the token endpoint URL from the discovery document."""
        discovery = await self.get_discovery_document()
        return discovery["token_endpoint"]

    async def get_userinfo_endpoint(self) -> str:
        """Get the userinfo endpoint URL from the discovery document."""
        discovery = await self.get_discovery_document()
        return discovery["userinfo_endpoint"]

    async def get_end_session_endpoint(self) -> Optional[str]:
        """Get the end session (logout) endpoint URL from the discovery document."""
        discovery = await self.get_discovery_document()
        return discovery.get("end_session_endpoint")

    async def get_jwks_uri(self) -> str:
        """Get the JWKS URI from the discovery document."""
        discovery = await self.get_discovery_document()
        return discovery["jwks_uri"]

    def get_frontchannel_logout_uri(self) -> str:
        """
        Get the front-channel logout URI for this application.
        
        This URI should be registered with the OIDC provider to enable
        front-channel logout. When a user logs out from another service,
        the OIDC provider will embed an iframe to this URI to clear the
        local session.
        
        Returns:
            str: The full front-channel logout URI
        """
        if not self.redirect_uri:
            raise ValueError("redirect_uri is not configured")
        
        # Extract base URL from redirect_uri
        redirect_base = self.redirect_uri.rsplit("/auth/callback", 1)[0]
        return f"{redirect_base}/auth/frontchannel-logout"


# Global instance
oidc_config = OIDCConfig()
