"""Schedule configuration endpoints."""

from typing import List, Optional

from fastapi import Query
from pydantic import BaseModel

from app.configs.schedule import ScheduleConfig, ScheduleEntry
from app.routers.base import BaseConfigRouter


class ScheduleConfigUpdate(BaseModel):
    force_on: bool
    button_delay: str
    schedule: List[ScheduleEntry]


# Create the router using the base class
schedule_router = BaseConfigRouter(ScheduleConfig, "schedule", "schedule")
router = schedule_router.router


# Override methods to use our specific model
@router.put("")
async def update_schedule(
    config: ScheduleConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump()
    return schedule_router.update_config_helper(config_dict, config_group)


@router.post("/validate")
async def validate_schedule(
    config: ScheduleConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump()
    return schedule_router.validate_config_helper(config_dict, config_group)
