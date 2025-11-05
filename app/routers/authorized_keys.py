"""Authorized keys API endpoints."""

import httpx
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config_loader import config_loader
from app.configs.authorized_keys import AuthorizedKeysConfig

router = APIRouter(prefix="/api/authorized-keys", tags=["authorized_keys"])


class SSHKeyAdd(BaseModel):
    """Request model for adding an SSH key."""

    key: str = Field(..., description="The SSH public key to add")


class SSHKeyImport(BaseModel):
    """Request model for importing SSH keys from a platform."""

    platform: str = Field(..., description="Platform: 'github' or 'launchpad'")
    username: str = Field(..., description="Username on the platform")


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

    In tracker mode, only user keys can be updated. Server keys are read-only
    and managed via config upload.

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

        # In tracker mode (non-server mode), reject if any server keys are in the update
        if not config_loader.is_server_mode():
            for key in key_data.keys:
                if key.get("source") == "server":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot modify server-managed SSH keys. Server keys are managed via config upload."
                    )

        # Prepare the configuration - merge with existing server keys if in tracker mode
        if not config_loader.is_server_mode():
            # Load current config to get server keys
            current_config = config.load()
            server_keys = [k for k in current_config["keys"] if k.get("source") == "server"]
            
            # Combine submitted user keys with existing server keys
            updated_config = {"keys": key_data.keys + server_keys}
        else:
            # In server mode, just use the provided keys
            updated_config = {"keys": key_data.keys}

        # Validate the configuration
        errors = config.validate(updated_config)
        if errors:
            raise HTTPException(status_code=400, detail={"message": "Invalid SSH keys configuration", "errors": errors})

        # In server mode, create versioned directory
        if config_loader.is_server_mode() and config_group:
            # Import here to avoid circular dependency
            import shutil

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


async def fetch_keys_from_github(username: str) -> list[str]:
    """Fetch SSH keys from GitHub for a given username.
    
    Args:
        username: GitHub username
        
    Returns:
        List of SSH public keys
        
    Raises:
        HTTPException: If the request fails or user not found
    """
    url = f"https://api.github.com/users/{username}/keys"
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"GitHub user '{username}' not found")
            elif response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"GitHub API error: {response.status_code}"
                )
            
            data = response.json()
            
            if not isinstance(data, list):
                raise HTTPException(status_code=500, detail="Unexpected response format from GitHub")
            
            # Extract the key field from each object
            keys = [item.get("key", "").strip() for item in data if item.get("key")]
            
            return keys
            
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="GitHub API request timed out")
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Failed to connect to GitHub: {str(e)}")


async def fetch_keys_from_launchpad(username: str) -> list[str]:
    """Fetch SSH keys from Launchpad for a given username.
    
    Args:
        username: Launchpad username
        
    Returns:
        List of SSH public keys
        
    Raises:
        HTTPException: If the request fails or user not found
    """
    url = f"https://launchpad.net/~{username}/+sshkeys"
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            response = await client.get(url)
            
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Launchpad user '{username}' not found")
            elif response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Launchpad API error: {response.status_code}"
                )
            
            # Launchpad returns plain text with one key per line
            text = response.text
            
            # Filter out empty lines and comments
            keys = [
                line.strip() 
                for line in text.split("\n") 
                if line.strip() and not line.strip().startswith("#")
            ]
            
            return keys
            
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Launchpad API request timed out")
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Failed to connect to Launchpad: {str(e)}")


@router.post("/import", summary="Import SSH keys from platform")
async def import_ssh_keys_from_platform(
    import_data: SSHKeyImport, 
    config_group: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Import SSH keys from GitHub or Launchpad.
    
    Args:
        import_data: Platform and username information
        config_group: Optional config group name for server mode
        
    Returns:
        Success message with count of imported keys and updated key list
    """
    try:
        # Validate platform
        platform = import_data.platform.lower()
        if platform not in ["github", "launchpad"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid platform '{import_data.platform}'. Must be 'github' or 'launchpad'"
            )
        
        # Fetch keys from platform
        if platform == "github":
            keys = await fetch_keys_from_github(import_data.username)
        else:  # launchpad
            keys = await fetch_keys_from_launchpad(import_data.username)
        
        if not keys:
            raise HTTPException(
                status_code=404,
                detail=f"No SSH keys found for {platform} user '{import_data.username}'"
            )
        
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
        
        # Try to add each key
        imported_count = 0
        skipped_count = 0
        errors = []
        
        for key in keys:
            try:
                # Add comment to identify source if key doesn't already have one
                key_parts = key.strip().split()
                if len(key_parts) >= 2:
                    # Check if key already has a comment (3+ parts means it likely has a comment)
                    if len(key_parts) == 2:
                        # No comment, add source identifier
                        key_with_comment = f"{key.strip()} from-{platform}:{import_data.username}"
                    else:
                        # Has a comment, append source identifier
                        key_with_comment = f"{key.strip()} (from-{platform}:{import_data.username})"
                else:
                    # Malformed key, try to add it as-is and let validation catch it
                    key_with_comment = key
                
                updated_config = config.add_key(key_with_comment)
                
                # Validate the configuration
                validation_errors = config.validate(updated_config)
                if validation_errors:
                    errors.append(f"Invalid key format: {validation_errors[0]}")
                    continue
                
                # In server mode, create versioned directory
                if config_loader.is_server_mode() and config_group:
                    import shutil
                    
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
                imported_count += 1
                
            except ValueError as e:
                # Key already exists or invalid format
                error_msg = str(e)
                if "already exists" in error_msg:
                    skipped_count += 1
                else:
                    errors.append(error_msg)
        
        # Load final configuration
        final_config = config.load()
        
        # Build success message
        message_parts = []
        if imported_count > 0:
            message_parts.append(f"Successfully imported {imported_count} key(s)")
        if skipped_count > 0:
            message_parts.append(f"{skipped_count} key(s) already exist")
        if errors:
            message_parts.append(f"{len(errors)} key(s) failed to import")
        
        message = " • ".join(message_parts) if message_parts else "No keys imported"
        
        if imported_count == 0 and not skipped_count:
            raise HTTPException(
                status_code=400,
                detail={"message": message, "errors": errors}
            )
        
        return {
            "message": message,
            "imported": imported_count,
            "skipped": skipped_count,
            "errors": errors,
            "keys": final_config["keys"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to import SSH keys: {str(e)}")
