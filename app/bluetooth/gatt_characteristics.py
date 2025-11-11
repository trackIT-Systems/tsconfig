"""GATT Characteristic implementations for BlueZ D-Bus.

This module provides the base Characteristic class and specific implementations
for each tsconfig API endpoint exposed via BLE GATT.
"""

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

import dbus
import dbus.service

from app.bluetooth.api_client import TsConfigApiClient
from app.bluetooth.protocol import (
    BinaryChunker,
    formatter,
    parser,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT,
    STATUS_READY,
    STATUS_ERROR,
    DEFAULT_MTU,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

# BlueZ D-Bus constants
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"


class Characteristic(dbus.service.Object):
    """Base GATT Characteristic class for BlueZ D-Bus."""

    def __init__(
        self,
        bus: dbus.Bus,
        index: int,
        uuid: str,
        flags: List[str],
        service,
        api_client: TsConfigApiClient,
        require_pairing: bool = True,
    ):
        """Initialize a GATT Characteristic.

        Args:
            bus: D-Bus system bus
            index: Characteristic index
            uuid: Characteristic UUID
            flags: Characteristic flags (read, write, notify, etc.)
            service: Parent GATT service
            api_client: HTTP API client instance
            require_pairing: Whether to require pairing for write operations
        """
        self.path = f"{service.path}/char{index:04d}"
        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = flags
        self.api_client = api_client
        self.require_pairing = require_pairing
        self.notifying = False
        self._mtu = DEFAULT_MTU  # Will be updated when device connects
        self._chunker = BinaryChunker(self._mtu)

        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> Dict[str, Any]:
        """Get characteristic properties for D-Bus."""
        return {
            GATT_CHRC_IFACE: {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": self.flags,
            }
        }

    def get_path(self) -> str:
        """Get the D-Bus object path."""
        return dbus.ObjectPath(self.path)

    def get_mtu(self, options: Dict[str, Any]) -> int:
        """Get the negotiated MTU for the connected device.

        Args:
            options: Options dictionary from D-Bus call (contains device path)

        Returns:
            Negotiated MTU or default if unavailable
        """
        try:
            device_path = options.get("device")
            if not device_path:
                return DEFAULT_MTU

            # Query the device's MTU from BlueZ
            device_obj = self.bus.get_object("org.bluez", device_path)
            device_props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
            mtu = device_props.Get("org.bluez.Device1", "MTU")
            logger.debug(f"Negotiated MTU for {device_path}: {mtu}")
            return int(mtu)
        except Exception as e:
            logger.debug(f"Could not get MTU from device, using default: {e}")
            return DEFAULT_MTU

    def update_mtu(self, mtu: int):
        """Update MTU and recreate chunker.

        Args:
            mtu: New MTU value
        """
        if mtu != self._mtu:
            self._mtu = mtu
            self._chunker = BinaryChunker(mtu)
            logger.debug(f"Updated MTU to {mtu} for {self.uuid}, chunk size: {self._chunker.chunk_size}")

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """D-Bus GetAll method."""
        if interface != GATT_CHRC_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs", "Invalid interface")
        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        """D-Bus ReadValue method - to be overridden by subclasses."""
        logger.warning(f"ReadValue called on {self.uuid} but not implemented")
        return []

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        """D-Bus WriteValue method - to be overridden by subclasses."""
        logger.warning(f"WriteValue called on {self.uuid} but not implemented")

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        """Start sending notifications."""
        if self.notifying:
            return
        self.notifying = True
        logger.debug(f"Notifications started for {self.uuid}")

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        """Stop sending notifications."""
        if not self.notifying:
            return
        self.notifying = False
        logger.debug(f"Notifications stopped for {self.uuid}")

    @dbus.service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed_properties, invalidated_properties):
        """Signal that properties have changed (used for notifications)."""
        pass

    def send_notification(self, value: bytes):
        """Send a notification to connected clients.

        Args:
            value: Data to send
        """
        if not self.notifying:
            return

        # Send notification via D-Bus PropertiesChanged signal
        # This updates the "Value" property of the characteristic
        self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": dbus.Array(value, signature="y")}, [])

    def send_chunked_response(self, data: str):
        """Send a chunked response via notifications.

        Args:
            data: String data to send (will be chunked if necessary)
        """
        if not self.notifying:
            logger.warning(f"Cannot send chunked response on {self.uuid}: not notifying")
            return

        # Convert string to bytes and chunk
        data_bytes = data.encode("utf-8")
        chunks = self._chunker.chunk_data(data_bytes)
        
        logger.debug(f"Sending {len(data_bytes)} bytes in {len(chunks)} chunks for {self.uuid}")
        
        # Send chunks sequentially (no delays - rely on BlueZ queuing)
        for i, chunk in enumerate(chunks):
            self.send_notification(chunk)
            logger.debug(f"Sent chunk {i+1}/{len(chunks)} ({len(chunk)} bytes)")

    def check_pairing(self, options: Dict[str, Any]) -> bool:
        """Check if the requesting device is paired.

        Args:
            options: Options dictionary from D-Bus call

        Returns:
            True if paired or pairing not required, False otherwise
        """
        if not self.require_pairing:
            return True

        # In a real implementation, check the device pairing status via BlueZ
        # For now, we'll check if the device path is provided
        device = options.get("device")
        if not device:
            logger.warning("No device in options, assuming not paired")
            return False

        # TODO: Query BlueZ for actual pairing status
        # For now, we'll assume if a device is provided, it's paired
        return True


