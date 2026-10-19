"""Network management router for NetworkManager connections."""

import asyncio
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config_loader import config_loader
from app.logging_config import get_logger
from app.privileged.ops import ALLOWED_CONNECTIONS
from app.system import modem
from app.system.modem import ModemInfo
from app.utils.privileged import PrivilegedCallError, unset_gsm_fields
from app.utils.subprocess_async import run_subprocess_async

router = APIRouter(prefix="/api/network", tags=["network"])
logger = get_logger(__name__)


def _ensure_tracker_mode() -> None:
    """Network configuration changes only make sense in tracker mode - server
    mode (the Docker/fleet deployment) has no NetworkManager, no `sudo`
    wrapper, and no boot partition (see app/utils/privileged.py's module
    docstring: it never checks this itself, so every caller must).
    """
    if config_loader.is_server_mode():
        raise HTTPException(status_code=403, detail="Network configuration is not available in server mode")


class ConnectionInfo(BaseModel):
    """Network connection information model."""

    name: str = Field(..., description="Connection name")
    type: str = Field(..., description="Connection type (wifi, ethernet, etc.)")
    device: str = Field(..., description="Network device (wlan0, eth0, etc.) or empty if not connected")
    state: str = Field(..., description="Connection state (activated, activating, deactivated, etc.)")
    ipv4_address: str | None = Field(None, description="IPv4 address if connected")


class HotspotConfig(BaseModel):
    """Hotspot configuration model."""

    ssid: str = Field(..., description="Hotspot SSID (network name)")
    password: str = Field(..., description="Hotspot password")
    band: str | None = Field(None, description="WiFi band (2.4GHz, 5GHz, 6GHz)")
    channel: int | None = Field(None, description="WiFi channel number (null for auto)")
    channel_width: str | None = Field(None, description="Channel width in MHz (20, 40, 80, 160)")
    hidden: bool | None = Field(None, description="Hide SSID (true/false)")


class HotspotUpdate(BaseModel):
    """Hotspot update request model."""

    password: str | None = Field(None, description="New hotspot password", min_length=8, max_length=63)
    band: str | None = Field(None, description="WiFi band (2.4GHz, 5GHz, 6GHz)")
    channel: int | None = Field(None, description="WiFi channel number (null for auto)")
    channel_width: str | None = Field(None, description="Channel width in MHz (20, 40, 80, 160)")
    hidden: bool | None = Field(None, description="Hide SSID (true/false)")


class WiFiStationConfig(BaseModel):
    """WiFi station (client) configuration model."""

    ssid: str = Field(..., description="WiFi network name (SSID) to join")
    password: str = Field(default="", description="WPA2 pre-shared key")
    autoconnect: bool = Field(..., description="Whether NetworkManager auto-activates this connection")


class WiFiStationUpdate(BaseModel):
    """WiFi station update request model."""

    ssid: str | None = Field(None, description="WiFi network name (SSID)")
    password: str | None = Field(None, description="WPA2 pre-shared key (8-63 characters)")
    autoconnect: bool | None = Field(None, description="Enable or disable connection.autoconnect")


class CellularConfig(BaseModel):
    """Cellular/GSM configuration model."""

    apn: str | None = Field(None, description="Access Point Name (APN)")
    username: str | None = Field(None, description="Username for APN authentication")
    password: str | None = Field(None, description="Password for APN authentication")
    pin: str | None = Field(None, description="SIM PIN code")


class CellularUpdate(BaseModel):
    """Cellular update request model."""

    apn: str | None = Field(None, description="New Access Point Name (APN)")
    username: str | None = Field(None, description="New username for APN authentication")
    password: str | None = Field(None, description="New password for APN authentication")
    pin: str | None = Field(None, description="New SIM PIN code", min_length=4, max_length=8)


class WiFiBandChannels(BaseModel):
    """WiFi band with available channels."""

    band: str = Field(..., description="Band name (2.4GHz, 5GHz, 6GHz)")
    channels: list[int] = Field(..., description="Available channels in this band")


