"""Tests for the /api/userconf/hash convenience endpoint - turns a plaintext
password into a 'pi:$6$...' userconf.txt line via `openssl passwd -6 -stdin`,
so an operator without their own way to generate a hash can still use a
plain password field. Never writes anything and never touches a live
account - see app/routers/userconf.py for the full rationale."""

import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.configs.userconf import UserconfConfig
from app.routers.userconf import hash_userconf_password, UserconfPasswordHash


@pytest.mark.asyncio
class TestHashUserconfPassword:
    async def test_hashes_a_real_password_into_a_valid_userconf_line(self):
        result = await hash_userconf_password(UserconfPasswordHash(password="hunter2"))
        assert result["content"].startswith("pi:$6$")
        # The actual format contract this endpoint exists to satisfy.
        assert UserconfConfig().validate({"content": result["content"]}) == []

    async def test_same_password_hashes_differently_each_time(self):
        # openssl passwd -6 generates a random salt per invocation - this
        # also guards against a future "helpful" refactor pinning the salt.
        first = await hash_userconf_password(UserconfPasswordHash(password="hunter2"))
        second = await hash_userconf_password(UserconfPasswordHash(password="hunter2"))
        assert first["content"] != second["content"]

    async def test_password_never_passed_as_a_command_line_argument(self):
        with patch(
            "app.routers.userconf.run_subprocess_async", new=AsyncMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="$6$abc$xyz\n"))
        ) as mock_run:
            await hash_userconf_password(UserconfPasswordHash(password="correct-horse-battery-staple"))
        args, kwargs = mock_run.call_args
        assert "correct-horse-battery-staple" not in args[0]
        assert kwargs["input_data"] == b"correct-horse-battery-staple\n"

    async def test_subprocess_failure_surfaces_as_500_not_a_traceback(self):
        with patch(
            "app.routers.userconf.run_subprocess_async",
            new=AsyncMock(side_effect=FileNotFoundError("openssl not found")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await hash_userconf_password(UserconfPasswordHash(password="hunter2"))
        assert exc_info.value.status_code == 500


class TestUserconfPasswordHashValidation:
    def test_rejects_empty_password(self):
        with pytest.raises(ValueError):
            UserconfPasswordHash(password="")
