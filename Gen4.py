#!/usr/bin/env python3
"""
Gen4.py — IconMaker helper utilities (no UI)

Responsibilities
- Dynamic path helpers aligned with Gen2 EnginePaths
- Locate/load app icon + title pixmap in dev and frozen (PyInstaller) runs
- Provide compatibility-safe wrappers for icons-folder orphan cleanup

Gen2 owns conversion logic; Gen4 intentionally avoids image processing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui

import Gen2 as eng

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"


# ----------------------------
# Folder layout (source of truth: Gen2)
# ----------------------------

def _load_shared_library_root() -> Path | None:
    raw = str(QtCore.QSettings(APP_ORG, APP_NAME).value("library_root", "") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except Exception:
        return None


def _current_engine_paths() -> eng.EnginePaths:
    root = _load_shared_library_root()
    if root is not None:
        return eng.EnginePaths.from_library_root(root)
    return eng.default_engine_paths()


def ICON_IMAGES_DIR() -> Path:
    return _current_engine_paths().images_dir


def ICONS_DIR() -> Path:
    return _current_engine_paths().icons_dir


def LOGS_DIR() -> Path:
    return ICON_IMAGES_DIR() / "Logs"


def ensure_logs_dir() -> None:
    try:
        LOGS_DIR().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


ensure_logs_dir()


# ----------------------------
# Assets
# ----------------------------
# NOTE: These are *overrides* (string paths) that may be absolute or relative.
# They are intentionally not validated until lookup time.

APP_TITLE_IMAGE_OVERRIDE = "assets/Iconner.png"
APP_ICON_ICO_OVERRIDE = "assets/Iconner.ico"

# Folder names to search under each base directory
ASSET_DIR_CANDIDATES = ("assets", "Assets")

# Default asset filenames
APP_TITLE_PNG_NAME = "Iconner.png"
APP_ICON_ICO_NAME = "Iconner.ico"


def _dev_dir() -> Path:
    return Path(__file__).resolve().parent


def _exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _meipass_dir() -> Optional[Path]:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            return Path(getattr(sys, "_MEIPASS"))
        except Exception:
            return None
    return None


def _candidate_base_dirs() -> list[Path]:
    bases: list[Path] = []

    mp = _meipass_dir()
    if mp:
        bases.append(mp)

    bases.append(_exe_dir())
    bases.append(_dev_dir())

    out: list[Path] = []
    seen: set[str] = set()
    for b in bases:
        key = os.fspath(b)
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def _try_file(path_str: str) -> Optional[Path]:
    if not path_str:
        return None
    try:
        p = Path(path_str)
    except Exception:
        return None
    return p if p.is_file() else None


def find_asset(filename: str, *, override_path: str = "") -> Optional[Path]:
    """
    Locate an asset in this priority order:

    1) override_path (if it points to an existing file)
    2) <_MEIPASS>/assets or Assets (PyInstaller)
    3) <exe_dir>/assets or Assets
    4) <dev_dir>/assets or Assets
    """
    p = _try_file(override_path)
    if p:
        return p

    if not filename:
        return None

    for base in _candidate_base_dirs():
        direct = base / filename
        if direct.is_file():
            return direct

        for folder in ASSET_DIR_CANDIDATES:
            cand = base / folder / filename
            if cand.is_file():
                return cand

    return None


def _build_multi_size_icon(pm: QtGui.QPixmap) -> QtGui.QIcon:
    ico = QtGui.QIcon()
    for s in (256, 192, 128, 96, 64, 48, 40, 32, 24, 20, 16):
        ico.addPixmap(
            pm.scaled(
                s,
                s,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )
    return ico


def get_app_icon() -> QtGui.QIcon:
    """
    Return a QIcon for window/taskbar/tray.

    Priority:
    1) Icon .ico file
    2) Title .png converted into multi-size icon
    3) Theme fallback
    4) Empty QIcon
    """
    p_ico = find_asset(APP_ICON_ICO_NAME, override_path=APP_ICON_ICO_OVERRIDE)
    if p_ico:
        ico = QtGui.QIcon(str(p_ico))
        if not ico.isNull():
            return ico

    p_png = find_asset(APP_TITLE_PNG_NAME, override_path=APP_TITLE_IMAGE_OVERRIDE)
    if p_png:
        pm = QtGui.QPixmap(str(p_png))
        if not pm.isNull():
            return _build_multi_size_icon(pm)

    theme = QtGui.QIcon.fromTheme("application-icon")
    return theme if not theme.isNull() else QtGui.QIcon()


def get_title_pixmap() -> QtGui.QPixmap:
    p = find_asset(APP_TITLE_PNG_NAME, override_path=APP_TITLE_IMAGE_OVERRIDE)
    if not p:
        return QtGui.QPixmap()
    pm = QtGui.QPixmap(str(p))
    return pm if not pm.isNull() else QtGui.QPixmap()


# ----------------------------
# Icons folder cleanup (compat wrapper)
# ----------------------------

def clean_icons_folder(
    log_fn: Callable[[str], None] | None = None,
    *,
    icons_dir: Path | None = None,
    images_dir: Path | None = None,
    suffix: str = "",
    remove_orphans: bool = True,
    orphan_action: str = "delete",
    # Compatibility aliases (older callers)
    src_dir: Path | None = None,
    out_dir: Path | None = None,
) -> int:
    """
    Remove/move orphan .ico files.

    Compatibility:
    - Older callers may pass src_dir/out_dir instead of images_dir/icons_dir.

    Returns: number removed/moved.
    """
    if not remove_orphans:
        return 0

    if images_dir is None and src_dir is not None:
        images_dir = src_dir
    if icons_dir is None and out_dir is not None:
        icons_dir = out_dir

    images_dir = Path(images_dir or ICON_IMAGES_DIR())
    icons_dir = Path(icons_dir or ICONS_DIR())

    act = str(orphan_action or "").strip().lower()
    action = "quarantine" if act in ("quarantine", "trash", "recycle", "move") else "delete"

    try:
        # Preferred path-driven mode when the provided dirs match the expected structure.
        if icons_dir == images_dir / "Icons":
            paths = eng.EnginePaths.from_library_root(images_dir.parent)
            return eng.remove_orphan_icons(
                paths=paths,
                suffix=str(suffix or ""),
                action=action,
                logfn=log_fn,
            )

        # Compatibility fallback for callers with explicit custom dirs.
        return eng.remove_orphan_icons(
            images_dir=images_dir,
            icons_dir=icons_dir,
            suffix=str(suffix or ""),
            action=action,
            logfn=log_fn,
        )
    except Exception as e:
        if log_fn:
            log_fn(f"[CLEAN][ERR] {type(e).__name__}: {e}")
        return 0