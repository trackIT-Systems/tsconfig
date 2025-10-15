"""Configuration file upload endpoints."""

import subprocess
import zipfile
from configparser import ConfigParser
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config_loader import config_loader
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.schedule import ScheduleConfig
from app.configs.soundscapepipe import SoundscapepipeConfig

router = APIRouter(prefix="/api/upload", tags=["upload"])


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
    config_group: Optional[str] = Form(None, description="Config group name for server mode"),
    restart_service: bool = Form(False, description="Restart the respective service after upload"),
):
    """Upload and validate a configuration file.

    Supported files:
    - radiotracking.ini - Radio tracking configuration
    - schedule.yml - Schedule configuration
    - soundscapepipe.yml - Soundscapepipe configuration

    The file will be validated and if valid, will replace the existing configuration.
    Optionally, the respective systemd service can be restarted after upload.

    Args:
        file: The configuration file to upload
        config_group: Optional config group name for server mode
        restart_service: Whether to restart the respective service after successful upload (default: False)

    Returns:
        Success message if successful, or validation errors if invalid
    """
    # Read file content
    try:
        content = await file.read()
        content_str = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File must be UTF-8 encoded text",
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read file: {str(e)}",
        )

    # Determine config type from filename
    filename = file.filename.lower() if file.filename else ""

    config_instance = None
    config_type = None
    parsed_config = None

    # Get config directory for the group (if specified)
    config_dir = None
    if config_group:
        config_dir = config_loader.get_config_group_dir(config_group)
        if not config_dir:
            raise HTTPException(
                status_code=404,
                detail=f"Config group '{config_group}' not found",
            )

    try:
        if filename == "radiotracking.ini":
            config_type = "radiotracking"
            parsed_config = parse_ini_file(content_str)
            config_instance = RadioTrackingConfig(config_dir) if config_dir else RadioTrackingConfig()

        elif filename == "schedule.yml":
            config_type = "schedule"
            parsed_config = parse_yaml_file(content_str)
            config_instance = ScheduleConfig(config_dir) if config_dir else ScheduleConfig()

        elif filename == "soundscapepipe.yml":
            config_type = "soundscapepipe"
            parsed_config = parse_yaml_file(content_str)
            config_instance = SoundscapepipeConfig(config_dir) if config_dir else SoundscapepipeConfig()

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported configuration file: {file.filename}. "
                "Supported files are: radiotracking.ini, schedule.yml, soundscapepipe.yml",
            )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to parse configuration file: {str(e)}",
        )

    # Validate the configuration
    try:
        validation_errors = config_instance.validate(parsed_config)

        if validation_errors:
            return {
                "success": False,
                "valid": False,
                "config_type": config_type,
                "filename": file.filename,
                "errors": validation_errors,
                "message": f"Configuration file '{file.filename}' is invalid",
            }

        # Configuration is valid, save it
        try:
            config_instance.save(parsed_config)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save configuration: {str(e)}",
            )

        # Prepare response
        response = {
            "success": True,
            "valid": True,
            "config_type": config_type,
            "filename": file.filename,
            "message": f"Configuration file '{file.filename}' uploaded and validated successfully",
            "config_path": str(config_instance.config_file),
            "config_group": config_group,
            "service_restarted": False,
            "service_restart_error": None,
        }

        # Optionally restart the service
        if restart_service:
            # Service restart only works in tracker mode (not server mode)
            if config_loader.is_server_mode():
                response["service_restart_error"] = "Service restart is not available in server mode"
            else:
                # Map config type to service name (matching the UI mappings)
                service_mapping = {
                    "radiotracking": "radiotracking",
                    "schedule": "wittypid",
                    "soundscapepipe": "soundscapepipe",
                }
                service_name = service_mapping.get(config_type)

                if not service_name:
                    response["service_restart_error"] = f"Unknown config type: {config_type}"
                else:
                    success, error = restart_systemd_service(service_name)
                    response["service_restarted"] = success

                    if not success:
                        response["service_restart_error"] = error
                        response["message"] += f" (Warning: Service restart failed: {error})"
                    else:
                        response["message"] += f" and service '{service_name}' restarted"

        return response

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to validate configuration: {str(e)}",
        )


