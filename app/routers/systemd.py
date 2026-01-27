"""Systemd services management router."""

import asyncio
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config_loader import config_loader
from app.utils.subprocess_async import run_subprocess_async

router = APIRouter(prefix="/api/systemd", tags=["systemd"])

# Empty default services configuration - no services loaded by default
DEFAULT_SERVICES_CONFIG = {"services": []}

# Configuration file path
CONFIG_PATH = Path("configs/tsconfig.yml")


class ServiceConfig(BaseModel):
    """Service configuration model."""

    name: str
    expert: bool = False


class ServicesConfigFile(BaseModel):
    """Services configuration file model."""

    services: List[ServiceConfig]


class ServiceInfo(BaseModel):
    """Service information model."""

    name: str
    description: str
    active: bool
    enabled: bool
    status: str
    uptime: Optional[str] = None
    expert: bool = False
    is_target: bool = False  # True if this is a systemd target (cannot be controlled)
    is_kernel: bool = False  # True if this is the kernel logs service


class ServiceAction(BaseModel):
    """Service action model."""

    service: str
    action: str


def get_services_config() -> ServicesConfigFile:
    """Get services configuration from the main config loader."""
    try:
        services_data = config_loader.get_services_config()
        if services_data:
            return ServicesConfigFile(services=services_data)
    except Exception:
        pass
    return ServicesConfigFile(**DEFAULT_SERVICES_CONFIG)


def get_configured_services(include_expert: bool = True) -> List[ServiceConfig]:
    """Get list of configured services, optionally filtering by expert mode."""
    config = get_services_config()
    if include_expert:
        return config.services
    else:
        return [service for service in config.services if not service.expert]


def format_uptime_from_seconds(total_seconds: float) -> str:
    """Format uptime from seconds into a human-readable string."""
    try:
        total_seconds = int(total_seconds)
        if total_seconds < 0:
            total_seconds = abs(total_seconds)

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except Exception:
        return "N/A"


