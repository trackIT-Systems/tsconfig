"""Centralized logging configuration for tsOS Configuration Manager.

Follows tsOS-base's logging convention (docs/logging.md). Under systemd, every
record goes to the journal over its native protocol, with its priority, the
program's SYSLOG_IDENTIFIER and the source location as separate fields; the
message itself is only `<logger name>: <message>`. On a terminal (or in tests)
records go to stderr as `<LEVEL> <logger name>: <message>`.

Level policy:
    ERROR   - an operation failed and needs attention
    WARNING - unexpected but handled (default)
    INFO    - one-off events only: startup, login/logout, config written,
              service restarted, deployment, shell/BLE session lifecycle.
              Never per-request or periodic.
    DEBUG   - everything chatty: request tracing, token refresh, polling,
              per-write details, HTTP access logs
"""

import logging
import os
import socket
import struct
import sys

JOURNAL_SOCKET = "/run/systemd/journal/socket"

# Python level -> syslog priority (RFC 5424); 0, 1 and 5 are not used
PRIORITIES = ((logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6))
DEBUG_PRIORITY = 7

# Longer messages are cut, so a record always fits in one datagram
MAX_MESSAGE = 32 * 1024


def syslog_priority(levelno: int) -> int:
    for level, priority in PRIORITIES:
        if levelno >= level:
            return priority
    return DEBUG_PRIORITY


def stderr_is_journal() -> bool:
    """Whether systemd connected stderr to the journal.

    systemd sets JOURNAL_STREAM to the device and inode of that stream; a
    process whose stderr was redirected since (or a child that inherited the
    variable) has a different stderr. See systemd.exec(5).
    """
    stream = os.environ.get("JOURNAL_STREAM")
    if not stream:
        return False
    try:
        dev, ino = (int(part) for part in stream.split(":"))
        stat = os.fstat(sys.stderr.fileno())
    except (ValueError, OSError, AttributeError):
        return False
    return (stat.st_dev, stat.st_ino) == (dev, ino)


def journal_field(key: str, value: str) -> bytes:
    """One field of the native journal protocol; a value with a newline is sent with its length."""
    data = value.encode("utf-8", "replace")
    if b"\n" in data:
        return key.encode() + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"
    return key.encode() + b"=" + data + b"\n"


class JournalHandler(logging.Handler):
    """Sends records to journald over its native protocol (https://systemd.io/JOURNAL_NATIVE_PROTOCOL/).

    A record that cannot be sent (journald restarting) is written to stderr
    instead, with its priority as `<N>` prefix, which journald also understands.
    """

    def __init__(self, identifier: str, path: str = JOURNAL_SOCKET):
        super().__init__()
        self.identifier = identifier
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.socket.connect(path)
        except OSError:
            self.socket.close()
            raise

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)[:MAX_MESSAGE]
            priority = str(syslog_priority(record.levelno))
            fields = (
                ("MESSAGE", message),
                ("PRIORITY", priority),
                ("SYSLOG_IDENTIFIER", self.identifier),
                ("CODE_FILE", record.pathname),
                ("CODE_LINE", str(record.lineno)),
                ("CODE_FUNC", record.funcName),
            )
            try:
                self.socket.send(b"".join(journal_field(key, value) for key, value in fields))
            except OSError:
                sys.stderr.write(f"<{priority}>{message}\n")
                sys.stderr.flush()
        except Exception:  # noqa: BLE001 - logging must not raise into the caller
            self.handleError(record)

    def close(self) -> None:
        self.socket.close()
        super().close()


def _handler(identifier: str | None) -> logging.Handler:
    """The journal handler under systemd, a stderr handler otherwise (or when journald is unreachable)."""
    if stderr_is_journal():
        try:
            handler: logging.Handler = JournalHandler(identifier or os.path.basename(sys.argv[0]) or "python3")
            handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
            return handler
        except OSError:
            pass
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    return handler


def setup_logging(
    verbose: bool = False, log_level: str | None = None, verbosity: int = 0, identifier: str | None = None
) -> None:
    """Set up logging configuration for the application.

    Args:
        verbose: Deprecated. If True, force DEBUG level (overrides other settings)
        log_level: Optional log level override (DEBUG, INFO, WARNING, ERROR)
        verbosity: Verbosity level (0=WARNING, 1=INFO, 2+=DEBUG)
        identifier: SYSLOG_IDENTIFIER in the journal, the program's name (default: the executable's name)
    """
    # Determine log level
    if verbose:
        # Backward compatibility
        level = logging.DEBUG
    elif verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    elif log_level:
        level = getattr(logging, log_level.upper(), logging.WARNING)
    else:
        # Read from environment variable, default to WARNING
        env_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
        level = getattr(logging, env_level, logging.WARNING)

    logging.basicConfig(
        level=level,
        handlers=[_handler(identifier)],
        force=True,  # Override any existing configuration
    )

    # uvicorn installs handlers of its own that write plain lines to stderr,
    # which the journal would store as info; route its records through ours
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Per-request chatter (HTTP access logs, httpx client calls) is DEBUG-only:
    # at INFO it would log every UI polling GET. Below DEBUG, pin it to WARNING.
    # ACCESS_LOG_LEVEL overrides this explicitly.
    access_env = os.environ.get("ACCESS_LOG_LEVEL")
    if access_env:
        access_level = getattr(logging, access_env.upper(), logging.WARNING)
    else:
        access_level = level if level <= logging.DEBUG else max(level, logging.WARNING)
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(access_level)

    # Other third-party libraries follow the application level
    for name in ("uvicorn", "fastapi", "dbus"):
        logging.getLogger(name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