class ReadOnlyCharacteristic(Characteristic):
    """Base class for read-only characteristics."""

    def __init__(self, bus, index, uuid, service, api_client, read_handler: Callable):
        """Initialize a read-only characteristic.

        Args:
            bus: D-Bus system bus
            index: Characteristic index
            uuid: Characteristic UUID
            service: Parent GATT service
            api_client: HTTP API client
            read_handler: Async function to call for reading data
        """
        super().__init__(bus, index, uuid, ["read", "notify"], service, api_client, require_pairing=False)
        self.read_handler = read_handler

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        """Handle read operation with notification-only protocol.

        All data is transferred via notifications. Read operations return only
        binary CBOR metadata about the data (length, chunks, content type, status).
        """
        try:
            # Check if notifications are enabled
            if not self.notifying:
                logger.warning(f"Read attempt on {self.uuid} without notifications enabled")
                error_metadata = formatter.metadata_cbor(
                    content_length=0,
                    chunk_count=0,
                    status=STATUS_ERROR,
                    error_message="Notifications must be enabled to receive data"
                )
                return dbus.Array(error_metadata, signature="y")

            # Update MTU from device
            mtu = self.get_mtu(options)
            self.update_mtu(mtu)

            # Run the async handler in a synchronous context
            # Handle event loop carefully to avoid "Event loop is closed" errors
            try:
                # Try to get existing event loop
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    # Loop exists but is closed, create a new one
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop_created = True
                else:
                    # Use existing loop
                    loop_created = False
            except RuntimeError:
                # No event loop exists, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop_created = True

            try:
                data = loop.run_until_complete(self.read_handler())
                # Ensure httpx client is closed before closing the loop
                if hasattr(self.api_client, "client") and self.api_client.client:
                    loop.run_until_complete(self.api_client.client.aclose())
                    self.api_client.client = None
                response = formatter.success(data)
                response_bytes = response.encode("utf-8")

                # Calculate chunks using binary chunker
                chunk_count = self._chunker.get_chunk_count(response_bytes)

                # Detect content type
                content_type = CONTENT_TYPE_JSON if response.strip().startswith(("{", "[")) else CONTENT_TYPE_TEXT

                # Schedule data to be sent via notifications AFTER read completes
                # This prevents the notification from overwriting the read response value
                logger.debug(f"Preparing {len(response_bytes)} bytes in {chunk_count} notification chunks for {self.uuid}")
                from gi.repository import GLib

                GLib.idle_add(lambda: self.send_chunked_response(response) or False)

                # Return CBOR metadata about the data
                metadata_response = formatter.metadata_cbor(
                    content_length=len(response_bytes),
                    chunk_count=chunk_count,
                    content_type=content_type,
                    status=STATUS_READY,
                )
                return dbus.Array(metadata_response, signature="y")

            finally:
                # Only close the loop if we created it
                if loop_created:
                    loop.close()
        except Exception as e:
            logger.error(f"Error reading {self.uuid}: {e}")
            # Return error in CBOR metadata format to maintain notification-only protocol
            error_metadata = formatter.metadata_cbor(
                content_length=0,
                chunk_count=0,
                status=STATUS_ERROR,
                error_message=str(e)
            )
            return dbus.Array(error_metadata, signature="y")


