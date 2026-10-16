"""Tests for app.auth.pam_auth's guard logic. The libpam call itself is
mocked here: it needs a Linux PAM stack and a real `pi` account (it was
checked in a Debian container as an unprivileged `pi`). The staff (root)
check through `su` is covered in test_staff_login.py."""

from unittest.mock import patch

import pytest

from app.auth import pam_auth


@pytest.mark.asyncio
async def test_accepts_operator_when_pam_says_yes():
    with patch.object(pam_auth, "_authenticate_blocking", return_value=True) as pam:
        assert await pam_auth.authenticate_operator("pi", "natur") is True
    pam.assert_called_once_with("pi", "natur")


@pytest.mark.asyncio
async def test_rejects_when_pam_says_no():
    with patch.object(pam_auth, "_authenticate_blocking", return_value=False):
        assert await pam_auth.authenticate_operator("pi", "wrong") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["admin", "nobody", "PI", "Root", "pi ", "root ", "", "pi\x00root"])
async def test_only_the_operator_and_staff_accounts_are_ever_checked(user):
    with (
        patch.object(pam_auth, "_authenticate_blocking", return_value=True) as pam,
        patch.object(pam_auth.subprocess, "Popen") as su,
    ):
        assert await pam_auth.authenticate_operator(user, "natur") is False
    pam.assert_not_called()
    su.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["", "a\x00b", "x" * (pam_auth.MAX_PASSWORD_LENGTH + 1)])
async def test_unusable_passwords_never_reach_pam(password):
    with patch.object(pam_auth, "_authenticate_blocking", return_value=True) as pam:
        assert await pam_auth.authenticate_operator("pi", password) is False
    pam.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("libpam not found"), RuntimeError("boom")])
async def test_pam_failures_mean_denied_not_exception(error):
    with patch.object(pam_auth, "_authenticate_blocking", side_effect=error):
        assert await pam_auth.authenticate_operator("pi", "natur") is False
