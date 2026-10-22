"""Base router for configuration endpoints to eliminate duplication."""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.config_loader import config_loader
from app.configs import BaseConfig, support
from app.configs.registry import CONFIG_TYPES
from app.logging_config import get_logger
from app.utils.privileged import PrivilegedCallError, write_config

logger = get_logger(__name__)


class BaseConfigRouter:
    """Generic base router for configuration endpoints."""

    def __init__(self, config_class: type[BaseConfig], prefix: str, tag: str):
        # update_config_helper's tracker-mode save uses `prefix` directly as
        # the config_type passed to the privileged write op, which requires
        # it to be a real entry in app.configs.registry.CONFIG_TYPES - fail
        # at startup rather than with a confusing rejection from the
        # privileged CLI the first time someone saves this config.
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

    def get_config_instance(self, config_group: str | None = None):
        """Get the current configuration instance for a given config group."""
        if config_group:
            # In server mode with config group specified
            config_dir = config_loader.get_config_group_dir(config_group)
            if not config_dir:
                raise HTTPException(status_code=404, detail=f"Config group '{config_group}' not found")
            return self.config_class(config_dir)
        return self.config_instance

    async def get_config(self, config_group: str | None = None) -> dict[str, Any]:
        """Get the current configuration."""
        try:
            config = self.get_config_instance(config_group)
            return config.load()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"{self.prefix.title()} configuration not found")

    async def update_config_helper(
        self, config_dict: dict[str, Any], config_group: str | None = None
    ) -> dict[str, Any]:
        """Helper method to update configuration with a dictionary."""
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

                    logger.info("Saving the %s configuration of group %s as a new version", self.prefix, config_group)

                    # Get the previous latest directory before creating new one
                    # Resolve the symlink to get the actual directory path
                    old_latest_dir = config_loader.get_config_group_dir(config_group)
                    if old_latest_dir and old_latest_dir.is_symlink():
                        old_latest_dir = old_latest_dir.resolve()
                    logger.debug("Previous version directory: %s", old_latest_dir)

                    # Create new versioned directory and update 'latest' symlink
                    try:
                        versioned_dir = config_loader.create_versioned_config_dir(config_group)
                        logger.debug("Created versioned directory: %s", versioned_dir)
                    except Exception as e:
                        logger.debug("Cannot create a versioned directory: %s", e)
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
                                    logger.debug("Copied %s from previous version", config_file)
                                except OSError as e:
                                    logger.warning("Cannot copy %s from the previous version: %s", config_file, e)
                    else:
                        logger.debug("No previous version found, starting fresh")

                    # Create a new config instance pointing to the versioned directory
                    try:
                        versioned_cfg_instance = self.config_class(versioned_dir)
                        logger.debug("Created config instance for versioned directory")
                    except Exception as e:
                        logger.debug("Cannot create a config instance for the versioned directory: %s", e)
                        raise

                    # Save to the versioned directory
                    try:
                        versioned_cfg_instance.save(config_dict)
                        logger.debug("Saved config to versioned directory")
                    except Exception as e:
                        logger.debug("Cannot save to the versioned directory: %s", e)
                        raise
                else:
                    # Tracker mode: this config lives on the root-owned boot
                    # partition, so the write goes through the privileged op
                    # (which independently re-validates before saving) rather
                    # than calling cfg_instance.save() directly here - that
                    # would just fail with PermissionError as pi.
                    logger.debug("Tracker mode, saving via privileged op")
                    # Same rule as for uploads: no config for a service that
                    # is not installed on this device
                    unsupported = await support.unsupported_config_types([self.prefix])
                    if unsupported:
                        files_by_type = {self.prefix: CONFIG_TYPES[self.prefix]["basename"]}
                        raise HTTPException(
                            status_code=400,
                            detail={"message": support.unsupported_message(unsupported, files_by_type)},
                        )
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
                logger.exception("Cannot save the %s configuration", self.prefix)
                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": f"Failed to save {self.prefix} configuration",
                        "error": str(e),
                        "type": type(e).__name__,
                    },
                )

        except HTTPException:
            raise
        except Exception as e:
            # Handle other errors
            logger.exception("Cannot update the %s configuration", self.prefix)
            error_detail: dict[str, Any] = {
                "message": f"Failed to update {self.prefix} configuration",
                "error": str(e),
                "type": type(e).__name__,
            }
            raise HTTPException(status_code=500, detail=error_detail)

    def validate_config_helper(self, config_dict: dict[str, Any], config_group: str | None = None) -> dict[str, Any]:
        """Helper method to validate a configuration without saving it."""
        cfg_instance = self.get_config_instance(config_group)
        errors = cfg_instance.validate(config_dict)

        if errors:
            return {"valid": False, "errors": errors}
        return {
            "valid": True,
            "message": f"{self.prefix.title()} configuration is valid",
        }
