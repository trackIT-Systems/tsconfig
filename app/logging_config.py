"""Centralized logging configuration for tsOS Configuration Manager.

This module provides consistent logging setup across all components,
optimized for journald consumption (no timestamps) with configurable verbosity.
"""

import logging
import os
import sys
from typing import Optional


def setup_logging(verbose: bool = False, log_level: Optional[str] = None) -> None:
    """Set up logging configuration for the application.
    
    Args:
        verbose: If True, force DEBUG level (overrides other settings)
        log_level: Optional log level override (DEBUG, INFO, WARNING, ERROR)
    """
    # Determine log level
    if verbose:
        level = logging.DEBUG
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
    
    # Set specific loggers to appropriate levels
    # Third-party libraries should respect the same log level as the main application
    logging.getLogger("httpx").setLevel(level)
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)  # HTTP access logs
    logging.getLogger("fastapi").setLevel(level)
    logging.getLogger("dbus").setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
