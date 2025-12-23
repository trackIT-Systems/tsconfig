"""Tsupdate daemon configuration management."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from app.configs import BaseConfig


class TsupdateConfig(BaseConfig):
    """Tsupdate daemon configuration management."""

    def __init__(self, config_dir: Path | None = None):
        # If no custom config_dir provided, use the config loader to get the configured directory
        if config_dir is None:
            try:
                from app.config_loader import config_loader

                config_dir = config_loader.get_config_dir()
            except ImportError:
                # Fallback to default if config_loader is not available
                config_dir = Path("/boot/firmware")
        super().__init__(config_dir)

    @property
    def config_file(self) -> Path:
        return self.config_dir / "tsupdate.yml"

    def load(self) -> Dict[str, Any]:
        """Load the tsupdate configuration from disk.
        
        Returns default values if the file doesn't exist.
        """
        try:
            with open(self.config_file, "r") as f:
                data = yaml.safe_load(f)
                if data is None:
                    data = {}
        except FileNotFoundError:
            # Return default values if file doesn't exist
            data = {}
        
        # Apply defaults for missing values
        defaults = {
            "check_interval": 3600,
            "include_prereleases": False,
            "max_releases": 5,
            "persist_timeout": 600,
            "update_countdown": 60,
        }
        
        # Merge defaults with loaded data
        result = defaults.copy()
        result.update(data)
        
        return result

    def save(self, config: Dict[str, Any]) -> None:
        """Save the tsupdate configuration to disk."""
        with open(self.config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the tsupdate configuration."""
        errors = []

        # Validate check_interval
        check_interval = config.get("check_interval")
        if check_interval is not None:
            try:
                interval = int(check_interval)
                if interval <= 0:
                    errors.append("check_interval must be a positive integer")
            except (ValueError, TypeError):
                errors.append("check_interval must be a valid integer")

        # Validate include_prereleases
        include_prereleases = config.get("include_prereleases")
        if include_prereleases is not None and not isinstance(include_prereleases, bool):
            errors.append("include_prereleases must be a boolean")

        # Validate github_url (optional)
        github_url = config.get("github_url")
        if github_url is not None:
            if not isinstance(github_url, str):
                errors.append("github_url must be a string")
            elif github_url.strip():
                # Validate URL format
                try:
                    parsed = urlparse(github_url)
                    if not parsed.scheme or not parsed.netloc:
                        errors.append("github_url must be a valid URL")
                except Exception:
                    errors.append("github_url must be a valid URL")

        # Validate max_releases
        max_releases = config.get("max_releases")
        if max_releases is not None:
            try:
                releases = int(max_releases)
                if releases <= 0:
                    errors.append("max_releases must be a positive integer")
            except (ValueError, TypeError):
                errors.append("max_releases must be a valid integer")

        # Validate persist_timeout
        persist_timeout = config.get("persist_timeout")
        if persist_timeout is not None:
            try:
                timeout = int(persist_timeout)
                if timeout <= 0:
                    errors.append("persist_timeout must be a positive integer")
            except (ValueError, TypeError):
                errors.append("persist_timeout must be a valid integer")

        # Validate update_countdown
        update_countdown = config.get("update_countdown")
        if update_countdown is not None:
            try:
                countdown = int(update_countdown)
                if countdown <= 0:
                    errors.append("update_countdown must be a positive integer")
            except (ValueError, TypeError):
                errors.append("update_countdown must be a valid integer")

        return errors

