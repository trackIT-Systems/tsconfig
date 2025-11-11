#!/usr/bin/env python3
"""BLE GATT Client Test Script for tsOS Configuration Manager.

This script connects to the BLE GATT gateway running on a Raspberry Pi
and tests various operations. Works on macOS, Linux, and Windows.

Requirements:
    pip install bleak cbor2

Usage:
    python3 test_ble_client.py [--device DEVICE_NAME] [--write] [--timeout SECONDS] [--retries N]

Options:
    --device, -d     Device name or address (auto-detects if not specified)
    --write, -w      Enable write operations (service restart, file upload)
    --timeout, -t    Scan timeout in seconds per attempt (default: 10.0)
    --retries, -r    Number of retry attempts for device discovery (default: 3)

Note:
    Device discovery can sometimes fail due to BLE advertising timing.
    The script now includes automatic retries to improve reliability.
"""

import argparse
import asyncio
import base64
import io
import json
import sys
import zipfile
from typing import Optional

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    import cbor2
except ImportError as e:
    # Only fail if not just showing help
    if "--help" not in sys.argv and "-h" not in sys.argv:
        print(f"Error: Required library not found! {e}")
        print("Install with: pip install bleak cbor2")
        sys.exit(1)
    # Dummy types for help display
    BleakClient = None
    BleakScanner = None
    BLEDevice = None
    cbor2 = None

# GATT Service UUIDs
SYSTEM_SERVICE_UUID = "00001000-7473-4f53-636f-6e6669672121"
SYSTEMD_SERVICE_UUID = "00002000-7473-4f53-636f-6e6669672121"
UPLOAD_SERVICE_UUID = "00003000-7473-4f53-636f-6e6669672121"

# System Service Characteristics
SYSTEM_STATUS_UUID = "00001001-7473-4f53-636f-6e6669672121"
SERVER_MODE_INFO_UUID = "00001002-7473-4f53-636f-6e6669672121"
TIMEDATECTL_STATUS_UUID = "00001003-7473-4f53-636f-6e6669672121"
AVAILABLE_SERVICES_UUID = "00001004-7473-4f53-636f-6e6669672121"

# Systemd Service Characteristics
SYSTEMD_SERVICES_LIST_UUID = "00002001-7473-4f53-636f-6e6669672121"
SYSTEMD_SERVICE_ACTION_UUID = "00002002-7473-4f53-636f-6e6669672121"
SYSTEMD_REBOOT_UUID = "00002003-7473-4f53-636f-6e6669672121"
SYSTEMD_SERVICE_LOGS_UUID = "00002004-7473-4f53-636f-6e6669672121"

