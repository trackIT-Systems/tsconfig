"""Configuration management for tsOS."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseConfig(ABC):
    """Base class for all configuration types."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or Path("configs")
        # Only create directory if it's the default configs directory
        # Don't automatically create system directories like /boot/firmware
        if str(self.config_dir) == "configs":
            self.config_dir.mkdir(parents=True, exist_ok=True)

    @property
    @abstractmethod
    def config_file(self) -> Path:
        """Return the path to the configuration file."""

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Load the configuration from disk."""

    @abstractmethod
    def save(self, config: dict[str, Any]) -> dict[str, Any] | None:
        """Save the configuration to disk.

        Returns:
            Optional dictionary with metadata about the save operation.
            Most implementations return None, but some may return metadata
            (e.g., cmdline returns hostname change information).
        """

    @abstractmethod
    def validate(self, config: dict[str, Any]) -> list[str]:
        """Validate the configuration."""
