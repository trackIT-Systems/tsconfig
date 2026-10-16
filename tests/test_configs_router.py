"""Tests for app.routers.configs' privileged-path rewiring: single-file
upload (upload_config), zip upload (upload_config_zip), the shared
_apply_config_zip_from_path (used by system reset), and service restart
(handle_service_restart) all go through app.utils.privileged instead of
calling config_instance.save()/systemctl/systemd-run directly.
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app.routers import configs
from app.utils.privileged import PrivilegedCallError


def _upload_file(filename: str, content: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content))


@pytest.mark.asyncio
class TestUploadConfigSingleFile:
    async def test_server_mode_rejected(self):
        with patch.object(configs.config_loader, "is_server_mode", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                await configs.upload_config(
                    file=_upload_file("wireguard.conf", b"[Interface]\n"),
                    restart_service=False,
                    mtime="2025-01-01T00:00:00",
                    force=True,
                )
        assert exc_info.value.status_code == 403

    async def test_valid_upload_calls_privileged_write_with_epoch_mtime(self):
        content = b"[Interface]\nPrivateKey=abc\n[Peer]\nPublicKey=x\nEndpoint=1.2.3.4:1\nAllowedIPs=0.0.0.0/0\n"
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "write_config", new=AsyncMock(return_value={"save_result": None})) as mock_write,
        ):
            await configs.upload_config(
                file=_upload_file("wireguard.conf", content),
                restart_service=False,
                mtime="2025-01-01T00:00:00",
                force=True,
            )
        assert mock_write.call_count == 1
        call_args = mock_write.call_args
        assert call_args.args[0] == "wireguard"
        assert call_args.args[1] == {"content": content.decode()}
        assert isinstance(call_args.kwargs["mtime"], int)

    async def test_wireguard_hook_rejected_before_reaching_privileged_write(self):
        content = b"[Interface]\nPostUp=evil\n[Peer]\nPublicKey=x\n"
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "write_config", new=AsyncMock()) as mock_write,
        ):
            result = await configs.upload_config(
                file=_upload_file("wireguard.conf", content),
                restart_service=False,
                mtime="2025-01-01T00:00:00",
                force=True,
            )
        assert result["valid"] is False
        mock_write.assert_not_called()

    async def test_privileged_write_failure_surfaces_as_500(self):
        content = b"[Interface]\nPrivateKey=abc\n[Peer]\nPublicKey=x\n"
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "write_config", new=AsyncMock(side_effect=PrivilegedCallError("denied"))),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await configs.upload_config(
                    file=_upload_file("wireguard.conf", content),
                    restart_service=False,
                    mtime="2025-01-01T00:00:00",
                    force=True,
                )
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
class TestApplyConfigZipFromPath:
    def _make_zip(self, tmp_path, files: dict):
        zip_path = tmp_path / "config.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return zip_path

    async def test_calls_privileged_write_per_file(self, tmp_path):
        zip_path = self._make_zip(
            tmp_path,
            {"schedule.yml": "button_delay: '00:01'\nschedule: []\n"},
        )
        with patch.object(configs, "priv") as mock_priv:
            mock_priv.write_config = AsyncMock(return_value={"save_result": None})
            mock_priv.PrivilegedCallError = PrivilegedCallError
            saved_files, cmdline_updated = await configs._apply_config_zip_from_path(zip_path, force=True)
        assert saved_files == ["schedule.yml"]
        assert cmdline_updated is False
        mock_priv.write_config.assert_called_once()
        assert mock_priv.write_config.call_args.args[0] == "schedule"

    async def test_privileged_failure_raises_value_error(self, tmp_path):
        zip_path = self._make_zip(tmp_path, {"schedule.yml": "button_delay: '00:01'\nschedule: []\n"})
        with patch.object(configs, "priv") as mock_priv:
            mock_priv.write_config = AsyncMock(side_effect=PrivilegedCallError("nope"))
            mock_priv.PrivilegedCallError = PrivilegedCallError
            with pytest.raises(ValueError, match="Failed to save"):
                await configs._apply_config_zip_from_path(zip_path, force=True)

    async def test_missing_zip_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            await configs._apply_config_zip_from_path(tmp_path / "missing.zip")


@pytest.mark.asyncio
class TestHandleServiceRestart:
    async def test_no_restart_requested_short_circuits(self):
        result = await configs.handle_service_restart("schedule", False)
        assert result == {"service_restarted": False, "service_restart_error": None}

    async def test_server_mode_rejected(self):
        with patch.object(configs.config_loader, "is_server_mode", return_value=True):
            result = await configs.handle_service_restart("schedule", True)
        assert result["service_restart_error"] == "Service restart is not available in server mode"

    async def test_unmapped_config_type_reports_error(self):
        with patch.object(configs.config_loader, "is_server_mode", return_value=False):
            result = await configs.handle_service_restart("not-a-real-type", True)
        assert "No service mapping" in result["service_restart_error"]

    async def test_uses_registry_service_mapping_and_privileged_restart(self):
        # wireguard is in registry.SERVICE_MAPPING but was missing from the
        # old locally-duplicated dict in this function - regression guard.
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "systemctl_action", new=AsyncMock(return_value=(True, None))) as mock_restart,
        ):
            result = await configs.handle_service_restart("wireguard", True)
        mock_restart.assert_called_once_with("wg-quick@wireguard", "restart")
        assert result["service_restarted"] is True


@pytest.mark.asyncio
class TestUploadConfigZip:
    def _zip_bytes(self, files: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()

    async def test_server_mode_rejected(self):
        with patch.object(configs.config_loader, "is_server_mode", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                await configs.upload_config_zip(
                    file=_upload_file("config.zip", self._zip_bytes({"schedule.yml": "x"})),
                    restart_services=False,
                    pedantic=False,
                    force=True,
                    reboot="allow",
                )
        assert exc_info.value.status_code == 403

    async def test_valid_zip_saves_via_privileged_write(self):
        zip_bytes = self._zip_bytes({"schedule.yml": "button_delay: '00:01'\nschedule: []\n"})
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "write_config", new=AsyncMock(return_value={"save_result": None})) as mock_write,
        ):
            result = await configs.upload_config_zip(
                file=_upload_file("config.zip", zip_bytes),
                restart_services=False,
                pedantic=False,
                force=True,
                reboot="allow",
            )
        assert result["success"] is True
        assert "schedule.yml" in result["saved_files"]
        mock_write.assert_called_once()
        assert mock_write.call_args.args[0] == "schedule"
