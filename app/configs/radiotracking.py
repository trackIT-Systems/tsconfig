"""Radio tracking configuration management."""

from configparser import ConfigParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, validator

from app.configs import BaseConfig


class RTLSDREntry(BaseModel):
    """RTL-SDR device configuration."""

    device: List[str] = Field(default_factory=lambda: ["0"])
    calibration: List[float] = Field(default_factory=lambda: [0.0])
    center_freq: int = Field(..., ge=0)
    sample_rate: int = Field(..., ge=0)
    sdr_callback_length: Optional[int] = None
    gain: float = Field(..., ge=0)
    lna_gain: int = Field(..., ge=0, le=15)
    mixer_gain: int = Field(..., ge=0, le=15)
    vga_gain: int = Field(..., ge=0, le=15)
    sdr_max_restart: int = Field(..., ge=0)
    sdr_timeout_s: float = Field(..., gt=0)

    @validator("sample_rate")
    def validate_sample_rate(cls, v):
        if not ((230_000 <= v <= 300_000) or (900_000 <= v <= 3_200_000)):
            raise ValueError("Sample rate must be either between 230-300 kHz or between 900 kHz-3.2 MHz")
        return v


class AnalysisEntry(BaseModel):
    """Signal analysis configuration."""

    fft_nperseg: int = Field(..., gt=0)
    fft_window: str
    signal_threshold_dbw: float
    snr_threshold_db: float
    signal_min_duration_ms: float = Field(..., gt=0)
    signal_max_duration_ms: float = Field(..., gt=0)


class MatchingEntry(BaseModel):
    """Signal matching configuration."""

    matching_timeout_s: float = Field(..., gt=0)
    matching_time_diff_s: float = Field(..., gt=0)
    matching_bandwidth_hz: int = Field(..., gt=0)
    matching_duration_diff_ms: float = Field(..., gt=0)


class PublishEntry(BaseModel):
    """Data publishing configuration."""

    sig_stdout: bool = False
    match_stdout: bool = False
    path: str
    csv: bool = True
    export_config: bool = True
    mqtt: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = Field(..., ge=1, le=65535)


class DashboardEntry(BaseModel):
    """Dashboard configuration."""

    dashboard: bool = True
    dashboard_host: str = "localhost"
    dashboard_port: int = Field(..., ge=1, le=65535)
    dashboard_signals: int = Field(..., gt=0)


class OptionalArgumentsEntry(BaseModel):
    """Optional arguments configuration."""

    verbose: int = Field(..., ge=0)
    calibrate: bool = False
    config: str
    station: Optional[str] = None
    schedule: List[str] = Field(default_factory=list)


class RadioTrackingConfig(BaseConfig):
    """Radio tracking configuration management."""

    def __init__(self, config_dir: Path | None = None):
        super().__init__(config_dir)

    @property
    def config_file(self) -> Path:
        return self.config_dir / "radiotracking.ini"

    def _convert_value(self, value: str) -> Union[str, int, float, bool, List[str], List[float]]:
        """Convert string value from INI to appropriate Python type."""
        # Handle lists
        if value.startswith("[") and value.endswith("]"):
            # Remove brackets and split by comma
            items = value[1:-1].split(",")
            # Try to convert each item to float, if fails keep as string
            try:
                return [float(item.strip()) for item in items]
            except ValueError:
                return [item.strip().strip("'").strip('"') for item in items]

        # Handle boolean values
        if value.lower() in ("true", "false"):
            return value.lower() == "true"

        # Handle numeric values
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value

    def _convert_to_ini_value(self, value: Any) -> str:
        """Convert Python value to INI string format."""
        if isinstance(value, list):
            return f"[{', '.join(str(x) for x in value)}]"
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)

    def load(self) -> Dict[str, Any]:
        """Load the radio tracking configuration from disk.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
        """
        try:
            config = ConfigParser()
            config.read(self.config_file)

            if not config.sections():
                raise FileNotFoundError("Configuration file is empty")

            data = {}
            for section in config.sections():
                data[section] = {key: self._convert_value(value) for key, value in config[section].items()}
            return data
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Radio tracking configuration not found at {self.config_file}. Please create a configuration first."
            )

    def save(self, config: Dict[str, Any]) -> None:
        """Save the radio tracking configuration to disk."""
        parser = ConfigParser()

        # Convert dictionary to ConfigParser format
        for section, values in config.items():
            parser[section] = {key: self._convert_to_ini_value(value) for key, value in values.items()}

        # Write to file
        with open(self.config_file, "w") as f:
            parser.write(f)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the radio tracking configuration."""
        errors = []

        # Validate required sections
        required_sections = ["optional arguments", "rtl-sdr", "analysis", "matching", "publish", "dashboard"]
        for section in required_sections:
            if section not in config:
                errors.append(f"Missing required section: {section}")

        # Validate each section using Pydantic models
        try:
            if "optional arguments" in config:
                OptionalArgumentsEntry(**config["optional arguments"])
        except Exception as e:
            errors.append(f"Invalid optional arguments: {str(e)}")

        try:
            if "rtl-sdr" in config:
                RTLSDREntry(**config["rtl-sdr"])
        except Exception as e:
            errors.append(f"Invalid RTL-SDR configuration: {str(e)}")

        try:
            if "analysis" in config:
                AnalysisEntry(**config["analysis"])
        except Exception as e:
            errors.append(f"Invalid analysis configuration: {str(e)}")

        try:
            if "matching" in config:
                MatchingEntry(**config["matching"])
        except Exception as e:
            errors.append(f"Invalid matching configuration: {str(e)}")

        try:
            if "publish" in config:
                PublishEntry(**config["publish"])
        except Exception as e:
            errors.append(f"Invalid publish configuration: {str(e)}")

        try:
            if "dashboard" in config:
                DashboardEntry(**config["dashboard"])
        except Exception as e:
            errors.append(f"Invalid dashboard configuration: {str(e)}")

        return errors
