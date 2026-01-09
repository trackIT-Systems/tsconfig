"""tsOS Configuration Manager."""

import asyncio
import datetime
import json
import os
import platform
import socket
import subprocess
import time

import psutil
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config_loader import config_loader
from app.utils.subprocess_async import run_subprocess_async
from app.configs.authorized_keys import AuthorizedKeysConfig
from app.configs.geolocation import GeolocationConfig
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.schedule import ScheduleConfig
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.configs.tsupdate import TsupdateConfig
from app.logging_config import get_logger, setup_logging
from app.routers import authorized_keys, configs, network, radiotracking, schedule, shell, soundscapepipe, systemd, tsupdate

# Set up logging for the main application
setup_logging()
logger = get_logger(__name__)

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
]

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
)

# Log application startup
logger.info(f"tsOS Configuration Manager v{__version__} starting up")
logger.debug(f"Base URL: {BASE_URL}")
logger.debug(f"Server mode: {config_loader.is_server_mode()}")

# Include routers
app.include_router(schedule.router)
app.include_router(radiotracking.router)
app.include_router(soundscapepipe.router)
app.include_router(authorized_keys.router)
app.include_router(configs.router)
app.include_router(tsupdate.router)

# Only include system-specific routers in tracker mode (default mode)
# These are disabled in server mode since they require direct hardware access
if not config_loader.is_server_mode():
    app.include_router(systemd.router)
    app.include_router(shell.router)
    app.include_router(network.router)

# Mount static files (must be after all route definitions to avoid conflicts)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


# Add base_url to template context for all responses
@app.middleware("http")
async def add_base_url_to_context(request: Request, call_next):
    """Add base_url to request state for use in templates."""
    request.state.base_url = BASE_URL
    response = await call_next(request)
    return response


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
            except Exception as e:
                debug_info["error"] = str(e)

    return {
        "enabled": is_server_mode,
        "config_groups": config_loader.list_config_groups() if is_server_mode else [],
        "config_root": str(config_root) if is_server_mode and config_root else None,
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
        logger.debug(f"Geolocation not available: {str(e)}")
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
    words = name.split('_')
    beautified_words = []
    
    for word in words:
        # Special case: "cpu" should be all caps
        if word.lower() == 'cpu':
            beautified_words.append('CPU')
        # Special case: short words (3 chars or less) or already uppercase words
        elif word.isupper() or len(word) <= 3:
            beautified_words.append(word.upper())
        else:
            beautified_words.append(word.capitalize())
    
    return ' '.join(beautified_words)


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
- Network connections and I/O statistics
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

    try:
        # Get freedesktop OS release info
        os_release_info = _get_freedesktop_os_release()

        # Get comprehensive system information using psutil
        boot_time = psutil.boot_time()
        current_time = time.time()
        uptime_seconds = current_time - boot_time

        # CPU information
        cpu_times = psutil.cpu_times()._asdict()
        cpu_percent = psutil.cpu_percent(interval=0.1)
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

        # Network information
        try:
            network_connections = len(psutil.net_connections())
        except (psutil.AccessDenied, PermissionError):
            # Network connections require elevated permissions on some systems
            network_connections = None

        network_io = psutil.net_io_counters()._asdict() if psutil.net_io_counters() else {}

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
            "current_datetime": datetime.datetime.now().isoformat(),
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
            "network": {"connections": network_connections, "io_counters": network_io},
            "temperatures": temperatures,
        }

        # Add tsupdate status if available
        if tsupdate_status:
            system_info["tsupdate_status"] = tsupdate_status

        return JSONResponse(content=system_info)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to get system status: {str(e)}"})


def _get_freedesktop_os_release():
    """Get freedesktop OS release information."""
    try:
        return platform.freedesktop_os_release()
    except (AttributeError, OSError):
        # platform.freedesktop_os_release() is not available or failed
        return None


async def _get_tsupdate_status():
    """Get tsupdate status information."""
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
        
        return tsupdate_status
    except (FileNotFoundError, subprocess.TimeoutExpired, asyncio.TimeoutError, json.JSONDecodeError) as e:
        logger.debug(f"Failed to get tsupdate status: {str(e)}")
        return None
    except Exception as e:
        logger.debug(f"Unexpected error getting tsupdate status: {str(e)}")
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
            fstypes = list(set(p.fstype for p in partitions))  # Unique fstypes
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
This determines which configuration tabs are displayed in the web interface.

Available services may include:
- `schedule` - Schedule configuration
- `radiotracking` - Radio tracking configuration
- `soundscapepipe` - Audio recording configuration

In server mode, you can optionally pass a config_group parameter to check services for a specific group.
    """,
    response_description="List of available configuration services",
)
async def get_available_services(config_group: str = None):
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
        pass

    # Check radiotracking configuration
    try:
        radiotracking_config = RadioTrackingConfig(config_dir) if config_dir else RadioTrackingConfig()
        if radiotracking_config.config_file.exists():
            available_services.append("radiotracking")
    except Exception:
        pass

    # Check soundscapepipe configuration
    try:
        soundscapepipe_config = SoundscapepipeConfig(config_dir) if config_dir else SoundscapepipeConfig()
        if soundscapepipe_config.config_file.exists():
            available_services.append("soundscapepipe")
    except Exception:
        pass

    # Check authorized_keys configuration
    try:
        authorized_keys_config = AuthorizedKeysConfig(config_dir) if config_dir else AuthorizedKeysConfig()
        if authorized_keys_config.config_file.exists():
            available_services.append("authorized_keys")
    except Exception:
        pass

    # Check tsupdate configuration
    try:
        tsupdate_config = TsupdateConfig(config_dir) if config_dir else TsupdateConfig()
        if tsupdate_config.config_file.exists():
            available_services.append("tsupdate")
    except Exception:
        pass

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
            "timestamp": datetime.datetime.now().isoformat(),
        }

        # Check if timedatectl command is available
        try:
            test_result = await run_subprocess_async(["timedatectl", "--version"], capture_output=True, text=True, timeout=5)
            timedatectl_status["available"] = test_result.returncode == 0
        except (subprocess.TimeoutExpired, asyncio.TimeoutError, FileNotFoundError):
            timedatectl_status["available"] = False
            timedatectl_status["error"] = "timedatectl command not found"
            return JSONResponse(content=timedatectl_status)

        if not timedatectl_status["available"]:
            timedatectl_status["error"] = "timedatectl command not available"
            return JSONResponse(content=timedatectl_status)

        # Get timedatectl status
        try:
            status_result = await run_subprocess_async(["timedatectl", "status"], capture_output=True, text=True, timeout=10)

            if status_result.returncode == 0:
                timedatectl_status["status"] = _parse_timedatectl_status(status_result.stdout)
            else:
                timedatectl_status["error"] = f"timedatectl status failed: {status_result.stderr.strip()}"
        except (subprocess.TimeoutExpired, asyncio.TimeoutError):
            timedatectl_status["error"] = "timedatectl status command timed out"
        except Exception as e:
            timedatectl_status["error"] = f"Error executing timedatectl status: {str(e)}"

        return JSONResponse(content=timedatectl_status)

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get timedatectl status: {str(e)}"},
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
    "/",
    include_in_schema=False,
    summary="Web Interface Home Page",
)
async def home(request: Request, config_group: str = None):
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

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "version": __version__,
            "base_url": BASE_URL,
            "config_group": config_group,
            "is_server_mode": config_loader.is_server_mode(),
            "config_loader": config_loader,
        },
    )
