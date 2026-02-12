"""Configuration file upload and download endpoints."""

import asyncio
import calendar
import os
import socket
import subprocess
import zipfile
from configparser import ConfigParser
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Path as PathParam, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.config_loader import config_loader
from app.utils.subprocess_async import run_subprocess_async
from app.configs.authorized_keys import AuthorizedKeysConfig
from app.configs.cmdline import CmdlineConfig
from app.configs.geolocation import GeolocationConfig
from app.configs.mosquitto_cert import MosquittoCertConfig
from app.configs.mosquitto_conf import MosquittoConfConfig
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.schedule import ScheduleConfig
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.configs.wireguard import WireguardConfig

router = APIRouter(prefix="/api/configs", tags=["configs"])


def round_mtime_for_fat32(dt: datetime) -> datetime:
    """Round datetime up to next even second for FAT32 compatibility.
    
    FAT32 has 2-second resolution for modification times. To prevent duplicate
    uploads when timestamps have odd seconds, we round UP to the next even second.
    
    Args:
        dt: Input datetime
        
    Returns:
        Datetime rounded up to next even second
    """
    # Remove microseconds first
    dt = dt.replace(microsecond=0)
    
    if dt.second % 2 == 1:
        # Odd second - add 1 second to round up to next even second
        # This automatically handles minute/hour/day rollover
        return dt + timedelta(seconds=1)
    else:
        # Even second - already good
        return dt


async def restart_systemd_service(service_name: str) -> tuple[bool, Optional[str]]:
    """Restart a systemd service.

    Args:
        service_name: Name of the service to restart

    Returns:
        Tuple of (success, error_message)
    """
    try:
        result = await run_subprocess_async(
            ["systemctl", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else f"Command failed with exit code {result.returncode}"
            return False, error_msg

        return True, None

    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        return False, "Service restart timed out"
    except Exception as e:
        return False, str(e)


def parse_ini_file(content: str) -> Dict[str, Any]:
    """Parse INI file content and return as dictionary.

    Args:
        content: String content of INI file

    Returns:
        Dictionary representation of INI file

    Raises:
        ValueError: If INI file cannot be parsed
    """
    try:
        parser = ConfigParser()
        parser.read_string(content)

        if not parser.sections():
            raise ValueError("INI file is empty or has no sections")

        # Use RadioTrackingConfig's conversion logic for proper type handling
        temp_config = RadioTrackingConfig()
        data = {}
        for section in parser.sections():
            data[section] = {key: temp_config._convert_value(value) for key, value in parser[section].items()}

        return data
    except Exception as e:
        raise ValueError(f"Failed to parse INI file: {str(e)}")


def parse_mtime_and_validate(mtime: str, force: bool) -> datetime:
    """Parse and validate mtime parameter.
    
    Args:
        mtime: ISO timestamp string (interpreted as UTC if no timezone specified)
        force: Whether force flag is set
        
    Returns:
        Parsed datetime (naive, in UTC)
        
    Raises:
        HTTPException: If validation fails
    """
    try:
        dt = datetime.fromisoformat(mtime)
        # If naive (no timezone), interpret as UTC
        if dt.tzinfo is None:
            # Already naive, just treating it as UTC implicitly
            return dt
        else:
            # Convert to UTC and make naive
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mtime format '{mtime}'. Expected ISO format like '2025-10-17T08:59:50' or '2025-10-17T08:59:50+00:00'",
            )
    
    return None


def create_config_instance(filename: str, config_type: str, content_str: str):
    """Create appropriate config instance and parse content.
    
    Args:
        filename: Name of config file
        config_type: Type of config
        content_str: File content as string
        
    Returns:
        Tuple of (config_instance, parsed_config)
        
    Raises:
        HTTPException: If parsing fails
    """
    try:
        if config_type == "radiotracking":
            parsed_config = parse_ini_file(content_str)
            config_instance = RadioTrackingConfig()
        elif config_type == "schedule":
            parsed_config = parse_yaml_file(content_str)
            config_instance = ScheduleConfig()
        elif config_type == "soundscapepipe":
            parsed_config = parse_yaml_file(content_str)
            config_instance = SoundscapepipeConfig()
        elif config_type == "authorized_keys":
            # Use config upload mode to write to /boot/firmware/authorized_keys
            config_instance = AuthorizedKeysConfig(is_config_upload=True)
            
            # Parse keys from uploaded content (no merging with existing)
            new_keys = []
            for line in content_str.strip().split("\n"):
                parsed = config_instance._parse_key_line(line, len(new_keys))
                if parsed:
                    # Mark as server key
                    parsed["source"] = "server"
                    new_keys.append(parsed)
            
            parsed_config = {"keys": new_keys}
        elif config_type in ["cmdline", "wireguard", "mosquitto_cert", "mosquitto_conf"]:
            parsed_config = {"content": content_str}
            if config_type == "cmdline":
                config_instance = CmdlineConfig()
            elif config_type == "wireguard":
                config_instance = WireguardConfig()
            elif config_type == "mosquitto_cert":
                config_instance = MosquittoCertConfig()
            else:  # mosquitto_conf
                config_instance = MosquittoConfConfig()
        elif config_type == "geolocation":
            lines = content_str.strip().split("\n")
            data_lines = [
                line.split("#")[0].strip() for line in lines if line.strip() and not line.strip().startswith("#")
            ]
            if len(data_lines) != 4:
                raise HTTPException(
                    status_code=400,
                    detail=f"Geolocation file must have exactly 4 data lines (got {len(data_lines)})",
                )
            try:
                parsed_config = {
                    "lat": float(data_lines[0]),
                    "lon": float(data_lines[1]),
                    "alt": float(data_lines[2]),
                    "accuracy": float(data_lines[3]),
                }
            except (ValueError, IndexError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid geolocation file format: {str(e)}",
                )
            config_instance = GeolocationConfig()
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported configuration file: {filename}. "
                "Supported files are: radiotracking.ini, schedule.yml, soundscapepipe.yml, authorized_keys, "
                "cmdline.txt, wireguard.conf, server.crt, server.conf, geolocation",
            )
        
        return config_instance, parsed_config
    
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to parse configuration file: {str(e)}",
        )


