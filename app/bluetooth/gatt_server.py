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
from app.bluetooth.auth import BleAuthenticator
from app.bluetooth.services import SystemdService, SystemService, UploadService
from app.logging_config import get_logger
from app.main import _get_hardware_info

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
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
AGENT_IFACE = "org.bluez.Agent1"
DEVICE_IFACE = "org.bluez.Device1"
AGENT_PATH = "/org/bluez/tsconfig/agent"

# Advertising interval in milliseconds. Without it the kernel's default of 1280 ms applies, and a
# scanner with a 3-10 s timeout then misses the device in most attempts (measured on Pi 3 and
# Pi 5: 7-9 of 40 3-second scans found it, 38-40 of 40 at 100 ms).
ADVERTISING_INTERVAL_MS = 100


class InvalidArgsException(dbus.exceptions.DBusException):
    """D-Bus exception for invalid arguments."""

    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupportedException(dbus.exceptions.DBusException):
    """D-Bus exception for unsupported operations."""

    _dbus_error_name = "org.bluez.Error.NotSupported"


class NotPermittedException(dbus.exceptions.DBusException):
    """D-Bus exception for permission denied."""

    _dbus_error_name = "org.bluez.Error.NotPermitted"


class RejectedException(dbus.exceptions.DBusException):
    """D-Bus exception for a rejected pairing request."""

    _dbus_error_name = "org.bluez.Error.Rejected"


class NoInputNoOutputAgent(dbus.service.Object):
    """BlueZ pairing agent for a device without display or keyboard.

    Registered with the NoInputNoOutput capability, so pairing is "Just Works":
    the link gets encrypted (LE Secure Connections where supported) without any
    user interaction. This protects against passive sniffing of the operator
    login; it does not protect against an active man-in-the-middle during
    pairing, which would need a displayed/typed passkey. The operator's
    authorization is the password login on top (see app.bluetooth.auth).
    """

    def __init__(self, bus: dbus.Bus, path: str = AGENT_PATH):
        self.path = path
        dbus.service.Object.__init__(self, bus, path)

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        logger.debug("Pairing agent released")

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        return  # services are protected by the login, not by per-service authorization

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        logger.debug(f"Accepting Just Works pairing request from {device}")

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        logger.debug(f"Accepting pairing confirmation from {device}")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        raise RejectedException("PIN code entry is not supported")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        raise RejectedException("Passkey entry is not supported")

    @dbus.service.method(AGENT_IFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        return

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        return

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self):
        logger.debug("Pairing request cancelled")


