"""Network management router for NetworkManager connections."""

import asyncio
import json
import subprocess
import yaml
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.utils.subprocess_async import run_subprocess_async

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


async def run_nmcli_command(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
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
        cmd = ["nmcli"] + args
        result = await run_subprocess_async(cmd, capture_output=True, text=True, check=check, timeout=10)
        return result
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500, detail=f"NetworkManager command failed: {e.stderr.strip() or e.stdout.strip() or str(e)}"
        )
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        raise HTTPException(status_code=504, detail="NetworkManager command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="NetworkManager (nmcli) not available")


async def run_mmcli_command(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
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
        result = await run_subprocess_async(["mmcli"] + args, capture_output=True, text=True, check=check, timeout=10)
        return result
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500, detail=f"ModemManager command failed: {e.stderr.strip() or e.stdout.strip() or str(e)}"
        )
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        raise HTTPException(status_code=504, detail="ModemManager command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="ModemManager (mmcli) not available")


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

        result = await run_subprocess_async(
            ["iw", "dev", interface, "info"], capture_output=True, text=True, timeout=5
        )
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
    sorted_widths = sorted(list(channel_widths), key=lambda x: int(x))

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
        
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        raise HTTPException(status_code=504, detail="WiFi capability detection timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="iw command not available")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse WiFi capabilities: {str(e)}")


async def get_nmcli_connection_properties(connection_name: str, show_secrets: bool = False) -> Dict[str, str]:
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
            raise HTTPException(
                status_code=404,
                detail=f"Connection '{connection_name}' not found"
            )
        
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
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get properties from connection {connection_name}: {str(e)}"
        )


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
        return None


async def remove_gsm_fields_from_netplan(fields_to_remove: List[str], connection_name: str = "cellular") -> None:
    """Remove GSM fields from netplan YAML file.
    
    nmcli doesn't allow directly unsetting certain GSM fields (like password, pin),
    so we need to edit the netplan YAML file directly:
    1. Get the connection UUID
    2. Find the corresponding netplan YAML file
    3. Remove the specified fields from the YAML
    4. Reload NetworkManager connections
    
    Args:
        fields_to_remove: List of field names to remove (e.g., ["password", "pin"])
        connection_name: Name of the cellular connection (default: "cellular")
        
    Raises:
        HTTPException: If critical operations fail
    """
    if not fields_to_remove:
        return
    
    try:
        # Step 1: Get connection UUID
        result = await run_nmcli_command(
            ["-t", "-f", "connection.uuid", "connection", "show", connection_name],
            check=False
        )
        
        if result.returncode != 0:
            raise HTTPException(
                status_code=404,
                detail=f"Connection '{connection_name}' not found"
            )
        
        uuid = result.stdout.strip().replace("connection.uuid:", "")
        if not uuid:
            raise HTTPException(
                status_code=500,
                detail="Failed to get connection UUID"
            )
        
        # Step 2: Find netplan file
        netplan_file = Path(f"/etc/netplan/90-NM-{uuid}.yaml")
        
        if not netplan_file.exists():
            # Log warning but don't fail - fields might not be in netplan
            print(f"Warning: Netplan file {netplan_file} not found, fields may not exist")
            return
        
        # Step 3: Read and parse YAML
        try:
            with open(netplan_file, "r") as f:
                netplan_content = yaml.safe_load(f)
        except PermissionError:
            # Try using subprocess
            read_result = await run_subprocess_async(
                ["cat", str(netplan_file)],
                capture_output=True,
                text=True,
                timeout=5
            )
            if read_result.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to read netplan file (permission denied)"
                )
            netplan_content = yaml.safe_load(read_result.stdout)
        
        # Step 4: Remove specified fields from YAML structure
        modified = False
        if (netplan_content and 
            "network" in netplan_content and 
            "modems" in netplan_content["network"] and 
            "cellular" in netplan_content["network"]["modems"]):
            
            cellular_config = netplan_content["network"]["modems"]["cellular"]
            for field in fields_to_remove:
                if field in cellular_config:
                    del cellular_config[field]
                    modified = True
                    print(f"Info: Removed '{field}' field from {netplan_file}")
        
        if not modified:
            # No fields found in YAML, nothing to do
            print(f"Info: None of the specified fields {fields_to_remove} found in {netplan_file}")
            return
        
        # Step 5: Write back the YAML
        yaml_output = yaml.dump(netplan_content, default_flow_style=False, sort_keys=False)
        
        try:
            with open(netplan_file, "w") as f:
                f.write(yaml_output)
        except PermissionError:
            # Use tee to write
            process = await asyncio.create_subprocess_exec(
                "tee",
                str(netplan_file),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(yaml_output.encode()),
                timeout=5.0
            )
            returncode = await process.wait()
            
            write_result = subprocess.CompletedProcess(
                args=["tee", str(netplan_file)],
                returncode=returncode,
                stdout=stdout.decode() if stdout else '',
                stderr=stderr.decode() if stderr else '',
            )
            if write_result.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to write netplan file (permission denied)"
                )
        
        # Step 6: Reload NetworkManager connections
        reload_result = await run_subprocess_async(
            ["nmcli", "connection", "reload"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if reload_result.returncode != 0:
            print(f"Warning: nmcli connection reload failed: {reload_result.stderr}")
            # Don't fail here - the file was updated successfully
        
    except HTTPException:
        raise
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        raise HTTPException(status_code=504, detail="Operation timed out")
    except Exception as e:
        # Log error but don't fail the entire update
        print(f"Warning: Failed to remove GSM fields from netplan: {str(e)}")


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
        raise HTTPException(status_code=500, detail=f"Failed to get WiFi capabilities: {str(e)}")


@router.get("/connections", summary="List all network connections", response_model=List[ConnectionInfo])
async def get_connections() -> List[ConnectionInfo]:
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
        raise HTTPException(status_code=500, detail=f"Failed to list network connections: {str(e)}")


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
                capabilities = await parse_wifi_capabilities()
                
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

        # Build list of modifications for a single nmcli call
        modifications = []
        properties_to_remove = []
        
        # Update password if provided
        if update.password is not None:
            modifications.extend(["802-11-wireless-security.psk", update.password])
        
        # Update band if provided
        # NetworkManager expects "a" for 5GHz, "bg" for 2.4GHz
        if update.band is not None:
            if update.band == "5GHz":
                band_value = "a"
            elif update.band == "2.4GHz":
                band_value = "bg"
            else:
                band_value = ""  # Auto/unset
            modifications.extend(["802-11-wireless.band", band_value])
        elif "band" in update.__fields_set__:  # Explicitly set to None (auto)
            properties_to_remove.append("802-11-wireless.band")
        
        # Update channel if provided
        if update.channel is not None:
            modifications.extend(["802-11-wireless.channel", str(update.channel)])
        elif "channel" in update.__fields_set__:  # Explicitly set to None (auto)
            modifications.extend(["802-11-wireless.channel", ""])
        
        # Update hidden if provided
        if update.hidden is not None:
            hidden_value = "yes" if update.hidden else "no"
            modifications.extend(["802-11-wireless.hidden", hidden_value])
        elif "hidden" in update.__fields_set__:  # Explicitly set to None (default)
            modifications.extend(["802-11-wireless.hidden", "no"])
        
        # Update channel width if provided
        if update.channel_width is not None:
            modifications.extend(["802-11-wireless.channel-width", update.channel_width])
        elif "channel_width" in update.__fields_set__:  # Explicitly set to None (unset/auto)
            modifications.extend(["802-11-wireless.channel-width", "0"])
        
        # Execute all modifications in a single nmcli call
        if modifications or properties_to_remove:
            args = ["connection", "modify", "hotspot"] + modifications
            if properties_to_remove:
                args.append("--")
                for prop in properties_to_remove:
                    args.append(f"-{prop}")
            await run_nmcli_command(args)

        # Get updated configuration
        updated_config = await get_hotspot_config()

        return {"message": "Hotspot configuration updated successfully (changes saved, restart connection to apply)", "config": updated_config}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update hotspot configuration: {str(e)}")


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
        # Validate PIN if provided (not empty/null)
        if update.pin:
            # PIN should be 4-8 digits
            if not update.pin.isdigit():
                raise HTTPException(status_code=400, detail="PIN must contain only digits")
            if len(update.pin) < 4 or len(update.pin) > 8:
                raise HTTPException(status_code=400, detail="PIN must be 4-8 digits")

        # Frontend always sends all 4 fields (either with values or null)
        # Treat None/null as empty string to clear the field for APN and username
        modifications = [
            "gsm.apn", update.apn or "",
            "gsm.username", update.username or ""
        ]
        
        # Track fields that need to be removed from netplan (nmcli can't unset them)
        fields_to_remove_from_netplan = []
        
        # Handle password field
        if update.password:
            # Set password via nmcli
            modifications.extend(["gsm.password", update.password])
        else:
            # Remove password by editing netplan YAML file
            fields_to_remove_from_netplan.append("password")
        
        # Handle pin field
        if update.pin:
            # Set pin via nmcli
            modifications.extend(["gsm.pin", update.pin])
        else:
            # Remove pin by editing netplan YAML file
            fields_to_remove_from_netplan.append("pin")
        
        # Remove fields from netplan if needed
        if fields_to_remove_from_netplan:
            await remove_gsm_fields_from_netplan(fields_to_remove_from_netplan, "cellular")
        
        # Execute all modifications in a single nmcli call
        args = ["connection", "modify", "cellular"] + modifications
        await run_nmcli_command(args)

        # Get updated configuration
        updated_config = await get_cellular_config()

        return {"message": "Cellular configuration updated successfully (changes saved, restart connection to apply)", "config": updated_config}

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
        result = await run_nmcli_command(["connection", "up", connection_name], check=False)

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
        list_result = await run_mmcli_command(["-L", "--output-json"], check=False)

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
        detail_result = await run_mmcli_command(["-m", modem_id, "--output-json"], check=False)

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
            bearer_result = await run_mmcli_command(["-b", bearer_id, "--output-json"], check=False)
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
            sim_result = await run_mmcli_command(["-i", sim_id, "--output-json"], check=False)
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
