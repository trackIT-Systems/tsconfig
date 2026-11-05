"""Why the device last reset: the firmware's PM_RSTS flags and how the previous boot ended.

`vcgencmd get_rsts` after each kind of reset, measured on tsOS with kernel
6.18 (panic=10, systemd RuntimeWatchdogSec=10s), 2026-11-05:

    =====================  ============  ======  ============  ===========
    Board                  power cycle   reboot  kernel panic  HW watchdog
    =====================  ============  ======  ============  ===========
    Pi 3 B                 0x1000        0x1020  0x20          0x20
    Pi 4 B (EEPROM 2022)   0x1000        0x1020  0x1000        0x1000
    Pi 5                   0x1000        0x1020  0x20          0x20
    =====================  ============  ======  ============  ===========

0x1000 is `power_on_reset`, 0x20 `watchdog_full_reset` (Linux reboots by
arming the watchdog). A kernel panic and an expired hardware watchdog cannot
be told apart; on a Pi 4 a crash even looks like a power-on. The journal can
tell more: a clean shutdown or reboot logs `systemd-shutdown` at the very end
of the boot, a crash or power loss does not. With both, `reset_reason()` names
the cause.
"""

import functools
import subprocess
from pathlib import Path
from typing import Any

# PM_RSTS flags (vcgencmd.parsers.RSTS_FLAGS) other than power_on_reset: the SoC was reset, not powered up
RESET_FLAGS = (
    "watchdog_quick_reset",
    "watchdog_full_reset",
    "watchdog_hard_reset",
    "software_quick_reset",
    "software_full_reset",
    "software_hard_reset",
    "debugger_quick_reset",
    "debugger_full_reset",
    "debugger_hard_reset",
)

DEVICE_TREE_MODEL = Path("/proc/device-tree/model")


@functools.cache
def previous_boot_clean() -> bool | None:
    """Whether the previous boot ended with a shutdown or reboot; None without a previous boot in the journal.

    Cached: the answer cannot change while the system runs.
    """
    try:
        result = subprocess.run(
            ["journalctl", "--boot=-1", "--identifier=systemd-shutdown", "--lines=1", "--quiet", "--output=cat"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # journalctl fails when the journal holds no previous boot (first boot, or rotated away)
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


@functools.cache
def crash_looks_like_power_on() -> bool:
    """Whether this board reports a kernel panic or watchdog reset as power-on (BCM2711: Pi 4, Pi 400, CM4)."""
    try:
        model = DEVICE_TREE_MODEL.read_text().rstrip("\0")
    except OSError:
        return False
    return any(name in model for name in ("Raspberry Pi 4", "Raspberry Pi 400", "Compute Module 4"))


def reset_reason(flags: dict[str, Any] | None, clean: bool | None, crash_as_power_on: bool) -> str | None:
    """Name the cause of the last reset from the PM_RSTS flags and `previous_boot_clean()`."""
    if not flags:
        return None
    reset = any(flags.get(flag) for flag in RESET_FLAGS)
    if clean:
        return "reboot" if reset else "shutdown"
    if reset:
        # A reboot keeps power_on_reset set, a crash clears it
        return "reboot" if flags.get("power_on_reset") else "crash"
    if crash_as_power_on:
        return "power_loss_or_crash" if clean is False else "power_on_or_crash"
    return "power_loss" if clean is False else "power_on"