class WiFiCapabilities(BaseModel):
    """WiFi device capabilities model."""

    bands: list[WiFiBandChannels] = Field(..., description="Supported bands and their channels")
    channel_widths: list[str] = Field(..., description="Supported channel widths (20, 40, 80, 160)")


class WiFiScanAP(BaseModel):
    """One WiFi access point from an nmcli device wifi scan."""

    ssid: str = Field(..., description="Network name (SSID), or placeholder for hidden networks")
    signal: int = Field(..., description="Signal strength 0–100")
    security: str = Field(..., description="Security flags as reported by nmcli (e.g. WPA2, -- for open)")
    active: bool = Field(..., description="Whether this AP is the current connection")
    channel: int | None = Field(None, description="WiFi channel if known")
    bssid: str | None = Field(None, description="BSSID if known")


def _split_nmcli_terse_line(line: str) -> list[str]:
    """Split an nmcli -t line on ':' with '\\:' as a literal colon in values."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    parts.append("".join(current))
    return parts


async def _connection_interface_name(connection_name: str) -> str | None:
    """Return connection.interface-name if the connection exists, else None."""
    try:
        props = await get_nmcli_connection_properties(connection_name, show_secrets=False)
        iface = (props.get("connection.interface-name") or "").strip()
        return iface or None
    except HTTPException as e:
        if e.status_code == 404:
            return None
        raise


async def _get_wifi_interface_for_scan() -> str:
    """Pick the WiFi device for `nmcli device wifi list` / rescan."""
    iface = await _connection_interface_name("station")
    if iface:
        return iface
    iface = await _connection_interface_name("hotspot")
    if iface:
        return iface
    result = await run_nmcli_command(["-t", "-f", "DEVICE,TYPE", "device", "status"], check=False)
    if result.returncode != 0:
        raise HTTPException(
            status_code=503,
            detail="Could not list NetworkManager devices for WiFi scan",
        )
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 2:
            continue
        dev, typ = parts[0], parts[1]
        if typ == "wifi":
            return dev
    raise HTTPException(
        status_code=503,
        detail="No WiFi interface found. Configure hotspot or station in NetworkManager first.",
    )


def _parse_wifi_scan_lines(stdout: str) -> list[WiFiScanAP]:
    """Parse `nmcli -t -f SSID,SIGNAL,SECURITY,ACTIVE,CHAN,BSSID device wifi list` output."""
    fields = ("SSID", "SIGNAL", "SECURITY", "ACTIVE", "CHAN", "BSSID")
    n = len(fields)
    networks: list[WiFiScanAP] = []
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        parts = _split_nmcli_terse_line(line)
        if len(parts) < n:
            continue
        raw_ssid = (parts[0] or "").strip()
        if not raw_ssid or raw_ssid == "--":
            ssid = "Hidden network"
        else:
            ssid = raw_ssid
        try:
            signal = int(parts[1].strip())
        except ValueError:
            signal = 0
        security = (parts[2] or "").strip() or "--"
        active_raw = (parts[3] or "").strip().lower()
        active = active_raw in ("yes", "true", "*", "1")
        channel: int | None = None
        ch = (parts[4] or "").strip()
        if ch.isdigit():
            channel = int(ch)
        bssid = (parts[5] or "").strip()
        if bssid == "--":
            bssid = None
        networks.append(
            WiFiScanAP(
                ssid=ssid,
                signal=max(0, min(100, signal)),
                security=security,
                active=active,
                channel=channel,
                bssid=bssid,
            )
        )
    return networks


def _merge_scan_by_ssid(networks: list[WiFiScanAP]) -> list[WiFiScanAP]:
    """Keep one row per SSID (strongest signal, active OR across BSSIDs)."""
    merged: dict[str, WiFiScanAP] = {}
    for ap in networks:
        key = ap.ssid
        if key not in merged:
            merged[key] = ap
            continue
        prev = merged[key]
        active = prev.active or ap.active
        if ap.signal > prev.signal:
            merged[key] = ap.model_copy(update={"active": active})
        else:
            merged[key] = prev.model_copy(update={"active": active})
    return sorted(merged.values(), key=lambda x: (-x.signal, x.ssid.lower()))


async def run_nmcli_command(args: list[str], check: bool = True, timeout: float = 10) -> subprocess.CompletedProcess:
    """Run nmcli command with proper error handling.

    Args:
        args: Command arguments to pass to nmcli
        check: Whether to check return code and raise exception on failure
        timeout: Subprocess timeout in seconds

    Returns:
        CompletedProcess instance

    Raises:
        HTTPException: If command fails and check=True
    """
    try:
        cmd = ["nmcli"] + args
        result = await run_subprocess_async(cmd, capture_output=True, text=True, check=check, timeout=timeout)
        return result
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500, detail=f"NetworkManager command failed: {e.stderr.strip() or e.stdout.strip() or str(e)}"
        )
    except (TimeoutError, subprocess.TimeoutExpired):
        raise HTTPException(status_code=504, detail="NetworkManager command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="NetworkManager (nmcli) not available")


async def nmcli_connection_modify(
    connection: str, set_props: dict[str, str], unset_props: list[str] | None = None
) -> subprocess.CompletedProcess:
    """`nmcli connection modify <connection> (<prop> <value>)... [-- (-<prop>)...]`

    Each property/value pair is passed as two separate argv elements (never
    joined into one string), so a value can't be mistaken for a new
    property/flag. `pi` has direct, polkit-authorized NetworkManager access
    (see docs/security.md's "Network management" section), so this runs
    unprivileged - there's no root boundary left to validate property names
    against; the properties set here are hardcoded by this router, never
    caller-supplied.
    """
    args = ["connection", "modify", connection]
    for key, value in set_props.items():
        args += [key, value]
    if unset_props:
        args.append("--")
        args += [f"-{key}" for key in unset_props]
    return await run_nmcli_command(args, check=False)


async def _get_hotspot_interface_phy() -> int | None:
    """Get the phy index of the interface used by the hotspot connection.

    Returns:
        phy index (e.g., 0) or None if hotspot is not configured or interface unavailable
    """
    try:
        props = await get_nmcli_connection_properties("hotspot", show_secrets=False)
        interface = props.get("connection.interface-name", "").strip()
        if not interface:
            return None

        result = await run_subprocess_async(["iw", "dev", interface, "info"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None

        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("wiphy "):
                return int(line.split()[1])
        return None
    except (HTTPException, ValueError, IndexError):
        return None


def _parse_iw_phy_output(output: str) -> WiFiCapabilities:
    """Parse iw phy output into WiFiCapabilities."""
    bands_data = []
    channel_widths = set()
    current_band = None
    current_channels = []

    for line in output.split("\n"):
        line = line.strip()

        if line.startswith("Band "):
            if current_band and current_channels:
                bands_data.append(WiFiBandChannels(band=current_band, channels=current_channels))

            current_channels = []
            band_num = line.split()[1].rstrip(":")
            if band_num == "1":
                current_band = "2.4GHz"
            elif band_num == "2":
                current_band = "5GHz"
            elif band_num == "3":
                current_band = "6GHz"
            else:
                current_band = None

        elif line.startswith("* ") and "MHz [" in line and current_band:
            if "(disabled)" in line:
                continue
            try:
                channel_start = line.index("[") + 1
                channel_end = line.index("]", channel_start)
                channel = int(line[channel_start:channel_end])
                current_channels.append(channel)
            except (ValueError, IndexError):
                continue

        elif "HT20" in line or "HT40" in line or "VHT80" in line or "VHT160" in line:
            if "HT20" in line or "HT40" in line:
                channel_widths.add("20")
            if "HT40" in line:
                channel_widths.add("40")
            if "VHT80" in line or "80MHz" in line:
                channel_widths.add("80")
            if "VHT160" in line or "160MHz" in line:
                channel_widths.add("160")

    if current_band and current_channels:
        bands_data.append(WiFiBandChannels(band=current_band, channels=current_channels))

    if not channel_widths:
        channel_widths.add("20")
    sorted_widths = sorted(channel_widths, key=lambda x: int(x))

    return WiFiCapabilities(bands=bands_data, channel_widths=sorted_widths)


async def parse_wifi_capabilities() -> WiFiCapabilities:
    """Parse WiFi capabilities from the hotspot interface only.

    Queries only the interface configured for the hotspot connection to avoid duplicate
    bands when multiple WiFi interfaces are present.

    Returns:
        WiFiCapabilities with supported bands, channels, and widths

    Raises:
        HTTPException: If command fails or capabilities cannot be determined
    """
    try:
        phy = await _get_hotspot_interface_phy()
        if phy is None:
            raise HTTPException(
                status_code=503,
                detail="Hotspot not configured or interface unavailable. Configure hotspot first.",
            )

        result = await run_subprocess_async(
            ["iw", "phy", f"phy{phy}", "info"], capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            raise HTTPException(status_code=503, detail="WiFi device not available or iw command failed")

        capabilities = _parse_iw_phy_output(result.stdout)
        if not capabilities.bands:
            raise HTTPException(status_code=503, detail="No WiFi bands detected")

        return capabilities

    except (TimeoutError, subprocess.TimeoutExpired):
        raise HTTPException(status_code=504, detail="WiFi capability detection timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="iw command not available")
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to parse WiFi capabilities", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to parse WiFi capabilities: {e!s}")


async def get_nmcli_connection_properties(connection_name: str, show_secrets: bool = False) -> dict[str, str]:
    """Get all properties from a NetworkManager connection.

    Args:
        connection_name: Name of the connection
        show_secrets: Whether to reveal hidden/secret values (e.g., passwords)

    Returns:
        Dictionary mapping property names to their values

    Raises:
        HTTPException: If connection doesn't exist or command fails
    """
    try:
        args = ["-t", "connection", "show"]
        if show_secrets:
            args.append("--show-secrets")
        args.append(connection_name)

        result = await run_nmcli_command(args, check=False)

        if result.returncode != 0:
            raise HTTPException(status_code=404, detail=f"Connection '{connection_name}' not found")

        # Parse output - each line is "property:value"
        properties = {}
        for line in result.stdout.strip().split("\n"):
            if not line or ":" not in line:
                continue

            # Split on first colon to separate property from value
            prop, value = line.split(":", 1)
            value = value.strip()

            # Store only non-empty values (skip "--", "<hidden>", and empty strings)
            if value and value != "--" and value != "<hidden>":
                properties[prop.strip()] = value

        return properties

    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"Failed to get properties from connection {connection_name}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get properties from connection {connection_name}: {e!s}"
        )


async def _try_get_hotspot_ssid() -> str | None:
    """SSID of the device's hotspot connection, if any (to omit from station scan list)."""
    try:
        props = await get_nmcli_connection_properties("hotspot", show_secrets=False)
        s = (props.get("802-11-wireless.ssid") or "").strip()
        return s or None
    except HTTPException as e:
        if e.status_code == 404:
            return None
        raise


async def get_device_ipv4_address(device: str) -> str | None:
    """Get IPv4 address for a network device using NetworkManager.

    Args:
        device: Network device name (e.g., eth0, wlan0)

    Returns:
        IPv4 address with CIDR notation or None if not found
    """
    if not device:
        return None

    try:
        result = await run_nmcli_command(["-t", "-f", "IP4.ADDRESS", "device", "show", device], check=False)

        if result.returncode == 0 and result.stdout:
            # Parse output like: "IP4.ADDRESS[1]:192.168.178.147/24"
            for line in result.stdout.strip().split("\n"):
                if line.startswith("IP4.ADDRESS[") and ":" in line:
                    # Extract the IP address part after the colon
                    address = line.split(":", 1)[1].strip()
                    if address:
                        return address

        return None
    except Exception:
        logger.debug(f"Could not get IPv4 address of {device}", exc_info=True)
        return None


@router.get("/wifi/capabilities", summary="Get WiFi device capabilities", response_model=WiFiCapabilities)
async def get_wifi_capabilities() -> WiFiCapabilities:
    """Get WiFi capabilities of the hotspot interface (bands, channels, channel widths).

    Queries only the interface configured for the hotspot connection to avoid duplicate
    bands when multiple WiFi interfaces are present.

    Returns:
        WiFi capabilities with bands, channels, and widths
    """
    try:
        return await parse_wifi_capabilities()
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to get WiFi capabilities", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get WiFi capabilities: {e!s}")


