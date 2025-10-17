"""Cmdline configuration management."""

from pathlib import Path
from typing import Any, Dict, List

from app.configs import BaseConfig


class CmdlineConfig(BaseConfig):
    """Cmdline.txt configuration management.

    This file contains kernel boot parameters and is written to /boot/firmware/cmdline.txt.
    It's a simple text file with no validation required.
    """

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/cmdline.txt")

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

    def save(self, config: Dict[str, Any]) -> None:
        """Save the cmdline configuration to disk.

        Args:
            config: Dictionary with 'content' key containing the file content
        """
        content = config.get("content", "")

        # Write content to file
        with open(self.config_file, "w") as f:
            f.write(content)

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
