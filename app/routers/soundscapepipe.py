"""Soundscapepipe configuration endpoints."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import sounddevice as sd
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.configs.soundscapepipe import SoundscapepipeConfig
from app.routers.base import BaseConfigRouter


class SoundscapepipeConfigUpdate(BaseModel):
    """Soundscapepipe configuration update model."""
    
    # Input device section
    stream_port: int = Field(..., ge=1, le=65535)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    input_device_match: Optional[str] = None
    sample_rate: Optional[int] = Field(None, gt=0)
    
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
    
    # Groups section
    groups: Optional[Dict[str, Any]] = None


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
        "/home/pi/lures",
        "/opt/lures",
        "/boot/firmware/lures"
    ]
    
    directories = []
    files = []
    
    for base_path in lure_base_paths:
        if os.path.exists(base_path):
            base_path_obj = Path(base_path)
            
            # Get all directories
            for item in base_path_obj.rglob("*"):
                if item.is_dir():
                    directories.append(str(item))
            
            # Get all audio files
            audio_extensions = ['.wav', '.mp3', '.flac', '.ogg', '.m4a']
            for ext in audio_extensions:
                for item in base_path_obj.rglob(f"*{ext}"):
                    if item.is_file():
                        files.append(str(item))
    
    return {
        "directories": sorted(directories),
        "files": sorted(files)
    } 