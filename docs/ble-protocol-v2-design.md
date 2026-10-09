# tsconfig BLE protocol v2: design proposal

**Status: proposal, not implemented.** The current protocol is documented in
[ble-protocol.md](ble-protocol.md). v2 was designed in October 2026 and shelved; the fixes that
were practical without a breaking change went into v1. This document keeps the design, its
decisions and the checks already done, so it can be picked up later.

## Why

v1 limits that cannot be fixed without breaking clients:

- Notifications go to every subscriber, and responses carry no request id.
- Requests are framed only by "the bytes parse as JSON", which needs heuristics for partial
  requests (`RequestParser.classify_json`).
- The `pi` or `root` password is sent in clear inside a Just Works link. An active man-in-the-middle
  during pairing gets it.
- Pairing and bonds cause problems on phones and on the Pi.
- Many characteristics, each with its own read/notify handling.

Alternatives considered:

- *PIN-based pairing (passkey)*: the device has no display, so the PIN would have to be fixed or
  printed. Old clients would be spared a change, but a static PIN is guessable and BlueZ
  passkey pairing is no better supported across phones than Just Works.
- *Keep v1 plus an encrypted password in the login*: still needs a key agreement and still leaves
  framing and broadcast problems.

Decision: a new protocol that replaces v1 once clients are updated, with a password-authenticated
key exchange (PAKE) so the password never crosses the air.

## Decisions

| Topic | Decision |
|---|---|
| PAKE | Balanced CPace (draft-irtf-cfrg-cpace, ristretto255 / SHA-512), keyed by the account's `$6$` sha512-crypt hash from `/etc/shadow`. The Pi has hashes only, never plaintext. Both `pi` and `root` work. |
| Pairing | None. Adapter `Pairable=False`, no agent, no `encrypt-*` flags. The key exchange is the authentication and the encryption. |
| v1 | Removed when v2 ships (clients are updated anyway because of the login change). |
| Hash format | Pinned to `$6$`; the protocol carries a KDF identifier so another format can be added later. Other formats get an explicit "unsupported kdf" error. |
| Before login | Only a hello. |

### Accepted consequence

The `$6$` hash becomes a BLE credential: whoever holds it can log in over BLE without the
password. That covers `userconf.txt` in config bundles (also the default bundle built by the
Pifile) and every image built with `TSOS_ROOT_PASSWORD` (one root hash and salt for the fleet).
Mitigations: treat such bundles and images as secrets; revoke by re-hashing the same password
with a new salt; an optional build flag `TSOS_BLE_STAFF=0` turns off the root login over BLE
(default on).

## Transport

- One GATT service `0000A000-7473-4f53-636f-6e6669672121`: RX `...A001` (write and
  write-without-response), TX `...A002` (notify). Clients subscribe to TX before sending HELLO.
- Every write or notification is one **segment**.
  - RX: `[flags u8][total_len u32be if START][data]`
  - TX: `[chan u32be][flags u8][total_len u32be if START][data]`
  - `flags`: bit 7 START, bit 6 END. START always resets reassembly, so leftovers of an aborted
    message cannot corrupt the next one.
- `chan` is chosen at random by the client in HELLO. Notifications reach every subscriber, so each
  client drops TX segments that are not its own. `chan` is first in the segment so it survives
  even when a copy is cut to a smaller MTU. TX segments are sized per connection:
  `min(mtu - 3, 512)`.
- Long writes: BlueZ passes `offset`; an offset equal to the bytes already received continues the
  segment, anything else is rejected.
- Message after reassembly: `[ver u8 = 2][type u8][body]`. Before login the body is strict
  canonical CBOR of at most 512 bytes. After login, type `0x10 DATA` carries
  `ChaCha20-Poly1305-IETF(k_dir, nonce = 0^4 || u64be(counter), aad = [ver, type], CBOR)`.
  Counters are implicit, start at 0 per direction and must match exactly.
