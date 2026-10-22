"""Core GATT server implementation using BlueZ D-Bus API.

This module provides the main BLE GATT server that manages the D-Bus application,
services, and BLE advertising.
"""

import socket
from collections.abc import Callable
from typing import Any

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from app.bluetooth import executor as ble_executor
from app.bluetooth.api_client import TsConfigApiClient
from app.bluetooth.auth import BleAuthenticator
from app.bluetooth.services import SystemdService, SystemService, UploadService
from app.bluetooth.supervisor import Supervisor, sd_notify
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

# How often the gateway checks that BlueZ still advertises it (seconds)
HEALTH_CHECK_INTERVAL_S = 10
BLUEZ_ALREADY_EXISTS = "org.bluez.Error.AlreadyExists"


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


class AdapterNotFoundError(RuntimeError):
    """No Bluetooth adapter is known to BlueZ (the supervisor retries later)."""


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
        logger.debug("Accepting Just Works pairing request from %s", device)

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        logger.debug("Accepting pairing confirmation from %s", device)

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
        authenticator: BleAuthenticator | None = None,
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
        self.services: list = []
        self.api_client = api_client
        self.authenticator = authenticator

        dbus.service.Object.__init__(self, bus, self.path)

        # Create services
        self.add_service(SystemService(bus, 0, self.path, api_client, authenticator))
        self.add_service(SystemdService(bus, 1, self.path, api_client, authenticator))
        self.add_service(UploadService(bus, 2, self.path, api_client, authenticator))

        logger.debug("GATT Application created with %s services", len(self.services))

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

    def __init__(
        self,
        bus: dbus.Bus,
        index: int,
        advertising_type: str,
        device_name: str | None = None,
        on_release: Callable[[], None] | None = None,
    ):
        """Initialize the BLE advertisement.

        Args:
            bus: D-Bus system bus
            index: Advertisement index
            advertising_type: Type of advertisement (e.g., "peripheral")
            device_name: Optional device name to advertise
            on_release: Called when BlueZ drops the advertisement
        """
        self.on_release = on_release
        self.path = f"{self.PATH_BASE}{index}"
        self.bus = bus
        self.ad_type = advertising_type
        # Truncate device name to fit within BLE advertisement 31-byte limit
        # Format: 1 byte (length) + 1 byte (type) + name + UUID (16 bytes) + flags (~3 bytes) = ~21 bytes for name
        hostname = device_name or socket.gethostname()
        self.local_name = hostname[:20] if len(hostname) > 20 else hostname
        self.service_uuids: list[str] = []
        self.service_data: dict[str, bytes] = {}
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
        logger.debug("Set service data for UUID %s: %s bytes", uuid, len(data))

    def get_properties(self) -> dict[str, Any]:
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
                service_data_dict[uuid] = dbus.Array(data, signature="y")
            properties["ServiceData"] = dbus.Dictionary(service_data_dict, signature="sv")

        return {LE_ADVERTISEMENT_IFACE: properties}

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """D-Bus GetAll method."""
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException("Invalid interface")
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        """BlueZ no longer advertises this (bluetoothd stopping, adapter reset, ...)."""
        logger.debug("Advertisement %s released", self.path)
        if self.on_release:
            self.on_release()


