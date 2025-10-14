"""Schedule configuration endpoints."""

from typing import List

from pydantic import BaseModel, Field

from app.configs.schedule import ScheduleConfig, ScheduleEntry
from app.routers.base import BaseConfigRouter


class ScheduleConfigUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    force_on: bool
    button_delay: str
    schedule: List[ScheduleEntry]


# Create the router using the base class
schedule_router = BaseConfigRouter(ScheduleConfig, "schedule", "schedule")
router = schedule_router.router


# Override methods to use our specific model
@router.put("")
async def update_schedule(config: ScheduleConfigUpdate):
    config_dict = config.model_dump()
    return schedule_router.update_config_helper(config_dict)


@router.post("/validate")
async def validate_schedule(config: ScheduleConfigUpdate):
    config_dict = config.model_dump()
    return schedule_router.validate_config_helper(config_dict)