- Limits: 1 MiB per message after login, 15 s idle timeout, any parse or AEAD failure ends the
  session.

## Handshake

Helpers: `lv_cat(...)` prefixes each argument with its LEB128 length (as in the CPace draft).
`PRS = lv_cat("sha512-crypt", <86-char digest after the last '$'>)`. Only the digest is used, which
avoids `rounds=5000$` normalisation differences between implementations.

1. `HELLO` (client to gateway): `{v:[2], suites:["cpace-ristretto255-sha512"], kdfs:["sha512-crypt"], chan, nc: 16 B}`
2. `HELLO_RESP`: `{v:2, suite, kdf, hostname, serial, ns: 16 B}`.
   `sid = SHA512(lv_cat("tsOS-BLE-v2/sid", HELLO_bytes, HELLO_RESP_bytes))[:32]`, so every hello
   field is bound and a downgrade is detected.
3. `AUTH_START`: `{user}` with user `pi` or `root`.
   The gateway applies its throttles (a semaphore of 2 concurrent PAKE calls, a token bucket,
   BUSY with `retry_after`) and calls the privileged op `ble-pake-start <user>` (stdin `{sid, serial}`).
   As root it reads shadow, checks the root-side throttle, computes
   `CI = lv_cat("tsOS-BLE-v2/CI", "user="+user, "serial="+serial)`,
   `ADb = lv_cat("tsOS-BLE-v2/B", setting)`, `g = CPace.calculate_generator(PRS, CI, sid)`,
   `yb`, `Yb`, stores `yb` under a random single-use `state_id` (expires after 60 s) and returns
   `{setting, Yb, state_id}`.
4. `AUTH_PARAMS`: `{kdf, setting, Yb}`.
5. The client computes PRS with sha512-crypt(password, setting) (refusing `rounds=` above
   5,000,000), `ADa = lv_cat("tsOS-BLE-v2/A", user, suite, kdf)`, `g`, `ya`, `Ya`; aborts if
   `Ya == Yb`; derives `K`, `ISK` and the tag `Ta`; sends `AUTH_FINISH {Ya, Ta}`.
6. The gateway calls `ble-pake-finish <user>` (stdin `{state_id, Ya, Ta}`). As root it takes the
   state, rejects `Ya == Yb`, identity and invalid points, checks `Ta` in constant time,
   records the failure on mismatch, and returns `{ok:false}` or `{ok:true, isk, Tb}`. The
   gateway never holds a key for a failed login and root sees every success and failure.
7. `AUTH_OK {Tb}` (client verifies `Tb`), or `ERROR {code: 401 or 429, retry_after}`.
8. `k_c2s`, `k_s2c` = `HKDF-SHA512(ISK, salt=sid, info="tsOS-BLE-v2 c2s" / "... s2c")`.

CPace on ristretto255 / SHA-512: `DSI = b"CPaceRistretto255"`; generator string
`lv_cat(DSI, PRS, zpad, CI, sid)` hashed with SHA-512 and mapped with `from_hash`;
`ISK = SHA512(lv_cat(DSI+"_ISK", sid, K) || lv_cat(Ya,ADa) || lv_cat(Yb,ADb))` ordered by role;
`mac_key = SHA512("CPaceMac" || sid || ISK)`; `Ta`, `Tb` = `HMAC-SHA512(mac_key, lv_cat(Y, AD))`.
Reflection is prevented by role-labelled ADa/ADb and the `Ya != Yb` check. The formulas must be
checked against the pinned draft revision and its test vectors when implementing.

Accounts that cannot authenticate (locked `!`/`*`/`!$6$...`, empty, expired, forced change) get a
**decoy**: a fake setting derived by HMAC from a root-only key, and the full exchange still runs, so
the outcome looks like a wrong password. A `$y$` or other format gets "unsupported kdf" (501).

## Features that follow from keying the exchange by the hash

These are intended behaviour, not side effects. Clients and the gateway should implement them.