@router.get("/wifi/scan", summary="Scan for WiFi networks", response_model=list[WiFiScanAP])
async def wifi_scan(
    rescan: bool = Query(True, description="Request a fresh scan before listing (slower but current)"),
) -> list[WiFiScanAP]:
    """List visible WiFi networks (same source as `nmcli device wifi list`) for station setup guidance."""
    try:
        iface = await _get_wifi_interface_for_scan()
        if rescan:
            if not config_loader.is_server_mode():
                # Best-effort: a rescan failure shouldn't block returning the
                # last-known list.
                await run_nmcli_command(["device", "wifi", "rescan", "ifname", iface], check=False, timeout=15)
            await asyncio.sleep(4)
        result = await run_nmcli_command(
            [
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY,ACTIVE,CHAN,BSSID",
                "device",
                "wifi",
                "list",
                "ifname",
                iface,
            ],
            check=True,
            timeout=45,
        )
        networks = _parse_wifi_scan_lines(result.stdout)
        merged = _merge_scan_by_ssid(networks)
        hotspot_ssid = await _try_get_hotspot_ssid()
        if hotspot_ssid:
            merged = [ap for ap in merged if ap.ssid != hotspot_ssid]
        return merged
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("WiFi scan failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"WiFi scan failed: {e!s}")


@router.get("/connections", summary="List all network connections", response_model=list[ConnectionInfo])
async def get_connections() -> list[ConnectionInfo]:
    """Get list of all NetworkManager connections with their status and IPv4 addresses.

    Returns:
        List of connection information including name, type, device, state, and IPv4 address
    """
    try:
        # Use terse format for easy parsing
        result = await run_nmcli_command(["-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show"])

        connections = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            # Parse the colon-separated output
            parts = line.split(":")
            if len(parts) >= 4:
                device = parts[2] if parts[2] else ""
                ipv4_address = await get_device_ipv4_address(device) if device else None

                connections.append(
                    ConnectionInfo(
                        name=parts[0], type=parts[1], device=device, state=parts[3], ipv4_address=ipv4_address
                    )
                )

        return connections

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to list network connections", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list network connections: {e!s}")


