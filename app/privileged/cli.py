"""Argument parsing and dispatch for the privileged ops.

Invoked as `python3 -I -m app.privileged <op> [args...]` by the
`tsconfig-privileged` wrapper script (installed in tsOS-base, run as root via
`pkexec`, authorized by the `systems.trackit.tsos.tsconfig-privileged` polkit
action - see docs/security.md's "Service control" section). Deliberately a
separate entry point from app.cli, not a subcommand bolted onto it: app.cli
imports app.routers.configs at module level, which pulls in FastAPI and its
whole dependency tree - importing all of that into a root process for every
invocation would work against the "keep the import graph small" rule in
app/privileged/__init__.py for no benefit, since none of it is needed here.

Output contract for callers (see app/utils/privileged.py on the unprivileged
side): every op prints exactly one JSON object to stdout and exits 0 on
success. On failure, prints {"success": false, "error": "..."} to stdout and
exits 1. Never a Python traceback - PrivilegedOpError messages are safe to
show the caller, but an unexpected exception is caught and reported
generically instead, to avoid leaking implementation details of a
root-owned process to an operator.

Everything that isn't a bespoke file operation has moved off this wrapper
entirely: NetworkManager connection changes, systemd unit actions, reboot,
and log reads are all direct, unprivileged calls (`nmcli`, `systemctl`,
`journalctl`) authorized by their own native polkit rules or (for logs)
plain `systemd-journal` group membership - see docs/security.md. What's left
here (`write-config`, `delete-config`, `wipe-overlay`, `unset-gsm-fields`)
has no D-Bus-native service to attach to, so it stays a bespoke wrapper
gated by `pkexec` plus a custom polkit action.

`dispatch_op()` below is the reusable business-logic core (parse an already-
parsed args Namespace, call the right app.privileged.ops function, return
its result or raise). `main()` wraps it for the pkexec/subprocess path;
app.utils.privileged.run_privileged_op() calls it directly, in-process, when
already root (no live polkit authority to reach in that case anyway - see
that module's docstring).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.privileged import ops


def _read_json_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ops.PrivilegedOpError(f"stdin was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ops.PrivilegedOpError("stdin JSON must be an object")
    return data


def _print_ok(result: dict[str, Any]) -> None:
    print(json.dumps({"success": True, **result}))


def _print_error(message: str) -> None:
    print(json.dumps({"success": False, "error": message}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsconfig-privileged",
        description="Privileged config-write operations invoked via pkexec on behalf of pi. Not for interactive use.",
    )
    subparsers = parser.add_subparsers(dest="op", required=True)

    write_config = subparsers.add_parser(
        "write-config",
        help="Validate and write one boot-partition config file. Reads {'config': {...}} JSON from stdin.",
    )
    write_config.add_argument("config_type", help="One of app.configs.registry.CONFIG_TYPES")
    write_config.add_argument("--mtime", type=float, default=None, help="Epoch seconds to set as the file's mtime")

    stat_config = subparsers.add_parser(
        "stat-config", help="Report whether a config file exists and its mtime (for root-only files)."
    )
    stat_config.add_argument("config_type", help="One of app.configs.registry.CONFIG_TYPES")

    delete_config = subparsers.add_parser("delete-config", help="Delete one boot-partition config file, if present.")
    delete_config.add_argument("config_type", help="One of app.configs.registry.CONFIG_TYPES")

    subparsers.add_parser("wipe-overlay", help="Remove everything under the persistent overlay directory.")

    unset_gsm = subparsers.add_parser(
        "unset-gsm-fields",
        help="Remove GSM fields nmcli can't unset from a connection's netplan file. Reads {'fields': [...]} JSON from stdin.",
    )
    unset_gsm.add_argument("connection", help="One of app.privileged.ops.ALLOWED_CONNECTIONS")

    return parser


def dispatch_op(args: argparse.Namespace, stdin_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one already-parsed op and return its result dict, raising
    ops.PrivilegedOpError on rejection.

    Shared by main() (the pkexec/subprocess entry point, which reads
    stdin_data itself via _read_json_stdin() before calling this) and
    app.utils.privileged.run_privileged_op()'s already-root bypass (see that
    module's docstring for why it exists - in short, a process that's
    already root, e.g. tsconfig's own CLI invoked during image build, has no
    live D-Bus/polkit authority for pkexec to reach and no privilege
    boundary to cross anyway, so it calls this directly, in-process, with
    stdin_data already available as a plain dict rather than needing to be
    read from an actual stdin stream).
    """
    if args.op == "write-config":
        return ops.write_config(args.config_type, (stdin_data or {}).get("config"), args.mtime)

    if args.op == "stat-config":
        return ops.stat_config(args.config_type)

    if args.op == "delete-config":
        return ops.delete_config(args.config_type)

    if args.op == "wipe-overlay":
        return ops.wipe_overlay()

    if args.op == "unset-gsm-fields":
        fields = (stdin_data or {}).get("fields") or []
        return ops.unset_gsm_fields(args.connection, fields)

    # argparse's `required=True` on the subparsers makes this unreachable,
    # kept only as a defensive fallback.
    raise ops.PrivilegedOpError(f"unknown op: {args.op}")


_STDIN_JSON_OPS = frozenset({"write-config", "unset-gsm-fields"})


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        stdin_data = _read_json_stdin() if args.op in _STDIN_JSON_OPS else None
        result = dispatch_op(args, stdin_data)
        _print_ok(result)
        return 0

    except ops.PrivilegedOpError as e:
        _print_error(str(e))
        return 1
    except Exception:
        # Never let an unexpected exception's message/traceback (which could
        # reference internal paths or state) reach the caller. Full detail
        # still goes to stderr, which lands in the journal for root/admin
        # troubleshooting.
        import traceback

        traceback.print_exc(file=sys.stderr)
        _print_error("internal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