### Login with the hash from a config bundle (no password needed)

The key exchange needs only the setting (salt and rounds) and the digest of the `$6$...` hash.
Both are in the bundle's `userconf.txt`. A client that holds the bundle can therefore log in
without asking for the password and without running sha512-crypt: it takes the setting and digest
from the bundle, builds PRS and continues with step 5 of the handshake.

- This works only while the device's hash in `/etc/shadow` is exactly the bundle's string,
  including the salt. It holds if the bundle was applied unchanged and the password was not
  changed on the device since (to be verified: whether tsOS copies the hash from `userconf.txt`
  into shadow unchanged).
- If the password was changed on the device, the login fails with 401. The client then asks for
  the password.
- A failed try counts toward the root-side backoff. A client tries the bundle hash **once**, then
  falls back to asking for the password.
- A client that stores bundles holds login credentials: it must treat them as secrets, like
  passwords (see "Accepted consequence").

### Changing the password through a bundle

The device only knows its current shadow entry, so the login always uses the **current**
(old) password, or the hash of the bundle if it is still the device's hash:

1. Log in with the old password.
2. Upload the bundle over the encrypted session; the new hash in `userconf.txt` travels inside the
   encrypted payload.
3. The device applies it and the password changes.
4. The client reconnects and logs in with the new password.

Rules for the gateway:

- **All BLE sessions end when a bundle that changes `userconf.txt` is applied**, so a session
  opened with the old password does not outlive it. The response to the upload is sent first.
- When the new password takes effect depends on how tsOS applies `userconf.txt` (not checked yet).
  If it only happens at boot, the old password stays valid until a reboot, and clients must expect
  that.
- A client that only knows the new password while the device has the old one cannot log in over
  BLE (401). The recovery is the bundle on the boot partition or another login path (SSH as root).

## Session and operations

- A session belongs to one BlueZ device path and ends on disconnect, `session.logout`, an AEAD or
  counter failure, after 8 hours, or when a bundle that changes `userconf.txt` has been applied.
- Request `{i:id, o:op, a:args}`; response `{i, c:code, b:body}` or `{i, c, e:error}`.
- Operations: `system.status`, `system.server_mode`, `system.timedatectl`, `services.available`,
  `systemd.list`, `systemd.action {service, action}`, `system.reboot`,
  `logs.get {service, lines <= 1000}`, `config.upload {filename, content: bytes, mtime, force,
  restart_service}`, `config.zip_upload {content: bytes, restart_services, pedantic, force,
  reboot}`, `session.logout`.
- Error codes: 400, 401, 404 (unknown op), 409 (busy or channel collision), 413, 429 (+`retry_after`),
  500, 501 (unsupported kdf).

## Implementation outline (all under `usr/local/src/tsconfig/`)

0. **Spikes on hardware** (go/no-go), see below.
1. **Crypto core**, pure modules without dbus or FastAPI: `app/crypto/sodium.py` (ctypes binding to
   `libsodium.so.23`: `from_hash`, `scalar_random`, `scalarmult` (a nonzero return is an
   exception), `is_valid_point`, AEAD; in the style of `app/auth/pam_auth.py`), `cpace.py`
   (`lv_cat`, generator, ISK, tags, tsOS-specific sid/CI/AD), `kdf.py` (HKDF-SHA512, crypt setting
   parsing), `sha512crypt.py` (pure Python, for the client and tests). Tests: CPace draft vectors,
   RFC 5869, Drepper's sha512-crypt spec, cross-check with `openssl passwd -6`.
2. **Root side**: `app/privileged/pake.py` (shadow parsing as root, since Python 3.13 has no
   `spwd`; decoy key `/var/lib/tsconfig-privileged/pake-decoy.key`; state in `/run/tsconfig-pake/`
   under `flock`, at most 8 pending states of 60 s; per-account limiter in the style of
   `BackoffLimiter` in `app/auth/session.py`: root 5 free failures then backoff up to 60 s, pi 10
   then up to 10 s; journal logging), subcommands `ble-pake-start` / `ble-pake-finish` in
   `app/privileged/cli.py` (binary fields as base64 in JSON, lazy imports), wrappers in
   `app/utils/privileged.py` (timeout about 10 s).
