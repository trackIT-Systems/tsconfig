"""Soundscapepipe configuration endpoints."""

import os
import json
import yaml
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import sounddevice as sd
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.configs.soundscapepipe import SoundscapepipeConfig
from app.routers.base import BaseConfigRouter


def is_system_default_device(device_name: str) -> bool:
    """Check if a device is a system default/virtual device that should be filtered out."""
    # Convert to lowercase for case-insensitive matching
    name_lower = device_name.lower()
    
    # List of system/virtual device names to exclude (using word boundaries for precision)
    exact_matches = [
        'default',
        'sysdefault', 
        'dmix',
        'pulse',
        'pipewire',
        'jack',
        'iec958',
        'spdif',
        'surround40',
        'surround51',
        'surround71',
        'front',
        'rear',
        'center_lfe',
        '/dev/dsp',  # OSS devices
        'null',
        'dummy'
    ]
    
    # Check for exact matches (device name exactly matches or starts with system name)
    for sys_device in exact_matches:
        if name_lower == sys_device or name_lower.startswith(sys_device + ' ') or name_lower.startswith(sys_device + ':'):
            return True
    
    # Special case for HDMI devices - check if it's a generic HDMI output
    if 'hdmi' in name_lower and ('hw:' in name_lower or 'alsa' in name_lower):
        # Allow specific HDMI devices with meaningful names, filter generic ones
        if name_lower.strip().endswith('hdmi') or 'hdmi 0' in name_lower or 'hdmi 1' in name_lower:
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

# Override methods to use our specific model
@router.put("")
async def update_soundscapepipe(config: SoundscapepipeConfigUpdate):
    config_dict = config.model_dump()
    return soundscapepipe_router.update_config_helper(config_dict)

@router.post("/validate")
async def validate_soundscapepipe(config: SoundscapepipeConfigUpdate):
    config_dict = config.model_dump()
    return soundscapepipe_router.validate_config_helper(config_dict)

@router.post("/download")
async def download_soundscapepipe(config: SoundscapepipeConfigUpdate):
    config_dict = config.model_dump()
    return soundscapepipe_router.download_config_helper(config_dict)

