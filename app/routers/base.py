"""Base router for configuration endpoints to eliminate duplication."""

from pathlib import Path
from typing import Any, Dict, Optional, Type

from fastapi import APIRouter, HTTPException

from app.config_loader import config_loader
from app.configs import BaseConfig


class BaseConfigRouter:
    """Generic base router for configuration endpoints."""

    def __init__(self, config_class: Type[BaseConfig], prefix: str, tag: str):
        self.config_class = config_class
        self.config_instance = config_class()
        self.router = APIRouter(prefix=f"/api/{prefix}", tags=[tag])
        self.prefix = prefix
        self._setup_routes()

    def _setup_routes(self):
        """Set up the common routes for all config types."""
        self.router.add_api_route("", self.get_config, methods=["GET"])
        # Note: update_config and validate_config need to be overridden
        # in individual routers to handle specific Pydantic models

    def get_config_instance(self, config_group: Optional[str] = None):
        """Get the current configuration instance for a given config group."""
        if config_group:
            # In server mode with config group specified
            config_dir = config_loader.get_config_group_dir(config_group)
            if not config_dir:
                raise HTTPException(status_code=404, detail=f"Config group '{config_group}' not found")
            return self.config_class(config_dir)
        return self.config_instance

    async def get_config(self, config_group: Optional[str] = None) -> Dict[str, Any]:
        """Get the current configuration."""
        try:
            config = self.get_config_instance(config_group)
            return config.load()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"{self.prefix.title()} configuration not found")

    def update_config_helper(self, config_dict: Dict[str, Any], config_group: Optional[str] = None) -> Dict[str, Any]:
        """Helper method to update configuration with a dictionary."""
        try:
            cfg_instance = self.get_config_instance(config_group)

            # Validate the configuration
            errors = cfg_instance.validate(config_dict)
            if errors:
                raise HTTPException(
                    status_code=400, detail={"message": f"Invalid {self.prefix} configuration", "errors": errors}
                )

            # Save the configuration
            try:
                cfg_instance.save(config_dict)
                return {"message": f"{self.prefix.title()} configuration updated successfully", "config": config_dict}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        except Exception as e:
            # Handle other errors
            error_detail: Dict[str, Any] = {
                "message": f"Failed to update {self.prefix} configuration",
                "error": str(e),
                "type": type(e).__name__,
            }
            raise HTTPException(status_code=500, detail=error_detail)

    def validate_config_helper(self, config_dict: Dict[str, Any], config_group: Optional[str] = None) -> Dict[str, Any]:
        """Helper method to validate a configuration without saving it."""
        cfg_instance = self.get_config_instance(config_group)
        errors = cfg_instance.validate(config_dict)

        if errors:
            return {"valid": False, "errors": errors}
        return {"valid": True, "message": f"{self.prefix.title()} configuration is valid"}