3. **Protocol core** `app/bluetooth/v2/`: `framing`, `messages`, `session`, `handshake` (state
   machine with an injected `PakeBackend`; reuse `login_retry_after`, `record_login_failure` and
   `record_login_success` from `app/auth/session.py`, a 30 s handshake deadline), `pake_backend`,
   `dispatch`, `connection`. Requests run through `executor.submit(key=device)`, sealing happens
   on the GLib main thread, epoch and disconnect handling are ported from
   `gatt_characteristics.py`.
4. **GATT glue**: `transport.py`; remove the pairing agent, `setup_pairing`, `require_auth`;
   `Pairable=False`; keep the `Supervisor` and the 100 ms advertising; delete the v1 services,
   characteristics and auth; drop `--no-auth`.
5. **Reference client** `app/bluetooth/v2/client.py` (bleak), a golden transcript
   `tests/vectors/ble_v2_transcript.json` as conformance vector for the mobile clients, a
   rewritten `test_ble_client.py`, and the protocol specification.
6. **Image** (`tsOS-base.Pifile`): `python3-nacl` and `libsodium23` installed explicitly,
   `ENCRYPT_METHOD SHA512` in `/etc/login.defs`, `yescrypt` replaced by `sha512` in
   `/etc/pam.d/common-password` and `/usr/share/pam-configs/unix` (so `pam-auth-update` does not
   revert it), optional `TSOS_BLE_STAFF`.

Reusable as is: `app/bluetooth/executor.py`, `supervisor.py`, `gatt_server.py` (registration,
disconnect signal, advertisement), the 512-byte cap and per-connection MTU handling, the limiters
in `app/auth/session.py`, the `tsconfig-privileged` / polkit chain, `TsConfigApiClient`, `cbor2`.

## Checks already done (Raspberry Pi 3, read-only)

- libsodium 1.0.18 provides every ristretto255 function needed, and ChaCha20-Poly1305.
- PyNaCl 1.5 does **not** expose the ristretto255 functions, hence the ctypes binding.
- Python starts in about 0.3 s (matters for the two privileged calls per login).
- `pam_unix` in `common-password` uses `yescrypt`, so `passwd pi` would write a `$y$` hash that v2
  cannot use until PAM is changed.

## Open spikes

1. CPace test vectors reproduce through ctypes against libsodium.
2. pkexec round-trip time of a stub op on a Pi 3 and a Pi 5.
3. With two centrals: notifications are broadcast, each copy is cut to its MTU, `AcquireNotify` behaves the same.
4. Write `type` and `offset` behaviour for long writes and write-without-response bursts.
5. With no agent and `Pairable=False`: no pairing prompt appears; a phone that still holds an old bond behaves sanely.
6. btmon: does the advertisement have room for a protocol-version byte in the service data?
7. After the PAM and `login.defs` change, `passwd pi` writes a `$6$` hash.

## Verification when implemented

Unit tests for vectors, framing fuzz, session replay and tamper, the handshake state machine
(downgrade, replay, reflection, 429, BUSY, decoy), the privileged PAKE against a temporary shadow
file, and every operation. On Pi 3, 4 and 5: both logins, 401 then growing 429, locked root
indistinguishable from a wrong password, a `$y$` account answering 501, local brute force through
`pkexec` hitting the root-side backoff, tampered or replayed frames closing the session, two clients
at once without cross-talk, a 1 MiB zip upload (write-without-response and long write), abort and
reconnect, all operations, the resilience checks of v1 (bluetoothd restart, tsconfig restart,
frozen API, watchdog), no pairing request in btmon, and a login time under 3 s on a Pi 3.