# Keep the special endpoints that are unique to soundscapepipe
@router.get("/audio-devices")
async def get_audio_devices(refresh: bool = True) -> Dict[str, Any]:
    """Get available audio input and output devices.
    
    Args:
        refresh: Whether to force refresh the device list (default: True)
    """
    try:
        # Force sounddevice to reinitialize and refresh device list if requested
        # This is necessary because sounddevice caches device info from module import
        if refresh:
            try:
                # Try multiple methods to force device list refresh
                if hasattr(sd, '_terminate') and hasattr(sd, '_initialize'):
                    # Method 1: Use private sounddevice methods (most reliable)
                    sd._terminate()
                    sd._initialize()
                elif hasattr(sd, '_get_stream_parameters'):
                    # Method 2: Try to trigger internal refresh via parameter query
                    try:
                        sd._get_stream_parameters(None, None, None, None, None, None)
                    except:
                        pass
                
                # Method 3: Clear any cached default devices to force re-detection
                if hasattr(sd.default, '_device'):
                    try:
                        # Reset default device cache
                        sd.default._device = None
                    except:
                        pass
                
                # Method 4: Force ALSA to refresh device list (Linux-specific)
                try:
                    # Run alsactl to force ALSA to scan for new devices
                    subprocess.run(['alsactl', 'scan'], 
                                 capture_output=True, 
                                 timeout=2,
                                 check=False)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    # If alsactl is not available or fails, continue
                    pass
                        
            except Exception as e:
                # If refresh fails, log it but continue with existing device list
                print(f"Warning: Could not refresh audio device list: {e}")
                pass
        
        # Query all available devices (should now include newly connected devices)
        devices = sd.query_devices()
        
        # Get default devices
        default_input = sd.default.device[0] if sd.default.device[0] is not None else None
        default_output = sd.default.device[1] if sd.default.device[1] is not None else None
        
        # Separate input and output devices
        input_devices = []
        output_devices = []
        filtered_count = 0
        
        for i, device in enumerate(devices):
            # Skip system default/virtual devices
            if is_system_default_device(device["name"]):
                filtered_count += 1
                continue
                
            # Skip devices with unrealistically high channel counts (virtual devices)
            if (device["max_input_channels"] > 32 or device["max_output_channels"] > 32):
                filtered_count += 1
                continue
            
            # Find maximum supported sample rate
            max_sample_rate = device["default_samplerate"]
            common_rates = [8000, 16000, 22050, 44100, 48000, 88200, 96000, 176400, 192000, 384000]
            
            # Test higher sample rates to find maximum
            if device["max_input_channels"] > 0:
                for rate in reversed(common_rates):  # Start from highest
                    if rate > device["default_samplerate"]:
                        try:
                            sd.check_input_settings(device=i, samplerate=rate)
                            max_sample_rate = rate
                            break
                        except:
                            continue
            
            device_info = {
                "index": i,
                "name": device["name"],
                "max_input_channels": device["max_input_channels"],
                "max_output_channels": device["max_output_channels"],
                "default_sample_rate": max_sample_rate,
                "hostapi": device["hostapi"],
                "is_default": False
            }
            
            # Add to input devices if it has input channels
            if device["max_input_channels"] > 0:
                if i == default_input:
                    device_info["is_default"] = True
                input_devices.append(device_info)
            
            # Add to output devices if it has output channels
            if device["max_output_channels"] > 0:
                device_info_output = device_info.copy()
                if i == default_output:
                    device_info_output["is_default"] = True
                output_devices.append(device_info_output)
        
        return {
            "input": input_devices,
            "output": output_devices,
            "default_input": default_input,
            "default_output": default_output,
            "refresh_attempted": refresh,
            "total_devices": len(devices),
            "filtered_devices": filtered_count,
            "input_device_count": len(input_devices),
            "output_device_count": len(output_devices)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query audio devices: {str(e)}")


@router.get("/model-files")
async def get_model_files() -> Dict[str, List[str]]:
    """Get available model files for BirdEdge and YoloBat."""
    model_files = {
        "birdedge": [],
        "yolobat": []
    }
    
    # Look for BirdEdge models (only in subfolders, not root models directory)
    birdedge_paths = [
        "/home/pi/pybirdedge/birdedge/models",
        "/opt/pybirdedge/models"
    ]
    
    for base_path in birdedge_paths:
        if os.path.exists(base_path):
            # Only search in subdirectories of the models folder
            for item in os.listdir(base_path):
                item_path = os.path.join(base_path, item)
                if os.path.isdir(item_path):
                    # Walk through this subdirectory to find .onnx files
                    for root, dirs, files in os.walk(item_path):
                        for file in files:
                            if file.endswith('.onnx'):
                                model_files["birdedge"].append(os.path.join(root, file))
    
    # Look for YoloBat models
    yolobat_paths = [
        "/home/pi/yolobat/models",
        "/opt/yolobat/models"
    ]
    
    for base_path in yolobat_paths:
        if os.path.exists(base_path):
            for root, dirs, files in os.walk(base_path):
                for file in files:
                    if file.endswith(('.xml', '.onnx')):
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
            audio_extensions = ['.wav', '.mp3', '.flac', '.ogg', '.m4a', '.WAV', '.MP3', '.FLAC', '.OGG', '.M4A']
            for ext in audio_extensions:
                for item in base_path_obj.rglob(f"*{ext}"):
                    if item.is_file():
                        files.append(str(item))
    
    return {
        "directories": sorted(directories),
        "files": sorted(files)
    }


@router.get("/species")
async def get_species() -> Dict[str, Any]:
    """Get available species information from detection models."""
    species_data = {
        "birdedge": [],
        "yolobat": []
    }
    
    # Load BirdEdge species with multi-language support
    base_paths = [
        "/home/pi/pybirdedge/birdedge/etc/",
        "/opt/pybirdedge/etc/"
    ]
    
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
                    with open(sci2i_path, 'r', encoding='utf-8') as f:
                        sci2i_data = json.load(f)
                        if isinstance(sci2i_data, dict):
                            scientific_names.update(sci2i_data.keys())
                
                # Load English to Scientific mapping
                if os.path.exists(eng2sci_path):
                    with open(eng2sci_path, 'r', encoding='utf-8') as f:
                        eng2sci_data = json.load(f)
                        if isinstance(eng2sci_data, dict):
                            eng_to_sci.update(eng2sci_data)
                            # Create reverse mapping (sci to eng)
                            for eng, sci in eng2sci_data.items():
                                if sci in scientific_names:  # Only include if species exists in model
                                    sci_to_eng[sci] = eng
                
                # Load German to Scientific mapping
                if os.path.exists(ger2sci_path):
                    with open(ger2sci_path, 'r', encoding='utf-8') as f:
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
                        "display": f"{sci_name}" + (f" ({eng_name})" if eng_name else "") + (f" / {ger_name}" if ger_name else ""),
                        "searchable": " ".join(filter(None, [sci_name, eng_name, ger_name])).lower()
                    }
                    species_list.append(species_entry)
                
                species_data["birdedge"] = species_list
                break  # Use the first path that works
                
            except (json.JSONDecodeError, IOError) as e:
                # Continue to next path if this one fails
                continue
    
    # Load YoloBat species from JSON file (now with abbreviations as keys)
    try:
        yolobat_species_path = os.path.join(os.path.dirname(__file__), "..", "data", "yolobat_species.json")
        if os.path.exists(yolobat_species_path):
            with open(yolobat_species_path, 'r', encoding='utf-8') as f:
                yolobat_species_mapping = json.load(f)
                
            # Create species list with display information
            yolobat_species_list = []
            for abbreviation, data in yolobat_species_mapping.items():
                scientific = data.get('scientific', abbreviation)
                english = data.get('english', '')
                german = data.get('german', '')
                
                species_entry = {
                    "scientific": scientific,
                    "english": english,
                    "german": german,
                    "modelLabel": abbreviation,  # Store the abbreviation for model use
                    "display": f"{scientific}" + (f" ({english})" if english else "") + (f" / {german}" if german else ""),
                    "searchable": " ".join(filter(None, [scientific, english, german, abbreviation])).lower()
                }
                yolobat_species_list.append(species_entry)
            
            species_data["yolobat"] = sorted(yolobat_species_list, key=lambda x: x["scientific"])
    except (json.JSONDecodeError, IOError) as e:
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
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse metadata.yaml: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read metadata.yaml: {str(e)}")
        
        # Extract labels from metadata
        labels = []
        if isinstance(metadata, dict):
            # Look for labels in common locations in the metadata structure
            if 'labels' in metadata:
                labels = metadata['labels']
            elif 'classes' in metadata:
                labels = metadata['classes']
            elif 'names' in metadata:
                labels = metadata['names']
            elif 'species' in metadata:
                labels = metadata['species']
            else:
                # If no common label field found, return the entire metadata for inspection
                return {
                    "labels": [],
                    "raw_metadata": metadata,
                    "message": "No standard label field found in metadata. Check raw_metadata for available fields."
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
                with open(yolobat_species_path, 'r', encoding='utf-8') as f:
                    yolobat_species_mapping = json.load(f)
        except (json.JSONDecodeError, IOError):
            # If loading fails, continue with empty mapping
            pass
        
        # Enhance labels with species information
        for label in clean_labels:
            # Look up the label directly in the mapping (labels are now keys)
            if label in yolobat_species_mapping:
                species_data = yolobat_species_mapping[label]
                scientific_name = species_data.get('scientific', label)
                enhanced_label = {
                    "scientific": scientific_name,
                    "english": species_data.get('english', ''),
                    "german": species_data.get('german', ''),
                    "display": f"{scientific_name}" + (f" ({species_data.get('english', '')})" if species_data.get('english') else "") + (f" / {species_data.get('german', '')}" if species_data.get('german') else ""),
                    "searchable": " ".join(filter(None, [scientific_name, species_data.get('english', ''), species_data.get('german', ''), label])).lower()
                }
            else:
                # No mapping found, use original label (could be unknown species or non-species like feeding-buzz)
                enhanced_label = {
                    "scientific": label,
                    "english": "",
                    "german": "",
                    "display": label,
                    "searchable": label.lower()
                }
            enhanced_labels.append(enhanced_label)
        
        return {
            "labels": clean_labels,
            "enhanced_labels": enhanced_labels,
            "model_path": model_path,
            "metadata_path": metadata_path,
            "total_labels": len(clean_labels),
            "enhanced_count": len([l for l in enhanced_labels if l["english"] or l["german"]])
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load YoloBat labels: {str(e)}")