@router.get("/connections/station", summary="Get WiFi station configuration", response_model=WiFiStationConfig)
async def get_wifi_station_config() -> WiFiStationConfig:
    """Get WiFi client SSID and password from the NetworkManager station connection."""
    try:
        props = await get_nmcli_connection_properties("station", show_secrets=True)
        ssid = props.get("802-11-wireless.ssid") or ""
        password = props.get("802-11-wireless-security.psk", "") or ""
        autoconnect_raw = (props.get("connection.autoconnect") or "yes").lower()
        autoconnect = autoconnect_raw in ("yes", "true", "1")
        return WiFiStationConfig(ssid=ssid, password=password, autoconnect=autoconnect)
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to get WiFi station configuration", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get WiFi station configuration: {e!s}")


@router.patch("/connections/station", summary="Update WiFi station configuration")
async def update_wifi_station_config(update: WiFiStationUpdate) -> dict[str, Any]:
    """Update WiFi client SSID and/or WPA2 password on the NetworkManager station connection."""
    try:
        _ensure_tracker_mode()

        if update.ssid is None and update.password is None and update.autoconnect is None:
            raise HTTPException(status_code=400, detail="At least one field must be provided")

        if update.password is not None:
            if len(update.password) < 8:
                raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
            if len(update.password) > 63:
                raise HTTPException(status_code=400, detail="Password must be 63 characters or less")

        ssid_value: str | None = None
        if update.ssid is not None:
            ssid_value = update.ssid.strip()
            if not ssid_value:
                raise HTTPException(status_code=400, detail="SSID cannot be empty")
            if len(ssid_value) > 32:
                raise HTTPException(status_code=400, detail="SSID must be 32 characters or less")

        set_props: dict[str, str] = {}
        if ssid_value is not None:
            set_props["802-11-wireless.ssid"] = ssid_value
        if update.password is not None:
            set_props["802-11-wireless-security.key-mgmt"] = "wpa-psk"
            set_props["802-11-wireless-security.psk"] = update.password
        if update.autoconnect is not None:
            set_props["connection.autoconnect"] = "yes" if update.autoconnect else "no"

        if set_props:
            result = await nmcli_connection_modify("station", set_props)
            if result.returncode != 0:
                error_msg = (result.stderr or result.stdout or "unknown error").strip()
                raise HTTPException(status_code=500, detail=f"Failed to update WiFi station configuration: {error_msg}")

        updated_config = await get_wifi_station_config()
        return {
            "message": "WiFi station configuration updated successfully (changes saved, restart connection to apply)",
            "config": updated_config,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to update WiFi station configuration", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update WiFi station configuration: {e!s}")


