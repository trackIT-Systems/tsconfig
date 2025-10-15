"""GATT Service implementations for tsconfig BLE gateway.

This module provides GATT service classes that group related characteristics
according to the REST API structure.
"""

import logging
from typing import Any, Dict, List

import dbus
import dbus.service

from app.bluetooth.api_client import TsConfigApiClient
from app.bluetooth.gatt_characteristics import (
    AvailableServicesCharacteristic,
    ServerModeInfoCharacteristic,
    SystemdRebootCharacteristic,
    SystemdServiceActionCharacteristic,
    SystemdServiceLogsCharacteristic,
    SystemdServicesListCharacteristic,
    SystemStatusCharacteristic,
    TimedatectlStatusCharacteristic,
    UploadConfigCharacteristic,
    UploadZipCharacteristic,
)
from app.bluetooth.protocol import (
    SYSTEM_SERVICE_UUID,
    SYSTEMD_SERVICE_UUID,
    UPLOAD_SERVICE_UUID,
)

logger = logging.getLogger(__name__)

# BlueZ D-Bus constants
GATT_SERVICE_IFACE = "org.bluez.GattService1"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"


class Service(dbus.service.Object):
    """Base GATT Service class for BlueZ D-Bus."""

    def __init__(
        self,
        bus: dbus.Bus,
        index: int,
        uuid: str,
        primary: bool,
        app_path: str,
        api_client: TsConfigApiClient,
        require_pairing: bool = True,
    ):
        """Initialize a GATT Service.

        Args:
            bus: D-Bus system bus
            index: Service index
            uuid: Service UUID
            primary: Whether this is a primary service
            app_path: D-Bus path of the parent application
            api_client: HTTP API client instance
            require_pairing: Whether to require pairing for write operations
        """
        self.path = f"{app_path}/service{index:04d}"
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.api_client = api_client
        self.require_pairing = require_pairing
        self.characteristics: List = []

        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> Dict[str, Any]:
        """Get service properties for D-Bus."""
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": self.primary,
                "Characteristics": dbus.Array(self.get_characteristic_paths(), signature="o"),
            }
        }

    def get_path(self) -> str:
        """Get the D-Bus object path."""
        return dbus.ObjectPath(self.path)

    def get_characteristic_paths(self) -> List[str]:
        """Get paths of all characteristics in this service."""
        return [chrc.get_path() for chrc in self.characteristics]

    def add_characteristic(self, characteristic):
        """Add a characteristic to this service.

        Args:
            characteristic: Characteristic instance to add
        """
        self.characteristics.append(characteristic)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """D-Bus GetAll method."""
        if interface != GATT_SERVICE_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs", "Invalid interface")
        return self.get_properties()[GATT_SERVICE_IFACE]


class SystemService(Service):
    """System Service - provides system status and information."""

    def __init__(self, bus: dbus.Bus, index: int, app_path: str, api_client: TsConfigApiClient):
        """Initialize System Service.

        Args:
            bus: D-Bus system bus
            index: Service index
            app_path: Parent application D-Bus path
            api_client: HTTP API client
        """
        super().__init__(bus, index, SYSTEM_SERVICE_UUID, True, app_path, api_client, require_pairing=False)

        # Add characteristics (read-only, no pairing required)
        char_index = 0
        self.add_characteristic(SystemStatusCharacteristic(bus, char_index, self, api_client))
        char_index += 1
        self.add_characteristic(ServerModeInfoCharacteristic(bus, char_index, self, api_client))
        char_index += 1
        self.add_characteristic(TimedatectlStatusCharacteristic(bus, char_index, self, api_client))
        char_index += 1
        self.add_characteristic(AvailableServicesCharacteristic(bus, char_index, self, api_client))

        logger.info(f"System Service created with {len(self.characteristics)} characteristics")


class SystemdService(Service):
    """Systemd Service - provides systemd service management."""

    def __init__(
        self,
        bus: dbus.Bus,
        index: int,
        app_path: str,
        api_client: TsConfigApiClient,
        require_pairing: bool = True,
    ):
        """Initialize Systemd Service.

        Args:
            bus: D-Bus system bus
            index: Service index
            app_path: Parent application D-Bus path
            api_client: HTTP API client
            require_pairing: Whether to require pairing for write operations
        """
        super().__init__(bus, index, SYSTEMD_SERVICE_UUID, True, app_path, api_client, require_pairing)

        # Add characteristics
        char_index = 0

        # Read-only: Services list (no pairing required)
        self.add_characteristic(SystemdServicesListCharacteristic(bus, char_index, self, api_client))
        char_index += 1

        # Write operations (pairing required if enabled)
        self.add_characteristic(SystemdServiceActionCharacteristic(bus, char_index, self, api_client, require_pairing))
        char_index += 1
        self.add_characteristic(SystemdRebootCharacteristic(bus, char_index, self, api_client, require_pairing))
        char_index += 1
        self.add_characteristic(SystemdServiceLogsCharacteristic(bus, char_index, self, api_client, require_pairing))

        logger.info(f"Systemd Service created with {len(self.characteristics)} characteristics")


class UploadService(Service):
    """Upload Service - provides configuration file upload."""

    def __init__(
        self,
        bus: dbus.Bus,
        index: int,
        app_path: str,
        api_client: TsConfigApiClient,
        require_pairing: bool = True,
    ):
        """Initialize Upload Service.

        Args:
            bus: D-Bus system bus
            index: Service index
            app_path: Parent application D-Bus path
            api_client: HTTP API client
            require_pairing: Whether to require pairing for write operations
        """
        super().__init__(bus, index, UPLOAD_SERVICE_UUID, True, app_path, api_client, require_pairing)

        # Add characteristics (write operations, pairing required if enabled)
        char_index = 0
        self.add_characteristic(UploadConfigCharacteristic(bus, char_index, self, api_client, require_pairing))
        char_index += 1
        self.add_characteristic(UploadZipCharacteristic(bus, char_index, self, api_client, require_pairing))

        logger.info(f"Upload Service created with {len(self.characteristics)} characteristics")
