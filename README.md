# tsOS Configuration Manager

A comprehensive web-based configuration and system management interface for trackIT Systems sensor stations. This application provides a unified interface to configure and monitor various aspects of sensor station operations, including schedule management, radio tracking configuration, system monitoring, and service control.

## Overview

tsOS Configuration Manager is designed for trackIT Systems sensor stations running tsOS (trackIT Systems Operating System). It serves as the central configuration hub, providing intuitive web-based management for:

- **Schedule Configuration** - Manage sensor station operation schedules with astronomical event support
- **Radio Tracking Configuration** - Configure RTL-SDR based radio tracking systems  
- **System Monitoring** - Real-time system status, resource monitoring, and health information
- **Service Management** - Control and monitor systemd services
- **System Control** - Perform system operations like rebooting

## Key Features

### 🕒 Schedule Management
- **Astronomical Events**: Schedule based on sunrise, sunset, dawn, dusk, solar noon/midnight
- **Golden/Blue Hours**: Support for photographic golden hour and blue hour scheduling
- **Time Offsets**: Add/subtract time from astronomical events
- **Multiple Schedules**: Create overlapping schedule entries for complex timing requirements
- **Interactive Map**: Visual location selection with geolocation support
- **Force Mode**: Override all scheduling for continuous operation

### 📡 Radio Tracking Configuration
- **RTL-SDR Support**: Configure multiple RTL-SDR devices for radio tracking
- **Signal Analysis**: Advanced signal detection with configurable thresholds and filters
- **Signal Matching**: Cross-device signal correlation for improved accuracy
- **Data Export**: MQTT, CSV, and stdout publishing options
- **Real-time Dashboard**: Optional web dashboard for live signal monitoring
- **Expert Mode**: Advanced configuration options for experienced users

### 💻 System Monitoring
- **Real-time Metrics**: CPU load, memory usage, disk space, network activity
- **Temperature Monitoring**: Hardware temperature sensors (when available)  
- **Load Averages**: System load monitoring with htop-style visualization
- **OS Information**: Detailed operating system and hardware information
- **Auto-refresh**: Continuous updates when monitoring view is active

### ⚙️ Service Management
- **Service Control**: Start, stop, restart systemd services through web interface
- **Real-time Status**: Monitor service health, uptime, and configuration
- **Log Streaming**: Live service log viewing with real-time updates
- **Expert Mode**: Show/hide advanced services based on user experience level
- **Secure Operations**: Controlled access to only configured services

### 🔧 System Control
- **System Reboot**: Safely restart the system with confirmation dialogs
- **Secure Operations**: Controlled execution with proper permission management

### 📱 Bluetooth Low Energy (BLE) Gateway
- **Wireless Access**: Access all API endpoints via Bluetooth GATT
- **Mobile Ready**: Connect from smartphones and tablets without network configuration
- **Standalone Operation**: Runs as separate service, independent of web interface
- **Secure**: Encrypted link (BlueZ Just Works pairing) plus an operator login with `pi`'s password for everything except basic system info
- **Standard Tools**: Works with nRF Connect, LightBlue, gatttool, and other BLE clients
- **Organized Services**: Multiple GATT services mirroring REST API structure
- **Auto-discovery**: Advertises using system hostname for easy identification

## Installation

### Prerequisites
- Python 3.9 or higher
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

3. **Configure system permissions** (required for service control and reboot):
   ```bash
   ./setup_systemd_permissions.sh
   ```

4. **Run the application**:
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
   pdm install --group ble
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

The default `pi` password is `natur` until a config bundle (`userconf.txt`) changes it. Staff can log in as root instead: add `"username": "root"` to the login request (works only if the device has a root password).

- **Disable authentication**: `--no-auth` (alias `--no-pairing`) turns off both the encryption requirement and the
  login (not recommended for production)
- **`test_ble_client.py`** logs in with `--password` (default `natur`)

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
│   ├── routers/             # API endpoints
│   │   ├── schedule.py      # Schedule management
│   │   ├── radiotracking.py # Radio tracking config
│   │   └── systemd.py       # System control
│   ├── configs/             # Configuration schemas
│   └── templates/           # Web interface
├── configs/                 # Configuration files
└── setup_systemd_permissions.sh # Permission setup
```

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
