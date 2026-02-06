"""Logging configuration — rotating file + console handlers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from polytrage.config import LogSettings


def setup_logging(
    settings: LogSettings,
    *,
    headless: bool = False,
    verbose: bool = False,
) -> None:
    """Configure root logger with file and console handlers.

    - File: always logs at the configured level with rotation.
    - Console: full output in interactive mode, WARNING+ in headless mode.
    - verbose flag overrides console to DEBUG.
    """
    root = logging.getLogger()

    # Clear any existing handlers (e.g. from basicConfig in tests)
    root.handlers.clear()

    level = getattr(logging, settings.level.upper(), logging.INFO)
    root.setLevel(logging.DEBUG)  # Capture everything; handlers filter

    fmt = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — rotating
    file_handler = RotatingFileHandler(
        settings.file,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    if verbose:
        console_handler.setLevel(logging.DEBUG)
    elif headless:
        console_handler.setLevel(logging.WARNING)
    else:
        console_handler.setLevel(level)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)
