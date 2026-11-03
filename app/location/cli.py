"""tslocation: talk to the running location daemon (tslocationd) over its control socket.

    tslocation status            mode, state, positions, deviation, problems
    tslocation fix               make a fine fix now and print it (stationary mode)
    tslocation watch             follow the location as it changes
    tslocation cells             the serving cell and the cells known at this position
    tslocation config            the settings in effect, and which come from the file
    tslocation mode [MODE]       show the mode, or set it in tslocation.yml and restart the daemon

Without a command, `status`. `--json` prints what the daemon answers. Exit codes: 0 ok, 1 the daemon answered
with an error, 2 usage error, 3 the daemon cannot be reached.
"""

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from app.location.server import SOCKET, RequestError, events, request
from app.location.settings import DEFAULT_CONFIG, DURATIONS, LocationSettings

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_UNREACHABLE = 0, 1, 2, 3


class Unreachable(Exception):
    pass


# --- formatting


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s" if s % 60 else f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def fmt_setting(name: str, value: Any) -> str:
    """A setting as the file would have it: durations as 7d, 1h, 60s."""
    if name in DURATIONS and isinstance(value, (int, float)):
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if value >= size and value % size == 0:
                return f"{value / size:g}{unit}"
        return f"{value:g}s"
    return f"{value:g}" if isinstance(value, float) else str(value)


def fmt_distance(metres: float | None) -> str:
    if metres is None:
        return "-"
    return f"{metres / 1000:.1f} km" if metres >= 1000 else f"{metres:.1f} m"


def fmt_position(position: dict[str, Any] | None) -> str:
    if not position:
        return "-"
    text = f"{position['lat']:.7f}, {position['lon']:.7f}"
    if position.get("accuracy") is not None:
        text += f" ±{position['accuracy']:g} m"
    if position.get("alt") is not None:
        text += f", {position['alt']:g} m altitude"
    return text


def fmt_fix(fix: dict[str, Any] | None, now: datetime.datetime | None = None) -> str:
    if not fix:
        return "none yet"
    parts = [fmt_position(fix)]
    if fix.get("sats") is not None:
        parts.append(f"{fix['sats']} satellites")
    if fix.get("hdop") is not None:
        parts.append(f"HDOP {fix['hdop']:g}")
    if fix.get("time"):
        when = datetime.datetime.fromisoformat(fix["time"])
        now = now or datetime.datetime.now(when.tzinfo)
        parts.append(f"{when:%Y-%m-%d %H:%M:%S} ({fmt_duration((now - when).total_seconds())} ago)")
    return ", ".join(parts)


def lines(rows: list[tuple[str, str]]) -> str:
    width = max(len(key) for key, _ in rows)
    return "\n".join(f"{key.ljust(width)}  {value}" for key, value in rows)


def table(rows: list[list[str]], header: list[str]) -> str:
    rows = [header, *rows]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip() for row in rows)


def format_status(status: dict[str, Any], now: datetime.datetime | None = None) -> str:
    location = status["location"]
    effective = location.get("effective")
    rows = [
        ("mode", location["mode"]),
        ("state", location["state"] + (", measuring now" if location.get("fixing") else "")),
        ("effective", f"{fmt_position(effective)} ({effective['source']})" if effective else "-"),
        ("reference", fmt_position(location.get("reference"))),
        ("fix", fmt_fix(location.get("fix"), now)),
        ("deviation", fmt_distance(location.get("deviation_m"))),
    ]
    if location.get("proposed"):
        rows.append(("proposed", fmt_position(location["proposed"])))
    if status.get("next_check_in_s") is not None:
        rows.append(("next check", f"in {fmt_duration(status['next_check_in_s'])}"))
    for what, error in (status.get("failures") or {}).items():
        rows.append(("problem", f"{what}: {error}"))
    rows.append(
        (
            "daemon",
            f"tslocationd {status['version']}, receiver {status['receiver']}, up {fmt_duration(status['uptime_s'])}",
        )
    )
    return lines(rows)


