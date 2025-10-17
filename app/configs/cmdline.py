"""Cmdline configuration management."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.configs import BaseConfig


class CmdlineConfig(BaseConfig):
    """Cmdline.txt configuration management.

    This file contains kernel boot parameters and is written to /boot/firmware/cmdline.txt.
    The file format is: <technical_params> -- <user_params>
    
    When updating, only user parameters (after --) are modified, while technical
    parameters (before --) are preserved from the existing file.
    """

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/cmdline.txt")

    def _parse_cmdline(self, content: str) -> Tuple[str, Dict[str, str]]:
        """Parse cmdline content into technical params and user params.
        
        Args:
            content: Raw cmdline.txt content
            
        Returns:
            Tuple of (technical_params_str, user_params_dict)
        """
        content = content.strip()
        
        # Split on --
        if " -- " in content:
            technical_part, user_part = content.split(" -- ", 1)
        else:
            # No separator, treat everything as technical params
            technical_part = content
            user_part = ""
        
        # Parse user parameters into a dictionary
        user_params = {}
        if user_part:
            for param in user_part.split():
                if "=" in param:
                    key, value = param.split("=", 1)
                    user_params[key] = value
                else:
                    # Standalone flag without value
                    user_params[param] = ""
        
        return technical_part, user_params

    def _build_cmdline(self, technical_part: str, user_params: Dict[str, str]) -> str:
        """Build cmdline content from technical params and user params.
        
        Args:
            technical_part: Technical boot parameters string
            user_params: Dictionary of user parameters
            
        Returns:
            Complete cmdline string
        """
        if not user_params:
            return technical_part
        
        # Build user params string
        user_parts = []
        for key, value in user_params.items():
            if value:
                user_parts.append(f"{key}={value}")
            else:
                user_parts.append(key)
        
        user_part = " ".join(user_parts)
        return f"{technical_part} -- {user_part}"

    def load(self) -> Dict[str, Any]:
        """Load the cmdline configuration from disk.

        Returns:
            Dictionary with 'content' key containing the file content
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    content = f.read()
                return {"content": content}
            except (IOError, OSError):
                pass

        return {"content": ""}

    def save(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Save the cmdline configuration to disk.
        
        Only updates user parameters (after --) while preserving technical
        parameters (before --) from the existing file.

        Args:
            config: Dictionary with 'content' key containing the new content
            
        Returns:
            Dictionary with metadata about the save operation:
            - parameters_changed: bool indicating if any parameter changed
            - parameter_changes: dict mapping parameter names to change info:
                - status: 'added', 'modified', 'removed', or 'unchanged'
                - old_value: previous value (None if added)
                - new_value: new value (None if removed)
        """
        new_content = config.get("content", "").strip()
        
        # Load existing file
        existing_content = ""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    existing_content = f.read().strip()
            except (IOError, OSError):
                pass
        
        # Parse new content to extract user parameters
        _, new_user_params = self._parse_cmdline(new_content)
        
        # Parse existing content
        if existing_content:
            technical_part, existing_user_params = self._parse_cmdline(existing_content)
        else:
            # No existing file, extract technical part from new content
            technical_part, _ = self._parse_cmdline(new_content)
            existing_user_params = {}
        
        # Update user parameters with new ones
        updated_user_params = existing_user_params.copy()
        updated_user_params.update(new_user_params)
        
        # Build final content
        final_content = self._build_cmdline(technical_part, updated_user_params)
        
        # Track all parameter changes
        parameter_changes = {}
        parameters_changed = False
        
        # Get all unique parameter keys
        all_params = set(existing_user_params.keys()) | set(updated_user_params.keys())
        
        for param in all_params:
            old_value = existing_user_params.get(param)
            new_value = updated_user_params.get(param)
            
            if old_value is None and new_value is not None:
                # Parameter was added
                parameter_changes[param] = {
                    "status": "added",
                    "old_value": None,
                    "new_value": new_value
                }
                parameters_changed = True
            elif old_value is not None and new_value is None:
                # Parameter was removed (shouldn't happen with current logic, but handle it)
                parameter_changes[param] = {
                    "status": "removed",
                    "old_value": old_value,
                    "new_value": None
                }
                parameters_changed = True
            elif old_value != new_value:
                # Parameter was modified
                parameter_changes[param] = {
                    "status": "modified",
                    "old_value": old_value,
                    "new_value": new_value
                }
                parameters_changed = True
            else:
                # Parameter unchanged
                parameter_changes[param] = {
                    "status": "unchanged",
                    "old_value": old_value,
                    "new_value": new_value
                }
        
        # Write content to file
        with open(self.config_file, "w") as f:
            f.write(final_content)
        
        return {
            "parameters_changed": parameters_changed,
            "parameter_changes": parameter_changes,
        }

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the cmdline configuration.

        Args:
            config: Dictionary with 'content' key

        Returns:
            List of validation error messages (always empty - no validation)
        """
        # Just check if it is empty
        content = config.get("content", "")

        if not content or not content.strip():
            return ["Configuration file is empty"]
        return []
