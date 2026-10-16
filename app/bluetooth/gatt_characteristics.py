"""GATT Characteristic implementations for BlueZ D-Bus.

This module provides the base Characteristic class and specific implementations
for each tsconfig API endpoint exposed via BLE GATT.
"""

import asyncio
import contextvars
import time
from collections.abc import Callable
from typing import Any

import dbus
import dbus.service

from app.bluetooth import executor as ble_executor
from app.bluetooth.api_client import TsConfigApiClient
from app.bluetooth.auth import BleAuthenticator, BleRequestError, secure_flags
from app.bluetooth.protocol import (
    ATT_HEADER_SIZE,
    ATT_MAX_VALUE_LEN,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT,
    DEFAULT_MTU,
    STATUS_ERROR,
    STATUS_READY,
    BinaryChunker,
    formatter,
    parser,
)
from app.identity import OPERATOR_USER, STAFF_USER
from app.logging_config import get_logger

logger = get_logger(__name__)

# BlueZ D-Bus constants
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

# Multi-write requests: an incomplete request is dropped after this many seconds without
# another write, and a request may not grow beyond this many bytes (a config zip is ~10 kB)
WRITE_IDLE_TIMEOUT = 15.0
MAX_WRITE_BUFFER = 2 * 1024 * 1024

# BlueZ answers a client's read with an error ~5 s after it was asked (measured), so a read
# that the API has not answered by then is not worth waiting for; its metadata says so.
READ_TIMEOUT_S = 4.0

# The connection (BlueZ device path) whose request a handler is serving. Handlers run on the
# executor, possibly several at once for different connections, so this cannot be an attribute.
_current_device: contextvars.ContextVar[str | None] = contextvars.ContextVar("ble_current_device", default=None)


class Characteristic(dbus.service.Object):
    """Base GATT Characteristic class for BlueZ D-Bus."""

    def __init__(
        self,
        bus: dbus.Bus,
        index: int,
        uuid: str,
        flags: list[str],
        service,
        api_client: TsConfigApiClient,
        authenticator: BleAuthenticator | None = None,
        encrypted: bool | None = None,
    ):
        """Initialize a GATT Characteristic.

        Args:
            bus: D-Bus system bus
            index: Characteristic index
            uuid: Characteristic UUID
            flags: Characteristic flags (read, write, notify, etc.)
            service: Parent GATT service
            api_client: HTTP API client instance
            authenticator: If given, this characteristic is gated: it refuses to
                work until the connection has logged in as the operator
            encrypted: Require an encrypted LE link (BlueZ pairing) for this
                characteristic. Defaults to True for gated characteristics.
        """
        self.path = f"{service.path}/char{index:04d}"
        self.bus = bus
        self.uuid = uuid
        self.service = service
        if encrypted is None:
            encrypted = authenticator is not None
        self.flags = secure_flags(flags) if encrypted else flags
        self.api_client = api_client
        self.authenticator = authenticator
        self.notifying = False
        # ATT MTU per connection, as BlueZ reports it with each read/write
        self._mtus: dict[str, int] = {}
        # Counts a connection's disconnects: a response is only delivered if its request was
        # made in the current epoch, so nothing of a closed connection reaches a later one
        self._epoch: dict[str, int] = {}

        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> dict[str, Any]:
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

    def get_mtu(self, options: dict[str, Any]) -> int:
        """The negotiated ATT MTU of the connection a call came from.

        BlueZ passes it in the call's options (`mtu`); without it the BLE minimum is assumed.

        Args:
            options: Options dictionary from the D-Bus call

        Returns:
            Negotiated MTU or the default if BlueZ did not say
        """
        try:
            return max(int(options.get("mtu", DEFAULT_MTU)), DEFAULT_MTU)
        except (TypeError, ValueError):
            return DEFAULT_MTU

    def note_connection(self, options: dict[str, Any]) -> str | None:
        """Remember the MTU of the calling connection; returns its device path (None if unknown)."""
        device = options.get("device")
        device = str(device) if device else None
        if device:
            mtu = self.get_mtu(options)
            if self._mtus.get(device) != mtu:
                self._mtus[device] = mtu
                logger.debug(f"MTU of {device} is {mtu} for {self.uuid}")
        return device

    def chunker_for(self, device: str | None) -> BinaryChunker:
        """Chunker sized for a connection's MTU (the BLE minimum if it is not known).

        Chunks never exceed `ATT_MAX_VALUE_LEN`: BlueZ reports an ATT MTU of 517 for a
        connection but trims every notification to 512 bytes, which cut the end off
        each full chunk.
        """
        mtu = self._mtus.get(device or "", DEFAULT_MTU)
        return BinaryChunker(min(mtu, ATT_MAX_VALUE_LEN + ATT_HEADER_SIZE))

    def on_disconnect(self, device: str):
        """Drop per-connection state when a device disconnects."""
        self._mtus.pop(device, None)
        self._epoch[device] = self._epoch.get(device, 0) + 1

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

    def send_chunked_response(self, data: str, device: str | None = None):
        """Send a chunked response via notifications.

        Args:
            data: String data to send (will be chunked if necessary)
            device: Connection the response is for; its MTU sets the chunk size
        """
        if not self.notifying:
            logger.warning(f"Cannot send chunked response on {self.uuid}: not notifying")
            return

        # Convert string to bytes and chunk
        data_bytes = data.encode("utf-8")
        chunks = self.chunker_for(device).chunk_data(data_bytes)

        logger.debug(f"Sending {len(data_bytes)} bytes in {len(chunks)} chunks for {self.uuid}")

        # Send chunks sequentially (no delays - rely on BlueZ queuing)
        for i, chunk in enumerate(chunks):
            self.send_notification(chunk)
            logger.debug(f"Sent chunk {i + 1}/{len(chunks)} ({len(chunk)} bytes)")

    def is_authorized(self, options: dict[str, Any]) -> bool:
        """Whether the requesting connection may use this characteristic.

        Open characteristics (no authenticator) always allow; gated ones need
        the connection (BlueZ device path in `options`) to have logged in.
        """
        if self.authenticator is None:
            return True
        return self.authenticator.is_authenticated(options.get("device"))


