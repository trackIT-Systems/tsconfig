"""Wall clock, boot clock and whether the wall clock can be trusted yet.

A station without RTC boots with a wrong clock (fake-hwclock's last value, or
worse), and stays that way until chrony syncs, which may take long without a
network. Telemetry runs anyway; every reading also carries CLOCK_BOOTTIME, so
readings taken before the sync can be re-timed once the offset is known.
"""

import ctypes
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# adjtimex(2) return value while STA_UNSYNC (or STA_CLOCKERR) is set
TIME_ERROR = 5


def kernel_clock_synced() -> bool:
    """Whether the kernel considers the clock synchronized.

    chronyd clears STA_UNSYNC once it is synced. adjtimex with `modes` 0 only
    reads, which needs no privileges. Where adjtimex does not exist (not Linux)
    the clock is assumed to be right.
    """
    try:
        adjtimex = ctypes.CDLL(None, use_errno=True).adjtimex
    except AttributeError:
        return True
    # struct timex is about 208 bytes on 64-bit Linux; zeroed means modes == 0
    buf = ctypes.create_string_buffer(512)
    state = adjtimex(buf)
    if state == -1:
        logger.debug("adjtimex failed: errno %d", ctypes.get_errno())
        return False
    return state != TIME_ERROR


class Clock:
    """The clocks the daemon reads. Tests replace it with a fake.

    Once synced, the clock stays synced for the rest of the boot: the kernel sets
    STA_UNSYNC again when chrony loses its sources for a while, but the clock is
    still right then. The marker in `state_dir` (a runtime directory, empty after
    every boot) keeps this across daemon restarts.
    """

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir
        self._synced = False

    def time(self) -> float:
        return time.time()

    def boottime(self) -> float:
        return time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))

    def boot_id(self) -> str:
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            return "unknown"

    def synced(self) -> bool:
        if self._synced:
            return True
        marker = self.state_dir / "synced" if self.state_dir else None
        if (marker and marker.exists()) or kernel_clock_synced():
            self._synced = True
            if marker:
                try:
                    marker.touch()
                except OSError as e:
                    logger.debug("Cannot write %s: %s", marker, e)
        return self._synced
