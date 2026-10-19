"""The cellular modem as ModemManager reports it (the same data as GET /api/network/modem)."""

import shutil

from app.system.modem import ModemInfo, read_modem
from app.telemetry.collectors.base import Collector, Reading, Unavailable


class ModemCollector(Collector):
    name = "modem"
    columns = tuple(ModemInfo.model_fields)

    def check(self) -> None:
        if shutil.which("mmcli") is None:
            raise Unavailable("mmcli not installed")

    async def collect(self) -> list[Reading]:
        info = await read_modem()
        if info is None:
            return []
        reading = info.model_dump()
        if reading["ip_addresses"] is not None:
            reading["ip_addresses"] = " ".join(reading["ip_addresses"])
        return [reading]
