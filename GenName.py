#!/usr/bin/env python3
"""
GenName.py â€” Canonical Naming + Archive Storage Integrity

- Safe filenames (Windows + ASCII)
- Deterministic duplicate handling (existing wins)
- Atomic file operations
- Minimal, reliable helpers for managed archive storage
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Callable, Optional, Tuple

LogFn = Optional[Callable[[str], None]]

# =========================
# Constants
# =========================

_BAD_PATTERN = re.compile(r'[<>:"/\\|?*]')

WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".svg"}

MAX_NAME_LEN = 180

# =========================
# Logging
# =========================

def _log(logfn: LogFn, msg: str):
    if logfn:
        try:
            logfn(msg)
        except Exception:
            pass

# =========================
# Canonicalization
# =========================

def _ascii_only(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def sanitize_piece(s: str) -> str:
    s = _ascii_only(str(s or ""))
    s = _BAD_PATTERN.sub("", s)
    s = s.strip(" .")
    s = re.sub(r"\s+", " ", s)
    s = s.casefold()

    if not s:
        s = "untitled"

    if s in WINDOWS_RESERVED:
        s = f"{s}_file"

    return s


def canonical_filename(name: str) -> str:
    p = Path(name)
    stem = sanitize_piece(p.stem)
    ext = p.suffix.lower()

    max_stem = MAX_NAME_LEN - len(ext)
    if len(stem) > max_stem:
        stem = stem[:max_stem]

    return f"{stem}{ext}" if ext else stem


def canonical_key(name: str) -> str:
    return canonical_filename(name)

# =========================
# Helpers
# =========================

def _is_image(p: Path) -> bool:
    try:
        return p.is_file() and p.suffix.lower() in IMAGE_EXTS
    except Exception:
        return False


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return False


def _file_hash_limited(path: Path, max_bytes: int = 4_000_000):
    import hashlib
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            h.update(f.read(max_bytes))
        return h.hexdigest()
    except Exception:
        return None


def _is_identical(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except Exception:
        return False

    ha = _file_hash_limited(a)
    hb = _file_hash_limited(b)
    return ha is not None and ha == hb

# =========================
# Atomic operations
# =========================

def _atomic_copy(src: Path, dst: Path):
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _atomic_move(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
    except OSError:
        _atomic_copy(src, dst)
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass

# =========================
# Core operations
# =========================

def copy_into_archive_storage_strict(
    src: Path,
    archive_dir: Path,
    *,
    logfn: LogFn = None,
) -> Tuple[Optional[Path], Optional[str]]:

    src = Path(src)
    archive_dir = Path(archive_dir)

    if not src.exists() or not _is_image(src):
        return None, None

    archive_dir.mkdir(parents=True, exist_ok=True)

    dst = archive_dir / canonical_filename(src.name)

    if dst.exists() and not _same_file(src, dst):
        if _is_identical(src, dst):
            _log(logfn, f"SKIP identical: {src.name}")
            return dst, None

        _log(logfn, f"COLLISION: {src.name} -> {dst.name}")
        return dst, "collision"

    try:
        _atomic_copy(src, dst)
        _log(logfn, f"COPY: {src.name} -> {dst.name}")
        return dst, None
    except Exception as e:
        _log(logfn, f"ERR: copy failed {src} -> {dst}: {e}")
        return None, None


def move_within_archive_storage_strict(
    src: Path,
    archive_dir: Path,
    *,
    logfn: LogFn = None,
) -> Tuple[Optional[Path], Optional[str]]:

    src = Path(src)
    archive_dir = Path(archive_dir)

    if not src.exists() or not _is_image(src):
        return None, None

    archive_dir.mkdir(parents=True, exist_ok=True)

    dst = archive_dir / canonical_filename(src.name)

    if dst.exists() and not _same_file(src, dst):
        if _is_identical(src, dst):
            try:
                src.unlink(missing_ok=True)
            except Exception:
                pass
            _log(logfn, f"SKIP identical(move): {src.name}")
            return dst, None

        _log(logfn, f"COLLISION: {src.name} -> {dst.name}")
        return dst, "collision"

    try:
        _atomic_move(src, dst)
        _log(logfn, f"MOVE: {src.name} -> {dst.name}")
        return dst, None
    except Exception as e:
        _log(logfn, f"ERR: move failed {src} -> {dst}: {e}")
        return None, None

def copy_into_library_strict(
    src: Path,
    library_dir: Path,
    *,
    logfn: LogFn = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """Compatibility wrapper for older call sites."""
    return copy_into_archive_storage_strict(src, library_dir, logfn=logfn)


def move_into_library_strict(
    src: Path,
    library_dir: Path,
    *,
    logfn: LogFn = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """Compatibility wrapper for older call sites."""
    return move_within_archive_storage_strict(src, library_dir, logfn=logfn)