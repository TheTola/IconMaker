#!/usr/bin/env python3
"""
Shared helper utilities for IconMaker runtime assets and storage paths.

Gen4 bridges UI and tray code to stable project resources. It resolves the
current archive-storage layout, finds branding/icon assets in source and frozen
builds, and provides compatibility-safe wrappers around engine cleanup helpers.
It intentionally avoids owning conversion logic or UI behavior.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui

import Gen2 as eng
import GenOps
from AppIdentity import APP_NAME, APP_ORG

IS_MAC = sys.platform == "darwin"


def _load_shared_archive_storage_root() -> Path | None:
    """Read the shared archive-storage root used by the rest of the application."""
    return GenOps.load_archive_storage_root(QtCore.QSettings(APP_ORG, APP_NAME))


def _current_engine_paths() -> eng.EnginePaths:
    """Resolve the active deterministic storage layout for the current settings."""
    root = _load_shared_archive_storage_root()
    if root is not None:
        return eng.EnginePaths.from_archive_storage_root(root)
    return eng.default_engine_paths()


def ICON_IMAGES_DIR() -> Path:
    """Return the managed source-images folder for the active archive root."""
    return _current_engine_paths().images_dir


def ICONS_DIR() -> Path:
    """Return the generated-icons folder that mirrors the managed source tree."""
    return _current_engine_paths().icons_dir


def LOGS_DIR() -> Path:
    """Keep application logs in the managed storage's internal Logs directory."""
    return ICON_IMAGES_DIR() / "Logs"


def ensure_logs_dir() -> None:
    """Create the shared Logs directory early so launcher, UI, and tray can log safely."""
    try:
        LOGS_DIR().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


ensure_logs_dir()


# These override strings may be absolute or relative. Validation is deferred
# until lookup so packaged and source runs can share the same config surface.
APP_TITLE_IMAGE_OVERRIDE = "assets/Iconner.png"
APP_ICON_MAC_OVERRIDE = "assets/IconMaker.icns"
APP_ICON_ICO_OVERRIDE = "assets/Iconner.ico"

ASSET_DIR_CANDIDATES = ("assets", "Assets")
APP_TITLE_PNG_NAME = "Iconner.png"
APP_ICON_MAC_NAME = "IconMaker.icns"
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
    """Search packaged assets first, then executable, then source checkout paths."""
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
    """Return a concrete file path when an override points to an existing file."""
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
    """Build a QIcon with common sizes so tray, taskbar, and window surfaces stay sharp."""
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
    1) macOS .icns bundle icon when available on macOS
    2) Icon .ico file
    3) Title .png converted into multi-size icon
    4) Theme fallback
    5) Empty QIcon
    """
    if IS_MAC:
        p_icns = find_asset(APP_ICON_MAC_NAME, override_path=APP_ICON_MAC_OVERRIDE)
        if p_icns:
            ico = QtGui.QIcon(str(p_icns))
            if not ico.isNull():
                return ico

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
    """Load the branding image used by the custom title and header surfaces."""
    p = find_asset(APP_TITLE_PNG_NAME, override_path=APP_TITLE_IMAGE_OVERRIDE)
    if not p:
        return QtGui.QPixmap()
    pm = QtGui.QPixmap(str(p))
    return pm if not pm.isNull() else QtGui.QPixmap()

def clean_icons_folder(
    log_fn: Callable[[str], None] | None = None,
    *,
    icons_dir: Path | None = None,
    images_dir: Path | None = None,
    suffix: str = "",
    remove_orphans: bool = True,
    orphan_action: str = "delete",
    # Older call sites may still use src_dir/out_dir names for the same concepts.
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
        # When the dirs match the managed archive layout, use EnginePaths so orphan
        # detection stays aligned with the deterministic storage model.
        if icons_dir == images_dir / "Icons":
            paths = eng.EnginePaths.from_archive_storage_root(images_dir.parent)
            return eng.remove_orphan_icons(
                paths=paths,
                suffix=str(suffix or ""),
                action=action,
                logfn=log_fn,
            )

        # Callers with custom dirs can still use the lower-level compatibility path.
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
