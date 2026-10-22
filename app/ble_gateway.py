"""BLE GATT Gateway for tsOS Configuration Manager.

This is the main entry point for the standalone BLE gateway that exposes
the tsconfig RESTful API via Bluetooth GATT.

Usage:
    python3 -m app.ble_gateway [options]

Options:
    --api-url URL           Base URL of tsconfig API (default: http://localhost:8000)
    --no-auth               Disable link encryption and the operator login
                            (alias: --no-pairing)
    --no-discoverable       Start in non-discoverable mode
    -v                      Enable info logging
    -vv                     Enable debug logging

The BLE device name will automatically use the system hostname.
"""

import argparse
import sys

from app.bluetooth.gatt_server import BleGattServer
from app.logging_config import get_logger, setup_logging


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
  # Run with defaults (localhost:8000, encrypted link + operator login, discoverable)
  python3 -m app.ble_gateway

  # Connect to remote API
  python3 -m app.ble_gateway --api-url http://192.168.1.100:8000

  # Disable encryption and the operator login (insecure)
  python3 -m app.ble_gateway --no-auth

  # Start in non-discoverable mode
  python3 -m app.ble_gateway --no-discoverable

  # Enable info logging
  python3 -m app.ble_gateway -v

  # Enable debug logging
  python3 -m app.ble_gateway -vv

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
        "--no-auth",
        "--no-pairing",
        dest="no_auth",
        action="store_true",
        help="Disable link encryption and the operator login (insecure; --no-pairing is the old name)",
    )

    parser.add_argument(
        "--no-discoverable",
        action="store_true",
        help="Start in non-discoverable mode (device won't appear in BLE scans)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="Increase verbosity: -v for INFO, -vv for DEBUG",
    )

    return parser.parse_args()


def main():
    """Main entry point for the BLE gateway."""
    args = parse_arguments()

    # Set up logging
    setup_logging(verbosity=args.verbose, identifier="tsconfig-ble")
    logger = get_logger(__name__)

    logger.info(
        "BLE gateway starting: API %s, operator login required: %s, discoverable: %s",
        args.api_url,
        not args.no_auth,
        not args.no_discoverable,
    )

    # Wait a little for the API, but start anyway: the gateway must stay reachable (and answer
    # requests with errors) while tsconfig is restarting or down, and works again once it is back.
    logger.debug("Verifying API connectivity to %s", args.api_url)
    max_retries = 10  # 10 seconds with 1-second intervals
    retry_interval = 1

    import httpx

    error = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{args.api_url}/api/server-mode")
                if response.status_code == 200:
                    logger.debug("API is reachable")
                    error = None
                    break
                else:
                    error = f"HTTP status {response.status_code}"
                    logger.debug(
                        "API returned status code %s (attempt %s/%s)", response.status_code, attempt + 1, max_retries
                    )
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            error = e
            if attempt < max_retries - 1:
                logger.debug(
                    "Cannot reach API (attempt %s/%s), retrying in %s s: %s",
                    attempt + 1,
                    max_retries,
                    retry_interval,
                    e,
                )
                import time

                time.sleep(retry_interval)
    if error is not None:
        logger.warning(
            "Cannot reach the tsconfig API at %s (%s), starting anyway: requests fail until it is reachable",
            args.api_url,
            error,
        )

    # Create and start the BLE GATT server
    try:
        server = BleGattServer(
            api_url=args.api_url,
            device_name=None,  # Will use hostname
            require_auth=not args.no_auth,
            discoverable=not args.no_discoverable,
        )

        logger.debug("Starting the BLE GATT server")
        server.start()

    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
        sys.exit(0)
    except PermissionError:
        logger.critical(
            "Permission denied while starting the BLE GATT server: on a tracker it runs as the tsconfig-ble user, "
            "see the BLE section of docs/security.md in tsOS-base"
        )
        sys.exit(1)
    except Exception:
        logger.critical("Cannot start the BLE GATT server", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
