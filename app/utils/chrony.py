"""chrony's view of the clock (`chronyc -c tracking`).

chrony is the only authority for whether the station's clock can be trusted
(tsOS-base docs/time.md): it has a source once it has selected NTP, GNSS (refids
GPS and PPS) or the RTC (refid RTC, fed by tsscheduled after its check).

tslocationd makes a fine fix when chrony's last update is older than
`time_max_age`; tstelemetry's `timesync` collector reports the source.
"""

import subprocess
from dataclasses import dataclass

from app.utils.subprocess_async import run_subprocess_async

CHRONYC_TIMEOUT_S = 10

# Reference names of chrony's reference clocks in tsOS (etc/chrony/conf.d)
GNSS_REFIDS = ("GPS", "PPS")
RTC_REFID = "RTC"
UNSYNCED = "Not synchronised"


class ChronyError(Exception):
    """chronyc is missing or failed."""


@dataclass(frozen=True)
class Tracking:
    """The fields of `chronyc -c tracking` this code uses."""

    # The reference's name: an NTP server's address, or a reference clock's refid
    name: str
    stratum: int
    # Unix time of the last clock update from the reference; None before the first
    ref_time: float | None
    # Seconds the system clock is off from chrony's estimate of the true time
    offset: float
    root_delay: float
    root_dispersion: float
    leap: str

    @property
    def source(self) -> str:
        """`none`, `rtc`, `gnss` or `ntp`."""
        if self.leap == UNSYNCED or self.ref_time is None:
            return "none"
        if self.name == RTC_REFID:
            return "rtc"
        if self.name in GNSS_REFIDS:
            return "gnss"
        return "ntp"

    @property
    def error(self) -> float:
        """chrony's bound on the clock's error in seconds: half the root delay plus the root dispersion."""
        return self.root_delay / 2 + self.root_dispersion


def parse_tracking(output: str) -> Tracking:
    """Parse one line of `chronyc -c tracking`.

    Fields: reference ID, name, stratum, reference time, system time offset, last
    offset, RMS offset, frequency, residual frequency, skew, root delay, root
    dispersion, update interval, leap status.
    """
    fields = output.strip().split(",")
    try:
        ref_time = float(fields[3])
        return Tracking(
            name=fields[1],
            stratum=int(fields[2]),
            ref_time=ref_time or None,
            offset=float(fields[4]),
            root_delay=float(fields[10]),
            root_dispersion=float(fields[11]),
            leap=fields[13],
        )
    except (IndexError, ValueError) as e:
        raise ChronyError(f"Unexpected chronyc output: {output.strip()[:80]!r}") from e


async def tracking() -> Tracking:
    """chrony's tracking state.

    Raises:
        ChronyError: chronyc is missing, timed out or failed.
    """
    try:
        result = await run_subprocess_async(
            ["chronyc", "-c", "tracking"], capture_output=True, text=True, timeout=CHRONYC_TIMEOUT_S
        )
    except FileNotFoundError as e:
        raise ChronyError("chronyc is not installed") from e
    except (TimeoutError, subprocess.TimeoutExpired) as e:
        raise ChronyError("chronyc timed out") from e
    if result.returncode != 0:
        raise ChronyError(f"chronyc failed: {result.stderr.strip() or result.returncode}")
    return parse_tracking(result.stdout)


async def last_update() -> float | None:
    """Unix time of chrony's last clock update from any source; None if there was none since it started.

    Raises:
        ChronyError: chronyc is missing, timed out or failed.
    """
    return (await tracking()).ref_time
