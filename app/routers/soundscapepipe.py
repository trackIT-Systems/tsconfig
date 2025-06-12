"""Soundscapepipe configuration endpoints."""

import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import sounddevice as sd
import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.configs.soundscapepipe import (
    DetectorEntry,
    GroupEntry,
    LureTaskEntry,
    ScheduleTaskEntry,
    SoundscapepipeConfig,
)

router = APIRouter(prefix="/api/soundscapepipe", tags=["soundscapepipe"])

# Initialize config
soundscapepipe_config = SoundscapepipeConfig()


def get_soundscapepipe_config() -> SoundscapepipeConfig:
    """Get the current soundscapepipe configuration instance."""
    global soundscapepipe_config
    return soundscapepipe_config


def reload_soundscapepipe_config():
    """Reload the soundscapepipe configuration with updated paths."""
    global soundscapepipe_config
    soundscapepipe_config = SoundscapepipeConfig()


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


@router.post("/reload")
async def reload_config():
    """Reload the soundscapepipe configuration with updated file locations."""
    try:
        reload_soundscapepipe_config()
        return {"message": "Soundscapepipe configuration reloaded successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload soundscapepipe configuration: {str(e)}"
        )


@router.get("")
async def get_soundscapepipe() -> Dict[str, Any]:
    """Get the current soundscapepipe configuration."""
    try:
        config = get_soundscapepipe_config()
        return config.load()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Soundscapepipe configuration not found")


@router.put("")
async def update_soundscapepipe(config: SoundscapepipeConfigUpdate) -> Dict[str, Any]:
    """Update the soundscapepipe configuration."""
    # Convert to dict for validation
    config_dict = config.model_dump()
    
    soundscapepipe_cfg = get_soundscapepipe_config()

    # Validate the configuration
    errors = soundscapepipe_cfg.validate(config_dict)
    if errors:
        raise HTTPException(
            status_code=400, 
            detail={"message": "Invalid soundscapepipe configuration", "errors": errors}
        )

    # Save the configuration
    try:
        soundscapepipe_cfg.save(config_dict)
        return {"message": "Soundscapepipe configuration updated successfully", "config": config_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_soundscapepipe(config: SoundscapepipeConfigUpdate) -> Dict[str, Any]:
    """Validate a soundscapepipe configuration without saving it."""
    config_dict = config.model_dump()
    soundscapepipe_cfg = get_soundscapepipe_config()
    errors = soundscapepipe_cfg.validate(config_dict)

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "message": "Soundscapepipe configuration is valid"}


@router.post("/download")
async def download_soundscapepipe(config: SoundscapepipeConfigUpdate) -> StreamingResponse:
    """Download the soundscapepipe configuration as a YAML file without saving it."""
    # Convert to dict for validation
    config_dict = config.model_dump()
    
    soundscapepipe_cfg = get_soundscapepipe_config()

    # Validate the configuration
    errors = soundscapepipe_cfg.validate(config_dict)
    if errors:
        raise HTTPException(
            status_code=400, 
            detail={"message": "Invalid soundscapepipe configuration", "errors": errors}
        )

    # Generate YAML content
    yaml_content = yaml.safe_dump(config_dict, default_flow_style=False)

    # Create a file-like object from the string
    file_like = io.StringIO(yaml_content)

    return StreamingResponse(
        io.BytesIO(yaml_content.encode()),
        media_type="application/x-yaml",
        headers={"Content-Disposition": "attachment; filename=soundscapepipe.yml"},
    )


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
                device_info["is_default"] = (i == default_input)
                input_devices.append(device_info.copy())
            
            # Add to output devices if it has output channels
            if device["max_output_channels"] > 0:
                device_info["is_default"] = (i == default_output)
                output_devices.append(device_info.copy())
        
        # Get host API information for context
        hostapis = []
        for i, hostapi in enumerate(sd.query_hostapis()):
            hostapis.append({
                "index": i,
                "name": hostapi["name"],
                "default_input_device": hostapi.get("default_input_device"),
                "default_output_device": hostapi.get("default_output_device"),
                "device_count": hostapi.get("device_count", 0)
            })
        
        return {
            "input_devices": input_devices,
            "output_devices": output_devices,
            "hostapis": hostapis,
            "default_input_device": default_input,
            "default_output_device": default_output,
            "total_devices": len(devices)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query audio devices: {str(e)}"
        )


@router.get("/model-files")
async def get_model_files() -> Dict[str, List[str]]:
    """Get available model files for detectors."""
    try:
        result = {
            "birdedge": [],
            "yolobat": []
        }
        
        # BirdEdge models - look for .onnx files in /home/pi/pybirdedge/birdedge/models/
        birdedge_path = Path("/home/pi/pybirdedge/birdedge/models/")
        if birdedge_path.exists() and birdedge_path.is_dir():
            # Recursively find all .onnx files
            for onnx_file in birdedge_path.rglob("*.onnx"):
                result["birdedge"].append(str(onnx_file))
        
        # YOLOBat models - look for .xml files in /home/pi/yolobat/models/
        yolobat_path = Path("/home/pi/yolobat/models/")
        if yolobat_path.exists() and yolobat_path.is_dir():
            # Recursively find all .xml files
            for xml_file in yolobat_path.rglob("*.xml"):
                result["yolobat"].append(str(xml_file))
        
        # Sort the lists for better UX
        result["birdedge"].sort()
        result["yolobat"].sort()
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scan model files: {str(e)}"
        ) 