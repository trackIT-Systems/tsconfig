"""Network management router for NetworkManager connections."""

import json
import os
import subprocess
import yaml
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


class WiFiBandChannels(BaseModel):
    """WiFi band with available channels."""

    band: str = Field(..., description="Band name (2.4GHz, 5GHz, 6GHz)")
    channels: List[int] = Field(..., description="Available channels in this band")


class WiFiCapabilities(BaseModel):
    """WiFi device capabilities model."""

    bands: List[WiFiBandChannels] = Field(..., description="Supported bands and their channels")
    channel_widths: List[str] = Field(..., description="Supported channel widths (20, 40, 80, 160)")


def run_nmcli_command(args: List[str], check: bool = True, sudo: bool = False) -> subprocess.CompletedProcess:
    """Run nmcli command with proper error handling.

    Args:
        args: Command arguments to pass to nmcli
        check: Whether to check return code and raise exception on failure
        sudo: Whether to run the command with sudo privileges

    Returns:
        CompletedProcess instance

    Raises:
        HTTPException: If command fails and check=True
    """
    try:
        cmd = ["sudo", "nmcli"] + args if sudo else ["nmcli"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=10)
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


def parse_wifi_capabilities() -> WiFiCapabilities:
    """Parse WiFi capabilities from iw list command.

    Returns:
        WiFiCapabilities with supported bands, channels, and widths

    Raises:
        HTTPException: If command fails or capabilities cannot be determined
    """
    try:
        # Run iw list to get device capabilities
        result = subprocess.run(["iw", "list"], capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            raise HTTPException(status_code=503, detail="WiFi device not available or iw command failed")
        
        output = result.stdout
        bands_data = []
        channel_widths = set()
        
        # Parse bands and channels
        current_band = None
        current_channels = []
        
        for line in output.split("\n"):
            line = line.strip()
            
            # Detect band sections (e.g., "Band 1:", "Band 2:")
            if line.startswith("Band "):
                # Save previous band if exists
                if current_band and current_channels:
                    bands_data.append(WiFiBandChannels(band=current_band, channels=current_channels))
                
                current_channels = []
                # Band 1 is 2.4GHz, Band 2 is 5GHz, Band 3+ would be 6GHz
                band_num = line.split()[1].rstrip(":")
                if band_num == "1":
                    current_band = "2.4GHz"
                elif band_num == "2":
                    current_band = "5GHz"
                elif band_num == "3":
                    current_band = "6GHz"
                else:
                    current_band = None
            
            # Parse frequency/channel lines
            # Format: "* 2412.0 MHz [1] (20.0 dBm)" or "* 5180.0 MHz [36] (20.0 dBm) (no IR)"
            elif line.startswith("* ") and "MHz [" in line and current_band:
                # Check if channel is disabled
                if "(disabled)" in line:
                    continue
                
                # Extract channel number from [N]
                try:
                    channel_start = line.index("[") + 1
                    channel_end = line.index("]", channel_start)
                    channel = int(line[channel_start:channel_end])
                    current_channels.append(channel)
                except (ValueError, IndexError):
                    continue
            
            # Parse channel width capabilities
            # Format: "Capabilities: 0x1062" followed by capability flags like "HT20/HT40"
            elif "HT20" in line or "HT40" in line or "VHT80" in line or "VHT160" in line:
                if "HT20" in line or "HT40" in line:
                    channel_widths.add("20")
                if "HT40" in line:
                    channel_widths.add("40")
                if "VHT80" in line or "80MHz" in line:
                    channel_widths.add("80")
                if "VHT160" in line or "160MHz" in line:
                    channel_widths.add("160")
        
        # Save last band
        if current_band and current_channels:
            bands_data.append(WiFiBandChannels(band=current_band, channels=current_channels))
        
        # Default to 20MHz if no widths detected
        if not channel_widths:
            channel_widths.add("20")
        
        # Sort channel widths
        sorted_widths = sorted(list(channel_widths), key=lambda x: int(x))
        
        if not bands_data:
            raise HTTPException(status_code=503, detail="No WiFi bands detected")
        
        return WiFiCapabilities(bands=bands_data, channel_widths=sorted_widths)
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="WiFi capability detection timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="iw command not available")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse WiFi capabilities: {str(e)}")


NETPLAN_WIFI_FILE = "/etc/netplan/20-hotspot.yaml"
NETPLAN_CELLULAR_FILE = "/etc/netplan/60-cellular.yaml"


def read_netplan_file(file_path: str) -> Dict[str, Any]:
    """Read and parse a netplan YAML file.

    Args:
        file_path: Path to the netplan YAML file

    Returns:
        Parsed YAML configuration as dictionary

    Raises:
        HTTPException: If file cannot be read or parsed
    """
    try:
        with open(file_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Netplan file not found: {file_path}"
        )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied reading netplan file: {file_path}"
        )
    except yaml.YAMLError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse netplan file {file_path}: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading netplan file {file_path}: {str(e)}"
        )


