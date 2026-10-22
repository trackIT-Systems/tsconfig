"""Command-line interface for tsconfig configuration upload and system reset."""

import argparse
import asyncio
import calendar
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from app.config_loader import config_loader
from app.configs import support
from app.identity import OPERATOR_HOME
from app.logging_config import get_logger, setup_logging
from app.routers.configs import (
    RECOGNIZED_CONFIG_FILES,
    SERVICE_MAPPING,
    _apply_config_zip_from_path,
    collect_privileged_stats,
    compare_file_timestamps,
    extract_zip_file_timestamps,
    get_config_instance,
    parse_config_file,
    truncate_mtime_for_fat32,
)
from app.utils import privileged as priv

DEFAULT_CONFIG_ZIP = OPERATOR_HOME / "tsos-default-name_config.zip"

logger = get_logger(__name__)


# restart_systemd_service_sync / schedule_reboot_sync used to shell out to
# systemctl/systemd-run directly - removed in favor of
# app.utils.privileged.systemctl_action/schedule_reboot (aliased `priv`),
# which run as pi directly (systemctl_action, authorized by its own polkit
# rule) or via pkcheck (schedule_reboot), rather than as root, now that this
# CLI, like the web/BLE service, runs as pi rather than root. This is also
# why process_system_reset and process_zip_upload below are now async: the
# privileged client is asyncio-based (shared with the FastAPI service), and
# main() runs them via asyncio.run() rather than duplicating a sync
# HTTP-free codepath.


async def process_system_reset(
    keep_config: bool = False,
    keep_overlay: bool = False,
) -> None:
    """Execute system reset: reset config and/or wipe overlay.

    Args:
        keep_config: If True, skip reset config step
        keep_overlay: If True, skip wipe overlay step

    Raises:
        SystemExit: On validation errors or other failures
    """
    if config_loader.is_server_mode():
        logger.error("System reset is not available in server mode")
        sys.exit(1)

    if keep_config and keep_overlay:
        logger.error("No steps to execute: both --keep-config and --keep-overlay specified")
        sys.exit(1)

    reset_config = not keep_config
    should_wipe_overlay = not keep_overlay
    reboot_needed = False

    if reset_config:
        if not DEFAULT_CONFIG_ZIP.exists():
            logger.error("Default config zip not found: %s", DEFAULT_CONFIG_ZIP)
            sys.exit(1)
        try:
            saved_files, cmdline_updated = await _apply_config_zip_from_path(DEFAULT_CONFIG_ZIP, force=True)
            logger.info("Reset config: %s", ", ".join(saved_files))
            if cmdline_updated:
                reboot_needed = True
        except ValueError as e:
            logger.error("Cannot apply the default configuration: %s", e)
            sys.exit(1)

        try:
            await priv.delete_config("wireguard")
            logger.info("Removed wireguard.conf")
        except priv.PrivilegedCallError as e:
            logger.warning("Failed to remove wireguard.conf: %s", e)

    if should_wipe_overlay:
        try:
            await priv.wipe_overlay()
            logger.info("Wiped overlay filesystem")
            reboot_needed = True
        except priv.PrivilegedCallError as e:
            logger.error("Failed to wipe overlay: %s", e)
            sys.exit(1)

    if reboot_needed:
        logger.debug("Scheduling a system reboot")
        try:
            await priv.schedule_reboot(delay=10)
            logger.info("System reboot scheduled in 10 seconds")
        except priv.PrivilegedCallError as e:
            logger.warning("Failed to schedule reboot: %s", e)


