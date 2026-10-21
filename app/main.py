"""tsOS Configuration Manager."""

import datetime
import json
import os
import platform
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.auth.middleware import AuthenticationMiddleware
from app.auth.oidc_config import oidc_config
from app.config_loader import config_loader
from app.configs import support
from app.configs.authorized_keys import AuthorizedKeysConfig
from app.configs.geolocation import GeolocationConfig
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.schedule import ScheduleConfig
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.configs.tsupdate import TsupdateConfig
from app.logging_config import get_logger, setup_logging
from app.proxy_headers import ProxyHeadersMiddleware
from app.routers import (
    auth,
    authorized_keys,
    configs,
    deployment,
    radiotracking,
    schedule,
    soundscapepipe,
    tsupdate,
    userconf,
)
from app.utils.subprocess_async import run_subprocess_async

# Set up logging for the main application
setup_logging(identifier="tsconfig")
logger = get_logger(__name__)

if not config_loader.is_server_mode():
    # psutil is in the `tracker` extra; the first cpu_percent(interval=None)
    # call only starts the measurement
    import psutil

    psutil.cpu_percent(interval=None)

# Get base URL from environment variable (default to "/" for root)
BASE_URL = os.environ.get("TSCONFIG_BASE_URL", "/").rstrip("/")
if not BASE_URL:
    BASE_URL = ""

# In server mode, we use query parameter routing: /tsconfig/?config_group={groupname}
# In tracker mode, we use the BASE_URL directly without config_group parameter

