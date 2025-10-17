"""Wireguard configuration management."""

from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, List

from app.configs import BaseConfig


class WireguardConfig(BaseConfig):
    """Wireguard configuration management.

    This file contains WireGuard VPN configuration and is written to /boot/firmware/wireguard.conf.
    It must be a valid INI file containing [Interface] and [Peer] sections.
    """

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/wireguard.conf")

    def load(self) -> Dict[str, Any]:
        """Load the wireguard configuration from disk.

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
        """Save the wireguard configuration to disk.

        Args:
            config: Dictionary with 'content' key containing the file content
        """
        content = config.get("content", "")

        # Write content to file
        with open(self.config_file, "w") as f:
            f.write(content)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the wireguard configuration.

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

        # Parse as INI file to check structure
        try:
            parser = ConfigParser()
            parser.read_string(content)

            sections = parser.sections()

            if not sections:
                errors.append("Configuration file has no sections")
                return errors

            # Check for required sections
            if "Interface" not in sections:
                errors.append("Configuration must contain [Interface] section")

            if "Peer" not in sections:
                errors.append("Configuration must contain [Peer] section")

        except Exception as e:
            errors.append(f"Invalid INI file format: {str(e)}")

        return errors