class WriteOnlyCharacteristic(Characteristic):
    """Base class for write-only characteristics."""

    def __init__(self, bus, index, uuid, service, api_client, write_handler: Callable, require_pairing: bool = True):
        """Initialize a write-only characteristic.

        Args:
            bus: D-Bus system bus
            index: Characteristic index
            uuid: Characteristic UUID
            service: Parent GATT service
            api_client: HTTP API client
            write_handler: Async function to call for writing data
            require_pairing: Whether to require pairing
        """
        super().__init__(bus, index, uuid, ["write", "notify"], service, api_client, require_pairing)
        self.write_handler = write_handler
        # Buffer for accumulating multi-write data
        self.write_buffer = bytearray()

    def _process_write_data(self, request_data: Dict[str, Any]):
        """Process write data by calling the write handler.

        Args:
            request_data: Parsed request data
        """
        # Run the async handler
        # Handle event loop carefully to avoid "Event loop is closed" errors
        try:
            # Try to get existing event loop
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                # Loop exists but is closed, create a new one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop_created = True
            else:
                # Use existing loop
                loop_created = False
        except RuntimeError:
            # No event loop exists, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_created = True

        try:
            result = loop.run_until_complete(self.write_handler(request_data))
            # Ensure httpx client is closed before closing the loop
            if hasattr(self.api_client, "client") and self.api_client.client:
                loop.run_until_complete(self.api_client.client.aclose())
                self.api_client.client = None
            response = formatter.success(result)
            self.send_chunked_response(response)
        finally:
            # Only close the loop if we created it
            if loop_created:
                loop.close()

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        """Handle write operation with simplified protocol.
        
        For small writes: Direct JSON parsing
        For large writes: Sequential writes are accumulated until valid JSON is received
        """
        # Check pairing if required
        if self.require_pairing and not self.check_pairing(options):
            error_response = formatter.pairing_required()
            self.send_notification(error_response.encode("utf-8"))
            return

        try:
            # Add incoming data to buffer
            data_bytes = bytes(value)
            self.write_buffer.extend(data_bytes)
            
            logger.debug(f"Received {len(data_bytes)} bytes for {self.uuid}, buffer now {len(self.write_buffer)} bytes")

            # Try to parse as JSON
            parsed_data = parser.parse_json(bytes(self.write_buffer))

            if parsed_data is not None:
                # Successfully parsed - process the request
                logger.info(f"Successfully parsed {len(self.write_buffer)} bytes of JSON for {self.uuid}")
                self.write_buffer.clear()
                self._process_write_data(parsed_data)
            else:
                # Not yet complete JSON - wait for more data
                logger.debug(f"Incomplete JSON, waiting for more data (buffer: {len(self.write_buffer)} bytes)")

        except Exception as e:
            logger.error(f"Error writing to {self.uuid}: {e}")
            # Clear buffer on error
            self.write_buffer.clear()
            error_response = formatter.error(str(e))
            self.send_notification(error_response.encode("utf-8"))


# Specific characteristic implementations


class SystemStatusCharacteristic(ReadOnlyCharacteristic):
    """System Status characteristic."""

    def __init__(self, bus, index, service, api_client):
        super().__init__(
            bus, index, "00001001-7473-4f53-636f-6e6669672121", service, api_client, api_client.get_system_status
        )


class ServerModeInfoCharacteristic(ReadOnlyCharacteristic):
    """Server Mode Info characteristic."""

    def __init__(self, bus, index, service, api_client):
        super().__init__(
            bus, index, "00001002-7473-4f53-636f-6e6669672121", service, api_client, api_client.get_server_mode
        )


