"""Authorized keys API endpoints."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config_loader import config_loader
from app.configs.authorized_keys import AuthorizedKeysConfig
from app.routers.base import BaseConfigRouter

router = APIRouter(prefix="/api/authorized-keys", tags=["authorized_keys"])


class SSHKeyAdd(BaseModel):
    """Request model for adding an SSH key."""

    key: str = Field(..., description="The SSH public key to add")


class SSHKeyUpdate(BaseModel):
    """Request model for updating the entire key list."""

    keys: list = Field(..., description="List of SSH key objects")


@router.get("", summary="Get authorized SSH keys")
async def get_authorized_keys(config_group: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Get the list of authorized SSH keys.

    Args:
        config_group: Optional config group name for server mode

    Returns:
        Dictionary with 'keys' list containing key information
    """
    try:
        # Get config directory for the group (if specified)
        config_dir = None
        if config_group:
            config_dir = config_loader.get_config_group_dir(config_group)
            if not config_dir:
                raise HTTPException(
                    status_code=404,
                    detail=f"Config group '{config_group}' not found",
                )

        # Create config instance
        config = AuthorizedKeysConfig(config_dir) if config_dir else AuthorizedKeysConfig()

        # Load and return keys
        return config.load()

    except HTTPException:
        # Re-raise HTTP exceptions (like config group not found)
        raise
    except Exception:
        # If file doesn't exist or other errors, return empty list
        return {"keys": []}


@router.post("", summary="Add an SSH key")
async def add_authorized_key(key_data: SSHKeyAdd, config_group: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Add a new SSH key to the authorized keys.

    Args:
        key_data: The SSH key to add
        config_group: Optional config group name for server mode

    Returns:
        Success message and updated key list
    """
    try:
        # Get config directory for the group (if specified)
        config_dir = None
        if config_group:
            config_dir = config_loader.get_config_group_dir(config_group)
            if not config_dir:
                raise HTTPException(
                    status_code=404,
                    detail=f"Config group '{config_group}' not found",
                )

        # Create config instance
        config = AuthorizedKeysConfig(config_dir) if config_dir else AuthorizedKeysConfig()

        # Add the key
        try:
            updated_config = config.add_key(key_data.key.strip())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Validate the configuration
        errors = config.validate(updated_config)
        if errors:
            raise HTTPException(status_code=400, detail={"message": "Invalid SSH key", "errors": errors})

        # In server mode, create versioned directory
        if config_loader.is_server_mode() and config_group:
            # Import here to avoid circular dependency
            import shutil
            from pathlib import Path

            # Get the previous latest directory
            old_latest_dir = config_loader.get_config_group_dir(config_group)
            if old_latest_dir and old_latest_dir.is_symlink():
                old_latest_dir = old_latest_dir.resolve()

            # Create new versioned directory
            versioned_dir = config_loader.create_versioned_config_dir(config_group)

            # Copy all config files from previous version
            if old_latest_dir and old_latest_dir.exists():
                config_files = ["radiotracking.ini", "schedule.yml", "soundscapepipe.yml", "authorized_keys"]
                for config_file in config_files:
                    old_file = old_latest_dir / config_file
                    if old_file.exists():
                        new_file = versioned_dir / config_file
                        shutil.copy2(old_file, new_file)

            # Create new config instance pointing to versioned directory
            config = AuthorizedKeysConfig(versioned_dir)

        # Save the configuration
        config.save(updated_config)

        return {"message": "SSH key added successfully", "keys": updated_config["keys"]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add SSH key: {str(e)}")


@router.delete("/{key_index}", summary="Remove an SSH key")
async def remove_authorized_key(key_index: int, config_group: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Remove an SSH key from the authorized keys by index.

    Args:
        key_index: The index of the key to remove
        config_group: Optional config group name for server mode

    Returns:
        Success message and updated key list
    """
    try:
        # Get config directory for the group (if specified)
        config_dir = None
        if config_group:
            config_dir = config_loader.get_config_group_dir(config_group)
            if not config_dir:
                raise HTTPException(
                    status_code=404,
                    detail=f"Config group '{config_group}' not found",
                )

        # Create config instance
        config = AuthorizedKeysConfig(config_dir) if config_dir else AuthorizedKeysConfig()

        # Remove the key
        try:
            updated_config = config.remove_key(key_index)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # In server mode, create versioned directory
        if config_loader.is_server_mode() and config_group:
            # Import here to avoid circular dependency
            import shutil
            from pathlib import Path

            # Get the previous latest directory
            old_latest_dir = config_loader.get_config_group_dir(config_group)
            if old_latest_dir and old_latest_dir.is_symlink():
                old_latest_dir = old_latest_dir.resolve()

            # Create new versioned directory
            versioned_dir = config_loader.create_versioned_config_dir(config_group)

            # Copy all config files from previous version
            if old_latest_dir and old_latest_dir.exists():
                config_files = ["radiotracking.ini", "schedule.yml", "soundscapepipe.yml", "authorized_keys"]
                for config_file in config_files:
                    old_file = old_latest_dir / config_file
                    if old_file.exists():
                        new_file = versioned_dir / config_file
                        shutil.copy2(old_file, new_file)

            # Create new config instance pointing to versioned directory
            config = AuthorizedKeysConfig(versioned_dir)

        # Save the configuration
        config.save(updated_config)

        return {"message": "SSH key removed successfully", "keys": updated_config["keys"]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove SSH key: {str(e)}")


@router.put("", summary="Update all SSH keys")
async def update_authorized_keys(key_data: SSHKeyUpdate, config_group: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Update the entire list of authorized SSH keys.

    Args:
        key_data: The complete list of SSH keys
        config_group: Optional config group name for server mode

    Returns:
        Success message
    """
    try:
        # Get config directory for the group (if specified)
        config_dir = None
        if config_group:
            config_dir = config_loader.get_config_group_dir(config_group)
            if not config_dir:
                raise HTTPException(
                    status_code=404,
                    detail=f"Config group '{config_group}' not found",
                )

        # Create config instance
        config = AuthorizedKeysConfig(config_dir) if config_dir else AuthorizedKeysConfig()

        # Prepare the configuration
        updated_config = {"keys": key_data.keys}

        # Validate the configuration
        errors = config.validate(updated_config)
        if errors:
            raise HTTPException(status_code=400, detail={"message": "Invalid SSH keys configuration", "errors": errors})

        # In server mode, create versioned directory
        if config_loader.is_server_mode() and config_group:
            # Import here to avoid circular dependency
            import shutil
            from pathlib import Path

            # Get the previous latest directory
            old_latest_dir = config_loader.get_config_group_dir(config_group)
            if old_latest_dir and old_latest_dir.is_symlink():
                old_latest_dir = old_latest_dir.resolve()

            # Create new versioned directory
            versioned_dir = config_loader.create_versioned_config_dir(config_group)

            # Copy all config files from previous version
            if old_latest_dir and old_latest_dir.exists():
                config_files = ["radiotracking.ini", "schedule.yml", "soundscapepipe.yml", "authorized_keys"]
                for config_file in config_files:
                    old_file = old_latest_dir / config_file
                    if old_file.exists():
                        new_file = versioned_dir / config_file
                        shutil.copy2(old_file, new_file)

            # Create new config instance pointing to versioned directory
            config = AuthorizedKeysConfig(versioned_dir)

        # Save the configuration
        config.save(updated_config)

        return {"message": "SSH keys updated successfully", "keys": updated_config["keys"]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update SSH keys: {str(e)}")
