# Changelog

All notable changes to tsconfig are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Calendar Versioning](https://calver.org/) (`YYYY.M.PATCH`).

## [Unreleased]

### Security

- Operator authentication in tracker mode. The web UI, API and web shell now require a login with the `pi` account's password, checked through PAM (`app/auth/pam_auth.py`, service `/etc/pam.d/tsconfig`). Sessions are an HttpOnly cookie with an 8-hour sliding expiry, signed with a key held only in memory (a restart logs everyone out). Failed logins back off per client and device-wide (no hard lockout). Server mode (OIDC) is unchanged
- Requests made directly by a process on the device (loopback, without the forwarding headers the reverse proxy adds) need no login, so `mqttutil` and `tsconfig-ble` keep working by calling `127.0.0.1:8000` directly; anything arriving through Caddy is authenticated
- Staff login: `root` with root's password is accepted on the web login form, on WebDAV (HTTP Basic) and by the BLE Authenticate characteristic (optional `"username"`, default `"pi"`), in addition to `pi`. Root's password is verified with `su -c true root` under a pty, since `pi` cannot verify another account through PAM; a locked root (no `TSOS_ROOT_PASSWORD`) simply fails, so the feature needs no setting. Root gets stricter, independent login throttling, at most two concurrent `su` checks, and control characters in its password are refused. Logging in as root grants no extra rights
- New `GET /auth/check` for the reverse proxy's `forward_auth`, used to protect Filebrowser (`/data`). For WebDAV (`/data/dav/`) it also accepts `Authorization: Basic` with the `pi` password, verified through PAM with the same backoff and a 60-second in-memory cache of verified credentials; Basic is accepted nowhere else
- BLE gateway: gated characteristics (writes, logs, the systemd service list) require an encrypted link (BlueZ Just Works pairing agent, `encrypt-*` GATT flags) and a per-connection login with `pi`'s password through the new Authenticate characteristic `00001005-...`; the old `check_pairing` stub is gone. `--no-pairing` is now an alias of `--no-auth`. The login ends on disconnect
- New `hotspot.nmconnection` bundle member for the access-point profile. A bundle's `[connection]` section and `ssid` are ignored (the on-disk ones win), the profile is written root-only (0600), loaded into NetworkManager, and rolled back if NetworkManager rejects it. The profile is never served through the API and cannot be deleted through `delete-config`. New privileged op `stat-config` (existence and mtime only) lets the "bundle is newer" check work for it despite the root-only directory
- A login over HTTPS (`X-Forwarded-Proto: https` from the reverse proxy) gets the `Secure` session cookie `__Host-tsconfig_session`, which browsers never send over plain HTTP; logins over HTTP keep `tsconfig_session`. Either cookie is accepted, and logout clears both

### Added

- `solarlife` and `smartsolar` (the SolarLife and Victron SmartSolar daemons) are in the default services list, in expert mode, so they can be inspected and restarted like `mqttutil`; they have no configuration page.
- BLE login with pi's password hash: `{"hash": "$6$..."}` on the Authenticate characteristic logs in as `pi` if it equals pi's entry in `/etc/shadow`, i.e. with the `userconf.txt` line of the config bundle applied to the device, as long as the password has not been changed since. Only for `pi`; throttled together with the password login. `test_ble_client.py` takes `--userconf FILE` / `--hash HASH`. The gateway reads shadow, so it must run as a user in the `shadow` group (`tsconfig-ble` in tsOS-base)
- BLE protocol reference: a section on device identity (use the serial) and on reflashing (tsOS-base gives each install its own Bluetooth address) with how to recognise and handle a stale bond
- BLE protocol reference for client developers (`docs/ble-protocol.md`) and the design of a possible protocol v2 (`docs/ble-protocol-v2-design.md`, proposal only). The README's BLE usage and protocol sections, which described an old format, now link to them
- `GET /api/systemd/logs/{service}` takes `follow=false` (a bounded plain-text answer, no stream) and `lines` (1 to 5000, default 200)
- BLE config upload takes optional `mtime`, `force` and `encoding` (`base64` or `utf-8`)
- Verification of file modification times (`mtime`) and filesystem synchronization (`sync`) on configuration updates

### Changed

- The default `configs/mqttutil.conf` no longer has a `[solarlife]` task: SolarLife controllers are read by the `solarlifed` daemon (tsOS-base `solarlife.service`), and `solarlife.client.read_device()` no longer exists
- `test_ble_client.py` no longer overwrites the device's config files by default: the config upload is sent with an old `mtime` and expects the refusal, the zip upload and real uploads need `--write-destructive`; the service action test restarts `mqttutil` instead of `chrony`
- The web shell now starts in the home directory of the user the service runs as (was the tsconfig source directory)
- BLE error responses for missing authorization are now `{"error": "Authentication required", "code": 401}` (was `Pairing required` / 403)
- Consistent log-level policy (documented in README): INFO is limited to one-off events. HTTP access logs and httpx/httpcore client logs are now DEBUG-only (override with `ACCESS_LOG_LEVEL`); per-request token/auth INFO messages and BLE write messages moved to DEBUG; stray `print()` calls in the network and soundscapepipe routers now use the logger
- Restrict 'maintenance' schedule entries to fixed clock times (HH:MM) and disable astronomical relative references
- Allow selecting and retaining 'No output device' (disabled) in the Soundscapepipe UI even when no output hardware is detected or list of devices is empty. The Expert Mode field is left blank when disabled, resolving to 'none' in memory and completely omitted from `soundscapepipe.yml` on disk (Issue #5)
- The operator account name and home directory (`pi`, `/home/pi`) are defined once in `app/identity.py` instead of being repeated across the code; no behaviour change.

### Fixed

- BLE config upload (`00003001`) works again: it passed a `config_group` argument the API client does not have (every upload failed) and never sent the `mtime` the API requires; it now forwards `mtime` (default: now) and `force`. HTTP errors of the API reach the BLE client with their status code and reason (e.g. a refused older file) instead of a generic server error
- BLE service logs (`00002004`) answer: the endpoint was an endless `journalctl -f` stream that the gateway could only time out on; the gateway now asks for `follow=false` and gets the last `lines` lines (1 to 1000)
- A systemd action that polkit denies (a configured service that is not on the polkit allow list, such as `chrony`) now answers `403` with systemd's message instead of `500`
- Prevent writing empty or unset optional fields (e.g. `disk_reserve_mb` being written as `null`/`None`) to `soundscapepipe.yml` by filtering none values on model serialization (Issue #6)
- BLE gateway: the device is found reliably when scanning. It advertised at the kernel default of 1280 ms, so a scanner with a 3-10 s timeout missed it in most attempts (7-9 of 40 3-second scans found it on a Pi 3 and a Pi 5); it now advertises at 100 ms (`MinInterval`/`MaxInterval` of the LE advertisement; 37-40 of 40)
- BLE gateway: one aborted or malformed write no longer disables the write characteristics until the service is restarted. A request that arrives in several writes was collected in one buffer per characteristic that never emptied, so after a client disconnected mid-upload every later request, from any client, was silently ignored. The buffer is now per connection and dropped when it disconnects or has been idle for 15 s; a request that can never become valid JSON (or exceeds 2 MiB) is answered with an error (`400`/`413`) instead of being waited on, and the discard is logged
- BLE gateway: it registers itself with BlueZ again when BlueZ loses it. After a bluetoothd restart (or a released advertisement) the service stayed `active` but no longer advertised, so the device could not be found until the service was restarted by hand. A supervisor now re-registers the GATT application, advertisement and pairing agent when bluetoothd returns or the advertisement is released, checks every 10 s that BlueZ still advertises, retries with a growing delay, and pings the systemd watchdog only while it is healthy or recovering. It also starts when the API is not reachable yet (requests fail until it is) instead of exiting
- BLE gateway: a slow or unreachable API no longer stalls the whole gateway. Requests ran synchronously on the single main loop, so one request waiting for the API (up to its 30 s timeout) blocked every other connection and D-Bus call (a call into the gateway took 29.9 s instead of 0.01 s). They now run on a background event loop with one shared HTTP client; a write returns at once and its response follows as notifications, requests of one connection stay in order, a response is dropped if its connection closed meanwhile, and a read the API does not answer within 4 s (BlueZ gives up after about 5 s) is answered with a "timed out" error in the metadata
- BLE gateway: responses are chunked to the connection's real MTU instead of always 20 bytes (a 2.6 kB status read took 133 notifications, now 6 at MTU 517). The MTU is taken from the options BlueZ passes with each call (the old code asked for a `Device1.MTU` property that does not exist) and chunks are capped at 512 bytes, the largest attribute value: BlueZ reports an MTU of 517 but trims every notification to 512 bytes

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