def write_netplan_file(file_path: str, config: Dict[str, Any]) -> None:
    """Write a netplan YAML configuration to file.

    Args:
        file_path: Path to the netplan YAML file
        config: Configuration dictionary to write

    Raises:
        HTTPException: If file cannot be written
    """
    try:
        # Write config to YAML file with proper formatting
        with open(file_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        # Set proper permissions (root:root, 0600)
        os.chmod(file_path, 0o600)
        
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied writing netplan file: {file_path}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error writing netplan file {file_path}: {str(e)}"
        )


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


@router.get("/wifi/capabilities", summary="Get WiFi device capabilities", response_model=WiFiCapabilities)
async def get_wifi_capabilities() -> WiFiCapabilities:
    """Get WiFi device capabilities including supported bands, channels, and channel widths.

    Returns:
        WiFi capabilities with bands, channels, and widths
    """
    try:
        return parse_wifi_capabilities()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get WiFi capabilities: {str(e)}")


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
    """Get current hotspot SSID, password, and expert settings from netplan configuration.

    Returns:
        Current hotspot configuration including band, channel, channel_width, and hidden
    """
    try:
        # Read the wifi netplan configuration file
        config = read_netplan_file(NETPLAN_WIFI_FILE)
        
        # Navigate to the hotspot access points
        network = config.get("network", {})
        wifis = network.get("wifis", {})
        hotspot = wifis.get("hotspot", {})
        access_points = hotspot.get("access-points", {})
        
        if not access_points or not isinstance(access_points, dict):
            raise HTTPException(status_code=404, detail="Hotspot access points not found in netplan configuration")
        
        # Get the first (and typically only) SSID
        ssid_key = next(iter(access_points.keys()))
        if not ssid_key:
            raise HTTPException(status_code=404, detail="Hotspot SSID not found")
        
        # The SSID is the key itself (no need to strip quotes, YAML parser handles that)
        ssid = ssid_key
        
        # Get the access point configuration
        ap_config = access_points[ssid_key]
        password = ap_config.get("auth", {}).get("password", "")
        
        # Get expert settings
        band = ap_config.get("band", None)
        channel = ap_config.get("channel", None)
        
        # Channel width and hidden are in NetworkManager passthrough
        nm_config = ap_config.get("networkmanager", {})
        passthrough = nm_config.get("passthrough", {})
        
        # Channel width from 802-11-wireless.channel-width
        channel_width = passthrough.get("802-11-wireless.channel-width", None)
        
        # Hidden SSID from 802-11-wireless.hidden-ssid
        hidden_ssid = passthrough.get("802-11-wireless.hidden-ssid", None)
        hidden = hidden_ssid == "yes" if hidden_ssid else None

        return HotspotConfig(
            ssid=ssid, 
            password=password,
            band=band,
            channel=channel,
            channel_width=channel_width,
            hidden=hidden
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get hotspot configuration: {str(e)}")


@router.patch("/connections/hotspot", summary="Update hotspot configuration")
async def update_hotspot_config(update: HotspotUpdate) -> Dict[str, Any]:
    """Update hotspot password and expert settings.

    The SSID is configured from the hostname on boot and cannot be changed here.
    Expert settings include band, channel, channel width, and hidden SSID.

    Args:
        update: Hotspot update with optional password and expert settings

    Returns:
        Success message with updated configuration
    """
    try:
        # Check that at least one field is provided
        if not any([
            update.password is not None,
            update.band is not None,
            update.channel is not None,
            update.channel_width is not None,
            update.hidden is not None
        ]):
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
                capabilities = parse_wifi_capabilities()
                
                # Validate band
                if update.band is not None:
                    valid_bands = [b.band for b in capabilities.bands]
                    if update.band not in valid_bands:
                        raise HTTPException(
                            status_code=400, 
                            detail=f"Invalid band '{update.band}'. Valid options: {', '.join(valid_bands)}"
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
                            detail=f"Invalid channel {update.channel} for band {target_band}. Valid channels: {', '.join(map(str, band_channels))}"
                        )
            except HTTPException:
                raise
            except Exception as e:
                # If capabilities check fails, log but don't block the update
                print(f"Warning: Could not validate WiFi capabilities: {e}")
        
        # Validate channel width if provided
        if update.channel_width is not None:
            valid_widths = ["20", "40", "80", "160"]
            if update.channel_width not in valid_widths:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid channel width '{update.channel_width}'. Valid options: {', '.join(valid_widths)}"
                )

        # Read the wifi netplan configuration file
        config = read_netplan_file(NETPLAN_WIFI_FILE)
        
        # Navigate to the hotspot access points
        network = config.get("network", {})
        wifis = network.get("wifis", {})
        hotspot = wifis.get("hotspot", {})
        access_points = hotspot.get("access-points", {})
        
        if not access_points or not isinstance(access_points, dict):
            raise HTTPException(status_code=404, detail="Hotspot access points not found in netplan configuration")
        
        # Get the first (and typically only) SSID
        ssid_key = next(iter(access_points.keys()))
        if not ssid_key:
            raise HTTPException(status_code=404, detail="Hotspot SSID not found")
        
        # Get the current access point config
        ap_config = access_points[ssid_key]
        
        # Update password if provided
        if update.password is not None:
            if "auth" not in ap_config:
                ap_config["auth"] = {}
            
            ap_config["auth"]["password"] = update.password
            
            # Ensure key-management is set to psk
            if "key-management" not in ap_config["auth"]:
                ap_config["auth"]["key-management"] = "psk"
        
        # Update band if provided
        if update.band is not None:
            ap_config["band"] = update.band
        
        # Update channel if provided (null means auto)
        if update.channel is not None:
            ap_config["channel"] = update.channel
        elif "channel" in update.__fields_set__:  # Explicitly set to None
            # Remove channel to use auto
            ap_config.pop("channel", None)
        
        # Ensure networkmanager section exists for passthrough settings
        if "networkmanager" not in ap_config:
            ap_config["networkmanager"] = {}
        if "passthrough" not in ap_config["networkmanager"]:
            ap_config["networkmanager"]["passthrough"] = {}
        
        passthrough = ap_config["networkmanager"]["passthrough"]
        
        # Update channel width if provided (via NetworkManager passthrough)
        if update.channel_width is not None:
            passthrough["802-11-wireless.channel-width"] = update.channel_width
        elif "channel_width" in update.__fields_set__:  # Explicitly set to None
            passthrough.pop("802-11-wireless.channel-width", None)
        
        # Update hidden if provided (via NetworkManager passthrough)
        if update.hidden is not None:
            passthrough["802-11-wireless.hidden-ssid"] = "yes" if update.hidden else "no"
        elif "hidden" in update.__fields_set__:  # Explicitly set to None
            passthrough.pop("802-11-wireless.hidden-ssid", None)
        
        # Write the modified configuration back to the file
        write_netplan_file(NETPLAN_WIFI_FILE, config)

        # Note: netplan apply is not called here - configuration changes will be applied later

        # Get updated configuration
        updated_config = await get_hotspot_config()

        return {"message": "Hotspot configuration updated successfully (not yet applied)", "config": updated_config}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update hotspot configuration: {str(e)}")


