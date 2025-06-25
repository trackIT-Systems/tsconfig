"""Soundscapepipe configuration endpoints."""

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import sounddevice as sd
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.configs.soundscapepipe import SoundscapepipeConfig
from app.routers.base import BaseConfigRouter


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
    maximize_confidence: Optional[bool] = None
    
    # Groups section - now properly typed
    groups: Optional[Dict[str, SpeciesGroup]] = None


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
async def get_audio_devices() -> Dict[str, Any]:
    """Get available audio input and output devices."""
    try:
        # Query all available devices
        devices = sd.query_devices()
        
        # Get default devices
        default_input = sd.default.device[0] if sd.default.device[0] is not None else None
        default_output = sd.default.device[1] if sd.default.device[1] is not None else None
        
        # Separate input and output devices
        input_devices = []
        output_devices = []
        
        for i, device in enumerate(devices):
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
            "default_output": default_output
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
    
    # Look for BirdEdge models
    birdedge_paths = [
        "/home/pi/pybirdedge/birdedge/models",
        "/opt/pybirdedge/models"
    ]
    
    for base_path in birdedge_paths:
        if os.path.exists(base_path):
            for root, dirs, files in os.walk(base_path):
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
    
    # TODO: Add YoloBat species when available
    # For now, YoloBat species list remains empty
    
    return species_data