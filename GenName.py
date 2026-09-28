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
import tempfile
import unicodedata
from pathlib import Path
from typing import Callable, Optional, Tuple

LogFn = Optional[Callable[[str], None]]

# =========================
# Constants
# =========================

_BAD_PATTERN = re.compile(r'[<>:"/\\|?*]')

WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
    *(f"com{i}" for i in "¹²³"),
    *(f"lpt{i}" for i in "¹²³"),
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


def validate_image_stem(stem: str, *, suffix: str = ".png") -> str:
    """Validate an edited image name without changing the user's wording."""
    if not isinstance(stem, str) or not stem or not stem.strip():
        raise ValueError("Enter a name for the image.")
    if stem.endswith((" ", ".")):
        raise ValueError("Image names cannot end with a space or period.")
    if _BAD_PATTERN.search(stem) or any(ord(char) < 32 for char in stem):
        raise ValueError('Image names cannot contain < > : " / \\ | ? * or control characters.')
    if stem.split(".", 1)[0].casefold() in WINDOWS_RESERVED:
        raise ValueError("That image name is reserved by Windows. Choose another name.")
    if len(stem) + len(suffix) > MAX_NAME_LEN:
        raise ValueError(f"Image names must be {MAX_NAME_LEN - len(suffix)} characters or fewer.")
    return stem


def validate_archive_image_filename(name: str) -> str:
    """Accept a safe image filename, preserving its case, spaces, and Unicode."""
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("Enter a valid image filename.")
    suffix = Path(name).suffix
    if suffix.lower() not in IMAGE_EXTS:
        raise ValueError("Choose a supported image filename extension.")
    validate_image_stem(name[: -len(suffix)], suffix=suffix)
    return name

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


def _file_hash(path: Path):
    import hashlib
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _is_identical(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except Exception:
        return False

    ha = _file_hash(a)
    hb = _file_hash(b)
    return ha is not None and ha == hb

# =========================
# Atomic operations
# =========================

def _atomic_copy(src: Path, dst: Path):
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _atomic_copy_without_overwrite(src: Path, dst: Path):
    """Stage a copy beside the target, then publish it only if absent."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    os.close(fd)
    try:
        shutil.copy2(src, temp_name)
        try:
            os.link(temp_name, dst)
        except FileExistsError:
            raise
        except OSError:
            # Some configured archive volumes do not support hard links.
            created = False
            try:
                with open(temp_name, "rb") as source, open(dst, "xb") as target:
                    created = True
                    shutil.copyfileobj(source, target)
                shutil.copystat(temp_name, dst)
            except Exception:
                if created:
                    dst.unlink(missing_ok=True)
                raise
    finally:
        Path(temp_name).unlink(missing_ok=True)


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
    target_name: str | None = None,
) -> Tuple[Optional[Path], Optional[str]]:

    src = Path(src)
    archive_dir = Path(archive_dir)

    if not src.exists() or not _is_image(src):
        return None, None

    if target_name is not None:
        target_name = validate_archive_image_filename(target_name)
    archive_dir.mkdir(parents=True, exist_ok=True)

    dst = archive_dir / (target_name if target_name is not None else canonical_filename(src.name))

    if target_name is not None and dst.exists():
        _log(logfn, f"COLLISION: {src.name} -> {dst.name}")
        return dst, "collision"

    if dst.exists() and not _same_file(src, dst):
        if _is_identical(src, dst):
            _log(logfn, f"SKIP identical: {src.name}")
            return dst, None

        _log(logfn, f"COLLISION: {src.name} -> {dst.name}")
        return dst, "collision"

    try:
        if target_name is None:
            _atomic_copy(src, dst)
        else:
            _atomic_copy_without_overwrite(src, dst)
        _log(logfn, f"COPY: {src.name} -> {dst.name}")
        return dst, None
    except FileExistsError:
        if target_name is not None:
            return dst, "collision"
        if _is_identical(src, dst):
            return dst, None
        return dst, "collision"
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
