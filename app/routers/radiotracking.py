"""Radio tracking configuration endpoints."""

import io
from configparser import ConfigParser
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.configs.radiotracking import (
    AnalysisEntry,
    DashboardEntry,
    MatchingEntry,
    OptionalArgumentsEntry,
    PublishEntry,
    RadioTrackingConfig,
    RTLSDREntry,
)

router = APIRouter(prefix="/api/radiotracking", tags=["radiotracking"])

# Initialize config
radiotracking_config = RadioTrackingConfig()


def get_radiotracking_config() -> RadioTrackingConfig:
    """Get the current radiotracking configuration instance."""
    global radiotracking_config
    return radiotracking_config


def reload_radiotracking_config():
    """Reload the radiotracking configuration with updated paths."""
    global radiotracking_config
    radiotracking_config = RadioTrackingConfig()


class RadioTrackingConfigUpdate(BaseModel):
    """Radio tracking configuration update model."""

    optional_arguments: OptionalArgumentsEntry
    rtl_sdr: RTLSDREntry
    analysis: AnalysisEntry
    matching: MatchingEntry
    publish: PublishEntry
    dashboard: DashboardEntry


@router.post("/reload")
async def reload_config():
    """Reload the radiotracking configuration with updated file locations."""
    try:
        reload_radiotracking_config()
        return {"message": "Radiotracking configuration reloaded successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload radiotracking configuration: {str(e)}"
        )


@router.get("")
async def get_radiotracking() -> Dict[str, Any]:
    """Get the current radio tracking configuration."""
    try:
        config = get_radiotracking_config()
        return config.load()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Radio tracking configuration not found")


@router.put("")
async def update_radiotracking(config: RadioTrackingConfigUpdate) -> Dict[str, Any]:
    """Update the radio tracking configuration."""
    try:
        radiotracking_cfg = get_radiotracking_config()
        
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
        errors = radiotracking_cfg.validate(config_dict)
        if errors:
            raise HTTPException(
                status_code=400, detail={"message": "Invalid radio tracking configuration", "errors": errors}
            )

        # Save the configuration
        try:
            radiotracking_cfg.save(config_dict)
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


@router.post("/validate")
async def validate_radiotracking(config: RadioTrackingConfigUpdate) -> Dict[str, Any]:
    """Validate a radio tracking configuration without saving it."""
    radiotracking_cfg = get_radiotracking_config()
    config_dict = {
        "optional arguments": config.optional_arguments.model_dump(),
        "rtl-sdr": config.rtl_sdr.model_dump(),
        "analysis": config.analysis.model_dump(),
        "matching": config.matching.model_dump(),
        "publish": config.publish.model_dump(),
        "dashboard": config.dashboard.model_dump(),
    }
    errors = radiotracking_cfg.validate(config_dict)

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "message": "Radio tracking configuration is valid"}


@router.post("/download")
async def download_radiotracking(config: RadioTrackingConfigUpdate) -> StreamingResponse:
    """Download the radio tracking configuration as an INI file without saving it."""
    try:
        radiotracking_cfg = get_radiotracking_config()
        
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
        errors = radiotracking_cfg.validate(config_dict)
        if errors:
            raise HTTPException(
                status_code=400, detail={"message": "Invalid radio tracking configuration", "errors": errors}
            )

        # Generate INI content using the same method as the save function
        parser = ConfigParser()
        for section, values in config_dict.items():
            parser[section] = {key: radiotracking_cfg._convert_to_ini_value(value) for key, value in values.items()}

        # Write to a string buffer
        ini_buffer = io.StringIO()
        parser.write(ini_buffer)
        ini_content = ini_buffer.getvalue()
        ini_buffer.close()

        return StreamingResponse(
            io.BytesIO(ini_content.encode()),
            media_type="application/x-ini",
            headers={"Content-Disposition": "attachment; filename=radiotracking.ini"},
        )
    except Exception as e:
        # Catch any validation errors from Pydantic and return detailed info
        error_detail: Dict[str, Any] = {
            "message": "Failed to generate radio tracking configuration",
            "error": str(e),
            "type": type(e).__name__,
        }
        if isinstance(e, ValidationError):
            error_detail["validation_errors"] = [
                {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in e.errors()
            ]
        raise HTTPException(status_code=422, detail=error_detail)
