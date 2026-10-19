"""The first modem's information from ModemManager (`mmcli`)."""

import json
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from app.utils.subprocess_async import run_subprocess_async


class MmcliUnavailable(Exception):
    """mmcli is not installed."""


class MmcliTimeout(Exception):
    """mmcli did not answer in time."""


class ModemInfo(BaseModel):
    """Modem information model."""

    # Basic info
    manufacturer: str | None = Field(None, description="Modem manufacturer")
    model: str | None = Field(None, description="Modem model")
    hardware_revision: str | None = Field(None, description="Hardware revision")
    imei: str | None = Field(None, description="IMEI number")

    # Signal info
    signal_strength_dbm: int | None = Field(None, description="Signal strength in dBm")
    signal_strength_percent: int | None = Field(None, description="Signal strength as percentage")
    access_technology: str | None = Field(None, description="Current access technology (e.g., LTE, 5G)")

    # Connection info
    state: str | None = Field(None, description="Modem state")
    operator_name: str | None = Field(None, description="Network operator name")
    ip_addresses: list[str] | None = Field(None, description="IP addresses")

    # SIM info
    sim_iccid: str | None = Field(None, description="SIM ICCID")
    sim_operator: str | None = Field(None, description="SIM operator name")
    sim_imsi: str | None = Field(None, description="SIM IMSI")


async def _mmcli_json(args: list[str]) -> dict[str, Any] | None:
    """`mmcli <args> --output-json`, parsed; None when mmcli fails or prints no JSON."""
    try:
        result = await run_subprocess_async(
            ["mmcli", *args, "--output-json"], capture_output=True, text=True, timeout=10
        )
    except (TimeoutError, subprocess.TimeoutExpired) as e:
        raise MmcliTimeout("ModemManager command timed out") from e
    except FileNotFoundError as e:
        raise MmcliUnavailable("ModemManager (mmcli) not available") from e
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


async def read_modem() -> ModemInfo | None:
    """Information about the first modem, or None if there is none.

    Raises:
        MmcliUnavailable: mmcli is not installed.
        MmcliTimeout: mmcli did not answer in time.
    """
    # Format is like: {"modem-list": ["/org/freedesktop/ModemManager1/Modem/0", ...]}
    modem_list = await _mmcli_json(["-L"])
    modem_paths = (modem_list or {}).get("modem-list", [])
    if not modem_paths:
        return None

    # "/org/freedesktop/ModemManager1/Modem/0" -> "0"
    modem_id = modem_paths[0].split("/")[-1]
    modem_data = await _mmcli_json(["-m", modem_id])
    if modem_data is None:
        return None

    modem = modem_data.get("modem", {})
    generic = modem.get("generic", {})
    signal = modem.get("signal", {})
    three_gpp = modem.get("3gpp", {})

    signal_strength_percent = None
    if generic.get("signal-quality"):
        signal_strength_percent = generic["signal-quality"].get("value")

    signal_strength_dbm = None
    for technology in ("lte", "cdma1x", "evdo", "gsm", "umts"):
        if signal and technology in signal:
            signal_strength_dbm = signal[technology].get("rssi")
            break

    access_tech = generic.get("access-technologies", [])

    ip_addresses = []
    for bearer_path in modem.get("bearers", []):
        bearer_data = await _mmcli_json(["-b", bearer_path.split("/")[-1]])
        if bearer_data is None:
            continue
        bearer_info = bearer_data.get("bearer", {})
        for config in ("ipv4-config", "ipv6-config"):
            address = bearer_info.get(config, {}).get("address")
            if address:
                ip_addresses.append(address)

    sim_properties: dict[str, Any] = {}
    sim_path = generic.get("sim")
    if sim_path:
        sim_data = await _mmcli_json(["-i", sim_path.split("/")[-1]])
        if sim_data is not None:
            sim_properties = sim_data.get("sim", {}).get("properties", {})

    return ModemInfo(
        manufacturer=generic.get("manufacturer"),
        model=generic.get("model"),
        hardware_revision=generic.get("revision"),
        imei=generic.get("equipment-identifier"),
        signal_strength_dbm=signal_strength_dbm,
        signal_strength_percent=signal_strength_percent,
        access_technology=access_tech[0] if access_tech else None,
        state=generic.get("state"),
        operator_name=three_gpp.get("operator-name"),
        ip_addresses=ip_addresses or None,
        sim_iccid=sim_properties.get("iccid"),
        sim_operator=sim_properties.get("operator-name"),
        sim_imsi=sim_properties.get("imsi"),
    )
