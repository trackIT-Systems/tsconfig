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
- **Secure Operations**: Sudo-based execution with proper permission management

### 📱 Bluetooth Low Energy (BLE) Gateway
- **Wireless Access**: Access all API endpoints via Bluetooth GATT
- **Mobile Ready**: Connect from smartphones and tablets without network configuration
- **Standalone Operation**: Runs as separate service, independent of web interface
- **Secure**: Configurable pairing requirement for write operations
- **Standard Tools**: Works with nRF Connect, LightBlue, gatttool, and other BLE clients
- **Organized Services**: Multiple GATT services mirroring REST API structure
- **Auto-discovery**: Advertises using system hostname for easy identification

## Installation

### Prerequisites
- Python 3.9 or higher
- Linux-based system (tested on Raspberry Pi OS / tsOS)
- sudo access for system control features

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
   pdm run uvicorn app.main:app --reload
   ```

The application will be available at `http://localhost:8000`

### Running Behind a Reverse Proxy

If you need to run tsconfig at a subpath (e.g., `http://example.com/tsconfig/`), you can configure the base URL using the `TSCONFIG_BASE_URL` environment variable:

```bash
# Run at a subpath
export TSCONFIG_BASE_URL="/tsconfig"
pdm run uvicorn app.main:app --reload

# Or inline
TSCONFIG_BASE_URL="/tsconfig" pdm run uvicorn app.main:app --reload
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
- **wittypid** - Witty Pi daemon for power management
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

System control features require sudo permissions. The setup script configures these securely:

```bash
# Allow specific operations without password
username ALL=(ALL) NOPASSWD: /bin/systemctl start servicename
username ALL=(ALL) NOPASSWD: /bin/systemctl stop servicename  
username ALL=(ALL) NOPASSWD: /bin/systemctl restart servicename
username ALL=(ALL) NOPASSWD: /sbin/reboot
```

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

- **Wireless Access**: All system, systemd, and upload endpoints accessible via BLE
- **Mobile Ready**: Perfect for field configuration from smartphones/tablets
- **Standalone**: Runs independently from the main web application
- **Secure**: Optional pairing requirement for write operations
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
   # Basic usage
   sudo python3 -m app.ble_gateway

   # With custom API URL
   sudo python3 -m app.ble_gateway --api-url http://192.168.1.100:8000

   # Disable pairing requirement (insecure)
   sudo python3 -m app.ble_gateway --no-pairing

   # Enable debug logging
   sudo python3 -m app.ble_gateway --verbose
   ```

4. **Install as systemd service** (recommended):
   ```bash
   sudo cp tsconfig-ble.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable tsconfig-ble.service
   sudo systemctl start tsconfig-ble.service
   ```

### GATT Services & Characteristics

The BLE gateway exposes three GATT services, each with multiple characteristics that map 1:1 to REST API endpoints.

**Base UUID**: `0000XXXX-7473-4f53-636f-6e6669672121`
- `7473` = "ts" (ASCII hex)
- `4f53` = "OS" (ASCII hex)
- `636f6e666967` = "config" (ASCII hex)

#### System Service (`00001000-7473-4f53-636f-6e6669672121`)

Read-only characteristics for system information:

| Characteristic | UUID | REST Endpoint |
|----------------|------|---------------|
| System Status | `00001001-...` | GET /api/system-status |
| Server Mode Info | `00001002-...` | GET /api/server-mode |
| Timedatectl Status | `00001003-...` | GET /api/timedatectl-status |
| Available Services | `00001004-...` | GET /api/available-services |

#### Systemd Service (`00002000-7473-4f53-636f-6e6669672121`)

Service management with pairing required for writes:

| Characteristic | UUID | REST Endpoint |
|----------------|------|---------------|
| Services List | `00002001-...` | GET /api/systemd/services |
| Service Action | `00002002-...` | POST /api/systemd/action |
| System Reboot | `00002003-...` | POST /api/systemd/reboot |
| Service Logs | `00002004-...` | GET /api/systemd/logs/{service} |

#### Upload Service (`00003000-7473-4f53-636f-6e6669672121`)

Configuration file uploads (pairing required):

| Characteristic | UUID | REST Endpoint |
|----------------|------|---------------|
| Config Upload | `00003001-...` | POST /api/upload |
| Zip Upload | `00003002-...` | POST /api/upload/zip |

### Usage Examples

#### Using gatttool (Linux)

```bash
# Scan for devices
sudo hcitool lescan

# Connect and explore services
gatttool -b AA:BB:CC:DD:EE:FF -I
> connect
> primary
> characteristics

# Read system status
gatttool -b AA:BB:CC:DD:EE:FF --char-read --uuid=00001001-7473-4f53-636f-6e6669672121

# Write service action (requires pairing)
echo '{"service":"radiotracking","action":"restart"}' | \
  gatttool -b AA:BB:CC:DD:EE:FF --char-write-req --uuid=00002002-7473-4f53-636f-6e6669672121
```

