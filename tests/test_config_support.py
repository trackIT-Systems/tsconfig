"""Tests for app.configs.support: a service's config is only accepted where its
systemd unit exists, so a bundle from another kind of device (soundscapepipe.yml
on a radiotracking image) is rejected instead of making tsconfig show a service
that is not installed."""

import io
import subprocess
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app import cli, main
from app.configs import support
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.routers import configs
from app.routers.base import BaseConfigRouter

SCHEDULE = "button_delay: '00:01'\nschedule: []\n"


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.asyncio
class TestUnsupportedConfigTypes:
    async def test_a_missing_unit_makes_its_configs_unsupported(self, missing_units):
        missing_units.add("soundscapepipe")
        result = await support.unsupported_config_types(["radiotracking", "soundscapepipe", "schedule"])
        assert result == {"soundscapepipe": "soundscapepipe"}

    async def test_base_system_configs_are_always_supported_without_asking_systemd(self, monkeypatch):
        monkeypatch.setattr(support, "run_subprocess_async", AsyncMock(side_effect=AssertionError("asked systemd")))
        assert await support.unsupported_config_types(["authorized_keys", "cmdline", "geolocation"]) == {}

    async def test_only_not_found_counts_as_missing(self, monkeypatch):
        # A masked or broken unit is still installed; its config stays editable.
        output = "LoadState=masked\n\nLoadState=not-found\n"
        monkeypatch.setattr(
            support, "run_subprocess_async", AsyncMock(return_value=subprocess.CompletedProcess([], 0, output, ""))
        )
        assert await support.unsupported_config_types(["radiotracking", "soundscapepipe"]) == {
            "soundscapepipe": "soundscapepipe"
        }

    @pytest.mark.parametrize(
        "behaviour",
        [
            {"side_effect": FileNotFoundError("systemctl")},
            {"side_effect": subprocess.TimeoutExpired("systemctl", 10)},
            {"return_value": subprocess.CompletedProcess([], 1, "", "Failed to connect to bus")},
        ],
    )
    async def test_everything_is_supported_if_systemd_cannot_be_asked(self, monkeypatch, behaviour):
        monkeypatch.setattr(support, "run_subprocess_async", AsyncMock(**behaviour))
        assert await support.unsupported_config_types(["soundscapepipe"]) == {}


@pytest.mark.asyncio
class TestUploadsRejectUnsupportedConfigs:
    async def test_zip_with_a_config_of_a_missing_service_is_rejected_as_a_whole(self, missing_units):
        missing_units.add("soundscapepipe")
        zip_bytes = _zip_bytes({"schedule.yml": SCHEDULE, "soundscapepipe.yml": "anything"})
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "write_config", new=AsyncMock()) as mock_write,
            pytest.raises(HTTPException) as exc_info,
        ):
            await configs.upload_config_zip(
                file=UploadFile(filename="bundle.zip", file=io.BytesIO(zip_bytes)),
                restart_services=False,
                pedantic=False,
                force=True,
                reboot="allow",
            )
        assert exc_info.value.status_code == 400
        assert "soundscapepipe.yml (no soundscapepipe service on this device)" in exc_info.value.detail
        mock_write.assert_not_called()

    async def test_single_file_of_a_missing_service_is_rejected(self, missing_units):
        missing_units.add("radiotracking")
        with (
            patch.object(configs.config_loader, "is_server_mode", return_value=False),
            patch.object(configs.priv, "write_config", new=AsyncMock()) as mock_write,
            pytest.raises(HTTPException) as exc_info,
        ):
            await configs.upload_config(
                file=UploadFile(filename="radiotracking.ini", file=io.BytesIO(b"[optional arguments]\n")),
                restart_service=False,
                mtime="2025-01-01T00:00:00",
                force=True,
            )
        assert exc_info.value.status_code == 400
        mock_write.assert_not_called()

    async def test_cli_zip_upload_is_rejected_as_a_whole(self, tmp_path, missing_units):
        missing_units.add("soundscapepipe")
        zip_path = tmp_path / "bundle.zip"
        zip_path.write_bytes(_zip_bytes({"schedule.yml": SCHEDULE, "soundscapepipe.yml": "anything"}))
        with (
            patch.object(cli.priv, "write_config", new=AsyncMock()) as mock_write,
            pytest.raises(SystemExit) as exc_info,
        ):
            await cli.process_zip_upload(zip_path, force=True)
        assert exc_info.value.code == 1
        mock_write.assert_not_called()

    async def test_system_reset_skips_unsupported_configs_and_applies_the_rest(self, tmp_path, missing_units):
        missing_units.add("soundscapepipe")
        zip_path = tmp_path / "default.zip"
        zip_path.write_bytes(_zip_bytes({"schedule.yml": SCHEDULE, "soundscapepipe.yml": "anything"}))
        with patch.object(configs.priv, "write_config", new=AsyncMock(return_value={})) as mock_write:
            saved_files, _cmdline_updated = await configs._apply_config_zip_from_path(zip_path, force=True)
        assert saved_files == ["schedule.yml"]
        mock_write.assert_called_once()


@pytest.mark.asyncio
class TestListingHidesUnsupportedConfigs:
    async def test_download_and_listing_leave_out_configs_of_missing_services(self, missing_units):
        missing_units.add("soundscapepipe")
        files = await configs.supported_config_files()
        assert "soundscapepipe.yml" not in files
        assert {"radiotracking.ini", "schedule.yml", "authorized_keys"} <= files.keys()

    async def test_available_services_needs_the_unit_in_tracker_mode(self, tmp_path, missing_units):
        missing_units.add("soundscapepipe")
        with (
            patch.object(main.config_loader, "is_server_mode", return_value=False),
            patch.object(main.config_loader, "get_config_dir", return_value=tmp_path),
        ):
            (tmp_path / "radiotracking.ini").write_text("[optional arguments]\n")
            (tmp_path / "soundscapepipe.yml").write_text("lat: 1\n")
            result = await main.get_available_services()
        assert "radiotracking" in result["available_services"]
        assert "soundscapepipe" not in result["available_services"]


@pytest.mark.asyncio
class TestSettingsPagesRejectUnsupportedConfigs:
    async def test_saving_the_config_of_a_missing_service_is_rejected(self, monkeypatch, missing_units):
        missing_units.add("soundscapepipe")
        router = BaseConfigRouter(SoundscapepipeConfig, "soundscapepipe", "tag")
        monkeypatch.setattr(router.config_instance, "validate", lambda config: [])
        with (
            patch("app.routers.base.config_loader.is_server_mode", return_value=False),
            patch("app.routers.base.write_config", new=AsyncMock()) as mock_write,
            pytest.raises(HTTPException) as exc_info,
        ):
            await router.update_config_helper({"lat": 1})
        assert exc_info.value.status_code == 400
        assert "soundscapepipe.yml" in exc_info.value.detail["message"]
        mock_write.assert_not_called()

    async def test_the_page_leaves_out_tabs_of_missing_services(self, tmp_path, missing_units):
        missing_units.add("soundscapepipe")
        (tmp_path / "radiotracking.ini").write_text("[optional arguments]\n")
        (tmp_path / "soundscapepipe.yml").write_text("lat: 1\n")
        with (
            patch.object(main.config_loader, "is_server_mode", return_value=False),
            patch.object(main.config_loader, "get_config_dir", return_value=tmp_path),
        ):
            assert await main._get_available_services_for_template() == ["radiotracking"]
