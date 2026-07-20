# Changelog

All notable changes to tsconfig are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Calendar Versioning](https://calver.org/) (`YYYY.M.PATCH`).

## [Unreleased]

### Added

- Verification of file modification times (`mtime`) and filesystem synchronization (`sync`) on configuration updates

### Changed

- Restrict 'maintenance' schedule entries to fixed clock times (HH:MM) and disable astronomical relative references

## [2026.7.1] - 2026-07-14

### Added

- Brownout recovery settings in the schedule configuration UI
- `mqttutil.conf` support with a Reporting settings panel
- `huaweicheck` and `wificheck` services to the default systemd service list
- BAT GM90IP microphone to the audio device catalog
- GitHub Actions workflow to build and push Docker images on branch and tag pushes
- Changelog documenting project history

## [2026.4.4] - 2026-04-17

### Changed

- Updated MBART model name in species configuration

## [2026.4.3] - 2026-04-14

### Fixed

- Label JSON formatting for species configuration

## [2026.4.2] - 2026-04-14

### Changed

- Updated YOLOBat species list and hints

## [2026.4.1] - 2026-04-13

### Added

- tsupdate configuration provisioning and settings panel
- tsupdate `config.zip` upload support
- Automatic copy of `tsconfig.yml` to the configured location when missing

### Changed

- Default analog frontend gain set to high
- Docker images published to ZOT registry

## [2026.3.3] - 2026-03-31

### Fixed

- Broken Jinja template renderer

### Added

- Manual workflow dispatch for Docker builds

## [2026.3.2] - 2026-03-30

_No user-facing changes._

## [2026.3.1] - 2026-03-25

### Added

- Wi-Fi client (station) mode with connection priority
- AudioProtoPNet classifier with feature flag
- Mdas and Malc species to `yolobat_species.json`
- New YOLOBat model version

### Changed

- Config path configurable via environment variable
- FAT32 mtime truncation logic aligned with filesystem behaviour
- App disabled by default in server mode

## [2026.2.4] - 2026-02-27

### Added

- Analog frontend gain selection for soundscapepipe
- System reset options

### Changed

- Input sanitization no longer runs on every keystroke
- Updated Python dependencies

### Fixed

- Wi-Fi capabilities queried system-wide instead of per-device
- Config group parameter lost during save

### Removed

- Unused authentication path

## [2026.2.3] - 2026-02-11

### Fixed

- Config group parameter lost when switching configuration tabs

## [2026.2.2] - 2026-02-10

### Added

- Automatic periodic refresh of OIDC access tokens

## [2026.2.1] - 2026-02-04

### Added

- OIDC authentication via Authentik
- CLI for applying configuration zip archives (`tsconfig` entry point)
- Auto-deploy of configs when saving from the web UI
- Radiotracking and soundscapepipe status panels on the status page
- dmesg / kernel log viewer
- Async subprocess execution for system commands

### Changed

- Auto-deploy logic moved from frontend to backend
- Status page sections extracted into reusable components
- Expert mode settings handling improved
- Server mode label fallbacks
- Network connectivity display made more user-centric
- Shell modal: focus on open, close on exit code 0
- Log status indicator styling

### Fixed

- Tracker mode no longer depends on authentication
- Incorrect quote escaping in shell commands
- Wrong pip group syntax in packaging

### Removed

- Reboot protection toggle
- Unused function in server mode module

## [2026.1.2] - 2026-01-28

### Added

- Auto-deploy of configs when saving (initial frontend implementation)

## [2026.1.1] - 2026-01-09

### Added

- tsupdate configuration panel
- Config group selector
- Tryboot status display
- Maintenance schedule integration for tsupdate

### Changed

- Schedule service renamed from `schedule` to `tsschedule`
- SSH keys management reflects `ssh.service` state
- UI refinements in tsschedule and tsupdate sections
- Server mode adaptation for multi-station deployments

### Fixed

- Infinite spinner on empty service logs
- Reboot spinner not stopping after reboot completed

### Removed

- `sudo` usage for privileged operations

## [2025.11.2] - 2025-11-17

### Added

- Config group selector for server mode

## [2025.11.1] - 2025-11-13

### Added

- Network configuration panel with NetworkManager integration
- Netplan-based network configuration
- Wi-Fi expert settings
- ModemManager service and modem details section
- SSH key management improvements
- VHF Signals dashboard link
- Config version status API
- Lean Bluetooth protocol with `0x7473` service serial announcement
- Forceful config overwrite via Bluetooth

### Changed

- Reboot initiated with 10-second delay
- Systemd target handling improved
- Temperature sensor name formatting
- File upload mtime matching made consistent
- Logging logic restructured
- Signal quality styling on status page

### Fixed

- Empty model paths in server mode

## [2025.10.2] - 2025-10-17

### Added

- Centralized geolocation handling via `/etc/geolocation`
- Config upload tab and versioned config-zip logic
- Upload handlers for all configuration file types
- SSH authorized keys configuration
- Bluetooth config gateway with chunked data transfer
- Server mode versioning
- Toast notification system

### Changed

- Schedule tab renamed to Settings
- Schedule and soundscapepipe UI redesign
- Map styling and location picker fixes
- Lat/lon removed from schedule and soundscapepipe (centralized instead)
- `cmdline.txt` updated for hostname; authorized keys synced
- Config update timestamps default to UTC
- Soundscapepipe audio device logic repaired

### Fixed

- BLE startup race condition
- Wrong REST API endpoint for zip upload
- Permission errors for authorized keys
- Config file upload endpoint
- Smaller map UI issues

### Removed

- Enable/disable toggle from schedule settings
- Shell REST endpoints (replaced by modal shell)

## [2025.10.1] - 2025-10-14

### Added

- Server mode for multi-station configuration management
- Dockerfile and GitHub Actions CI for container builds
- `TSCONFIG_BASE_URL` support for reverse-proxy subpath deployment
- Swagger UI for API documentation
- Config upload API endpoints
- Refactored, modular JavaScript architecture

### Changed

- Audio device configuration refactored for tracker mode
- Legacy API endpoints removed

## [2025.8.0] - 2025-08-19

### Changed

- Storage usage display refactored on status page

## [2025.7.0] - 2025-07-31

### Added

- Dynamic hostname updates
- Stream player on status page
- Soundscapepipe `disk_reserve_mb` parameter
- Channel strategy configuration
- Species group configuration with YOLOBat species hints
- Service enable/disable toggles on status page
- Datetime and hardware details on status dashboard
- Buttons with status indication in service list

### Fixed

- Inconsistent latitude/longitude handling

## [2025.6.0] - 2025-06-04

Initial release of the tsOS Configuration Manager, replacing sysdweb.

### Added

- Web-based configuration manager built on FastAPI, Bootstrap 5, and Alpine.js
- Schedule configuration with astronomical events and interactive Leaflet map
- Radio tracking (RTL-SDR) configuration panel
- Soundscapepipe configuration with species selection, lure entries, and model paths
- System monitoring status page with CPU, memory, disk, and temperature metrics
- Systemd service management: start, stop, restart, and live log streaming
- System reboot with confirmation dialog
- Expert mode for advanced settings across all sections
- Configuration download and save/reload buttons
- Modal shell terminal (xterm.js) for remote command access
- Reboot protection toggle
- Locally served static assets (JS, CSS, vendor libraries)
- BLE gateway for wireless API access via Bluetooth GATT
