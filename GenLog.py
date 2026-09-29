#!/usr/bin/env python3
"""
Shared rotating log helpers for IconMaker.

This module gives launcher, UI, tray, and ops code a consistent way to write
bounded log files without each subsystem inventing its own logging policy.
"""
from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 3
_LOGGER_LOCK = threading.Lock()


def app_data_dir() -> Path:
    """Return the user-writable root for persistent app data on the current platform."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        if not root.is_absolute():
            root = Path.home() / "AppData" / "Local"
        return root / APP_ORG / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def logs_dir() -> Path:
    """Return the log directory and ensure it exists before callers write to it."""
    path = app_data_dir() / "Logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name or "app"))


def log_file(name: str) -> Path:
    """Normalize logger names into safe on-disk filenames."""
    return logs_dir() / f"{_safe_name(name)}.log"


def write_file(name: str, contents: str) -> Path | None:
    """Write a standalone log report without raising when storage is unavailable."""
    try:
        path = log_file(name)
        path.write_text(contents, encoding="utf-8", errors="ignore")
        return path
    except Exception:
        return None


def get_logger(name: str) -> logging.Logger:
    """Return a cached rotating logger so logs stay bounded over long-running sessions."""
    safe_name = _safe_name(name)
    logger = logging.getLogger(f"{APP_NAME}.{safe_name}")
    with _LOGGER_LOCK:
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
            return logger
        try:
            handler = RotatingFileHandler(
                log_file(safe_name), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
        except Exception:
            if not logger.handlers:
                logger.addHandler(logging.NullHandler())
            return logger
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        for old_handler in list(logger.handlers):
            if isinstance(old_handler, logging.NullHandler):
                logger.removeHandler(old_handler)
        logger.addHandler(handler)
    return logger


def write_line(name: str, message: str, *, level: str = "info") -> None:
    """Convenience wrapper for one-off writes from modules that do not manage loggers."""
    try:
        logger = get_logger(name)
        log_level = getattr(logging, str(level).upper(), logging.INFO)
        logger.log(log_level if isinstance(log_level, int) else logging.INFO, str(message))
    except Exception:
        pass
