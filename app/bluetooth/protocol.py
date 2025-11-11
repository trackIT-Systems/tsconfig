"""BLE GATT protocol definitions for tsOS Configuration Manager.

This module defines:
- Service and characteristic UUIDs
- Data chunking logic for BLE notifications
- Request/response format definitions
- Error response formats

UUID Scheme:
    Base UUID: 0000XXXX-7473-4f53-636f-6e6669672121
    - 7473 = "ts" in ASCII hex
    - 4f53 = "OS" in ASCII hex
    - 636f6e666967 = "config" in ASCII hex
    - 2121 = "!!" for flair

    Services use 0xS000 where S = service number (1, 2, 3...)
    Characteristics use 0xSCCC where S = service, CCC = characteristic number
"""

import json
from typing import Any, Dict, List, Optional
import cbor2

# Base UUID pattern
BASE_UUID = "0000{:04x}-7473-4f53-636f-6e6669672121"

# Service UUIDs
SYSTEM_SERVICE_UUID = BASE_UUID.format(0x1000)
SYSTEMD_SERVICE_UUID = BASE_UUID.format(0x2000)
UPLOAD_SERVICE_UUID = BASE_UUID.format(0x3000)

# System Service Characteristics (0x1000)
SYSTEM_STATUS_UUID = BASE_UUID.format(0x1001)
SERVER_MODE_INFO_UUID = BASE_UUID.format(0x1002)
TIMEDATECTL_STATUS_UUID = BASE_UUID.format(0x1003)
AVAILABLE_SERVICES_UUID = BASE_UUID.format(0x1004)

# Systemd Service Characteristics (0x2000)
SYSTEMD_SERVICES_LIST_UUID = BASE_UUID.format(0x2001)
SYSTEMD_SERVICE_ACTION_UUID = BASE_UUID.format(0x2002)
SYSTEMD_REBOOT_UUID = BASE_UUID.format(0x2003)
SYSTEMD_SERVICE_LOGS_UUID = BASE_UUID.format(0x2004)

# Upload Service Characteristics (0x3000)
UPLOAD_CONFIG_UUID = BASE_UUID.format(0x3001)
UPLOAD_ZIP_UUID = BASE_UUID.format(0x3002)

# BLE GATT MTU constraints
DEFAULT_MTU = 23  # BLE 4.0 minimum MTU
ATT_HEADER_SIZE = 3  # ATT protocol header overhead


class BinaryChunker:
    """Handle MTU-aware chunking of data for BLE notifications.
    
    This chunker splits raw bytes into MTU-sized chunks without any
    framing overhead, relying on BLE's ordered delivery guarantee.
    """

    def __init__(self, mtu: int = DEFAULT_MTU):
        """Initialize the chunker with negotiated MTU.

        Args:
            mtu: Negotiated MTU size (defaults to BLE 4.0 minimum of 23)
        """
        self.mtu = mtu
        # Usable payload size: MTU - ATT header
        self.chunk_size = max(mtu - ATT_HEADER_SIZE, 20)  # Minimum 20 bytes

    def chunk_data(self, data: bytes) -> List[bytes]:
        """Split data into MTU-sized chunks for BLE transmission.

        Args:
            data: Raw bytes to chunk

        Returns:
            List of byte chunks ready for BLE transmission
        """
        if len(data) <= self.chunk_size:
            # Small enough to send in one chunk
            return [data]

        chunks = []
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + self.chunk_size]
            chunks.append(chunk)
            offset += self.chunk_size

        return chunks

    def get_chunk_count(self, data: bytes) -> int:
        """Calculate number of chunks needed for data.

        Args:
            data: Raw bytes to be chunked

        Returns:
            Number of chunks required
        """
        if len(data) <= self.chunk_size:
            return 1
        return (len(data) + self.chunk_size - 1) // self.chunk_size


# Content type enumeration for CBOR metadata
CONTENT_TYPE_JSON = 0
CONTENT_TYPE_TEXT = 1
CONTENT_TYPE_BINARY = 2

# Status enumeration for CBOR metadata
STATUS_READY = 0
STATUS_ERROR = 1


class ResponseFormatter:
    """Format API responses for BLE transmission."""

    @staticmethod
    def success(data: Any) -> str:
        """Format a successful API response.

        Args:
            data: Response data (will be JSON serialized)

        Returns:
            JSON string ready for transmission
        """
        if isinstance(data, str):
            return data
        return json.dumps(data)

    @staticmethod
    def error(message: str, code: int = 500) -> str:
        """Format an error response.

        Args:
            message: Error message
            code: HTTP status code

        Returns:
            JSON string with error information
        """
        return json.dumps({"error": message, "code": code})

    @staticmethod
    def pairing_required() -> str:
        """Format a pairing required error response.

        Returns:
            JSON string indicating pairing is required
        """
        return json.dumps({"error": "Pairing required", "code": 403})

    @staticmethod
    def metadata_cbor(
        content_length: int,
        chunk_count: int,
        content_type: int = CONTENT_TYPE_JSON,
        status: int = STATUS_READY,
        error_message: Optional[str] = None
    ) -> bytes:
        """Format a metadata response using CBOR for read operations.

        In the notification-only protocol, read operations return compact binary
        metadata about the data that will be sent via notifications.

        Args:
            content_length: Size of the actual data in bytes
            chunk_count: Number of notification chunks that will be sent
            content_type: Content type enum (0=json, 1=text, 2=binary)
            status: Status enum (0=ready, 1=error)
            error_message: Optional error message if status is ERROR

        Returns:
            CBOR-encoded binary metadata (~8-12 bytes for success, more with error)
        """
        metadata = {
            1: content_length,  # Using integer keys for compactness
            2: chunk_count,
            3: content_type,
            4: status,
        }
        
        # Add error message if present
        if error_message:
            metadata[5] = error_message
        
        return cbor2.dumps(metadata)


class RequestParser:
    """Parse incoming BLE write requests."""

    @staticmethod
    def parse_json(data: bytes) -> Optional[Dict[str, Any]]:
        """Parse JSON data from a BLE write operation.

        Args:
            data: Raw bytes from BLE write

        Returns:
            Parsed dictionary or None if parsing fails
        """
        try:
            json_str = data.decode("utf-8")
            return json.loads(json_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    @staticmethod
    def validate_service_action(data: Dict[str, Any]) -> bool:
        """Validate a systemd service action request.

        Args:
            data: Parsed request data

        Returns:
            True if valid, False otherwise
        """
        required_fields = ["service", "action"]
        return all(field in data for field in required_fields)

    @staticmethod
    def validate_log_request(data: Dict[str, Any]) -> bool:
        """Validate a log request.

        Args:
            data: Parsed request data

        Returns:
            True if valid, False otherwise
        """
        return "service" in data

    @staticmethod
    def validate_upload_request(data: Dict[str, Any]) -> bool:
        """Validate a file upload request.

        Args:
            data: Parsed request data

        Returns:
            True if valid, False otherwise
        """
        required_fields = ["filename", "content"]
        return all(field in data for field in required_fields)


# Convenience instances
# Note: chunker should be instantiated per-characteristic with negotiated MTU
formatter = ResponseFormatter()
parser = RequestParser()
