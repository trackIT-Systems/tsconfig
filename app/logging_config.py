"""Centralized logging configuration for tsOS Configuration Manager.

This module provides consistent logging setup across all components,
optimized for journald consumption (no timestamps) with configurable verbosity.

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


def setup_logging(verbose: bool = False, log_level: str | None = None, verbosity: int = 0) -> None:
    """Set up logging configuration for the application.

    Args:
        verbose: Deprecated. If True, force DEBUG level (overrides other settings)
        log_level: Optional log level override (DEBUG, INFO, WARNING, ERROR)
        verbosity: Verbosity level (0=WARNING, 1=INFO, 2+=DEBUG)
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

    # Configure logging format (no timestamps - journald handles this)
    format_string = "%(name)s - %(levelname)s - %(message)s"

    # Set up basic configuration
    logging.basicConfig(
        level=level,
        format=format_string,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,  # Override any existing configuration
    )

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
