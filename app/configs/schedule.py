"""Schedule configuration management."""

from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel

from app.configs import BaseConfig


def _validate_hh_mm(value: str, field_name: str) -> List[str]:
    """Validate an HH:MM duration string."""
    errors = []
    if not isinstance(value, str) or not value:
        errors.append(f"{field_name} is required")
    elif value.count(":") != 1:
        errors.append(f"{field_name} must be in HH:MM format")
    else:
        try:
            hours, minutes = map(int, value.split(":"))
            if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                errors.append(f"{field_name} hours must be 0-23 and minutes 0-59")
        except ValueError:
            errors.append(f"Invalid {field_name} format")
    return errors


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

        errors.extend(_validate_hh_mm(config.get("button_delay", ""), "Button delay"))

        recovery_interval = config.get("recovery_interval", "00:00")
        if recovery_interval and recovery_interval != "00:00":
            errors.extend(_validate_hh_mm(recovery_interval, "Recovery interval"))

        recovery_guard = config.get("recovery_guard", "00:00")
        if recovery_guard:
            errors.extend(_validate_hh_mm(recovery_guard, "Guard interval"))

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
