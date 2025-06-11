# Sensor Schedule Manager

A web-based configuration manager for sensor station schedules. This application provides a user-friendly interface to manage the schedule configuration for sensor stations, including sunrise/sunset-based scheduling.

## Features

- Web-based configuration interface
- Support for sunrise/sunset-based scheduling
- YAML configuration file management
- **Systemd Services Management** - Monitor and control systemd services
- Real-time system status monitoring
- Responsive design using Bootstrap

## Setup

1. Install PDM (Python Dependency Manager):
```bash
curl -sSL https://raw.githubusercontent.com/pdm-project/pdm/main/install-pdm.py | python3 -
```

2. Install dependencies:
```bash
pdm install
```

3. Run the application:
```bash
pdm run uvicorn app.main:app --reload
```

The application will be available at http://localhost:8000

## Systemd Services Management

The application includes a systemd services management feature that allows you to monitor and control system services through the web interface.

### Setup Service Control Permissions

To enable service start/stop/restart functionality, you need to configure sudo permissions:

1. Run the setup script:
```bash
./setup_systemd_permissions.sh
```

2. Or manually copy the sudoers configuration:
```bash
sudo cp configs/sudoers.d/tsconfig /etc/sudoers.d/tsconfig
sudo chmod 440 /etc/sudoers.d/tsconfig
```

### Default Services

The application monitors these services by default:
- `chrony` - Time synchronization
- `wittypid` - Witty Pi daemon
- `wg-quick@wireguard` - WireGuard VPN
- `mosquitto` - MQTT broker
- `mqttutil` - MQTT utilities

### Configuration

Services are configured in `configs/systemd_services.yml`. You can modify this file to add or remove services to monitor.

Example configuration:
```yaml
# Systemd services configuration
# Set expert: true for services that should only be visible in expert mode

services:
  - name: chrony
    expert: false
  - name: mosquitto
    expert: true
```

### Features

- **Service Status**: View active/inactive status and enabled/disabled state
- **Service Control**: Start, stop, and restart services
- **Service Information**: Display service descriptions and uptime
- **Expert Mode**: Toggle to show/hide expert-level services
- **Real-time Updates**: Refresh service status on demand
- **Security**: Only configured services can be controlled
- **YAML Configuration**: Flexible configuration with expert mode flags

## Development

This project uses:
- FastAPI for the backend
- Bootstrap for the frontend
- PDM for dependency management
- PyYAML for configuration file handling 