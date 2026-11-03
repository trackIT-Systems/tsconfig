"""Fixes from gpsd, over its JSON protocol (https://gpsd.io/gpsd_json.html).

gpsd is the only program that reads the receiver; this is a client like
gpspipe. It sends `?WATCH` and turns every TPV report with a 2D or 3D fix into
a Fix, with the satellites used and the HDOP of the latest SKY report.

gpsd sends a TPV for every NMEA sentence of a cycle, so several (identical) per
GNSS second; only the first of each second becomes a fix. The receiver's cycle,
aligned to the GNSS second, is the clock of the fixes.
"""

import asyncio
import datetime
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.location.geo import Fix

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 2947
WATCH = b'?WATCH={"enable":true,"json":true};\n'


def parse_time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value).astimezone()
    except ValueError:
        return None


def tpv_fix(tpv: dict[str, Any], sky: dict[str, Any]) -> Fix | None:
    """The fix in a TPV report, or None without one (mode 0 or 1, or no position)."""
    if tpv.get("mode", 0) < 2 or tpv.get("lat") is None or tpv.get("lon") is None:
        return None
    accuracy = tpv.get("eph")
    if accuracy is None and tpv.get("epx") is not None and tpv.get("epy") is not None:
        accuracy = max(tpv["epx"], tpv["epy"])
    alt = None
    if tpv.get("mode") == 3:
        alt = next((tpv[key] for key in ("altMSL", "alt", "altHAE") if tpv.get(key) is not None), None)
    sats = sky.get("uSat")
    if sats is None and isinstance(sky.get("satellites"), list):
        sats = sum(1 for s in sky["satellites"] if s.get("used"))
    return Fix(
        lat=tpv["lat"],
        lon=tpv["lon"],
        alt=alt,
        accuracy=accuracy,
        time=parse_time(tpv.get("time")),
        sats=sats,
        hdop=sky.get("hdop"),
    )


class Gpsd:
    """One connection to gpsd. `fixes()` yields until the connection is closed."""

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port

    async def fixes(self) -> AsyncIterator[Fix]:
        """One fix per GNSS second from now on (every fix, for a receiver that reports no time).

        Raises:
            OSError: gpsd cannot be reached, or closed the connection.
        """
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(WATCH)
            await writer.drain()
            sky: dict[str, Any] = {}
            last_time = None
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionResetError("gpsd closed the connection")
                try:
                    report = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Ignoring a line from gpsd that is not JSON: %r", line[:80])
                    continue
                cls = report.get("class")
                if cls == "SKY":
                    sky = report
                elif cls == "TPV":
                    fix = tpv_fix(report, sky)
                    if fix is None or (fix.time is not None and fix.time == last_time):
                        continue
                    last_time = fix.time
                    yield fix
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
