# tsconfig BLE protocol (v1)

Reference for developers of BLE clients (phone apps, scripts). It describes what the gateway
(`app/ble_gateway.py`) does today. `test_ble_client.py` is a working example (Python, bleak).
A proposed successor is in [ble-protocol-v2-design.md](ble-protocol-v2-design.md); it is not implemented.

## Discovery

- The gateway advertises the 16-bit service UUID `0x7473` ("ts") and the hostname as local name.
- The advertising interval is 100 ms. Scan for at least 5 s and retry; a missed scan is normal.
- A client should scan for the UUID or the name, not for a fixed address.

## UUIDs

Base UUID `0000XXXX-7473-4f53-636f-6e6669672121`. Services are `0xS000`, characteristics `0xSCCC`.

| Service | Characteristic | UUID | Access | Login | API endpoint |
|---|---|---|---|---|---|
| System `1000` | Status | `1001` | read | no | `GET /api/system-status` |
| | Server mode | `1002` | read | no | `GET /api/server-mode` |
| | Timedatectl | `1003` | read | no | `GET /api/timedatectl-status` |
| | Available services | `1004` | read | no | `GET /api/available-services` |
| | Authenticate | `1005` | write | (is the login) | PAM check |
| Systemd `2000` | Services list | `2001` | read | yes | `GET /api/systemd/services` |
| | Service action | `2002` | write | yes | `POST /api/systemd/action` |
| | Reboot | `2003` | write | yes | `POST /api/systemd/reboot` |
| | Logs | `2004` | write | yes | `GET /api/systemd/logs/{service}?follow=false` |
| Upload `3000` | Config upload | `3001` | write | yes | `POST /api/configs/update` |
| | Zip upload | `3002` | write | yes | `POST /api/configs.zip` |

Every characteristic also supports notify; responses arrive there.

## Link and login

- Characteristics other than the four status reads are exposed with BlueZ's `encrypt-*` flags, so
  the OS pairs on first access (LE Just Works, no PIN). This only protects against passive sniffing.
- Per connection, write `{"password": "...", "username": "pi"}` to Authenticate (`username` optional,
  `pi` or `root`). The reply is a notification: `{"authenticated": true}` or an error with code
  `401` (wrong password) or `429` (backing off, `retry_after` in seconds).
- Instead of the password, a client can send pi's password hash: `{"hash": "$6$..."}` (`username`
  optional, only `pi`). It must equal pi's entry in `/etc/shadow` exactly, so it is the hash from the
  `userconf.txt` of the config bundle applied to the device, and it works only as long as the password
  has not been changed since. It is a convenience for the common case: try the hash **once**, and on
  `401` ask for the password. Failed hash and password attempts count toward the same backoff.
  Errors: `400` for `root`, for `hash` together with `password`, or a hash that is not a string.
- Until then gated characteristics answer `{"error": "Authentication required", "code": 401}`.
- The login ends on disconnect, after 8 hours or when the gateway restarts.

### Changing pi's password with a bundle

`userconf.txt` is applied at boot, so the device keeps its current password until it reboots:

1. Log in with the current password (or the hash of the bundle applied last).
2. Upload the bundle with the new `userconf.txt`.
3. Reboot (for example through the Reboot characteristic).
4. Log in with the new password, or the new bundle's hash.

## Device identity and reflashing

- **Identify a device by the serial in the service data** (`0x7473`, the last 4 bytes of the board's
  serial; the hostname `tsos-base-<serial>` is derived from the same serial). Do not use the name,
  the Bluetooth address or the UUID the OS shows for it: the name is only a label, and the address
  changes (below).
- **The Bluetooth address belongs to the install.** At every boot the device gives its controller the
  vendor prefix of the board plus three random bytes that are stored next to the pairing keys on the
  SD card (`tsos-bluetooth-address.service`). After a reflash the keys are gone and the address is
  new, so a phone sees a new device, pairs afresh, and the bond to the old address just stays unused.
  Phones key their bond by address, so this avoids a stale bond.
