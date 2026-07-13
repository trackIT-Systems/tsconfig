"""MQTT util configuration management."""

import json
import re
from ast import literal_eval
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.configs import BaseConfig

# Matches common pytimeparse-style intervals (e.g. 5s, 1m, 2h, 1d)
_INTERVAL_PATTERN = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
    r"h|hr|hrs|hour|hours|d|day|days)?$",
    re.IGNORECASE,
)

_UNIT_TO_SECONDS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}

ALLOWED_SECTION_KEYS = frozenset({"scheduling_interval", "topic_prefix", "requires", "qos", "func"})


def _interval_to_seconds(value: str) -> Optional[float]:
    """Parse scheduling interval string to seconds, or None if invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    match = _INTERVAL_PATTERN.match(value.strip())
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    multiplier = _UNIT_TO_SECONDS.get(unit)
    if multiplier is None:
        return None
    seconds = number * multiplier
    return seconds if seconds > 0 else None


def _decode_ini_value(raw: str) -> Any:
    """Deserialize INI value using literal_eval (matches pymqttutil)."""
    return literal_eval(raw)


def _encode_ini_value(value: Any) -> str:
    """Serialize Python value to INI string (matches pymqttutil expectations)."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


class MqttUtilConfig(BaseConfig):
    """mqttutil.conf configuration management."""

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            try:
                from app.config_loader import config_loader

                config_dir = config_loader.get_config_dir()
            except ImportError:
                config_dir = Path("/boot/firmware")
        super().__init__(config_dir)

    @property
    def config_file(self) -> Path:
        return self.config_dir / "mqttutil.conf"

    def load(self) -> Dict[str, Any]:
        """Load mqttutil configuration from disk."""
        if not self.config_file.exists():
            raise FileNotFoundError(
                f"MQTT util configuration not found at {self.config_file}. Please create a configuration first."
            )

        parser = ConfigParser()
        parser.read(self.config_file)

        data: Dict[str, Any] = {}

        defaults = dict(parser.defaults())
        if defaults:
            data["DEFAULT"] = {key: _decode_ini_value(value) for key, value in defaults.items()}

        for section in parser.sections():
            data[section] = {key: _decode_ini_value(value) for key, value in parser[section].items()}

        if not data:
            raise FileNotFoundError("Configuration file is empty")

        return data

    def save(self, config: Dict[str, Any]) -> None:
        """Save mqttutil configuration to disk."""
        parser = ConfigParser()

        if "DEFAULT" in config and config["DEFAULT"]:
            for key, value in config["DEFAULT"].items():
                parser["DEFAULT"][key] = _encode_ini_value(value)

        for section, values in config.items():
            if section == "DEFAULT" or not isinstance(values, dict):
                continue
            parser[section] = {key: _encode_ini_value(value) for key, value in values.items()}

        with open(self.config_file, "w") as f:
            parser.write(f)

    def _validate_section_fields(
        self, section_name: str, fields: Dict[str, Any], errors: List[str], require_func: bool
    ) -> None:
        """Validate fields for a DEFAULT or task section."""
        if not isinstance(fields, dict):
            errors.append(f"Section '{section_name}' must be a dictionary")
            return

        unknown = set(fields.keys()) - ALLOWED_SECTION_KEYS
        if unknown:
            errors.append(f"Section '{section_name}' has unknown field(s): {', '.join(sorted(unknown))}")

        if require_func:
            func = fields.get("func")
            if not func or not isinstance(func, str) or not func.strip():
                errors.append(f"Section '{section_name}' requires a non-empty 'func' string")

        scheduling_interval = fields.get("scheduling_interval")
        if scheduling_interval is not None:
            if not isinstance(scheduling_interval, str) or _interval_to_seconds(scheduling_interval) is None:
                errors.append(
                    f"Section '{section_name}' scheduling_interval must be a valid interval "
                    "(e.g. '5s', '1m', '2h')"
                )

        topic_prefix = fields.get("topic_prefix")
        if topic_prefix is not None and not isinstance(topic_prefix, str):
            errors.append(f"Section '{section_name}' topic_prefix must be a string")

        requires = fields.get("requires")
        if requires is not None:
            if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
                errors.append(f"Section '{section_name}' requires must be a list of module name strings")

        qos = fields.get("qos")
        if qos is not None:
            if not isinstance(qos, int) or qos not in (0, 1, 2):
                errors.append(f"Section '{section_name}' qos must be an integer 0, 1, or 2")

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate mqttutil configuration."""
        errors: List[str] = []

        if not config:
            errors.append("Configuration is empty")
            return errors

        task_sections = [name for name in config.keys() if name != "DEFAULT"]
        if not task_sections:
            errors.append("At least one task section is required (besides DEFAULT)")

        if "DEFAULT" in config:
            self._validate_section_fields("DEFAULT", config["DEFAULT"], errors, require_func=False)

        for section_name in task_sections:
            section_data = config[section_name]
            if not isinstance(section_data, dict):
                errors.append(f"Task section '{section_name}' must be a dictionary")
                continue
            if not section_name.strip():
                errors.append("Task section names cannot be empty")
            self._validate_section_fields(section_name, section_data, errors, require_func=True)

        return errors
