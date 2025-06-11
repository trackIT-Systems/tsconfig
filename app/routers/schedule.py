"""Schedule configuration endpoints."""

import io
from typing import Any, Dict, List

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.configs.schedule import ScheduleConfig, ScheduleEntry

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

# Initialize config
schedule_config = ScheduleConfig()


def get_schedule_config() -> ScheduleConfig:
    """Get the current schedule configuration instance."""
    global schedule_config
    return schedule_config


def reload_schedule_config():
    """Reload the schedule configuration with updated paths."""
    global schedule_config
    schedule_config = ScheduleConfig()


class ScheduleConfigUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    force_on: bool
    button_delay: str
    schedule: List[ScheduleEntry]


@router.post("/reload")
async def reload_config():
    """Reload the schedule configuration with updated file locations."""
    try:
        reload_schedule_config()
        return {"message": "Schedule configuration reloaded successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload schedule configuration: {str(e)}"
        )


@router.get("")
async def get_schedule() -> Dict[str, Any]:
    """Get the current schedule configuration."""
    try:
        config = get_schedule_config()
        return config.load()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Schedule configuration not found")


@router.put("")
async def update_schedule(config: ScheduleConfigUpdate) -> Dict[str, Any]:
    """Update the schedule configuration."""
    # Convert to dict for validation
    config_dict = config.model_dump()
    
    schedule_cfg = get_schedule_config()

    # Validate the configuration
    errors = schedule_cfg.validate(config_dict)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Invalid schedule configuration", "errors": errors})

    # Save the configuration
    try:
        schedule_cfg.save(config_dict)
        return {"message": "Schedule configuration updated successfully", "config": config_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_schedule(config: ScheduleConfigUpdate) -> Dict[str, Any]:
    """Validate a schedule configuration without saving it."""
    config_dict = config.model_dump()
    schedule_cfg = get_schedule_config()
    errors = schedule_cfg.validate(config_dict)

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "message": "Schedule configuration is valid"}


@router.post("/download")
async def download_schedule(config: ScheduleConfigUpdate) -> StreamingResponse:
    """Download the schedule configuration as a YAML file without saving it."""
    # Convert to dict for validation
    config_dict = config.model_dump()
    
    schedule_cfg = get_schedule_config()

    # Validate the configuration
    errors = schedule_cfg.validate(config_dict)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Invalid schedule configuration", "errors": errors})

    # Generate YAML content
    yaml_content = yaml.safe_dump(config_dict, default_flow_style=False)

    # Create a file-like object from the string
    file_like = io.StringIO(yaml_content)

    return StreamingResponse(
        io.BytesIO(yaml_content.encode()),
        media_type="application/x-yaml",
        headers={"Content-Disposition": "attachment; filename=schedule.yml"},
    )
