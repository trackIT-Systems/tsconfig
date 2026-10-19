# tsconfig

[![Lint and test](https://github.com/trackIT-Systems/tsconfig/actions/workflows/test.yml/badge.svg)](https://github.com/trackIT-Systems/tsconfig/actions/workflows/test.yml)
[![Build and Push Docker Image](https://github.com/trackIT-Systems/tsconfig/actions/workflows/docker-build-push.yml/badge.svg)](https://github.com/trackIT-Systems/tsconfig/actions/workflows/docker-build-push.yml)
![Python](https://img.shields.io/badge/python-%E2%89%A53.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Alpine.js](https://img.shields.io/badge/Alpine.js-8BC0D0?logo=alpinedotjs&logoColor=white)
[![Last commit](https://img.shields.io/github/last-commit/trackIT-Systems/tsconfig)](https://github.com/trackIT-Systems/tsconfig/commits)
[![Issues](https://img.shields.io/github/issues/trackIT-Systems/tsconfig)](https://github.com/trackIT-Systems/tsconfig/issues)
[![tsOS-base](https://img.shields.io/badge/part%20of-tsOS--base-blue)](https://github.com/trackIT-Systems/tsOS-base)

The web interface and API for configuring and monitoring trackIT Systems sensor stations. It runs on every station of the [tsOS-base](https://github.com/trackIT-Systems/tsOS-base) image, behind Caddy and a login with the `pi` account's password, and is the one place to set up a station, see how it is doing and watch its signals live. A Bluetooth Low Energy gateway offers the same API to a phone in the field.

## Contents

[Features](#features) · [Installation](#installation) · [Configuration](#configuration) · [Usage](#usage) · [API](#api-reference) · [BLE gateway](#bluetooth-low-energy-ble-gateway) · [Development](#development) · [License](#license)

## Features

| Page | What it does |
| --- | --- |
| **Status** | CPU, memory, disk, temperature, load, network, power and location at a glance; start, stop and restart the station's systemd services and stream their logs; reboot |
| **Live** | Signals of the radiotracking pipeline as they arrive, in the browser, with no server-side component (see below) |
| **System Settings** | Network (Wi-Fi, LTE, WireGuard), time and location, users and SSH keys, MQTT, updates, and the other groups of the station's own configuration |
| **Radio Tracking** | RTL-SDR devices, signal detection and matching, and where the results are published (MQTT, CSV); an expert mode shows the advanced options |
| **Schedule** | Operation schedules from fixed times or astronomical events (sunrise, sunset, dawn, dusk, noon, golden and blue hour) with offsets, several overlapping entries, a map to pick the location and a force mode for continuous operation |
| **Soundscape pipeline** | Configuration of the acoustic pipeline |
| **Web shell** | A terminal in the browser, for those who need one |

### Live dashboard

The **Live** page reads the station's own MQTT broker over WebSockets at `/mqtt` (tsOS-base serves it behind the login) and draws what arrives. tsconfig itself subscribes to nothing and stores nothing: the last five minutes are held in the browser and cleared when the page or tab is left. There is a tab for each service that is available, and a time window selector (30 seconds to 5 minutes). Radiotracking has three panels:

- **Signal Power**: matched signals in dBW, or calibrated in dBm
- **Frequency Usage**: the frequency of the matched signals over time
- **Signal Detail**: the raw signals of every SDR, with the value on the vertical axis to choose (started with a button, as they are many)

Dots are drawn at the signal's own `Time` on a sliding 24-hour axis, so a station clock that is off or detections that arrive late show up as signals away from "now". A dot's size is the signal's duration, its colour the SDR's compass direction (0 blue, 1 yellow, 2 red, 3 green), and the axes follow the radiotracking configuration. Details: [docs/frontend.md](docs/frontend.md).

### Bluetooth Low Energy (BLE) gateway

A separate service (`tsconfig-ble`) that exposes the API as GATT services, so smartphones and tablets can configure a station without any network: encrypted link plus the operator login, standard tools work (nRF Connect, LightBlue, gatttool), and the station advertises under its hostname. See [below](#bluetooth-low-energy-ble-gateway) and [docs/ble-protocol.md](docs/ble-protocol.md).

### Two modes

In **tracker mode** (on a station) the login is the `pi` password, checked through PAM. In **server mode** it runs centrally, keeps configuration per group of stations to deploy to them, and logs in through OIDC.

## Installation

### Prerequisites
- Python 3.12 or higher (tsOS runs 3.13)
- Linux-based system (tested on Raspberry Pi OS / tsOS)
- Appropriate system permissions for system control features

### Setup

1. **Install PDM (Python Dependency Manager)**:
   ```bash
   curl -sSL https://raw.githubusercontent.com/pdm-project/pdm/main/install-pdm.py | python3 -
   ```

2. **Install dependencies**:
   ```bash
   pdm install
   ```
   Extras: `pdm install -G server` adds the OIDC libraries for server mode, `pdm install -G ble` the BLE
   gateway's. Without PDM: `pip install -e .` (plus `".[server]"` or `".[ble]"`) and `pip install --group dev`
   for the test tools. On a tsOS image everything comes from Debian packages (see `tsOS-base.Pifile`).

3. **Run the application**:
   ```bash
   # Using uvicorn directly (recommended)
   python3 -m uvicorn app.main:app --reload
   
   # Or using pdm
   pdm run uvicorn app.main:app --reload
   ```

The application will be available at `http://localhost:8000`

### Logging Configuration

The application supports configurable logging levels optimized for journald consumption:

```bash
# Default (WARNING level - only warnings and errors)
python3 -m uvicorn app.main:app --reload

# INFO level (shows operational events)
LOG_LEVEL=INFO python3 -m uvicorn app.main:app --reload

# DEBUG level (shows detailed diagnostics)
LOG_LEVEL=DEBUG python3 -m uvicorn app.main:app --reload
```

**Log Levels:**
- **ERROR**: an operation failed and needs attention
- **WARNING** (default): unexpected but handled
- **INFO**: one-off events only: startup, login/logout, config written, service restarted, deployment, shell/BLE session lifecycle. Never per-request or periodic.
- **DEBUG**: everything chatty: request tracing, token refresh, polling, D-Bus communication, HTTP access logs

**Note:** HTTP access logs (`uvicorn.access`) and HTTP client logs (`httpx`, `httpcore`) are only emitted at DEBUG, so INFO stays readable while the web UI polls the API. Set `ACCESS_LOG_LEVEL` (e.g. `INFO`) to override this independently of `LOG_LEVEL`. Other libraries (uvicorn, fastapi, dbus) follow `LOG_LEVEL`.

### Running Behind a Reverse Proxy

If you need to run tsconfig at a subpath (e.g., `http://example.com/tsconfig/`), you can configure the base URL using the `TSCONFIG_BASE_URL` environment variable:

```bash
# Run at a subpath
export TSCONFIG_BASE_URL="/tsconfig"
python3 -m uvicorn app.main:app --reload

# Or inline
TSCONFIG_BASE_URL="/tsconfig" python3 -m uvicorn app.main:app --reload
```

The default value is `/` (root path). When configured, all URLs (API endpoints, static files, and navigation) will respect the base path.

**Nginx reverse proxy example:**
```nginx
location /tsconfig/ {
    proxy_pass http://localhost:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Configuration

### Service Management Setup

The application can control systemd services through the web interface. The default monitored services include:

- **chrony** - Time synchronization service
- **tsschedule** - tsschedule daemon for power management
- **wg-quick@wireguard** - WireGuard VPN connection
- **mosquitto** - MQTT broker for data communication
- **mqttutil** - MQTT utility services

Services are configured in `configs/systemd_services.yml`:

```yaml
# Systemd services configuration
services:
  - name: chrony
    expert: false  # Always visible
  - name: mosquitto  
    expert: true   # Only visible in expert mode
```

### Security Configuration

System control features require appropriate permissions. The application must be run with sufficient privileges to execute system control operations such as:

- Starting, stopping, and restarting systemd services
- Initiating system reboots
- Modifying network configurations
- Managing system files

## Usage

### Schedule Configuration
1. Navigate to the **Schedule** tab
2. Set your location using the interactive map or coordinates
3. Create schedule entries with start/stop times
4. Use astronomical events or specific times
5. Download or save configuration

### Radio Tracking Configuration
1. Go to the **Radio Tracking** tab
2. Configure RTL-SDR devices and frequency settings
3. Set signal analysis parameters
4. Configure data publishing options
5. Enable expert mode for advanced settings

### System Monitoring
1. Visit the **Status** tab (default page)
2. View real-time system information
3. Monitor service status and control services
4. Stream service logs for troubleshooting
5. Use the reboot button for system restarts

## API Reference

The application provides REST APIs for all functionality:

- `GET /api/system-status` - System monitoring data
- `GET/PUT /api/schedule` - Schedule configuration
- `GET/PUT /api/radiotracking` - Radio tracking configuration  
- `GET /api/systemd/services` - Service status
- `POST /api/systemd/action` - Service control
- `POST /api/systemd/reboot` - System reboot
- `GET /api/systemd/logs/{service}` - Service log streaming

## Bluetooth Low Energy (BLE) Gateway

The BLE gateway provides wireless access to the tsconfig API via Bluetooth GATT, enabling mobile devices and other BLE clients to configure and monitor sensor stations without network infrastructure.

### Features

- **Wireless Access**: All system, systemd, and config endpoints accessible via BLE
- **Mobile Ready**: Perfect for field configuration from smartphones/tablets
- **Standalone**: Runs independently from the main web application
- **Secure**: Encrypted link and operator login (`pi` password) required for writes, logs and config
- **Auto-discovery**: Advertises using system hostname
- **Standard Compliance**: Works with any BLE GATT client

### Installation

1. **Install BLE dependencies**:
   ```bash
   pdm install -G ble
   ```

2. **Verify Bluetooth adapter**:
   ```bash
   bluetoothctl show
   ```

3. **Run the BLE gateway**:
   ```bash
   # Basic usage (requires appropriate permissions)
   python3 -m app.ble_gateway

   # With custom API URL
   python3 -m app.ble_gateway --api-url http://192.168.1.100:8000

   # Disable link encryption and the operator login (insecure)
   python3 -m app.ble_gateway --no-auth

   # Enable debug logging
   python3 -m app.ble_gateway --verbose
   ```

4. **Install as systemd service** (recommended):
   ```bash
   cp tsconfig-ble.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable tsconfig-ble.service
   systemctl start tsconfig-ble.service
   ```

### GATT Services & Characteristics

The BLE gateway exposes three GATT services, each with multiple characteristics that map 1:1 to REST API endpoints.

**Base UUID**: `0000XXXX-7473-4f53-636f-6e6669672121`
- `7473` = "ts" (ASCII hex)
- `4f53` = "OS" (ASCII hex)
- `636f6e666967` = "config" (ASCII hex)

#### System Service (`00001000-7473-4f53-636f-6e6669672121`)

Read-only characteristics for system information (open: no login needed, so devices can be identified), plus the operator login:

| Characteristic | UUID | REST Endpoint |
|----------------|------|---------------|
| System Status | `00001001-...` | GET /api/system-status |
| Server Mode Info | `00001002-...` | GET /api/server-mode |
| Timedatectl Status | `00001003-...` | GET /api/timedatectl-status |
| Available Services | `00001004-...` | GET /api/available-services |
| Authenticate (write) | `00001005-...` | operator login, see [Security](#security) |

#### Systemd Service (`00002000-7473-4f53-636f-6e6669672121`)

Service management (operator login required):

| Characteristic | UUID | REST Endpoint |
|----------------|------|---------------|
| Services List | `00002001-...` | GET /api/systemd/services |
| Service Action | `00002002-...` | POST /api/systemd/action |
| System Reboot | `00002003-...` | POST /api/systemd/reboot |
| Service Logs | `00002004-...` | GET /api/systemd/logs/{service}?follow=false |

#### Config Service (`00003000-7473-4f53-636f-6e6669672121`)

Configuration file upload and download (operator login required):

| Characteristic | UUID | REST Endpoint |
|----------------|------|---------------|
| Config Upload | `00003001-...` | POST /api/configs/update |
| Zip Upload | `00003002-...` | POST /api/configs.zip |

**Note**: Download endpoints (`GET /api/configs/{filename}` and `GET /api/configs.zip`) are available via the REST API but not exposed as BLE characteristics.

### Protocol

Reads return a small CBOR metadata record; the data itself follows as notifications of at most 512 bytes. Writes carry a JSON request (split over several writes when it is longer than the MTU) and are answered by notifications. The full reference for client developers, including request and response formats, framing, limits and error codes, is in [docs/ble-protocol.md](docs/ble-protocol.md). `test_ble_client.py` is a working example client (bleak).

A redesigned protocol (framed, encrypted, password-authenticated key exchange, no pairing) is described in [docs/ble-protocol-v2-design.md](docs/ble-protocol-v2-design.md). It is a proposal and not implemented.

### Security

Everything except the System Service's status characteristics requires two things:

1. **An encrypted link.** The gated characteristics (and the login itself) are exposed with BlueZ's
   `encrypt-*` flags, so your OS starts LE pairing the first time you touch one. The gateway registers a
   `NoInputNoOutput` pairing agent: pairing is "Just Works" - no PIN or confirmation on the device. This
   keeps the login from being sniffable; it does not protect against an active man-in-the-middle during
   pairing (the device has no display to compare a passkey on).
2. **The operator login, per connection.** Write `{"password": "<pi's password>"}` to the Authenticate
   characteristic (`00001005-...`). The gateway checks it against PAM (the same check as the web login) and
   notifies `{"authenticated": true}`, or an error: code `401` for a wrong password, `429` with a
   `retry_after` (seconds) while it is backing off after repeated failures. Until a connection has logged
   in, gated characteristics answer `{"error": "Authentication required", "code": 401}` and do nothing. The
   login ends on disconnect, after 8 hours, or when the gateway restarts.

Instead of the password, `{"hash": "$6$..."}` logs in as `pi` with pi's password hash, i.e. the line in the `userconf.txt` of the config bundle applied to the device; it works while the password has not been changed since (see [docs/ble-protocol.md](docs/ble-protocol.md)). To read `/etc/shadow` the gateway runs as its own user `tsconfig-ble` in the `shadow` group.

The default `pi` password is `natur` until a config bundle (`userconf.txt`) changes it. Staff can log in as root instead: add `"username": "root"` to the login request (works only if the device has a root password).

- **Disable authentication**: `--no-auth` (alias `--no-pairing`) turns off both the encryption requirement and the
  login (not recommended for production)
- **`test_ble_client.py`** logs in with `--password` (default `natur`), or first with pi's hash from `--userconf FILE` / `--hash HASH`

### Requirements

- Linux with BlueZ 5.50+
- Bluetooth hardware adapter
- Root or CAP_NET_ADMIN capability
- Running tsconfig HTTP API
- D-Bus system bus

### Troubleshooting

**Device not appearing in scans:**
- Ensure Bluetooth is powered: `bluetoothctl power on`
- Check gateway is running: `systemctl status tsconfig-ble`
- Try making adapter discoverable: `bluetoothctl discoverable on`

**Intermittent device detection (test_ble_client.py):**
- BLE advertising is intermittent - the test client now includes automatic retry logic
- Use `--retries 5` for more reliable detection in difficult environments
- Use `--timeout 15` to increase scan time per attempt
- See [BLE_TROUBLESHOOTING.md](BLE_TROUBLESHOOTING.md) for detailed troubleshooting guide

**Permission denied errors:**
- BLE operations require appropriate privileges (root or CAP_NET_ADMIN capability)
- Grant capabilities: `setcap cap_net_admin+eip $(which python3)`

**Cannot write to characteristics:**
- Pair the device first (your OS normally starts this on first access) and log in through the Authenticate characteristic
- Or disable authentication: `--no-auth` flag

**API connection errors:**
- Verify tsconfig is running: `curl http://localhost:8000/api/server-mode`
- Check API URL: `--api-url http://correct-ip:8000`

## Development

### Technology Stack
- **Backend**: FastAPI with async/await support
- **Frontend**: Bootstrap 5 with Alpine.js for reactivity
- **Configuration**: YAML and INI file formats
- **System Integration**: systemd, psutil for system monitoring
- **Mapping**: Leaflet with Mapbox satellite imagery

### Project Structure
```
tsconfig/
├── app/
│   ├── main.py              # FastAPI application
│   ├── routers/             # API endpoints (schedule, radiotracking, systemd, network, ...)
│   ├── configs/             # Configuration schemas
│   ├── ble_gateway.py       # BLE gateway
│   ├── templates/           # Jinja pages and partials
│   └── static/              # Alpine.js frontend (see docs/frontend.md), vendored libraries
├── configs/                 # Default configuration files
├── docs/                    # Frontend structure, BLE protocol
└── tests/                   # pytest (backend) and node:test (frontend, tests/js)
```

### Tests

```bash
ruff check .              # lint (pinned in CI)
python -m pytest -q        # backend
node --test 'tests/js/*.test.mjs'   # frontend (Node 24)
```

CI ([Lint and test](.github/workflows/test.yml)) runs all three, and builds the Docker image.

### Contributing
This project is part of the trackIT Systems ecosystem. For development:

1. Follow PEP 8 coding standards
2. Use type hints for all functions
3. Add docstrings for public APIs
4. Test on target hardware (Raspberry Pi/tsOS)

## License

© 2025 trackIT Systems. All rights reserved.

## Support

For support and documentation:
- Website: [https://trackit.systems](https://trackit.systems)
- Email: [info@trackit.systems](mailto:info@trackit.systems)

---

**Compatibility**: tsOS, Raspberry Pi OS, Linux systems with systemd 
