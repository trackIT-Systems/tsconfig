"""MQTT util configuration endpoints."""

from typing import Any

from fastapi import Query
from pydantic import BaseModel, Field

from app.configs.mqttutil import MqttUtilConfig
from app.routers.base import BaseConfigRouter


class MqttUtilDefaultSection(BaseModel):
    """DEFAULT section defaults for all mqttutil tasks."""

    scheduling_interval: str | None = Field(None, description="Default scheduling interval (e.g. '5s')")
    topic_prefix: str | None = Field(None, description="Default MQTT topic prefix")
    requires: list[str] | None = Field(None, description="Default Python modules to import")
    qos: int | None = Field(None, ge=0, le=2, description="Default MQTT QoS level (0, 1, or 2)")


class MqttUtilTaskSection(BaseModel):
    """Single mqttutil reporting task."""

    func: str = Field(..., min_length=1, description="Python expression evaluated on schedule")
    scheduling_interval: str | None = Field(None, description="Scheduling interval (e.g. '5s')")
    topic_prefix: str | None = Field(None, description="MQTT topic prefix for this task")
    requires: list[str] | None = Field(None, description="Python modules to import for this task")
    qos: int | None = Field(None, ge=0, le=2, description="MQTT QoS level (0, 1, or 2)")


class MqttUtilConfigUpdate(BaseModel):
    """Full mqttutil configuration update model."""

    DEFAULT: MqttUtilDefaultSection | None = None

    model_config = {"extra": "allow"}


mqttutil_router = BaseConfigRouter(MqttUtilConfig, "mqttutil", "mqttutil")
router = mqttutil_router.router


def _normalize_config_update(config: MqttUtilConfigUpdate) -> dict[str, Any]:
    """Convert Pydantic model to dict, preserving dynamic task sections."""
    data = config.model_dump(exclude_none=True)
    default_section = data.pop("DEFAULT", None)
    if default_section is not None:
        data["DEFAULT"] = default_section
    return data


@router.put("")
async def update_mqttutil(
    config: MqttUtilConfigUpdate,
    config_group: str | None = Query(None, description="Config group name for server mode"),
):
    config_dict = _normalize_config_update(config)
    return await mqttutil_router.update_config_helper(config_dict, config_group)


@router.post("/validate")
async def validate_mqttutil(
    config: MqttUtilConfigUpdate,
    config_group: str | None = Query(None, description="Config group name for server mode"),
):
    config_dict = _normalize_config_update(config)
    return mqttutil_router.validate_config_helper(config_dict, config_group)
