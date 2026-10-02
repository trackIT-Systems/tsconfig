"""Tests for the staff (root) login: the password is checked by `su -c true
root` under a pty (app.auth.pam_auth._check_staff_via_su_blocking).

A fake `su` script stands in for the real binary. Like the real one it prints
"Password:" and *discards pending input* when it turns echo off, so a password
written before the prompt is lost; this makes the tests fail for an
implementation that does not wait for the prompt. The real `su` was checked
separately in a Debian container, as an unprivileged `pi` against a real root
password (right, wrong, locked root, spaces/non-ASCII, control characters).
"""

import asyncio
import json
import os
import sys
import time
from unittest.mock import patch

import pytest

from app.auth import pam_auth

ROOT_PASSWORD = "Tr0ub4dor&3 pässwörd"

FAKE_SU = """#!{python}
import json, os, sys, termios, time
json.dump({{"argv": sys.argv, "environ": dict(os.environ), "pid": os.getpid()}}, open({dump!r}, "w"))
mode = {mode!r}
if mode == "silent":      # never prompts, never exits
    time.sleep(60)
if mode == "noprompt":    # fails straight away, e.g. su cannot run
    sys.exit(1)
time.sleep({delay})
termios.tcflush(0, termios.TCIFLUSH)   # like su turning echo off (TCSAFLUSH): earlier input is lost
sys.stdout.write("Password: "); sys.stdout.flush()
line = sys.stdin.readline().rstrip("\\n")
sys.exit(0 if line == {expected!r} else 1)
"""


@pytest.fixture
def fake_su(tmp_path, monkeypatch):
    """Install a fake su; returns a function (mode, delay) -> dump file path."""
    dump = tmp_path / "su-call.json"

    def install(mode="normal", delay=0.1):
        script = tmp_path / "su"
        script.write_text(FAKE_SU.format(python=sys.executable, dump=str(dump), mode=mode, delay=delay, expected=ROOT_PASSWORD))
        script.chmod(0o755)
        monkeypatch.setattr(pam_auth, "SU_PATH", str(script))
        return dump

    return install


def check(password):
    return asyncio.run(pam_auth.authenticate_operator("root", password))


class TestStaffCheck:
    def test_right_password_is_accepted(self, fake_su):
        fake_su()
        assert check(ROOT_PASSWORD) is True

    def test_wrong_password_is_refused(self, fake_su):
        fake_su()
        assert check("wrong") is False
        assert check(ROOT_PASSWORD + " ") is False

    def test_password_is_only_written_after_the_prompt(self, fake_su):
        # The fake flushes input right before printing the prompt; an early write would be lost.
        fake_su(delay=0.5)
        assert check(ROOT_PASSWORD) is True

    def test_su_is_called_for_root_with_a_plain_shell(self, fake_su):
        dump = fake_su()
        check(ROOT_PASSWORD)
        argv = json.loads(dump.read_text())["argv"]
        assert argv[1:] == ["-s", "/bin/sh", "-c", "true", "root"]

    def test_password_never_reaches_argv_or_environment(self, fake_su):
        dump = fake_su()
        check(ROOT_PASSWORD)
        assert ROOT_PASSWORD not in dump.read_text()

    def test_environment_is_clean(self, fake_su, monkeypatch):
        monkeypatch.setenv("SECRET_FROM_SERVICE", "leak")
        dump = fake_su()
        check(ROOT_PASSWORD)
        environ = json.loads(dump.read_text())["environ"]
        assert "SECRET_FROM_SERVICE" not in environ
        assert environ["PATH"] == "/usr/bin:/bin" and environ["LC_ALL"] == "C"

    def test_su_that_never_prompts_times_out_and_is_killed(self, fake_su, monkeypatch):
        monkeypatch.setattr(pam_auth, "SU_TIMEOUT_SECONDS", 1.0)
        dump = fake_su(mode="silent")
        started = time.monotonic()
        assert check(ROOT_PASSWORD) is False
        assert time.monotonic() - started < 5
        pid = json.loads(dump.read_text())["pid"]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # gone and reaped, not left running or as a zombie

    def test_su_that_fails_immediately_is_a_refusal(self, fake_su):
        fake_su(mode="noprompt")
        started = time.monotonic()
        assert check(ROOT_PASSWORD) is False
        assert time.monotonic() - started < 3

    def test_missing_su_binary_is_a_refusal_not_an_error(self, monkeypatch):
        monkeypatch.setattr(pam_auth, "SU_PATH", "/nonexistent/su")
        assert check(ROOT_PASSWORD) is False


class TestStaffGuards:
    @pytest.mark.parametrize("password", ["abc\x03def", "abc\x04", "abc\x15", "back\x7fspace", "tab\there", "line\nbreak", "cr\r"])
    def test_control_characters_are_refused_without_starting_su(self, password):
        with patch.object(pam_auth.subprocess, "Popen") as su:
            assert check(password) is False
        su.assert_not_called()

    @pytest.mark.parametrize("password", ["", "a\x00b", "x" * (pam_auth.MAX_PASSWORD_LENGTH + 1)])
    def test_unusable_passwords_never_start_su(self, password):
        with patch.object(pam_auth.subprocess, "Popen") as su:
            assert check(password) is False
        su.assert_not_called()

    def test_operator_goes_through_pam_and_never_su(self):
        with patch.object(pam_auth, "_authenticate_blocking", return_value=True) as pam, patch.object(
            pam_auth.subprocess, "Popen"
        ) as su:
            assert asyncio.run(pam_auth.authenticate_operator("pi", "natur")) is True
        pam.assert_called_once_with("pi", "natur")
        su.assert_not_called()

    def test_staff_goes_through_su_and_never_direct_pam(self, fake_su):
        fake_su()
        with patch.object(pam_auth, "_authenticate_blocking") as pam:
            assert check(ROOT_PASSWORD) is True
        pam.assert_not_called()

    def test_parallel_attempts_beyond_the_cap_are_refused_not_queued(self, fake_su):
        fake_su(delay=1.0)

        async def burst():
            return await asyncio.gather(*(pam_auth.authenticate_operator("root", ROOT_PASSWORD) for _ in range(4)))

        started = time.monotonic()
        results = asyncio.run(burst())
        assert sorted(results, reverse=True) == [True] * pam_auth.SU_MAX_CONCURRENT + [False] * (
            4 - pam_auth.SU_MAX_CONCURRENT
        )
        assert time.monotonic() - started < 4  # the refused ones did not wait for a slot

    def test_slots_are_released_after_use(self, fake_su):
        fake_su()
        for _ in range(pam_auth.SU_MAX_CONCURRENT + 2):
            assert check(ROOT_PASSWORD) is True
