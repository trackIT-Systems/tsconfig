"""Client for the privileged CLI (app.privileged), the pi -> root boundary.

`pi` has no sudo access of any kind. Every privileged capability is
authorized by an explicit, committed polkit rule instead - see
docs/security.md's "Service control" section for the full picture. This
module covers the two pieces that don't have a more direct native path:

1. `pkexec /usr/local/sbin/tsconfig-privileged <op> ...` (run_privileged_op,
   below) - the wrapper script (installed by the parent repo's Pifile) that
   execs `python3 -I -m app.privileged <op> ...`. Used only for config file
   writes/deletes, overlay wipe, and netplan GSM field removal - the
   operations with no D-Bus-native service to attach to. Authorized by a
   custom polkit action (`systems.trackit.tsos.tsconfig-privileged`, pinned
   to this exact executable path) plus a rule granting `pi`; see
   app/privileged/__init__.py for why this stays a separate, minimal-import
   entry point rather than a subcommand of app.cli. `pkexec` execs the
   target as root directly after checking that action, the same role sudo
   played before - it does not validate arguments, which is why
   app.privileged.ops re-validates everything itself regardless.

   **Exception:** if the calling process is already root (`os.geteuid() ==
   0` - the only real case is `tsconfig`'s own CLI invoked during image
   build, inside the Pifile's chroot, which has no live D-Bus/polkit
   authority for `pkexec` to reach at all), `run_privileged_op()` calls
   `app.privileged.cli.dispatch_op()` directly, in-process, skipping
   `pkexec` entirely - there's no privilege boundary to cross, and no
   authority daemon to ask even if there were. `tsconfig.service` itself
   always runs as `pi`, so this branch never applies to it.
2. `pkcheck` (schedule_reboot, below) - a read-only authorization query
   (no side effects) used to fail fast before scheduling a reboot, since
   that itself doesn't go through pkexec at all (see schedule_reboot's
   docstring).

Everything else that used to reach root through sudo now bypasses this
module entirely: NetworkManager access is a direct, unprivileged `nmcli`
call (app.routers.network), systemd unit actions are a direct, unprivileged
`systemctl` call (systemctl_action, below - authorized by its own polkit
rule, not pkexec), and log reads are a direct, unprivileged `journalctl`
call (app.routers.systemd) since `pi` is a member of the `systemd-journal`
group.

This module only ever runs in tracker mode - server mode (the Docker/fleet
deployment) has no boot partition, no systemd units of its own to restart,
and no wrapper script installed, so callers must check
config_loader.is_server_mode() themselves before reaching here (this module
doesn't check it itself, since what "the non-privileged equivalent" means is
specific to each caller - e.g. configs writes go straight to the versioned
config dir in server mode, not through any privileged path at all).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple

PRIVILEGED_WRAPPER = "/usr/local/sbin/tsconfig-privileged"

# Generous but bounded - these are local process calls (systemctl, or a
# short-lived Python process doing a file write/nmcli call), not network
# requests, so anything taking longer than this indicates something stuck.
DEFAULT_TIMEOUT = 30.0

# The app-facing curated subset of systemd verbs exposed through
# systemctl_action() / POST /api/systemd/action - independent of, and
# narrower than, what etc/polkit-1/rules.d/20-tsos-systemctl.rules itself
# permits (every non-persistent org.freedesktop.systemd1.manage-units verb).
# An interactive `pi` shell can still `systemctl kill`/`reset-failed`/etc.
# directly; this constant only keeps tsconfig's own HTTP surface curated,
# the same way it always has been.
ALLOWED_SYSTEMCTL_VERBS = frozenset({"start", "stop", "restart", "reload"})


class PrivilegedCallError(Exception):
    """Raised when a privileged call fails, for any reason: the underlying
    op rejected the request (PrivilegedOpError on the root side), polkit
    denied the request, the process timed out, or its output wasn't the
    expected JSON. The message is always safe to show to an API caller -
    it's either the root-side op's own PrivilegedOpError message (already
    written to be caller-safe, see app/privileged/ops.py) or a generic
    description of a transport-level failure, never a raw stderr dump or
    traceback.
    """


async def _run_subprocess(argv: List[str], stdin_bytes: Optional[bytes], timeout: float) -> Tuple[int, bytes, bytes]:
    """Run `argv` directly (no elevation prefix), optionally writing
    stdin_bytes, and return (returncode, stdout, stderr). Raises
    PrivilegedCallError on timeout or if the executable can't be found/run
    at all.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise PrivilegedCallError(f"failed to start process: {e}") from e

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(input=stdin_bytes), timeout=timeout)
    except asyncio.TimeoutError as e:
        process.kill()
        await process.wait()
        raise PrivilegedCallError("privileged operation timed out") from e

    return process.returncode, stdout, stderr


