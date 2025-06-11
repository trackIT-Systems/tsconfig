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

**Version**: 2025.6.1  
**Compatibility**: tsOS, Raspberry Pi OS, Linux systems with systemd 