class ReadOnlyCharacteristic(Characteristic):
    """Base class for read-only characteristics."""

    def __init__(
        self,
        bus,
        index,
        uuid,
        service,
        api_client,
        read_handler: Callable,
        authenticator: BleAuthenticator | None = None,
    ):
        """Initialize a read-only characteristic.

        Args:
            bus: D-Bus system bus
            index: Characteristic index
            uuid: Characteristic UUID
            service: Parent GATT service
            api_client: HTTP API client
            read_handler: Async function to call for reading data
            authenticator: Gate this characteristic behind the operator login
                (omit for the open discovery data of the system service)
        """
        super().__init__(bus, index, uuid, ["read", "notify"], service, api_client, authenticator)
        self.read_handler = read_handler

    def _error_metadata(self, message: str) -> "dbus.Array":
        return dbus.Array(
            formatter.metadata_cbor(content_length=0, chunk_count=0, status=STATUS_ERROR, error_message=message),
            signature="y",
        )

    @dbus.service.method(
        GATT_CHRC_IFACE,
        in_signature="a{sv}",
        out_signature="ay",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ReadValue(self, options, reply_cb, error_cb):
        """Handle read operation with notification-only protocol.

        All data is transferred via notifications. Read operations return only
        binary CBOR metadata about the data (length, chunks, content type, status).

        The API request runs on the executor and the reply is sent when it is done, so
        a slow API never blocks the gateway's main loop (and with it every other
        connection); a request that takes longer than BlueZ waits is answered with an
        error in the metadata instead.
        """
        if not self.is_authorized(options):
            reply_cb(self._error_metadata("Authentication required"))
            return

        if not self.notifying:
            logger.warning(f"Read attempt on {self.uuid} without notifications enabled")
            reply_cb(self._error_metadata("Notifications must be enabled to receive data"))
            return

        device = self.note_connection(options)
        epoch = self._epoch.get(device or "", 0)

        def done(data, error):
            try:
                if self._epoch.get(device or "", 0) != epoch:
                    logger.debug(f"Dropping read result for {device}: it disconnected meanwhile")
                    reply_cb(self._error_metadata("Connection closed"))
                    return
                if error is not None:
                    if isinstance(error, asyncio.TimeoutError):
                        logger.error(f"Reading {self.uuid} timed out after {READ_TIMEOUT_S} s")
                        reply_cb(self._error_metadata("Request timed out"))
                    else:
                        logger.error(f"Error reading {self.uuid}: {error}")
                        reply_cb(self._error_metadata(str(error)))
                    return

                response = formatter.success(data)
                response_bytes = response.encode("utf-8")
                chunk_count = self.chunker_for(device).get_chunk_count(response_bytes)
                content_type = CONTENT_TYPE_JSON if response.strip().startswith(("{", "[")) else CONTENT_TYPE_TEXT

                # The data follows as notifications AFTER this reply, so that the notification
                # does not overwrite the read response value
                logger.debug(
                    f"Preparing {len(response_bytes)} bytes in {chunk_count} notification chunks for {self.uuid}"
                )
                reply_cb(
                    dbus.Array(
                        formatter.metadata_cbor(
                            content_length=len(response_bytes),
                            chunk_count=chunk_count,
                            content_type=content_type,
                            status=STATUS_READY,
                        ),
                        signature="y",
                    )
                )
                ble_executor.get_executor().defer(lambda: self.send_chunked_response(response, device))
            except Exception as e:  # noqa: BLE001 - the caller is waiting for a reply
                logger.error(f"Error replying to read of {self.uuid}: {e}")
                reply_cb(self._error_metadata(str(e)))

        ble_executor.get_executor().submit(self.read_handler(), done, key=device or "", timeout=READ_TIMEOUT_S)


class WriteOnlyCharacteristic(Characteristic):
    """Base class for write-only characteristics."""

    def __init__(
        self,
        bus,
        index,
        uuid,
        service,
        api_client,
        write_handler: Callable,
        authenticator: BleAuthenticator | None = None,
        encrypted: bool | None = None,
    ):
        """Initialize a write-only characteristic.

        Args:
            bus: D-Bus system bus
            index: Characteristic index
            uuid: Characteristic UUID
            service: Parent GATT service
            api_client: HTTP API client
            write_handler: Async function to call for writing data
            authenticator: Gate writes behind the operator login (None = open)
            encrypted: Require an encrypted link (defaults to True when gated)
        """
        super().__init__(bus, index, uuid, ["write", "notify"], service, api_client, authenticator, encrypted)
        self.write_handler = write_handler
        # Partially received requests, per connection (BlueZ device path) so that one
        # client's aborted upload can never corrupt another client's request
        self.write_buffers: dict[str, bytearray] = {}
        self._last_write: dict[str, float] = {}

    def on_disconnect(self, device: str):
        """Discard the half-received request of a connection that ended."""
        super().on_disconnect(device)
        if self.write_buffers.pop(device, None):
            logger.warning(f"Discarded incomplete write on {self.uuid}: {device} disconnected")
        self._last_write.pop(device, None)

    def _buffer_for(self, device: str) -> bytearray:
        """The request buffer of a connection; a stale partial request is dropped first."""
        now = time.monotonic()
        buffer = self.write_buffers.get(device)
        if buffer and now - self._last_write.get(device, now) > WRITE_IDLE_TIMEOUT:
            logger.warning(f"Discarded {len(buffer)} bytes of stale incomplete write on {self.uuid} ({device})")
            buffer.clear()
        if buffer is None:
            buffer = self.write_buffers[device] = bytearray()
        self._last_write[device] = now
        return buffer

    @property
    def current_device(self) -> str | None:
        """The connection whose request the running handler is serving (None outside a handler)."""
        return _current_device.get()

    async def _run_handler(self, device: str | None, request_data: dict[str, Any]):
        token = _current_device.set(device)
        try:
            return await self.write_handler(request_data)
        finally:
            _current_device.reset(token)

    def _dispatch(self, request_data: dict[str, Any], device: str | None):
        """Run a complete request on the executor; the response follows as notifications."""
        epoch = self._epoch.get(device or "", 0)

        def done(result, error):
            if self._epoch.get(device or "", 0) != epoch:
                logger.warning(f"Dropping response on {self.uuid}: {device} disconnected meanwhile")
                return
            if error is None:
                response = formatter.success(result)
            elif isinstance(error, BleRequestError):
                response = formatter.error(str(error), error.code, **error.extra)
            else:
                logger.error(f"Error writing to {self.uuid}: {error}")
                response = formatter.error(str(error))
            self.send_chunked_response(response, device)

        ble_executor.get_executor().submit(self._run_handler(device, request_data), done, key=device or "")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        """Handle write operation with simplified protocol.

        For small writes: Direct JSON parsing
        For large writes: Sequential writes are accumulated until valid JSON is received

        A complete request is handed to the executor and this call returns right away;
        the response arrives as notifications, in the order the requests were made.
        """
        # Gated characteristics need the operator login for this connection
        if not self.is_authorized(options):
            self.send_chunked_response(formatter.auth_required(), self.note_connection(options))
            return

        device = self.note_connection(options)
        buffer = self._buffer_for(device or "")

        try:
            # Add incoming data to this connection's buffer
            data_bytes = bytes(value)
            buffer.extend(data_bytes)

            logger.debug(f"Received {len(data_bytes)} bytes for {self.uuid}, buffer now {len(buffer)} bytes")

            if len(buffer) > MAX_WRITE_BUFFER:
                raise BleRequestError("Request too large", 413)

            state, parsed_data = parser.classify_json(bytes(buffer))

            if state == "complete":
                # Successfully parsed - process the request
                logger.debug(f"Successfully parsed {len(buffer)} bytes of JSON for {self.uuid}")
                buffer.clear()
                self._dispatch(parsed_data, device)
            elif state == "incomplete":
                # Not yet complete JSON - wait for more data
                logger.debug(f"Incomplete JSON, waiting for more data (buffer: {len(buffer)} bytes)")
            else:
                # Can never become valid (garbage, or leftovers of an aborted request)
                logger.warning(f"Invalid JSON request on {self.uuid} ({len(buffer)} bytes), discarding")
                raise BleRequestError("Invalid JSON request", 400)

        except BleRequestError as e:
            buffer.clear()
            self.send_chunked_response(formatter.error(str(e), e.code, **e.extra), device)
        except Exception as e:
            logger.error(f"Error writing to {self.uuid}: {e}")
            # Clear buffer on error
            buffer.clear()
            self.send_chunked_response(formatter.error(str(e)), device)


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

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator | None = None):
        super().__init__(
            bus,
            index,
            "00002001-7473-4f53-636f-6e6669672121",
            service,
            api_client,
            api_client.get_systemd_services,
            authenticator,
        )