@router.get("/connections/hotspot", summary="Get hotspot configuration", response_model=HotspotConfig)
async def get_hotspot_config() -> HotspotConfig:
    """Get current hotspot SSID, password, and expert settings from NetworkManager connection.

    Returns:
        Current hotspot configuration including band, channel, channel_width, and hidden
    """
    try:
        # Get all properties in a single call (with secrets revealed)
        props = await get_nmcli_connection_properties("hotspot", show_secrets=True)

        # Get SSID
        ssid = props.get("802-11-wireless.ssid")
        if not ssid:
            raise HTTPException(status_code=404, detail="Hotspot SSID not found in connection")

        # Get password
        password = props.get("802-11-wireless-security.psk", "")

        # Get band (returns "a", "bg", or None)
        band_value = props.get("802-11-wireless.band")
        band = None
        if band_value == "a":
            band = "5GHz"
        elif band_value == "bg":
            band = "2.4GHz"
        # If band_value is None or unrecognized, band stays None (auto)

        # Get channel
        channel_str = props.get("802-11-wireless.channel")
        channel = int(channel_str) if channel_str and channel_str.isdigit() else None

        # Get hidden SSID setting
        hidden_str = props.get("802-11-wireless.hidden")
        hidden = hidden_str == "yes" if hidden_str else None

        # Get channel width (0 means auto/unset)
        channel_width_str = props.get("802-11-wireless.channel-width")
        channel_width = None
        if channel_width_str and channel_width_str != "0":
            channel_width = channel_width_str

        return HotspotConfig(
            ssid=ssid, password=password, band=band, channel=channel, channel_width=channel_width, hidden=hidden
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to get hotspot configuration", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get hotspot configuration: {e!s}")


