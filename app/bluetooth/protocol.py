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
MAX_CHARACTERISTIC_LENGTH = 512  # Maximum size for a single characteristic value


class DataChunker:
    """Handle chunking of large data for BLE notifications."""

    def __init__(self, max_chunk_size: int = MAX_CHARACTERISTIC_LENGTH):
        """Initialize the chunker with a maximum chunk size.

        The chunk size accounts for JSON overhead:
        - JSON structure: {"seq": X, "total": Y, "data": "...", "complete": false}
        - Overhead is ~60 bytes, so we use max_chunk_size - 200 to be safe
        """
        self.max_chunk_size = max_chunk_size - 200  # Reserve space for JSON encoding overhead

    def chunk_data(self, data: str) -> List[Dict[str, Any]]:
        """Split data into chunks for BLE transmission.

        Args:
            data: String data to chunk

        Returns:
            List of chunk dictionaries with seq, total, data, and complete fields
        """
        if len(data) <= self.max_chunk_size:
            # Small enough to send in one chunk
            return [{"seq": 0, "total": 1, "data": data, "complete": True}]

        chunks = []
        total_chunks = (len(data) + self.max_chunk_size - 1) // self.max_chunk_size

        for i in range(total_chunks):
            start = i * self.max_chunk_size
            end = min((i + 1) * self.max_chunk_size, len(data))
            chunk_data = data[start:end]

            chunk = {
                "seq": i,
                "total": total_chunks,
                "data": chunk_data,
                "complete": (i == total_chunks - 1),
            }
            chunks.append(chunk)

        return chunks

    def encode_chunk(self, chunk: Dict[str, Any]) -> bytes:
        """Encode a chunk dictionary to bytes for BLE transmission.

        Args:
            chunk: Chunk dictionary with seq, total, data, complete

        Returns:
            UTF-8 encoded JSON bytes
        """
        return json.dumps(chunk).encode("utf-8")

    def chunk_and_encode(self, data: str) -> List[bytes]:
        """Convenience method to chunk and encode data in one step.

        Args:
            data: String data to chunk and encode

        Returns:
            List of encoded chunks ready for BLE transmission
        """
        chunks = self.chunk_data(data)
        return [self.encode_chunk(chunk) for chunk in chunks]


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
    def metadata(
        content_length: int, chunks_expected: int, content_type: str = "application/json", status: str = "ready"
    ) -> str:
        """Format a metadata response for read operations.

        In the notification-only protocol, read operations return metadata about
        the data that will be sent via notifications.

        Args:
            content_length: Size of the actual data in bytes
            chunks_expected: Number of notification chunks that will be sent
            content_type: MIME type of the content (default: application/json)
            status: Status of the data (ready, pending, error)

        Returns:
            JSON string with metadata information
        """
        return json.dumps({
            "metadata": True,
            "content_length": content_length,
            "chunks_expected": chunks_expected,
            "content_type": content_type,
            "status": status,
            "hint": "Data will be delivered via notifications",
        })


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
    def is_chunk(data: Dict[str, Any]) -> bool:
        """Check if parsed data is a chunk in the chunked write protocol.

        Args:
            data: Parsed dictionary

        Returns:
            True if data is a chunk (has seq, total, data fields)
        """
        required_fields = ["seq", "total", "data"]
        return all(field in data for field in required_fields)

    @staticmethod
    def parse_chunked_write(data: bytes) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Parse data and determine if it's a chunk or direct JSON.

        Args:
            data: Raw bytes from BLE write

        Returns:
            Tuple of (is_chunk, parsed_data)
            - is_chunk: True if this is a chunk, False if direct JSON
            - parsed_data: The parsed dictionary or None if parsing fails
        """
        parsed = RequestParser.parse_json(data)
        if parsed is None:
            return False, None

        is_chunk = RequestParser.is_chunk(parsed)
        return is_chunk, parsed

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
chunker = DataChunker()
formatter = ResponseFormatter()
parser = RequestParser()
