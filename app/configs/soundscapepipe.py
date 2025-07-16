"""Soundscapepipe configuration management."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, validator

from app.configs import BaseConfig


class DetectorEntry(BaseModel):
    """Detector configuration entry."""
    detection_threshold: Optional[float] = None
    class_threshold: Optional[float] = None
    model_path: Optional[str] = None
    tasks: Optional[List["ScheduleTaskEntry"]] = None
    channel_strategy: Optional[str] = "mix"


class ScheduleTaskEntry(BaseModel):
    """Schedule task configuration entry."""
    name: str
    start: str
    stop: str


class LureTaskEntry(BaseModel):
    """Lure task configuration entry."""
    species: str
    paths: List[str]
    start: str
    stop: str
    record: bool = False


class GroupEntry(BaseModel):
    """Group configuration entry."""
    ratio: Optional[float] = None
    maximize_confidence: Optional[bool] = None
    species: List[str]


class SoundscapepipeConfig(BaseConfig):
    """Soundscapepipe configuration management."""

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
        return self.config_dir / "soundscapepipe.yml"

    def load(self) -> Dict[str, Any]:
        """Load the soundscapepipe configuration from disk.

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
                f"Soundscapepipe configuration not found at {self.config_file}. Please create a configuration first."
            )

    def save(self, config: Dict[str, Any]) -> None:
        """Save the soundscapepipe configuration to disk."""
        with open(self.config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the soundscapepipe configuration."""
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

        # Validate stream port
        stream_port = config.get("stream_port")
        if stream_port is not None:
            try:
                port = int(stream_port)
                if not (1 <= port <= 65535):
                    errors.append("Stream port must be between 1 and 65535")
            except (ValueError, TypeError):
                errors.append("Stream port must be a valid integer")

        # Validate sample rate
        sample_rate = config.get("sample_rate")
        if sample_rate is not None:
            try:
                rate = int(sample_rate)
                if rate <= 0:
                    errors.append("Sample rate must be positive")
            except (ValueError, TypeError):
                errors.append("Sample rate must be a valid integer")

        # Validate input length
        input_length_s = config.get("input_length_s")
        if input_length_s is not None:
            try:
                length = float(input_length_s)
                if not (0.1 <= length <= 1.0):
                    errors.append("Input length must be between 0.1 and 1.0 seconds")
            except (ValueError, TypeError):
                errors.append("Input length must be a valid number")

        # Validate channels
        channels = config.get("channels")
        if channels is not None:
            try:
                channel_count = int(channels)
                if channel_count not in [1, 2]:
                    errors.append("Channels must be 1 or 2")
            except (ValueError, TypeError):
                errors.append("Channels must be a valid integer")

        # Validate input length
        input_length_s = config.get("input_length_s")
        if input_length_s is not None:
            try:
                length = float(input_length_s)
                if not (0.1 <= length <= 1.0):
                    errors.append("Input length must be between 0.1 and 1.0 seconds")
            except (ValueError, TypeError):
                errors.append("Input length must be a valid number")

        # Validate channels
        channels = config.get("channels")
        if channels is not None:
            try:
                channel_count = int(channels)
                if channel_count not in [1, 2]:
                    errors.append("Channels must be 1 or 2")
            except (ValueError, TypeError):
                errors.append("Channels must be a valid integer")

        # Validate detectors
        detectors = config.get("detectors", {})
        if not isinstance(detectors, dict):
            errors.append("Detectors must be a dictionary")
        else:
            for detector_name, detector_config in detectors.items():
                if not isinstance(detector_config, dict):
                    errors.append(f"Detector '{detector_name}' configuration must be a dictionary")
                    continue

                # Validate detector thresholds
                for threshold_key in ["detection_threshold", "class_threshold"]:
                    threshold = detector_config.get(threshold_key)
                    if threshold is not None:
                        try:
                            threshold_val = float(threshold)
                            if not (0.0 <= threshold_val <= 1.0):
                                errors.append(f"Detector '{detector_name}' {threshold_key} must be between 0.0 and 1.0")
                        except (ValueError, TypeError):
                            errors.append(f"Detector '{detector_name}' {threshold_key} must be a valid number")

                # Validate channel_strategy for detectors that support it (birdedge and yolobat)
                if detector_name in ["birdedge", "yolobat"]:
                    channel_strategy = detector_config.get("channel_strategy")
                    if channel_strategy is not None:
                        if isinstance(channel_strategy, str):
                            # Define valid strategies for each detector
                            if detector_name == "yolobat":
                                valid_strategies = ["mix", "all"]
                            else:  # birdedge
                                valid_strategies = ["mix", "all", "or", "and"]
                            
                            if channel_strategy not in valid_strategies:
                                # Try to validate as string representation of a channel number
                                try:
                                    channel_num = int(channel_strategy)
                                    if channel_num < 0:
                                        errors.append(f"Detector '{detector_name}' channel_strategy must be non-negative if specified as a channel number")
                                except (ValueError, TypeError):
                                    if detector_name == "yolobat":
                                        errors.append(f"Detector '{detector_name}' channel_strategy must be 'mix', 'all', or a valid channel number")
                                    else:  # birdedge
                                        errors.append(f"Detector '{detector_name}' channel_strategy must be 'mix', 'all', 'or', 'and', or a valid channel number")
                        else:
                            # Try to validate as integer channel number
                            try:
                                channel_num = int(channel_strategy)
                                if channel_num < 0:
                                    errors.append(f"Detector '{detector_name}' channel_strategy must be non-negative if specified as a channel number")
                            except (ValueError, TypeError):
                                if detector_name == "yolobat":
                                    errors.append(f"Detector '{detector_name}' channel_strategy must be 'mix', 'all', or a valid channel number")
                                else:  # birdedge
                                    errors.append(f"Detector '{detector_name}' channel_strategy must be 'mix', 'all', 'or', 'and', or a valid channel number")

                # Validate detector tasks (applicable to all detectors except schedule)
                if detector_name != "schedule":
                    tasks = detector_config.get("tasks", [])
                    if tasks is not None:
                        if not isinstance(tasks, list):
                            errors.append(f"Detector '{detector_name}' tasks must be a list")
                        else:
                            for i, task in enumerate(tasks):
                                if not isinstance(task, dict):
                                    errors.append(f"Detector '{detector_name}' task {i} must be a dictionary")
                                    continue
                                
                                required_fields = ["name", "start", "stop"]
                                for field in required_fields:
                                    if not task.get(field):
                                        errors.append(f"Detector '{detector_name}' task {i} must have a '{field}' field")

                # Validate schedule tasks
                if detector_name == "schedule":
                    tasks = detector_config.get("tasks", [])
                    if not isinstance(tasks, list):
                        errors.append("Schedule tasks must be a list")
                    else:
                        for i, task in enumerate(tasks):
                            if not isinstance(task, dict):
                                errors.append(f"Schedule task {i} must be a dictionary")
                                continue
                            
                            required_fields = ["name", "start", "stop"]
                            for field in required_fields:
                                if not task.get(field):
                                    errors.append(f"Schedule task {i} must have a '{field}' field")

        # Validate speaker enable pin
        speaker_pin = config.get("speaker_enable_pin")
        if speaker_pin is not None:
            try:
                pin = int(speaker_pin)
                if pin < 0:
                    errors.append("Speaker enable pin must be non-negative")
            except (ValueError, TypeError):
                errors.append("Speaker enable pin must be a valid integer")

        # Validate highpass frequency
        highpass_freq = config.get("highpass_freq")
        if highpass_freq is not None:
            try:
                freq = float(highpass_freq)
                if freq < 0:
                    errors.append("Highpass frequency must be non-negative")
            except (ValueError, TypeError):
                errors.append("Highpass frequency must be a valid number")

        # Validate lure tasks
        lure = config.get("lure", {})
        if lure:
            tasks = lure.get("tasks", [])
            if not isinstance(tasks, list):
                errors.append("Lure tasks must be a list")
            else:
                for i, task in enumerate(tasks):
                    if not isinstance(task, dict):
                        errors.append(f"Lure task {i} must be a dictionary")
                        continue
                    
                    required_fields = ["species", "paths", "start", "stop"]
                    for field in required_fields:
                        if not task.get(field):
                            errors.append(f"Lure task {i} must have a '{field}' field")
                    
                    # Validate paths is a list
                    paths = task.get("paths", [])
                    if not isinstance(paths, list):
                        errors.append(f"Lure task {i} paths must be a list")
                    
                    # Validate record is a boolean
                    record = task.get("record", False)
                    if record is not None and not isinstance(record, bool):
                        errors.append(f"Lure task {i} record must be a boolean (true/false)")

        # Validate recording settings
        ratio = config.get("ratio")
        if ratio is not None:
            try:
                ratio_val = float(ratio)
                if not (0.0 <= ratio_val <= 1.0):
                    errors.append("Recording ratio must be between 0.0 and 1.0")
            except (ValueError, TypeError):
                errors.append("Recording ratio must be a valid number")

        length_s = config.get("length_s")
        if length_s is not None:
            try:
                length_val = int(length_s)
                if length_val <= 0:
                    errors.append("Recording length must be positive")
            except (ValueError, TypeError):
                errors.append("Recording length must be a valid integer")

        # Validate groups
        groups = config.get("groups", {})
        if not isinstance(groups, dict):
            errors.append("Groups must be a dictionary")
        else:
            for group_name, group_config in groups.items():
                if not isinstance(group_config, dict):
                    errors.append(f"Group '{group_name}' configuration must be a dictionary")
                    continue

                # Validate species list
                species = group_config.get("species", [])
                if not isinstance(species, list):
                    errors.append(f"Group '{group_name}' species must be a list")

                # Validate ratio if present
                group_ratio = group_config.get("ratio")
                if group_ratio is not None:
                    try:
                        ratio_val = float(group_ratio)
                        if not (0.0 <= ratio_val <= 1.0):
                            errors.append(f"Group '{group_name}' ratio must be between 0.0 and 1.0")
                    except (ValueError, TypeError):
                        errors.append(f"Group '{group_name}' ratio must be a valid number")

        return errors 