class BleGattServer:
    """Main BLE GATT server managing D-Bus and BlueZ interactions.

    The objects the gateway exports (GATT application, advertisement, pairing agent) are
    created once. What has to be registered with BlueZ is tracked by a `Supervisor`, which
    registers it at startup and again whenever BlueZ loses it (bluetoothd restart,
    advertisement released, adapter reset).
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        device_name: str | None = None,
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
        self.authenticator: BleAuthenticator | None = BleAuthenticator() if require_auth else None
        self.agent: NoInputNoOutputAgent | None = None
        self.discoverable = discoverable

        self.bus: dbus.Bus | None = None
        self.adapter_props: dbus.Interface | None = None
        self.ad_manager: dbus.Interface | None = None
        self.gatt_manager: dbus.Interface | None = None
        self.application: Application | None = None
        self.advertisement: Advertisement | None = None
        self.api_client: TsConfigApiClient | None = None
        self.mainloop: GLib.MainLoop | None = None
        self.executor: ble_executor.ThreadedExecutor | None = None
        self.supervisor = Supervisor(register=self._register_missing, schedule=self._schedule)

        self._app_inflight = False
        self._adv_inflight = False
        self._active_instances: int | None = None  # last ActiveInstances read from BlueZ
        self._owner_reported = False

        logger.debug("BLE GATT server initialized for device %s", self.device_name)

    @staticmethod
    def _schedule(delay: float, fn: Callable[[], None]) -> None:
        """Call `fn` once on the main loop after `delay` seconds."""
        GLib.timeout_add(max(int(delay * 1000), 1), lambda: fn() or False)

    def find_adapter(self) -> str:
        """Find the first available Bluetooth adapter.

        Returns:
            D-Bus path of the adapter

        Raises:
            AdapterNotFoundError: If no adapter is found
        """
        remote_om = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
        objects = remote_om.GetManagedObjects()

        for path, interfaces in objects.items():
            if ADAPTER_IFACE in interfaces:
                logger.debug("Found Bluetooth adapter: %s", path)
                return path

        raise AdapterNotFoundError("No Bluetooth adapter found")

    def setup(self):
        """Create what the gateway exports and subscribe to BlueZ events (nothing is registered yet)."""
        logger.debug("Setting up the BLE GATT server")

        # Initialize D-Bus
        logger.debug("Connecting to the D-Bus system bus")
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()

        # HTTP work runs on a background loop so that a slow API never blocks the main loop
        self.executor = ble_executor.ThreadedExecutor(dispatch=lambda fn: GLib.idle_add(lambda: fn() or False))
        ble_executor.set_executor(self.executor)

        # Create API client
        logger.debug("Creating the API client")
        self.api_client = TsConfigApiClient(base_url=self.api_url)

        # Create GATT application
        logger.debug("Creating the GATT application")
        app_path = "/org/bluez/tsconfig/app"
        self.application = Application(self.bus, app_path, self.api_client, self.authenticator)

        # Create advertisement
        logger.debug("Creating the BLE advertisement")
        self.advertisement = Advertisement(
            self.bus, 0, "peripheral", self.device_name, on_release=self.supervisor.advertisement_released
        )

        # Advertise with 16-bit tsOS UUID (0x7473) and serial number in service data
        # This is space-efficient and allows client-side filtering
        from app.bluetooth.protocol import TSOS_SERVICE_UUID_16BIT

        self.advertisement.add_service_uuid(TSOS_SERVICE_UUID_16BIT)
        logger.debug("Advertising with tsOS service UUID: %s", TSOS_SERVICE_UUID_16BIT)

        # Get serial number and add to service data (last 8 hex chars → 4 bytes)
        try:
            hardware_info = _get_hardware_info()
            serial_short = hardware_info.get("serial_short", "00000000")
            serial_bytes = bytes.fromhex(serial_short)
            self.advertisement.set_service_data(TSOS_SERVICE_UUID_16BIT, serial_bytes)
            logger.debug("Advertising serial: %s (%s bytes)", serial_short, len(serial_bytes))
        except (ValueError, TypeError) as e:
            # TypeError: no serial in /proc/cpuinfo (not a Raspberry Pi)
            logger.warning("Could not add serial to advertisement: %s", e)
            # Continue without serial - device will still be discoverable

        if self.require_auth:
            self.agent = NoInputNoOutputAgent(self.bus)

        # Registered regardless of authentication: partial writes must not outlive their connection
        self.bus.add_signal_receiver(
            self._on_device_properties_changed,
            dbus_interface=DBUS_PROP_IFACE,
            signal_name="PropertiesChanged",
            arg0=DEVICE_IFACE,
            path_keyword="path",
        )
        # Called with the current owner right away and whenever bluetoothd (re)starts or stops
        self.bus.watch_name_owner(BLUEZ_SERVICE_NAME, self._on_bluez_owner)

        logger.debug("BLE GATT server set up")

    # --- registration with BlueZ (driven by the supervisor) -------------------

    def _on_bluez_owner(self, owner: str):
        """bluetoothd appeared on or vanished from the bus."""
        present = bool(owner)
        self.ad_manager = self.gatt_manager = self.adapter_props = None
        self._active_instances = None
        self._app_inflight = self._adv_inflight = False
        if not self._owner_reported:
            # First report is the state at startup, not a change
            self._owner_reported = True
            self.supervisor.bluez_present = present
            if present:
                self.supervisor.request(0.0)
            else:
                logger.warning("bluetoothd is not on the bus yet, waiting for it")
            return
        self.supervisor.bluez_changed(present)

    def _register_missing(self):
        """Register whatever BlueZ does not have yet (called by the supervisor, may raise)."""
        adapter_path = self.find_adapter()
        adapter_obj = self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path)
        self.adapter_props = dbus.Interface(adapter_obj, DBUS_PROP_IFACE)
        self.ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)
        self.gatt_manager = dbus.Interface(adapter_obj, GATT_MANAGER_IFACE)

        # Set adapter properties
        logger.debug("Configuring the adapter properties")
        self.adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
        self.adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(self.discoverable))

        if self.require_auth and not self.supervisor.agent_registered:
            self.setup_pairing()

        if not self.supervisor.app_registered and not self._app_inflight:
            self.register_application()
        if not self.supervisor.adv_registered and not self._adv_inflight:
            self.register_advertisement()

    def setup_pairing(self):
        """Make the adapter pairable and register the Just Works pairing agent."""
        try:
            self.adapter_props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(True))
            self.adapter_props.Set(ADAPTER_IFACE, "PairableTimeout", dbus.UInt32(0))
        except dbus.exceptions.DBusException as e:
            logger.warning("Could not make the adapter pairable: %s", e)

        try:
            agent_manager = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, "/org/bluez"), AGENT_MANAGER_IFACE)
            agent_manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
            agent_manager.RequestDefaultAgent(AGENT_PATH)
            self.supervisor.agent_registered = True
            logger.debug("Registered the NoInputNoOutput pairing agent")
        except dbus.exceptions.DBusException as e:
            if BLUEZ_ALREADY_EXISTS in str(e):
                self.supervisor.agent_registered = True
                return
            # Encrypted characteristics cannot be used without a way to pair.
            logger.error("Could not register the pairing agent: %s", e)

    def _on_device_properties_changed(self, interface, changed, invalidated, path=None):
        """A connection's login and half-received writes end when its device disconnects."""
        if path and "Connected" in changed and not bool(changed["Connected"]):
            if self.authenticator:
                self.authenticator.forget(str(path))
            if self.application:
                self.application.on_disconnect(str(path))

    def register_application(self):
        """Register the GATT application with BlueZ."""
        logger.debug("Registering the GATT application")
        self._app_inflight = True

        def done():
            self._app_inflight = False
            self.supervisor.app_registered = True
            self.supervisor.registration_ok("GATT application")
            logger.debug("GATT application registered")

        def failed(error):
            self._app_inflight = False
            if BLUEZ_ALREADY_EXISTS in str(error):
                self.supervisor.app_registered = True
                return
            self.supervisor.registration_failed("GATT application", error)

        self.gatt_manager.RegisterApplication(self.application.get_path(), {}, reply_handler=done, error_handler=failed)

    def register_advertisement(self):
        """Register the BLE advertisement with BlueZ."""
        logger.debug("Registering the BLE advertisement")
        self._adv_inflight = True

        def done():
            self._adv_inflight = False
            self.supervisor.adv_registered = True
            self.supervisor.registration_ok("BLE advertisement")
            self._active_instances = None  # what we read before this is out of date
            logger.info("BLE advertisement registered as '%s'", self.device_name)

        def failed(error):
            self._adv_inflight = False
            if BLUEZ_ALREADY_EXISTS in str(error):
                self.supervisor.adv_registered = True
                return
            self.supervisor.registration_failed("BLE advertisement", error)

        self.ad_manager.RegisterAdvertisement(
            self.advertisement.get_path(), {}, reply_handler=done, error_handler=failed
        )

    # --- health ---------------------------------------------------------------

    def _health_check(self) -> bool:
        """Periodic self-check: feeds the systemd watchdog and triggers recovery."""
        self.supervisor.check(self._active_instances)
        # Ask BlueZ how many advertisements it has; the answer is used by the next check
        if self.adapter_props is not None:
            self.adapter_props.Get(
                LE_ADVERTISING_MANAGER_IFACE,
                "ActiveInstances",
                reply_handler=lambda value: setattr(self, "_active_instances", int(value)),
                error_handler=lambda error: setattr(self, "_active_instances", None),
            )
        return True

    def start(self):
        """Start the BLE GATT server."""
        logger.debug("Starting the BLE GATT server")

        try:
            self.setup()

            GLib.timeout_add_seconds(HEALTH_CHECK_INTERVAL_S, self._health_check)

            # Start GLib main loop; registration with BlueZ is done by the supervisor
            self.mainloop = GLib.MainLoop()
            sd_notify("READY=1")
            logger.debug("BLE GATT server running")

            try:
                self.mainloop.run()
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
        finally:
            self.stop()

    def stop(self):
        """Stop the BLE GATT server."""
        logger.debug("Stopping the BLE GATT server")
        sd_notify("STOPPING=1")

        # Unregister advertisement
        if self.ad_manager and self.advertisement:
            try:
                self.ad_manager.UnregisterAdvertisement(self.advertisement.get_path())
                logger.debug("BLE advertisement unregistered")
            except Exception:
                logger.exception("Cannot unregister the BLE advertisement")

        # Unregister application
        if self.gatt_manager and self.application:
            try:
                self.gatt_manager.UnregisterApplication(self.application.get_path())
                logger.debug("GATT application unregistered")
            except Exception:
                logger.exception("Cannot unregister the GATT application")

        # Close API client and stop the executor
        if self.executor:
            try:
                if self.api_client:
                    self.executor.run_blocking(self.api_client.close())
            except Exception as e:
                logger.debug("Cannot close the API client: %s", e, exc_info=True)
            self.executor.shutdown()

        # Quit mainloop
        if self.mainloop:
            self.mainloop.quit()

        logger.info("BLE GATT server stopped")
