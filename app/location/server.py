"""The control socket of tslocationd: the `tslocation` CLI and tsconfig talk to the daemon through it.

One JSON object per line, in both directions, as the sockets of solarlifed and
smartsolard:

    request   {"id": 1, "cmd": "fix", "args": {"wait": true}}
    success   {"id": 1, "ok": true, "result": {...}}
    failure   {"id": 1, "ok": false, "error": {"type": "not_available", "message": "..."}}

A handler that returns an async iterator streams: every item is sent as
{"id": ..., "ok": true, "event": {...}} until the client closes the connection
(`watch`). There is no authentication; the socket's mode (0660, owner pi) is the
only protection. See tsOS-base docs/location.md, "Control socket".
"""

import asyncio
import json
import logging
import os
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# In tslocation.service's RuntimeDirectory, next to location.json
SOCKET = Path("/run/tslocation/tslocation.sock")
IDLE_TIMEOUT_S = 30
MAX_REQUEST_BYTES = 64 * 1024
SOCKET_MODE = 0o660

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class RequestError(Exception):
    """A request that cannot be answered; `type` goes to the client as error.type."""

    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_


def encode(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":"), default=str).encode() + b"\n"


class ControlServer:
    def __init__(self, path: Path, handlers: dict[str, Handler]):
        self.path = path
        self.handlers = handlers
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if stat.S_ISSOCK(self.path.lstat().st_mode):
                self.path.unlink()  # left over from a daemon that was killed
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._client, str(self.path), limit=MAX_REQUEST_BYTES)
        os.chmod(self.path, SOCKET_MODE)
        logger.debug("Listening on %s", self.path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for task in list(self._clients):
                task.cancel()
            await asyncio.gather(*self._clients, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None
        self.path.unlink(missing_ok=True)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), IDLE_TIMEOUT_S)
                except (TimeoutError, ConnectionError):
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    await self._send(writer, _error(None, "bad_request", "request too long"))
                    break
                if not line:
                    break
                request_id, result = await self._dispatch(line)
                if hasattr(result, "__aiter__"):
                    await self._stream(writer, request_id, result)
                    break
                if not await self._send(writer, result):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if task is not None:
                self._clients.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _stream(self, writer: asyncio.StreamWriter, request_id: Any, events: AsyncIterator[Any]) -> None:
        try:
            async for event in events:
                if not await self._send(writer, {"id": request_id, "ok": True, "event": event}):
                    break
        finally:
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, message: dict[str, Any]) -> bool:
        """Whether it could be sent (the client may have gone)."""
        try:
            writer.write(encode(message))
            await writer.drain()
        except (ConnectionError, OSError):
            return False
        return True

    async def _dispatch(self, line: bytes) -> tuple[Any, Any]:
        """(request id, the response, or an async iterator of events)."""
        request_id: Any = None
        cmd: Any = None
        try:
            try:
                request = json.loads(line)
            except ValueError as e:
                raise RequestError("bad_request", f"invalid JSON: {e}") from None
            if not isinstance(request, dict):
                raise RequestError("bad_request", "a request must be a JSON object")
            request_id = request.get("id")
            cmd = request.get("cmd")
            args = request.get("args") or {}
            if not isinstance(cmd, str):
                raise RequestError("bad_request", "missing cmd")
            if not isinstance(args, dict):
                raise RequestError("bad_request", "args must be an object")
            handler = self.handlers.get(cmd)
            if handler is None:
                raise RequestError("unknown_command", f"unknown command {cmd!r}")
            result = await handler(args)
            if hasattr(result, "__aiter__"):
                return request_id, result
            return request_id, {"id": request_id, "ok": True, "result": result}
        except asyncio.CancelledError:
            raise
        except RequestError as e:
            return request_id, _error(request_id, e.type, str(e))
        except Exception as e:  # a bug in one command must not take the socket down
            logger.exception("Command %s failed", cmd)
            return request_id, _error(request_id, "internal", str(e) or type(e).__name__)


def _error(request_id: Any, type_: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"type": type_, "message": message}}


async def request(path: Path, cmd: str, args: dict[str, Any] | None = None, timeout: float = 30) -> Any:
    """One request to the daemon; its result.

    Raises:
        OSError: The daemon cannot be reached (no socket, not running, no access).
        RequestError: The daemon answered with an error.
        TimeoutError: No answer within `timeout`.
    """
    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_unix_connection(str(path), limit=16 * 1024 * 1024)
        try:
            writer.write(encode({"id": 1, "cmd": cmd, "args": args or {}}))
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
    if not line:
        raise ConnectionResetError("the daemon closed the connection without answering")
    response = json.loads(line)
    if not response.get("ok"):
        error = response.get("error") or {}
        raise RequestError(error.get("type", "internal"), error.get("message", "unknown error"))
    return response.get("result")


async def events(path: Path, cmd: str, args: dict[str, Any] | None = None) -> AsyncIterator[Any]:
    """The events of a streaming command (`watch`) until the daemon stops.

    Raises:
        OSError: The daemon cannot be reached.
        RequestError: The daemon answered with an error.
    """
    reader, writer = await asyncio.open_unix_connection(str(path), limit=16 * 1024 * 1024)
    try:
        writer.write(encode({"id": 1, "cmd": cmd, "args": args or {}}))
        await writer.drain()
        while line := await reader.readline():
            response = json.loads(line)
            if not response.get("ok"):
                error = response.get("error") or {}
                raise RequestError(error.get("type", "internal"), error.get("message", "unknown error"))
            yield response.get("event")
    finally:
        writer.close()
