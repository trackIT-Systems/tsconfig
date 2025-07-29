"""tsOS Configuration Manager."""

import datetime
import platform
import socket
import time
import subprocess
import json
from pathlib import Path

import psutil
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.routers import radiotracking, schedule, soundscapepipe, systemd, shell
from app.configs.schedule import ScheduleConfig
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.soundscapepipe import SoundscapepipeConfig

app = FastAPI(title="tsOS Configuration")

# Include routers
app.include_router(schedule.router)
app.include_router(radiotracking.router)
app.include_router(soundscapepipe.router)
app.include_router(systemd.router)
app.include_router(shell.router)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


@app.get("/")
async def home(request: Request):
    """Render the main configuration page with status integration."""
    return templates.TemplateResponse("index.html", {"request": request, "version": __version__})


@app.get("/api/system-status")
async def get_system_status():
    """Get current system status information."""
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

        # Disk information
        disk_usage = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disk_usage.append(
                    {
                        "device": partition.device,
                        "mountpoint": partition.mountpoint,
                        "fstype": partition.fstype,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": (usage.used / usage.total * 100) if usage.total > 0 else 0,
                    }
                )
            except (PermissionError, OSError):
                # Skip inaccessible partitions
                continue

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
                            "label": entry.label or f"Sensor {i + 1}",
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


def _get_hardware_info():
    """Get hardware information from /proc/cpuinfo (Raspberry Pi specific)."""
    hardware_info = {
        "model": None,
        "serial": None,
        "serial_short": None,
        "revision": None,
    }
    
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('Model'):
                    hardware_info["model"] = line.split(':', 1)[1].strip()
                elif line.startswith('Serial'):
                    serial = line.split(':', 1)[1].strip()
                    hardware_info["serial"] = serial
                    # Get last 8 digits of serial
                    hardware_info["serial_short"] = serial[-8:] if len(serial) >= 8 else serial
                elif line.startswith('Revision'):
                    hardware_info["revision"] = line.split(':', 1)[1].strip()
    except (FileNotFoundError, PermissionError, OSError):
        # /proc/cpuinfo not available or not accessible
        pass
    
    return hardware_info


@app.get("/api/available-services")
async def get_available_services():
    """Get list of services that have configuration files available."""
    available_services = []
    
    # Check schedule configuration
    try:
        schedule_config = ScheduleConfig()
        if schedule_config.config_file.exists():
            available_services.append("schedule")
    except Exception:
        pass
    
    # Check radiotracking configuration
    try:
        radiotracking_config = RadioTrackingConfig()
        if radiotracking_config.config_file.exists():
            available_services.append("radiotracking")
    except Exception:
        pass
    
    # Check soundscapepipe configuration
    try:
        soundscapepipe_config = SoundscapepipeConfig()
        if soundscapepipe_config.config_file.exists():
            available_services.append("soundscapepipe")
    except Exception:
        pass
    
    return {"available_services": available_services}

@app.get("/api/timedatectl-status")
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
            test_result = subprocess.run(
                ["timedatectl", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            timedatectl_status["available"] = test_result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            timedatectl_status["available"] = False
            timedatectl_status["error"] = "timedatectl command not found"
            return JSONResponse(content=timedatectl_status)
        
        if not timedatectl_status["available"]:
            timedatectl_status["error"] = "timedatectl command not available"
            return JSONResponse(content=timedatectl_status)
        
        # Get timedatectl status
        try:
            status_result = subprocess.run(
                ["timedatectl", "status"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if status_result.returncode == 0:
                timedatectl_status["status"] = _parse_timedatectl_status(status_result.stdout)
            else:
                timedatectl_status["error"] = f"timedatectl status failed: {status_result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            timedatectl_status["error"] = "timedatectl status command timed out"
        except Exception as e:
            timedatectl_status["error"] = f"Error executing timedatectl status: {str(e)}"
        
        return JSONResponse(content=timedatectl_status)
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to get timedatectl status: {str(e)}"})


def _parse_timedatectl_status(status_output):
    """Parse timedatectl status output into structured data."""
    timedatectl_info = {}
    
    for line in status_output.strip().split('\n'):
        line = line.strip()
        if line and ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            
            # Convert to snake_case and parse specific fields
            key_snake = key.lower().replace(' ', '_').replace('-', '_')
            
            # Parse boolean values
            if value.lower() in ['yes', 'no']:
                timedatectl_info[key_snake] = value.lower() == 'yes'
            else:
                # Try to parse as integer
                try:
                    timedatectl_info[key_snake] = int(value)
                except ValueError:
                    timedatectl_info[key_snake] = value
    
    return timedatectl_info

