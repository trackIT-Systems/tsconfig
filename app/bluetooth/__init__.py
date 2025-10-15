"""Bluetooth GATT Gateway for tsOS Configuration Manager."""

from app.bluetooth.protocol import (
    BASE_UUID,
    SYSTEM_SERVICE_UUID,
    SYSTEMD_SERVICE_UUID,
    UPLOAD_SERVICE_UUID,
)

__all__ = [
    "BASE_UUID",
    "SYSTEM_SERVICE_UUID",
    "SYSTEMD_SERVICE_UUID",
    "UPLOAD_SERVICE_UUID",
]
