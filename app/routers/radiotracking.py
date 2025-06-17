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
from app.routers.base import BaseConfigRouter


class RadioTrackingConfigUpdate(BaseModel):
    """Radio tracking configuration update model."""

    optional_arguments: OptionalArgumentsEntry
    rtl_sdr: RTLSDREntry
    analysis: AnalysisEntry
    matching: MatchingEntry
    publish: PublishEntry
    dashboard: DashboardEntry


# Create the router using the base class
radiotracking_router = BaseConfigRouter(RadioTrackingConfig, "radiotracking", "radiotracking")
router = radiotracking_router.router

# Override methods to handle radiotracking's special config format
@router.put("")
async def update_radiotracking(config: RadioTrackingConfigUpdate):
    # Convert to the special format expected by radiotracking
    config_dict = {
        "optional arguments": config.optional_arguments.model_dump(),
        "rtl-sdr": config.rtl_sdr.model_dump(),
        "analysis": config.analysis.model_dump(),
        "matching": config.matching.model_dump(),
        "publish": config.publish.model_dump(),
        "dashboard": config.dashboard.model_dump(),
    }
    
    # Create a temporary config object with the formatted data
    class TempConfig:
        def model_dump(self):
            return config_dict
    
    return radiotracking_router.update_config_helper(config_dict)

@router.post("/validate")
async def validate_radiotracking(config: RadioTrackingConfigUpdate):
    # Convert to the special format expected by radiotracking
    config_dict = {
        "optional arguments": config.optional_arguments.model_dump(),
        "rtl-sdr": config.rtl_sdr.model_dump(),
        "analysis": config.analysis.model_dump(),
        "matching": config.matching.model_dump(),
        "publish": config.publish.model_dump(),
        "dashboard": config.dashboard.model_dump(),
    }
    
    class TempConfig:
        def model_dump(self):
            return config_dict
    
    return radiotracking_router.validate_config_helper(config_dict)

@router.post("/download")
async def download_radiotracking(config: RadioTrackingConfigUpdate):
    # Convert to the special format expected by radiotracking
    config_dict = {
        "optional arguments": config.optional_arguments.model_dump(),
        "rtl-sdr": config.rtl_sdr.model_dump(),
        "analysis": config.analysis.model_dump(),
        "matching": config.matching.model_dump(),
        "publish": config.publish.model_dump(),
        "dashboard": config.dashboard.model_dump(),
    }
    
    class TempConfig:
        def model_dump(self):
            return config_dict
    
    return radiotracking_router.download_config_helper(config_dict)