- **A stale bond can still occur** (for example after only the key store was deleted, or on a device
  without this mechanism). It shows as a failed connection or a failed access to an encrypted
  characteristic: iOS and macOS report `CBError.peerRemovedPairingInformation` ("Peer removed pairing
  information") at connect, Android a GATT status of 5 or 15 (insufficient authentication or
  encryption) or a bond state change to none. iOS cannot remove a bond from an app: tell the user to
  forget the device in the Bluetooth settings. Android can remove it (the hidden `removeBond`) and
  pair again.
- A card that was cloned after its first boot gives all clones of one board type the same address;
  flash the image fresh instead.

## Reading

1. Subscribe (notify) to the characteristic.
2. Read it. The value is CBOR metadata with integer keys: `1` content length, `2` number of chunks,
   `3` content type (0 json, 1 text, 2 binary), `4` status (0 ready, 1 error), `5` error message.
3. The data arrives as `chunks` notifications, to be concatenated in order. There is no framing
   inside the notifications. The result is a UTF-8 JSON document.

The reply to the read is sent when the API has answered (at most 4 s, then status 1 with a
timeout message), and the data notifications follow it.

## Writing

- The request is a UTF-8 JSON object. Write it in one or more writes (without-response is fine).
  A request longer than one write is detected by "the bytes so far are a prefix of valid JSON".
  The gateway discards a partial request after 15 s without a write or beyond 2 MiB; a request
  that can never become valid JSON (garbage, leftovers of an aborted request) is rejected at once
  with code 400.
- The response is a JSON document delivered as notifications, like read data but without the
  metadata step. A failure is `{"error": "<message>", "code": <http-style code>}`.
- Requests of one connection run in order. Responses carry no request id.

### Requests

| Characteristic | Request | Response |
|---|---|---|
| Service action `2002` | `{"service": "...", "action": "start\|stop\|restart\|reload"}` | API result. `400` unknown service or action, `403` the service is not allowed for the `pi` user (polkit), `500` systemctl failed. |
| Reboot `2003` | `{}` | API result |
| Logs `2004` | `{"service": "...", "lines": 100}` (`lines` 1 to 1000, default 100; service `all` and `kernel` also work) | `{"logs": "<text>"}`, the last lines, not a stream |
| Config upload `3001` | `{"filename": "...", "content": "...", "mtime": "2025-10-17T08:59:50", "force": false, "restart_service": false, "encoding": "base64"}` | API result. `content` is base64 when `encoding` is `"base64"`, text when `"utf-8"`; without `encoding` valid base64 is decoded and anything else is taken as text. `mtime` (ISO, UTC) defaults to the current time. Without `force` the API refuses a file older than the one on the device (`4xx` with the reason). |
| Zip upload `3002` | `{"filename": "...", "content": "<base64 zip>", "restart_services": false, "pedantic": false, "force": false, "reboot": false}` | API result |

API errors are passed on with their HTTP status as `code` and their `detail` as `error`.

## Sizes

- Notifications are at most 512 bytes (`min(mtu - 3, 512)`; BlueZ reports an ATT MTU of 517 and
  cuts longer values to 512). Without an MTU exchange the chunk size is 20 bytes.
- Request limit 2 MiB. Writes of more than the MTU are split by the client; Android and iOS do
  this themselves for write-with-response ("long write").

## Known limits (what v2 would address)

- Notifications go to every subscribed connection. With two clients connected, each receives the
  other's responses (ignore a response you did not ask for; better, use one client at a time).
- Responses carry no request id, so only one request per connection may be in flight.
- The password, or the hash, is sent in clear inside a Just Works link, which resists only passive
  sniffing. A fake device or someone intercepting the pairing can capture it. A captured hash logs in as
  `pi` over BLE on every device with the same `userconf.txt` until the password is re-hashed with a new
  salt, and it allows offline guessing of the password. Treat bundles that contain `userconf.txt` as
  secrets.
- Reading a characteristic costs two steps (read metadata, then notifications).
- Logs are bounded text, there is no live stream.

## Resilience of the gateway

- It re-registers with BlueZ after `bluetoothd` restarts and keeps running (and connected) when
  `tsconfig` restarts. A frozen gateway is restarted by systemd's watchdog after about 60 s.
- A client that disconnects mid-request leaves nothing behind: the next connection starts clean.