# Recognized configuration files
RECOGNIZED_CONFIG_FILES = {
    "radiotracking.ini": "radiotracking",
    "schedule.yml": "schedule",
    "soundscapepipe.yml": "soundscapepipe",
}

# Service mapping for restart
SERVICE_MAPPING = {
    "radiotracking": "radiotracking",
    "schedule": "wittypid",
    "soundscapepipe": "soundscapepipe",
}


def get_config_instance(config_type: str, config_dir: Optional[Path] = None):
    """Get appropriate config instance for the given type.

    Args:
        config_type: Type of configuration (radiotracking, schedule, soundscapepipe)
        config_dir: Optional config directory

    Returns:
        Config instance (RadioTrackingConfig, ScheduleConfig, or SoundscapepipeConfig)
    """
    if config_type == "radiotracking":
        return RadioTrackingConfig(config_dir) if config_dir else RadioTrackingConfig()
    elif config_type == "schedule":
        return ScheduleConfig(config_dir) if config_dir else ScheduleConfig()
    elif config_type == "soundscapepipe":
        return SoundscapepipeConfig(config_dir) if config_dir else SoundscapepipeConfig()
    else:
        raise ValueError(f"Unknown config type: {config_type}")


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
    else:
        raise ValueError(f"Unsupported file type: {filename}")


@router.post("/config-zip")
async def upload_config_zip(
    file: UploadFile = File(..., description="Zip file containing configuration files"),
    config_group: Optional[str] = Form(None, description="Config group name for server mode"),
    restart_services: bool = Form(False, description="Restart affected services after upload"),
    pedantic: bool = Form(False, description="Reject upload if unknown files are present"),
):
    """Upload and validate multiple configuration files from a zip archive.

    The zip file should contain configuration files at the root level:
    - radiotracking.ini - Radio tracking configuration
    - schedule.yml - Schedule configuration
    - soundscapepipe.yml - Soundscapepipe configuration

    All files will be validated before any changes are made. If any file fails validation,
    no files will be modified. If all files are valid, they will all be saved.

    In pedantic mode, the upload will be rejected if the zip contains any unrecognized files.

    Args:
        file: Zip file containing configuration files
        config_group: Optional config group name for server mode
        restart_services: Whether to restart affected services after successful upload (default: False)
        pedantic: Reject upload if unknown files are present (default: False)

    Returns:
        Detailed results including validation status for each file, files processed, ignored, and service restart status
    """
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

    # Get config directory for the group (if specified)
    config_dir = None
    if config_group:
        config_dir = config_loader.get_config_group_dir(config_group)
        if not config_dir:
            raise HTTPException(
                status_code=404,
                detail=f"Config group '{config_group}' not found",
            )

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
            # Parse the file
            parsed_config = parse_config_file(filename, content)
            parsed_configs[filename] = parsed_config

            # Get config instance
            config_instance = get_config_instance(config_type, config_dir)
            config_instances[filename] = config_instance

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
        "config_group": config_group,
    }

    # If not all files are valid, return validation errors without saving
    if not all_valid:
        response["message"] = "Validation failed for one or more files. No changes were made."
        return response

    # All files are valid - save them all
    saved_files = []
    save_errors = {}

    for filename in recognized_files.keys():
        try:
            config_instances[filename].save(parsed_configs[filename])
            saved_files.append(filename)
        except Exception as e:
            save_errors[filename] = str(e)

    if save_errors:
        response["success"] = False
        response["message"] = f"Failed to save some configuration files: {save_errors}"
        response["save_errors"] = save_errors
        return response

    # Files saved successfully
    response["message"] = f"Successfully uploaded and validated {len(saved_files)} configuration file(s)"

    # Optionally restart services
    if restart_services:
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
                response["message"] += f" and restarted service(s): {', '.join(response['services_restarted'])}"

            if response["service_restart_errors"]:
                errors_msg = ", ".join([f"{svc}: {err}" for svc, err in response["service_restart_errors"].items()])
                response["message"] += f" (Warning: Some services failed to restart: {errors_msg})"

    return response
