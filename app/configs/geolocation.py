"""Geolocation configuration management."""

from pathlib import Path
from typing import Any, Dict, List

from app.configs import BaseConfig


class GeolocationConfig(BaseConfig):
    """Geolocation configuration management.

    This file contains the geolocation information in geoclue format
    and is written to /boot/firmware/geolocation.
    """

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/geolocation")

    def load(self) -> Dict[str, Any]:
        """Load the geolocation from disk.

        Returns:
            Dictionary with 'lat', 'lon', 'alt', 'accuracy' keys

        Raises:
            FileNotFoundError: If the configuration file does not exist.
        """
        if not self.config_file.exists():
            raise FileNotFoundError(
                f"Geolocation file not found at {self.config_file}. Please create a configuration first."
            )

        try:
            with open(self.config_file, "r") as f:
                lines = f.readlines()

            # Filter out comment-only lines and empty lines, and strip inline comments
            data_lines = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Strip inline comments (everything after #)
                    value = line.split("#")[0].strip()
                    if value:
                        data_lines.append(value)

            if len(data_lines) != 4:
                raise ValueError(f"Expected 4 data lines, got {len(data_lines)}")

            # Parse the four values
            lat = float(data_lines[0])
            lon = float(data_lines[1])
            alt = float(data_lines[2])
            accuracy = float(data_lines[3])

            return {"lat": lat, "lon": lon, "alt": alt, "accuracy": accuracy}

        except (IOError, OSError) as e:
            raise FileNotFoundError(f"Failed to read geolocation file: {str(e)}")
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid geolocation file format: {str(e)}")

    def save(self, config: Dict[str, Any]) -> None:
        """Save the geolocation to disk.

        Args:
            config: Dictionary with 'lat', 'lon', 'alt', 'accuracy' keys
        """
        lat = config.get("lat", 0.0)
        lon = config.get("lon", 0.0)
        alt = config.get("alt", 0.0)
        accuracy = config.get("accuracy", 0.0)

        # Write in geoclue format with comments
        content = f"""{lat} # latitude
{lon} # longitude
{alt} # altitude
{accuracy} # accuracy radius
"""

        # Ensure directory exists
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.config_file, "w") as f:
            f.write(content)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the geolocation configuration.

        Args:
            config: Dictionary with 'lat', 'lon', 'alt', 'accuracy' keys

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate required fields
        required_fields = ["lat", "lon", "alt", "accuracy"]
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")
                return errors

        # Validate latitude
        try:
            lat = float(config.get("lat", 0))
            if not (-90 <= lat <= 90):
                errors.append("Latitude must be between -90 and 90")
        except (ValueError, TypeError):
            errors.append("Latitude must be a valid number")

        # Validate longitude
        try:
            lon = float(config.get("lon", 0))
            if not (-180 <= lon <= 180):
                errors.append("Longitude must be between -180 and 180")
        except (ValueError, TypeError):
            errors.append("Longitude must be a valid number")

        # Validate altitude (just check it's a number)
        try:
            float(config.get("alt", 0))
        except (ValueError, TypeError):
            errors.append("Altitude must be a valid number")

        # Validate accuracy (just check it's a non-negative number)
        try:
            accuracy = float(config.get("accuracy", 0))
            if accuracy < 0:
                errors.append("Accuracy must be non-negative")
        except (ValueError, TypeError):
            errors.append("Accuracy must be a valid number")

        return errors