def build_standard_response(success: bool, config_type: str, filename: str, **kwargs) -> Dict[str, Any]:
    """Build standardized response structure.
    
    Args:
        success: Whether operation was successful
        config_type: Type of configuration
        filename: Name of uploaded file
        **kwargs: Additional response fields
        
    Returns:
        Standardized response dictionary
    """
    response = {
        "success": success,
        "config_type": config_type,
        "filename": filename,
        "timestamp": datetime.now().isoformat(),
    }
    response.update(kwargs)
    return response


async def handle_service_restart(config_type: str, restart_service: bool) -> Dict[str, Any]:
    """Handle service restart logic.
    
    Args:
        config_type: Type of configuration
        restart_service: Whether to restart service
        
    Returns:
        Dictionary with restart results
    """
    result = {
        "service_restarted": False,
        "service_restart_error": None,
    }
    
    if not restart_service:
        return result
    
    if config_loader.is_server_mode():
        result["service_restart_error"] = "Service restart is not available in server mode"
        return result
    
    service_mapping = {
        "radiotracking": "radiotracking",
        "schedule": "tsschedule", 
        "soundscapepipe": "soundscapepipe",
    }
    
    service_name = service_mapping.get(config_type)
    if not service_name:
        result["service_restart_error"] = f"No service mapping for config type: {config_type}"
        return result
    
    success, error = await restart_systemd_service(service_name)
    result["service_restarted"] = success
    if error:
        result["service_restart_error"] = error
    
    return result


def parse_yaml_file(content: str) -> Dict[str, Any]:
    """Parse YAML file content and return as dictionary.

    Args:
        content: String content of YAML file

    Returns:
        Dictionary representation of YAML file

    Raises:
        ValueError: If YAML file cannot be parsed
    """
    try:
        data = yaml.safe_load(content)
        if data is None:
            raise ValueError("YAML file is empty")
        if not isinstance(data, dict):
            raise ValueError("YAML file must contain a mapping/dictionary at root level")
        return data
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML file: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to parse YAML file: {str(e)}")


@router.get("/")
async def list_configs():
    """List all existing configuration files with their modification times.

    Returns metadata for all configuration files that exist on the system,
    including their filenames and last modified timestamps.

    This endpoint is disabled in server mode.

    Returns:
        JSON object with:
        - files: List of config file metadata (filename, mtime)
        - count: Number of existing config files
        - most_recent_mtime: ISO timestamp of most recently modified file
    """
    # Check if server mode is enabled - disable endpoint if so
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="Config download endpoints are disabled in server mode",
        )

    files_metadata = []
    most_recent_mtime_timestamp = None

    for filename, config_type in RECOGNIZED_CONFIG_FILES.items():
        try:
            config_instance = get_config_instance(config_type)
            
            if config_instance.config_file.exists():
                file_stat = config_instance.config_file.stat()
                mtime = datetime.utcfromtimestamp(file_stat.st_mtime)
                
                files_metadata.append({
                    "filename": filename,
                    "mtime": mtime.isoformat() + "Z",  # Add Z to indicate UTC
                })
                
                # Track most recent mtime
                if most_recent_mtime_timestamp is None or file_stat.st_mtime > most_recent_mtime_timestamp:
                    most_recent_mtime_timestamp = file_stat.st_mtime
        except Exception:
            # Skip files that can't be accessed
            continue

    # Build response
    response_data = {
        "files": files_metadata,
        "count": len(files_metadata),
    }

    # Add most recent mtime if any files exist
    if most_recent_mtime_timestamp is not None:
        most_recent_dt = datetime.utcfromtimestamp(most_recent_mtime_timestamp)
        response_data["most_recent_mtime"] = most_recent_dt.isoformat() + "Z"

    # Prepare response with Last-Modified header
    response_headers = {}
    if most_recent_mtime_timestamp is not None:
        http_date = formatdate(most_recent_mtime_timestamp, usegmt=True)
        response_headers["Last-Modified"] = http_date

    return JSONResponse(content=response_data, headers=response_headers)


