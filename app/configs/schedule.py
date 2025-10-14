"""Schedule configuration management."""

from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel

from app.configs import BaseConfig


class ScheduleEntry(BaseModel):
    """A single schedule entry."""

    name: str
    start: str
    stop: str


class ScheduleConfig(BaseConfig):
    """Schedule configuration management."""

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
        return self.config_dir / "schedule.yml"

    def load(self) -> Dict[str, Any]:
        """Load the schedule configuration from disk.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
        """
        try:
            with open(self.config_file, "r") as f:
                data = yaml.safe_load(f)
                if data is None:
                    raise FileNotFoundError("Configuration file is empty")
                return data
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Schedule configuration not found at {self.config_file}. Please create a configuration first."
            )

    def save(self, config: Dict[str, Any]) -> None:
        """Save the schedule configuration to disk."""
        with open(self.config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the schedule configuration."""
        errors = []

        # Validate coordinates
        try:
            lat = float(config.get("lat", 0))
            lon = float(config.get("lon", 0))
            if not (-90 <= lat <= 90):
                errors.append("Latitude must be between -90 and 90")
            if not (-180 <= lon <= 180):
                errors.append("Longitude must be between -180 and 180")
        except (ValueError, TypeError):
            errors.append("Invalid coordinate values")

        # Validate button delay
        button_delay = config.get("button_delay", "")
        if not isinstance(button_delay, str) or not button_delay:
            errors.append("Button delay is required")
        elif not button_delay.count(":") == 1:
            errors.append("Button delay must be in HH:MM format")
        else:
            try:
                hours, minutes = map(int, button_delay.split(":"))
                if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                    errors.append("Button delay hours must be 0-23 and minutes 0-59")
            except ValueError:
                errors.append("Invalid button delay format")

        # Validate schedule entries
        schedule = config.get("schedule", [])
        if not isinstance(schedule, list):
            errors.append("Schedule must be a list")
        else:
            for i, entry in enumerate(schedule):
                if not isinstance(entry, dict):
                    errors.append(f"Schedule entry {i} must be a dictionary")
                    continue

                if not entry.get("name"):
                    errors.append(f"Schedule entry {i} must have a name")

                if not entry.get("start"):
                    errors.append(f"Schedule entry {i} must have a start time")

                if not entry.get("stop"):
                    errors.append(f"Schedule entry {i} must have a stop time")

        return errors
