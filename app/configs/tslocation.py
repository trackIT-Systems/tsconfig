"""Location daemon (tslocationd) configuration management."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.configs import BaseConfig
from app.location.settings import LocationSettings


class TslocationConfig(BaseConfig):
    """tslocation.yml: the mode and intervals of the location daemon, set by the server.

    Validated with the daemon's own settings model, so tsconfig accepts exactly
    what tslocationd does. Loaded and saved as written: a missing key keeps
    meaning the daemon's default, and durations keep their spelling (`7d`).
    """

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
        return self.config_dir / "tslocation.yml"

    def load(self) -> dict[str, Any]:
        """The file's settings; empty (all defaults) if it does not exist."""
        try:
            with open(self.config_file) as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}

    def save(self, config: dict[str, Any]) -> None:
        with open(self.config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    def validate(self, config: dict[str, Any]) -> list[str]:
        try:
            LocationSettings.model_validate(config)
        except ValidationError as e:
            return [
                f"{'.'.join(str(p) for p in error['loc']) or 'tslocation.yml'}: {error['msg']}" for error in e.errors()
            ]
        return []
