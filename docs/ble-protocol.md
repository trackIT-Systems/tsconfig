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
- Until then gated characteristics answer `{"error": "Authentication required", "code": 401}`.
- The login ends on disconnect, after 8 hours or when the gateway restarts.

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
- The password is sent in clear inside a Just Works link, which resists only passive sniffing.
- Reading a characteristic costs two steps (read metadata, then notifications).
- Logs are bounded text, there is no live stream.

## Resilience of the gateway

- It re-registers with BlueZ after `bluetoothd` restarts and keeps running (and connected) when
  `tsconfig` restarts. A frozen gateway is restarted by systemd's watchdog after about 60 s.
- A client that disconnects mid-request leaves nothing behind: the next connection starts clean.
