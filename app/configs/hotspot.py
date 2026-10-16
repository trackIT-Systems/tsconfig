"""hotspot.nmconnection configuration (the device's WiFi access point profile).

The bundle (config zip) may carry a NetworkManager keyfile for the `hotspot`
connection, which is how a server sets the hotspot password (and optionally
other hotspot properties such as band/channel or the IPv4 shared settings).

Two parts of the file are never taken from the bundle - the on-disk version
wins:

- the whole `[connection]` section (connection id, UUID, interface name,
  autoconnect policy, ...), so a bundle cannot turn the hotspot profile into
  something else, and
- `ssid` in `[wifi]`, which etc/hostname.sh derives from the device's hostname
  at every boot.

The file lives in a root-owned (0600) directory, so `pi` can neither read nor
stat it; everything that touches it runs as root through the privileged
`write-config` op. This is also why `load()` returns nothing for `pi`: the
password never leaves the device through tsconfig's API.

Applying a changed profile reloads it into NetworkManager and re-activates
the hotspot if it is up (which drops connected clients). If NetworkManager
rejects the new file, the previous one is restored, because the hotspot is the
device's recovery path.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from app.configs import BaseConfig

NM_CONNECTION_DIR = Path("/etc/NetworkManager/system-connections")
CONNECTION_NAME = "hotspot"
FILE_NAME = "hotspot.nmconnection"

MAX_CONTENT_BYTES = 8192

# nmcli exit status when the NetworkManager daemon is not running (e.g. while
# the image is being built in a chroot): nothing to reload, not an error.
_NMCLI_NOT_RUNNING = 8
_NMCLI_TIMEOUT = 20.0

_SECTION_RE = re.compile(r"^\[([A-Za-z0-9_.:-]+)\]\s*$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_.:-]+)\s*=(.*)$")

Section = tuple[str, list[str]]  # (name, raw lines including the "[name]" header)


def _parse_sections(content: str) -> tuple[list[Section], list[str]]:
    """Split keyfile text into sections, preserving lines verbatim.

    Returns (sections, errors). Only a strict, simple subset is accepted:
    comments and blank lines, `[section]` headers, `key=value` lines.
    """
    errors: list[str] = []
    sections: list[Section] = []
    for number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            if sections:
                sections[-1][1].append(line)
            continue
        header = _SECTION_RE.match(stripped)
        if header:
            sections.append((header.group(1), [stripped]))
            continue
        if _KEY_RE.match(stripped) and sections:
            sections[-1][1].append(line)
            continue
        if not sections:
            errors.append(f"line {number}: expected a [section] header before any settings")
        else:
            errors.append(f"line {number}: not a 'key=value' line or comment")
    return sections, errors


def _key_of(line: str) -> str | None:
    match = _KEY_RE.match(line.strip())
    return match.group(1) if match else None


def _get(section_lines: list[str], key: str) -> str | None:
    for line in section_lines[1:]:
        match = _KEY_RE.match(line.strip())
        if match and match.group(1) == key:
            return match.group(2).strip()
    return None


def _render(sections: list[Section]) -> str:
    out: list[str] = []
    for _, lines in sections:
        # Exactly one blank line between sections, none trailing inside one.
        body = list(lines)
        while body and not body[-1].strip():
            body.pop()
        out.extend(body)
        out.append("")
    return "\n".join(out)


def merge_with_existing(supplied: str, existing: str) -> str:
    """Return `supplied` with the `[connection]` section and the `[wifi]` ssid
    replaced by the ones from `existing`. Raises ValueError if either input is
    not a usable keyfile."""
    new_sections, errors = _parse_sections(supplied)
    if errors:
        raise ValueError("; ".join(errors))
    old_sections, errors = _parse_sections(existing)
    if errors:
        raise ValueError("existing hotspot profile is unreadable: " + "; ".join(errors))

    old_connection = next((s for s in old_sections if s[0] == "connection"), None)
    old_wifi = next((s for s in old_sections if s[0] == "wifi"), None)
    if old_connection is None or old_wifi is None:
        raise ValueError("existing hotspot profile has no [connection]/[wifi] section")
    old_ssid = _get(old_wifi[1], "ssid")
    if not old_ssid:
        raise ValueError("existing hotspot profile has no ssid")

    merged: list[Section] = [("connection", list(old_connection[1]))]
    wifi_seen = False
    for name, lines in new_sections:
        if name == "connection":
            continue  # operator-supplied [connection] is ignored
        if name == "wifi":
            wifi_seen = True
            # The bundle's ssid is dropped; the on-disk (hostname-derived) one goes first.
            lines = [lines[0], f"ssid={old_ssid}"] + [l for l in lines[1:] if _key_of(l) != "ssid"]
        merged.append((name, lines))
    if not wifi_seen:
        merged.append(("wifi", ["[wifi]", f"ssid={old_ssid}"]))
    return _render(merged)


def normalise(text: str) -> str:
    """Canonical rendering of a keyfile, to compare profiles by content rather
    than by incidental whitespace."""
    sections, _ = _parse_sections(text)
    return _render(sections)


class HotspotConfig(BaseConfig):
    """The hotspot's NetworkManager keyfile, as text (`content`)."""

    def __init__(self, config_dir: Path | None = None):
        super().__init__(config_dir or NM_CONNECTION_DIR)

    @property
    def config_file(self) -> Path:
        return self.config_dir / FILE_NAME

    def load(self) -> dict[str, Any]:
        """Never exposes the profile: it holds the access point password and
        sits in a root-only directory. Returns an empty config when unreadable."""
        try:
            content = self.config_file.read_text()
        except OSError:
            return {"content": ""}
        return {"content": re.sub(r"(?m)^(psk\s*=).*$", r"\1<hidden>", content)}

    def validate(self, config: dict[str, Any]) -> list[str]:
        content = config.get("content")
        if not isinstance(content, str):
            return ["Configuration must contain a 'content' string"]
        if not content.strip():
            return ["Configuration file is empty"]
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            return [f"hotspot.nmconnection must be at most {MAX_CONTENT_BYTES} bytes"]
        if any(ord(c) < 32 and c not in "\n\r\t" for c in content):
            return ["hotspot.nmconnection contains control characters"]

        sections, errors = _parse_sections(content)
        if errors:
            return errors

        errors = []
        wifi = next((s for s in sections if s[0] == "wifi"), None)
        if wifi is None:
            errors.append("Missing [wifi] section")
        elif _get(wifi[1], "mode") != "ap":
            errors.append("[wifi] mode must be 'ap' - this profile is the device's access point")
        security = next((s for s in sections if s[0] == "wifi-security"), None)
        if security is not None:
            key_mgmt = _get(security[1], "key-mgmt")
            psk = _get(security[1], "psk")
            if key_mgmt in ("wpa-psk", "sae") and not (psk and 8 <= len(psk) <= 63):
                errors.append("[wifi-security] psk must be 8-63 characters")
        return errors

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        """Merge with the on-disk profile and apply it (runs as root).

        Returns {"changed": bool, "applied": bool}. `applied` is False when
        NetworkManager was not running (nothing to reload).
        """
        path = self.config_file
        try:
            existing = path.read_text()
        except OSError as e:
            raise RuntimeError(f"cannot read the existing hotspot profile ({path}): {e}") from e

        try:
            merged = merge_with_existing(config["content"], existing)
        except ValueError as e:
            raise RuntimeError(f"invalid hotspot profile: {e}") from e

        if merged == normalise(existing):
            return {"changed": False, "applied": False}

        self._write_atomically(path, merged)
        try:
            applied = self._reload_into_networkmanager(path)
        except Exception:
            self._write_atomically(path, existing)
            self._reload_into_networkmanager(path, ignore_errors=True)
            raise
        return {"changed": True, "applied": applied}

    @staticmethod
    def _write_atomically(path: Path, content: str) -> None:
        # NetworkManager ignores dot-files, so the half-written temp file is
        # never picked up as a profile.
        tmp = path.with_name(f".{path.name}.new")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    @staticmethod
    def _nmcli(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=_NMCLI_TIMEOUT)

    def _reload_into_networkmanager(self, path: Path, ignore_errors: bool = False) -> bool:
        """Load the profile into NetworkManager and re-activate the hotspot if
        it is up. Returns False if NetworkManager is not running."""
        try:
            loaded = self._nmcli(["connection", "load", str(path)])
        except FileNotFoundError:
            return False  # no nmcli at all (e.g. chroot during image build)
        except subprocess.TimeoutExpired as e:
            if ignore_errors:
                return False
            raise RuntimeError("nmcli timed out loading the hotspot profile") from e
        if loaded.returncode == _NMCLI_NOT_RUNNING:
            return False
        if loaded.returncode != 0:
            if ignore_errors:
                return False
            raise RuntimeError(
                f"NetworkManager rejected the hotspot profile: {loaded.stderr.strip() or loaded.stdout.strip()}"
            )

        try:
            active = self._nmcli(["-t", "-f", "NAME", "connection", "show", "--active"])
            if CONNECTION_NAME in active.stdout.splitlines():
                # --wait 0: do not block on (re)activation; this restarts the AP.
                self._nmcli(["--wait", "0", "connection", "up", CONNECTION_NAME])
        except (OSError, subprocess.TimeoutExpired):
            pass  # profile is loaded; the hotspot will pick it up on next activation
        return True
