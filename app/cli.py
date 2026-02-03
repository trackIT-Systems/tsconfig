"""Command-line interface for tsconfig configuration upload."""

import argparse
import calendar
import logging
import os
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from app.logging_config import setup_logging, get_logger
from app.routers.configs import (
    RECOGNIZED_CONFIG_FILES,
    SERVICE_MAPPING,
    extract_zip_file_timestamps,
    compare_file_timestamps,
    parse_config_file,
    get_config_instance,
    round_mtime_for_fat32,
)

# Logger will be initialized in main() after logging setup
logger = None


def restart_systemd_service_sync(service_name: str) -> Tuple[bool, Optional[str]]:
    """Restart a systemd service synchronously.

    Args:
        service_name: Name of the service to restart

    Returns:
        Tuple of (success, error_message)
    """
    try:
        result = subprocess.run(
            ["systemctl", "restart", service_name],
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


def schedule_reboot_sync() -> Tuple[bool, Optional[str]]:
    """Schedule a system reboot in 10 seconds synchronously.

    Returns:
        Tuple of (success, error_message)
    """
    try:
        result = subprocess.run(
            ["systemd-run", "--on-active=10s", "systemctl", "reboot"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else f"Command failed with exit code {result.returncode}"
            return False, error_msg

        return True, None

    except subprocess.TimeoutExpired:
        return False, "Reboot scheduling timed out"
    except Exception as e:
        return False, str(e)


def process_zip_upload(
    zip_path: Path,
    restart_services: bool = False,
    pedantic: bool = False,
    force: bool = False,
    reboot: str = "allow",
) -> Dict[str, Any]:
    """Process a zip file upload synchronously.

    This function replicates the logic from the upload_config_zip API endpoint
    but runs synchronously without requiring the API server.

    Args:
        zip_path: Path to the zip file
        restart_services: Whether to restart affected services after upload
        pedantic: Reject upload if unknown files present or existing files newer
        force: Force overwrite regardless of file modification time
        reboot: Reboot policy - 'forbid', 'allow' (default), or 'force'

    Returns:
        Dictionary with upload results

    Raises:
        SystemExit: On validation errors or other failures
    """
    # Get logger (will be initialized in main())
    log = get_logger(__name__)
    # Validate reboot parameter
    reboot_lower = reboot.lower()
    if reboot_lower not in ["forbid", "allow", "force"]:
        log.error(f"Invalid reboot parameter: '{reboot}'. Must be 'forbid', 'allow', or 'force'")
        sys.exit(1)

    # Read zip file content
    try:
        with open(zip_path, "rb") as f:
            content = f.read()
        zip_buffer = BytesIO(content)
    except FileNotFoundError:
        log.error(f"Zip file not found: {zip_path}")
        sys.exit(1)
    except Exception as e:
        log.error(f"Failed to read file: {str(e)}")
        sys.exit(1)

    # Verify it's a valid zip file
    try:
        with zipfile.ZipFile(zip_buffer, "r") as zip_file:
            # Test zip integrity
            zip_file.testzip()
            file_list = zip_file.namelist()
    except zipfile.BadZipFile:
        log.error("Invalid zip file")
        sys.exit(1)
    except Exception as e:
        log.error(f"Failed to read zip file: {str(e)}")
        sys.exit(1)

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
                    log.error(f"File '{basename}' must be UTF-8 encoded text")
                    sys.exit(1)
            else:
                # Skip hidden files and common non-config files
                if not basename.startswith(".") and basename.lower() not in ["readme.md", "readme.txt", "readme"]:
                    unknown_files.append(basename)

    # Handle pedantic mode
    if pedantic and unknown_files:
        log.error(
            f"Unknown files in zip archive: {', '.join(unknown_files)}. "
            f"Recognized files are: {', '.join(RECOGNIZED_CONFIG_FILES.keys())}"
        )
        sys.exit(1)

    # If no recognized files found
    if not recognized_files:
        log.error(
            f"No recognized configuration files found in zip. "
            f"Supported files are: {', '.join(RECOGNIZED_CONFIG_FILES.keys())}"
        )
        sys.exit(1)

    log.info(f"Found {len(recognized_files)} recognized configuration file(s)")
    if unknown_files:
        log.warning(f"Ignoring {len(unknown_files)} unknown file(s): {', '.join(unknown_files)}")

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
            parsed_configs[filename] = parsed_config

            # Validate the configuration
            validation_errors = config_instance.validate(parsed_config)

            validation_results[filename] = {
                "valid": len(validation_errors) == 0,
                "errors": validation_errors,
            }

            if validation_errors:
                all_valid = False
                log.error(f"Validation failed for {filename}: {validation_errors}")

        except ValueError as e:
            validation_results[filename] = {
                "valid": False,
                "errors": [f"Parse error: {str(e)}"],
            }
            all_valid = False
            log.error(f"Parse error for {filename}: {str(e)}")
        except Exception as e:
            validation_results[filename] = {
                "valid": False,
                "errors": [f"Validation error: {str(e)}"],
            }
            all_valid = False
            log.error(f"Validation error for {filename}: {str(e)}")

    # If not all files are valid, exit without saving
    if not all_valid:
        log.error("Validation failed for one or more files. No changes were made.")
        sys.exit(1)

    log.info("All configuration files validated successfully")

    # Perform timestamp comparisons for all valid files
    timestamp_comparisons = compare_file_timestamps(config_instances, zip_timestamps)

    # Handle pedantic mode timestamp checking (only if not in force mode)
    if pedantic and not force:
        # Check if any existing file is newer than its zip counterpart
        newer_files_on_disk = []
        for filename, comparison in timestamp_comparisons.items():
            if (
                comparison["exists_on_disk"]
                and comparison["existing_timestamp"]
                and not comparison["is_newer"]
            ):
                newer_files_on_disk.append({
                    "filename": filename,
                    "existing_timestamp": comparison["existing_timestamp"],
                    "zip_timestamp": comparison["zip_timestamp"],
                })

        if newer_files_on_disk:
            log.error(
                f"Pedantic mode: {len(newer_files_on_disk)} file(s) on disk are newer than zip versions. "
                "No changes were made."
            )
            for file_info in newer_files_on_disk:
                log.error(
                    f"  {file_info['filename']}: existing={file_info['existing_timestamp']}, "
                    f"zip={file_info['zip_timestamp']}"
                )
            sys.exit(1)

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

    if files_skipped:
        log.warning(f"Skipping {len(files_skipped)} file(s) (not newer than existing)")
        for skip_info in files_skipped:
            log.debug(
                f"  {skip_info['filename']}: existing={skip_info.get('existing_timestamp')}, "
                f"zip={skip_info.get('zip_timestamp')}"
            )

    # Save the selected files
    saved_files = []
    save_errors = {}
    save_metadata = {}  # Track metadata from save operations (e.g., hostname changes)

    for filename in files_to_save:
        try:
            log.debug(f"Saving {filename}...")
            save_result = config_instances[filename].save(parsed_configs[filename])
            # Capture metadata if save() returns any (e.g., hostname changes for cmdline)
            if save_result and isinstance(save_result, dict):
                save_metadata[filename] = save_result

            # Set mtime from zip timestamp (regardless of mode)
            zip_timestamp = zip_timestamps.get(filename)
            if zip_timestamp:
                zip_timestamp_rounded = round_mtime_for_fat32(zip_timestamp)
                config_file_path = config_instances[filename].config_file
                # Convert naive UTC datetime to Unix timestamp using timegm (treats as UTC)
                timestamp = calendar.timegm(zip_timestamp_rounded.timetuple())
                os.utime(config_file_path, (timestamp, timestamp))

            saved_files.append(filename)
            log.info(f"Saved {filename}")
        except Exception as e:
            save_errors[filename] = str(e)
            log.error(f"Failed to save {filename}: {str(e)}")

    if save_errors:
        log.error(f"Failed to save some configuration files: {save_errors}")
        sys.exit(1)

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

    log.info(". ".join(message_parts) if message_parts else "No files were processed")

    # Add save metadata if any (e.g., hostname changes for cmdline.txt)
    if save_metadata:
        for filename, metadata in save_metadata.items():
            log.info(f"Save metadata for {filename}: {metadata}")

    # Optionally restart services (only if not force-rebooting, as reboot will restart everything)
    services_restarted = []
    service_restart_errors = {}

    if restart_services and reboot_lower != "force":
        # Restart services for each saved config file
        for filename in saved_files:
            config_type = RECOGNIZED_CONFIG_FILES[filename]
            service_name = SERVICE_MAPPING.get(config_type)

            if service_name:
                log.debug(f"Restarting service {service_name}...")
                success, error = restart_systemd_service_sync(service_name)

                if success:
                    services_restarted.append(service_name)
                    log.info(f"Restarted service: {service_name}")
                else:
                    service_restart_errors[service_name] = error
                    log.warning(f"Failed to restart service {service_name}: {error}")

        # Log restart results
        if services_restarted:
            log.info(f"Restarted service(s): {', '.join(set(services_restarted))}")

        if service_restart_errors:
            errors_msg = ", ".join([f"{svc}: {err}" for svc, err in service_restart_errors.items()])
            log.warning(f"Some services failed to restart: {errors_msg}")

    # Handle reboot based on policy
    # Check if cmdline.txt was updated (requires reboot to take effect)
    cmdline_updated = "cmdline.txt" in saved_files

    if reboot_lower == "forbid":
        # Never reboot, even if cmdline.txt was updated
        if cmdline_updated:
            log.warning(
                "cmdline.txt updated but reboot forbidden - changes will not take effect until manual reboot"
            )
    elif reboot_lower == "force":
        # Always reboot after successful upload
        log.info("Scheduling system reboot in 10 seconds (forced)...")
        success, error = schedule_reboot_sync()
        if success:
            log.info("System reboot scheduled in 10 seconds (forced)")
        else:
            log.warning(f"Failed to schedule reboot: {error}")
    elif reboot_lower == "allow" and cmdline_updated:
        # "allow" mode with cmdline.txt updated - automatically reboot
        log.info("cmdline.txt updated - scheduling system reboot in 10 seconds...")
        success, error = schedule_reboot_sync()
        if success:
            log.info("System reboot scheduled in 10 seconds (cmdline.txt updated)")
        else:
            log.warning(f"Failed to schedule reboot: {error}")
    # else: "allow" mode but cmdline.txt was not updated - no reboot

    return {
        "success": True,
        "files_processed": list(recognized_files.keys()),
        "files_ignored": unknown_files,
        "saved_files": saved_files,
        "files_skipped": files_skipped,
        "services_restarted": services_restarted,
        "service_restart_errors": service_restart_errors,
    }


def main():
    """Main entry point for the CLI tool."""
    parser = argparse.ArgumentParser(
        description="tsconfig - Command-line tools for configuration management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global options
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Set logging to warning level",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Zip upload command
    zip_parser = subparsers.add_parser(
        "zip",
        help="Upload configuration files from a zip archive",
        description=(
            "Upload and validate multiple configuration files from a zip archive.\n\n"
            "Supported files:\n"
            "  - radiotracking.ini - Radio tracking configuration\n"
            "  - schedule.yml - Schedule configuration\n"
            "  - soundscapepipe.yml - Soundscapepipe configuration\n"
            "  - authorized_keys - SSH authorized keys\n"
            "  - cmdline.txt - Kernel boot parameters\n"
            "  - wireguard.conf - WireGuard VPN configuration\n"
            "  - server.crt - Mosquitto server certificate\n"
            "  - server.conf - Mosquitto server configuration\n"
            "  - geolocation - Geolocation file (geoclue format)\n\n"
            "Upload Modes:\n"
            "  - Default: Only overwrites files newer than existing\n"
            "  - --force: Overwrites all files regardless of timestamps\n"
            "  - --pedantic: Rejects upload if unknown files present or existing files newer\n\n"
            "Reboot Modes:\n"
            "  - allow (default): Automatically reboot if cmdline.txt is updated\n"
            "  - forbid: Never reboot\n"
            "  - force: Always reboot after successful upload"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    zip_parser.add_argument(
        "zip_path",
        type=Path,
        help="Path to the zip file containing configuration files",
    )

    zip_parser.add_argument(
        "-r",
        "--restart-services",
        action="store_true",
        help="Restart affected services after upload",
    )

    zip_parser.add_argument(
        "-p",
        "--pedantic",
        action="store_true",
        help="Reject upload if unknown files are present or if any existing file is newer",
    )

    zip_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force overwrite regardless of file modification time",
    )

    zip_parser.add_argument(
        "-b",
        "--reboot",
        choices=["forbid", "allow", "force"],
        default="allow",
        help="Reboot policy: 'forbid' (no reboot), 'allow' (default, reboot if requested), 'force' (always reboot)",
    )

    args = parser.parse_args()

    # Set up logging based on verbosity flags
    if args.verbose:
        setup_logging(log_level="DEBUG")
    elif args.quiet:
        setup_logging(log_level="WARNING")
    else:
        setup_logging(log_level="INFO")

    # Initialize module-level logger
    global logger
    logger = get_logger(__name__)

    # Handle commands
    if args.command == "zip":
        try:
            process_zip_upload(
                zip_path=args.zip_path,
                restart_services=args.restart_services,
                pedantic=args.pedantic,
                force=args.force,
                reboot=args.reboot,
            )
            logger.info("Upload completed successfully")
            sys.exit(0)
        except KeyboardInterrupt:
            logger.warning("Upload interrupted by user")
            sys.exit(130)
        except Exception as e:
            logger.exception(f"Unexpected error: {str(e)}")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
