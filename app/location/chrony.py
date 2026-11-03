"""When chrony last used a time source (`chronyc -c tracking`).

On a station without network, GNSS is the only time source, and chrony only
gets time from it while the receiver runs. The daemon makes a fine fix when
chrony's last update is older than `time_max_age`.
"""

import subprocess

from app.utils.subprocess_async import run_subprocess_async

CHRONYC_TIMEOUT_S = 10
# Field of `chronyc -c tracking`: the reference time, Unix time of the last update (0 before the first)
REF_TIME_FIELD = 3


class ChronyError(Exception):
    """chronyc is missing or failed."""


def parse_ref_time(output: str) -> float | None:
    """The reference time in `chronyc -c tracking` output; None if chrony has never updated."""
    fields = output.strip().split(",")
    try:
        ref_time = float(fields[REF_TIME_FIELD])
    except (IndexError, ValueError) as e:
        raise ChronyError(f"Unexpected chronyc output: {output.strip()[:80]!r}") from e
    return ref_time or None


async def last_update() -> float | None:
    """Unix time of chrony's last clock update from any source; None if there was none since it started.

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
    return parse_ref_time(result.stdout)