class TimedatectlStatusCharacteristic(ReadOnlyCharacteristic):
    """Timedatectl Status characteristic."""

    def __init__(self, bus, index, service, api_client):
        super().__init__(
            bus, index, "00001003-7473-4f53-636f-6e6669672121", service, api_client, api_client.get_timedatectl_status
        )


class AvailableServicesCharacteristic(ReadOnlyCharacteristic):
    """Available Services characteristic."""

    def __init__(self, bus, index, service, api_client):
        super().__init__(
            bus, index, "00001004-7473-4f53-636f-6e6669672121", service, api_client, api_client.get_available_services
        )


class SystemdServicesListCharacteristic(ReadOnlyCharacteristic):
    """Systemd Services List characteristic."""

    def __init__(self, bus, index, service, api_client):
        super().__init__(
            bus, index, "00002001-7473-4f53-636f-6e6669672121", service, api_client, api_client.get_systemd_services
        )


class SystemdServiceActionCharacteristic(WriteOnlyCharacteristic):
    """Systemd Service Action characteristic."""

    def __init__(self, bus, index, service, api_client, require_pairing: bool = True):
        async def action_handler(data: Dict[str, Any]):
            if not parser.validate_service_action(data):
                raise ValueError("Invalid service action request. Required: service, action")
            return await api_client.systemd_service_action(data["service"], data["action"])

        super().__init__(
            bus, index, "00002002-7473-4f53-636f-6e6669672121", service, api_client, action_handler, require_pairing
        )


class SystemdRebootCharacteristic(WriteOnlyCharacteristic):
    """System Reboot characteristic."""

    def __init__(self, bus, index, service, api_client, require_pairing: bool = True):
        async def reboot_handler(data: Dict[str, Any]):
            return await api_client.systemd_reboot()

        super().__init__(
            bus, index, "00002003-7473-4f53-636f-6e6669672121", service, api_client, reboot_handler, require_pairing
        )


class SystemdServiceLogsCharacteristic(WriteOnlyCharacteristic):
    """Systemd Service Logs characteristic."""

    def __init__(self, bus, index, service, api_client, require_pairing: bool = True):
        async def logs_handler(data: Dict[str, Any]):
            if not parser.validate_log_request(data):
                raise ValueError("Invalid log request. Required: service")
            lines = data.get("lines", 100)
            logs = await api_client.get_systemd_logs(data["service"], lines)
            return {"logs": logs}

        super().__init__(
            bus, index, "00002004-7473-4f53-636f-6e6669672121", service, api_client, logs_handler, require_pairing
        )


class UploadConfigCharacteristic(WriteOnlyCharacteristic):
    """Config Upload characteristic (POST /api/configs/update)."""

    def __init__(self, bus, index, service, api_client, require_pairing: bool = True):
        async def upload_handler(data: Dict[str, Any]):
            if not parser.validate_upload_request(data):
                raise ValueError("Invalid upload request. Required: filename, content")
            return await api_client.upload_config(
                filename=data["filename"],
                content=data["content"],
                config_group=data.get("config_group"),
                restart_service=data.get("restart_service", False),
            )

        super().__init__(
            bus, index, "00003001-7473-4f53-636f-6e6669672121", service, api_client, upload_handler, require_pairing
        )


class UploadZipCharacteristic(WriteOnlyCharacteristic):
    """Zip Upload characteristic (POST /api/configs.zip)."""

    def __init__(self, bus, index, service, api_client, require_pairing: bool = True):
        async def upload_zip_handler(data: Dict[str, Any]):
            if not parser.validate_upload_request(data):
                raise ValueError("Invalid upload request. Required: filename, content")
            return await api_client.upload_zip(
                filename=data["filename"],
                content=data["content"],
                restart_services=data.get("restart_services", False),
                pedantic=data.get("pedantic", False),
                force=data.get("force", False),
                reboot=data.get("reboot", False),
            )

        super().__init__(
            bus, index, "00003002-7473-4f53-636f-6e6669672121", service, api_client, upload_zip_handler, require_pairing
        )