@router.patch("/connections/hotspot", summary="Update hotspot configuration")
async def update_hotspot_config(update: HotspotUpdate) -> dict[str, Any]:
    """Update hotspot password and expert settings.

    The SSID is configured from the hostname on boot and cannot be changed here.
    Expert settings include band, channel, channel width, and hidden SSID.

    Args:
        update: Hotspot update with optional password and expert settings

    Returns:
        Success message with updated configuration
    """
    try:
        _ensure_tracker_mode()

        # Check that at least one field is provided
        if not any(
            [
                update.password is not None,
                update.band is not None,
                update.channel is not None,
                update.channel_width is not None,
                update.hidden is not None,
            ]
        ):
            raise HTTPException(status_code=400, detail="At least one field must be provided")

        # Validate password if provided
        if update.password is not None:
            if len(update.password) < 8:
                raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
            if len(update.password) > 63:
                raise HTTPException(status_code=400, detail="Password must be 63 characters or less")

        # Validate band and channel if provided
        if update.band is not None or update.channel is not None:
            try:
                capabilities = await parse_wifi_capabilities()

                # Validate band
                if update.band is not None:
                    valid_bands = [b.band for b in capabilities.bands]
                    if update.band not in valid_bands:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid band '{update.band}'. Valid options: {', '.join(valid_bands)}",
                        )

                # Validate channel against band
                if update.channel is not None:
                    # Need to know which band to validate against
                    if update.band is not None:
                        target_band = update.band
                    else:
                        # Use current band from config
                        current_config = await get_hotspot_config()
                        target_band = current_config.band or "2.4GHz"  # Default to 2.4GHz

                    # Find the band and its valid channels
                    band_channels = None
                    for b in capabilities.bands:
                        if b.band == target_band:
                            band_channels = b.channels
                            break

                    if band_channels and update.channel not in band_channels:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid channel {update.channel} for band {target_band}. Valid channels: {', '.join(map(str, band_channels))}",
                        )
            except HTTPException:
                raise
            except Exception as e:
                # If capabilities check fails, log but don't block the update
                logger.warning(f"Could not validate WiFi capabilities: {e}", exc_info=True)

        # Validate channel width if provided
        if update.channel_width is not None:
            valid_widths = ["20", "40", "80", "160"]
            if update.channel_width not in valid_widths:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid channel width '{update.channel_width}'. Valid options: {', '.join(valid_widths)}",
                )

        # Build the set of properties to set/unset for a single nm-modify call
        set_props: dict[str, str] = {}
        unset_props: list[str] = []

        # Update password if provided
        if update.password is not None:
            set_props["802-11-wireless-security.psk"] = update.password

        # Update band if provided
        # NetworkManager expects "a" for 5GHz, "bg" for 2.4GHz
        if update.band is not None:
            if update.band == "5GHz":
                band_value = "a"
            elif update.band == "2.4GHz":
                band_value = "bg"
            else:
                band_value = ""  # Auto/unset
            set_props["802-11-wireless.band"] = band_value
        elif "band" in update.__fields_set__:  # Explicitly set to None (auto)
            unset_props.append("802-11-wireless.band")

        # Update channel if provided
        if update.channel is not None:
            set_props["802-11-wireless.channel"] = str(update.channel)
        elif "channel" in update.__fields_set__:  # Explicitly set to None (auto)
            set_props["802-11-wireless.channel"] = ""

        # Update hidden if provided
        if update.hidden is not None:
            set_props["802-11-wireless.hidden"] = "yes" if update.hidden else "no"
        elif "hidden" in update.__fields_set__:  # Explicitly set to None (default)
            set_props["802-11-wireless.hidden"] = "no"

        # Update channel width if provided
        if update.channel_width is not None:
            set_props["802-11-wireless.channel-width"] = update.channel_width
        elif "channel_width" in update.__fields_set__:  # Explicitly set to None (unset/auto)
            set_props["802-11-wireless.channel-width"] = "0"

        # Execute all modifications in a single nmcli connection modify call
        if set_props or unset_props:
            result = await nmcli_connection_modify("hotspot", set_props, unset_props)
            if result.returncode != 0:
                error_msg = (result.stderr or result.stdout or "unknown error").strip()
                raise HTTPException(status_code=500, detail=f"Failed to update hotspot configuration: {error_msg}")

        # Get updated configuration
        updated_config = await get_hotspot_config()

        return {
            "message": "Hotspot configuration updated successfully (changes saved, restart connection to apply)",
            "config": updated_config,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to update hotspot configuration", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update hotspot configuration: {e!s}")


