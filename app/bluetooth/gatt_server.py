"""Core GATT server implementation using BlueZ D-Bus API.

This module provides the main BLE GATT server that manages the D-Bus application,
services, and BLE advertising.
"""

import logging
import socket
from typing import Any, Dict, List, Optional

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from app.bluetooth.api_client import TsConfigApiClient
from app.bluetooth.services import SystemdService, SystemService, UploadService
from app.logging_config import get_logger

logger = get_logger(__name__)

# BlueZ D-Bus constants
BLUEZ_SERVICE_NAME = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
ADAPTER_IFACE = "org.bluez.Adapter1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"


class InvalidArgsException(dbus.exceptions.DBusException):
    """D-Bus exception for invalid arguments."""

    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupportedException(dbus.exceptions.DBusException):
    """D-Bus exception for unsupported operations."""

    _dbus_error_name = "org.bluez.Error.NotSupported"


class NotPermittedException(dbus.exceptions.DBusException):
    """D-Bus exception for permission denied."""

    _dbus_error_name = "org.bluez.Error.NotPermitted"


class Application(dbus.service.Object):
    """GATT Application for BlueZ D-Bus."""

    def __init__(self, bus: dbus.Bus, path: str, api_client: TsConfigApiClient, require_pairing: bool = True):
        """Initialize the GATT application.

        Args:
            bus: D-Bus system bus
            path: D-Bus object path
            api_client: HTTP API client instance
            require_pairing: Whether to require pairing for write operations
        """
        self.path = path
        self.services: List = []
        self.api_client = api_client
        self.require_pairing = require_pairing

        dbus.service.Object.__init__(self, bus, self.path)

        # Create services
        self.add_service(SystemService(bus, 0, self.path, api_client))
        self.add_service(SystemdService(bus, 1, self.path, api_client, require_pairing))
        self.add_service(UploadService(bus, 2, self.path, api_client, require_pairing))

        logger.debug(f"GATT Application created with {len(self.services)} services")

    def get_path(self) -> str:
        """Get the D-Bus object path."""
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        """Add a service to the application.

        Args:
            service: Service instance to add
        """
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        """D-Bus GetManagedObjects method - return all services and characteristics."""
        response = {}

        for service in self.services:
            response[service.get_path()] = service.get_properties()

            for chrc in service.characteristics:
                response[chrc.get_path()] = chrc.get_properties()

        return response


class Advertisement(dbus.service.Object):
    """BLE Advertisement for BlueZ D-Bus."""

    PATH_BASE = "/org/bluez/tsconfig/advertisement"

    def __init__(self, bus: dbus.Bus, index: int, advertising_type: str, device_name: Optional[str] = None):
        """Initialize the BLE advertisement.

        Args:
            bus: D-Bus system bus
            index: Advertisement index
            advertising_type: Type of advertisement (e.g., "peripheral")
            device_name: Optional device name to advertise
        """
        self.path = f"{self.PATH_BASE}{index}"
        self.bus = bus
        self.ad_type = advertising_type
        # Truncate device name to fit within BLE advertisement 31-byte limit
        # Format: 1 byte (length) + 1 byte (type) + name + UUID (16 bytes) + flags (~3 bytes) = ~21 bytes for name
        hostname = device_name or socket.gethostname()
        self.local_name = hostname[:20] if len(hostname) > 20 else hostname
        self.service_uuids: List[str] = []
        # Disable TX power to save advertisement space
        self.include_tx_power = False

        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self) -> str:
        """Get the D-Bus object path."""
        return dbus.ObjectPath(self.path)

    def add_service_uuid(self, uuid: str):
        """Add a service UUID to advertise.

        Args:
            uuid: Service UUID to advertise
        """
        if uuid not in self.service_uuids:
            self.service_uuids.append(uuid)

    def get_properties(self) -> Dict[str, Any]:
        """Get advertisement properties for D-Bus."""
        properties = {
            "Type": self.ad_type,
            "ServiceUUIDs": dbus.Array(self.service_uuids, signature="s"),
            "LocalName": dbus.String(self.local_name),
            "IncludeTxPower": dbus.Boolean(self.include_tx_power),
        }
        return {LE_ADVERTISEMENT_IFACE: properties}

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """D-Bus GetAll method."""
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException("Invalid interface")
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        """Release the advertisement."""
        logger.info(f"Advertisement {self.path} released")