def format_event(location: dict[str, Any], now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now().astimezone()
    parts = [f"{now:%H:%M:%S}", f"{location['state']:<9}"]
    if location.get("fixing"):
        parts.append("measuring...")
    elif location.get("fix"):
        fix = location["fix"]
        parts.append(fmt_position(fix))
        if fix.get("sats") is not None:
            parts.append(f"{fix['sats']} sats")
    if location.get("deviation_m") is not None:
        parts.append(f"deviation {fmt_distance(location['deviation_m'])}")
    return "  ".join(parts)


def format_cells(cells: dict[str, Any]) -> str:
    current = cells.get("current")
    known = cells.get("known") or []
    rows = [[c["mcc"], c["mnc"], c["tac"], c["cell_id"], "*" if c == current else ""] for c in known]
    if current and current not in known:
        rows.append([current["mcc"], current["mnc"], current["tac"], current["cell_id"], "* (new)"])
    if not rows:
        return "no cells known" + ("" if current else ", and no serving cell (no modem, or not registered)")
    return table(rows, ["MCC", "MNC", "TAC", "CELL ID", "SERVING"])


def format_config(config: dict[str, Any], file_settings: dict[str, Any]) -> str:
    rows = [
        [name, fmt_setting(name, value), "file" if name in file_settings else "default"]
        for name, value in config["settings"].items()
    ]
    return f"{config['path']}\n\n" + table(rows, ["SETTING", "VALUE", "FROM"])


# --- commands


async def call(args: argparse.Namespace, cmd: str, cmd_args: dict[str, Any] | None = None, timeout: float = 30) -> Any:
    try:
        return await request(args.socket, cmd, cmd_args, timeout=timeout)
    except (OSError, TimeoutError) as e:
        raise Unreachable(f"cannot reach tslocationd at {args.socket}: {e or type(e).__name__}") from None


def show(args: argparse.Namespace, result: Any, text: str) -> None:
    print(json.dumps(result, indent=1) if args.json else text)


async def cmd_status(args: argparse.Namespace) -> int:
    status = await call(args, "status")
    show(args, status, format_status(status))
    return EXIT_OK


async def cmd_fix(args: argparse.Namespace) -> int:
    if args.no_wait:
        result = await call(args, "fix", {"wait": False})
        show(args, result, "fine fix requested")
        return EXIT_OK
    settings = (await call(args, "config"))["settings"]
    # switching on, up to fix_timeout for the first fix, fix_duration of averaging
    timeout = settings["fix_timeout"] + settings["fix_duration"] + 60
    if not args.json and settings["mode"] == "stationary":
        print(f"measuring, up to {fmt_duration(timeout - 60)} ...", file=sys.stderr)
    location = await call(args, "fix", {"wait": True}, timeout=timeout)
    rows = [
        ("fix", fmt_fix(location.get("fix"))),
        ("state", location["state"]),
        ("deviation", fmt_distance(location.get("deviation_m"))),
    ]
    if location.get("proposed"):
        rows.append(("proposed", fmt_position(location["proposed"])))
    show(args, location, lines(rows))
    return EXIT_OK


async def cmd_watch(args: argparse.Namespace) -> int:
    try:
        async for location in events(args.socket, "watch"):
            print(json.dumps(location) if args.json else format_event(location), flush=True)
    except OSError as e:
        raise Unreachable(f"cannot reach tslocationd at {args.socket}: {e}") from None
    print("tslocationd stopped", file=sys.stderr)
    return EXIT_UNREACHABLE


async def cmd_cells(args: argparse.Namespace) -> int:
    cells = await call(args, "cells")
    show(args, cells, format_cells(cells))
    return EXIT_OK


def read_file(path: Path) -> dict[str, Any]:
    """The settings tslocation.yml sets itself (empty without the file)."""
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


async def cmd_config(args: argparse.Namespace) -> int:
    config = await call(args, "config")
    try:
        file_settings = read_file(Path(config["path"]))
    except (OSError, yaml.YAMLError):
        file_settings = {}
    show(args, config, format_config(config, file_settings))
    return EXIT_OK


async def cmd_mode(args: argparse.Namespace) -> int:
    if args.mode is None:
        config = await call(args, "config")
        show(args, {"mode": config["settings"]["mode"]}, config["settings"]["mode"])
        return EXIT_OK
    # Written like tsconfig writes it: as root through the privileged op, which validates again
    from app.utils.privileged import PrivilegedCallError, systemctl_action, write_config

    settings = read_file(args.config)
    settings["mode"] = args.mode
    LocationSettings.model_validate(settings)
    try:
        await write_config("tslocation", settings)
    except PrivilegedCallError as e:
        print(f"error: cannot write {args.config}: {e}", file=sys.stderr)
        return EXIT_ERROR
    if args.no_restart:
        print(f"mode {args.mode} written to {args.config}; it takes effect when tslocation restarts")
        return EXIT_OK
    ok, error = await systemctl_action("tslocation.service", "restart")
    if not ok:
        print(f"error: mode written, but tslocation did not restart: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"mode {args.mode}, tslocation restarted")
    return EXIT_OK


COMMANDS = {
    "status": cmd_status,
    "fix": cmd_fix,
    "watch": cmd_watch,
    "cells": cmd_cells,
    "config": cmd_config,
    "mode": cmd_mode,
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        "tslocation", description="Talk to the location daemon tslocationd. Without a command: status."
    )
    p.add_argument("--socket", type=Path, default=SOCKET, help="control socket (default: %(default)s)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def command(name: str, help: str) -> argparse.ArgumentParser:
        c = sub.add_parser(name, help=help, description=help)
        c.add_argument("--json", action="store_true", help="print what the daemon answers, as JSON")
        return c

    command("status", "mode, state, positions, deviation and problems")
    fix = command("fix", "make a fine fix now and print it (stationary mode only)")
    fix.add_argument("--no-wait", action="store_true", help="only request it, do not wait for the result")
    command("watch", "print the location every time it changes, until interrupted")
    command("cells", "the serving cell and the cells known at this position")
    command("config", "the settings in effect, and which come from the settings file")
    mode = command("mode", "show the mode, or set it in the settings file and restart tslocation")
    mode.add_argument("mode", nargs="?", choices=["off", "stationary", "mobile"])
    mode.add_argument("--no-restart", action="store_true", help="only write the file")
    mode.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG, help="settings file (default: %(default)s)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command is None:
        # Like timedatectl or nmcli: the overview
        args.command, args.json = "status", False
    try:
        return asyncio.run(COMMANDS[args.command](args))
    except Unreachable as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except RequestError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as e:  # an invalid settings file for `mode`
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
