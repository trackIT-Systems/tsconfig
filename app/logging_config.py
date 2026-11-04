"""Centralized logging configuration for tsOS Configuration Manager.

Follows tsOS-base's logging convention (docs/logging.md). Under systemd, every
record goes to the journal through python3-systemd's JournalHandler, with its
priority, the program's SYSLOG_IDENTIFIER and the source location as separate
fields; the message itself is only `<logger name>: <message>`. On a terminal,
in tests, in the Docker image or without python3-systemd (a C extension from
Debian that does not install on macOS) records go to stderr as
`<LEVEL> <logger name>: <message>`.

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
import sys

try:
    from systemd.journal import JournalHandler
except ImportError:
    JournalHandler = None


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


def _handler(identifier: str | None) -> logging.Handler:
    """The journal handler under systemd, a stderr handler otherwise."""
    if JournalHandler is not None and stderr_is_journal():
        identifier = identifier or os.path.basename(sys.argv[0]) or "python3"
        handler: logging.Handler = JournalHandler(SYSLOG_IDENTIFIER=identifier)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        return handler
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

    # Other third-party libraries follow the application level. uvicorn sets
    # uvicorn.error to INFO itself, so it has to be named, not only its parent
    for name in ("uvicorn", "uvicorn.error", "fastapi", "dbus"):
        logging.getLogger(name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