async def process_zip_upload(
    zip_path: Path,
    restart_services: bool = False,
    pedantic: bool = False,
    force: bool = False,
    reboot: str = "allow",
) -> dict[str, Any]:
    """Process a zip file upload.

    This function replicates the logic from the upload_config_zip API endpoint
    for use outside the API server (an admin/operator running `tsconfig zip`
    from a shell). Saves go through the same privileged path the API uses,
    so a shell operator gets exactly the same capabilities as the web UI -
    not more, not less.

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
    # Validate reboot parameter
    reboot_lower = reboot.lower()
    if reboot_lower not in ["forbid", "allow", "force"]:
        logger.error("Invalid reboot parameter '%s', must be 'forbid', 'allow' or 'force'", reboot)
        sys.exit(1)

    # Read zip file content
    try:
        content = await asyncio.to_thread(Path(zip_path).read_bytes)
        zip_buffer = BytesIO(content)
    except FileNotFoundError:
        logger.error("Zip file not found: %s", zip_path)
        sys.exit(1)
    except OSError as e:
        logger.error("Failed to read file: %s", e)
        sys.exit(1)

    # Verify it's a valid zip file
    try:
        with zipfile.ZipFile(zip_buffer, "r") as zip_file:
            # Test zip integrity
            zip_file.testzip()
            file_list = zip_file.namelist()
    except zipfile.BadZipFile:
        logger.error("Invalid zip file")
        sys.exit(1)
    except Exception:
        # testzip()/namelist() can raise far more than BadZipFile (encrypted
        # members, unsupported compression, zlib errors, ...); any of them
        # means the upload is unusable.
        logger.exception("Failed to read zip file")
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
                    logger.error("File '%s' must be UTF-8 encoded text", basename)
                    sys.exit(1)
            else:
                # Skip hidden files and common non-config files
                if not basename.startswith(".") and basename.lower() not in ["readme.md", "readme.txt", "readme"]:
                    unknown_files.append(basename)

    # Handle pedantic mode
    if pedantic and unknown_files:
        logger.error(
            "Unknown files in zip archive: %s (recognized files are: %s)",
            ", ".join(unknown_files),
            ", ".join(RECOGNIZED_CONFIG_FILES.keys()),
        )
        sys.exit(1)

    # If no recognized files found
    if not recognized_files:
        logger.error(
            "No recognized configuration files found in zip (supported files are: %s)",
            ", ".join(RECOGNIZED_CONFIG_FILES.keys()),
        )
        sys.exit(1)

    # A bundle for another kind of device is rejected as a whole, not partly applied
    files_by_type = {RECOGNIZED_CONFIG_FILES[filename]: filename for filename in recognized_files}
    unsupported = await support.unsupported_config_types(files_by_type)
    if unsupported:
        logger.error("%s, no changes were made", support.unsupported_message(unsupported, files_by_type))
        sys.exit(1)

    logger.info("Found %s recognized configuration file(s)", len(recognized_files))
    if unknown_files:
        logger.warning("Ignoring %s unknown file(s): %s", len(unknown_files), ", ".join(unknown_files))

    # Parse and validate all recognized files
    validation_results = {}
    parsed_configs = {}
    config_instances = {}
    all_valid = True

    for filename, content in recognized_files.items():
        config_type = RECOGNIZED_CONFIG_FILES[filename]

        try:
            # Get config instance (with config upload flag for authorized_keys)
            config_instance = get_config_instance(config_type)
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
                logger.error("Validation failed for %s: %s", filename, validation_errors)

        except ValueError as e:
            validation_results[filename] = {
                "valid": False,
                "errors": [f"Parse error: {e!s}"],
            }
            all_valid = False
            logger.error("Parse error for %s: %s", filename, e)
        except Exception as e:
            validation_results[filename] = {
                "valid": False,
                "errors": [f"Validation error: {e!s}"],
            }
            all_valid = False
            logger.exception("Validation error for %s", filename)

    # If not all files are valid, exit without saving
    if not all_valid:
        logger.error("Validation failed for one or more files, no changes were made")
        sys.exit(1)

    logger.info("All configuration files validated successfully")

    # Perform timestamp comparisons for all valid files
    timestamp_comparisons = compare_file_timestamps(
        config_instances, zip_timestamps, await collect_privileged_stats(config_instances)
    )

    # Handle pedantic mode timestamp checking (only if not in force mode)
    if pedantic and not force:
        # Check if any existing file is newer than its zip counterpart
        newer_files_on_disk = []
        for filename, comparison in timestamp_comparisons.items():
            if comparison["exists_on_disk"] and comparison["existing_timestamp"] and not comparison["is_newer"]:
                newer_files_on_disk.append(
                    {
                        "filename": filename,
                        "existing_timestamp": comparison["existing_timestamp"],
                        "zip_timestamp": comparison["zip_timestamp"],
                    }
                )

        if newer_files_on_disk:
            logger.error(
                "Pedantic mode: %s file(s) on disk are newer than in the zip, no changes were made",
                len(newer_files_on_disk),
            )
            for file_info in newer_files_on_disk:
                logger.error(
                    "%s is newer on disk: existing=%s, zip=%s",
                    file_info["filename"],
                    file_info["existing_timestamp"],
                    file_info["zip_timestamp"],
                )
            sys.exit(1)

    # Determine which files to save based on mode
    files_to_save = []
    files_skipped = []

    for filename in recognized_files:
        comparison = timestamp_comparisons.get(filename, {})

        if force:
            # Force mode: save all files
            files_to_save.append(filename)
        else:
            # Default mode: only save files that are newer or don't exist
            if comparison.get("should_update", True):
                files_to_save.append(filename)
            else:
                files_skipped.append(
                    {
                        "filename": filename,
                        "reason": "File is not newer than existing version",
                        "existing_timestamp": comparison.get("existing_timestamp"),
                        "zip_timestamp": comparison.get("zip_timestamp"),
                    }
                )

    if files_skipped:
        logger.warning("Skipping %s file(s) (not newer than existing)", len(files_skipped))
        for skip_info in files_skipped:
            logger.debug(
                "Skipping %s: existing=%s, zip=%s",
                skip_info["filename"],
                skip_info.get("existing_timestamp"),
                skip_info.get("zip_timestamp"),
            )

    # Save the selected files
    saved_files = []
    save_errors = {}
    save_metadata = {}  # Track metadata from save operations (e.g., hostname changes)

    for filename in files_to_save:
        try:
            logger.debug("Saving %s", filename)
            config_type = RECOGNIZED_CONFIG_FILES[filename]

            # Set mtime from zip timestamp (regardless of mode)
            zip_timestamp = zip_timestamps.get(filename)
            mtime_epoch = None
            if zip_timestamp:
                zip_timestamp_truncated = truncate_mtime_for_fat32(zip_timestamp)
                mtime_epoch = calendar.timegm(zip_timestamp_truncated.timetuple())

            priv_result = await priv.write_config(config_type, parsed_configs[filename], mtime=mtime_epoch)
            save_result = priv_result.get("save_result")
            # Capture metadata if save() returns any (e.g., hostname changes for cmdline)
            if save_result and isinstance(save_result, dict):
                save_metadata[filename] = save_result

            saved_files.append(filename)
            logger.info("Saved %s", filename)
        except Exception as e:
            save_errors[filename] = str(e)
            logger.exception("Failed to save %s", filename)

    if save_errors:
        logger.error("Failed to save some configuration files: %s", save_errors)
        sys.exit(1)

    # Files saved successfully
    if force:
        mode_desc = "force mode"
    elif pedantic:
        mode_desc = "pedantic mode"
    else:
        mode_desc = "default mode"

    if saved_files:
        logger.info("Uploaded and validated %s configuration file(s) in %s", len(saved_files), mode_desc)
    if files_skipped:
        logger.info("Skipped %s file(s), not newer than the existing ones", len(files_skipped))
    if not saved_files and not files_skipped:
        logger.info("No files were processed")

    # Add save metadata if any (e.g., hostname changes for cmdline.txt)
    if save_metadata:
        for filename, metadata in save_metadata.items():
            logger.info("Save metadata for %s: %s", filename, metadata)

    # Optionally restart services (only if not force-rebooting, as reboot will restart everything)
    services_restarted = []
    service_restart_errors = {}

    if restart_services and reboot_lower != "force":
        # Restart services for each saved config file
        for filename in saved_files:
            config_type = RECOGNIZED_CONFIG_FILES[filename]
            service_name = SERVICE_MAPPING.get(config_type)

            if service_name:
                logger.debug("Restarting %s", service_name)
                success, error = await priv.systemctl_action(service_name, "restart")

                if success:
                    services_restarted.append(service_name)
                    logger.debug("Restarted %s", service_name)
                else:
                    service_restart_errors[service_name] = error
                    logger.warning("Failed to restart service %s: %s", service_name, error)

        # Log restart results
        if services_restarted:
            logger.info("Restarted service(s): %s", ", ".join(set(services_restarted)))

        if service_restart_errors:
            errors_msg = ", ".join([f"{svc}: {err}" for svc, err in service_restart_errors.items()])
            logger.warning("Some services failed to restart: %s", errors_msg)

    # Handle reboot based on policy
    # Check if cmdline.txt was updated (requires reboot to take effect)
    cmdline_updated = "cmdline.txt" in saved_files

    if reboot_lower == "forbid":
        # Never reboot, even if cmdline.txt was updated
        if cmdline_updated:
            logger.warning("cmdline.txt updated but reboot forbidden: the changes take effect at the next reboot")
    elif reboot_lower == "force":
        # Always reboot after successful upload
        logger.debug("Scheduling a system reboot in 10 seconds (forced)")
        try:
            await priv.schedule_reboot(delay=10)
            logger.info("System reboot scheduled in 10 seconds (forced)")
        except priv.PrivilegedCallError as e:
            logger.warning("Failed to schedule reboot: %s", e)
    elif reboot_lower == "allow" and cmdline_updated:
        # "allow" mode with cmdline.txt updated - automatically reboot
        logger.debug("cmdline.txt updated, scheduling a system reboot in 10 seconds")
        try:
            await priv.schedule_reboot(delay=10)
            logger.info("System reboot scheduled in 10 seconds (cmdline.txt updated)")
        except priv.PrivilegedCallError as e:
            logger.warning("Failed to schedule reboot: %s", e)
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
            "  - geolocation - Geolocation file (geoclue format)\n"
            "  - tsupdate.yml - Automatic update daemon (tsupdated) configuration\n\n"
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

    # System reset command
    reset_parser = subparsers.add_parser(
        "system-reset",
        help="Reset original config and/or wipe overlay filesystem",
        description=(
            "Execute system reset steps. By default performs full reset (reset config + wipe overlay).\n\n"
            "Options:\n"
            "  --keep-overlay   Only reset config, do not wipe overlay\n"
            "  --keep-config    Only wipe overlay, do not reset config\n"
            "  Both flags       Error (no steps to execute)\n\n"
            "Reboot is scheduled if cmdline.txt was updated or overlay was wiped."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset_parser.add_argument(
        "--keep-overlay",
        action="store_true",
        help="Skip wipe overlay step (only reset config)",
    )
    reset_parser.add_argument(
        "--keep-config",
        action="store_true",
        help="Skip reset config step (only wipe overlay)",
    )

    args = parser.parse_args()

    # Set up logging based on verbosity flags
    if args.verbose:
        setup_logging(log_level="DEBUG")
    elif args.quiet:
        setup_logging(log_level="WARNING")
    else:
        setup_logging(log_level="INFO")

    # Handle commands
    if args.command == "zip":
        try:
            asyncio.run(
                process_zip_upload(
                    zip_path=args.zip_path,
                    restart_services=args.restart_services,
                    pedantic=args.pedantic,
                    force=args.force,
                    reboot=args.reboot,
                )
            )
            logger.info("Upload completed")
            sys.exit(0)
        except KeyboardInterrupt:
            logger.warning("Upload interrupted by user")
            sys.exit(130)
        except Exception:
            logger.exception("Upload failed")
            sys.exit(1)
    elif args.command == "system-reset":
        try:
            asyncio.run(
                process_system_reset(
                    keep_config=args.keep_config,
                    keep_overlay=args.keep_overlay,
                )
            )
            sys.exit(0)
        except KeyboardInterrupt:
            logger.warning("Reset interrupted by user")
            sys.exit(130)
        except Exception:
            logger.exception("System reset failed")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
