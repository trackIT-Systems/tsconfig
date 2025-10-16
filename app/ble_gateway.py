"""BLE GATT Gateway for tsOS Configuration Manager.

This is the main entry point for the standalone BLE gateway that exposes
the tsconfig RESTful API via Bluetooth GATT.

Usage:
    python3 -m app.ble_gateway [options]

Options:
    --api-url URL           Base URL of tsconfig API (default: http://localhost:8000)
    --no-pairing            Disable pairing requirement for write operations
    --no-discoverable       Start in non-discoverable mode
    --verbose, -v           Enable debug logging

The BLE device name will automatically use the system hostname.
"""

import argparse
import logging
import sys

from app.bluetooth.gatt_server import BleGattServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def parse_arguments():
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="BLE GATT Gateway for tsOS Configuration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with defaults (localhost:8000, require pairing, discoverable)
  python3 -m app.ble_gateway

  # Connect to remote API
  python3 -m app.ble_gateway --api-url http://192.168.1.100:8000

  # Disable pairing requirement (insecure)
  python3 -m app.ble_gateway --no-pairing

  # Start in non-discoverable mode
  python3 -m app.ble_gateway --no-discoverable

  # Enable verbose logging
  python3 -m app.ble_gateway --verbose

The BLE device will advertise using the system hostname as its name.
        """,
    )

    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8000",
        help="Base URL of the tsconfig HTTP API (default: http://localhost:8000)",
    )

    parser.add_argument(
        "--no-pairing",
        action="store_true",
        help="Disable pairing requirement for write operations (insecure)",
    )

    parser.add_argument(
        "--no-discoverable",
        action="store_true",
        help="Start in non-discoverable mode (device won't appear in BLE scans)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


def main():
    """Main entry point for the BLE gateway."""
    args = parse_arguments()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    # Log configuration
    logger.info("=" * 60)
    logger.info("tsOS Configuration Manager - BLE GATT Gateway")
    logger.info("=" * 60)
    logger.info(f"API URL: {args.api_url}")
    logger.info(f"Pairing required: {not args.no_pairing}")
    logger.info(f"Discoverable: {not args.no_discoverable}")
    logger.info("=" * 60)

    # Check for dependencies
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
    except ImportError as e:
        logger.error("Required dependencies not installed!")
        logger.error("Please install BLE dependencies: pdm install --group ble")
        logger.error(f"Missing: {e}")
        sys.exit(1)

    # Verify API is reachable with retry logic
    logger.info(f"Verifying API connectivity to {args.api_url}...")
    max_retries = 10  # 10 seconds with 1-second intervals
    retry_interval = 1
    
    for attempt in range(max_retries):
        try:
            import httpx

            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{args.api_url}/api/server-mode")
                if response.status_code == 200:
                    logger.info("✓ API is reachable")
                    break
                else:
                    logger.warning(f"API returned status code: {response.status_code}")
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"✗ Cannot reach API (attempt {attempt + 1}/{max_retries}): {e}")
                logger.info(f"Retrying in {retry_interval} seconds...")
                import time
                time.sleep(retry_interval)
            else:
                logger.error(f"✗ Cannot reach API after {max_retries} attempts: {e}")
                logger.error("Please ensure tsconfig is running and accessible")
                sys.exit(1)

    # Create and start the BLE GATT server
    try:
        server = BleGattServer(
            api_url=args.api_url,
            device_name=None,  # Will use hostname
            require_pairing=not args.no_pairing,
            discoverable=not args.no_discoverable,
        )

        logger.info("Starting BLE GATT server...")
        server.start()

    except KeyboardInterrupt:
        logger.info("\nShutdown requested by user")
        sys.exit(0)
    except PermissionError:
        logger.error("Permission denied! BLE operations require elevated privileges.")
        logger.error("Try running with: sudo python3 -m app.ble_gateway")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to start BLE GATT server: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