def calculate_service_uptime(properties: Dict[str, str], active_state: str) -> Optional[str]:
    """Calculate how long the service has been in its current state."""
    try:
        # Determine which timestamp to use based on current state
        if active_state == "active":
            timestamp_str = properties.get("ActiveEnterTimestamp", "")
        else:
            # For inactive states, try InactiveEnterTimestamp first, then StateChangeTimestamp
            timestamp_str = properties.get("InactiveEnterTimestamp", "") or properties.get("StateChangeTimestamp", "")

        if not timestamp_str or timestamp_str == "0" or timestamp_str == "n/a":
            return None

        # Parse systemd timestamp format
        try:
            # Systemd timestamps are typically in format like "Mon 2023-12-04 10:30:15 UTC" or Unix timestamp
            if timestamp_str.isdigit():
                # Unix timestamp in microseconds
                timestamp = datetime.fromtimestamp(int(timestamp_str) / 1000000)
            else:
                # Try to parse various timestamp formats
                timestamp_formats = [
                    "%a %Y-%m-%d %H:%M:%S %Z",  # "Tue 2024-11-19 14:39:37 CET"
                    "%Y-%m-%d %H:%M:%S %Z",  # "2024-11-19 14:39:37 CET"
                    "%Y-%m-%d %H:%M:%S",  # "2024-11-19 14:39:37"
                    "%a %b %d %H:%M:%S %Y",  # "Tue Nov 19 14:39:37 2024"
                ]

                for fmt in timestamp_formats:
                    try:
                        timestamp = datetime.strptime(timestamp_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    # If all parsing attempts fail, return None
                    return None
        except (ValueError, OSError):
            return None

        # Calculate duration
        now = datetime.now()
        if timestamp.tzinfo is None:
            # Use local time for comparison since systemd timestamps are typically local
            timestamp = timestamp.replace(tzinfo=None)
            now = datetime.now()

        duration = now - timestamp

        # Format duration using the shared formatting function
        return format_uptime_from_seconds(duration.total_seconds())

    except Exception:
        return None


async def get_service_info(service_config: ServiceConfig) -> ServiceInfo:
    """Get information about a systemd service."""
    # Special handling for kernel/dmesg service
    if service_config.name == "kernel" or service_config.name == "dmesg":
        # Calculate system uptime for kernel service
        try:
            boot_time = psutil.boot_time()
            current_time = time.time()
            uptime_seconds = current_time - boot_time
            uptime_str = format_uptime_from_seconds(uptime_seconds)
        except Exception:
            uptime_str = None
        
        return ServiceInfo(
            name=service_config.name,
            description="Kernel ring buffer logs (dmesg)",
            active=True,  # Kernel is always "active"
            enabled=True,
            status="active",
            uptime=uptime_str,
            expert=service_config.expert,
            is_target=False,
            is_kernel=True,
        )
    
    try:
        # Get service status including timestamps and type
        result = await run_subprocess_async(
            [
                "systemctl",
                "show",
                service_config.name,
                "--no-pager",
                "--property=ActiveState,UnitFileState,Description,ActiveEnterTimestamp,InactiveEnterTimestamp,StateChangeTimestamp,Type",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            )

        if result.returncode != 0:
            # Service doesn't exist or error occurred - mark as expert
            return ServiceInfo(
                name=service_config.name,
                description="Service not found",
                active=False,
                enabled=False,
                status="not-found",
                uptime=None,
                expert=True,  # Force expert mode for unavailable services
                is_target=False,
            )

        # Parse output
        properties = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value

        active_state = properties.get("ActiveState", "unknown")
        unit_file_state = properties.get("UnitFileState", "unknown")
        description = properties.get("Description", "No description available")
        
        # Check if this is a target unit (targets end with .target)
        is_target = service_config.name.endswith(".target")

        # Check if service is not found (empty UnitFileState and inactive + generic description)
        service_not_found = unit_file_state == "" and active_state == "inactive" and description.endswith(".service")

        if service_not_found:
            return ServiceInfo(
                name=service_config.name,
                description="Service not found",
                active=False,
                enabled=False,
                status="not-found",
                uptime=None,
                expert=True,  # Force expert mode for unavailable services
                is_target=False,
            )

        # Calculate uptime/downtime
        uptime = calculate_service_uptime(properties, active_state)

        return ServiceInfo(
            name=service_config.name,
            description=description,
            active=active_state == "active",
            enabled=unit_file_state == "enabled",
            status=active_state,
            uptime=uptime,
            expert=service_config.expert,
            is_target=is_target,
        )

    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        return ServiceInfo(
            name=service_config.name,
            description="Timeout getting service info",
            active=False,
            enabled=False,
            status="timeout",
            uptime=None,
            expert=True,  # Force expert mode for services with timeout
            is_target=False,
        )
    except Exception as e:
        return ServiceInfo(
            name=service_config.name,
            description=f"Error: {str(e)}",
            active=False,
            enabled=False,
            status="error",
            uptime=None,
            expert=True,  # Force expert mode for services with errors
            is_target=False,
        )


@router.get("/services", response_model=List[ServiceInfo])
async def list_services():
    """Get status of all configured systemd services."""
    services = get_configured_services(include_expert=True)
    service_info = []

    for service in services:
        info = await get_service_info(service)
        service_info.append(info)

    return service_info


@router.post("/action")
async def service_action(action: ServiceAction):
    """Perform action on a systemd service."""
    if action.action not in ["start", "stop", "restart"]:
        raise HTTPException(status_code=400, detail="Invalid action. Must be start, stop, or restart")

    # Validate service is in our configured list
    configured_services = get_configured_services(include_expert=True)
    service_names = [service.name for service in configured_services]
    if action.service not in service_names:
        raise HTTPException(status_code=400, detail="Service not in configured list")
    
    # Prevent actions on kernel service (it's not a real systemd service)
    if action.service == "kernel" or action.service == "dmesg":
        raise HTTPException(status_code=400, detail="Cannot perform actions on kernel logs service")

    try:
        # Build systemctl command
        cmd = ["systemctl", action.action, action.service]

        # Execute systemctl command
        result = await run_subprocess_async(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else f"Command failed with exit code {result.returncode}"
            raise HTTPException(
                status_code=500, detail=f"Failed to {action.action} service {action.service}: {error_msg}"
            )

        # Create success message
        message = f"Successfully {action.action}ed service {action.service}"

        return {"success": True, "message": message, "service": action.service, "action": action.action}

    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        raise HTTPException(status_code=500, detail=f"Timeout while trying to {action.action} service {action.service}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error performing action: {str(e)}")


@router.post("/reboot")
async def reboot_system():
    """Initiate system reboot."""
    try:
        # Use systemctl to reboot the system
        result = await run_subprocess_async(["systemctl", "reboot"], capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to initiate reboot: {result.stderr}")

        return {"message": "System reboot initiated"}

    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        # Timeout is expected as the system will be rebooting
        return {"message": "System reboot initiated"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Failed to initiate reboot: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error during reboot: {str(e)}")


@router.get("/config/system")
async def get_system_config():
    """Get the current system configuration."""
    try:
        refresh_interval = config_loader.get_status_refresh_interval()
        return {"status_refresh_interval": refresh_interval}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get system configuration: {str(e)}")


@router.get("/logs/{service_name}")
async def stream_service_logs(service_name: str):
    """Stream journalctl logs for a specific service, kernel logs, or all system logs."""
    # Special handling for "all" logs - bypass validation
    if service_name == "all":
        pass  # Allow "all" without validation
    else:
        # Validate service is in our configured list
        configured_services = get_configured_services(include_expert=True)
        service_names = [service.name for service in configured_services]
        if service_name not in service_names:
            raise HTTPException(status_code=400, detail="Service not in configured list")

    async def generate_logs():
        """Generate streaming logs using journalctl -fu, journalctl -kf for kernel, or journalctl -f for all."""
        # Send initial comment to establish the connection (comments don't trigger onmessage)
        yield f": Stream started\n\n"
        
        try:
            # Special handling for all system logs
            if service_name == "all":
                # Use journalctl -f for all system logs (follow mode, no unit filter)
                process = await asyncio.create_subprocess_exec(
                    "journalctl",
                    "-f",
                    "--no-pager",
                    "--no-hostname",
                    "--output", "short",
                    "-n",
                    "50",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            # Special handling for kernel logs
            elif service_name == "kernel" or service_name == "dmesg":
                # Use journalctl -k -f for kernel logs (kernel messages, follow mode)
                process = await asyncio.create_subprocess_exec(
                    "journalctl",
                    "-k",
                    "-f",
                    "--no-pager",
                    "--no-hostname",
                    "--output", "short",
                    "-n",
                    "50",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                # Start journalctl process with follow and unit flags
                process = await asyncio.create_subprocess_exec(
                    "journalctl",
                    "-fu",
                    service_name,
                    "--no-pager",
                    "--no-hostname",
                    "--output", "short",
                    "-n",
                    "50",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                # Decode and yield the line
                log_line = line.decode("utf-8", errors="replace").rstrip()
                yield f"data: {log_line}\n\n"

        except Exception as e:
            yield f"data: Error streaming logs: {str(e)}\n\n"
        finally:
            if process and process.returncode is None:
                try:
                    process.terminate()
                    await process.wait()
                except Exception:
                    pass

    return StreamingResponse(
        generate_logs(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Content-Type": "text/event-stream"},
    )
