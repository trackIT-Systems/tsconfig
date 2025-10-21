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
from app.bluetooth.protocol import chunker, formatter, parser
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
        self.notification_callbacks: List[Callable] = []

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

        chunks = chunker.chunk_and_encode(data)
        for chunk in chunks:
            self.send_notification(chunk)

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
        metadata about the data (length, chunks, content type, status).
        """
        try:
            # Check if notifications are enabled
            if not self.notifying:
                logger.warning(f"Read attempt on {self.uuid} without notifications enabled")
                error_metadata = formatter.metadata(content_length=0, chunks_expected=0, status="error")
                error_dict = json.loads(error_metadata)
                error_dict["error"] = "Notifications must be enabled to receive data"
                error_response = json.dumps(error_dict)
                return dbus.Array(error_response.encode("utf-8"), signature="y")

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

                # Calculate chunks
                chunks = chunker.chunk_data(response)

                # Schedule data to be sent via notifications AFTER read completes
                # This prevents the notification from overwriting the read response value
                logger.debug(f"Preparing {len(response_bytes)} bytes in {len(chunks)} notification chunks for {self.uuid}")
                from gi.repository import GLib

                GLib.idle_add(lambda: self.send_chunked_response(response) or False)

                # Return metadata about the data
                metadata_response = formatter.metadata(
                    content_length=len(response_bytes),
                    chunks_expected=len(chunks),
                    content_type="application/json"
                    if response.strip().startswith("{") or response.strip().startswith("[")
                    else "text/plain",
                    status="ready",
                )
                return dbus.Array(metadata_response.encode("utf-8"), signature="y")

            finally:
                # Only close the loop if we created it
                if loop_created:
                    loop.close()
        except Exception as e:
            logger.error(f"Error reading {self.uuid}: {e}")
            # Return error in metadata format to maintain notification-only protocol
            error_metadata = formatter.metadata(content_length=0, chunks_expected=0, status="error")
            error_dict = json.loads(error_metadata)
            error_dict["error"] = str(e)
            error_response = json.dumps(error_dict)
            return dbus.Array(error_response.encode("utf-8"), signature="y")


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
        # Chunk accumulator for chunked writes
        self.write_chunks: Dict[int, str] = {}
        self.expected_chunks: int = 0

    def _reset_chunks(self):
        """Reset chunk accumulator state."""
        self.write_chunks = {}
        self.expected_chunks = 0

    def _process_write_data(self, request_data: Dict[str, Any]):
        """Process write data by calling the write handler.

        Args:
            request_data: Parsed and reassembled request data
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
        """Handle write operation with support for chunked writes."""
        # Check pairing if required
        if self.require_pairing and not self.check_pairing(options):
            error_response = formatter.pairing_required()
            self.send_notification(error_response.encode("utf-8"))
            return

        try:
            # Parse the incoming data
            data_bytes = bytes(value)
            is_chunk, parsed_data = parser.parse_chunked_write(data_bytes)

            if parsed_data is None:
                raise ValueError("Invalid JSON data")

            if is_chunk:
                # Handle chunked write protocol
                seq = parsed_data["seq"]
                total = parsed_data["total"]
                chunk_data = parsed_data["data"]
                is_complete = parsed_data.get("complete", False)

                # Store chunk
                self.write_chunks[seq] = chunk_data
                self.expected_chunks = total

                logger.debug(f"Received chunk {seq + 1}/{total} for {self.uuid}")

                # Check if we have all chunks
                if is_complete and len(self.write_chunks) == total:
                    # Reassemble chunks in order
                    reassembled_data = "".join(self.write_chunks[i] for i in range(total))
                    logger.info(f"Reassembled {len(reassembled_data)} bytes from {total} chunks for {self.uuid}")

                    # Reset chunk state
                    self._reset_chunks()

                    # Parse the reassembled data as JSON
                    request_data = parser.parse_json(reassembled_data.encode("utf-8"))
                    if request_data is None:
                        raise ValueError("Invalid JSON data after reassembly")

                    # Process the complete request
                    self._process_write_data(request_data)
                else:
                    # Still waiting for more chunks
                    logger.debug(f"Waiting for more chunks ({len(self.write_chunks)}/{total})")
            else:
                # Direct write (not chunked) - backwards compatible
                logger.debug(f"Processing direct write for {self.uuid}")
                self._process_write_data(parsed_data)

        except Exception as e:
            logger.error(f"Error writing to {self.uuid}: {e}")
            # Reset chunk state on error
            self._reset_chunks()
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
                reboot=data.get("reboot", False),
            )

        super().__init__(
            bus, index, "00003002-7473-4f53-636f-6e6669672121", service, api_client, upload_zip_handler, require_pairing
        )