class Application(dbus.service.Object):
    """GATT Application for BlueZ D-Bus."""

    def __init__(
        self,
        bus: dbus.Bus,
        path: str,
        api_client: TsConfigApiClient,
        authenticator: Optional[BleAuthenticator] = None,
    ):
        """Initialize the GATT application.

        Args:
            bus: D-Bus system bus
            path: D-Bus object path
            api_client: HTTP API client instance
            authenticator: Operator login tracker; None disables authentication
                (and link encryption) entirely
        """
        self.path = path
        self.services: List = []
        self.api_client = api_client
        self.authenticator = authenticator

        dbus.service.Object.__init__(self, bus, self.path)

        # Create services
        self.add_service(SystemService(bus, 0, self.path, api_client, authenticator))
        self.add_service(SystemdService(bus, 1, self.path, api_client, authenticator))
        self.add_service(UploadService(bus, 2, self.path, api_client, authenticator))

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

    def on_disconnect(self, device: str):
        """Drop per-connection state (partial writes) of a device that disconnected.

        Args:
            device: BlueZ device path of the connection that ended
        """
        for service in self.services:
            for chrc in service.characteristics:
                chrc.on_disconnect(device)

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
        self.service_data: Dict[str, bytes] = {}
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

    def set_service_data(self, uuid: str, data: bytes):
        """Set service data for a specific UUID.

        Args:
            uuid: Service UUID to attach data to (e.g., "7473")
            data: Binary data to include in advertisement
        """
        self.service_data[uuid] = data
        logger.debug(f"Set service data for UUID {uuid}: {len(data)} bytes")

    def get_properties(self) -> Dict[str, Any]:
        """Get advertisement properties for D-Bus."""
        properties = {
            "Type": self.ad_type,
            "ServiceUUIDs": dbus.Array(self.service_uuids, signature="s"),
            "LocalName": dbus.String(self.local_name),
            "IncludeTxPower": dbus.Boolean(self.include_tx_power),
            "MinInterval": dbus.UInt32(ADVERTISING_INTERVAL_MS),
            "MaxInterval": dbus.UInt32(ADVERTISING_INTERVAL_MS),
        }
        
        # Add service data if present
        if self.service_data:
            service_data_dict = {}
            for uuid, data in self.service_data.items():
                service_data_dict[uuid] = dbus.Array(data, signature='y')
            properties["ServiceData"] = dbus.Dictionary(service_data_dict, signature='sv')
        
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
        require_auth: bool = True,
        discoverable: bool = True,
    ):
        """Initialize the BLE GATT server.

        Args:
            api_url: Base URL of the tsconfig HTTP API
            device_name: BLE device name (defaults to system hostname)
            require_auth: Require an encrypted link and the operator login
                (pi's password) for everything except the discovery data
            discoverable: Whether the device should be discoverable
        """
        self.api_url = api_url
        self.device_name = device_name or socket.gethostname()
        self.require_auth = require_auth
        self.authenticator: Optional[BleAuthenticator] = BleAuthenticator() if require_auth else None
        self.agent: Optional[NoInputNoOutputAgent] = None
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

        if self.require_auth:
            self.setup_pairing(adapter_obj)

        # Registered regardless of authentication: partial writes must not outlive their connection
        self.bus.add_signal_receiver(
            self._on_device_properties_changed,
            dbus_interface=DBUS_PROP_IFACE,
            signal_name="PropertiesChanged",
            arg0=DEVICE_IFACE,
            path_keyword="path",
        )

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
        self.application = Application(self.bus, app_path, self.api_client, self.authenticator)

        # Create advertisement
        logger.debug("Creating BLE advertisement...")
        self.advertisement = Advertisement(self.bus, 0, "peripheral", self.device_name)
        
        # Advertise with 16-bit tsOS UUID (0x7473) and serial number in service data
        # This is space-efficient and allows client-side filtering
        from app.bluetooth.protocol import TSOS_SERVICE_UUID_16BIT
        
        self.advertisement.add_service_uuid(TSOS_SERVICE_UUID_16BIT)
        logger.debug(f"Advertising with tsOS service UUID: {TSOS_SERVICE_UUID_16BIT}")
        
        # Get serial number and add to service data (last 8 hex chars → 4 bytes)
        try:
            hardware_info = _get_hardware_info()
            serial_short = hardware_info.get("serial_short", "00000000")
            serial_bytes = bytes.fromhex(serial_short)
            self.advertisement.set_service_data(TSOS_SERVICE_UUID_16BIT, serial_bytes)
            logger.debug(f"Advertising serial: {serial_short} ({len(serial_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"Could not add serial to advertisement: {e}")
            # Continue without serial - device will still be discoverable

        logger.info("BLE GATT Server setup complete")

    def setup_pairing(self, adapter_obj):
        """Make the adapter pairable and register the Just Works pairing agent."""
        try:
            self.adapter_props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(True))
            self.adapter_props.Set(ADAPTER_IFACE, "PairableTimeout", dbus.UInt32(0))
        except Exception as e:
            logger.warning(f"Could not make the adapter pairable: {e}")

        try:
            self.agent = NoInputNoOutputAgent(self.bus)
            agent_manager = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, "/org/bluez"), AGENT_MANAGER_IFACE)
            agent_manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
            agent_manager.RequestDefaultAgent(AGENT_PATH)
            logger.info("Registered NoInputNoOutput pairing agent")
        except Exception as e:
            # Encrypted characteristics cannot be used without a way to pair.
            logger.error(f"Could not register the pairing agent: {e}")

    def _on_device_properties_changed(self, interface, changed, invalidated, path=None):
        """A connection's login and half-received writes end when its device disconnects."""
        if path and "Connected" in changed and not bool(changed["Connected"]):
            if self.authenticator:
                self.authenticator.forget(str(path))
            if self.application:
                self.application.on_disconnect(str(path))

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
