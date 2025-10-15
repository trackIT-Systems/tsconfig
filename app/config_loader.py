"""Configuration loader for tsOS main configuration."""

import os
from datetime import datetime
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
        Config groups must have a 'latest' subdirectory.
        """
        if not self.is_server_mode():
            return []

        config_root = self.get_config_root()
        if not config_root or not config_root.exists():
            return []

        # Return only directories that contain a 'latest' subdirectory with config files
        config_groups = []
        for item in config_root.iterdir():
            if item.is_dir():
                latest_dir = item / "latest"
                if latest_dir.exists() and latest_dir.is_dir():
                    # Check if latest directory contains any config files
                    has_config = (
                        (latest_dir / "radiotracking.ini").exists()
                        or (latest_dir / "schedule.yml").exists()
                        or (latest_dir / "soundscapepipe.yml").exists()
                    )
                    if has_config:
                        config_groups.append(item.name)

        return sorted(config_groups)

    def get_config_group_dir(self, config_group: str) -> Optional[Path]:
        """Get the config directory for a specific config group.

        Returns the 'latest' subdirectory within the config group.
        Returns None in tracker mode (default).
        """
        if not self.is_server_mode():
            return None

        config_root = self.get_config_root()
        if not config_root:
            return None

        group_dir = config_root / config_group / "latest"
        if group_dir.exists() and group_dir.is_dir():
            return group_dir

        return None

    def create_versioned_config_dir(self, config_group: str) -> Path:
        """Create a new timestamped config directory and update the 'latest' symlink.

        Args:
            config_group: The name of the config group

        Returns:
            Path to the newly created versioned directory

        Raises:
            ValueError: If not in server mode or config_root is not set
            OSError: If directory creation or symlink update fails
        """
        if not self.is_server_mode():
            raise ValueError("Versioned config directories are only available in server mode")

        config_root = self.get_config_root()
        if not config_root:
            raise ValueError("TSCONFIG_CONFIG_ROOT environment variable is not set")

        # Create the group directory if it doesn't exist
        group_root = config_root / config_group
        group_root.mkdir(parents=True, exist_ok=True)

        # Create timestamped directory (format: YYYYMMDD_HHMMSS)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        versioned_dir = group_root / timestamp
        versioned_dir.mkdir(parents=True, exist_ok=True)

        # Update the 'latest' symlink
        latest_symlink = group_root / "latest"

        # Remove existing symlink if it exists
        if latest_symlink.exists() or latest_symlink.is_symlink():
            latest_symlink.unlink()

        # Create new symlink pointing to the timestamped directory
        # Use relative path for the symlink target
        latest_symlink.symlink_to(timestamp, target_is_directory=True)

        return versioned_dir


# Global instance
config_loader = ConfigLoader()