@router.get("/connections/cellular", summary="Get cellular configuration", response_model=CellularConfig)
async def get_cellular_config() -> CellularConfig:
    """Get current cellular/GSM configuration from netplan including APN, username, password, and PIN.

    Returns:
        Current cellular configuration
    """
    try:
        # Read the cellular netplan configuration file
        config = read_netplan_file(NETPLAN_CELLULAR_FILE)
        
        # Navigate to the cellular configuration
        network = config.get("network", {})
        modems = network.get("modems", {})
        cellular_config = modems.get("cellular", {})
        
        if not cellular_config or not isinstance(cellular_config, dict):
            raise HTTPException(status_code=404, detail="Cellular configuration not found in netplan")
        
        # Extract values, treating empty strings as None
        apn = cellular_config.get("apn") or None
        username = cellular_config.get("username") or None
        password = cellular_config.get("password") or None
        pin = cellular_config.get("pin") or None

        return CellularConfig(apn=apn, username=username, password=password, pin=pin)

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

        # Read the cellular netplan configuration file
        config = read_netplan_file(NETPLAN_CELLULAR_FILE)
        
        # Navigate to the cellular configuration
        network = config.get("network", {})
        modems = network.get("modems", {})
        cellular_config = modems.get("cellular", {})
        
        if not isinstance(cellular_config, dict):
            raise HTTPException(status_code=404, detail="Cellular configuration not found in netplan")

        # Update fields if provided (preserve existing fields)
        if update.apn is not None:
            cellular_config["apn"] = update.apn if update.apn else ""
        
        if update.username is not None:
            cellular_config["username"] = update.username if update.username else ""
        
        if update.password is not None:
            cellular_config["password"] = update.password if update.password else ""
        
        if update.pin is not None:
            cellular_config["pin"] = update.pin if update.pin else ""

        # Write the modified configuration back to the file
        write_netplan_file(NETPLAN_CELLULAR_FILE, config)

        # Note: netplan apply is not called here - configuration changes will be applied later

        # Get updated configuration
        updated_config = await get_cellular_config()

        return {"message": "Cellular configuration updated successfully (not yet applied)", "config": updated_config}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update cellular configuration: {str(e)}")


@router.post("/connections/{connection_name}/up", summary="Bring a connection up")
async def bring_connection_up(connection_name: str) -> Dict[str, Any]:
    """Bring a network connection up using nmcli connection up.

    Args:
        connection_name: Name of the connection to bring up

    Returns:
        Success message with connection information
    """
    try:
        # Try to bring the connection up
        result = run_nmcli_command(["connection", "up", connection_name], check=False, sudo=True)

        if result.returncode == 0:
            return {"message": f"Connection '{connection_name}' activated successfully", "connection_name": connection_name}
        else:
            # Parse error message from nmcli
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            
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
        raise HTTPException(status_code=500, detail=f"Failed to bring connection up: {str(e)}")


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
