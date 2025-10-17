"""Configuration file upload endpoints."""

import calendar
import os
import subprocess
import time
import zipfile
from configparser import ConfigParser
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config_loader import config_loader
from app.configs.authorized_keys import AuthorizedKeysConfig
from app.configs.cmdline import CmdlineConfig
from app.configs.geolocation import GeolocationConfig
from app.configs.mosquitto_cert import MosquittoCertConfig
from app.configs.mosquitto_conf import MosquittoConfConfig
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.schedule import ScheduleConfig
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.configs.wireguard import WireguardConfig

router = APIRouter(prefix="/api/upload", tags=["upload"])


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


def restart_systemd_service(service_name: str) -> tuple[bool, Optional[str]]:
    """Restart a systemd service.

    Args:
        service_name: Name of the service to restart

    Returns:
        Tuple of (success, error_message)
    """
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else f"Command failed with exit code {result.returncode}"
            return False, error_msg

        return True, None

    except subprocess.TimeoutExpired:
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


def parse_mtime_and_validate(mtime: Optional[str], force: bool) -> Optional[datetime]:
    """Parse and validate mtime parameter.
    
    Args:
        mtime: ISO timestamp string or None (interpreted as UTC if no timezone specified)
        force: Whether force flag is set
        
    Returns:
        Parsed datetime (naive, in UTC) or None
        
    Raises:
        HTTPException: If validation fails
    """
    if not force and mtime is None:
        raise HTTPException(
            status_code=400,
            detail="mtime parameter is required when force=False",
        )
    
    if mtime is not None:
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
            config_instance = AuthorizedKeysConfig()
            existing_config = config_instance.load()
            existing_keys = existing_config.get("keys", [])
            new_keys = []
            for idx, line in enumerate(content_str.strip().split("\n")):
                parsed = config_instance._parse_key_line(line, len(existing_keys) + idx)
                if parsed:
                    new_keys.append(parsed)
            parsed_config = {"keys": existing_keys + new_keys}
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


def handle_service_restart(config_type: str, restart_service: bool) -> Dict[str, Any]:
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
        "schedule": "wittypid", 
        "soundscapepipe": "soundscapepipe",
    }
    
    service_name = service_mapping.get(config_type)
    if not service_name:
        result["service_restart_error"] = f"No service mapping for config type: {config_type}"
        return result
    
    success, error = restart_systemd_service(service_name)
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


@router.post("/config")
async def upload_config(
    file: UploadFile = File(..., description="Configuration file to upload"),
    restart_service: bool = Form(False, description="Restart the respective service after upload"),
    mtime: Optional[str] = Form(None, description="Last modified timestamp in ISO format (e.g., 2025-10-17T08:59:50)"),
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
    
    When force=False:
    - mtime parameter is REQUIRED
    - File is only overwritten if uploaded mtime is newer than existing file
    - File mtime is set to the uploaded mtime (rounded for FAT32 compatibility)
    
    When force=True:
    - mtime parameter is optional (ignored if provided)
    - File is always overwritten regardless of timestamps
    - File mtime is set to current system time
    
    Optionally, the respective systemd service can be restarted after upload.

    This endpoint is disabled in server mode.

    Args:
        file: The configuration file to upload
        restart_service: Whether to restart the respective service after successful upload (default: False)
        mtime: Last modified timestamp in ISO format (e.g., 2025-10-17T08:59:50) - REQUIRED when force=False, ignored when force=True
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
    if not force and upload_mtime:
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
    try:
        config_instance.save(parsed_config)
        
        # Set file mtime if provided and not forced
        if upload_mtime and not force:
            upload_mtime_rounded = round_mtime_for_fat32(upload_mtime)
            # Convert naive UTC datetime to Unix timestamp using timegm (treats as UTC)
            timestamp = calendar.timegm(upload_mtime_rounded.timetuple())
            os.utime(config_instance.config_file, (timestamp, timestamp))
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {str(e)}")

    # Build success response
    mtime_set = upload_mtime is not None and not force
    message = f"Configuration file '{file.filename}' uploaded successfully"
    if force:
        message += " (forced - using current system time)"
    elif mtime_set:
        message += " (mtime preserved)"
    
    response_data = {
        "valid": True,
        "message": message,
        "config_path": str(config_instance.config_file),
        "mtime_set": mtime_set,
        "force_used": force,
    }
    
    # Add mtime info if provided
    if upload_mtime:
        upload_mtime_rounded = round_mtime_for_fat32(upload_mtime)
        response_data.update({
            "upload_mtime": upload_mtime.isoformat(),
            "upload_mtime_rounded": upload_mtime_rounded.isoformat(),
        })
    if existing_mtime:
        response_data["existing_mtime"] = existing_mtime.isoformat()
    
    # Handle service restart
    restart_result = handle_service_restart(config_type, restart_service)
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
    "schedule": "wittypid",
    "soundscapepipe": "soundscapepipe",
    "wireguard": "wg-quick@wireguard",
    "mosquitto_cert": "mosquitto",
    "mosquitto_conf": "mosquitto",
}


