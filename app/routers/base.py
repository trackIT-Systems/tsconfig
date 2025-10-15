"""Base router for configuration endpoints to eliminate duplication."""

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
        import logging
        import traceback

        logger = logging.getLogger(__name__)

        try:
            cfg_instance = self.get_config_instance(config_group)

            # Validate the configuration
            errors = cfg_instance.validate(config_dict)
            if errors:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": f"Invalid {self.prefix} configuration",
                        "errors": errors,
                    },
                )

            # Save the configuration
            try:
                # Check if we're in server mode
                if config_loader.is_server_mode():
                    # In server mode, we MUST have a config_group
                    if not config_group:
                        raise ValueError("Config group is required in server mode")

                    logger.info(f"Server mode detected, creating versioned directory for group: {config_group}")

                    # Get the previous latest directory before creating new one
                    # Resolve the symlink to get the actual directory path
                    old_latest_dir = config_loader.get_config_group_dir(config_group)
                    if old_latest_dir and old_latest_dir.is_symlink():
                        old_latest_dir = old_latest_dir.resolve()
                    logger.info(f"Previous version directory: {old_latest_dir}")

                    # Create new versioned directory and update 'latest' symlink
                    try:
                        versioned_dir = config_loader.create_versioned_config_dir(config_group)
                        logger.info(f"Created versioned directory: {versioned_dir}")
                    except Exception as e:
                        logger.error(f"Failed to create versioned directory: {e}")
                        logger.error(traceback.format_exc())
                        raise

                    # Copy all config files from previous version to new version
                    if old_latest_dir and old_latest_dir.exists():
                        import shutil

                        config_files = ["radiotracking.ini", "schedule.yml", "soundscapepipe.yml"]
                        for config_file in config_files:
                            old_file = old_latest_dir / config_file
                            if old_file.exists():
                                try:
                                    new_file = versioned_dir / config_file
                                    shutil.copy2(old_file, new_file)
                                    logger.info(f"Copied {config_file} from previous version")
                                except Exception as e:
                                    logger.warning(f"Failed to copy {config_file}: {e}")
                    else:
                        logger.info("No previous version found, starting fresh")

                    # Create a new config instance pointing to the versioned directory
                    try:
                        versioned_cfg_instance = self.config_class(versioned_dir)
                        logger.info("Created config instance for versioned directory")
                    except Exception as e:
                        logger.error(f"Failed to create config instance: {e}")
                        logger.error(traceback.format_exc())
                        raise

                    # Save to the versioned directory
                    try:
                        versioned_cfg_instance.save(config_dict)
                        logger.info("Saved config to versioned directory")
                    except Exception as e:
                        logger.error(f"Failed to save config: {e}")
                        logger.error(traceback.format_exc())
                        raise
                else:
                    # In tracker mode, save directly
                    logger.info("Tracker mode, saving directly")
                    cfg_instance.save(config_dict)

                return {"message": f"{self.prefix.title()} configuration updated successfully", "config": config_dict}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error saving configuration: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": f"Failed to save {self.prefix} configuration",
                        "error": str(e),
                        "type": type(e).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )

        except HTTPException:
            raise
        except Exception as e:
            # Handle other errors
            logger.error(f"Unexpected error in update_config_helper: {e}")
            logger.error(traceback.format_exc())
            error_detail: Dict[str, Any] = {
                "message": f"Failed to update {self.prefix} configuration",
                "error": str(e),
                "type": type(e).__name__,
                "traceback": traceback.format_exc(),
            }
            raise HTTPException(status_code=500, detail=error_detail)

    def validate_config_helper(self, config_dict: Dict[str, Any], config_group: Optional[str] = None) -> Dict[str, Any]:
        """Helper method to validate a configuration without saving it."""
        cfg_instance = self.get_config_instance(config_group)
        errors = cfg_instance.validate(config_dict)

        if errors:
            return {"valid": False, "errors": errors}
        return {
            "valid": True,
            "message": f"{self.prefix.title()} configuration is valid",
        }