@router.post("/update")
async def upload_config(
    file: UploadFile = File(..., description="Configuration file to upload"),
    restart_service: bool = Form(False, description="Restart the respective service after upload"),
    mtime: str = Form(..., description="File modification time in ISO format (e.g., 2025-10-17T08:59:50)"),
    force: bool = Form(False, description="Force overwrite regardless of file modification time"),
):
    """Upload and validate a configuration file.

    Supported files:
    - radiotracking.ini - Radio tracking configuration
    - schedule.yml - Schedule configuration
    - soundscapepipe.yml - Soundscapepipe configuration
    - authorized_keys - SSH authorized keys
    - cmdline.txt - Kernel boot parameters
    - wireguard.conf - WireGuard VPN configuration
    - server.crt - Mosquitto server certificate
    - server.conf - Mosquitto server configuration
    - geolocation - Geolocation file (geoclue format)

    The file will be validated and if valid, will replace the existing configuration.
    
    mtime parameter:
    - When force=False: File is only overwritten if uploaded mtime is newer than existing file
    - When force=True: File is always overwritten regardless of timestamps
    - The uploaded file's mtime is preserved and set on the target file
    
    Special Handling:
    - If cmdline.txt is uploaded with restart_service=True, the system will reboot instead of 
      attempting a service restart (since cmdline.txt changes require a reboot to take effect)
    - For other config files, the respective systemd service can be restarted after upload

    This endpoint is disabled in server mode.

    Args:
        file: The configuration file to upload
        restart_service: Whether to restart the respective service after successful upload (default: False). 
                        For cmdline.txt, this triggers a system reboot instead.
        mtime: File modification time in ISO format (e.g., 2025-10-17T08:59:50)
        force: Force overwrite regardless of file modification time (default: False)

    Returns:
        Success message if successful, validation errors if invalid, or skipped if file is not newer
    """
    # Check if server mode is enabled - disable endpoint if so
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="Config upload endpoints are disabled in server mode",
        )

    # Read and decode file content
    try:
        content = await file.read()
        content_str = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    # Determine config type from filename
    filename_lower = file.filename.lower() if file.filename else ""
    filename_to_type = {
        "radiotracking.ini": "radiotracking",
        "schedule.yml": "schedule", 
        "soundscapepipe.yml": "soundscapepipe",
        "authorized_keys": "authorized_keys",
        "cmdline.txt": "cmdline",
        "wireguard.conf": "wireguard",
        "server.crt": "mosquitto_cert",
        "server.conf": "mosquitto_conf",
        "geolocation": "geolocation",
    }
    
    config_type = filename_to_type.get(filename_lower)
    if not config_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported configuration file: {file.filename}. "
            f"Supported files are: {', '.join(filename_to_type.keys())}",
        )

    # Validate mtime and parse if needed
    upload_mtime = parse_mtime_and_validate(mtime, force)
    
    # Create config instance and parse content
    config_instance, parsed_config = create_config_instance(file.filename, config_type, content_str)
    
    # Validate the configuration
    validation_errors = config_instance.validate(parsed_config)
    if validation_errors:
        return build_standard_response(
            success=False,
            config_type=config_type,
            filename=file.filename,
            valid=False,
            errors=validation_errors,
            message=f"Configuration file '{file.filename}' is invalid",
        )

    # Check mtime comparison when not forced
    existing_mtime = None
    if not force:
        upload_mtime_rounded = round_mtime_for_fat32(upload_mtime)
        
        if config_instance.config_file.exists():
            # Read file mtime as UTC (naive datetime)
            existing_mtime = datetime.utcfromtimestamp(config_instance.config_file.stat().st_mtime)
            
            if upload_mtime_rounded <= existing_mtime:
                return build_standard_response(
                    success=False,
                    config_type=config_type,
                    filename=file.filename,
                    valid=True,
                    skipped=True,
                    message=f"File is not newer. Upload: {upload_mtime.isoformat()} "
                           f"(rounded: {upload_mtime_rounded.isoformat()}), Existing: {existing_mtime.isoformat()}",
                    upload_mtime=upload_mtime.isoformat(),
                    upload_mtime_rounded=upload_mtime_rounded.isoformat(),
                    existing_mtime=existing_mtime.isoformat(),
                    config_path=str(config_instance.config_file),
                )

    # Save configuration
    save_metadata = {}
    try:
        save_result = config_instance.save(parsed_config)
        # Capture metadata if save() returns any (e.g., hostname changes for cmdline)
        if save_result and isinstance(save_result, dict):
            save_metadata = save_result
        
        # Set file mtime from uploaded mtime
        upload_mtime_rounded = round_mtime_for_fat32(upload_mtime)
        # Convert naive UTC datetime to Unix timestamp using timegm (treats as UTC)
        timestamp = calendar.timegm(upload_mtime_rounded.timetuple())
        os.utime(config_instance.config_file, (timestamp, timestamp))
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {str(e)}")

    # Build success response
    message = f"Configuration file '{file.filename}' uploaded successfully"
    if force:
        message += " (forced - mtime preserved)"
    else:
        message += " (mtime preserved)"
    
    response_data = {
        "valid": True,
        "message": message,
        "config_path": str(config_instance.config_file),
        "mtime_set": True,
        "force_used": force,
    }
    
    # Add save metadata (e.g., hostname_changed for cmdline.txt)
    if save_metadata:
        response_data.update(save_metadata)
    
    # Add mtime info
    upload_mtime_rounded = round_mtime_for_fat32(upload_mtime)
    response_data.update({
        "upload_mtime": upload_mtime.isoformat(),
        "upload_mtime_rounded": upload_mtime_rounded.isoformat(),
    })
    if existing_mtime:
        response_data["existing_mtime"] = existing_mtime.isoformat()
    
    # Handle service restart or reboot
    # Special case: cmdline.txt requires reboot, not service restart
    if config_type == "cmdline" and restart_service:
        # Reboot the system instead of restarting a service
        # Schedule reboot in 10 seconds to allow response to reach client
        response_data["reboot_initiated"] = False
        response_data["reboot_delay_seconds"] = 10
        try:
            # Use systemd-run to schedule reboot in 10 seconds
            await run_subprocess_async(
                ["systemd-run", "--on-active=10s", "systemctl", "reboot"],
                capture_output=True,
                text=True,
                timeout=5
            )
            response_data["message"] += ". System reboot scheduled in 10 seconds (cmdline.txt requires reboot)"
            response_data["reboot_initiated"] = True
        except Exception as e:
            response_data["message"] += f" (Warning: Failed to schedule reboot: {str(e)})"
            response_data["reboot_initiated"] = False
    else:
        # Normal service restart for other config types
        restart_result = await handle_service_restart(config_type, restart_service)
        response_data.update(restart_result)
        
        # Update message with restart info
        if restart_result["service_restarted"]:
            response_data["message"] += f" and service restarted"
        elif restart_result["service_restart_error"]:
            response_data["message"] += f" (Service restart failed: {restart_result['service_restart_error']})"

    return build_standard_response(success=True, config_type=config_type, filename=file.filename, **response_data)


# Recognized configuration files
RECOGNIZED_CONFIG_FILES = {
    "radiotracking.ini": "radiotracking",
    "schedule.yml": "schedule",
    "soundscapepipe.yml": "soundscapepipe",
    "authorized_keys": "authorized_keys",
    "cmdline.txt": "cmdline",
    "wireguard.conf": "wireguard",
    "server.crt": "mosquitto_cert",
    "server.conf": "mosquitto_conf",
    "geolocation": "geolocation",
}

# Service mapping for restart
SERVICE_MAPPING = {
    "radiotracking": "radiotracking",
    "schedule": "tsschedule",
    "soundscapepipe": "soundscapepipe",
    "wireguard": "wg-quick@wireguard",
    "mosquitto_cert": "mosquitto",
    "mosquitto_conf": "mosquitto",
}


