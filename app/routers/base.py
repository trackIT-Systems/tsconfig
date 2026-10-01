"""Base router for configuration endpoints to eliminate duplication."""

from typing import Any, Dict, Optional, Type

from fastapi import APIRouter, HTTPException

from app.config_loader import config_loader
from app.configs import BaseConfig
from app.utils.privileged import PrivilegedCallError, write_config


class BaseConfigRouter:
    """Generic base router for configuration endpoints."""

    def __init__(self, config_class: Type[BaseConfig], prefix: str, tag: str):
        # update_config_helper's tracker-mode save uses `prefix` directly as
        # the config_type passed to the privileged write op, which requires
        # it to be a real entry in app.configs.registry.CONFIG_TYPES - fail
        # at startup rather than with a confusing rejection from the
        # privileged CLI the first time someone saves this config.
        from app.configs.registry import CONFIG_TYPES

        if prefix not in CONFIG_TYPES:
            raise ValueError(
                f"BaseConfigRouter prefix '{prefix}' has no matching entry in "
                f"app.configs.registry.CONFIG_TYPES - privileged writes for it would fail"
            )

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

    async def update_config_helper(
        self, config_dict: Dict[str, Any], config_group: Optional[str] = None
    ) -> Dict[str, Any]:
        """Helper method to update configuration with a dictionary."""
        import traceback

        from app.logging_config import get_logger

        logger = get_logger(__name__)

        try:
            cfg_instance = self.get_config_instance(config_group)

            # Validate the configuration here too, even though the privileged
            # write path (tracker mode) re-validates independently before
            # writing - this copy is purely for fast, specific user feedback
            # (a 400 with the actual field errors) rather than a generic
            # failure from the privileged call.
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
                    logger.debug(f"Previous version directory: {old_latest_dir}")

                    # Create new versioned directory and update 'latest' symlink
                    try:
                        versioned_dir = config_loader.create_versioned_config_dir(config_group)
                        logger.debug(f"Created versioned directory: {versioned_dir}")
                    except Exception as e:
                        logger.error(f"Failed to create versioned directory: {e}")
                        logger.error(traceback.format_exc())
                        raise

                    # Copy all config files from previous version to new version
                    if old_latest_dir and old_latest_dir.exists():
                        import shutil

                        from app.config_loader import VERSIONED_CONFIG_FILES

                        for config_file in VERSIONED_CONFIG_FILES:
                            old_file = old_latest_dir / config_file
                            if old_file.exists():
                                try:
                                    new_file = versioned_dir / config_file
                                    shutil.copy2(old_file, new_file)
                                    logger.debug(f"Copied {config_file} from previous version")
                                except Exception as e:
                                    logger.warning(f"Failed to copy {config_file}: {e}")
                    else:
                        logger.debug("No previous version found, starting fresh")

                    # Create a new config instance pointing to the versioned directory
                    try:
                        versioned_cfg_instance = self.config_class(versioned_dir)
                        logger.debug("Created config instance for versioned directory")
                    except Exception as e:
                        logger.error(f"Failed to create config instance: {e}")
                        logger.error(traceback.format_exc())
                        raise

                    # Save to the versioned directory
                    try:
                        versioned_cfg_instance.save(config_dict)
                        logger.debug("Saved config to versioned directory")
                    except Exception as e:
                        logger.error(f"Failed to save config: {e}")
                        logger.error(traceback.format_exc())
                        raise
                else:
                    # Tracker mode: this config lives on the root-owned boot
                    # partition, so the write goes through the privileged op
                    # (which independently re-validates before saving) rather
                    # than calling cfg_instance.save() directly here - that
                    # would just fail with PermissionError as pi.
                    logger.debug("Tracker mode, saving via privileged op")
                    try:
                        await write_config(self.prefix, config_dict)
                    except PrivilegedCallError as e:
                        raise HTTPException(
                            status_code=500,
                            detail={"message": f"Failed to save {self.prefix} configuration", "error": str(e)},
                        )

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