class SystemdServiceActionCharacteristic(WriteOnlyCharacteristic):
    """Systemd Service Action characteristic."""

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator | None = None):
        async def action_handler(data: dict[str, Any]):
            if not parser.validate_service_action(data):
                raise ValueError("Invalid service action request. Required: service, action")
            return await api_client.systemd_service_action(data["service"], data["action"])

        super().__init__(
            bus, index, "00002002-7473-4f53-636f-6e6669672121", service, api_client, action_handler, authenticator
        )


class SystemdRebootCharacteristic(WriteOnlyCharacteristic):
    """System Reboot characteristic."""

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator | None = None):
        async def reboot_handler(data: dict[str, Any]):
            return await api_client.systemd_reboot()

        super().__init__(
            bus, index, "00002003-7473-4f53-636f-6e6669672121", service, api_client, reboot_handler, authenticator
        )


class SystemdServiceLogsCharacteristic(WriteOnlyCharacteristic):
    """Systemd Service Logs characteristic."""

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator | None = None):
        async def logs_handler(data: dict[str, Any]):
            if not parser.validate_log_request(data):
                raise ValueError("Invalid log request. Required: service")
            lines = data.get("lines", 100)
            if isinstance(lines, bool) or not isinstance(lines, int):
                raise BleRequestError("lines must be an integer", 400)
            lines = max(1, min(lines, 1000))
            logs = await api_client.get_systemd_logs(data["service"], lines)
            return {"logs": logs}

        super().__init__(
            bus, index, "00002004-7473-4f53-636f-6e6669672121", service, api_client, logs_handler, authenticator
        )