def get_config_instance(config_type: str, is_config_upload: bool = False):
    """Get appropriate config instance for the given type.

    Args:
        config_type: Type of configuration (radiotracking, schedule, soundscapepipe, authorized_keys,
                     cmdline, wireguard, mosquitto_cert, mosquitto_conf, geolocation)
        is_config_upload: Whether this is for config upload (affects authorized_keys behavior)

    Returns:
        Config instance for the specified type
    """
    if config_type == "radiotracking":
        return RadioTrackingConfig()
    elif config_type == "schedule":
        return ScheduleConfig()
    elif config_type == "soundscapepipe":
        return SoundscapepipeConfig()
    elif config_type == "authorized_keys":
        return AuthorizedKeysConfig(is_config_upload=is_config_upload)
    elif config_type == "cmdline":
        return CmdlineConfig()
    elif config_type == "wireguard":
        return WireguardConfig()
    elif config_type == "mosquitto_cert":
        return MosquittoCertConfig()
    elif config_type == "mosquitto_conf":
        return MosquittoConfConfig()
    elif config_type == "geolocation":
        return GeolocationConfig()
    else:
        raise ValueError(f"Unknown config type: {config_type}")


def extract_zip_file_timestamps(zip_buffer: BytesIO) -> Dict[str, datetime]:
    """Extract file timestamps from zip archive.
    
    Args:
        zip_buffer: BytesIO buffer containing zip data
        
    Returns:
        Dictionary mapping filename to datetime object (naive, interpreted as UTC)
    """
    timestamps = {}
    
    with zipfile.ZipFile(zip_buffer, "r") as zip_file:
        for zip_info in zip_file.infolist():
            # Skip directories
            if zip_info.filename.endswith("/"):
                continue
                
            # Get just the basename (ignore any directory structure in zip)
            basename = zip_info.filename.split("/")[-1]
            
            # Convert zip timestamp to datetime (interpreted as UTC)
            # zip_info.date_time is (year, month, day, hour, minute, second)
            # Zip timestamps don't have timezone info, so we treat them as UTC
            if len(zip_info.date_time) >= 6:
                zip_datetime = datetime(*zip_info.date_time[:6])
                timestamps[basename] = zip_datetime
    
    return timestamps


def compare_file_timestamps(config_instances: Dict[str, Any], zip_timestamps: Dict[str, datetime]) -> Dict[str, Dict[str, Any]]:
    """Compare zip file timestamps with existing files on disk.
    
    Args:
        config_instances: Dictionary of config instances
        zip_timestamps: Dictionary of zip file timestamps
        
    Returns:
        Dictionary with comparison results for each file
    """
    comparison_results = {}
    
    for filename, config_instance in config_instances.items():
        zip_timestamp = zip_timestamps.get(filename)
        if not zip_timestamp:
            continue
            
        result = {
            "zip_timestamp": zip_timestamp.isoformat(),
            "zip_timestamp_rounded": round_mtime_for_fat32(zip_timestamp).isoformat(),
            "exists_on_disk": config_instance.config_file.exists(),
            "existing_timestamp": None,
            "is_newer": False,
            "should_update": False,
        }
        
        if config_instance.config_file.exists():
            # Read file mtime as UTC (naive datetime)
            existing_mtime = datetime.utcfromtimestamp(config_instance.config_file.stat().st_mtime)
            result["existing_timestamp"] = existing_mtime.isoformat()
            
            # Compare rounded zip timestamp with existing timestamp
            zip_timestamp_rounded = round_mtime_for_fat32(zip_timestamp)
            result["is_newer"] = zip_timestamp_rounded > existing_mtime
            result["should_update"] = result["is_newer"]
        else:
            # File doesn't exist, should update
            result["should_update"] = True
            result["is_newer"] = True
            
        comparison_results[filename] = result
    
    return comparison_results


def parse_config_file(filename: str, content: str) -> Dict[str, Any]:
    """Parse configuration file content based on filename.

    Args:
        filename: Name of the configuration file
        content: String content of the file

    Returns:
        Parsed configuration dictionary

    Raises:
        ValueError: If file cannot be parsed
    """
    if filename.endswith(".ini"):
        return parse_ini_file(content)
    elif filename.endswith(".yml") or filename.endswith(".yaml"):
        return parse_yaml_file(content)
    elif filename == "authorized_keys":
        # For authorized_keys, parse each line
        # Use config upload mode to target /boot/firmware/authorized_keys
        keys = []
        temp_config = AuthorizedKeysConfig(is_config_upload=True)
        for idx, line in enumerate(content.strip().split("\n")):
            parsed = temp_config._parse_key_line(line, idx)
            if parsed:
                # Mark as server key
                parsed["source"] = "server"
                keys.append(parsed)
        return {"keys": keys}
    elif filename in ["cmdline.txt", "wireguard.conf", "server.crt", "server.conf"]:
        # Plain text files - return content as-is
        return {"content": content}
    elif filename == "geolocation":
        # Parse geolocation file format
        lines = content.strip().split("\n")
        data_lines = [line.split("#")[0].strip() for line in lines if line.strip() and not line.strip().startswith("#")]

        if len(data_lines) != 4:
            raise ValueError(f"Geolocation file must have exactly 4 data lines (got {len(data_lines)})")

        try:
            return {
                "lat": float(data_lines[0]),
                "lon": float(data_lines[1]),
                "alt": float(data_lines[2]),
                "accuracy": float(data_lines[3]),
            }
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid geolocation file format: {str(e)}")
    else:
        raise ValueError(f"Unsupported file type: {filename}")