@router.get("/connections/cellular", summary="Get cellular configuration", response_model=CellularConfig)
async def get_cellular_config() -> CellularConfig:
    """Get current cellular/GSM configuration from NetworkManager connection including APN, username, password, and PIN.

    Returns:
        Current cellular configuration
    """
    try:
        # Get all properties in a single call (with secrets revealed)
        props = await get_nmcli_connection_properties("cellular", show_secrets=True)

        # Extract GSM properties
        apn = props.get("gsm.apn")
        username = props.get("gsm.username")
        password = props.get("gsm.password")
        pin = props.get("gsm.pin")

        return CellularConfig(apn=apn, username=username, password=password, pin=pin)

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to get cellular configuration", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get cellular configuration: {e!s}")


@router.patch("/connections/cellular", summary="Update cellular configuration")
async def update_cellular_config(update: CellularUpdate) -> dict[str, Any]:
    """Update cellular/GSM configuration.

    Allows updating APN, username, password, and/or PIN for the cellular connection.
    All fields are optional - only provided fields will be updated.

    Args:
        update: Cellular update with optional fields

    Returns:
        Success message with updated configuration
    """
    try:
        _ensure_tracker_mode()

        # Validate PIN if provided (not empty/null)
        if update.pin:
            # PIN should be 4-8 digits
            if not update.pin.isdigit():
                raise HTTPException(status_code=400, detail="PIN must contain only digits")
            if len(update.pin) < 4 or len(update.pin) > 8:
                raise HTTPException(status_code=400, detail="PIN must be 4-8 digits")

        # Frontend always sends all 4 fields (either with values or null)
        # Treat None/null as empty string to clear the field for APN and username
        set_props: dict[str, str] = {
            "gsm.apn": update.apn or "",
            "gsm.username": update.username or "",
        }

        # Track fields that need to be removed from netplan (nmcli can't unset them)
        fields_to_remove_from_netplan: list[str] = []

        # Handle password field
        if update.password:
            # Set password via nmcli
            set_props["gsm.password"] = update.password
        else:
            # Remove password by editing netplan YAML file
            fields_to_remove_from_netplan.append("password")

        # Handle pin field
        if update.pin:
            # Set pin via nmcli
            set_props["gsm.pin"] = update.pin
        else:
            # Remove pin by editing netplan YAML file
            fields_to_remove_from_netplan.append("pin")

        # Remove fields from netplan if needed - this is the one op still
        # routed through root (nmcli can't unset these itself, and
        # /etc/netplan is root-owned; see app.privileged.ops.unset_gsm_fields).
        # Best-effort: a failure here shouldn't block the nmcli-side update
        # below.
        if fields_to_remove_from_netplan:
            try:
                await unset_gsm_fields("cellular", fields_to_remove_from_netplan)
            except PrivilegedCallError as e:
                logger.warning(f"Failed to remove GSM fields from netplan: {e}")

        # Execute all modifications in a single nmcli connection modify call
        result = await nmcli_connection_modify("cellular", set_props)
        if result.returncode != 0:
            error_msg = (result.stderr or result.stdout or "unknown error").strip()
            raise HTTPException(status_code=500, detail=f"Failed to update cellular configuration: {error_msg}")

        # Get updated configuration
        updated_config = await get_cellular_config()

        return {
            "message": "Cellular configuration updated successfully (changes saved, restart connection to apply)",
            "config": updated_config,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to update cellular configuration", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update cellular configuration: {e!s}")


