"""The station's clock as chrony sees it: which source it follows and how far off it may be.

Station readings carry no time over MQTT; the receiver stamps them on arrival.
`station_time` is the station's own clock as a value, so the receiver can see
how far it is off, e.g. an RTC that drifted (tsOS-base docs/time.md).
"""

import datetime
import shutil

from app.telemetry.collectors.base import Collector, Reading, Unavailable
from app.utils import chrony


class TimesyncCollector(Collector):
    name = "timesync"
    columns = ("source", "stratum", "error_s", "station_time")

    def check(self) -> None:
        if shutil.which("chronyc") is None:
            raise Unavailable("chronyc not installed")

    async def collect(self) -> list[Reading]:
        tracking = await chrony.tracking()
        synced = tracking.source != "none"
        return [
            {
                "source": tracking.source,
                "stratum": tracking.stratum if synced else None,
                "error_s": round(tracking.error, 6) if synced else None,
                "station_time": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            }
        ]
