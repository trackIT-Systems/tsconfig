"""Base router for configuration endpoints to eliminate duplication."""

import io
from typing import Any, Dict, Type

import yaml
from configparser import ConfigParser
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

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
        self.router.add_api_route("/reload", self.reload_config, methods=["POST"])
        self.router.add_api_route("", self.get_config, methods=["GET"])
        # Note: update_config, validate_config, and download_config need to be overridden 
        # in individual routers to handle specific Pydantic models
    
    def get_config_instance(self):
        """Get the current configuration instance."""
        return self.config_instance
    
    def reload_config_instance(self):
        """Reload the configuration with updated paths."""
        self.config_instance = self.config_class()
    
    async def reload_config(self):
        """Reload the configuration with updated file locations."""
        try:
            self.reload_config_instance()
            return {"message": f"{self.prefix.title()} configuration reloaded successfully"}
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to reload {self.prefix} configuration: {str(e)}"
            )
    
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
                    status_code=400, 
                    detail={"message": f"Invalid {self.prefix} configuration", "errors": errors}
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
    
    def download_config_helper(self, config_dict: Dict[str, Any]) -> StreamingResponse:
        """Helper method to download the configuration file without saving it."""
        try:
            cfg_instance = self.get_config_instance()

            # Validate the configuration
            errors = cfg_instance.validate(config_dict)
            if errors:
                raise HTTPException(
                    status_code=400, 
                    detail={"message": f"Invalid {self.prefix} configuration", "errors": errors}
                )

            # Generate content based on config type
            if self.prefix == 'radiotracking':
                content = self._generate_ini_content(config_dict)
                media_type = "application/x-ini"
                filename = f"{self.prefix}.ini"
            else:
                content = yaml.safe_dump(config_dict, default_flow_style=False)
                media_type = "application/x-yaml"
                filename = f"{self.prefix}.yml"

            return StreamingResponse(
                io.BytesIO(content.encode()),
                media_type=media_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
            
        except Exception as e:
            error_detail: Dict[str, Any] = {
                "message": f"Failed to generate {self.prefix} configuration",
                "error": str(e),
                "type": type(e).__name__,
            }
            raise HTTPException(status_code=500, detail=error_detail)
    
    def _generate_ini_content(self, config_dict: Dict[str, Any]) -> str:
        """Generate INI content for radiotracking configuration."""
        parser = ConfigParser()
        for section, values in config_dict.items():
            parser[section] = {
                key: self._convert_to_ini_value(value) 
                for key, value in values.items()
            }

        ini_buffer = io.StringIO()
        parser.write(ini_buffer)
        content = ini_buffer.getvalue()
        ini_buffer.close()
        return content
    
    def _convert_to_ini_value(self, value: Any) -> str:
        """Convert a value to INI format."""
        if isinstance(value, bool):
            return str(value).lower()
        elif isinstance(value, list):
            return ','.join(map(str, value))
        else:
            return str(value) 