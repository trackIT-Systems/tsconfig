"""Tsupdate daemon configuration endpoints."""

from typing import Literal, Optional

from fastapi import Query
from pydantic import BaseModel, Field

from app.routers.base import BaseConfigRouter
from app.configs.tsupdate import TsupdateConfig


class TsupdateConfigUpdate(BaseModel):
    """Tsupdate configuration update model."""

    check_interval: int = Field(3600, gt=0, description="How often to check for updates (in seconds)")
    include_prereleases: bool = Field(False, description="Include pre-releases when checking for updates")
    github_url: Optional[str] = Field(None, description="Optional GitHub repository URL override")
    max_releases: int = Field(5, gt=0, description="Maximum number of recent releases to check for batch updates")
    persist_timeout: int = Field(600, gt=0, description="System uptime threshold before persisting tryboot configuration (in seconds)")
    update_countdown: int = Field(60, gt=0, description="Countdown before initiating tryboot reboot after update (in seconds)")
    do: Literal["nothing", "check", "download", "apply"] = Field("nothing", description="Regular behavior: what to do when checking for updates (applies outside maintenance window or when no maintenance schedule). Valid values: nothing, check, download, apply")
    maintenance_check_interval: Optional[int] = Field(3600, gt=0, description="How often to check for updates during maintenance window (in seconds). If not specified, uses check_interval value")
    maintenance_do: Optional[Literal["nothing", "check", "download", "apply"]] = Field("check", description="Maintenance behavior: what to do when checking for updates during maintenance window. If not specified, defaults to 'apply' when maintenance schedule exists")


# Create the router using the base class
tsupdate_router = BaseConfigRouter(TsupdateConfig, "tsupdate", "tsupdate")
router = tsupdate_router.router


# Override methods to use our specific model
@router.put("")
async def update_tsupdate(
    config: TsupdateConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump(exclude_none=True)
    return tsupdate_router.update_config_helper(config_dict, config_group)


@router.post("/validate")
async def validate_tsupdate(
    config: TsupdateConfigUpdate,
    config_group: Optional[str] = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump(exclude_none=True)
    return tsupdate_router.validate_config_helper(config_dict, config_group)

