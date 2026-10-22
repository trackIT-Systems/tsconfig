"""Read-only proxy to the control sockets of the power daemons (charge controllers).

solarlifed and smartsolard each keep the state of one charge controller and
answer requests on a unix socket: one JSON object per line, in both
directions. This router forwards one request from the web UI to a daemon and
returns the daemon's reply unchanged; how to show the answer is up to the
frontend. Only the commands that read are forwarded: the sockets have no
authentication of their own and also take commands that change the
controller's settings.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/power", tags=["power"])

# Daemon name -> its control socket (`daemon.socket` in /etc/<name>/<name>.yml)
BACKENDS: dict[str, Path] = {
    "solarlife": Path("/run/solarlife/solarlife.sock"),
    "smartsolar": Path("/run/smartsolar/smartsolar.sock"),
}

# Commands forwarded to the daemons, with how long to wait for the reply.
# `status` and `cached` are answered from the daemon's memory; `read` asks the
# controller, which can wait for the radio (smartsolar's GATT in poll mode
# connects first: connect, pair and collect take up to about 110 s).
TIMEOUTS_S = {"status": 2.0, "cached": 2.0, "read": 120.0}

# The daemons' largest response (ipc.py in both)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class DaemonRequest(BaseModel):
    """One request to a daemon, as its control socket takes it."""

    cmd: str = Field(..., description="Daemon command: status, cached or read")
    args: dict[str, Any] | None = Field(None, description="Arguments of the command, e.g. {'block': 'all'}")


async def forward(socket_path: Path, request: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    """Send one request line to the daemon and return its reply line, parsed.

    None when the daemon cannot be reached (no socket, not running, no access),
    does not answer in time, or answers with something that is not a JSON object.
    """
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        reply = json.loads(line)
        return reply if isinstance(reply, dict) else None
    except (OSError, TimeoutError, ValueError) as e:
        logger.debug("No reply from %s: %r", socket_path, e)
        return None
    finally:
        if writer is not None:
            writer.close()


@router.post("/{source}")
async def power_request(source: str, request: DaemonRequest) -> dict[str, Any] | None:
    """Forward one read-only request to a power daemon.

    Returns the daemon's reply envelope unchanged ({id, ok, result} or
    {id, ok: false, error}), or null if the daemon can't be reached: on most
    systems one of the two daemons has no controller, or there is none at all.
    """
    socket_path = BACKENDS.get(source)
    if socket_path is None:
        raise HTTPException(status_code=404, detail=f"Unknown power source: {source}")
    timeout = TIMEOUTS_S.get(request.cmd)
    if timeout is None:
        raise HTTPException(status_code=400, detail=f"Command not allowed: {request.cmd}")
    message: dict[str, Any] = {"id": 1, "cmd": request.cmd}
    if request.args is not None:
        message["args"] = request.args
    return await forward(socket_path, message, timeout)