def _apply_config_zip_from_path(
    zip_path: Path,
    force: bool = True,
) -> Tuple[List[str], bool]:
    """Apply configuration files from a local zip file.

    Used by system reset to reset config from tsos-default-name_config.zip.
    Does not restart services or reboot.

    Args:
        zip_path: Path to the zip file
        force: If True, overwrite all files regardless of timestamps (default True)

    Returns:
        Tuple of (saved_files, cmdline_updated)
        - saved_files: List of filenames that were successfully saved
        - cmdline_updated: True if cmdline.txt was in saved_files

    Raises:
        FileNotFoundError: If zip file does not exist
        ValueError: If zip is invalid or validation fails
    """
    if not isinstance(zip_path, Path):
        zip_path = Path(zip_path)

    if not zip_path.exists():
        raise FileNotFoundError(f"Zip file not found: {zip_path}")

    with open(zip_path, "rb") as f:
        zip_buffer = BytesIO(f.read())

    try:
        with zipfile.ZipFile(zip_buffer, "r") as zip_file:
            zip_file.testzip()
            file_list = zip_file.namelist()
    except zipfile.BadZipFile:
        raise ValueError("Invalid zip file")

    zip_timestamps = extract_zip_file_timestamps(zip_buffer)

    recognized_files = {}
    with zipfile.ZipFile(zip_buffer, "r") as zip_file:
        for filename in file_list:
            if filename.endswith("/"):
                continue
            basename = filename.split("/")[-1]
            if basename in RECOGNIZED_CONFIG_FILES:
                try:
                    file_content = zip_file.read(filename).decode("utf-8")
                    recognized_files[basename] = file_content
                except UnicodeDecodeError:
                    raise ValueError(f"File '{basename}' must be UTF-8 encoded text")

    if not recognized_files:
        raise ValueError(
            f"No recognized configuration files found in zip. "
            f"Supported files are: {', '.join(RECOGNIZED_CONFIG_FILES.keys())}"
        )

    parsed_configs = {}
    config_instances = {}
    for filename, content in recognized_files.items():
        config_type = RECOGNIZED_CONFIG_FILES[filename]
        config_instance = get_config_instance(config_type, is_config_upload=True)
        config_instances[filename] = config_instance
        parsed_config = parse_config_file(filename, content)
        validation_errors = config_instance.validate(parsed_config)
        if validation_errors:
            raise ValueError(f"Validation failed for {filename}: {'; '.join(validation_errors)}")
        parsed_configs[filename] = parsed_config

    timestamp_comparisons = compare_file_timestamps(config_instances, zip_timestamps)
    files_to_save = (
        list(recognized_files.keys())
        if force
        else [f for f in recognized_files.keys() if timestamp_comparisons.get(f, {}).get("should_update", True)]
    )

    saved_files = []
    for filename in files_to_save:
        config_instances[filename].save(parsed_configs[filename])
        zip_timestamp = zip_timestamps.get(filename)
        if zip_timestamp:
            zip_timestamp_rounded = round_mtime_for_fat32(zip_timestamp)
            config_file_path = config_instances[filename].config_file
            timestamp = calendar.timegm(zip_timestamp_rounded.timetuple())
            os.utime(config_file_path, (timestamp, timestamp))
        saved_files.append(filename)

    cmdline_updated = "cmdline.txt" in saved_files
    return saved_files, cmdline_updated