#### Using bluetoothctl (Linux)

```bash
# Scan and connect
bluetoothctl
> scan on
> connect AA:BB:CC:DD:EE:FF
> pair AA:BB:CC:DD:EE:FF

# List GATT attributes
> menu gatt
> list-attributes
> select-attribute /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service0010/char0011
> read
```

#### Using nRF Connect (Android/iOS)

1. Open nRF Connect app
2. Scan for devices, find device named with your sensor's hostname
3. Connect to the device
4. Tap on a service to expand characteristics
5. Read values by tapping the download icon
6. Write values by tapping the upload icon (requires pairing for write operations)

#### Using Python with Bleak

```python
import asyncio
import json
from bleak import BleakClient, BleakScanner

SYSTEM_STATUS_UUID = "00001001-7473-4f53-636f-6e6669672121"

# Notification handler to collect chunks
chunks = {}
complete_data = None

def notification_handler(sender, data):
    global chunks, complete_data
    chunk = json.loads(data.decode('utf-8'))
    chunks[chunk['seq']] = chunk['data']
    if chunk.get('complete') and len(chunks) == chunk['total']:
        complete_data = ''.join(chunks[i] for i in range(chunk['total']))

async def main():
    global complete_data
    
    # Find device by name
    device = await BleakScanner.find_device_by_name("your-hostname")
    
    async with BleakClient(device) as client:
        # Enable notifications
        await client.start_notify(SYSTEM_STATUS_UUID, notification_handler)
        
        # Read characteristic to get metadata and trigger data transfer
        metadata = await client.read_gatt_char(SYSTEM_STATUS_UUID)
        print("Metadata:", json.loads(metadata.decode('utf-8')))
        
        # Wait for notification data
        await asyncio.sleep(2.0)
        
        # Stop notifications
        await client.stop_notify(SYSTEM_STATUS_UUID)
        
        if complete_data:
            print("Data:", complete_data)

asyncio.run(main())
```

### Data Protocol

**Notification-Only Protocol:**
All actual data is transferred via BLE notifications. Read operations return only metadata.

**Reading Data (Notification-Only):**
1. **Enable notifications** on the characteristic
2. **Read the characteristic** to trigger data transfer and get metadata
3. **Receive actual data** via notification chunks
4. **Reassemble chunks** in order to get complete data

Metadata response format:
```json
{
  "metadata": true,
  "content_length": 1234,
  "chunks_expected": 4,
  "content_type": "application/json",
  "status": "ready",
  "hint": "Data will be delivered via notifications"
}
```

Data chunks format:
```json
{
  "seq": 0,
  "total": 4,
  "data": "...",
  "complete": false
}
```

**Important:** Notifications must be enabled before reading, or you'll receive an error:
```json
{
  "metadata": true,
  "status": "error",
  "error": "Notifications must be enabled to receive data"
}
```

**Write Operations:**
Write operations send responses via notifications (no read required):
- Service actions: `{"service": "name", "action": "restart"}`
- Logs: `{"service": "name", "lines": 100}`
- File uploads: `{"filename": "config.yml", "content": "base64data..."}`

### Security

- **Read operations**: No pairing required (open access)
- **Write operations**: Pairing required by default
- **Disable pairing**: Use `--no-pairing` flag (not recommended for production)
- **Pairing process**: Use your BLE client's pairing feature before write operations

### Requirements

- Linux with BlueZ 5.50+
- Bluetooth hardware adapter
- Root or CAP_NET_ADMIN capability
- Running tsconfig HTTP API
- D-Bus system bus

### Troubleshooting

**Device not appearing in scans:**
- Ensure Bluetooth is powered: `sudo bluetoothctl power on`
- Check gateway is running: `sudo systemctl status tsconfig-ble`
- Try making adapter discoverable: `sudo bluetoothctl discoverable on`

**Intermittent device detection (test_ble_client.py):**
- BLE advertising is intermittent - the test client now includes automatic retry logic
- Use `--retries 5` for more reliable detection in difficult environments
- Use `--timeout 15` to increase scan time per attempt
- See [BLE_TROUBLESHOOTING.md](BLE_TROUBLESHOOTING.md) for detailed troubleshooting guide

**Permission denied errors:**
- BLE operations require root: `sudo python3 -m app.ble_gateway`
- Or grant capabilities: `sudo setcap cap_net_admin+eip $(which python3)`

**Cannot write to characteristics:**
- Pair the device first using your BLE client
- Or disable pairing requirement: `--no-pairing` flag

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

**Version**: 2025.10.1  
**Compatibility**: tsOS, Raspberry Pi OS, Linux systems with systemd 