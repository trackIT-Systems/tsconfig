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
- Failed config saves no longer send the Python traceback to the client (it is still logged)
- A login over HTTPS (`X-Forwarded-Proto: https` from the reverse proxy) gets the `Secure` session cookie `__Host-tsconfig_session`, which browsers never send over plain HTTP; logins over HTTP keep `tsconfig_session`. Either cookie is accepted, and logout clears both
- Mountpoints on the status page are rendered as text, not HTML: a mount path containing markup was inserted unescaped. Their Filebrowser links encode the path
- Tracker mode refuses state-changing requests (POST, PUT, PATCH, DELETE) and web shell connections whose `Origin` names another site (403), in addition to the `SameSite=Lax` session cookie. Requests without `Origin` (curl, mqttutil, the BLE gateway) are not affected; the same device over HTTP and HTTPS counts as one origin

### Added

- `solarlife` and `smartsolar` (the SolarLife and Victron SmartSolar daemons) are in the default services list, in expert mode, so they can be inspected and restarted like `mqttutil`; they have no configuration page.
- CI workflow `test.yml`: ruff (check and format, pinned to 0.16.10) and the tests, once with the tsOS image's Python 3.13 and library versions (`.github/device-versions.txt`) and once with current releases. The `dev` dependency group has `httpx2`, which Starlette's test client prefers since 1.7 (it warned when falling back to `httpx`)
- Frontend tests with Node's test runner (`node --test 'tests/js/*.test.mjs'`, a `frontend` CI job): the shared component code against a fake browser, what each settings page sends, and that every component builds
- BLE login with pi's password hash: `{"hash": "$6$..."}` on the Authenticate characteristic logs in as `pi` if it equals pi's entry in `/etc/shadow`, i.e. with the `userconf.txt` line of the config bundle applied to the device, as long as the password has not been changed since. Only for `pi`; throttled together with the password login. `test_ble_client.py` takes `--userconf FILE` / `--hash HASH`. The gateway reads shadow, so it must run as a user in the `shadow` group (`tsconfig-ble` in tsOS-base)
- BLE protocol reference: a section on device identity (use the serial) and on reflashing (tsOS-base gives each install its own Bluetooth address) with how to recognise and handle a stale bond
- BLE protocol reference for client developers (`docs/ble-protocol.md`) and the design of a possible protocol v2 (`docs/ble-protocol-v2-design.md`, proposal only). The README's BLE usage and protocol sections, which described an old format, now link to them
- `GET /api/systemd/logs/{service}` takes `follow=false` (a bounded plain-text answer, no stream) and `lines` (1 to 5000, default 200)
- BLE config upload takes optional `mtime`, `force` and `encoding` (`base64` or `utf-8`)
- Verification of file modification times (`mtime`) and filesystem synchronization (`sync`) on configuration updates

### Changed

- The code follows ruff 0.16's default rules (`ruff check` and `ruff format` are clean). Broad `except Exception` handlers now either catch the specific exceptions or log the traceback; failures that used to be logged as a one-line message now show where they happened
- Local times in API responses carry their UTC offset: `current_datetime` of `/api/system-status` and the `timestamp` fields of `/api/timedatectl-status`, `/api/network-connectivity` and config uploads (e.g. `2026-10-16T13:36:25+02:00`). The status page now shows the device time correctly in a browser in another time zone. Service uptimes are computed in UTC, so they stay correct across DST changes
- Shared BLE test fixtures live in `tests/conftest.py` and `tests/ble_helpers.py`
- On a tracker the main `tsconfig.yml` is read from `/etc/tsconfig/tsconfig.yml` (set by `TSCONFIG_CONFIG_FILE`) instead of `/boot/firmware/`; `file_locations.config_dir` inside it is unchanged.
- The default `configs/mqttutil.conf` no longer has a `[solarlife]` task: SolarLife controllers are read by the `solarlifed` daemon (tsOS-base `solarlife.service`), and `solarlife.client.read_device()` no longer exists
- `test_ble_client.py` no longer overwrites the device's config files by default: the config upload is sent with an old `mtime` and expects the refusal, the zip upload and real uploads need `--write-destructive`; the service action test restarts `mqttutil` instead of `chrony`
- Configs of services that are not installed on the device are refused: an upload (zip bundle, single file, `tsconfig zip`) containing e.g. `soundscapepipe.yml` on a device without `soundscapepipe.service` is rejected as a whole with 400 and nothing is written. A service-bound config (radiotracking, soundscapepipe, schedule, tsupdate, mqttutil, mosquitto, wireguard) needs its systemd unit; base-system configs (authorized_keys, cmdline.txt, ...) are always accepted. A system reset skips such files instead of failing. `/api/available-services` and the config listing and bundle download leave them out, so a stale file no longer shows a tab and a downloaded bundle uploads again ([#16](https://github.com/trackIT-Systems/tsconfig/issues/16))
- Settings pages follow the same rule: saving the config of a service that is not installed is refused (400), and the page no longer includes the tab markup for it
- The authentication middleware and the proxy-headers middleware are pure ASGI middleware instead of `BaseHTTPMiddleware`. The login check now also covers websocket handshakes (the web shell keeps its own check as well): an unauthenticated or cross-origin handshake is refused before it is accepted. The application's responses, including the log stream and its disconnect detection, no longer pass through a per-request task. The unused `request.state.base_url` middleware is gone
- Dependency declarations match what the code imports: `starlette`, `pydantic` and `anyio` are declared (they are imported directly), the OIDC libraries are the `server` extra and the BLE gateway's are the `ble` extra (instead of dependency groups; `pip install "tsconfig[server]"`, `pdm install -G server`), and the test tools are the `dev` group. `tests/test_dependencies.py` fails if an imported module is not declared. The Dockerfile installs `.[server]`, `pdm.lock` is regenerated, and the README's install steps are current (Python 3.12, no reference to a script that no longer exists)
- Web UI: the settings pages share their common behaviour (`mixins/configPageMixin.js`, built from `lifecycleMixin`, `toastMixin`, `serverModeMixin` and `serviceLogsMixin`): the status box of the service, Save, Save & Restart, Deploy, toasts and the log buttons. About 1,350 lines of per-page copies are replaced by about 400 lines of shared code; server mode is read from `serverModeManager` instead of `window.serverModeManager` (still exposed for the navbar), and the unused `window` globals are gone. How it fits together and how to add a page: `docs/frontend.md`
- The application's own static files (JS, CSS) are sent with `Cache-Control: no-cache`, so a browser asks before reusing its copy and does not run old scripts against an updated API. Unchanged files answer 304; the vendored libraries stay cacheable
- The Mapbox token for the status page map is read from `tsconfig.yml` (`map.mapbox_token`) instead of being hard-coded in `statusPage.js`. A `tsconfig.yml` without a `map` section (a device's copy from an older image) uses the token shipped with the code; an empty token shows the OpenStreetMap street map instead of Mapbox satellite imagery
- The web shell now starts in the home directory of the user the service runs as (was the tsconfig source directory)
- BLE error responses for missing authorization are now `{"error": "Authentication required", "code": 401}` (was `Pairing required` / 403)
- Consistent log-level policy (documented in README): INFO is limited to one-off events. HTTP access logs and httpx/httpcore client logs are now DEBUG-only (override with `ACCESS_LOG_LEVEL`); per-request token/auth INFO messages and BLE write messages moved to DEBUG; stray `print()` calls in the network and soundscapepipe routers now use the logger
- Restrict 'maintenance' schedule entries to fixed clock times (HH:MM) and disable astronomical relative references
- Allow selecting and retaining 'No output device' (disabled) in the Soundscapepipe UI even when no output hardware is detected or list of devices is empty. The Expert Mode field is left blank when disabled, resolving to 'none' in memory and completely omitted from `soundscapepipe.yml` on disk (Issue #5)
- The operator account name and home directory (`pi`, `/home/pi`) are defined once in `app/identity.py` instead of being repeated across the code; no behaviour change.
- Server SSH keys on the SSH keys page come from `/boot/firmware/authorized_keys`, which sshd now reads in place for `pi` (tsOS-base), instead of the `~/.ssh/authorized_keys2` copy. Keys from a bundle upload show up immediately

### Fixed

- The Updates page's Save sent the persist timeout and update countdown unconverted (only Save & Restart converted them from HH:MM), so changing them and pressing Save did not change the config
- Soundscapepipe's Save & Restart skipped the sync of the lure task times that Save does
- Pages no longer leave listeners behind when their tab is left: `config-group-changed` and the schedule page's `hashchange`/`popstate` handlers kept running for removed components
- Saving the schedule, updates or reporting settings showed every message as two toasts (one titled, one without)
- BLE config upload (`00003001`) works again: it passed a `config_group` argument the API client does not have (every upload failed) and never sent the `mtime` the API requires; it now forwards `mtime` (default: now) and `force`. HTTP errors of the API reach the BLE client with their status code and reason (e.g. a refused older file) instead of a generic server error
- BLE service logs (`00002004`) answer: the endpoint was an endless `journalctl -f` stream that the gateway could only time out on; the gateway now asks for `follow=false` and gets the last `lines` lines (1 to 1000)
- A systemd action that polkit denies (a configured service that is not on the polkit allow list, such as `chrony`) now answers `403` with systemd's message instead of `500`
- Prevent writing empty or unset optional fields (e.g. `disk_reserve_mb` being written as `null`/`None`) to `soundscapepipe.yml` by filtering none values on model serialization (Issue #6)
- BLE gateway: the device is found reliably when scanning. It advertised at the kernel default of 1280 ms, so a scanner with a 3-10 s timeout missed it in most attempts (7-9 of 40 3-second scans found it on a Pi 3 and a Pi 5); it now advertises at 100 ms (`MinInterval`/`MaxInterval` of the LE advertisement; 37-40 of 40)
- BLE gateway: one aborted or malformed write no longer disables the write characteristics until the service is restarted. A request that arrives in several writes was collected in one buffer per characteristic that never emptied, so after a client disconnected mid-upload every later request, from any client, was silently ignored. The buffer is now per connection and dropped when it disconnects or has been idle for 15 s; a request that can never become valid JSON (or exceeds 2 MiB) is answered with an error (`400`/`413`) instead of being waited on, and the discard is logged
- BLE gateway: it registers itself with BlueZ again when BlueZ loses it. After a bluetoothd restart (or a released advertisement) the service stayed `active` but no longer advertised, so the device could not be found until the service was restarted by hand. A supervisor now re-registers the GATT application, advertisement and pairing agent when bluetoothd returns or the advertisement is released, checks every 10 s that BlueZ still advertises, retries with a growing delay, and pings the systemd watchdog only while it is healthy or recovering. It also starts when the API is not reachable yet (requests fail until it is) instead of exiting
- BLE gateway: a slow or unreachable API no longer stalls the whole gateway. Requests ran synchronously on the single main loop, so one request waiting for the API (up to its 30 s timeout) blocked every other connection and D-Bus call (a call into the gateway took 29.9 s instead of 0.01 s). They now run on a background event loop with one shared HTTP client; a write returns at once and its response follows as notifications, requests of one connection stay in order, a response is dropped if its connection closed meanwhile, and a read the API does not answer within 4 s (BlueZ gives up after about 5 s) is answered with a "timed out" error in the metadata
- BLE gateway: responses are chunked to the connection's real MTU instead of always 20 bytes (a 2.6 kB status read took 133 notifications, now 6 at MTU 517). The MTU is taken from the options BlueZ passes with each call (the old code asked for a `Device1.MTU` property that does not exist) and chunks are capped at 512 bytes, the largest attribute value: BlueZ reports an MTU of 517 but trims every notification to 512 bytes
- Web shell: an open shell no longer stalls every other request. The terminal was polled with a blocking 100 ms `select()` on the event loop (API requests took up to 105 ms instead of 6 ms); it is now watched by the event loop. A closed shell gets SIGHUP (SIGKILL only if it ignores that) and is reaped instead of staying behind as a zombie. Output split inside a multi-byte character is no longer garbled. A second connection to a running session id is refused
- Log streams end when the browser disconnects. uvicorn drops writes to a closed connection silently, so a `journalctl -f` for a quiet service kept running indefinitely after its log viewer was closed (one per view)
- Web UI components were initialized twice (Alpine calls `init()` itself, and the templates called it again via `x-init`), which doubled requests and timers. Their cleanup was wired to a non-existent `x-destroy` directive and never ran, so every tab switch left another set of polling timers behind (after 6 switches, 137 instead of 36 requests per 25 s on the status page). Cleanup is now Alpine's `destroy()`, and the status page also stops its timer, service subscription and map
- Server mode: the token refresh on a `401` threw a `TypeError` (a URL string added to a `WeakSet`) instead of refreshing and retrying the request
- Server mode: OIDC login and token refresh no longer turn their own errors into `500`. An unavailable OIDC handler answers `503`, and a user who lost access gets `403 Access denied` on refresh instead of a server error
- Privileged operations that time out report the timeout. Once `pkexec` has made the process root, `pi` cannot kill it, so the timeout surfaced as a `PermissionError` and the stuck process ran on. The root process now ends itself after 60 s (longer than the caller's 30 s)
- A scheduled reboot can no longer be lost: its task was only weakly referenced by the event loop and could be garbage-collected before it ran. A failing `systemctl reboot` is now logged
- `/api/system-status` answers in a few milliseconds instead of about 0.57 s. It no longer blocks the server for 100 ms to measure CPU usage (it reports usage since the previous call), and asks `tsupdate status` (a 0.5 s Python start) once instead of on every 5 s poll, since the booted partition and the boot method do not change while the system runs
- The status page loads the radiotracking and soundscapepipe configs once per refresh instead of twice
- A downloaded config bundle carries `/boot/firmware/authorized_keys`, the file a bundle upload writes (copied to `~/.ssh/authorized_keys2` at boot). It carried the operator's own `~/.ssh/authorized_keys` instead, so a downloaded bundle did not upload back unchanged
- The web UI asks for `/api/server-mode`, `/api/available-services` and `/api/system-status` once on the first page load instead of twice each (10 instead of 13 requests). Components that are left while still loading no longer start their refresh timers afterwards, and a status page left during its map setup no longer breaks the map of the next one. SSH key service actions show their toast with the right type

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