async def run_privileged_op(
    op: str,
    args: Optional[List[str]] = None,
    stdin_json: Optional[Dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run one `tsconfig-privileged <op>` call and return its result dict.

    Args:
        op: e.g. "write-config", "delete-config", "wipe-overlay", "unset-gsm-fields".
        args: extra argv for the op (e.g. ["wireguard", "--mtime", "123"]).
        stdin_json: if given, JSON-encoded and written to the process's
            stdin (used by write-config's {"config": {...}} contract).
        timeout: seconds before giving up and killing the process.

    Returns:
        The op's result dict (whatever it put alongside "success": true -
        e.g. write-config's {"config_type", "config_file", "save_result",
        "mtime_set"}).

    Raises:
        PrivilegedCallError: on rejection (op-level failure, polkit denial,
            timeout, or unparseable output).
    """
    if os.geteuid() == 0:
        # Already root - e.g. tsconfig's own CLI invoked during image build
        # (a Pifile RUN step runs the whole build inside a chroot with no
        # live D-Bus/polkit authority for pkexec to reach - polkit
        # fundamentally needs a running daemon, unlike sudo's static file
        # checks - and there's no privilege boundary to cross anyway since
        # we're already root). Call the root-side op directly, in-process,
        # skipping pkexec entirely. This never applies to tsconfig.service
        # itself, which always runs as `pi`.
        from app.privileged import cli as privileged_cli
        from app.privileged import ops as privileged_ops

        parsed_args = privileged_cli.build_parser().parse_args([op, *(args or [])])
        try:
            return privileged_cli.dispatch_op(parsed_args, stdin_json)
        except privileged_ops.PrivilegedOpError as e:
            raise PrivilegedCallError(str(e)) from e

    argv = ["pkexec", PRIVILEGED_WRAPPER, op, *(args or [])]
    stdin_bytes = json.dumps(stdin_json).encode("utf-8") if stdin_json is not None else None

    returncode, stdout, stderr = await _run_subprocess(argv, stdin_bytes, timeout)

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    if not stdout_text:
        # pkexec refuses silently (polkit denial, unknown action, etc.) by
        # printing to stderr and producing no stdout at all - that's the
        # most common way this branch is hit in practice.
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise PrivilegedCallError(detail or "privileged operation produced no output")

    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as e:
        raise PrivilegedCallError("privileged operation returned malformed output") from e

    if not payload.get("success"):
        raise PrivilegedCallError(payload.get("error") or "privileged operation failed")

    payload.pop("success", None)
    return payload


async def systemctl_action(unit: str, verb: str, timeout: float = DEFAULT_TIMEOUT) -> Tuple[bool, Optional[str]]:
    """`systemctl <verb> <unit>` directly - no elevation at all. Authorized
    by etc/polkit-1/rules.d/20-tsos-systemctl.rules, which grants `pi` every
    non-persistent org.freedesktop.systemd1.manage-units verb for a fixed
    unit allowlist. `verb` is re-checked here too against
    ALLOWED_SYSTEMCTL_VERBS - this module's own contract, independent of
    (and narrower than) what the polkit rule itself permits.

    Passing a unit not on the polkit rule's allowlist doesn't raise here -
    systemd/polkit itself refuses it, which surfaces as success=False with
    systemd's own denial message, exactly like any other rejected call.

    Returns:
        (success, error_message) - error_message is None on success.
    """
    if verb not in ALLOWED_SYSTEMCTL_VERBS:
        return False, f"unsupported systemctl action: {verb!r}"
    returncode, stdout, stderr = await _run_subprocess(["/usr/bin/systemctl", verb, unit], None, timeout)
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        return False, detail or f"systemctl {verb} {unit} failed (exit {returncode})"
    return True, None


async def schedule_reboot(delay: int = 10, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Verify `pi` is authorized to reboot, then schedule `systemctl reboot`
    `delay` seconds from now as an in-process asyncio task.

    Authorization is checked upfront via `pkcheck` (a read-only polkit
    query, no side effects) against org.freedesktop.login1.reboot - the
    same action logind checks when `systemctl reboot` runs as a non-root
    user - rather than just firing the reboot and finding out later,
    preserving the property that a caller (typically finishing an HTTP
    request) learns about a denial before getting a success response.

    The delay itself is held in this process (not a root-side systemd-run
    transient timer, the way tsconfig-privileged's old `reboot` op worked)
    since `tsconfig.service` is already a long-running daemon and `pi` can
    now call `systemctl reboot` directly - see
    etc/polkit-1/rules.d/21-tsos-reboot.rules.

    Raises:
        PrivilegedCallError: if pkcheck reports `pi` is not authorized.
    """
    returncode, _stdout, _stderr = await _run_subprocess(
        ["pkcheck", "--action-id", "org.freedesktop.login1.reboot", "--process", str(os.getpid())],
        None,
        timeout,
    )
    if returncode != 0:
        raise PrivilegedCallError("not authorized to reboot")

    async def _reboot_after_delay() -> None:
        await asyncio.sleep(delay)
        await asyncio.create_subprocess_exec("/usr/bin/systemctl", "reboot")

    asyncio.create_task(_reboot_after_delay())
    return {"scheduled": True, "delay_seconds": delay}


async def write_config(
    config_type: str,
    config: Dict[str, Any],
    mtime: Optional[float] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Validate and write one boot-partition config file as root.

    Note this does NOT validate `config` itself first - the caller (a
    router) may do that for fast user feedback, but the privileged op
    re-validates unconditionally regardless, so there's no correctness
    requirement to do so here too.
    """
    args = [config_type]
    if mtime is not None:
        args += ["--mtime", str(mtime)]
    return await run_privileged_op("write-config", args, stdin_json={"config": config}, timeout=timeout)


async def stat_config(config_type: str, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Existence and mtime of one config file as root (for files in root-only
    directories that pi cannot stat itself). Returns {"exists", "mtime", ...}."""
    return await run_privileged_op("stat-config", [config_type], timeout=timeout)


async def delete_config(config_type: str, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Delete one boot-partition config file, if present, as root."""
    return await run_privileged_op("delete-config", [config_type], timeout=timeout)


async def wipe_overlay(timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Wipe the persistent overlay directory as root."""
    return await run_privileged_op("wipe-overlay", timeout=timeout)


# --- Netplan GSM field removal, used by app.routers.network -------------
#
# The one nmcli-adjacent operation still routed through root: nmcli cannot
# unset GSM password/pin fields itself, and /etc/netplan is root-owned.
# Every other NetworkManager interaction (connection modify/up/reload,
# WiFi rescan, revealing secrets) is now a direct, unprivileged nmcli call
# from app.routers.network - `pi` has native, polkit-authorized
# NetworkManager access (see docs/security.md's "Network management"
# section), so there's no root boundary left to cross for those.


async def unset_gsm_fields(connection: str, fields: List[str], timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Remove GSM fields (password/pin) nmcli can't unset itself from a
    cellular connection's netplan file, as root.
    """
    return await run_privileged_op("unset-gsm-fields", [connection], stdin_json={"fields": fields}, timeout=timeout)
