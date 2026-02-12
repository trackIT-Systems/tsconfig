"""System reset endpoints."""

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config_loader import config_loader
from app.routers.configs import _apply_config_zip_from_path
from app.utils.subprocess_async import run_subprocess_async

router = APIRouter(prefix="/api/system-reset", tags=["system-reset"])

DEFAULT_CONFIG_ZIP = Path("/home/pi/tsos-default-name_config.zip")
OVERLAY_PATH = Path("/media/root-rw")
WIREGUARD_CONF = Path("/boot/firmware/wireguard.conf")


class SystemResetRequest(BaseModel):
    """Request body for system reset."""

    reset_config: bool = Field(True, description="Reset original config from default zip")
    wipe_overlay: bool = Field(True, description="Wipe overlay filesystem at /media/root-rw")


class SystemResetResponse(BaseModel):
    """Response from system reset."""

    reset_config_done: bool
    wipe_overlay_done: bool
    reboot_needed: bool
    reboot_initiated: bool
    message: str = ""


@router.post("", response_model=SystemResetResponse)
async def system_reset(request: SystemResetRequest) -> SystemResetResponse:
    """Execute system reset steps: reset config and/or wipe overlay.

    Steps run in order: reset_config (if requested), then wipe_overlay (if requested).
    After all steps: reboots if needed (cmdline.txt updated or overlay wiped).
    Disabled in server mode.
    """
    if config_loader.is_server_mode():
        raise HTTPException(
            status_code=403,
            detail="System reset is not available in server mode",
        )

    reset_config_done = False
    wipe_overlay_done = False
    reboot_needed = False
    errors = []

    if request.reset_config:
        if not DEFAULT_CONFIG_ZIP.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Default config zip not found: {DEFAULT_CONFIG_ZIP}",
            )
        try:
            saved_files, cmdline_updated = _apply_config_zip_from_path(DEFAULT_CONFIG_ZIP, force=True)
            reset_config_done = True
            if cmdline_updated:
                reboot_needed = True
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if WIREGUARD_CONF.exists():
            try:
                WIREGUARD_CONF.unlink()
            except OSError as e:
                errors.append(f"Failed to remove wireguard.conf: {e}")

    if request.wipe_overlay:
        if not OVERLAY_PATH.exists() or not OVERLAY_PATH.is_dir():
            raise HTTPException(
                status_code=500,
                detail=f"Overlay path does not exist or is not a directory: {OVERLAY_PATH}",
            )
        try:
            for item in OVERLAY_PATH.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            wipe_overlay_done = True
            reboot_needed = True
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to wipe overlay: {e}",
            )

    reboot_initiated = False
    if reboot_needed:
        try:
            await run_subprocess_async(
                ["systemd-run", "--on-active=10s", "systemctl", "reboot"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            reboot_initiated = True
        except Exception as e:
            errors.append(f"Failed to schedule reboot: {e}")

    message_parts = []
    if reset_config_done:
        message_parts.append("Config reset")
    if wipe_overlay_done:
        message_parts.append("Overlay wiped")
    if reboot_initiated:
        message_parts.append("Reboot scheduled")
    if errors:
        message_parts.append("; ".join(errors))
    message = ". ".join(message_parts) if message_parts else "No steps executed"

    return SystemResetResponse(
        reset_config_done=reset_config_done,
        wipe_overlay_done=wipe_overlay_done,
        reboot_needed=reboot_needed,
        reboot_initiated=reboot_initiated,
        message=message,
    )