class BleGattServer:
    """Main BLE GATT server managing D-Bus and BlueZ interactions."""

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        device_name: Optional[str] = None,
        require_pairing: bool = True,
        discoverable: bool = True,
    ):
        """Initialize the BLE GATT server.

        Args:
            api_url: Base URL of the tsconfig HTTP API
            device_name: BLE device name (defaults to system hostname)
            require_pairing: Whether to require pairing for write operations
            discoverable: Whether the device should be discoverable
        """
        self.api_url = api_url
        self.device_name = device_name or socket.gethostname()
        self.require_pairing = require_pairing
        self.discoverable = discoverable

        self.bus: Optional[dbus.Bus] = None
        self.adapter: Optional[dbus.Interface] = None
        self.adapter_props: Optional[dbus.Interface] = None
        self.ad_manager: Optional[dbus.Interface] = None
        self.gatt_manager: Optional[dbus.Interface] = None
        self.application: Optional[Application] = None
        self.advertisement: Optional[Advertisement] = None
        self.api_client: Optional[TsConfigApiClient] = None
        self.mainloop: Optional[GLib.MainLoop] = None

        logger.info(f"BLE GATT Server initialized for device: {self.device_name}")

    def find_adapter(self) -> str:
        """Find the first available Bluetooth adapter.

        Returns:
            D-Bus path of the adapter

        Raises:
            Exception: If no adapter is found
        """
        remote_om = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
        objects = remote_om.GetManagedObjects()

        for path, interfaces in objects.items():
            if ADAPTER_IFACE in interfaces:
                logger.debug(f"Found Bluetooth adapter: {path}")
                return path

        raise Exception("No Bluetooth adapter found")

    def setup(self):
        """Set up the BLE GATT server."""
        logger.debug("Setting up BLE GATT server...")

        # Initialize D-Bus
        logger.debug("Initializing D-Bus connection...")
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()

        # Find adapter
        logger.debug("Finding Bluetooth adapter...")
        adapter_path = self.find_adapter()
        adapter_obj = self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path)
        self.adapter = dbus.Interface(adapter_obj, ADAPTER_IFACE)
        self.adapter_props = dbus.Interface(adapter_obj, DBUS_PROP_IFACE)

        # Check current adapter state
        try:
            powered = self.adapter_props.Get(ADAPTER_IFACE, "Powered")
            address = self.adapter_props.Get(ADAPTER_IFACE, "Address")
            logger.debug(f"Adapter {address} - Powered: {powered}")
        except Exception as e:
            logger.warning(f"Could not read adapter properties: {e}")

        # Set adapter properties
        logger.debug("Configuring adapter properties...")
        self.adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(self.discoverable))

        # Get managers
        logger.debug("Getting LE Advertising and GATT managers...")
        self.ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)
        self.gatt_manager = dbus.Interface(adapter_obj, GATT_MANAGER_IFACE)

        # Create API client
        logger.debug("Creating API client...")
        self.api_client = TsConfigApiClient(base_url=self.api_url)

        # Create GATT application
        logger.debug("Creating GATT application...")
        app_path = "/org/bluez/tsconfig/app"
        self.application = Application(self.bus, app_path, self.api_client, self.require_pairing)

        # Create advertisement
        logger.debug("Creating BLE advertisement...")
        self.advertisement = Advertisement(self.bus, 0, "peripheral", self.device_name)
        # Only advertise primary service UUID to stay within 31-byte BLE advertisement limit
        # Other services will be discovered after connection via GATT service discovery
        from app.bluetooth.protocol import SYSTEM_SERVICE_UUID

        self.advertisement.add_service_uuid(SYSTEM_SERVICE_UUID)
        logger.debug(f"Advertising with primary service UUID: {SYSTEM_SERVICE_UUID}")

        logger.info("BLE GATT Server setup complete")

    def register_application(self):
        """Register the GATT application with BlueZ."""
        logger.info("Registering GATT application...")
        self.gatt_manager.RegisterApplication(
            self.application.get_path(),
            {},
            reply_handler=lambda: logger.info("GATT application registered successfully"),
            error_handler=lambda e: logger.error(f"Failed to register GATT application: {e}"),
        )

    def register_advertisement(self):
        """Register the BLE advertisement with BlueZ."""
        logger.debug("Registering BLE advertisement...")

        def success_handler():
            logger.info(f"BLE advertisement registered successfully as '{self.device_name}'")
            logger.info("Device is now discoverable via Bluetooth")

        def error_handler(error):
            logger.error("=" * 70)
            logger.error(f"FAILED to register BLE advertisement: {error}")
            logger.error("=" * 70)
            logger.error("")
            logger.error("Troubleshooting steps:")
            logger.error("  1. Run the reset script: sudo ./reset_ble.sh")
            logger.error("  2. Or manually reset:")
            logger.error("     sudo systemctl stop tsconfig-ble")
            logger.error("     sudo hciconfig hci0 down && sleep 2 && sudo hciconfig hci0 up")
            logger.error("     sudo systemctl restart bluetooth && sleep 3")
            logger.error("     sudo systemctl start tsconfig-ble")
            logger.error("")
            logger.error("  3. Check for other BLE services:")
            logger.error("     sudo systemctl list-units | grep -i ble")
            logger.error("")
            logger.error("See TROUBLESHOOTING_BLE.md for detailed help")
            logger.error("=" * 70)

            # Stop the mainloop gracefully instead of raising
            if self.mainloop:
                logger.info("Stopping server due to advertisement failure...")
                GLib.timeout_add(100, lambda: self.mainloop.quit())

        self.ad_manager.RegisterAdvertisement(
            self.advertisement.get_path(),
            {},
            reply_handler=success_handler,
            error_handler=error_handler,
        )

    def start(self):
        """Start the BLE GATT server."""
        logger.info("Starting BLE GATT server...")

        try:
            # Setup
            self.setup()

            # Register application and advertisement
            self.register_application()
            self.register_advertisement()

            # Start GLib main loop
            self.mainloop = GLib.MainLoop()
            logger.info("BLE GATT server running. Press Ctrl+C to stop.")

            try:
                self.mainloop.run()
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
        except Exception as e:
            logger.error(f"Failed to start BLE GATT server: {e}")
            raise
        finally:
            self.stop()

    def stop(self):
        """Stop the BLE GATT server."""
        logger.info("Stopping BLE GATT server...")

        # Unregister advertisement
        if self.ad_manager and self.advertisement:
            try:
                self.ad_manager.UnregisterAdvertisement(self.advertisement.get_path())
                logger.debug("BLE advertisement unregistered")
            except Exception as e:
                logger.error(f"Error unregistering advertisement: {e}")

        # Unregister application
        if self.gatt_manager and self.application:
            try:
                self.gatt_manager.UnregisterApplication(self.application.get_path())
                logger.debug("GATT application unregistered")
            except Exception as e:
                logger.error(f"Error unregistering application: {e}")

        # Close API client
        if self.api_client:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.api_client.close())
            finally:
                loop.close()

        # Quit mainloop
        if self.mainloop:
            self.mainloop.quit()

        logger.info("BLE GATT server stopped")
