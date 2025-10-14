"""Configuration loader for tsOS main configuration."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class ConfigLoader:
    """Load and manage the main tsconfig.yml configuration."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("configs/tsconfig.yml")
        self._config_cache: Optional[Dict[str, Any]] = None

    def load_config(self) -> Dict[str, Any]:
        """Load the main configuration file."""
        if self._config_cache is None:
            try:
                if self.config_path.exists():
                    with open(self.config_path, "r") as f:
                        self._config_cache = yaml.safe_load(f) or {}
                else:
                    self._config_cache = {}
            except Exception:
                self._config_cache = {}
        return self._config_cache

    def get_config_dir(self) -> Path:
        """Get the configured directory for radiotracking.ini and schedule.yml files."""
        config = self.load_config()
        file_locations = config.get("file_locations", {})
        config_dir = file_locations.get("config_dir", "/boot/firmware")
        return Path(config_dir)

    def get_services_config(self) -> Dict[str, Any]:
        """Get the services configuration section."""
        config = self.load_config()
        return config.get("services", [])

    def get_status_refresh_interval(self) -> int:
        """Get the configured system status refresh interval in seconds."""
        config = self.load_config()
        system_config = config.get("system", {})
        return system_config.get("status_refresh_interval", 30)

    def reload_config(self):
        """Force reload of the configuration."""
        self._config_cache = None

    def is_server_mode(self) -> bool:
        """Check if server mode is enabled via environment variable.

        By default, runs in tracker mode (for sensor stations).
        Set TSCONFIG_SERVER_MODE=true to enable server mode (for remote configuration).
        """
        return os.environ.get("TSCONFIG_SERVER_MODE", "").lower() in ("true", "1", "yes")

    def get_config_root(self) -> Optional[Path]:
        """Get the config root directory for server mode.

        Returns None in tracker mode (default).
        """
        if not self.is_server_mode():
            return None

        config_root = os.environ.get("TSCONFIG_CONFIG_ROOT")
        if config_root:
            return Path(config_root)
        return None

    def list_config_groups(self) -> List[str]:
        """List available config groups in server mode.

        Returns empty list in tracker mode (default).
        """
        if not self.is_server_mode():
            return []

        config_root = self.get_config_root()
        if not config_root or not config_root.exists():
            return []

        # Return only directories that contain at least one config file
        config_groups = []
        for item in config_root.iterdir():
            if item.is_dir():
                # Check if directory contains any config files
                has_config = (
                    (item / "radiotracking.ini").exists()
                    or (item / "schedule.yml").exists()
                    or (item / "soundscapepipe.yml").exists()
                )
                if has_config:
                    config_groups.append(item.name)

        return sorted(config_groups)

    def get_config_group_dir(self, config_group: str) -> Optional[Path]:
        """Get the config directory for a specific config group.

        Returns None in tracker mode (default).
        """
        if not self.is_server_mode():
            return None

        config_root = self.get_config_root()
        if not config_root:
            return None

        group_dir = config_root / config_group
        if group_dir.exists() and group_dir.is_dir():
            return group_dir

        return None


# Global instance
config_loader = ConfigLoader()