@router.post(".zip")
async def upload_config_zip(
    file: UploadFile = File(..., description="Zip file containing configuration files"),
    restart_services: bool = Form(False, description="Restart affected services after upload"),
    pedantic: bool = Form(False, description="Reject upload if unknown files are present or if any existing file is newer"),
    force: bool = Form(False, description="Force overwrite regardless of file modification time"),
    reboot: str = Form("allow", description="Reboot policy: 'forbid' (no reboot), 'allow' (default, reboot if requested), 'force' (always reboot)"),
):
    """Upload and validate multiple configuration files from a zip archive.

    The zip file should contain configuration files at the root level:
    - radiotracking.ini - Radio tracking configuration
    - schedule.yml - Schedule configuration
    - soundscapepipe.yml - Soundscapepipe configuration
    - authorized_keys - SSH authorized keys
    - cmdline.txt - Kernel boot parameters
    - wireguard.conf - WireGuard VPN configuration
    - server.crt - Mosquitto server certificate
    - server.conf - Mosquitto server configuration
    - geolocation - Geolocation file (geoclue format)

    All files will be validated before any changes are made. If any file fails validation,
    no files will be modified. If all files are valid, they will be saved based on the mode.

    Upload Modes:
    - Force mode (force=True): Overwrites all files regardless of timestamps
    - Default mode (force=False, pedantic=False): Only overwrites files newer than existing
    - Pedantic mode (force=False, pedantic=True): Rejects upload if any existing file is newer OR unknown files present
    
    Note: Zip file timestamps are always preserved and set on the target files (regardless of mode)

    Reboot Modes:
    - forbid: Never reboot, even if cmdline.txt is updated (user warned if changes require reboot)
    - allow (default): Automatically reboot if cmdline.txt is updated (requires reboot to take effect), otherwise no reboot
    - force: Always reboot after successful upload regardless of which files were updated

    This endpoint is disabled in server mode.

    Args:
        file: Zip file containing configuration files
        restart_services: Whether to restart affected services after successful upload (default: False). Ignored if reboot is 'force'.
        pedantic: Reject upload if unknown files are present or if any existing file is newer (default: False)
        force: Force overwrite regardless of file modification time (default: False)
        reboot: Reboot policy - 'forbid', 'allow' (default), or 'force'

    Returns:
        Detailed results including validation status, timestamp comparisons, files processed, and service restart status
    """
    # Validate reboot parameter
    reboot_lower = reboot.lower()
    if reboot_lower not in ["forbid", "allow", "force"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reboot parameter: '{reboot}'. Must be 'forbid', 'allow', or 'force'",
        )
    
    # Check if server mode is enabled - disable endpoint if so
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="Config upload endpoints are disabled in server mode",
        )

    # Read zip file content
    try:
        content = await file.read()
        zip_buffer = BytesIO(content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read file: {str(e)}",
        )

    # Verify it's a valid zip file
    try:
        with zipfile.ZipFile(zip_buffer, "r") as zip_file:
            # Test zip integrity
            zip_file.testzip()
            file_list = zip_file.namelist()
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400,
            detail="Invalid zip file",
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read zip file: {str(e)}",
        )

    # Extract file timestamps from zip
    zip_timestamps = extract_zip_file_timestamps(zip_buffer)


    # Identify recognized and unknown files
    recognized_files = {}
    unknown_files = []

    with zipfile.ZipFile(zip_buffer, "r") as zip_file:
        for filename in file_list:
            # Skip directories
            if filename.endswith("/"):
                continue

            # Get just the basename (ignore any directory structure in zip)
            basename = filename.split("/")[-1]

            if basename in RECOGNIZED_CONFIG_FILES:
                try:
                    file_content = zip_file.read(filename).decode("utf-8")
                    recognized_files[basename] = file_content
                except UnicodeDecodeError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File '{basename}' must be UTF-8 encoded text",
                    )
            else:
                # Skip hidden files and common non-config files
                if not basename.startswith(".") and basename.lower() not in ["readme.md", "readme.txt", "readme"]:
                    unknown_files.append(basename)

    # Handle pedantic mode
    if pedantic and unknown_files:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown files in zip archive: {', '.join(unknown_files)}. "
            f"Recognized files are: {', '.join(RECOGNIZED_CONFIG_FILES.keys())}",
        )

    # If no recognized files found
    if not recognized_files:
        raise HTTPException(
            status_code=400,
            detail=f"No recognized configuration files found in zip. "
            f"Supported files are: {', '.join(RECOGNIZED_CONFIG_FILES.keys())}",
        )

    # Parse and validate all recognized files
    validation_results = {}
    parsed_configs = {}
    config_instances = {}
    all_valid = True

    for filename, content in recognized_files.items():
        config_type = RECOGNIZED_CONFIG_FILES[filename]

        try:
            # Get config instance (with config upload flag for authorized_keys)
            config_instance = get_config_instance(config_type, is_config_upload=True)
            config_instances[filename] = config_instance

            # Parse the file
            parsed_config = parse_config_file(filename, content)

            # No special handling needed for authorized_keys - it writes to /boot/firmware/authorized_keys
            # which is then synced to authorized_keys2 by systemd. User keys remain separate.

            parsed_configs[filename] = parsed_config

            # Validate the configuration
            validation_errors = config_instance.validate(parsed_config)

            validation_results[filename] = {
                "valid": len(validation_errors) == 0,
                "errors": validation_errors,
            }

            if validation_errors:
                all_valid = False

        except ValueError as e:
            validation_results[filename] = {
                "valid": False,
                "errors": [f"Parse error: {str(e)}"],
            }
            all_valid = False
        except Exception as e:
            validation_results[filename] = {
                "valid": False,
                "errors": [f"Validation error: {str(e)}"],
            }
            all_valid = False

    # Prepare response
    response = {
        "success": all_valid,
        "files_processed": list(recognized_files.keys()),
        "files_ignored": unknown_files,
        "validation_results": validation_results,
        "services_restarted": [],
        "service_restart_errors": {},
    }

    # If not all files are valid, return validation errors without saving
    if not all_valid:
        response["message"] = "Validation failed for one or more files. No changes were made."
        return response

    # Perform timestamp comparisons for all valid files
    timestamp_comparisons = compare_file_timestamps(config_instances, zip_timestamps)
    
    # Handle pedantic mode timestamp checking (only if not in force mode)
    if pedantic and not force:
        # Check if any existing file is newer than its zip counterpart
        newer_files_on_disk = []
        for filename, comparison in timestamp_comparisons.items():
            if (comparison["exists_on_disk"] and 
                comparison["existing_timestamp"] and 
                not comparison["is_newer"]):
                newer_files_on_disk.append({
                    "filename": filename,
                    "existing_timestamp": comparison["existing_timestamp"],
                    "zip_timestamp": comparison["zip_timestamp"],
                })
        
        if newer_files_on_disk:
            response["success"] = False
            response["message"] = f"Pedantic mode: {len(newer_files_on_disk)} file(s) on disk are newer than zip versions. No changes were made."
            response["newer_files_on_disk"] = newer_files_on_disk
            response["timestamp_comparisons"] = timestamp_comparisons
            return response

    # Determine which files to save based on mode
    files_to_save = []
    files_skipped = []
    
    for filename in recognized_files.keys():
        comparison = timestamp_comparisons.get(filename, {})
        
        if force:
            # Force mode: save all files
            files_to_save.append(filename)
        else:
            # Default mode: only save files that are newer or don't exist
            if comparison.get("should_update", True):
                files_to_save.append(filename)
            else:
                files_skipped.append({
                    "filename": filename,
                    "reason": "File is not newer than existing version",
                    "existing_timestamp": comparison.get("existing_timestamp"),
                    "zip_timestamp": comparison.get("zip_timestamp"),
                })

    # Save the selected files
    saved_files = []
    save_errors = {}
    save_metadata = {}  # Track metadata from save operations (e.g., hostname changes)

    for filename in files_to_save:
        try:
            save_result = config_instances[filename].save(parsed_configs[filename])
            # Capture metadata if save() returns any (e.g., hostname changes for cmdline)
            if save_result and isinstance(save_result, dict):
                save_metadata[filename] = save_result
            
            # Set mtime from zip timestamp (regardless of mode)
            # This matches the behavior in /api/config/update where uploaded mtime is always preserved
            zip_timestamp = zip_timestamps.get(filename)
            if zip_timestamp:
                zip_timestamp_rounded = round_mtime_for_fat32(zip_timestamp)
                config_file_path = config_instances[filename].config_file
                # Convert naive UTC datetime to Unix timestamp using timegm (treats as UTC)
                timestamp = calendar.timegm(zip_timestamp_rounded.timetuple())
                os.utime(config_file_path, (timestamp, timestamp))
            
            saved_files.append(filename)
        except Exception as e:
            save_errors[filename] = str(e)

    if save_errors:
        response["success"] = False
        response["message"] = f"Failed to save some configuration files: {save_errors}"
        response["save_errors"] = save_errors
        return response

    # Files saved successfully
    if force:
        mode_desc = "force mode"
    elif pedantic:
        mode_desc = "pedantic mode"
    else:
        mode_desc = "default mode"
    
    message_parts = []
    if saved_files:
        message_parts.append(f"Successfully uploaded and validated {len(saved_files)} configuration file(s) in {mode_desc}")
    
    if files_skipped:
        message_parts.append(f"Skipped {len(files_skipped)} file(s) (not newer than existing)")
    
    response["message"] = ". ".join(message_parts) if message_parts else "No files were processed"
    
    # Add timestamp and mode information to response
    response.update({
        "mode": mode_desc,
        "force_used": force,
        "pedantic_used": pedantic,
        "timestamp_comparisons": timestamp_comparisons,
        "files_skipped": files_skipped,
        "saved_files": saved_files,
    })
    
    # Add save metadata if any (e.g., hostname changes for cmdline.txt)
    if save_metadata:
        response["save_metadata"] = save_metadata

    # Optionally restart services (only if not force-rebooting, as reboot will restart everything)
    if restart_services and reboot_lower != "force":
        # Service restart only works in tracker mode (not server mode)
        if config_loader.is_server_mode():
            response["message"] += " (Service restart is not available in server mode)"
        else:
            # Restart services for each saved config file
            for filename in saved_files:
                config_type = RECOGNIZED_CONFIG_FILES[filename]
                service_name = SERVICE_MAPPING.get(config_type)

                if service_name:
                    success, error = await restart_systemd_service(service_name)

                    if success:
                        response["services_restarted"].append(service_name)
                    else:
                        response["service_restart_errors"][service_name] = error

            # Update message based on restart results
            if response["services_restarted"]:
                response["message"] += f" and restarted service(s): {', '.join(set(response['services_restarted']))}"

            if response["service_restart_errors"]:
                errors_msg = ", ".join([f"{svc}: {err}" for svc, err in response["service_restart_errors"].items()])
                response["message"] += f" (Warning: Some services failed to restart: {errors_msg})"

    # Handle reboot based on policy
    # Check if cmdline.txt was updated (requires reboot to take effect)
    cmdline_updated = "cmdline.txt" in saved_files
    
    if reboot_lower == "forbid":
        # Never reboot, even if cmdline.txt was updated
        response["reboot_initiated"] = False
        response["reboot_policy"] = "forbid"
        if cmdline_updated:
            response["message"] += " (Note: cmdline.txt updated but reboot forbidden - changes will not take effect until manual reboot)"
    elif reboot_lower == "force":
        # Always reboot after successful upload
        response["reboot_policy"] = "force"
        response["reboot_delay_seconds"] = 10
        # Reboot only works in tracker mode (not server mode)
        if config_loader.is_server_mode():
            response["message"] += " (System reboot is not available in server mode)"
            response["reboot_initiated"] = False
        else:
            try:
                # Use systemd-run to schedule reboot in 10 seconds
                await run_subprocess_async(
                    ["systemd-run", "--on-active=10s", "systemctl", "reboot"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                response["message"] += ". System reboot scheduled in 10 seconds (forced)"
                response["reboot_initiated"] = True
            except Exception as e:
                response["message"] += f" (Warning: Failed to schedule reboot: {str(e)})"
                response["reboot_initiated"] = False
    elif reboot_lower == "allow" and cmdline_updated:
        # "allow" mode with cmdline.txt updated - automatically reboot
        response["reboot_policy"] = "allow"
        response["reboot_delay_seconds"] = 10
        # Reboot only works in tracker mode (not server mode)
        if config_loader.is_server_mode():
            response["message"] += " (System reboot is not available in server mode, but cmdline.txt was updated)"
            response["reboot_initiated"] = False
        else:
            try:
                # Use systemd-run to schedule reboot in 10 seconds
                subprocess.run(
                    ["systemd-run", "--on-active=10s", "systemctl", "reboot"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                response["message"] += ". System reboot scheduled in 10 seconds (cmdline.txt updated)"
                response["reboot_initiated"] = True
            except Exception as e:
                response["message"] += f" (Warning: Failed to schedule reboot: {str(e)})"
                response["reboot_initiated"] = False
    else:
        # "allow" mode but cmdline.txt was not updated - no reboot
        response["reboot_policy"] = "allow"
        response["reboot_initiated"] = False

    return response


def _validate_config_filename_and_get_instance(filename: str):
    """Validate filename and return config instance.
    
    Args:
        filename: Configuration filename
        
    Returns:
        Tuple of (config_instance, response_headers)
        
    Raises:
        HTTPException: If validation fails
    """
    # Check if server mode is enabled - disable endpoint if so
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="Config download endpoints are disabled in server mode",
        )

    # Validate filename
    filename_lower = filename.lower()
    if filename_lower not in RECOGNIZED_CONFIG_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported configuration file: {filename}. "
            f"Supported files are: {', '.join(RECOGNIZED_CONFIG_FILES.keys())}",
        )

    # Get config instance
    config_type = RECOGNIZED_CONFIG_FILES[filename_lower]
    try:
        config_instance = get_config_instance(config_type)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Check if file exists
    if not config_instance.config_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Configuration file '{filename}' not found",
        )

    # Prepare response headers with Last-Modified
    response_headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    try:
        file_stat = config_instance.config_file.stat()
        http_date = formatdate(file_stat.st_mtime, usegmt=True)
        response_headers["Last-Modified"] = http_date
    except Exception:
        pass

    return config_instance, response_headers


@router.get("/{filename}")
async def download_config(
    filename: str = PathParam(..., description="Configuration filename to download"),
):
    """Download a single configuration file.

    Supported files:
    - radiotracking.ini - Radio tracking configuration
    - schedule.yml - Schedule configuration
    - soundscapepipe.yml - Soundscapepipe configuration
    - authorized_keys - SSH authorized keys
    - cmdline.txt - Kernel boot parameters
    - wireguard.conf - WireGuard VPN configuration
    - server.crt - Mosquitto server certificate
    - server.conf - Mosquitto server configuration
    - geolocation - Geolocation file (geoclue format)

    This endpoint is disabled in server mode.

    Args:
        filename: The configuration filename to download

    Returns:
        The configuration file content with Last-Modified header
    """
    config_instance, response_headers = _validate_config_filename_and_get_instance(filename)

    # Read file content
    try:
        with open(config_instance.config_file, "r") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read configuration file: {str(e)}",
        )

    # Return file content
    return Response(
        content=content,
        media_type="text/plain",
        headers=response_headers,
    )


