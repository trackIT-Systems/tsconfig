"""Authentication module for OIDC/Keycloak integration."""

from app.auth.oidc_config import oidc_config
from app.auth.oidc_handler import oidc_handler

__all__ = ["oidc_config", "oidc_handler"]

