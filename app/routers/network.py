"""Network management router for NetworkManager connections."""

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