# OpenAPI tags metadata for better API documentation organization
tags_metadata = [
    {
        "name": "system",
        "description": "System monitoring and status information including CPU, memory, disk, and network metrics.",
    },
    {
        "name": "authentication",
        "description": "OIDC authentication endpoints for server mode (only available when OIDC is configured).",
    },
    {
        "name": "schedule",
        "description": "Schedule configuration management with astronomical event support (sunrise, sunset, etc.).",
    },
    {
        "name": "radiotracking",
        "description": "Radio tracking configuration for RTL-SDR based radio tracking systems.",
    },
    {
        "name": "soundscapepipe",
        "description": "Soundscapepipe configuration for audio recording and analysis.",
    },
    {
        "name": "authorized_keys",
        "description": "SSH authorized keys management for secure remote access.",
    },
    {
        "name": "configs",
        "description": "Configuration file upload and download with validation.",
    },
    {
        "name": "systemd",
        "description": "Systemd service management including status monitoring, control, and log streaming.",
    },
    {
        "name": "network",
        "description": "Network configuration management for NetworkManager connections.",
    },
    {
        "name": "tsupdate",
        "description": "Tsupdate daemon configuration for automatic system updates.",
    },
    {
        "name": "userconf",
        "description": "pi account password (userconf.txt), applied by userconfig.service on next boot.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn applies its own logging config after this module is imported,
    # which resets the uvicorn/uvicorn.access levels; re-apply ours once the
    # server is up.
    setup_logging(identifier="tsconfig")
    await validate_oidc_config()
    yield


app = FastAPI(
    title="tsOS Configuration Manager",
    description="""
## tsOS Configuration Manager API

A comprehensive REST API for managing and monitoring trackIT Systems sensor stations.

### Features

* **System Monitoring** - Real-time system status, CPU, memory, disk, network, and temperature monitoring
* **Schedule Management** - Configure operation schedules with astronomical event support
* **Radio Tracking** - Configure RTL-SDR based radio tracking systems
* **Soundscapepipe** - Audio recording and analysis configuration
* **Service Control** - Manage systemd services (start, stop, restart, logs)
* **System Control** - Reboot system and manage system time settings

### API Documentation

* **Swagger UI** - Interactive API documentation at `/docs`
* **ReDoc** - Alternative documentation at `/redoc`
* **OpenAPI Schema** - Raw OpenAPI specification at `/openapi.json`

### Getting Started

Use the interactive Swagger UI at `/docs` to explore and test all available endpoints.
Each endpoint includes detailed request/response schemas and the ability to try requests directly.
    """,
    version=__version__,
    openapi_tags=tags_metadata,
    contact={
        "name": "trackIT Systems",
        "url": "https://trackit.systems",
        "email": "info@trackit.systems",
    },
    license_info={
        "name": "© 2025 trackIT Systems. All rights reserved.",
    },
    root_path=BASE_URL,
    lifespan=lifespan,
)

# Log application startup
logger.info(f"tsOS Configuration Manager v{__version__} starting up")
logger.debug(f"Base URL: {BASE_URL}")
logger.debug(f"Server mode: {config_loader.is_server_mode()}")

# Include routers
app.include_router(auth.router)  # Authentication router (only active in server mode)
app.include_router(schedule.router)
app.include_router(radiotracking.router)
app.include_router(soundscapepipe.router)
app.include_router(authorized_keys.router)
app.include_router(configs.router)
app.include_router(tsupdate.router)
app.include_router(userconf.router)
app.include_router(deployment.router)  # Deployment proxy (for server mode)

# Only include system-specific routers in tracker mode (default mode)
# These are disabled in server mode since they require direct hardware access,
# and only imported in tracker mode, since they need the `tracker` extra
if not config_loader.is_server_mode():
    from app.routers import network, power, shell, system_reset, systemd

    app.include_router(systemd.router)
    app.include_router(system_reset.router)
    app.include_router(shell.router)
    app.include_router(network.router)
    app.include_router(power.router)


# Mount static files (must be after all route definitions to avoid conflicts)
class AppStaticFiles(StaticFiles):
    """Static files whose own code must never be served stale.

    Starlette sends only ETag and Last-Modified, so a browser may reuse
    its copy of the application's JS and CSS for a while on its own
    judgement, and after an update run old scripts against the new API (the
    modules import each other, so a version in the page's script URL would
    not reach them). `no-cache` makes it ask first; unchanged files answer
    304. The vendored libraries are versioned by their path and stay cacheable."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        if Path(full_path).relative_to(Path(self.directory).resolve()).parts[0] != "vendor":
            response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", AppStaticFiles(directory="app/static"), name="static")

# Add authentication middleware (operator login in tracker mode, OIDC in server mode)
app.add_middleware(AuthenticationMiddleware)

# Add proxy headers middleware to handle X-Forwarded-* headers from reverse proxy
if config_loader.is_server_mode():
    app.add_middleware(ProxyHeadersMiddleware)

# Templates
templates = Jinja2Templates(directory="app/templates")


async def validate_oidc_config():
    """Validate OIDC configuration on startup if in server mode."""
    if config_loader.is_server_mode():
        if oidc_config.is_configured():
            is_valid, error = oidc_config.validate()
            if is_valid:
                logger.info("OIDC authentication is configured and valid")
                try:
                    # Try to fetch discovery document to validate connection
                    await oidc_config.get_discovery_document()
                    logger.info("Successfully connected to OIDC provider")
                except Exception:
                    logger.exception("Failed to connect to OIDC provider")
                    logger.warning("Server mode is enabled but OIDC provider is not accessible")
            else:
                logger.error(f"OIDC configuration is invalid: {error}")
                logger.warning("Server mode is enabled but OIDC is misconfigured")
        else:
            logger.warning("Server mode is enabled but OIDC is not configured")
            logger.warning("Authentication will not be enforced")


@app.get(
    "/api/server-mode",
    tags=["system"],
    summary="Get server mode configuration",
    description="""
Get server mode status and available config groups.

Returns:
- `enabled`: Whether server mode is active (default is tracker mode)
- `config_groups`: List of available config groups (empty in tracker mode)
- `config_root`: Root directory for config groups (null in tracker mode)
    """,
    response_description="Server mode configuration information",
)
async def get_server_mode():
    """Get server mode configuration."""
    is_server_mode = config_loader.is_server_mode()
    config_root = config_loader.get_config_root()

    # Add debug information
    debug_info = {}
    if is_server_mode and config_root:
        debug_info["config_root_exists"] = config_root.exists()
        debug_info["config_root_is_dir"] = config_root.is_dir()
        if config_root.exists() and config_root.is_dir():
            # List all subdirectories
            try:
                all_dirs = [item.name for item in config_root.iterdir() if item.is_dir()]
                debug_info["all_subdirectories"] = sorted(all_dirs)

                # Check each directory for latest subdirectory and config files
                dir_details = {}
                for item in config_root.iterdir():
                    if item.is_dir():
                        latest_dir = item / "latest"
                        dir_details[item.name] = {
                            "has_latest": latest_dir.exists() and latest_dir.is_dir(),
                            "config_files": [],
                        }
                        if latest_dir.exists() and latest_dir.is_dir():
                            if (latest_dir / "radiotracking.ini").exists():
                                dir_details[item.name]["config_files"].append("radiotracking.ini")
                            if (latest_dir / "schedule.yml").exists():
                                dir_details[item.name]["config_files"].append("schedule.yml")
                            if (latest_dir / "soundscapepipe.yml").exists():
                                dir_details[item.name]["config_files"].append("soundscapepipe.yml")
                debug_info["directory_details"] = dir_details
            except OSError as e:
                debug_info["error"] = str(e)

    return {
        "enabled": is_server_mode,
        "config_groups": config_loader.list_config_groups() if is_server_mode else [],
        "config_root": str(config_root) if is_server_mode and config_root else None,
        "oidc_enabled": is_server_mode and oidc_config.is_configured(),
        "debug": debug_info if is_server_mode else None,
    }


@app.get(
    "/api/geolocation",
    tags=["system"],
    summary="Get tracker geolocation",
    description="""
Get current tracker geolocation from /boot/firmware/geolocation file.

Returns:
- `lat`: Latitude in degrees
- `lon`: Longitude in degrees
- `alt`: Altitude in meters
- `accuracy`: Accuracy radius in meters

Returns null if geolocation file is not found.
    """,
    response_description="Geolocation information or null if not available",
)
async def get_geolocation():
    """Get current tracker geolocation."""
    try:
        geolocation_config = GeolocationConfig()
        geolocation = geolocation_config.load()
        return geolocation
    except (FileNotFoundError, ValueError) as e:
        logger.debug(f"Geolocation not available: {e!s}")
        return None


def _beautify_sensor_name(name: str) -> str:
    """
    Convert a system sensor name to a more beautiful display string.

    Examples:
    - cpu_thermal -> CPU Thermal
    - rp1_adc -> RP1 ADC
    - coretemp -> Coretemp
    """
    # Split by underscores and capitalize each word
    words = name.split("_")
    beautified_words = []

    for word in words:
        # Special case: "cpu" should be all caps
        if word.lower() == "cpu":
            beautified_words.append("CPU")
        # Special case: short words (3 chars or less) or already uppercase words
        elif word.isupper() or len(word) <= 3:
            beautified_words.append(word.upper())
        else:
            beautified_words.append(word.capitalize())

    return " ".join(beautified_words)


@app.get(
    "/api/system-status",
    tags=["system"],
    summary="Get system status and monitoring information",
    description="""
Get comprehensive system status information including:
- Operating system details and hardware information
- CPU usage, load averages, and processor information
- Memory (RAM) and swap usage statistics
- Disk usage for all mounted filesystems
- Temperature sensors (when available)
- System uptime and current time

This endpoint is used by the Status page for real-time monitoring.
    """,
    response_description="Detailed system status and monitoring information",
)
async def get_system_status():
    """Get current system status information."""
    # Disable system status in server mode
    if config_loader.is_server_mode():
        return JSONResponse(
            status_code=503,
            content={"error": "System status is not available in server mode"},
        )

    import psutil

    try:
        # Get freedesktop OS release info
        os_release_info = _get_freedesktop_os_release()

        # Get comprehensive system information using psutil
        boot_time = psutil.boot_time()
        current_time = time.time()
        uptime_seconds = current_time - boot_time

        # CPU information
        cpu_times = psutil.cpu_times()._asdict()
        # Usage since the previous call (primed at import), instead of blocking
        # the event loop to measure over an interval
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count()
        cpu_count_logical = psutil.cpu_count(logical=True)

        # Get load averages (available on Unix-like systems)
        load_avg = None
        try:
            load_avg = psutil.getloadavg()  # Returns (1min, 5min, 15min)
        except (AttributeError, OSError):
            # Load averages not available on this platform (e.g., Windows)
            pass

        # Memory information
        memory = psutil.virtual_memory()._asdict()
        swap = psutil.swap_memory()._asdict()

        # Disk information - consolidate devices with multiple mountpoints
        disk_usage = _get_consolidated_disk_usage()

        # Temperature sensors (if available)
        temperatures = {}
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    temperatures[name] = [
                        {
                            "label": entry.label or _beautify_sensor_name(name),
                            "current": entry.current,
                            "high": entry.high,
                            "critical": entry.critical,
                        }
                        for i, entry in enumerate(entries)
                    ]
        except (AttributeError, OSError):
            # Temperature sensors not available on this platform
            pass

        # Get hardware information (Raspberry Pi specific)
        hardware_info = _get_hardware_info()

        # Get tsupdate status information
        tsupdate_status = await _get_tsupdate_status()

        # Get system information
        system_info = {
            "operating_system": f"{platform.system()} {platform.release()}",
            "uptime": uptime_seconds,
            "current_datetime": datetime.datetime.now().astimezone().isoformat(),
            "timezone": str(datetime.datetime.now().astimezone().tzinfo),
            "hostname": socket.gethostname(),
            "version": __version__,
            "os_release": os_release_info,
            "hardware": hardware_info,
            "cpu": {
                "times": cpu_times,
                "percent": cpu_percent,
                "count_physical": cpu_count,
                "count_logical": cpu_count_logical,
                "load_avg": load_avg,  # (1min, 5min, 15min) or None if not available
            },
            "memory": {"virtual": memory, "swap": swap},
            "disk": disk_usage,
            "temperatures": temperatures,
        }

        # Add tsupdate status if available
        if tsupdate_status:
            system_info["tsupdate_status"] = tsupdate_status

        return JSONResponse(content=system_info)
    except Exception as e:
        logger.debug("Failed to get system status", exc_info=True)
        return JSONResponse(status_code=500, content={"error": f"Failed to get system status: {e!s}"})


def _get_freedesktop_os_release():
    """Get freedesktop OS release information."""
    try:
        return platform.freedesktop_os_release()
    except (AttributeError, OSError):
        # platform.freedesktop_os_release() is not available or failed
        return None


# The booted partition and the boot method cannot change while the system runs,
# so a successful `tsupdate status` (about 0.5 s, a Python process start) is
# asked once instead of on every status poll.
_tsupdate_status_cache: dict | None = None


async def _get_tsupdate_status():
    """Get tsupdate status information."""
    global _tsupdate_status_cache
    if _tsupdate_status_cache is not None:
        return _tsupdate_status_cache
    try:
        result = await run_subprocess_async(
            ["tsupdate", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            logger.debug(f"tsupdate status command failed: {result.stderr}")
            return None

        status_data = json.loads(result.stdout)

        # Extract relevant fields
        tsupdate_status = {
            "booted_via_tryboot": status_data.get("booted_via_tryboot", "False"),
            "active_partition": status_data.get("active_partition"),
            "active_partition_label": status_data.get("active_partition_label"),
        }

        _tsupdate_status_cache = tsupdate_status
        return tsupdate_status
    except (TimeoutError, FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        logger.debug(f"Failed to get tsupdate status: {e!s}")
        return None
    except Exception as e:
        logger.debug(f"Unexpected error getting tsupdate status: {e!s}", exc_info=True)
        return None


def _get_hardware_info():
    """Get hardware information from /proc/cpuinfo (Raspberry Pi specific)."""
    hardware_info = {
        "model": None,
        "serial": None,
        "serial_short": None,
        "revision": None,
    }

    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Model"):
                    hardware_info["model"] = line.split(":", 1)[1].strip()
                elif line.startswith("Serial"):
                    serial = line.split(":", 1)[1].strip()
                    hardware_info["serial"] = serial
                    # Get last 8 digits of serial
                    hardware_info["serial_short"] = serial[-8:] if len(serial) >= 8 else serial
                elif line.startswith("Revision"):
                    hardware_info["revision"] = line.split(":", 1)[1].strip()
    except (FileNotFoundError, PermissionError, OSError):
        # /proc/cpuinfo not available or not accessible
        pass

    return hardware_info


def _get_consolidated_disk_usage():
    """Get disk usage information with consolidated mountpoints per device."""
    import collections

    import psutil

    # Group partitions by device
    device_groups = collections.defaultdict(list)

    for partition in psutil.disk_partitions():
        try:
            # Test if we can access this partition
            psutil.disk_usage(partition.mountpoint)
            device_groups[partition.device].append(partition)
        except (PermissionError, OSError):
            # Skip inaccessible partitions
            continue

    disk_usage = []

    for device, partitions in device_groups.items():
        # Use the first partition to get disk usage stats
        # (all mountpoints for the same device will have same stats)
        primary_partition = partitions[0]

        try:
            usage = psutil.disk_usage(primary_partition.mountpoint)

            # Collect all mountpoints and fstypes for this device
            mountpoints = [p.mountpoint for p in partitions]
            fstypes = list({p.fstype for p in partitions})  # Unique fstypes
            primary_fstype = fstypes[0] if fstypes else primary_partition.fstype

            disk_info = {
                "device": device,
                "mountpoints": mountpoints,  # List of all mountpoints
                "mountpoint": mountpoints[0],  # Primary mountpoint for compatibility
                "fstype": primary_fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": (usage.used / usage.total * 100) if usage.total > 0 else 0,
            }
            disk_usage.append(disk_info)

        except (PermissionError, OSError):
            # Skip if we can't get usage stats
            continue

    return disk_usage


@app.get(
    "/api/available-services",
    tags=["system"],
    summary="Get list of available configuration services",
    description="""
Returns a list of services that have configuration files available in the system.
In tracker mode, a service also needs its systemd unit on the device.
This determines which configuration tabs are displayed in the web interface.

Available services may include:
- `schedule` - Schedule configuration
- `radiotracking` - Radio tracking configuration
- `soundscapepipe` - Audio recording configuration

In server mode, you can optionally pass a config_group parameter to check services for a specific group.
    """,
    response_description="List of available configuration services",
)
async def get_available_services(config_group: str | None = None):
    """Get list of services that have configuration files available."""
    available_services = []

    # Determine config directory
    config_dir = None
    if config_group:
        config_dir = config_loader.get_config_group_dir(config_group)
        if not config_dir:
            return JSONResponse(
                status_code=404,
                content={"error": f"Config group '{config_group}' not found"},
            )

    # Check schedule configuration
    try:
        schedule_config = ScheduleConfig(config_dir) if config_dir else ScheduleConfig()
        if schedule_config.config_file.exists():
            available_services.append("schedule")
    except Exception:
        logger.debug("Could not check the schedule configuration", exc_info=True)

    # Check radiotracking configuration
    try:
        radiotracking_config = RadioTrackingConfig(config_dir) if config_dir else RadioTrackingConfig()
        if radiotracking_config.config_file.exists():
            available_services.append("radiotracking")
    except Exception:
        logger.debug("Could not check the radiotracking configuration", exc_info=True)

    # Check soundscapepipe configuration
    try:
        soundscapepipe_config = SoundscapepipeConfig(config_dir) if config_dir else SoundscapepipeConfig()
        if soundscapepipe_config.config_file.exists():
            available_services.append("soundscapepipe")
    except Exception:
        logger.debug("Could not check the soundscapepipe configuration", exc_info=True)

    # Check authorized_keys configuration
    try:
        authorized_keys_config = AuthorizedKeysConfig(config_dir) if config_dir else AuthorizedKeysConfig()
        if authorized_keys_config.config_file.exists():
            available_services.append("authorized_keys")
    except Exception:
        logger.debug("Could not check the authorized_keys configuration", exc_info=True)

    # Check tsupdate configuration
    try:
        tsupdate_config = TsupdateConfig(config_dir) if config_dir else TsupdateConfig()
        if tsupdate_config.config_file.exists():
            available_services.append("tsupdate")
    except Exception:
        logger.debug("Could not check the tsupdate configuration", exc_info=True)

    # On a tracker, a config file alone does not make a service: one copied
    # from another kind of device (soundscapepipe.yml on a radiotracking
    # image) must not show a tab for a service that is not installed.
    if not config_loader.is_server_mode():
        unsupported = await support.unsupported_config_types(available_services)
        available_services = [service for service in available_services if service not in unsupported]

    return {"available_services": available_services}


@app.get(
    "/api/timedatectl-status",
    tags=["system"],
    summary="Get system time and date status",
    description="""
Get system time and date configuration using the `timedatectl` command.

Returns information about:
- Current local time and UTC time
- Time zone configuration
- NTP synchronization status
- RTC (Real-Time Clock) time

This endpoint checks if `timedatectl` is available and returns structured
status information parsed from its output.
    """,
    response_description="System time and date status information",
)
async def get_timedatectl_status():
    """Get system time and date status using timedatectl."""
    try:
        timedatectl_status = {
            "available": False,
            "status": None,
            "error": None,
            "timestamp": datetime.datetime.now().astimezone().isoformat(),
        }

        # Check if timedatectl command is available
        try:
            test_result = await run_subprocess_async(
                ["timedatectl", "--version"], capture_output=True, text=True, timeout=5
            )
            timedatectl_status["available"] = test_result.returncode == 0
        except (TimeoutError, subprocess.TimeoutExpired, FileNotFoundError):
            timedatectl_status["available"] = False
            timedatectl_status["error"] = "timedatectl command not found"
            return JSONResponse(content=timedatectl_status)

        if not timedatectl_status["available"]:
            timedatectl_status["error"] = "timedatectl command not available"
            return JSONResponse(content=timedatectl_status)

        # Get timedatectl status
        try:
            status_result = await run_subprocess_async(
                ["timedatectl", "status"], capture_output=True, text=True, timeout=10
            )

            if status_result.returncode == 0:
                timedatectl_status["status"] = _parse_timedatectl_status(status_result.stdout)
            else:
                timedatectl_status["error"] = f"timedatectl status failed: {status_result.stderr.strip()}"
        except (TimeoutError, subprocess.TimeoutExpired):
            timedatectl_status["error"] = "timedatectl status command timed out"
        except Exception as e:
            logger.debug("Error executing timedatectl status", exc_info=True)
            timedatectl_status["error"] = f"Error executing timedatectl status: {e!s}"

        return JSONResponse(content=timedatectl_status)

    except Exception as e:
        logger.debug("Failed to get timedatectl status", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get timedatectl status: {e!s}"},
        )


def _parse_timedatectl_status(status_output):
    """Parse timedatectl status output into structured data."""
    timedatectl_info = {}

    for line in status_output.strip().split("\n"):
        line = line.strip()
        if line and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            # Convert to snake_case and parse specific fields
            key_snake = key.lower().replace(" ", "_").replace("-", "_")

            # Parse boolean values
            if value.lower() in ["yes", "no"]:
                timedatectl_info[key_snake] = value.lower() == "yes"
            else:
                # Try to parse as integer
                try:
                    timedatectl_info[key_snake] = int(value)
                except ValueError:
                    timedatectl_info[key_snake] = value

    return timedatectl_info


@app.get(
    "/api/network-connectivity",
    tags=["system"],
    summary="Get network connectivity status",
    description="""
Get network connectivity state using the `nmcli networking connectivity` command.

Returns the connectivity state which can be one of:
- `full` - The host is connected to a network and has full access to the Internet
- `limited` - The host is connected to a network, but it has no access to the Internet
- `none` - The host is not connected to any network
- `portal` - The host is behind a captive portal and cannot reach the full Internet
- `unknown` - The connectivity status cannot be found out

This endpoint checks if `nmcli` is available and returns the current connectivity state.
    """,
    response_description="Network connectivity status information",
)
async def get_network_connectivity():
    """Get network connectivity status using nmcli."""
    # Disable network connectivity in server mode
    if config_loader.is_server_mode():
        return JSONResponse(
            status_code=503,
            content={"error": "Network connectivity status is not available in server mode"},
        )

    try:
        network_connectivity_status = {
            "available": False,
            "connectivity": None,
            "error": None,
            "timestamp": datetime.datetime.now().astimezone().isoformat(),
        }

        # Check if nmcli command is available
        try:
            test_result = await run_subprocess_async(["nmcli", "--version"], capture_output=True, text=True, timeout=5)
            network_connectivity_status["available"] = test_result.returncode == 0
        except (TimeoutError, subprocess.TimeoutExpired, FileNotFoundError):
            network_connectivity_status["available"] = False
            network_connectivity_status["error"] = "nmcli command not found"
            return JSONResponse(content=network_connectivity_status)

        if not network_connectivity_status["available"]:
            network_connectivity_status["error"] = "nmcli command not available"
            return JSONResponse(content=network_connectivity_status)

        # Get network connectivity status
        try:
            connectivity_result = await run_subprocess_async(
                ["nmcli", "networking", "connectivity"], capture_output=True, text=True, timeout=10
            )

            if connectivity_result.returncode == 0:
                # Parse the output - should be a single line with the state
                connectivity_state = connectivity_result.stdout.strip().lower()
                # Validate the state is one of the expected values
                valid_states = ["full", "limited", "none", "portal", "unknown"]
                if connectivity_state in valid_states:
                    network_connectivity_status["connectivity"] = connectivity_state
                else:
                    network_connectivity_status["error"] = f"Unexpected connectivity state: {connectivity_state}"
            else:
                network_connectivity_status["error"] = (
                    f"nmcli networking connectivity failed: {connectivity_result.stderr.strip()}"
                )
        except (TimeoutError, subprocess.TimeoutExpired):
            network_connectivity_status["error"] = "nmcli networking connectivity command timed out"
        except Exception as e:
            logger.debug("Error executing nmcli networking connectivity", exc_info=True)
            network_connectivity_status["error"] = f"Error executing nmcli networking connectivity: {e!s}"

        return JSONResponse(content=network_connectivity_status)

    except Exception as e:
        logger.debug("Failed to get network connectivity status", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get network connectivity status: {e!s}"},
        )


async def _get_available_services_for_template(config_group: str | None = None) -> list:
    """Helper function to get available services for template rendering.

    Returns a list of service names that have configuration files available.
    """
    available_services = []

    # Determine config directory
    config_dir = None
    if config_group:
        config_dir = config_loader.get_config_group_dir(config_group)
        if not config_dir:
            return available_services

    # Check radiotracking configuration
    try:
        radiotracking_config = RadioTrackingConfig(config_dir) if config_dir else RadioTrackingConfig()
        if radiotracking_config.config_file.exists():
            available_services.append("radiotracking")
    except Exception:
        logger.debug("Could not check the radiotracking configuration", exc_info=True)

    # Check soundscapepipe configuration
    try:
        soundscapepipe_config = SoundscapepipeConfig(config_dir) if config_dir else SoundscapepipeConfig()
        if soundscapepipe_config.config_file.exists():
            available_services.append("soundscapepipe")
    except Exception:
        logger.debug("Could not check the soundscapepipe configuration", exc_info=True)

    # As in get_available_services: on a tracker the service must be installed
    if not config_loader.is_server_mode():
        unsupported = await support.unsupported_config_types(available_services)
        available_services = [service for service in available_services if service not in unsupported]

    return available_services


@app.get(
    "/",
    include_in_schema=False,
    summary="Web Interface Home Page",
)
async def home(request: Request, config_group: str | None = None):
    """Render the main configuration page with status integration.

    In server mode, accepts optional config_group query parameter to display
    configuration for a specific group.
    """
    # In server mode, validate config_group if provided
    if config_loader.is_server_mode() and config_group:
        available_groups = config_loader.list_config_groups()
        if config_group not in available_groups:
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"Config group '{config_group}' not found",
                    "requested": config_group,
                    "available": available_groups,
                },
            )

    # Get available services for conditional rendering
    available_services = await _get_available_services_for_template(config_group)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "base_url": BASE_URL,
            "config_group": config_group,
            "is_server_mode": config_loader.is_server_mode(),
            "config_loader": config_loader,
            "available_services": available_services,
            "audioprotopnet_enabled": config_loader.is_feature_enabled("audioprotopnet"),
            "mapbox_token": config_loader.get_mapbox_token(),
        },
    )
