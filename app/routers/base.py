"""Base router for configuration endpoints to eliminate duplication."""

from typing import Any, Dict, Type

from fastapi import APIRouter, HTTPException

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

    def get_config_instance(self):
        """Get the current configuration instance."""
        return self.config_instance

    async def get_config(self) -> Dict[str, Any]:
        """Get the current configuration."""
        try:
            config = self.get_config_instance()
            return config.load()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"{self.prefix.title()} configuration not found")

    def update_config_helper(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Helper method to update configuration with a dictionary."""
        try:
            cfg_instance = self.get_config_instance()

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

    def validate_config_helper(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Helper method to validate a configuration without saving it."""
        cfg_instance = self.get_config_instance()
        errors = cfg_instance.validate(config_dict)

        if errors:
            return {"valid": False, "errors": errors}
        return {"valid": True, "message": f"{self.prefix.title()} configuration is valid"}
