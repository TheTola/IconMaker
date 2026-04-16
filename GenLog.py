#!/usr/bin/env python3
"""
Shared rotating log helpers for IconMaker.

This module gives launcher, UI, tray, and ops code a consistent way to write
bounded log files without each subsystem inventing its own logging policy.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3


def app_data_dir() -> Path:
    """Return the user-writable root for persistent app data on the current platform."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / APP_ORG / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def logs_dir(*, base_dir: Optional[Path] = None) -> Path:
    """Return the log directory and ensure it exists before callers write to it."""
    root = Path(base_dir) if base_dir is not None else app_data_dir()
    path = root / "Logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file(name: str, *, base_dir: Optional[Path] = None) -> Path:
    """Normalize logger names into safe on-disk filenames."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name or "app"))
    return logs_dir(base_dir=base_dir) / f"{safe}.log"


def get_logger(name: str, *, base_dir: Optional[Path] = None) -> logging.Logger:
    """Return a cached rotating logger so logs stay bounded over long-running sessions."""
    logger_name = f"{APP_NAME}.{name}.{Path(base_dir).as_posix() if base_dir else 'default'}"
    logger = logging.getLogger(logger_name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(log_file(name, base_dir=base_dir), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def write_line(name: str, message: str, *, level: str = "info", base_dir: Optional[Path] = None) -> None:
    """Convenience wrapper for one-off writes from modules that do not manage loggers."""
    logger = get_logger(name, base_dir=base_dir)
    fn = getattr(logger, str(level).lower(), logger.info)
    fn(str(message))