def get_config_instance(config_type: str):
    """Get appropriate config instance for the given type.

    Args:
        config_type: Type of configuration (radiotracking, schedule, soundscapepipe, authorized_keys,
                     cmdline, wireguard, mosquitto_cert, mosquitto_conf, geolocation)

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
        return AuthorizedKeysConfig()
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
        # Note: This returns new keys only, merging happens in the caller
        keys = []
        temp_config = AuthorizedKeysConfig()
        for idx, line in enumerate(content.strip().split("\n")):
            parsed = temp_config._parse_key_line(line, idx)
            if parsed:
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


@router.post("/config-zip")
async def upload_config_zip(
    file: UploadFile = File(..., description="Zip file containing configuration files"),
    restart_services: bool = Form(False, description="Restart affected services after upload"),
    pedantic: bool = Form(False, description="Reject upload if unknown files are present or if any existing file is newer"),
    force: bool = Form(False, description="Force overwrite regardless of file modification time"),
    reboot: bool = Form(False, description="Reboot the system after successfully applying the config-zip"),
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
    - Force mode (force=True): Overwrites all files regardless of timestamps, sets current time as mtime
    - Default mode (force=False, pedantic=False): Only overwrites files newer than existing, preserves zip timestamps
    - Pedantic mode (force=False, pedantic=True): Rejects upload if any existing file is newer OR unknown files present

    This endpoint is disabled in server mode.

    Args:
        file: Zip file containing configuration files
        restart_services: Whether to restart affected services after successful upload (default: False). Ignored if reboot=True.
        pedantic: Reject upload if unknown files are present or if any existing file is newer (default: False)
        force: Force overwrite regardless of file modification time (default: False)
        reboot: Whether to reboot the system after successfully applying the config-zip (default: False). When enabled, restart_services is ignored.

    Returns:
        Detailed results including validation status, timestamp comparisons, files processed, and service restart status
    """
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
            # Get config instance
            config_instance = get_config_instance(config_type)
            config_instances[filename] = config_instance

            # Parse the file
            parsed_config = parse_config_file(filename, content)

            # Special handling for authorized_keys: append to existing keys
            if filename == "authorized_keys":
                existing_config = config_instance.load()
                existing_keys = existing_config.get("keys", [])
                new_keys = parsed_config.get("keys", [])

                # Re-index new keys
                for idx, key in enumerate(new_keys):
                    key["index"] = len(existing_keys) + idx

                # Combine existing and new keys
                parsed_config = {"keys": existing_keys + new_keys}

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

    for filename in files_to_save:
        try:
            config_instances[filename].save(parsed_configs[filename])
            
            # Set appropriate mtime based on mode
            if force:
                # Force mode: use current time (default behavior of save())
                pass
            else:
                # Default/pedantic mode: preserve zip timestamp
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

    # Optionally restart services (only if not rebooting, as reboot will restart everything)
    if restart_services and not reboot:
        # Service restart only works in tracker mode (not server mode)
        if config_loader.is_server_mode():
            response["message"] += " (Service restart is not available in server mode)"
        else:
            # Restart services for each saved config file
            for filename in saved_files:
                config_type = RECOGNIZED_CONFIG_FILES[filename]
                service_name = SERVICE_MAPPING.get(config_type)

                if service_name:
                    success, error = restart_systemd_service(service_name)

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

    # Optionally reboot the system
    if reboot:
        # Reboot only works in tracker mode (not server mode)
        if config_loader.is_server_mode():
            response["message"] += " (System reboot is not available in server mode)"
            response["reboot_initiated"] = False
        else:
            try:
                # Use systemctl to reboot the system
                subprocess.run(["sudo", "systemctl", "reboot"], capture_output=True, text=True, timeout=10)
                response["message"] += ". System reboot initiated"
                response["reboot_initiated"] = True
            except subprocess.TimeoutExpired:
                # Timeout is expected as the system will be rebooting
                response["message"] += ". System reboot initiated"
                response["reboot_initiated"] = True
            except Exception as e:
                response["message"] += f" (Warning: Failed to initiate reboot: {str(e)})"
                response["reboot_initiated"] = False

    return response