@router.head("/{filename}")
async def head_config(
    filename: str = PathParam(..., description="Configuration filename to check"),
):
    """Check if a configuration file exists and get metadata (HEAD request).

    Returns the same headers as GET but without the body content.
    Useful for checking file existence and Last-Modified timestamp efficiently.

    Supported files:
    - radiotracking.ini - Radio tracking configuration
    - schedule.yml - Schedule configuration
    - soundscapepipe.yml - Soundscapepipe configuration
    - authorized_keys - SSH authorized keys
    - cmdline.txt - Kernel boot parameters
    - wireguard.conf - WireGuard VPN configuration
    - server.crt - Mosquitto server certificate
    - server.conf - Mosquitto server configuration
    - geolocation - Geolocation file (geoclue format)

    This endpoint is disabled in server mode.

    Args:
        filename: The configuration filename to check

    Returns:
        Headers only (no body) with Last-Modified header
    """
    config_instance, response_headers = _validate_config_filename_and_get_instance(filename)
    
    # Get file size for Content-Length header
    try:
        file_stat = config_instance.config_file.stat()
        response_headers["Content-Length"] = str(file_stat.st_size)
    except Exception:
        pass

    return Response(
        content="",
        media_type="text/plain",
        headers=response_headers,
    )


def _check_existing_config_files():
    """Check which config files exist and get metadata.
    
    Returns:
        Tuple of (file_count, most_recent_mtime, total_size)
        
    Raises:
        HTTPException: If server mode is enabled or no files found
    """
    # Check if server mode is enabled - disable endpoint if so
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="Config download endpoints are disabled in server mode",
        )

    file_count = 0
    most_recent_mtime = None
    total_size = 0

    for filename, config_type in RECOGNIZED_CONFIG_FILES.items():
        try:
            config_instance = get_config_instance(config_type)
            
            if config_instance.config_file.exists():
                file_stat = config_instance.config_file.stat()
                file_count += 1
                total_size += file_stat.st_size
                
                # Track most recent mtime
                if most_recent_mtime is None or file_stat.st_mtime > most_recent_mtime:
                    most_recent_mtime = file_stat.st_mtime
        except Exception:
            # Skip files that can't be accessed
            continue

    # Check if any files were found
    if file_count == 0:
        raise HTTPException(
            status_code=404,
            detail="No configuration files found on the system",
        )

    return file_count, most_recent_mtime, total_size


