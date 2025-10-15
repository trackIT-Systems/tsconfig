"""Soundscapepipe configuration endpoints."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Try to import sounddevice for hardware audio device detection
# This is optional and only needed for tracker mode with hardware validation
try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    sd = None

import yaml
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from app.config_loader import config_loader
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.routers.base import BaseConfigRouter


def is_system_default_device(device_name: str) -> bool:
    """Check if a device is a system default/virtual device that should be filtered out."""
    # Convert to lowercase for case-insensitive matching
    name_lower = device_name.lower()

    # List of system/virtual device names to exclude (using word boundaries for precision)
    exact_matches = [
        "default",
        "sysdefault",
        "dmix",
        "pulse",
        "pipewire",
        "jack",
        "iec958",
        "spdif",
        "surround40",
        "surround51",
        "surround71",
        "front",
        "rear",
        "center_lfe",
        "/dev/dsp",  # OSS devices
        "null",
        "dummy",
    ]

    # Check for exact matches (device name exactly matches or starts with system name)
    for sys_device in exact_matches:
        if (
            name_lower == sys_device
            or name_lower.startswith(sys_device + " ")
            or name_lower.startswith(sys_device + ":")
        ):
            return True

    # Special case for HDMI devices - check if it's a generic HDMI output
    if "hdmi" in name_lower and ("hw:" in name_lower or "alsa" in name_lower):
        # Allow specific HDMI devices with meaningful names, filter generic ones
        if name_lower.strip().endswith("hdmi") or "hdmi 0" in name_lower or "hdmi 1" in name_lower:
            return True

    return False


class SpeciesGroup(BaseModel):
    """Species group configuration model."""

    ratio: Optional[float] = Field(None, ge=0.0, le=1.0)
    maximize_confidence: Optional[bool] = None
    length_s: Optional[int] = Field(None, gt=0)
    species: List[str] = Field(default_factory=list)


class SoundscapepipeConfigUpdate(BaseModel):
    """Soundscapepipe configuration update model."""

    # Input device section
    stream_port: int = Field(..., ge=1, le=65535)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    input_device_match: Optional[str] = None
    sample_rate: Optional[int] = Field(None, gt=0)
    input_length_s: Optional[float] = Field(None, gt=0)
    channels: Optional[int] = Field(None, gt=0)

    # Detectors section
    detectors: Dict[str, Any] = Field(default_factory=dict)

    # Output device section
    output_device_match: Optional[str] = None
    speaker_enable_pin: Optional[int] = Field(None, ge=0)
    highpass_freq: Optional[float] = Field(None, ge=0)

    # Lure section
    lure: Optional[Dict[str, Any]] = None

    # Recording section
    ratio: Optional[float] = Field(None, ge=0.0, le=1.0)
    length_s: Optional[int] = Field(None, gt=0)
    soundfile_limit: Optional[int] = Field(None, gt=0)
    soundfile_format: Optional[str] = None
    maximize_confidence: Optional[bool] = None

    # Groups section - now properly typed
    groups: Optional[Dict[str, SpeciesGroup]] = None

    # Optional arguments section
    disk_reserve_mb: Optional[int] = Field(None, ge=512)


# Create the router using the base class
soundscapepipe_router = BaseConfigRouter(SoundscapepipeConfig, "soundscapepipe", "soundscapepipe")
router = soundscapepipe_router.router


# Override get method to support config_group
@router.get("")
async def get_soundscapepipe(
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    return await soundscapepipe_router.get_config(config_group)


# Override methods to use our specific model
@router.put("")
async def update_soundscapepipe(
    config: SoundscapepipeConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump()
    return soundscapepipe_router.update_config_helper(config_dict, config_group)


@router.post("/validate")
async def validate_soundscapepipe(
    config: SoundscapepipeConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump()
    return soundscapepipe_router.validate_config_helper(config_dict, config_group)


# Keep the special endpoints that are unique to soundscapepipe
def _load_audio_devices_config() -> Dict[str, Any]:
    """Load audio devices configuration from YAML file."""
    config_file = Path(__file__).parent.parent / "configs" / "audio_devices.yml"
    try:
        with open(config_file, "r") as f:
            return yaml.safe_load(f) or {"input": [], "output": []}
    except FileNotFoundError:
        return {"input": [], "output": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load audio devices config: {str(e)}")


def _match_device_name(config_name: str, actual_name: str) -> bool:
    """Check if a configured device name matches an actual device name.

    Matches if the config name appears at the start of the actual device name.
    This allows matching "trackIT Analog Frontend" with "trackIT Analog Frontend: Audio (hw:2,0)"
    """
    return actual_name.startswith(config_name)


@router.get("/audio-devices")
async def get_audio_devices(refresh: bool = True) -> Dict[str, Any]:
    """Get available audio input and output devices.

    Args:
        refresh: Whether to force refresh the device list (default: True)

    In tracker mode (default): Returns devices from config file that are present on the system.
    In server mode: Returns all devices from config file without validation.
    """
    # Load configuration file
    devices_config = _load_audio_devices_config()

    # In server mode or when sounddevice is not available, return config as-is without hardware validation
    if config_loader.is_server_mode() or not SOUNDDEVICE_AVAILABLE:
        input_devices = []
        output_devices = []
        default_input = None
        default_output = None

        # Process input devices
        for idx, device in enumerate(devices_config.get("input", [])):
            device_with_index = device.copy()
            device_with_index["index"] = idx
            if device.get("is_default", False):
                default_input = idx
            input_devices.append(device_with_index)

        # Process output devices
        for idx, device in enumerate(devices_config.get("output", [])):
            device_with_index = device.copy()
            device_with_index["index"] = idx
            if device.get("is_default", False):
                default_output = idx
            output_devices.append(device_with_index)

        return {
            "input": input_devices,
            "output": output_devices,
            "default_input": default_input,
            "default_output": default_output,
            "refresh_attempted": False,
            "total_devices": len(input_devices) + len(output_devices),
            "filtered_devices": 0,
            "input_device_count": len(input_devices),
            "output_device_count": len(output_devices),
            "server_mode": config_loader.is_server_mode(),
            "sounddevice_available": SOUNDDEVICE_AVAILABLE,
        }

    # Tracker mode: Validate config against actual hardware
    try:
        # Force sounddevice to reinitialize and refresh device list if requested
        if refresh:
            try:
                if hasattr(sd, "_terminate") and hasattr(sd, "_initialize"):
                    sd._terminate()
                    sd._initialize()
                if hasattr(sd.default, "_device"):
                    try:
                        sd.default._device = None
                    except Exception:
                        pass
                try:
                    subprocess.run(["alsactl", "scan"], capture_output=True, timeout=2, check=False)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass
            except Exception as e:
                print(f"Warning: Could not refresh audio device list: {e}")

        # Query all available devices from hardware
        hardware_devices = sd.query_devices()

        # Get default devices from hardware
        hw_default_input = sd.default.device[0] if sd.default.device[0] is not None else None
        hw_default_output = sd.default.device[1] if sd.default.device[1] is not None else None

        # Filter configured devices to only include those present on hardware
        input_devices = []
        output_devices = []
        default_input = None
        default_output = None
        config_filtered = 0

        # Process input devices from config
        for config_device in devices_config.get("input", []):
            config_name = config_device.get("name", "")

            # Find matching hardware device by name only
            matched_hw_index = None
            for i, hw_dev in enumerate(hardware_devices):
                if _match_device_name(config_name, hw_dev["name"]):
                    matched_hw_index = i
                    break

            if matched_hw_index is not None:
                # Device exists on hardware, add it with actual hardware index
                device_info = {
                    "index": matched_hw_index,
                    "name": config_name,  # Use config name for consistency
                    "max_input_channels": config_device.get("max_input_channels"),
                    "default_sample_rate": config_device.get("default_sample_rate"),
                    "is_default": matched_hw_index == hw_default_input or config_device.get("is_default", False),
                }
                if device_info["is_default"]:
                    default_input = matched_hw_index
                input_devices.append(device_info)
            else:
                config_filtered += 1

        # Process output devices from config
        for config_device in devices_config.get("output", []):
            config_name = config_device.get("name", "")

            # Find matching hardware device by name only
            matched_hw_index = None
            for i, hw_dev in enumerate(hardware_devices):
                if _match_device_name(config_name, hw_dev["name"]):
                    matched_hw_index = i
                    break

            if matched_hw_index is not None:
                # Device exists on hardware, add it with actual hardware index
                device_info = {
                    "index": matched_hw_index,
                    "name": config_name,  # Use config name for consistency
                    "max_output_channels": config_device.get("max_output_channels"),
                    "default_sample_rate": config_device.get("default_sample_rate"),
                    "is_default": matched_hw_index == hw_default_output or config_device.get("is_default", False),
                }
                if device_info["is_default"]:
                    default_output = matched_hw_index
                output_devices.append(device_info)
            else:
                config_filtered += 1

        return {
            "input": input_devices,
            "output": output_devices,
            "default_input": default_input,
            "default_output": default_output,
            "refresh_attempted": refresh,
            "total_devices": len(input_devices) + len(output_devices),
            "filtered_devices": config_filtered,
            "input_device_count": len(input_devices),
            "output_device_count": len(output_devices),
            "server_mode": False,
            "config_validated": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query audio devices: {str(e)}")


@router.get("/model-files")
async def get_model_files() -> Dict[str, List[str]]:
    """Get available model files for BirdEdge and YoloBat."""
    model_files = {"birdedge": [], "yolobat": []}

    # Look for BirdEdge models (only in subfolders, not root models directory)
    birdedge_paths = ["/home/pi/pybirdedge/birdedge/models", "/opt/pybirdedge/models"]

    for base_path in birdedge_paths:
        if os.path.exists(base_path):
            # Only search in subdirectories of the models folder
            for item in os.listdir(base_path):
                item_path = os.path.join(base_path, item)
                if os.path.isdir(item_path):
                    # Walk through this subdirectory to find .onnx files
                    for root, dirs, files in os.walk(item_path):
                        for file in files:
                            if file.endswith(".onnx"):
                                model_files["birdedge"].append(os.path.join(root, file))

    # Look for YoloBat models
    yolobat_paths = ["/home/pi/yolobat/models", "/opt/yolobat/models"]

    for base_path in yolobat_paths:
        if os.path.exists(base_path):
            for root, dirs, files in os.walk(base_path):
                for file in files:
                    if file.endswith((".xml", ".onnx")):
                        model_files["yolobat"].append(os.path.join(root, file))

    return model_files


@router.get("/lure-files")
async def get_lure_files() -> Dict[str, Any]:
    """Get available lure files and directories."""
    lure_base_paths = [
        "/data/lure",
        "/home/pi/lure",
    ]

    directories = []
    files = []

    for base_path in lure_base_paths:
        if os.path.exists(base_path):
            base_path_obj = Path(base_path)

            # Get all directories (including the base path itself)
            directories.append(str(base_path_obj))
            for item in base_path_obj.rglob("*"):
                if item.is_dir():
                    directories.append(str(item))

            # Get all audio files
            audio_extensions = [".wav", ".mp3", ".flac", ".ogg", ".m4a", ".WAV", ".MP3", ".FLAC", ".OGG", ".M4A"]
            for ext in audio_extensions:
                for item in base_path_obj.rglob(f"*{ext}"):
                    if item.is_file():
                        files.append(str(item))

    return {"directories": sorted(directories), "files": sorted(files)}


@router.get("/species")
async def get_species() -> Dict[str, Any]:
    """Get available species information from detection models."""
    species_data = {"birdedge": [], "yolobat": []}

    # Load BirdEdge species with multi-language support
    base_paths = ["/home/pi/pybirdedge/birdedge/etc/", "/opt/pybirdedge/etc/"]

    for base_path in base_paths:
        if os.path.exists(base_path):
            try:
                # Load scientific names from sci2i.json
                sci2i_path = os.path.join(base_path, "sci2i.json")
                eng2sci_path = os.path.join(base_path, "eng2sci.json")
                ger2sci_path = os.path.join(base_path, "ger2sci.json")

                scientific_names = set()
                eng_to_sci = {}
                ger_to_sci = {}
                sci_to_eng = {}
                sci_to_ger = {}

                # Load scientific names
                if os.path.exists(sci2i_path):
                    with open(sci2i_path, "r", encoding="utf-8") as f:
                        sci2i_data = json.load(f)
                        if isinstance(sci2i_data, dict):
                            scientific_names.update(sci2i_data.keys())

                # Load English to Scientific mapping
                if os.path.exists(eng2sci_path):
                    with open(eng2sci_path, "r", encoding="utf-8") as f:
                        eng2sci_data = json.load(f)
                        if isinstance(eng2sci_data, dict):
                            eng_to_sci.update(eng2sci_data)
                            # Create reverse mapping (sci to eng)
                            for eng, sci in eng2sci_data.items():
                                if sci in scientific_names:  # Only include if species exists in model
                                    sci_to_eng[sci] = eng

                # Load German to Scientific mapping
                if os.path.exists(ger2sci_path):
                    with open(ger2sci_path, "r", encoding="utf-8") as f:
                        ger2sci_data = json.load(f)
                        if isinstance(ger2sci_data, dict):
                            ger_to_sci.update(ger2sci_data)
                            # Create reverse mapping (sci to ger)
                            for ger, sci in ger2sci_data.items():
                                if sci in scientific_names:  # Only include if species exists in model
                                    sci_to_ger[sci] = ger

                # Create comprehensive species list with all name variants
                species_list = []
                for sci_name in sorted(scientific_names):
                    eng_name = sci_to_eng.get(sci_name, "")
                    ger_name = sci_to_ger.get(sci_name, "")

                    species_entry = {
                        "scientific": sci_name,
                        "english": eng_name,
                        "german": ger_name,
                        "display": f"{sci_name}"
                        + (f" ({eng_name})" if eng_name else "")
                        + (f" / {ger_name}" if ger_name else ""),
                        "searchable": " ".join(filter(None, [sci_name, eng_name, ger_name])).lower(),
                    }
                    species_list.append(species_entry)

                species_data["birdedge"] = species_list
                break  # Use the first path that works

            except (json.JSONDecodeError, IOError):
                # Continue to next path if this one fails
                continue

    # Load YoloBat species from JSON file (now with abbreviations as keys)
    try:
        yolobat_species_path = os.path.join(os.path.dirname(__file__), "..", "data", "yolobat_species.json")
        if os.path.exists(yolobat_species_path):
            with open(yolobat_species_path, "r", encoding="utf-8") as f:
                yolobat_species_mapping = json.load(f)

            # Create species list with display information
            yolobat_species_list = []
            for abbreviation, data in yolobat_species_mapping.items():
                scientific = data.get("scientific", abbreviation)
                english = data.get("english", "")
                german = data.get("german", "")

                species_entry = {
                    "scientific": scientific,
                    "english": english,
                    "german": german,
                    "modelLabel": abbreviation,  # Store the abbreviation for model use
                    "display": f"{scientific}"
                    + (f" ({english})" if english else "")
                    + (f" / {german}" if german else ""),
                    "searchable": " ".join(filter(None, [scientific, english, german, abbreviation])).lower(),
                }
                yolobat_species_list.append(species_entry)

            species_data["yolobat"] = sorted(yolobat_species_list, key=lambda x: x["scientific"])
    except (json.JSONDecodeError, IOError):
        # If loading fails, keep yolobat empty
        species_data["yolobat"] = []

    return species_data


@router.get("/yolobat-labels")
async def get_yolobat_labels(model_path: str) -> Dict[str, Any]:
    """Get available labels for a specific YoloBat model from its metadata.yaml file."""
    try:
        if not model_path:
            raise HTTPException(status_code=400, detail="Model path is required")

        # Ensure the model path exists
        if not os.path.exists(model_path):
            raise HTTPException(status_code=404, detail=f"Model file not found: {model_path}")

        # Get the directory containing the model file
        model_dir = os.path.dirname(model_path)
        metadata_path = os.path.join(model_dir, "metadata.yaml")

        # Check if metadata.yaml exists
        if not os.path.exists(metadata_path):
            raise HTTPException(status_code=404, detail=f"Metadata file not found: {metadata_path}")

        # Load and parse the metadata.yaml file
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse metadata.yaml: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read metadata.yaml: {str(e)}")

        # Extract labels from metadata
        labels = []
        if isinstance(metadata, dict):
            # Look for labels in common locations in the metadata structure
            if "labels" in metadata:
                labels = metadata["labels"]
            elif "classes" in metadata:
                labels = metadata["classes"]
            elif "names" in metadata:
                labels = metadata["names"]
            elif "species" in metadata:
                labels = metadata["species"]
            else:
                # If no common label field found, return the entire metadata for inspection
                return {
                    "labels": [],
                    "raw_metadata": metadata,
                    "message": "No standard label field found in metadata. Check raw_metadata for available fields.",
                }

        # Ensure labels is a list
        if not isinstance(labels, list):
            if isinstance(labels, dict):
                # If labels is a dict, try to extract values or keys
                if all(isinstance(v, str) for v in labels.values()):
                    labels = list(labels.values())
                elif all(isinstance(k, (str, int)) for k in labels.keys()):
                    labels = list(labels.keys())
                else:
                    labels = []
            else:
                labels = []

        # Convert to list of strings and filter out empty values
        clean_labels = []
        for label in labels:
            if isinstance(label, str) and label.strip():
                clean_labels.append(label.strip())
            elif isinstance(label, (int, float)):
                clean_labels.append(str(label))

        # Load YoloBat species mapping to enhance labels with common names
        enhanced_labels = []
        yolobat_species_mapping = {}

        try:
            # Load species mapping (abbreviation -> species data)
            yolobat_species_path = os.path.join(os.path.dirname(__file__), "..", "data", "yolobat_species.json")
            if os.path.exists(yolobat_species_path):
                with open(yolobat_species_path, "r", encoding="utf-8") as f:
                    yolobat_species_mapping = json.load(f)
        except (json.JSONDecodeError, IOError):
            # If loading fails, continue with empty mapping
            pass

        # Enhance labels with species information
        for label in clean_labels:
            # Look up the label directly in the mapping (labels are now keys)
            if label in yolobat_species_mapping:
                species_data = yolobat_species_mapping[label]
                scientific_name = species_data.get("scientific", label)
                enhanced_label = {
                    "scientific": scientific_name,
                    "english": species_data.get("english", ""),
                    "german": species_data.get("german", ""),
                    "display": f"{scientific_name}"
                    + (f" ({species_data.get('english', '')})" if species_data.get("english") else "")
                    + (f" / {species_data.get('german', '')}" if species_data.get("german") else ""),
                    "searchable": " ".join(
                        filter(
                            None,
                            [scientific_name, species_data.get("english", ""), species_data.get("german", ""), label],
                        )
                    ).lower(),
                }
            else:
                # No mapping found, use original label (could be unknown species or non-species like feeding-buzz)
                enhanced_label = {
                    "scientific": label,
                    "english": "",
                    "german": "",
                    "display": label,
                    "searchable": label.lower(),
                }
            enhanced_labels.append(enhanced_label)

        return {
            "labels": clean_labels,
            "enhanced_labels": enhanced_labels,
            "model_path": model_path,
            "metadata_path": metadata_path,
            "total_labels": len(clean_labels),
            "enhanced_count": len([label for label in enhanced_labels if label["english"] or label["german"]]),
        }

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load YoloBat labels: {str(e)}")
