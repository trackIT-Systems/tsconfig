"""Tests for app.cli's privileged-path rewiring: process_zip_upload and
process_system_reset (the `tsconfig zip` / `tsconfig system-reset` shell
commands) go through app.utils.privileged the same way the web/BLE service
does, rather than shelling out to systemctl/systemd-run directly or calling
config_instance.save() - a shell operator gets exactly the capabilities the
UI has, not more.
"""

import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from app import cli
from app.utils.privileged import PrivilegedCallError


def _make_zip(tmp_path, files: dict):
    zip_path = tmp_path / "upload.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return zip_path


@pytest.mark.asyncio
class TestProcessZipUpload:
    async def test_saves_via_privileged_write(self, tmp_path):
        zip_path = _make_zip(tmp_path, {"schedule.yml": "button_delay: '00:01'\nschedule: []\n"})
        with patch.object(cli.priv, "write_config", new=AsyncMock(return_value={"save_result": None})) as mock_write:
            result = await cli.process_zip_upload(zip_path, force=True)
        assert result["success"] is True
        assert "schedule.yml" in result["saved_files"]
        mock_write.assert_called_once()
        assert mock_write.call_args.args[0] == "schedule"

    async def test_restart_services_uses_privileged_restart(self, tmp_path):
        zip_path = _make_zip(tmp_path, {"schedule.yml": "button_delay: '00:01'\nschedule: []\n"})
        with patch.object(cli.priv, "write_config", new=AsyncMock(return_value={"save_result": None})), patch.object(
            cli.priv, "systemctl_action", new=AsyncMock(return_value=(True, None))
        ) as mock_restart:
            result = await cli.process_zip_upload(zip_path, force=True, restart_services=True)
        mock_restart.assert_called_once_with("tsschedule", "restart")
        assert "tsschedule" in result["services_restarted"]

    async def test_cmdline_update_with_allow_schedules_reboot(self, tmp_path):
        zip_path = _make_zip(tmp_path, {"cmdline.txt": "timezone=Europe/Berlin"})
        with patch.object(cli.priv, "write_config", new=AsyncMock(return_value={"save_result": None})), patch.object(
            cli.priv, "schedule_reboot", new=AsyncMock(return_value={"scheduled": True})
        ) as mock_reboot:
            await cli.process_zip_upload(zip_path, force=True, reboot="allow")
        mock_reboot.assert_called_once_with(delay=10)

    async def test_reboot_forbid_never_reboots(self, tmp_path):
        zip_path = _make_zip(tmp_path, {"cmdline.txt": "timezone=Europe/Berlin"})
        with patch.object(cli.priv, "write_config", new=AsyncMock(return_value={"save_result": None})), patch.object(
            cli.priv, "schedule_reboot", new=AsyncMock()
        ) as mock_reboot:
            await cli.process_zip_upload(zip_path, force=True, reboot="forbid")
        mock_reboot.assert_not_called()

    async def test_save_failure_exits(self, tmp_path, capsys):
        zip_path = _make_zip(tmp_path, {"schedule.yml": "button_delay: '00:01'\nschedule: []\n"})
        with patch.object(cli.priv, "write_config", new=AsyncMock(side_effect=PrivilegedCallError("denied"))):
            with pytest.raises(SystemExit) as exc_info:
                await cli.process_zip_upload(zip_path, force=True)
        assert exc_info.value.code == 1


@pytest.mark.asyncio
class TestProcessSystemReset:
    async def test_server_mode_exits(self):
        with patch.object(cli.config_loader, "is_server_mode", return_value=True):
            with pytest.raises(SystemExit):
                await cli.process_system_reset()

    async def test_both_flags_exits(self):
        with patch.object(cli.config_loader, "is_server_mode", return_value=False):
            with pytest.raises(SystemExit):
                await cli.process_system_reset(keep_config=True, keep_overlay=True)

    async def test_wipe_overlay_uses_privileged_op_and_reboots(self, tmp_path):
        with patch.object(cli.config_loader, "is_server_mode", return_value=False), patch.object(
            cli, "DEFAULT_CONFIG_ZIP", tmp_path / "nonexistent.zip"
        ), patch.object(cli.priv, "wipe_overlay", new=AsyncMock(return_value={"wiped": True})) as mock_wipe, patch.object(
            cli.priv, "schedule_reboot", new=AsyncMock(return_value={"scheduled": True})
        ) as mock_reboot:
            await cli.process_system_reset(keep_config=True, keep_overlay=False)
        mock_wipe.assert_called_once()
        mock_reboot.assert_called_once_with(delay=10)

    async def test_reset_config_deletes_wireguard_via_privileged_op(self, tmp_path):
        zip_path = _make_zip(tmp_path, {"schedule.yml": "button_delay: '00:01'\nschedule: []\n"})
        with patch.object(cli.config_loader, "is_server_mode", return_value=False), patch.object(
            cli, "DEFAULT_CONFIG_ZIP", zip_path
        ), patch.object(cli.priv, "write_config", new=AsyncMock(return_value={"save_result": None})), patch.object(
            cli.priv, "delete_config", new=AsyncMock(return_value={"deleted": True})
        ) as mock_delete:
            await cli.process_system_reset(keep_config=False, keep_overlay=True)
        mock_delete.assert_called_once_with("wireguard")
