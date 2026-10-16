"""Root-only config files (the hotspot profile) sit in a directory `pi` cannot
traverse, so `Path.exists()` raises PermissionError instead of returning False.
The shared bundle/download helpers must cope with that rather than 500."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.routers import configs


class Untraversable(type(Path())):
    """A path whose exists()/stat() fail the way they do inside a 0700 root directory."""

    def exists(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))

    def stat(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))


def utc(*args: int) -> datetime:
    """A naive UTC datetime, as app.routers.configs uses for file times."""
    return datetime(*args, tzinfo=UTC).replace(tzinfo=None)


def root_only_instance():
    return SimpleNamespace(config_file=Untraversable("/etc/NetworkManager/system-connections/hotspot.nmconnection"))


def test_timestamp_comparison_treats_unreadable_file_as_updatable():
    results = configs.compare_file_timestamps(
        {"hotspot.nmconnection": root_only_instance()},
        {"hotspot.nmconnection": utc(2026, 1, 1, 12, 0, 0)},
    )
    result = results["hotspot.nmconnection"]
    assert result["exists_on_disk"] is True
    assert result["existing_timestamp"] is None
    assert result["should_update"] is True and result["is_newer"] is True


def test_timestamp_comparison_uses_the_mtime_reported_by_the_privileged_op():
    instance = root_only_instance()
    zip_time = utc(2026, 6, 1, 12, 0, 0)
    epoch = lambda dt: dt.replace(tzinfo=UTC).timestamp()

    def compare(disk_time):
        stats = {"hotspot.nmconnection": {"exists": True, "mtime": epoch(disk_time)}}
        return configs.compare_file_timestamps(
            {"hotspot.nmconnection": instance}, {"hotspot.nmconnection": zip_time}, stats
        )["hotspot.nmconnection"]

    newer_bundle = compare(utc(2026, 5, 1))
    assert newer_bundle["should_update"] is True and newer_bundle["existing_timestamp"] is not None
    same_bundle = compare(zip_time)
    assert same_bundle["should_update"] is False  # applied before: skipped without --force
    older_bundle = compare(utc(2026, 7, 1))
    assert older_bundle["should_update"] is False and older_bundle["is_newer"] is False


def test_privileged_report_of_a_missing_file_means_update():
    stats = {"hotspot.nmconnection": {"exists": False, "mtime": None}}
    result = configs.compare_file_timestamps(
        {"hotspot.nmconnection": root_only_instance()},
        {"hotspot.nmconnection": utc(2026, 1, 1)},
        stats,
    )["hotspot.nmconnection"]
    assert result["exists_on_disk"] is False and result["should_update"] is True


@pytest.mark.asyncio
async def test_stats_are_only_requested_for_files_pi_cannot_stat(tmp_path):
    readable = tmp_path / "wireguard.conf"
    readable.write_text("x")
    instances = {
        "hotspot.nmconnection": root_only_instance(),
        "wireguard.conf": SimpleNamespace(config_file=readable),
    }
    reply = {"exists": True, "mtime": 123.0}
    with patch.object(configs.priv, "stat_config", AsyncMock(return_value=reply)) as stat:
        stats = await configs.collect_privileged_stats(instances)
    stat.assert_awaited_once_with("hotspot")
    assert stats == {"hotspot.nmconnection": reply}


@pytest.mark.asyncio
async def test_failed_privileged_lookup_falls_back_to_updatable():
    error = configs.priv.PrivilegedCallError("denied")
    with patch.object(configs.priv, "stat_config", AsyncMock(side_effect=error)):
        stats = await configs.collect_privileged_stats({"hotspot.nmconnection": root_only_instance()})
    assert stats == {}
    result = configs.compare_file_timestamps(
        {"hotspot.nmconnection": root_only_instance()}, {"hotspot.nmconnection": utc(2026, 1, 1)}, stats
    )["hotspot.nmconnection"]
    assert result["should_update"] is True


def test_single_file_download_is_forbidden_not_a_server_error():
    with patch.object(configs, "get_config_instance", return_value=root_only_instance()):
        with patch.object(configs.config_loader, "is_server_mode", return_value=False):
            with pytest.raises(HTTPException) as err:
                configs._validate_config_filename_and_get_instance("hotspot.nmconnection")
    assert err.value.status_code == 403


def test_hotspot_is_a_recognized_bundle_member():
    assert configs.RECOGNIZED_CONFIG_FILES["hotspot.nmconnection"] == "hotspot"
    parsed = configs.parse_config_file("hotspot.nmconnection", "[wifi]\nmode=ap\n")
    assert parsed == {"content": "[wifi]\nmode=ap\n"}
