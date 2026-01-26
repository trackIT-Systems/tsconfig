"""Cmdline configuration management."""

from pathlib import Path
from typing import Any, Dict, List

from app.configs import BaseConfig


class CmdlineConfig(BaseConfig):
    """Cmdline.txt configuration management.

    This file contains kernel boot parameters and is written to /boot/firmware/cmdline.txt.
    
    When updating, only parameters listed in CONFIGURABLE_PARAMETERS are modified,
    while all other parameters are preserved from the existing file.
    """

    # Parameters that can be updated via tsconfig
    CONFIGURABLE_PARAMETERS = [
        "timezone",
        "systemd.hostname",
        "cfg80211.ieee80211_regdom",
    ]

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/cmdline.txt")

    def _parse_cmdline(self, content: str) -> Dict[str, str]:
        """Parse cmdline content into a dictionary of parameters.
        
        Args:
            content: Raw cmdline.txt content
            
        Returns:
            Dictionary mapping parameter names to values (empty string for flags without values)
        """
        content = content.strip()
        params = {}
        
        if not content:
            return params
        
        # Parse all parameters (split by spaces)
        for param in content.split():
            # Skip empty strings
            if not param:
                continue
            
            if "=" in param:
                key, value = param.split("=", 1)
                params[key] = value
            else:
                # Standalone flag without value
                params[param] = ""
        
        return params

    def _build_cmdline(self, params: Dict[str, str]) -> str:
        """Build cmdline content from a dictionary of parameters.
        
        Args:
            params: Dictionary mapping parameter names to values
            
        Returns:
            Complete cmdline string with parameters joined by spaces
        """
        if not params:
            return ""
        
        parts = []
        for key, value in sorted(params.items()):
            if value:
                parts.append(f"{key}={value}")
            else:
                parts.append(key)
        
        return " ".join(parts)

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
        
        Only updates parameters listed in CONFIGURABLE_PARAMETERS from the new content,
        while preserving all other parameters from the existing file.

        Args:
            config: Dictionary with 'content' key containing the new content
            
        Returns:
            Dictionary with metadata about the save operation:
            - parameters_changed: bool indicating if any whitelisted parameter changed
            - parameter_changes: dict mapping whitelisted parameter names to change info:
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
        
        # Parse all parameters from existing and new content
        existing_params = self._parse_cmdline(existing_content) if existing_content else {}
        new_params = self._parse_cmdline(new_content)
        
        # Extract only whitelisted parameters from new content
        new_whitelisted_params = {
            key: value
            for key, value in new_params.items()
            if key in self.CONFIGURABLE_PARAMETERS
        }
        
        # Start with all existing parameters
        final_params = existing_params.copy()
        
        # Update only whitelisted parameters from new content
        final_params.update(new_whitelisted_params)
        
        # Build final content
        final_content = self._build_cmdline(final_params)
        
        # Track changes only for whitelisted parameters
        parameter_changes = {}
        parameters_changed = False
        
        # Check changes for each whitelisted parameter
        for param in self.CONFIGURABLE_PARAMETERS:
            old_value = existing_params.get(param)
            new_value = final_params.get(param)
            
            if old_value is None and new_value is not None:
                # Parameter was added
                parameter_changes[param] = {
                    "status": "added",
                    "old_value": None,
                    "new_value": new_value
                }
                parameters_changed = True
            elif old_value is not None and new_value is None:
                # Parameter was removed
                # Note: This case should not occur with current merge logic since we preserve
                # existing parameters. It's included for completeness in case the logic changes.
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
