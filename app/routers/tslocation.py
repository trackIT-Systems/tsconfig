"""Location daemon (tslocationd) configuration endpoints."""

from typing import Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict

from app.configs.tslocation import TslocationConfig
from app.location.settings import LocationSettings
from app.routers.base import BaseConfigRouter

# A duration is seconds or a string such as "60s", "1h", "7d"
Duration = float | str


class TslocationConfigUpdate(BaseModel):
    """tslocation.yml as the settings page sends it: a key that is left out takes the daemon's default.

    Only the shape is checked here; the values are validated with the daemon's own
    settings model (TslocationConfig.validate), so the page accepts what tslocationd does.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "stationary", "mobile"] | None = None
    receiver: Literal["modem"] | None = None
    fix_duration: Duration | None = None
    fix_timeout: Duration | None = None
    fix_max_age: Duration | None = None
    check_interval: Duration | None = None
    threshold: float | None = None
    recheck_interval: Duration | None = None
    settle_count: int | None = None
    track_interval: Duration | None = None
    track_distance: float | None = None
    time_max_age: Duration | None = None


tslocation_router = BaseConfigRouter(TslocationConfig, "tslocation", "tslocation")
router = tslocation_router.router


@router.get("/defaults", summary="The daemon's defaults (durations in seconds, distances in metres)")
async def tslocation_defaults():
    return LocationSettings().model_dump()


@router.put("")
async def update_tslocation(
    config: TslocationConfigUpdate,
    config_group: str | None = Query(None, description="Config group name for server mode"),
):
    return await tslocation_router.update_config_helper(config.model_dump(exclude_none=True), config_group)


@router.post("/validate")
async def validate_tslocation(
    config: TslocationConfigUpdate,
    config_group: str | None = Query(None, description="Config group name for server mode"),
):
    return tslocation_router.validate_config_helper(config.model_dump(exclude_none=True), config_group)
