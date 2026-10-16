"""Tests for app.routers.system_reset's privileged-path rewiring: wireguard
deletion, overlay wipe, and reboot scheduling all go through
app.utils.privileged instead of touching the filesystem/systemctl directly.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.routers import system_reset
from app.utils.privileged import PrivilegedCallError


class FakeRequest:
    def __init__(self, reset_config: bool = False, wipe_overlay: bool = False):
        self.reset_config = reset_config
        self.wipe_overlay = wipe_overlay


@pytest.mark.asyncio
class TestSystemReset:
    async def test_server_mode_rejected(self):
        with (
            patch.object(system_reset.config_loader, "is_server_mode", return_value=True),
            pytest.raises(HTTPException) as exc_info,
        ):
            await system_reset.system_reset(FakeRequest())
        assert exc_info.value.status_code == 403

    async def test_wipe_overlay_calls_privileged_op_and_schedules_reboot(self):
        with (
            patch.object(system_reset.config_loader, "is_server_mode", return_value=False),
            patch.object(system_reset, "wipe_overlay", new=AsyncMock(return_value={"wiped": True})) as mock_wipe,
            patch.object(
                system_reset, "schedule_reboot", new=AsyncMock(return_value={"scheduled": True})
            ) as mock_reboot,
        ):
            result = await system_reset.system_reset(FakeRequest(wipe_overlay=True))
        mock_wipe.assert_called_once()
        mock_reboot.assert_called_once_with(delay=10)
        assert result.wipe_overlay_done is True
        assert result.reboot_initiated is True

    async def test_wipe_overlay_failure_surfaces_as_500(self):
        with (
            patch.object(system_reset.config_loader, "is_server_mode", return_value=False),
            patch.object(
                system_reset, "wipe_overlay", new=AsyncMock(side_effect=PrivilegedCallError("permission denied"))
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await system_reset.system_reset(FakeRequest(wipe_overlay=True))
        assert exc_info.value.status_code == 500

    async def test_reset_config_deletes_wireguard_via_privileged_op(self, tmp_path):
        default_zip = tmp_path / "default.zip"
        default_zip.write_text("not a real zip, mocked below")
        with (
            patch.object(system_reset.config_loader, "is_server_mode", return_value=False),
            patch.object(system_reset, "DEFAULT_CONFIG_ZIP", default_zip),
            patch.object(system_reset, "_apply_config_zip_from_path", return_value=(["schedule.yml"], False)),
            patch.object(system_reset, "delete_config", new=AsyncMock(return_value={"deleted": True})) as mock_delete,
        ):
            result = await system_reset.system_reset(FakeRequest(reset_config=True))
        mock_delete.assert_called_once_with("wireguard")
        assert result.reset_config_done is True

    async def test_wireguard_delete_failure_does_not_abort_reset(self, tmp_path):
        default_zip = tmp_path / "default.zip"
        default_zip.write_text("not a real zip, mocked below")
        with (
            patch.object(system_reset.config_loader, "is_server_mode", return_value=False),
            patch.object(system_reset, "DEFAULT_CONFIG_ZIP", default_zip),
            patch.object(system_reset, "_apply_config_zip_from_path", return_value=(["schedule.yml"], False)),
            patch.object(system_reset, "delete_config", new=AsyncMock(side_effect=PrivilegedCallError("boom"))),
        ):
            result = await system_reset.system_reset(FakeRequest(reset_config=True))
        assert result.reset_config_done is True
        assert "Failed to remove wireguard.conf" in result.message