class UploadConfigCharacteristic(WriteOnlyCharacteristic):
    """Config Upload characteristic (POST /api/configs/update)."""

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator | None = None):
        async def upload_handler(data: dict[str, Any]):
            if not parser.validate_upload_request(data):
                raise ValueError("Invalid upload request. Required: filename, content")
            mtime, force, encoding = data.get("mtime"), data.get("force", False), data.get("encoding")
            if not isinstance(data["filename"], str) or not isinstance(data["content"], str):
                raise BleRequestError("filename and content must be strings", 400)
            if (mtime is not None and not isinstance(mtime, str)) or not isinstance(force, bool):
                raise BleRequestError("mtime must be a string and force a boolean", 400)
            if encoding not in (None, "base64", "utf-8"):
                raise BleRequestError('encoding must be "base64" or "utf-8"', 400)
            return await api_client.upload_config(
                filename=data["filename"],
                content=data["content"],
                restart_service=data.get("restart_service", False),
                mtime=mtime,
                force=force,
                encoding=encoding,
            )

        super().__init__(
            bus, index, "00003001-7473-4f53-636f-6e6669672121", service, api_client, upload_handler, authenticator
        )


class UploadZipCharacteristic(WriteOnlyCharacteristic):
    """Zip Upload characteristic (POST /api/configs.zip)."""

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator | None = None):
        async def upload_zip_handler(data: dict[str, Any]):
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
            bus, index, "00003002-7473-4f53-636f-6e6669672121", service, api_client, upload_zip_handler, authenticator
        )


