"""Mosquitto configuration file management."""

from pathlib import Path
from typing import Any, Dict, List

from app.configs import BaseConfig


class MosquittoConfConfig(BaseConfig):
    """Mosquitto configuration file management.

    This file contains the Mosquitto MQTT broker configuration
    and is written to /boot/firmware/mosquitto.d/server.conf.
    """

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware/mosquitto.d regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware/mosquitto.d"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/mosquitto.d/server.conf")

    def load(self) -> Dict[str, Any]:
        """Load the configuration from disk.

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

    def save(self, config: Dict[str, Any]) -> None:
        """Save the configuration to disk.

        Args:
            config: Dictionary with 'content' key containing the file content
        """
        content = config.get("content", "")

        # Write content to file
        with open(self.config_file, "w") as f:
            f.write(content)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the configuration.

        Args:
            config: Dictionary with 'content' key

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if "content" not in config:
            errors.append("Configuration must contain 'content' field")
            return errors

        content = config.get("content", "")

        if not content or not content.strip():
            errors.append("Configuration file is empty")

        return errors
