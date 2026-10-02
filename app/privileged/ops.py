"""Implementations of the individual privileged operations.

See app/privileged/__init__.py for the security rules that apply to this
whole package. Each op function here:

- takes already-parsed, type-checked arguments from app.privileged.cli
  (argv parsing/JSON stdin handling lives there, not here)
- re-validates everything itself rather than trusting that the caller (the
  unprivileged tsconfig process, or an operator's shell) already did
- returns a plain JSON-serializable dict on success
- raises PrivilegedOpError(message) on any rejection or failure

None of this module imports FastAPI, app.main, or app.routers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

import yaml

from app.configs import registry

# The overlay upper directory (persistent /etc, /home, etc. changes layered
# over the read-only rootfs). Wiping it and rebooting returns the system to
# its image-default state. This path is root-owned; nothing here reads
# operator-supplied data.
OVERLAY_PATH = Path("/media/root-rw")

# The exact, fixed set of NetworkManager connection names app.routers.network
# ever operates on - used here only to validate `unset_gsm_fields`'s
# `connection` argument. Kept in sync manually with that module (which
# imports this constant for its own pre-validation) - there is no
# config-driven source for this the way app.configs.registry is for config
# types, since these names are baked into how the device's networking is
# provisioned at image build time, not something an operator configures.
ALLOWED_CONNECTIONS: FrozenSet[str] = frozenset({"station", "hotspot", "cellular"})

# The only GSM fields nmcli cannot unset itself, and therefore the only ones
# unset_gsm_fields is allowed to touch in the netplan YAML.
_ALLOWED_GSM_FIELDS: FrozenSet[str] = frozenset({"password", "pin"})

# Where netplan keeps NetworkManager-generated connection files. A module
# constant (rather than inlined in unset_gsm_fields) so tests can point it at
# a tmp_path, the same pattern this module uses for OVERLAY_PATH.
NETPLAN_DIR = Path("/etc/netplan")

_NMCLI_TIMEOUT = 10.0


class PrivilegedOpError(Exception):
    """Raised for any rejected or failed privileged operation.

    Caught once, at the top level in app.privileged.cli, and reported back
    to the caller as {"success": false, "error": str(e)} with a non-zero
    exit code - never as a Python traceback, which could leak details about
    the root-side filesystem layout.
    """


def _set_and_sync_mtime(path: Path, mtime: float) -> bool:
    """Set a file's mtime and fsync, mirroring
    app.routers.configs.set_and_verify_file_mtime (which this replaces for
    boot-partition writes, since that function assumes it can os.utime a
    file it doesn't necessarily own once /boot/firmware is root-owned).

    Returns True if the on-disk mtime ended up within 2 seconds of the
    requested value (FAT32 has 2-second resolution), False otherwise.
    """
    os.utime(path, (mtime, mtime))
    try:
        os.sync()
    except AttributeError:
        pass
    actual = path.stat().st_mtime
    return abs(actual - mtime) <= 2


def write_config(config_type: str, config: Dict[str, Any], mtime: Optional[float]) -> Dict[str, Any]:
    """Validate and write a single config type to its (root-owned) location.

    `config_type` must be one of app.configs.registry.CONFIG_TYPES - the
    fixed set of config files this device ever writes to the boot partition
    or /boot/firmware/mosquitto.d. Anything else is rejected before any
    filesystem access happens.
    """
    try:
        instance = registry.get_privileged_config_instance(config_type)
    except ValueError as e:
        raise PrivilegedOpError(str(e)) from e

    if not isinstance(config, dict):
        raise PrivilegedOpError("'config' must be a JSON object")

    errors = instance.validate(config)
    if errors:
        raise PrivilegedOpError("validation failed: " + "; ".join(errors))

    try:
        save_result = instance.save(config)
    except Exception as e:
        raise PrivilegedOpError(f"failed to save {config_type}: {e}") from e

    mtime_set: Optional[bool] = None
    if mtime is not None:
        try:
            mtime_set = _set_and_sync_mtime(instance.config_file, mtime)
        except OSError as e:
            raise PrivilegedOpError(f"saved but failed to set mtime: {e}") from e

    return {
        "config_type": config_type,
        "config_file": str(instance.config_file),
        "save_result": save_result,
        "mtime_set": mtime_set,
    }


def stat_config(config_type: str) -> Dict[str, Any]:
    """Report whether a config type's file exists and its mtime.

    Exists for config files `pi` cannot stat itself because they live in a
    root-only directory (the hotspot profile), so the "only overwrite if the
    bundle is newer" decision can still be made for them. Reveals nothing
    but existence and modification time - never content. Same fixed
    config_type set as write_config.
    """
    try:
        instance = registry.get_privileged_config_instance(config_type)
    except ValueError as e:
        raise PrivilegedOpError(str(e)) from e

    try:
        mtime = instance.config_file.stat().st_mtime
    except FileNotFoundError:
        return {"config_type": config_type, "exists": False, "mtime": None}
    except OSError as e:
        raise PrivilegedOpError(f"failed to stat {config_type}: {e}") from e
    return {"config_type": config_type, "exists": True, "mtime": mtime}


def delete_config(config_type: str) -> Dict[str, Any]:
    """Delete a single config type's file, if it exists.

    Used for e.g. removing wireguard.conf on system reset. Same fixed
    config_type set as write_config.
    """
    if config_type in registry.NON_DELETABLE_CONFIG_TYPES:
        raise PrivilegedOpError(f"config type '{config_type}' cannot be deleted")
    try:
        instance = registry.get_privileged_config_instance(config_type)
    except ValueError as e:
        raise PrivilegedOpError(str(e)) from e

    path = instance.config_file
    existed = path.exists()
    if existed:
        try:
            path.unlink()
        except OSError as e:
            raise PrivilegedOpError(f"failed to delete {path}: {e}") from e

    return {"config_type": config_type, "config_file": str(path), "deleted": existed}


def wipe_overlay() -> Dict[str, Any]:
    """Remove every entry under the persistent overlay directory.

    Mirrors the existing logic in app.cli.process_system_reset - the overlay
    holds every change layered over the read-only rootfs (most of /etc,
    /home, etc.), so wiping it and rebooting returns the device to its
    image-default state.
    """
    if not OVERLAY_PATH.exists() or not OVERLAY_PATH.is_dir():
        raise PrivilegedOpError(f"overlay path does not exist or is not a directory: {OVERLAY_PATH}")

    removed: List[str] = []
    errors: List[str] = []
    for item in OVERLAY_PATH.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed.append(item.name)
        except OSError as e:
            errors.append(f"{item.name}: {e}")

    if errors:
        raise PrivilegedOpError("failed to wipe overlay: " + "; ".join(errors))

    return {"wiped": True, "removed": removed}


def _run_nmcli(args: List[str], timeout: float = _NMCLI_TIMEOUT) -> Dict[str, Any]:
    """Run nmcli with an explicit argv list (never a shell) and return its
    result. Does not raise on a non-zero nmcli exit; only a failure to even
    launch nmcli raises PrivilegedOpError. Used internally by
    unset_gsm_fields, which still needs root (unlike every other nmcli call
    tsconfig makes, now that `pi` has direct, polkit-authorized
    NetworkManager access - see docs/security.md's "Network management"
    section) because it edits a root-owned netplan file, not a NetworkManager
    property.
    """
    try:
        result = subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise PrivilegedOpError(f"nmcli not available: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise PrivilegedOpError("nmcli command timed out") from e
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def unset_gsm_fields(connection: str, fields: List[str]) -> Dict[str, Any]:
    """Remove GSM fields (password/pin) from a cellular connection's netplan
    YAML - nmcli itself cannot unset these fields, and /etc/netplan is
    root-owned, so this is the one nmcli-adjacent operation that still needs
    to run as root even though `pi` otherwise talks to NetworkManager
    directly (see docs/security.md's "Network management" section).
    """
    if not isinstance(connection, str) or connection not in ALLOWED_CONNECTIONS:
        raise PrivilegedOpError(f"connection is not in the allowed set: {connection!r}")

    if not isinstance(fields, list) or not fields:
        raise PrivilegedOpError("'fields' must be a non-empty JSON array")
    for field in fields:
        if field not in _ALLOWED_GSM_FIELDS:
            raise PrivilegedOpError(f"field not allowed: {field!r}")

    uuid_result = _run_nmcli(["-t", "-f", "connection.uuid", "connection", "show", connection])
    if uuid_result["returncode"] != 0:
        raise PrivilegedOpError(f"connection not found: {connection}")
    uuid_line = uuid_result["stdout"].strip()
    uuid = uuid_line.split(":", 1)[1].strip() if ":" in uuid_line else ""
    if not uuid:
        raise PrivilegedOpError("failed to determine connection UUID")

    netplan_file = NETPLAN_DIR / f"90-NM-{uuid}.yaml"
    if not netplan_file.exists():
        return {"modified": False, "removed": [], "netplan_file": str(netplan_file)}

    try:
        content = yaml.safe_load(netplan_file.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise PrivilegedOpError(f"failed to read netplan file: {e}") from e

    removed: List[str] = []
    modem_config = None
    if isinstance(content, dict):
        network = content.get("network")
        if isinstance(network, dict):
            modems = network.get("modems")
            if isinstance(modems, dict):
                modem_config = modems.get(connection)

    if isinstance(modem_config, dict):
        for field in fields:
            if field in modem_config:
                del modem_config[field]
                removed.append(field)

    if not removed:
        return {"modified": False, "removed": [], "netplan_file": str(netplan_file)}

    try:
        netplan_file.write_text(yaml.dump(content, default_flow_style=False, sort_keys=False))
    except OSError as e:
        raise PrivilegedOpError(f"failed to write netplan file: {e}") from e

    reload_result = _run_nmcli(["connection", "reload"])

    return {
        "modified": True,
        "removed": removed,
        "netplan_file": str(netplan_file),
        "reload_returncode": reload_result["returncode"],
    }

