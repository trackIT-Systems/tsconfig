"""Usage of the configured mount points, one reading each."""

import logging
import os

import psutil

from app.telemetry.collectors.base import Collector, Reading

logger = logging.getLogger(__name__)


class DiskCollector(Collector):
    name = "disk"
    columns = ("mount", "total", "used", "free", "percent")

    def read(self) -> list[Reading]:
        readings = []
        for mount in self.settings.mounts:
            if not os.path.ismount(mount):
                logger.debug("%s is not mounted", mount)
                continue
            usage = psutil.disk_usage(mount)
            readings.append(
                {"mount": mount, "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent}
            )
        return readings
