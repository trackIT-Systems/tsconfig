"""The reset reason from PM_RSTS and the previous boot's journal (app.system.reset)."""

import subprocess

import pytest

from app.system import reset

POWER_ON = {"power_on_reset": True, "raw": 0x1000}
REBOOT = {"power_on_reset": True, "watchdog_full_reset": True, "raw": 0x1020}
CRASH = {"power_on_reset": False, "watchdog_full_reset": True, "raw": 0x20}


@pytest.fixture(autouse=True)
def clear_caches():
    yield
    reset.previous_boot_clean.cache_clear()
    reset.crash_looks_like_power_on.cache_clear()


# The values measured on a Pi 3, 4 and 5 (see the module docstring)
@pytest.mark.parametrize(
    ("flags", "clean", "pi4", "reason"),
    [
        (REBOOT, True, False, "reboot"),
        (REBOOT, True, True, "reboot"),
        (CRASH, False, False, "crash"),
        (POWER_ON, False, False, "power_loss"),
        (POWER_ON, False, True, "power_loss_or_crash"),
        (POWER_ON, True, False, "shutdown"),
        # Without a previous boot in the journal only the flags speak
        (REBOOT, None, False, "reboot"),
        (CRASH, None, False, "crash"),
        (POWER_ON, None, False, "power_on"),
        (POWER_ON, None, True, "power_on_or_crash"),
        (None, None, False, None),
    ],
)
def test_reset_reason(flags, clean, pi4, reason):
    assert reset.reset_reason(flags, clean, pi4) == reason


def _journalctl(monkeypatch, returncode, stdout):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(reset.subprocess, "run", run)
    return calls


def test_a_previous_boot_with_systemd_shutdown_ended_cleanly(monkeypatch):
    calls = _journalctl(monkeypatch, 0, "Sending SIGTERM to remaining processes...\n")
    assert reset.previous_boot_clean() is True
    assert reset.previous_boot_clean() is True
    assert len(calls) == 1
    assert "--boot=-1" in calls[0] and "--identifier=systemd-shutdown" in calls[0]


def test_a_previous_boot_without_systemd_shutdown_crashed_or_lost_power(monkeypatch):
    _journalctl(monkeypatch, 0, "")
    assert reset.previous_boot_clean() is False


def test_no_previous_boot_in_the_journal_is_unknown(monkeypatch):
    _journalctl(monkeypatch, 1, "")
    assert reset.previous_boot_clean() is None


def test_no_journalctl_is_unknown(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(reset.subprocess, "run", run)
    assert reset.previous_boot_clean() is None


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Raspberry Pi 4 Model B Rev 1.5\0", True),
        ("Raspberry Pi Compute Module 4 Rev 1.0\0", True),
        ("Raspberry Pi 3 Model B Rev 1.2\0", False),
        ("Raspberry Pi 5 Model B Rev 1.1\0", False),
    ],
)
def test_only_bcm2711_boards_report_a_crash_as_power_on(tmp_path, monkeypatch, model, expected):
    path = tmp_path / "model"
    path.write_text(model)
    monkeypatch.setattr(reset, "DEVICE_TREE_MODEL", path)
    assert reset.crash_looks_like_power_on() is expected