# Upload Service Characteristics
UPLOAD_CONFIG_UUID = "00003001-7473-4f53-636f-6e6669672121"
UPLOAD_ZIP_UUID = "00003002-7473-4f53-636f-6e6669672121"


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def print_header(text: str):
    """Print a header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.ENDC}\n")


def print_success(text: str):
    """Print success message."""
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    """Print error message."""
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_info(text: str):
    """Print info message."""
    print(f"{Colors.OKCYAN}→ {text}{Colors.ENDC}")


def print_warning(text: str):
    """Print warning message."""
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")


def format_json(data: str) -> str:
    """Pretty print JSON data."""
    try:
        obj = json.loads(data)
        return json.dumps(obj, indent=2)
    except json.JSONDecodeError:
        return data


async def write_large_data(client: BleakClient, char, data: str, mtu: int = None):
    """Write large data to a characteristic using MTU-aware chunking.

    Args:
        client: Connected BleakClient
        char: Characteristic to write to
        data: JSON string to write (will be chunked if needed)
        mtu: MTU size (uses client.mtu_size if not provided)

    Returns:
        True if successful, False otherwise
    """
    # Get MTU from client if not provided
    if mtu is None:
        mtu = client.mtu_size
    
    # Calculate chunk size (MTU - 3 bytes for ATT header)
    chunk_size = max(mtu - 3, 20)
    
    data_bytes = data.encode("utf-8")
    total_size = len(data_bytes)

    if total_size <= chunk_size:
        # Small enough for single write
        print_info(f"Writing {total_size} bytes in single operation...")
        try:
            await client.write_gatt_char(char, data_bytes)
            return True
        except Exception as e:
            print_error(f"Write failed: {e}")
            return False

    # Large data - split into MTU-sized chunks
    num_chunks = (total_size + chunk_size - 1) // chunk_size
    print_info(f"Writing {total_size} bytes in {num_chunks} chunks (MTU: {mtu}, chunk size: {chunk_size})...")

    try:
        offset = 0
        chunk_num = 0
        while offset < total_size:
            chunk = data_bytes[offset:offset + chunk_size]
            chunk_num += 1
            
            print_info(f"  Writing chunk {chunk_num}/{num_chunks} ({len(chunk)} bytes)...")
            await client.write_gatt_char(char, chunk)
            
            offset += chunk_size

        print_success(f"All {num_chunks} chunks written successfully")
        return True

    except Exception as e:
        print_error(f"Chunked write failed on chunk {chunk_num}: {e}")
        return False


class ChunkedReader:
    """Handle chunked BLE read responses via notifications with raw byte accumulation."""

    def __init__(self):
        self.buffer = bytearray()
        self.complete_data = None
        self.expected_length = 0
        self.expected_chunks = 0

    def set_metadata(self, content_length: int, chunk_count: int):
        """Set expected metadata from CBOR read response.
        
        Args:
            content_length: Total bytes expected
            chunk_count: Number of notification chunks expected
        """
        self.expected_length = content_length
        self.expected_chunks = chunk_count
        self.buffer.clear()
        self.complete_data = None

    def handle_notification(self, sender, data: bytearray):
        """Handle incoming notification chunks - raw byte accumulation."""
        try:
            # Accumulate raw bytes
            self.buffer.extend(data)
            
            # Check if we've received all expected data
            if self.expected_length > 0 and len(self.buffer) >= self.expected_length:
                # Decode UTF-8 JSON (read responses with metadata)
                self.complete_data = self.buffer[:self.expected_length].decode("utf-8")
            elif self.expected_length == 0:
                # Write responses without metadata - try to parse as complete JSON
                try:
                    decoded = self.buffer.decode("utf-8")
                    # Try to parse - if successful, we have complete JSON
                    json.loads(decoded)
                    self.complete_data = decoded
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Not complete yet, keep accumulating
                    pass
        except Exception as e:
            print_error(f"Error handling notification: {e}")



def get_device_uuids(device) -> list:
    """Extract UUIDs from a BLE device (cross-platform).

    Args:
        device: BLE device

    Returns:
        List of UUID strings
    """
    try:
        if hasattr(device, "metadata") and device.metadata:
            return device.metadata.get("uuids", [])
        elif hasattr(device, "details") and hasattr(device.details, "get"):
            return device.details.get("uuids", [])
    except (AttributeError, KeyError, TypeError):
        pass
    return []


def is_tsconfig_device(device) -> bool:
    """Check if a device is a tsconfig device.

    Args:
        device: BLE device to check

    Returns:
        True if tsconfig device, False otherwise
    """
    # Check by service UUID first (most reliable)
    uuids = get_device_uuids(device)
    if any(SYSTEM_SERVICE_UUID.lower() in str(u).lower() for u in uuids):
        return True

    # Fallback: check by device name pattern
    if device.name:
        name_lower = device.name.lower()
        return any(pattern in name_lower for pattern in ["tsos", "tsconfig", "trackit"])

    return False


async def _scan_tsconfig_single(timeout: float, attempt: int, retries: int) -> list:
    """Single attempt to scan for tsconfig devices."""
    print_info(f"Scanning for tsconfig devices (attempt {attempt}/{retries}, {timeout}s)")
    devices = await BleakScanner.discover(timeout=timeout)
    print_info(f"Found {len(devices)} total BLE devices")

    tsconfig_devices = [d for d in devices if is_tsconfig_device(d)]

    if tsconfig_devices:
        print_success(f"Found {len(tsconfig_devices)} tsconfig device(s)")
    else:
        print_warning("No tsconfig devices found")

    return tsconfig_devices


async def _find_device_single(name: str, timeout: float, attempt: int, retries: int) -> Optional[BLEDevice]:
    """Single attempt to find a device by name."""
    print_info(f"Searching for '{name}' (attempt {attempt}/{retries}, {timeout}s)")
    device = await BleakScanner.find_device_by_name(name, timeout=timeout)

    if device:
        print_success("Found device")
    else:
        print_warning("Device not found")

    return device


async def scan_for_tsconfig_devices(timeout: float = 10.0, retries: int = 3) -> list:
    """Scan for tsconfig BLE devices by service UUID or name pattern."""
    return await retry_scan(_scan_tsconfig_single, timeout, retries=retries, delay=1.0)


async def find_device_by_name(name: str, timeout: float = 10.0, retries: int = 3) -> Optional[BLEDevice]:
    """Find a BLE device by name with retry logic."""
    return await retry_scan(_find_device_single, name, timeout, retries=retries, delay=1.0, return_single=True)


def find_characteristic(client: BleakClient, uuid: str):
    """Find a characteristic by UUID.

    Args:
        client: Connected BleakClient
        uuid: Characteristic UUID to find

    Returns:
        Characteristic if found, None otherwise
    """
    for service in client.services:
        for char in service.characteristics:
            if char.uuid.lower() == uuid.lower():
                return char
    return None


async def read_with_notifications(client: BleakClient, uuid: str, name: str, timeout: float = 5.0) -> Optional[str]:
    """Read a characteristic using notifications with automatic cleanup.

    Args:
        client: Connected BleakClient
        uuid: Characteristic UUID
        name: Human-readable name for display
        timeout: Timeout in seconds for waiting for data

    Returns:
        String value if successful, None otherwise
    """
    char = find_characteristic(client, uuid)
    if not char:
        print_error(f"Characteristic {uuid} not found")
        return None

    print_info(f"Reading {name}...")
    reader = ChunkedReader()

    try:
        # Enable notifications and read metadata
        await client.start_notify(char, reader.handle_notification)
        await asyncio.sleep(0.5)

        data = await client.read_gatt_char(char)
        
        # Parse CBOR metadata
        try:
            metadata = cbor2.loads(bytes(data))
            # Metadata uses integer keys: 1=content_length, 2=chunk_count, 3=content_type, 4=status, 5=error_message
            content_length = metadata.get(1, 0)
            chunk_count = metadata.get(2, 0)
            content_type = metadata.get(3, 0)  # 0=json, 1=text, 2=binary
            status = metadata.get(4, 0)  # 0=ready, 1=error
            error_message = metadata.get(5, None)
            
            # Handle error response
            if status == 1:  # STATUS_ERROR
                print_error(f"  {error_message or 'Unknown error'}")
                return None

            # Display metadata
            content_type_str = {0: "JSON", 1: "text", 2: "binary"}.get(content_type, "unknown")
            info = f"  {content_length} bytes, {chunk_count} chunks, type: {content_type_str}"
            print_success(info)
            print_info("  Waiting for notification data...")

            # Set metadata in reader
            reader.set_metadata(content_length, chunk_count)

            # Wait for chunks
            for _ in range(int(timeout * 10)):
                await asyncio.sleep(0.1)
                if reader.complete_data:
                    break

            if reader.complete_data:
                print_success(f"{name} received ({len(reader.complete_data)} bytes)")
                return reader.complete_data
            else:
                print_error(f"Timeout waiting for {name} data (received {len(reader.buffer)}/{content_length} bytes)")
                return None

        except Exception as e:
            print_error(f"Invalid CBOR metadata: {e}")
            return None

    except Exception as e:
        print_error(f"Failed to read {name}: {e}")
        return None
    finally:
        try:
            await client.stop_notify(char)
        except:
            pass


async def write_with_notifications(
    client: BleakClient, uuid: str, name: str, data: dict, timeout: float = 2.0
) -> Optional[str]:
    """Write data to a characteristic and wait for response via notifications.

    Args:
        client: Connected BleakClient
        uuid: Characteristic UUID
        name: Human-readable name for display
        data: Dictionary to write (will be JSON encoded)
        timeout: Timeout in seconds for waiting for response

    Returns:
        Response data if successful, None otherwise
    """
    char = find_characteristic(client, uuid)
    if not char:
        print_error(f"Characteristic {uuid} not found")
        return None

    print_info(f"Writing to {name}...")
    reader = ChunkedReader()

    try:
        # Enable notifications
        await client.start_notify(char, reader.handle_notification)
        await asyncio.sleep(0.5)

        # Write data (with chunking if needed)
        json_data = json.dumps(data)
        success = await write_large_data(client, char, json_data)

        if not success:
            return None

        print_success(f"{name} sent successfully")
        print_info("Waiting for response...")
        
        # Wait for response notifications
        for _ in range(int(timeout * 10)):
            await asyncio.sleep(0.1)
            if reader.complete_data:
                break

        if reader.complete_data:
            print_success("Response received:")
            return reader.complete_data
        else:
            print_info("No response received")
            return None

    except Exception as e:
        print_error(f"Failed to write {name}: {e}")
        return None
    finally:
        try:
            await client.stop_notify(char)
        except:
            pass


async def retry_scan(scan_func, *args, retries: int = 3, delay: float = 1.0, return_single: bool = False, **kwargs):
    """Generic retry wrapper for scan operations.

    Args:
        scan_func: Async function to retry
        *args: Positional arguments for scan_func
        retries: Number of retry attempts
        delay: Delay between retries in seconds
        return_single: If True, return None on failure; if False, return empty list
        **kwargs: Keyword arguments for scan_func

    Returns:
        Result from scan_func, or None/empty list if all retries fail
    """
    for attempt in range(1, retries + 1):
        if attempt > 1:
            print_warning(f"Retry attempt {attempt}/{retries}...")
            await asyncio.sleep(delay)

        result = await scan_func(*args, attempt=attempt, retries=retries, **kwargs)
        if result:
            return result

    return None if return_single else []


async def test_system_service(client: BleakClient):
    """Test System Service characteristics."""
    print_header("Testing System Service (0x1000)")

    # Define characteristics to test
    chars = [
        (SYSTEM_STATUS_UUID, "System Status"),
        (SERVER_MODE_INFO_UUID, "Server Mode Info"),
        (TIMEDATECTL_STATUS_UUID, "Timedatectl Status"),
        (AVAILABLE_SERVICES_UUID, "Available Services"),
    ]

    # Read and display each characteristic
    for uuid, name in chars:
        data = await read_with_notifications(client, uuid, name)
        if data:
            print(f"\n{name}:")
            print(format_json(data))


async def test_systemd_service(client: BleakClient):
    """Test Systemd Service characteristics."""
    print_header("Testing Systemd Service (0x2000)")

    # Read Services List
    data = await read_with_notifications(client, SYSTEMD_SERVICES_LIST_UUID, "Systemd Services List")
    if data:
        print("\nSystemd Services:")
        try:
            services = json.loads(data)
            if isinstance(services, list):
                # Show first 5 services
                for service in services[:5]:
                    name = service.get("name", "unknown")
                    state = service.get("state", "unknown")
                    print(f"  - {name}: {state}")
                if len(services) > 5:
                    print(f"  ... and {len(services) - 5} more services")
            else:
                print(format_json(data))
        except:
            print(format_json(data))

    # Show available write characteristics
    write_chars = [
        (SYSTEMD_SERVICE_ACTION_UUID, "Service Action", '{"service": "servicename", "action": "restart"}'),
        (SYSTEMD_REBOOT_UUID, "System Reboot", "{}"),
        (SYSTEMD_SERVICE_LOGS_UUID, "Service Logs", '{"service": "servicename", "lines": 50}'),
    ]

    for uuid, name, example in write_chars:
        print_info(f"\n{name} characteristic: {uuid}")
        print_info(f"Example: {example}")


async def test_chunked_reads(client: BleakClient):
    """Test notification-only read protocol with chunked data."""
    print_header("Testing Notification-Only Protocol")
    print_info("Demonstrates: metadata read → data via notifications → chunk reassembly")

    data = await read_with_notifications(client, SYSTEMD_SERVICES_LIST_UUID, "Systemd Services List")

    if data:
        print_success(f"Protocol test successful - {len(data)} bytes received")
        try:
            services = json.loads(data)
            if isinstance(services, list):
                print_info(f"Services list contains {len(services)} items")
                print("\nFirst 3 services:")
                for service in services[:3]:
                    print(f"    - {service.get('name', 'unknown')}: {service.get('state', 'unknown')}")
            else:
                print(f"\nData preview: {data[:200]}{'...' if len(data) > 200 else ''}")
        except json.JSONDecodeError:
            print(f"\nData preview: {data[:200]}{'...' if len(data) > 200 else ''}")
    else:
        print_error("Protocol test failed")


async def test_write_operations(client: BleakClient, enabled: bool = False):
    """Test write operations - restart chrony service."""
    print_header("Testing Write Operations")

    if not enabled:
        print_warning("Write operations disabled (use --write to enable)")
        print_info("Would restart chrony service")
        return

    action_data = {"service": "chrony", "action": "restart"}
    response = await write_with_notifications(client, SYSTEMD_SERVICE_ACTION_UUID, "Service Action", action_data)

    if response:
        print(format_json(response))


async def upload_file(filepath: str) -> Optional[bytes]:
    """Load and encode a file for upload.

    Args:
        filepath: Path to file to load

    Returns:
        Base64-encoded bytes, or None if error
    """
    try:
        with open(filepath, "r") as f:
            content = f.read()
        print_success(f"Loaded {filepath} ({len(content)} bytes)")
        return base64.b64encode(content.encode("utf-8")).decode("ascii")
    except FileNotFoundError:
        print_error(f"File {filepath} not found")
        return None
    except Exception as e:
        print_error(f"Failed to read {filepath}: {e}")
        return None


async def test_upload_service(client: BleakClient, enabled: bool = False):
    """Test Upload Service - upload a schedule.yml file."""
    print_header("Testing Upload Service (0x3000)")

    if not enabled:
        print_warning("Upload operations disabled (use --write to enable)")
        print_info("Would upload configs/schedule.yml")
        return

    # Load and encode file
    content_b64 = await upload_file("configs/schedule.yml")
    if not content_b64:
        return

    # Prepare upload request
    upload_data = {"filename": "schedule.yml", "content": content_b64, "restart_service": False}

    # Send upload
    response = await write_with_notifications(client, UPLOAD_CONFIG_UUID, "Config Upload", upload_data)

    if response:
        print(format_json(response))


async def create_zip_upload(file_paths: list) -> Optional[str]:
    """Create a zip file from multiple files and return base64-encoded content.

    Args:
        file_paths: List of file paths to include in zip

    Returns:
        Base64-encoded zip data, or None if error
    """
    zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in file_paths:
                try:
                    with open(file_path, "r") as f:
                        content = f.read()
                    arcname = file_path.split("/")[-1]
                    zip_file.writestr(arcname, content)
                    print_success(f"Added {arcname} ({len(content)} bytes)")
                except FileNotFoundError:
                    print_warning(f"File {file_path} not found, skipping")
                except Exception as e:
                    print_error(f"Failed to read {file_path}: {e}")

        zip_data = zip_buffer.getvalue()
        print_success(f"Created zip file ({len(zip_data)} bytes)")
        return base64.b64encode(zip_data).decode("ascii")

    except Exception as e:
        print_error(f"Failed to create zip file: {e}")
        return None


async def test_zip_upload(client: BleakClient, enabled: bool = False):
    """Test Zip Upload - upload multiple config files as a zip."""
    print_header("Testing Zip Upload")

    if not enabled:
        print_warning("Upload operations disabled (use --write to enable)")
        print_info("Would upload: radiotracking.ini, soundscapepipe.yml, schedule.yml")
        return

    # Create zip
    config_files = ["configs/radiotracking.ini", "configs/soundscapepipe.yml", "configs/schedule.yml"]
    content_b64 = await create_zip_upload(config_files)
    if not content_b64:
        return

    # Prepare upload request
    upload_data = {"filename": "configs.zip", "content": content_b64, "restart_services": False, "pedantic": False}

    # Send upload
    response = await write_with_notifications(client, UPLOAD_ZIP_UUID, "Zip Upload", upload_data)

    if response:
        print(format_json(response))


async def discover_services(client: BleakClient):
    """Discover and display all services and characteristics.

    Args:
        client: Connected BleakClient
    """
    print_header("Discovering Services and Characteristics")

    for service in client.services:
        print(f"\n{Colors.BOLD}Service: {service.uuid}{Colors.ENDC}")
        print(f"  Handle: {service.handle}")

        for char in service.characteristics:
            props = ", ".join(char.properties)
            print(f"\n  {Colors.OKCYAN}Characteristic: {char.uuid}{Colors.ENDC}")
            print(f"    Handle: {char.handle}")
            print(f"    Properties: {props}")

            for descriptor in char.descriptors:
                print(f"      Descriptor: {descriptor.uuid}")


async def main():
    """Main test function."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="BLE GATT Client Test for tsOS Configuration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--device",
        "-d",
        type=str,
        help="Device name or address (auto-detects if not specified)",
    )
    parser.add_argument(
        "--write",
        "-w",
        action="store_true",
        help="Enable write operations (service restart, file upload)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=float,
        default=10.0,
        help="Scan timeout in seconds per attempt (default: 10.0)",
    )
    parser.add_argument(
        "--retries",
        "-r",
        type=int,
        default=3,
        help="Number of retry attempts for device discovery (default: 3)",
    )

    args = parser.parse_args()

    print_header("tsOS Configuration Manager - BLE GATT Client Test")

    if args.write:
        print_warning("Write operations ENABLED")
    else:
        print_info("Write operations disabled (use --write to enable)")

    # Find device
    if args.device:
        print_info(f"Looking for device: {args.device}")
        device = await find_device_by_name(args.device, timeout=args.timeout, retries=args.retries)
        device_name = args.device
    else:
        # Scan for tsconfig devices
        devices = await scan_for_tsconfig_devices(timeout=args.timeout, retries=args.retries)

        if not devices:
            print_error("No tsconfig devices found")
            print_info("Try: --device DEVICE_NAME")
            return

        device = devices[0]
        device_name = device.name or device.address

        if len(devices) == 1:
            print_success(f"Auto-selected: {device_name}")
        else:
            print_warning(f"Found {len(devices)} devices, using: {device_name}")
            for i, d in enumerate(devices):
                print(f"  {'→' if i == 0 else ' '} {d.name or 'Unknown'} ({d.address})")

    if not device:
        print_error(f"Device not found after {args.retries} attempts")
        troubleshooting = [
            "1. Verify BLE gateway: sudo systemctl status tsconfig-ble",
            "2. Check advertising: sudo bluetoothctl scan on",
            "3. Restart service: sudo systemctl restart tsconfig-ble",
            "4. Try: --timeout 15 --retries 5",
            "5. Check logs: sudo journalctl -u tsconfig-ble -n 50",
        ]
        print_info("\nTroubleshooting:")
        for step in troubleshooting:
            print_info(step)
        return

    print_success(f"Found device: {device.name} ({device.address})")

    # Connect and run tests
    print_info("Connecting to device...")
    try:
        async with BleakClient(device) as client:
            print_success(f"Connected to {device.name}")
            print_info(f"MTU Size: {client.mtu_size}")

            # Run all tests
            await discover_services(client)
            await test_system_service(client)
            await test_systemd_service(client)
            await test_chunked_reads(client)
            await test_write_operations(client, enabled=args.write)
            await test_upload_service(client, enabled=args.write)
            await test_zip_upload(client, enabled=args.write)

            print_header("Test Complete")
            print_success("All tests completed!")
            if not args.write:
                print_info("Use --write flag to test write operations")

    except Exception as e:
        print_error(f"Connection failed: {e}")
        troubleshooting = [
            "1. Verify BLE gateway: sudo systemctl status tsconfig-ble",
            "2. Check Bluetooth enabled on both devices",
            "3. Try pairing first if write operations needed",
            "4. Check logs: sudo journalctl -u tsconfig-ble -f",
        ]
        print_info("\nTroubleshooting:")
        for step in troubleshooting:
            print_info(step)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
