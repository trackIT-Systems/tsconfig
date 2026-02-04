"""Authentication module for OIDC integration."""

from app.auth.oidc_config import oidc_config
from app.auth.oidc_handler import get_oidc_handler

# Get handler instance (None in tracker mode)
oidc_handler = get_oidc_handler()

__all__ = ["oidc_config", "oidc_handler", "get_oidc_handler"]
