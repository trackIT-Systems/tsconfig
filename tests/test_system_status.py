"""Tests for the system status endpoint's helpers in app.main."""

import json
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from app import main

STATUS = {"booted_via_tryboot": "False", "active_partition": "/dev/mmcblk0p2", "active_partition_label": "rootfs"}


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(main, "_tsupdate_status_cache", None)


def _completed(returncode, stdout=""):
    return subprocess.CompletedProcess(["tsupdate"], returncode, stdout=stdout, stderr="")


@pytest.mark.asyncio
class TestTsupdateStatus:
    async def test_successful_status_is_asked_once(self):
        run = AsyncMock(return_value=_completed(0, json.dumps(STATUS)))
        with patch.object(main, "run_subprocess_async", new=run):
            first = await main._get_tsupdate_status()
            second = await main._get_tsupdate_status()
        assert first == STATUS
        assert second == STATUS
        run.assert_awaited_once()

    async def test_failure_is_not_cached(self):
        run = AsyncMock(return_value=_completed(1))
        with patch.object(main, "run_subprocess_async", new=run):
            assert await main._get_tsupdate_status() is None
            assert await main._get_tsupdate_status() is None
        assert run.await_count == 2
