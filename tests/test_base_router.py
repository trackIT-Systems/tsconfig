"""Tests for app.routers.base.BaseConfigRouter - the shared PUT-config
handler used by radiotracking/schedule/soundscapepipe/mqttutil/tsupdate.
Tracker-mode saves must go through the privileged write path, never call
the config class's save() directly (which fails as pi against a root-owned
boot partition).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.configs.wireguard import WireguardConfig
from app.routers.base import BaseConfigRouter
from app.utils.privileged import PrivilegedCallError


class TestBaseConfigRouterConstruction:
    def test_rejects_prefix_not_in_registry(self):
        with pytest.raises(ValueError, match="no matching entry"):
            BaseConfigRouter(WireguardConfig, "not-a-registered-type", "tag")

    def test_accepts_a_registered_prefix(self):
        # "wireguard" is a valid registry entry even though its config
        # class isn't normally driven through BaseConfigRouter in practice -
        # this only tests the prefix/registry validation, not real usage.
        router = BaseConfigRouter(WireguardConfig, "wireguard", "tag")
        assert router.prefix == "wireguard"


@pytest.mark.asyncio
class TestUpdateConfigHelper:
    async def test_tracker_mode_save_goes_through_privileged_write(self, monkeypatch):
        router = BaseConfigRouter(WireguardConfig, "wireguard", "tag")
        monkeypatch.setattr(router.config_instance, "validate", lambda config: [])
        with (
            patch("app.routers.base.config_loader.is_server_mode", return_value=False),
            patch("app.routers.base.write_config", new=AsyncMock(return_value={"ok": True})) as mock_write,
        ):
            result = await router.update_config_helper({"content": "x"})
        mock_write.assert_called_once_with("wireguard", {"content": "x"})
        assert "updated successfully" in result["message"]

    async def test_validation_errors_returned_as_400_without_calling_privileged_write(self, monkeypatch):
        router = BaseConfigRouter(WireguardConfig, "wireguard", "tag")
        monkeypatch.setattr(router.config_instance, "validate", lambda config: ["bad content"])
        with patch("app.routers.base.write_config", new=AsyncMock()) as mock_write:
            with pytest.raises(HTTPException) as exc_info:
                await router.update_config_helper({"content": "bad"})
        assert exc_info.value.status_code == 400
        mock_write.assert_not_called()

    async def test_privileged_write_failure_surfaces_as_500(self, monkeypatch):
        router = BaseConfigRouter(WireguardConfig, "wireguard", "tag")
        monkeypatch.setattr(router.config_instance, "validate", lambda config: [])
        with (
            patch("app.routers.base.config_loader.is_server_mode", return_value=False),
            patch("app.routers.base.write_config", new=AsyncMock(side_effect=PrivilegedCallError("denied"))),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await router.update_config_helper({"content": "x"})
        assert exc_info.value.status_code == 500
