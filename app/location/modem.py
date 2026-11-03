"""The LTE modem's GNSS and serving cell, through ModemManager (`mmcli`).

ModemManager switches the GNSS with its `gps-unmanaged` location source: it
starts the receiver but leaves the NMEA port to gpsd. The `3gpp` source reports
the serving cell for the coarse check. Both need ModemManager's Location polkit
action (tsOS-base grants it to pi).
"""

import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from app.utils.subprocess_async import run_subprocess_async

logger = logging.getLogger(__name__)

MMCLI_TIMEOUT_S = 20


class ReceiverError(Exception):
    """The receiver cannot be switched, or there is none."""


@dataclass(frozen=True)
class Cell:
    mcc: str
    mnc: str
    # TAC on LTE, LAC on older networks
    tac: str
    cell_id: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


async def mmcli(*args: str, output: bool = True) -> dict[str, Any]:
    """`mmcli <args> --output-json`, parsed; with `output` False the command only acts and {} is returned.

    Commands that change something (`--location-enable-...`) print a sentence even with
    --output-json, so their output is not parsed.

    Raises:
        ReceiverError: mmcli is missing, timed out or failed.
    """
    command = ["mmcli", *args, "--output-json"] if output else ["mmcli", *args]
    try:
        result = await run_subprocess_async(command, capture_output=True, text=True, timeout=MMCLI_TIMEOUT_S)
    except FileNotFoundError as e:
        raise ReceiverError("mmcli is not installed") from e
    except (TimeoutError, subprocess.TimeoutExpired) as e:
        raise ReceiverError(f"mmcli {' '.join(args)} timed out") from e
    if result.returncode != 0:
        raise ReceiverError(f"mmcli {' '.join(args)} failed: {result.stderr.strip() or result.returncode}")
    if not output:
        return {}
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as e:
        raise ReceiverError(f"mmcli {' '.join(args)} printed no JSON") from e


def _known(value: Any) -> str | None:
    """ModemManager writes `--` for a value it does not know."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text in ("", "--") else text


def parse_cell(location: dict[str, Any]) -> Cell | None:
    """The serving cell in `mmcli --location-get`, or None if it is not known."""
    gpp = location.get("modem", {}).get("location", {}).get("3gpp", {})
    mcc, mnc, cell_id = _known(gpp.get("mcc")), _known(gpp.get("mnc")), _known(gpp.get("cid"))
    area = _known(gpp.get("tac"))
    if area is None or area.strip("0") == "":
        area = _known(gpp.get("lac"))
    if mcc is None or mnc is None or cell_id is None or area is None:
        return None
    return Cell(mcc=mcc, mnc=mnc, tac=area, cell_id=cell_id)


class ModemReceiver:
    """The GNSS of the first modem ModemManager knows.

    The modem is looked up for every call: it may appear after the daemon starts
    (USB enumeration, a modem reset), and its index changes when it comes back.
    """

    name = "modem"

    async def _modem(self) -> str:
        listing = await mmcli("-L")
        modems = listing.get("modem-list", [])
        if not modems:
            raise ReceiverError("ModemManager knows no modem")
        # "/org/freedesktop/ModemManager1/Modem/0" -> "0"
        return modems[0].rsplit("/", 1)[-1]

    async def _location(self, modem: str) -> dict[str, Any]:
        return (await mmcli("-m", modem, "--location-status")).get("modem", {}).get("location", {})

    async def start(self) -> None:
        """Switch the GNSS on.

        Raises:
            ReceiverError: No modem, or it has no GNSS that gpsd can read.
        """
        modem = await self._modem()
        location = await self._location(modem)
        if "gps-unmanaged" not in location.get("capabilities", []):
            raise ReceiverError(f"Modem {modem} does not support gps-unmanaged")
        if "gps-unmanaged" not in location.get("enabled", []):
            await mmcli("-m", modem, "--location-enable-gps-unmanaged", output=False)
            logger.debug("Enabled gps-unmanaged on modem %s", modem)

    async def stop(self) -> None:
        """Switch the GNSS off.

        Raises:
            ReceiverError: The modem cannot be reached.
        """
        modem = await self._modem()
        if "gps-unmanaged" in (await self._location(modem)).get("enabled", []):
            await mmcli("-m", modem, "--location-disable-gps-unmanaged", output=False)
            logger.debug("Disabled gps-unmanaged on modem %s", modem)

    async def serving_cell(self) -> Cell | None:
        """The serving cell, or None if the modem does not know it.

        Raises:
            ReceiverError: No modem, or it does not report cells.
        """
        modem = await self._modem()
        location = await self._location(modem)
        if "3gpp-lac-ci" not in location.get("capabilities", []):
            raise ReceiverError(f"Modem {modem} does not report its serving cell")
        if "3gpp-lac-ci" not in location.get("enabled", []):
            await mmcli("-m", modem, "--location-enable-3gpp", output=False)
            logger.debug("Enabled 3gpp on modem %s", modem)
        return parse_cell(await mmcli("-m", modem, "--location-get"))
