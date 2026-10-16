"""Tests for app.routers.systemd's privileged-path rewiring: service_action
accepts any verb in ALLOWED_SYSTEMCTL_VERBS (start/stop/restart/reload) -
never enable/disable/mask, which are a categorically separate polkit action
`pi`'s polkit rule never grants - and reboot/action both call directly into
app.utils.privileged's unprivileged (or pkexec/pkcheck-authorized) helpers,
never sudo.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.routers import systemd
from app.utils.privileged import PrivilegedCallError


class FakeAction:
    def __init__(self, action: str, service: str):
        self.action = action
        self.service = service


@pytest.mark.asyncio
class TestServiceAction:
    @pytest.mark.parametrize("action", ["enable", "disable", "mask"])
    async def test_persistent_actions_rejected_before_systemctl(self, action):
        with patch.object(systemd, "systemctl_action", new=AsyncMock()) as mock_action:
            with pytest.raises(HTTPException) as exc_info:
                await systemd.service_action(FakeAction(action, "tsconfig"))
        assert exc_info.value.status_code == 400
        mock_action.assert_not_called()

    @pytest.mark.parametrize("action", ["start", "stop", "restart", "reload"])
    async def test_non_persistent_actions_accepted(self, action):
        fake_service = type("S", (), {"name": "tsconfig", "expert": False})()
        with (
            patch.object(systemd, "get_configured_services", return_value=[fake_service]),
            patch.object(systemd, "systemctl_action", new=AsyncMock(return_value=(True, None))) as mock_action,
        ):
            result = await systemd.service_action(FakeAction(action, "tsconfig"))
        assert result["success"] is True
        assert result["action"] == action
        mock_action.assert_called_once_with("tsconfig", action)

    async def test_unconfigured_service_rejected(self):
        with patch.object(systemd, "get_configured_services", return_value=[]):
            with pytest.raises(HTTPException) as exc_info:
                await systemd.service_action(FakeAction("restart", "sshd"))
        assert exc_info.value.status_code == 400

    async def test_denied_action_surfaces_as_500(self):
        fake_service = type("S", (), {"name": "sshd", "expert": True})()
        with (
            patch.object(systemd, "get_configured_services", return_value=[fake_service]),
            patch.object(systemd, "systemctl_action", new=AsyncMock(return_value=(False, "not allowed by polkit"))),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await systemd.service_action(FakeAction("restart", "sshd"))
        assert exc_info.value.status_code == 500
        assert "not allowed" in exc_info.value.detail


@pytest.mark.asyncio
class TestRebootEndpoint:
    async def test_success(self):
        with patch.object(systemd, "schedule_reboot", new=AsyncMock(return_value={"scheduled": True})):
            result = await systemd.reboot_system()
        assert result == {"message": "System reboot initiated"}

    async def test_failure_surfaces_as_500(self):
        with patch.object(systemd, "schedule_reboot", new=AsyncMock(side_effect=PrivilegedCallError("denied"))):
            with pytest.raises(HTTPException) as exc_info:
                await systemd.reboot_system()
        assert exc_info.value.status_code == 500