@router.post("/connections/{connection_name}/up", summary="Bring a connection up")
async def bring_connection_up(connection_name: str) -> dict[str, Any]:
    """Bring a network connection up using nmcli connection up.

    Args:
        connection_name: Name of the connection to bring up - must be one of
            app.privileged.ops.ALLOWED_CONNECTIONS. This isn't a privilege
            boundary any more (pi can run `nmcli connection up <anything>`
            directly), just a clean 400 for an unknown name instead of
            nmcli's own "connection not found" error.

    Returns:
        Success message with connection information
    """
    if connection_name not in ALLOWED_CONNECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown connection: {connection_name}")

    try:
        _ensure_tracker_mode()

        result = await run_nmcli_command(["connection", "up", connection_name], check=False, timeout=30)

        if result.returncode == 0:
            return {
                "message": f"Connection '{connection_name}' activated successfully",
                "connection_name": connection_name,
            }
        else:
            # Parse error message from nmcli
            error_msg = (result.stderr or "").strip() or (result.stdout or "").strip() or "Unknown error"

            # Check if already active
            if "already active" in error_msg.lower() or "is already active" in error_msg.lower():
                return {
                    "message": f"Connection '{connection_name}' is already active",
                    "connection_name": connection_name,
                    "already_active": True,
                }

            raise HTTPException(status_code=500, detail=f"Failed to activate connection: {error_msg}")

    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Failed to bring connection up", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to bring connection up: {e!s}")


@router.get("/modem", summary="Get modem information", response_model=ModemInfo | None)
async def get_modem_info() -> ModemInfo | None:
    """Get information about the first available modem from ModemManager.

    Returns:
        Modem information including basic info, signal strength, and connection status, or None if no modem found
    """
    try:
        return await modem.read_modem()
    except modem.MmcliTimeout as e:
        raise HTTPException(status_code=504, detail=str(e))
    except modem.MmcliUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        # Log the error but don't raise - return None for graceful degradation
        logger.exception("Error getting modem information")
        return None
