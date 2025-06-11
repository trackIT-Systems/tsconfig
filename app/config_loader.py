"""Configuration loader for tsOS main configuration."""

from pathlib import Path
from typing import Dict, Any, Optional
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
                    with open(self.config_path, 'r') as f:
                        self._config_cache = yaml.safe_load(f) or {}
                else:
                    self._config_cache = {}
            except Exception:
                self._config_cache = {}
        return self._config_cache
    
    def get_config_dir(self) -> Path:
        """Get the configured directory for radiotracking.ini and schedule.yml files."""
        config = self.load_config()
        file_locations = config.get('file_locations', {})
        config_dir = file_locations.get('config_dir', '/boot/firmware')
        return Path(config_dir)
    
    def get_services_config(self) -> Dict[str, Any]:
        """Get the services configuration section."""
        config = self.load_config()
        return config.get('services', [])
    
    def get_status_refresh_interval(self) -> int:
        """Get the configured system status refresh interval in seconds."""
        config = self.load_config()
        system_config = config.get('system', {})
        return system_config.get('status_refresh_interval', 30)
    
    def reload_config(self):
        """Force reload of the configuration."""
        self._config_cache = None


# Global instance
config_loader = ConfigLoader() 