"""Network management router for NetworkManager connections."""

import json
import subprocess
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/network", tags=["network"])


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


class HotspotUpdate(BaseModel):
    """Hotspot update request model."""

    password: str = Field(..., description="New hotspot password", min_length=8, max_length=63)


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
    ip_addresses: List[str] | None = Field(None, description="IP addresses")

    # SIM info
    sim_iccid: str | None = Field(None, description="SIM ICCID")
    sim_operator: str | None = Field(None, description="SIM operator name")
    sim_imsi: str | None = Field(None, description="SIM IMSI")


def run_nmcli_command(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run nmcli command with proper error handling.

    Args:
        args: Command arguments to pass to nmcli
        check: Whether to check return code and raise exception on failure

    Returns:
        CompletedProcess instance

    Raises:
        HTTPException: If command fails and check=True
    """
    try:
        result = subprocess.run(["nmcli"] + args, capture_output=True, text=True, check=check, timeout=10)
        return result
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500, detail=f"NetworkManager command failed: {e.stderr.strip() or e.stdout.strip() or str(e)}"
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="NetworkManager command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="NetworkManager (nmcli) not available")


def run_mmcli_command(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run mmcli command with proper error handling.

    Args:
        args: Command arguments to pass to mmcli
        check: Whether to check return code and raise exception on failure

    Returns:
        CompletedProcess instance

    Raises:
        HTTPException: If command fails and check=True
    """
    try:
        result = subprocess.run(["mmcli"] + args, capture_output=True, text=True, check=check, timeout=10)
        return result
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500, detail=f"ModemManager command failed: {e.stderr.strip() or e.stdout.strip() or str(e)}"
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="ModemManager command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="ModemManager (mmcli) not available")


def get_device_ipv4_address(device: str) -> str | None:
    """Get IPv4 address for a network device using NetworkManager.

    Args:
        device: Network device name (e.g., eth0, wlan0)

    Returns:
        IPv4 address with CIDR notation or None if not found
    """
    if not device:
        return None

    try:
        result = run_nmcli_command(["-t", "-f", "IP4.ADDRESS", "device", "show", device], check=False)

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
        return None


@router.get("/connections", summary="List all network connections", response_model=List[ConnectionInfo])
async def get_connections() -> List[ConnectionInfo]:
    """Get list of all NetworkManager connections with their status and IPv4 addresses.

    Returns:
        List of connection information including name, type, device, state, and IPv4 address
    """
    try:
        # Use terse format for easy parsing
        result = run_nmcli_command(["-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show"])

        connections = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            # Parse the colon-separated output
            parts = line.split(":")
            if len(parts) >= 4:
                device = parts[2] if parts[2] else ""
                ipv4_address = get_device_ipv4_address(device) if device else None

                connections.append(
                    ConnectionInfo(
                        name=parts[0], type=parts[1], device=device, state=parts[3], ipv4_address=ipv4_address
                    )
                )

        return connections

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list network connections: {str(e)}")


@router.get("/connections/hotspot", summary="Get hotspot configuration", response_model=HotspotConfig)
async def get_hotspot_config() -> HotspotConfig:
    """Get current hotspot SSID and password.

    Returns:
        Current hotspot configuration
    """
    try:
        # Get hotspot SSID
        result_ssid = run_nmcli_command(["-t", "-f", "802-11-wireless.ssid", "connection", "show", "hotspot"])
        ssid_line = result_ssid.stdout.strip()
        # Output format is "802-11-wireless.ssid:value"
        ssid = ssid_line.split(":", 1)[1] if ":" in ssid_line else ""

        # Get hotspot password (requires --show-secrets to reveal the actual password)
        result_psk = run_nmcli_command(
            ["-t", "-f", "802-11-wireless-security.psk", "connection", "show", "hotspot", "--show-secrets"]
        )
        psk_line = result_psk.stdout.strip()
        # Output format is "802-11-wireless-security.psk:value"
        password = psk_line.split(":", 1)[1] if ":" in psk_line else ""

        if not ssid:
            raise HTTPException(status_code=404, detail="Hotspot SSID not found")

        return HotspotConfig(ssid=ssid, password=password)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get hotspot configuration: {str(e)}")


@router.patch("/connections/hotspot", summary="Update hotspot configuration")
async def update_hotspot_config(update: HotspotUpdate) -> Dict[str, Any]:
    """Update hotspot password.

    Only the hotspot password can be modified. The SSID is configured from the hostname on boot.
    Other system connections are read-only.

    Args:
        update: Hotspot update with new password

    Returns:
        Success message with updated configuration
    """
    try:
        # Validate password
        if len(update.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        if len(update.password) > 63:
            raise HTTPException(status_code=400, detail="Password must be 63 characters or less")

        # Update password
        run_nmcli_command(["connection", "modify", "hotspot", "802-11-wireless-security.psk", update.password])

        # Get updated configuration
        updated_config = await get_hotspot_config()

        return {"message": "Hotspot password updated successfully", "config": updated_config}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update hotspot password: {str(e)}")


@router.get("/connections/cellular", summary="Get cellular configuration", response_model=CellularConfig)
async def get_cellular_config() -> CellularConfig:
    """Get current cellular/GSM configuration including APN, username, password, and PIN.

    Returns:
        Current cellular configuration
    """
    try:
        # Get cellular configuration with secrets
        result = run_nmcli_command(
            [
                "-t",
                "-f",
                "gsm.apn,gsm.username,gsm.password,gsm.pin",
                "connection",
                "show",
                "cellular",
                "--show-secrets",
            ]
        )

        # Parse the output - format is "field:value" per line
        config = {"apn": None, "username": None, "password": None, "pin": None}

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            if ":" in line:
                key, value = line.split(":", 1)
                value = value.strip()

                # Empty values or special markers should be treated as None
                if not value or value in ["--", "<hidden>"]:
                    value = None

                if key == "gsm.apn":
                    config["apn"] = value
                elif key == "gsm.username":
                    config["username"] = value
                elif key == "gsm.password":
                    config["password"] = value
                elif key == "gsm.pin":
                    config["pin"] = value

        return CellularConfig(**config)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get cellular configuration: {str(e)}")


@router.patch("/connections/cellular", summary="Update cellular configuration")
async def update_cellular_config(update: CellularUpdate) -> Dict[str, Any]:
    """Update cellular/GSM configuration.

    Allows updating APN, username, password, and/or PIN for the cellular connection.
    All fields are optional - only provided fields will be updated.

    Args:
        update: Cellular update with optional fields

    Returns:
        Success message with updated configuration
    """
    try:
        # Check if at least one field is provided
        if not any(
            [update.apn is not None, update.username is not None, update.password is not None, update.pin is not None]
        ):
            raise HTTPException(status_code=400, detail="At least one field must be provided")

        # Validate PIN if provided
        if update.pin is not None:
            # PIN should be 4-8 digits
            if update.pin and not update.pin.isdigit():
                raise HTTPException(status_code=400, detail="PIN must contain only digits")
            if update.pin and (len(update.pin) < 4 or len(update.pin) > 8):
                raise HTTPException(status_code=400, detail="PIN must be 4-8 digits")

        # Update APN if provided
        if update.apn is not None:
            value = update.apn if update.apn else ""
            run_nmcli_command(["connection", "modify", "cellular", "gsm.apn", value])

        # Update username if provided
        if update.username is not None:
            value = update.username if update.username else ""
            run_nmcli_command(["connection", "modify", "cellular", "gsm.username", value])

        # Update password if provided
        if update.password is not None:
            value = update.password if update.password else ""
            run_nmcli_command(["connection", "modify", "cellular", "gsm.password", value])

        # Update PIN if provided
        if update.pin is not None:
            value = update.pin if update.pin else ""
            run_nmcli_command(["connection", "modify", "cellular", "gsm.pin", value])

        # Get updated configuration
        updated_config = await get_cellular_config()

        return {"message": "Cellular configuration updated successfully", "config": updated_config}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update cellular configuration: {str(e)}")


@router.get("/modem", summary="Get modem information", response_model=ModemInfo | None)
async def get_modem_info() -> ModemInfo | None:
    """Get information about the first available modem from ModemManager.

    Returns:
        Modem information including basic info, signal strength, and connection status, or None if no modem found
    """
    try:
        # First, list available modems
        list_result = run_mmcli_command(["-L", "--output-json"], check=False)

        if list_result.returncode != 0:
            # ModemManager not available or no modems
            return None

        # Parse the modem list
        try:
            modem_list = json.loads(list_result.stdout)
        except json.JSONDecodeError:
            return None

        # Extract modem IDs from the list
        # Format is like: {"modem-list": ["/org/freedesktop/ModemManager1/Modem/0", ...]}
        modem_paths = modem_list.get("modem-list", [])
        if not modem_paths:
            # No modems found
            return None

        # Get the first modem ID from the path (e.g., "/org/freedesktop/ModemManager1/Modem/0" -> "0")
        first_modem_path = modem_paths[0]
        modem_id = first_modem_path.split("/")[-1]

        # Get detailed modem information
        detail_result = run_mmcli_command(["-m", modem_id, "--output-json"], check=False)

        if detail_result.returncode != 0:
            return None

        # Parse modem details
        try:
            modem_data = json.loads(detail_result.stdout)
        except json.JSONDecodeError:
            return None

        # Extract relevant information from the modem data
        modem = modem_data.get("modem", {})
        generic = modem.get("generic", {})
        signal = modem.get("signal", {})
        three_gpp = modem.get("3gpp", {})
        sim = modem.get("sim", {})
        bearer_paths = modem.get("bearers", [])

        # Extract basic info
        manufacturer = generic.get("manufacturer")
        model = generic.get("model")
        hardware_revision = generic.get("revision")
        equipment_id = generic.get("equipment-identifier")  # IMEI

        # Extract signal info
        signal_strength_percent = None
        if generic.get("signal-quality"):
            signal_strength_percent = generic["signal-quality"].get("value")

        # Get signal strength in dBm if available
        signal_strength_dbm = None
        if signal and "lte" in signal:
            signal_strength_dbm = signal["lte"].get("rssi")
        elif signal and "cdma1x" in signal:
            signal_strength_dbm = signal["cdma1x"].get("rssi")
        elif signal and "evdo" in signal:
            signal_strength_dbm = signal["evdo"].get("rssi")
        elif signal and "gsm" in signal:
            signal_strength_dbm = signal["gsm"].get("rssi")
        elif signal and "umts" in signal:
            signal_strength_dbm = signal["umts"].get("rssi")

        # Extract access technology
        access_tech = generic.get("access-technologies", [])
        access_technology = access_tech[0] if access_tech else None

        # Extract connection info
        state = generic.get("state")
        operator_name = three_gpp.get("operator-name")

        # Extract IP addresses from bearers
        ip_addresses = []
        for bearer_path in bearer_paths:
            bearer_id = bearer_path.split("/")[-1]
            bearer_result = run_mmcli_command(["-b", bearer_id, "--output-json"], check=False)
            if bearer_result.returncode == 0:
                try:
                    bearer_data = json.loads(bearer_result.stdout)
                    bearer_info = bearer_data.get("bearer", {})
                    ipv4_config = bearer_info.get("ipv4-config", {})
                    ipv6_config = bearer_info.get("ipv6-config", {})

                    if ipv4_config.get("address"):
                        ip_addresses.append(ipv4_config["address"])
                    if ipv6_config.get("address"):
                        ip_addresses.append(ipv6_config["address"])
                except json.JSONDecodeError:
                    pass

        # Extract SIM info
        sim_path = generic.get("sim")
        sim_iccid = None
        sim_operator = None
        sim_imsi = None

        if sim_path:
            sim_id = sim_path.split("/")[-1]
            sim_result = run_mmcli_command(["-i", sim_id, "--output-json"], check=False)
            if sim_result.returncode == 0:
                try:
                    sim_data = json.loads(sim_result.stdout)
                    sim_info = sim_data.get("sim", {})
                    sim_properties = sim_info.get("properties", {})
                    sim_iccid = sim_properties.get("iccid")
                    sim_operator = sim_properties.get("operator-name")
                    sim_imsi = sim_properties.get("imsi")
                except json.JSONDecodeError:
                    pass

        return ModemInfo(
            manufacturer=manufacturer,
            model=model,
            hardware_revision=hardware_revision,
            imei=equipment_id,
            signal_strength_dbm=signal_strength_dbm,
            signal_strength_percent=signal_strength_percent,
            access_technology=access_technology,
            state=state,
            operator_name=operator_name,
            ip_addresses=ip_addresses if ip_addresses else None,
            sim_iccid=sim_iccid,
            sim_operator=sim_operator,
            sim_imsi=sim_imsi,
        )

    except HTTPException:
        raise
    except Exception as e:
        # Log the error but don't raise - return None for graceful degradation
        print(f"Error getting modem information: {str(e)}")
        return None