@router.get(".zip")
async def download_config_zip():
    """Download a zip file containing all existing configuration files.

    Creates a zip archive with all configuration files that currently exist on the system.
    Each file's modification time in the zip is set to match the actual file's mtime.

    This endpoint is disabled in server mode.

    Returns:
        Zip file containing existing configuration files with Last-Modified header
    """
    # Check if server mode is enabled - disable endpoint if so
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="Config download endpoints are disabled in server mode",
        )

    # Collect all existing config files
    existing_files = {}
    most_recent_mtime = None

    for filename, config_type in RECOGNIZED_CONFIG_FILES.items():
        try:
            config_instance = get_config_instance(config_type)
            
            if config_instance.config_file.exists():
                # Read file content
                with open(config_instance.config_file, "r") as f:
                    content = f.read()
                
                # Get file mtime
                file_stat = config_instance.config_file.stat()
                mtime = datetime.utcfromtimestamp(file_stat.st_mtime)
                
                existing_files[filename] = {
                    "content": content,
                    "mtime": mtime,
                }
                
                # Track most recent mtime for Last-Modified header
                if most_recent_mtime is None or file_stat.st_mtime > most_recent_mtime:
                    most_recent_mtime = file_stat.st_mtime
        except Exception:
            # Skip files that can't be read
            continue

    # Check if any files were found
    if not existing_files:
        raise HTTPException(
            status_code=404,
            detail="No configuration files found on the system",
        )

    # Create zip file in memory
    zip_buffer = BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for filename, file_data in existing_files.items():
                # Create ZipInfo to set file timestamp
                zip_info = zipfile.ZipInfo(filename)
                # Convert datetime to zip timestamp format (year, month, day, hour, minute, second)
                mtime = file_data["mtime"]
                zip_info.date_time = (
                    mtime.year,
                    mtime.month,
                    mtime.day,
                    mtime.hour,
                    mtime.minute,
                    mtime.second,
                )
                # Write file to zip
                zip_file.writestr(zip_info, file_data["content"])
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create zip file: {str(e)}",
        )

    # Get hostname for filename
    hostname = socket.gethostname()
    zip_filename = f"{hostname}_tsconfig.zip"

    # Prepare response headers
    response_headers = {"Content-Disposition": f'attachment; filename="{zip_filename}"'}
    
    # Add Last-Modified header to most recent file's mtime
    if most_recent_mtime is not None:
        http_date = formatdate(most_recent_mtime, usegmt=True)
        response_headers["Last-Modified"] = http_date

    # Return zip file
    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers=response_headers,
    )


@router.head("/")
async def head_config_zip():
    """Check if config files exist and get metadata for zip archive (HEAD request).

    Returns headers for the zip archive without creating or returning the zip file.
    Useful for checking if configs exist and getting Last-Modified timestamp efficiently.

    This endpoint is disabled in server mode.

    Returns:
        Headers only (no body) with Last-Modified header for zip archive
    """
    file_count, most_recent_mtime, total_size = _check_existing_config_files()

    # Get hostname for filename
    hostname = socket.gethostname()
    zip_filename = f"{hostname}_tsconfig.zip"

    # Prepare response headers
    response_headers = {"Content-Disposition": f'attachment; filename="{zip_filename}"'}
    
    # Add Last-Modified header
    if most_recent_mtime is not None:
        http_date = formatdate(most_recent_mtime, usegmt=True)
        response_headers["Last-Modified"] = http_date
    
    # Add approximate Content-Length (actual zip will be smaller due to compression)
    # This is an estimate, real size would require creating the zip
    response_headers["X-File-Count"] = str(file_count)

    return Response(
        content="",
        media_type="application/zip",
        headers=response_headers,
    )
