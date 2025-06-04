"""tsOS Configuration Manager."""

import socket
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from app import __version__
from app.configs.radiotracking import (
    AnalysisEntry,
    DashboardEntry,
    MatchingEntry,
    OptionalArgumentsEntry,
    PublishEntry,
    RadioTrackingConfig,
    RTLSDREntry,
)
from app.configs.schedule import ScheduleConfig, ScheduleEntry

app = FastAPI(title="tsOS Configuration")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Initialize configs
schedule_config = ScheduleConfig()
radiotracking_config = RadioTrackingConfig()


# Pydantic models for request/response validation
class ScheduleConfigUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    force_on: bool
    button_delay: str
    schedule: List[ScheduleEntry]


class RadioTrackingConfigUpdate(BaseModel):
    """Radio tracking configuration update model."""

    optional_arguments: OptionalArgumentsEntry
    rtl_sdr: RTLSDREntry
    analysis: AnalysisEntry
    matching: MatchingEntry
    publish: PublishEntry
    dashboard: DashboardEntry


@app.get("/")
async def home(request: Request):
    """Render the main configuration page."""
    hostname = socket.gethostname()
    return templates.TemplateResponse(
        "index.html", {"request": request, "title": f"tsOS Configuration @ {hostname}", "version": __version__}
    )


# Schedule configuration endpoints
@app.get("/api/schedule")
async def get_schedule() -> Dict[str, Any]:
    """Get the current schedule configuration."""
    try:
        return schedule_config.load()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Schedule configuration not found")


@app.put("/api/schedule")
async def update_schedule(config: ScheduleConfigUpdate) -> Dict[str, Any]:
    """Update the schedule configuration."""
    # Convert to dict for validation
    config_dict = config.model_dump()

    # Validate the configuration
    errors = schedule_config.validate(config_dict)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Invalid schedule configuration", "errors": errors})

    # Save the configuration
    try:
        schedule_config.save(config_dict)
        return {"message": "Schedule configuration updated successfully", "config": config_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/schedule/validate")
async def validate_schedule(config: ScheduleConfigUpdate) -> Dict[str, Any]:
    """Validate a schedule configuration without saving it."""
    config_dict = config.model_dump()
    errors = schedule_config.validate(config_dict)

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "message": "Schedule configuration is valid"}


# Radio tracking configuration endpoints
@app.get("/api/radiotracking")
async def get_radiotracking() -> Dict[str, Any]:
    """Get the current radio tracking configuration."""
    try:
        return radiotracking_config.load()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Radio tracking configuration not found")


@app.put("/api/radiotracking")
async def update_radiotracking(config: RadioTrackingConfigUpdate) -> Dict[str, Any]:
    """Update the radio tracking configuration."""
    try:
        # Convert to dict for validation
        config_dict = {
            "optional arguments": config.optional_arguments.model_dump(),
            "rtl-sdr": config.rtl_sdr.model_dump(),
            "analysis": config.analysis.model_dump(),
            "matching": config.matching.model_dump(),
            "publish": config.publish.model_dump(),
            "dashboard": config.dashboard.model_dump(),
        }

        # Validate the configuration
        errors = radiotracking_config.validate(config_dict)
        if errors:
            raise HTTPException(
                status_code=400, detail={"message": "Invalid radio tracking configuration", "errors": errors}
            )

        # Save the configuration
        try:
            radiotracking_config.save(config_dict)
            return {"message": "Radio tracking configuration updated successfully", "config": config_dict}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # Catch any validation errors from Pydantic and return detailed info
        error_detail: Dict[str, Any] = {
            "message": "Failed to parse radio tracking configuration",
            "error": str(e),
            "type": type(e).__name__,
        }
        if isinstance(e, ValidationError):
            error_detail["validation_errors"] = [
                {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in e.errors()
            ]
        raise HTTPException(status_code=422, detail=error_detail)


@app.post("/api/radiotracking/validate")
async def validate_radiotracking(config: RadioTrackingConfigUpdate) -> Dict[str, Any]:
    """Validate a radio tracking configuration without saving it."""
    config_dict = {
        "optional arguments": config.optional_arguments.model_dump(),
        "rtl-sdr": config.rtl_sdr.model_dump(),
        "analysis": config.analysis.model_dump(),
        "matching": config.matching.model_dump(),
        "publish": config.publish.model_dump(),
        "dashboard": config.dashboard.model_dump(),
    }
    errors = radiotracking_config.validate(config_dict)

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "message": "Radio tracking configuration is valid"}
