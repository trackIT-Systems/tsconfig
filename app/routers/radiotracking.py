"""Radio tracking configuration endpoints."""

import io
from configparser import ConfigParser
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
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


# Override get method to support config_group
@router.get("")
async def get_radiotracking(config_group: Optional[str] = Query(None, description="Config group name for server mode")):
    return await radiotracking_router.get_config(config_group)


# Override methods to handle radiotracking's special config format
@router.put("")
async def update_radiotracking(
    config: RadioTrackingConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
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

    return radiotracking_router.update_config_helper(config_dict, config_group)


@router.post("/validate")
async def validate_radiotracking(
    config: RadioTrackingConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
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

    return radiotracking_router.validate_config_helper(config_dict, config_group)