class AuthenticateCharacteristic(WriteOnlyCharacteristic):
    """Operator login: write {"password": "<password>"} for `pi`, or add
    "username": "root" for the staff login. Alternatively {"hash": "$6$..."}
    logs in as `pi` with pi's password hash (as in a config bundle's
    userconf.txt), which works while the password has not been changed since.

    The login itself is not gated (that would be circular) but it does require
    an encrypted link, so the password is never sent in clear over the air.
    The result is notified: {"authenticated": true} on success, or an error
    with code 401 (wrong password) / 429 (backing off, with retry_after).
    """

    def __init__(self, bus, index, service, api_client, authenticator: BleAuthenticator):
        def outcome(result):
            if not result.ok:
                extra = {"retry_after": int(result.retry_after) + 1} if result.retry_after else {}
                raise BleRequestError(result.message, result.code, **extra)
            return {"authenticated": True}

        async def login_handler(data: dict[str, Any]):
            if "hash" in data:
                token, username = data["hash"], data.get("username", OPERATOR_USER)
                if "password" in data:
                    raise BleRequestError("Send either password or hash, not both", 400)
                if username != OPERATOR_USER:
                    raise BleRequestError(f"Hash login is only available for {OPERATOR_USER}", 400)
                if not isinstance(token, str) or not token:
                    raise BleRequestError("hash must be a non-empty string", 400)
                return outcome(await authenticator.login_with_hash(self.current_device, token))

            password = data.get("password")
            username = data.get("username", OPERATOR_USER)
            if not isinstance(password, str) or not password or not isinstance(username, str):
                raise ValueError(
                    f"Invalid login request. Required: password (or hash, for {OPERATOR_USER}); "
                    f"optional: username ({OPERATOR_USER} or {STAFF_USER})"
                )
            return outcome(await authenticator.login(self.current_device, password, username))

        super().__init__(
            bus,
            index,
            "00001005-7473-4f53-636f-6e6669672121",
            service,
            api_client,
            login_handler,
            authenticator=None,
            encrypted=True,
        )
