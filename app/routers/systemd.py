"""Systemd services management router."""

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config_loader import config_loader

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

        # Format duration
        total_seconds = int(duration.total_seconds())
        if total_seconds < 0:
            # Handle negative durations (future timestamps) - might indicate clock issues
            total_seconds = abs(total_seconds)  # Show absolute time for debugging

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
        return None


def get_service_info(service_config: ServiceConfig) -> ServiceInfo:
    """Get information about a systemd service."""
    try:
        # Get service status including timestamps
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service_config.name,
                "--no-pager",
                "--property=ActiveState,UnitFileState,Description,ActiveEnterTimestamp,InactiveEnterTimestamp,StateChangeTimestamp",
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
        )

    except subprocess.TimeoutExpired:
        return ServiceInfo(
            name=service_config.name,
            description="Timeout getting service info",
            active=False,
            enabled=False,
            status="timeout",
            uptime=None,
            expert=True,  # Force expert mode for services with timeout
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
        )


@router.get("/services", response_model=List[ServiceInfo])
async def list_services():
    """Get status of all configured systemd services."""
    services = get_configured_services(include_expert=True)
    service_info = []

    for service in services:
        info = get_service_info(service)
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

    try:
        # Build systemctl command
        cmd = ["sudo", "systemctl", action.action, action.service]

        # Execute systemctl command
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else f"Command failed with exit code {result.returncode}"
            raise HTTPException(
                status_code=500, detail=f"Failed to {action.action} service {action.service}: {error_msg}"
            )

        # Create success message
        message = f"Successfully {action.action}ed service {action.service}"

        return {"success": True, "message": message, "service": action.service, "action": action.action}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail=f"Timeout while trying to {action.action} service {action.service}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error performing action: {str(e)}")


@router.post("/reboot")
async def reboot_system():
    """Initiate system reboot."""
    try:
        # Use systemctl to reboot the system
        result = subprocess.run(["sudo", "systemctl", "reboot"], capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to initiate reboot: {result.stderr}")

        return {"message": "System reboot initiated"}

    except subprocess.TimeoutExpired:
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
    """Stream journalctl logs for a specific service."""
    # Validate service is in our configured list
    configured_services = get_configured_services(include_expert=True)
    service_names = [service.name for service in configured_services]
    if service_name not in service_names:
        raise HTTPException(status_code=400, detail="Service not in configured list")

    async def generate_logs():
        """Generate streaming logs using journalctl -fu."""
        try:
            # Start journalctl process with follow and unit flags
            process = await asyncio.create_subprocess_exec(
                "journalctl",
                "-fu",
                service_name,
                "--no-pager",
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


class RebootProtectionStatus(BaseModel):
    """Reboot protection status model."""

    enabled: bool
    services: List[str] = Field(default_factory=list, description="Services with reboot protection")


class RebootProtectionToggle(BaseModel):
    """Reboot protection toggle model."""

    enabled: bool


def get_services_with_reboot_action() -> List[str]:
    """Get list of services that have StartLimitAction=reboot configured."""
    services_to_check = ["radiotracking", "soundscapepipe"]
    services_with_reboot = []

    for service in services_to_check:
        try:
            result = subprocess.run(
                ["systemctl", "cat", f"{service}.service"], capture_output=True, text=True, timeout=10
            )

            if result.returncode == 0 and "StartLimitAction=reboot" in result.stdout:
                services_with_reboot.append(service)
        except Exception:
            continue

    return services_with_reboot


def is_reboot_protection_enabled() -> bool:
    """Check if reboot protection is currently enabled by checking for override directories."""
    services_to_check = ["radiotracking", "soundscapepipe"]

    for service in services_to_check:
        override_dir = Path(f"/etc/systemd/system/{service}.service.d")
        override_file = override_dir / "reboot-protection.conf"

        if override_file.exists():
            try:
                content = override_file.read_text()
                if "StartLimitAction=none" in content:
                    return True
            except Exception:
                continue

    return False


def create_service_override(service_name: str) -> bool:
    """Create a systemd service override to disable reboot action."""
    try:
        override_dir = Path(f"/etc/systemd/system/{service_name}.service.d")
        override_file = override_dir / "reboot-protection.conf"

        # Create override directory
        result = subprocess.run(["sudo", "mkdir", "-p", str(override_dir)], capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            return False

        # Create override file content
        override_content = """[Service]
StartLimitAction=none
"""

        # Write override file
        result = subprocess.run(
            ["sudo", "tee", str(override_file)], input=override_content, capture_output=True, text=True, timeout=10
        )

        return result.returncode == 0

    except Exception:
        return False


def remove_service_override(service_name: str) -> bool:
    """Remove systemd service override file."""
    try:
        override_dir = Path(f"/etc/systemd/system/{service_name}.service.d")
        override_file = override_dir / "reboot-protection.conf"

        # Remove override file
        if override_file.exists():
            result = subprocess.run(["sudo", "rm", str(override_file)], capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                return False

        # Remove directory if empty
        try:
            override_dir.rmdir()
        except OSError:
            # Directory not empty, that's fine
            pass

        return True

    except Exception:
        return False


@router.get("/reboot-protection", response_model=RebootProtectionStatus)
async def get_reboot_protection_status():
    """Get current reboot protection status."""
    try:
        enabled = is_reboot_protection_enabled()
        services_with_reboot = get_services_with_reboot_action()

        return RebootProtectionStatus(enabled=enabled, services=services_with_reboot)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get reboot protection status: {str(e)}")


@router.post("/reboot-protection")
async def toggle_reboot_protection(toggle: RebootProtectionToggle):
    """Enable or disable reboot protection for services."""
    try:
        services_to_protect = ["radiotracking", "soundscapepipe"]
        success = True
        errors = []

        for service in services_to_protect:
            if toggle.enabled:
                if not create_service_override(service):
                    success = False
                    errors.append(f"Failed to create override for {service}")
            else:
                if not remove_service_override(service):
                    success = False
                    errors.append(f"Failed to remove override for {service}")

        if not success:
            raise HTTPException(status_code=500, detail=f"Some operations failed: {', '.join(errors)}")

        # Reload systemd daemon
        result = subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to reload systemd daemon")

        # Restart services to apply changes
        for service in services_to_protect:
            try:
                # Check if service exists and is active before restarting
                status_result = subprocess.run(
                    ["systemctl", "is-active", service], capture_output=True, text=True, timeout=10
                )

                if status_result.returncode == 0:  # Service is active
                    restart_result = subprocess.run(
                        ["sudo", "systemctl", "restart", service], capture_output=True, text=True, timeout=30
                    )

                    if restart_result.returncode != 0:
                        errors.append(f"Failed to restart {service}")
            except Exception as e:
                errors.append(f"Error restarting {service}: {str(e)}")

        if errors:
            return {
                "message": f"Reboot protection {'enabled' if toggle.enabled else 'disabled'} with warnings",
                "warnings": errors,
            }

        return {"message": f"Reboot protection {'enabled' if toggle.enabled else 'disabled'} successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to toggle reboot protection: {str(e)}")
