"""Configuration management for tsOS."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class BaseConfig(ABC):
    """Base class for all configuration types."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path("configs")
        self.config_dir.mkdir(parents=True, exist_ok=True)

    @property
    @abstractmethod
    def config_file(self) -> Path:
        """Return the path to the configuration file."""
        pass

    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """Load the configuration from disk."""
        pass

    @abstractmethod
    def save(self, config: Dict[str, Any]) -> None:
        """Save the configuration to disk."""
        pass

    @abstractmethod
    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the configuration."""
        pass
