#!/usr/bin/env python3
"""
Application identity and version metadata shared across the IconForge runtime.

This module keeps the app name, organization, bundle metadata, and visible
version string in one place so the launcher, UI, tray worker, and packaging
steps all describe the same product.

During source-based development, the visible patch version advances when the
tracked project files change. Frozen builds keep the release version fixed so
distributed packages remain predictable.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Final

from PySide6 import QtCore

APP_ORG: Final[str] = "InfiniWorks"
# Keep this settings namespace and its derived Windows AppUserModelID stable.
APP_NAME: Final[str] = "IconMaker"
APP_VERSION_SERIES: Final[str] = "1.0"
APP_VERSION_BASE_PATCH: Final[int] = 3
APP_COPYRIGHT: Final[str] = "InfiniWorks"
APP_DESCRIPTION: Final[str] = "Managed archive-based icon conversion workflow."
APP_EXECUTABLE_NAME: Final[str] = "IconMaker.exe"
APP_DISPLAY_NAME: Final[str] = "IconForge"
APP_USER_MODEL_ID: Final[str] = f"{APP_ORG}.{APP_NAME}"
APP_VERSION_PATCH_KEY: Final[str] = "app_identity/dev_patch"
APP_VERSION_SIGNATURE_KEY: Final[str] = "app_identity/dev_signature"
APP_RELEASE_VERSION: Final[str] = f"{APP_VERSION_SERIES}.{APP_VERSION_BASE_PATCH}"
_VERSION_CACHE: str | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def _iter_version_tracked_files() -> list[Path]:
    root = _project_root()
    tracked: list[Path] = []
    for pattern in ("*.py", "*.spec", "*.versioninfo"):
        tracked.extend(sorted(root.glob(pattern)))
    return tracked


def _source_signature() -> str:
    digest = hashlib.sha256()
    for path in _iter_version_tracked_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(path.name.encode("utf-8", errors="ignore"))
        digest.update(str(stat.st_size).encode("ascii", errors="ignore"))
        digest.update(str(stat.st_mtime_ns).encode("ascii", errors="ignore"))
    return digest.hexdigest()


def resolve_runtime_version(settings: QtCore.QSettings | None = None) -> str:
    """
    Return the version string that should be shown by the current runtime.

    Development runs use a persistent patch counter keyed by the project file
    signature so the visible version changes only when the source changes.
    Frozen builds return the declared release version instead.
    """
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE

    if _is_frozen_runtime():
        _VERSION_CACHE = APP_RELEASE_VERSION
        return _VERSION_CACHE

    s = settings or QtCore.QSettings(APP_ORG, APP_NAME)
    signature = _source_signature()
    stored_signature = str(s.value(APP_VERSION_SIGNATURE_KEY, "") or "")
    try:
        stored_patch = int(s.value(APP_VERSION_PATCH_KEY, APP_VERSION_BASE_PATCH) or APP_VERSION_BASE_PATCH)
    except Exception:
        stored_patch = APP_VERSION_BASE_PATCH

    patch = max(APP_VERSION_BASE_PATCH, stored_patch)
    if stored_signature != signature:
        patch = APP_VERSION_BASE_PATCH if not stored_signature else patch + 1
        s.setValue(APP_VERSION_PATCH_KEY, patch)
        s.setValue(APP_VERSION_SIGNATURE_KEY, signature)
        s.sync()

    _VERSION_CACHE = f"{APP_VERSION_SERIES}.{patch}"
    return _VERSION_CACHE


APP_VERSION: str = resolve_runtime_version()
APP_DISPLAY_VERSION: str = f"{APP_DISPLAY_NAME} v{APP_VERSION}"


def apply_qt_application_identity(app: QtCore.QCoreApplication | None) -> None:
    """Apply consistent Qt application metadata when a QApplication exists."""
    if app is None:
        return
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    if hasattr(app, "setApplicationDisplayName"):
        app.setApplicationDisplayName(APP_DISPLAY_NAME)
    if hasattr(app, "setApplicationVersion"):
        app.setApplicationVersion(APP_VERSION)
