from pathlib import Path
from typing import Any, Dict, List

import yaml

CONFIG_FILE = Path("configs/schedule.yml")


def load_config() -> Dict[str, Any]:
    """Load the configuration from the YAML file."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def save_config(config: Dict[str, Any]) -> None:
    """Save the configuration to the YAML file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def validate_config(config: Dict[str, Any]) -> List[str]:
    """Validate the configuration structure."""
    errors = []

    # Check required fields
    required_fields = ["lat", "lon", "force_on", "button_delay", "schedule"]
    for field in required_fields:
        if field not in config:
            errors.append(f"Missing required field: {field}")

    # Validate coordinates
    if "lat" in config and not isinstance(config["lat"], (int, float)):
        errors.append("Latitude must be a number")
    if "lon" in config and not isinstance(config["lon"], (int, float)):
        errors.append("Longitude must be a number")

    # Validate schedule entries
    if "schedule" in config:
        if not isinstance(config["schedule"], list):
            errors.append("Schedule must be a list")
        else:
            for i, entry in enumerate(config["schedule"]):
                if not isinstance(entry, dict):
                    errors.append(f"Schedule entry {i} must be a dictionary")
                    continue

                if "name" not in entry:
                    errors.append(f"Schedule entry {i} missing 'name'")
                if "start" not in entry:
                    errors.append(f"Schedule entry {i} missing 'start'")
                if "stop" not in entry:
                    errors.append(f"Schedule entry {i} missing 'stop'")

    return errors
