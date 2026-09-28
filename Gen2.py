#!/usr/bin/env python3
"""
Core archive-storage and icon-generation engine for IconMaker.

Gen2 owns the filesystem rules for managed archive storage. It knows where
source images live, where generated icons live, how imports are copied into
managed storage, and how icon output mirrors the source-image folder layout.

This module deliberately avoids UI concerns. Gen1 and GenArchive present the
workflow to the user, while Gen2 defines the deterministic behavior that keeps
archive storage safe, portable, and predictable.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError

from GenName import (
    copy_into_archive_storage_strict,
    move_within_archive_storage_strict,
    canonical_key,
    sanitize_piece,
    validate_archive_image_filename,
)


@dataclass(frozen=True)
class EnginePaths:
    """Resolved archive storage layout shared across engine and UI modules."""
    storage_root: Path
    images_dir: Path
    icons_dir: Path

    @classmethod
    def from_archive_storage_root(cls, archive_storage_root: Path) -> "EnginePaths":
        root = Path(archive_storage_root).resolve()
        return cls(
            storage_root=root,
            images_dir=root / "Icon Images",
            icons_dir=root / "Icon Images" / "Icons",
        )

    @property
    def library_root(self) -> Path:
        """Legacy compatibility for older call sites."""
        return self.storage_root

    @classmethod
    def from_library_root(cls, library_root: Path) -> "EnginePaths":
        """Legacy compatibility for older call sites."""
        return cls.from_archive_storage_root(library_root)


__all__ = [
    "IMAGE_EXTS",
    "DEFAULT_SIZES",
    "QUALITY_SIZES",
    "AUTO_FULL_SIZES",
    "DEFAULT_ARCHIVE_STORAGE_ROOT",
    "DEFAULT_OUTPUT_DIR",
    "PADDING_PRESETS",
    "normalize_padding_mode",
    "EnginePaths",
    "default_engine_paths",
    "resolve_engine_paths",
    "ensure_archive_storage_dirs",
    "list_archive_source_images",
    "build_archive_snapshot",
    "set_archive_storage_root",
    "ScanReport",
    "ImageDiscoveryReport",
    "diagnose_image_discovery",
    "parse_sizes",
    "quality_range",
    "quality_preset_sizes",
    "find_images",
    "make_ico",
    "unique_path",
    "normalize_archive_source_images",
    "reconcile_archive_image_copies",
    "archive_icon_path_for_source_image",
    "mirror_copy_to_archive_sources",
    "mirror_copy_to_archive_sources_ex",
    "list_missing_icon_tasks",
    "convert_many",
    "scan_archive_sources_and_convert",
    "remove_orphan_icons",
]


IMAGE_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".svg"}
_COPY_STEM = re.compile(r"^(.+?) - copy(?: \(((?:[2-9]|[1-9]\d+))\))?$", re.IGNORECASE)

QUALITY_SIZES = tuple(sorted(
    {8, 16, 24, 32, 48, 64, 96, 128, 256} | {20, 30, 36, 40, 60, 72, 80}
))
DEFAULT_SIZES: List[int] = [size for size in QUALITY_SIZES if size >= 16]
ICO_MAX_SIZE = 256
ICON_WRITE_TEMP_PREFIX = ".iconmaker-"
AUTO_FULL_SIZES: List[int] = list(range(8, ICO_MAX_SIZE + 1, 8))
DEFAULT_ARCHIVE_STORAGE_ROOT = Path.home() / "Desktop"

# These aliases keep older call sites working while newer code relies on
# EnginePaths instances loaded from the chosen archive storage root.
_DEFAULT_PATHS = EnginePaths.from_archive_storage_root(DEFAULT_ARCHIVE_STORAGE_ROOT)
ICON_IMAGES_DIR: Path = _DEFAULT_PATHS.images_dir
ICONS_DIR: Path = _DEFAULT_PATHS.icons_dir


def default_engine_paths() -> EnginePaths:
    return EnginePaths.from_archive_storage_root(DEFAULT_ARCHIVE_STORAGE_ROOT)


def resolve_engine_paths(
    *,
    paths: EnginePaths | None = None,
    archive_storage_root: Path | None = None,
    library_root: Path | None = None,
) -> EnginePaths:
    if paths is not None:
        return paths
    if archive_storage_root is None and library_root is not None:
        archive_storage_root = library_root
    if archive_storage_root is not None:
        return EnginePaths.from_archive_storage_root(archive_storage_root)
    return default_engine_paths()


def ensure_archive_storage_dirs(paths: EnginePaths | None = None) -> EnginePaths:
    resolved = resolve_engine_paths(paths=paths)
    for folder in (resolved.images_dir, resolved.icons_dir):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return resolved


def list_archive_source_images(paths: EnginePaths | None = None) -> List[Path]:
    resolved = resolve_engine_paths(paths=paths)
    return _iter_archive_source_images(paths=resolved, recursive=True)


def build_archive_snapshot(paths: EnginePaths | None = None):
    resolved = resolve_engine_paths(paths=paths)
    snapshot = set()
    for path in _iter_archive_source_images(paths=resolved, recursive=True):
        try:
            snapshot.add((str(path), path.stat().st_mtime))
        except Exception:
            pass
    return snapshot
def set_archive_storage_root(archive_storage_root: Path) -> EnginePaths:
    """Return resolved engine paths for the given archive storage root."""
    resolved = EnginePaths.from_archive_storage_root(archive_storage_root)
    ensure_archive_storage_dirs(resolved)
    return resolved


ensure_archive_storage_dirs(default_engine_paths())
DEFAULT_OUTPUT_DIR = str(default_engine_paths().icons_dir)

PADDING_PRESETS = {
    "None": 1.0,
    "Minor": 0.88,
    "Extra": 0.80,
}
_PADDING_ALIASES = {
    "none": "None",
    "tight": "None",
    "minor": "Minor",
    "balanced": "Minor",
    "extra": "Extra",
}


def normalize_padding_mode(value: str | None) -> str:
    """Return the current padding label, including legacy saved choices."""
    return _PADDING_ALIASES.get(str(value or "").strip().casefold(), "None")


def _parse_padding_mode(value: str) -> str:
    choice = str(value).strip().casefold()
    if choice not in _PADDING_ALIASES:
        raise argparse.ArgumentTypeError("padding must be None, Minor, or Extra")
    return _PADDING_ALIASES[choice]
# Progress callbacks describe work without coupling the engine to any
# particular UI, tray, or logging surface.
ProgressCB = Callable[[str, int, int, Optional[Path]], None]


def _safe_log(logfn: Callable[[str], None] | None, msg: str) -> None:
    """Call logger without ever throwing (workers must not crash)."""
    if not logfn:
        return
    try:
        logfn(msg)
    except Exception:
        pass


def _safe_progress(progress_cb: ProgressCB | None, label: str, i: int, total: int, path: Path | None) -> None:
    """Call progress callback without ever throwing (workers must not crash)."""
    if not progress_cb:
        return
    try:
        progress_cb(label, i, total, path)
    except Exception:
        pass


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _archive_quarantine_dir(paths: EnginePaths | None = None, images_dir: Path | None = None) -> Path:
    if paths is not None:
        base = paths.images_dir
    else:
        base = Path(images_dir) if images_dir is not None else Path(ICON_IMAGES_DIR)
    return base / "_Quarantine"

def _is_reserved_archive_path(
    p: Path,
    images_dir: Path | None = None,
    icons_dir: Path | None = None,
    paths: EnginePaths | None = None,
) -> bool:
    if paths is not None:
        base = paths.images_dir
        icons = paths.icons_dir
    else:
        base = Path(images_dir) if images_dir is not None else Path(ICON_IMAGES_DIR)
        icons = Path(icons_dir) if icons_dir is not None else Path(ICONS_DIR)

    quarantine = _archive_quarantine_dir(paths=paths, images_dir=base)
    temporary = base / "Temporary"

    return (
        _is_under(p, icons)
        or _is_under(p, quarantine)
        or _is_under(p, temporary)
    )


def _iter_archive_source_images(
    paths: EnginePaths | None = None,
    *,
    recursive: bool = True,
) -> List[Path]:
    if paths is None:
        base = resolve_engine_paths(paths=paths).images_dir
    else:
        base = paths.images_dir

    if not base.exists():
        return []

    try:
        iterator = base.rglob("*") if recursive else base.glob("*")
    except Exception:
        return []

    out: List[Path] = []
    for p in iterator:
        if not _is_image_file(p):
            continue
        if _is_reserved_archive_path(p, paths=paths):
            continue
        out.append(p)
    return out


def _canonical_relative_file_key(p: Path, base: Path) -> str:
    rel = p.relative_to(base)
    folder_parts = [canonical_key(part) for part in rel.parts[:-1]]
    stem_key = canonical_key(Path(rel.name).stem)
    return "/".join([*folder_parts, stem_key])


def _icon_names_for_images(images: Sequence[Path]) -> Dict[Path, str]:
    """Keep unique stems unchanged and disambiguate competing source images."""
    groups: Dict[Tuple[Path, str], List[Path]] = {}
    for image in images:
        groups.setdefault((image.parent, sanitize_piece(image.stem)), []).append(image)

    used_by_dir: Dict[Path, set[str]] = {}
    for parent, stem in groups:
        used_by_dir.setdefault(parent, set()).add(stem)

    names: Dict[Path, str] = {}
    for (parent, stem), members in sorted(groups.items(), key=lambda item: (str(item[0][0]).casefold(), item[0][1])):
        if len(members) == 1:
            names[members[0]] = stem
            continue
        used = used_by_dir[parent]
        for image in sorted(members, key=lambda path: (path.suffix.casefold(), path.name.casefold(), path.name)):
            base = f"{stem}~{sanitize_piece(image.suffix.lstrip('.'))}"
            candidate = base
            number = 2
            while candidate in used:
                candidate = f"{base}~{number}"
                number += 1
            names[image] = candidate
            used.add(candidate)
    return names


def _icon_target_for_image(
    img: Path,
    *,
    paths: EnginePaths | None = None,
    images_dir: Path | None = None,
    icons_dir: Path | None = None,
    suffix: str = "",
    icon_names: Dict[Path, str] | None = None,
) -> Path:
    if paths is not None:
        base = paths.images_dir
        icons = paths.icons_dir
    else:
        base = Path(images_dir) if images_dir is not None else Path(ICON_IMAGES_DIR)
        icons = Path(icons_dir) if icons_dir is not None else Path(ICONS_DIR)

    rel_parent = img.relative_to(base).parent
    if icon_names is None:
        try:
            siblings = [path for path in img.parent.iterdir() if _is_image_file(path)]
        except OSError:
            siblings = []
        if img not in siblings:
            siblings.append(img)
        icon_names = _icon_names_for_images(siblings)
    out_name = f"{icon_names.get(img, sanitize_piece(img.stem))}{suffix}.ico"
    target_dir = icons / rel_parent
    return target_dir / out_name


def archive_icon_path_for_source_image(source_image: Path, *, paths: EnginePaths) -> Path:
    return _icon_target_for_image(Path(source_image), paths=paths)


# =========================
# Image discovery diagnostics
# =========================

@dataclass(frozen=True)
class ImageDiscoveryReport:
    input_path: Path
    recursive: bool
    accepted_exts: List[str]
    found_total: int
    found_images: int
    skipped_reason_counts: Dict[str, int]
    sample_found: List[Path]
    sample_skipped: List[Tuple[Path, str]]


def _is_image_file(p: Path) -> bool:
    try:
        return p.is_file() and p.suffix.lower() in IMAGE_EXTS
    except Exception:
        return False


def parse_sizes(s: str) -> List[int]:
    """
    Parse sizes:
      "16,24,32" -> [16,24,32]
      "auto" -> DEFAULT_SIZES
      "full" -> AUTO_FULL_SIZES
    """
    s = (s or "").strip().lower()
    if not s or s == "auto":
        return list(DEFAULT_SIZES)
    if s == "full":
        return list(AUTO_FULL_SIZES)

    out: List[int] = []
    for part in s.replace(" ", "").split(","):
        if not part:
            continue
        try:
            n = int(part)
            if 8 <= n <= ICO_MAX_SIZE:
                out.append(n)
        except Exception:
            pass
    return sorted(set(out))


def _normalize_sizes(sizes: Sequence[int]) -> List[int]:
    out: List[int] = []
    for n in sizes:
        try:
            n2 = int(n)
            if 8 <= n2 <= ICO_MAX_SIZE:
                out.append(n2)
        except Exception:
            pass
    return sorted(set(out))


def quality_range(preset: str) -> tuple[int, int]:
    """Read a saved icon-size range, including legacy maxima above ICO limits."""
    try:
        minimum, maximum = (int(part) for part in preset.replace("–", "-").split("-", 1))
    except (AttributeError, ValueError):
        return 16, ICO_MAX_SIZE

    minimum = minimum if minimum in QUALITY_SIZES else 16
    maximum = min(maximum, ICO_MAX_SIZE)
    maximum = maximum if maximum in QUALITY_SIZES else ICO_MAX_SIZE
    return min(minimum, maximum), maximum


def quality_preset_sizes(preset: str) -> List[int]:
    minimum, maximum = quality_range(preset)
    return [size for size in QUALITY_SIZES if minimum <= size <= maximum]


def _normalize_ico_sizes(sizes: Sequence[int] | None) -> List[int]:
    return _normalize_sizes(DEFAULT_SIZES if sizes is None else sizes)


def diagnose_image_discovery(input_path: Path, recursive: bool) -> ImageDiscoveryReport:
    input_path = Path(input_path)

    skipped: Dict[str, int] = {}
    found_total = 0
    found_images = 0
    sample_found: List[Path] = []
    sample_skipped: List[Tuple[Path, str]] = []
    accepted_exts = sorted(IMAGE_EXTS)

    def bump(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    if not input_path.exists():
        bump("missing_path")
        return ImageDiscoveryReport(
            input_path=input_path,
            recursive=recursive,
            accepted_exts=accepted_exts,
            found_total=0,
            found_images=0,
            skipped_reason_counts=skipped,
            sample_found=[],
            sample_skipped=[],
        )

    paths: Iterable[Path]
    if input_path.is_file():
        paths = [input_path]
    else:
        paths = input_path.rglob("*") if recursive else input_path.glob("*")

    for p in paths:
        found_total += 1

        if not p.exists():
            bump("missing_entry")
            if len(sample_skipped) < 12:
                sample_skipped.append((p, "missing_entry"))
            continue

        if p.is_dir():
            bump("directory")
            continue

        if not p.is_file():
            bump("not_a_file")
            continue

        if p.suffix.lower() not in IMAGE_EXTS:
            bump("unsupported_extension")
            if len(sample_skipped) < 12:
                sample_skipped.append((p, "unsupported_extension"))
            continue

        found_images += 1
        if len(sample_found) < 12:
            sample_found.append(p)

    return ImageDiscoveryReport(
        input_path=input_path,
        recursive=recursive,
        accepted_exts=accepted_exts,
        found_total=found_total,
        found_images=found_images,
        skipped_reason_counts=skipped,
        sample_found=sample_found,
        sample_skipped=sample_skipped,
    )


def find_images(input_path: Path, recursive: bool = True) -> List[Path]:
    input_path = Path(input_path)

    if not input_path.exists():
        return []

    if input_path.is_file():
        return [input_path] if _is_image_file(input_path) else []

    it = input_path.rglob("*") if recursive else input_path.glob("*")
    out: List[Path] = []
    for p in it:
        if _is_image_file(p):
            out.append(p)
    return out


# =========================
# Utility: unique path
# =========================

def unique_path(p: Path) -> Path:
    """If p exists, generate a unique name like 'name (2).ext'."""
    p = Path(p)
    if not p.exists():
        return p

    stem = p.stem
    ext = p.suffix
    parent = p.parent

    n = 2
    while True:
        cand = parent / f"{stem} ({n}){ext}"
        if not cand.exists():
            return cand
        n += 1


# =========================
# Image loading: raster + svg
# =========================

def _load_svg_to_rgba(svg_path: Path, output_size: tuple[int, int] | None = None) -> Image.Image:
    try:
        import cairosvg
        options = (
            {"output_width": output_size[0], "output_height": output_size[1]}
            if output_size else {}
        )
        png_bytes = cairosvg.svg2png(url=str(svg_path), **options)
        im = Image.open(BytesIO(png_bytes))
        return im.convert("RGBA")
    except Exception as cairo_error:
        try:
            from PySide6 import QtCore, QtGui, QtSvg

            renderer = QtSvg.QSvgRenderer(str(svg_path))
            if not renderer.isValid():
                raise ValueError("Invalid SVG")
            if output_size is None:
                size = renderer.defaultSize()
                width, height = size.width(), size.height()
                if width <= 0 or height <= 0:
                    view_box = renderer.viewBoxF()
                    width, height = view_box.width(), view_box.height()
                if width <= 0 or height <= 0:
                    width = height = 256
                scale = 256 / max(width, height)
                width = max(1, round(width * scale))
                height = max(1, round(height * scale))
            else:
                width, height = output_size

            image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
            image.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(image)
            try:
                renderer.render(painter)
            finally:
                painter.end()
            buffer = QtCore.QBuffer()
            if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
                raise RuntimeError("Could not encode rendered SVG")
            return Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")
        except Exception as qt_error:
            raise RuntimeError(f"SVG render failed (CairoSVG: {cairo_error}; QtSvg: {qt_error})") from qt_error


def _load_image_any(path: Path) -> Image.Image:
    path = Path(path)
    if path.suffix.lower() == ".svg":
        return _load_svg_to_rgba(path)

    try:
        im = Image.open(path)
        im.load()
        return im
    except UnidentifiedImageError:
        raise
    except Exception as e:
        raise RuntimeError(str(e))


def _autocrop_alpha(im: Image.Image) -> Image.Image:
    """Crop transparent borders safely. If fully transparent, returns original."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    alpha = im.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return im
    return im.crop(bbox)


def _svg_intrinsic_size(svg_path: Path) -> tuple[int, int]:
    try:
        from PySide6 import QtSvg

        renderer = QtSvg.QSvgRenderer(str(svg_path))
        if renderer.isValid():
            size = renderer.defaultSize()
            if size.width() > 0 and size.height() > 0:
                return size.width(), size.height()
            view_box = renderer.viewBoxF()
            if view_box.width() > 0 and view_box.height() > 0:
                return max(1, round(view_box.width())), max(1, round(view_box.height()))
    except Exception:
        pass
    return _load_svg_to_rgba(svg_path).size


def _render_sparse_svg_frame(
    svg_path: Path,
    size: int,
    reference_size: tuple[int, int],
    bounds: tuple[int, int, int, int],
    *,
    content_scale: float,
    keep_alpha: bool,
) -> Image.Image:
    from PySide6 import QtCore, QtGui, QtSvg

    renderer = QtSvg.QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError("Invalid SVG")
    content_side = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    sampled_content_side = max(64, size * 4)
    canvas_side = math.ceil(sampled_content_side / content_scale)
    scale = sampled_content_side / content_side
    image = QtGui.QImage(canvas_side, canvas_side, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    try:
        renderer.render(
            painter,
            QtCore.QRectF(
                (canvas_side - (bounds[2] - bounds[0]) * scale) / 2 - bounds[0] * scale,
                (canvas_side - (bounds[3] - bounds[1]) * scale) / 2 - bounds[1] * scale,
                reference_size[0] * scale,
                reference_size[1] * scale,
            ),
        )
    finally:
        painter.end()
    buffer = QtCore.QBuffer()
    if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode rendered SVG")
    frame = Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")
    if not keep_alpha:
        frame = frame.convert("RGB")
    return frame.resize((size, size), Image.LANCZOS)


def _svg_frames(
    svg_path: Path, sizes: Sequence[int], *, content_scale: float, keep_alpha: bool
) -> List[Image.Image]:
    source_width, source_height = _svg_intrinsic_size(svg_path)
    reference_side = 1024
    reference_size = (
        max(1, round(source_width * reference_side / max(source_width, source_height))),
        max(1, round(source_height * reference_side / max(source_width, source_height))),
    )
    reference = _load_svg_to_rgba(svg_path, reference_size)
    bounds = reference.getchannel("A").getbbox() or (0, 0, *reference_size)
    content_side = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    use_clipped_renderer = (
        max(reference_size) * max(64, max(sizes) * 4) / content_side > 4096
    )
    frames = []
    for size in sizes:
        # Render the vector anew for each frame, with at least four samples per
        # output pixel across the visible artwork.
        if use_clipped_renderer:
            # Draw into the cropped viewport instead of allocating a huge
            # image for a mostly transparent SVG viewBox.
            frames.append(_render_sparse_svg_frame(
                svg_path, size, reference_size, bounds,
                content_scale=content_scale, keep_alpha=keep_alpha,
            ))
            continue
        render_scale = max(64, size * 4) / content_side
        render_size = (
            max(1, math.ceil(reference_size[0] * render_scale)),
            max(1, math.ceil(reference_size[1] * render_scale)),
        )
        rendered = _load_svg_to_rgba(svg_path, render_size)
        crop_box = (
            math.floor(bounds[0] * render_size[0] / reference_size[0]),
            math.floor(bounds[1] * render_size[1] / reference_size[1]),
            math.ceil(bounds[2] * render_size[0] / reference_size[0]),
            math.ceil(bounds[3] * render_size[1] / reference_size[1]),
        )
        cropped = rendered.crop(crop_box)
        if keep_alpha:
            canvas = _pad_to_square_rgba(cropped, content_scale=content_scale)
        else:
            canvas = _pad_to_square_rgb(cropped, content_scale=content_scale)
        frames.append(canvas.resize((size, size), Image.LANCZOS))
    return frames


def _pad_to_square_rgba(im: Image.Image, *, content_scale: float) -> Image.Image:
    if im.mode != "RGBA":
        im = im.convert("RGBA")

    w, h = im.size
    max_side = max(w, h)

    side = int(math.ceil(max_side / max(0.01, float(content_scale))))
    side = max(side, 1)

    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    x = (side - w) // 2
    y = (side - h) // 2
    canvas.paste(im, (x, y))
    return canvas


def _pad_to_square_rgb(im: Image.Image, *, content_scale: float) -> Image.Image:
    if im.mode != "RGB":
        im = im.convert("RGB")

    w, h = im.size
    max_side = max(w, h)

    side = int(math.ceil(max_side / max(0.01, float(content_scale))))
    side = max(side, 1)

    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    x = (side - w) // 2
    y = (side - h) // 2
    canvas.paste(im, (x, y))
    return canvas


# =========================
# Output target resolution
# =========================

def _resolve_output_target(
    outdir_or_file: Path,
    *,
    src: Path,
    suffix: str = "",
) -> Tuple[Path, Path]:
    p = Path(outdir_or_file)

    if p.suffix.lower() == ".ico":
        out_dir = p.parent
        return out_dir, p

    out_dir = p
    out_name = f"{sanitize_piece(src.stem)}{suffix}.ico"
    return out_dir, out_dir / out_name


# =========================
# ICO generation
# =========================

@contextmanager
def _icon_output_lock(out_path: Path):
    """Serialize writes to one icon across the UI and tray processes."""
    if os.name != "nt":
        yield
        return

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    normalized = os.path.normcase(os.path.abspath(out_path))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    handle = kernel32.CreateMutexW(None, 0, f"Local\\IconMaker-ICO-{digest}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        wait_result = kernel32.WaitForSingleObject(handle, 30000)
        if wait_result == 0x102:  # WAIT_TIMEOUT
            raise TimeoutError(f"Timed out waiting to write {out_path}")
        if wait_result not in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


def _commit_icon_output(temporary_path: Path, out_path: Path, overwrite: bool) -> str:
    with _icon_output_lock(out_path):
        if out_path.exists():
            if not overwrite:
                return "exists"
            try:
                if filecmp.cmp(temporary_path, out_path, shallow=False):
                    return "unchanged"
            except OSError:
                pass

        last_error: PermissionError | None = None
        for delay in (0, 0.1, 0.2, 0.4):
            if delay:
                time.sleep(delay)
            try:
                os.replace(temporary_path, out_path)
                return "written"
            except PermissionError as exc:
                if os.name != "nt" or exc.winerror not in (5, 32):
                    raise
                last_error = exc

        raise PermissionError(
            f"Windows cannot replace {out_path} while it is in use or access is denied; "
            "close any Explorer window or preview using it and retry. "
            "The existing icon was preserved."
        ) from last_error


def make_ico(
    src: Path,
    outdir: Path,
    *,
    sizes: Optional[Sequence[int]] = None,
    suffix: str = "",
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "None",
    logfn: Callable[[str], None] | None = None,
) -> Tuple[bool, str]:
    src = Path(src)
    if not src.exists() or not src.is_file():
        return False, f"ERR: Source does not exist: {src}"

    out_dir, out_path = _resolve_output_target(Path(outdir), src=src, suffix=suffix)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"ERR: Cannot create output directory {out_dir}: {e}"

    if out_path.exists() and not overwrite:
        return True, f"SKIP: {src.name} -> {out_path.name} (exists)"

    sizes_to_use = _normalize_ico_sizes(sizes)
    if not sizes_to_use:
        return False, f"ERR: No valid sizes for {src.name}"

    content_scale = PADDING_PRESETS[normalize_padding_mode(padding_mode)]

    # Keep the autocrop argument for existing callers; transparent trimming is unconditional.
    try:
        if src.suffix.lower() == ".svg":
            frames = _svg_frames(
                src, sizes_to_use, content_scale=content_scale, keep_alpha=keep_alpha
            )
        else:
            original = _load_image_any(src)
            trimmed = _autocrop_alpha(original.convert("RGBA"))
            if keep_alpha:
                working = _pad_to_square_rgba(trimmed, content_scale=content_scale)
            else:
                working = _pad_to_square_rgb(trimmed, content_scale=content_scale)
            frames = [working.resize((size, size), Image.LANCZOS) for size in sizes_to_use]
    except UnidentifiedImageError as e:
        return False, f"ERR: Unrecognized image file {src.name}: {e}"
    except Exception as e:
        return False, f"ERR: Failed to prepare {src.name}: {type(e).__name__}: {e}"

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=ICON_WRITE_TEMP_PREFIX,
            suffix=".tmp",
            dir=out_dir,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        frames[-1].save(
            temporary_path,
            format="ICO",
            sizes=[(s, s) for s in sizes_to_use],
            append_images=frames[:-1],
        )
        with Image.open(temporary_path) as written_icon:
            actual_sizes = sorted(
                width for width, height in written_icon.info.get("sizes", set())
                if width == height
            )
        if actual_sizes != sizes_to_use:
            raise RuntimeError(
                f"ICO frames differ from requested sizes: {actual_sizes} != {sizes_to_use}"
            )
        commit_status = _commit_icon_output(temporary_path, out_path, overwrite)
        if commit_status != "written":
            msg = f"SKIP: {src.name} -> {out_path.name} ({commit_status})"
            _safe_log(logfn, msg)
            return True, msg
        temporary_path = None

        msg = (
            f"OK: {src.name} -> {out_path.name} "
            f"sizes={','.join(str(size) for size in actual_sizes)} ({len(actual_sizes)} frames)"
        )
        _safe_log(logfn, msg)
        return True, msg

    except Exception as e:
        msg = f"ERR: Failed to write {out_path.name}: {type(e).__name__}: {e}"
        _safe_log(logfn, msg)
        return False, msg
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


# =========================
# Archive source management (Icon Images)
# =========================
def reconcile_archive_image_copies(
    *,
    paths: EnginePaths,
    promote_missing: bool,
    remove_extra: bool = False,
    directory: Path | None = None,
    logfn: Callable[[str], None] | None = None,
) -> Tuple[int, int]:
    """Apply opt-in copy rules only to managed source images, within each folder."""
    if not promote_missing and not remove_extra:
        return 0, 0

    root = paths.images_dir
    if directory is not None:
        folder = Path(directory)
        if not folder.is_dir() or not _is_under(folder, root) or _is_reserved_archive_path(folder, paths=paths):
            return 0, 0
        folders = [folder]
    else:
        folders = sorted({path.parent for path in _iter_archive_source_images(paths=paths, recursive=True)})

    renamed = 0
    deleted = 0
    for folder in folders:
        try:
            images = [path for path in folder.iterdir() if _is_image_file(path) and not path.is_symlink()]
        except OSError as exc:
            _safe_log(logfn, f"Copy cleanup skipped {folder}: {exc}")
            continue

        names = {path.name.casefold() for path in images}
        groups: Dict[Tuple[str, str], List[Tuple[int, Path]]] = {}
        for path in images:
            match = _COPY_STEM.fullmatch(path.stem)
            if match:
                rank = int(match.group(2)) if match.group(2) else 1
                groups.setdefault((match.group(1).casefold(), path.suffix.casefold()), []).append((rank, path))

        for (base_key, suffix_key), copies in groups.items():
            base_name = f"{base_key}{suffix_key}"
            if base_name not in names and promote_missing:
                _, source = min(copies, key=lambda item: (item[0], item[1].name.casefold()))
                match = _COPY_STEM.fullmatch(source.stem)
                if match is not None:
                    target = source.with_name(f"{match.group(1)}{source.suffix}")
                    try:
                        if not target.exists():
                            source.rename(target)
                            copies = [(rank, path) for rank, path in copies if path != source]
                            names.add(base_name)
                            renamed += 1
                            _safe_log(logfn, f"Promoted image copy: {source} -> {target}")
                    except OSError as exc:
                        _safe_log(logfn, f"Copy promotion failed for {source}: {exc}")

            # A numbered copy activates cleanup; an original plus one plain copy is kept.
            if remove_extra and base_name in names and any(rank >= 2 for rank, _ in copies):
                for _, source in copies:
                    try:
                        source.unlink()
                        deleted += 1
                        _safe_log(logfn, f"Deleted extra image copy: {source}")
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        _safe_log(logfn, f"Copy deletion failed for {source}: {exc}")

    return renamed, deleted


def normalize_archive_source_images(
    *,
    paths: EnginePaths,
    logfn: Callable[[str], None] | None = None,
) -> Tuple[int, int, int]:
    """
    Normalize stored source-image filenames while preserving subfolder structure.

    Rules:
    - Never flatten subfolders.
    - Never touch Icons/ or _Quarantine/ trees.
    - Keep valid image filenames, including user-edited case and Unicode.
    - Canonicalize invalid image filenames inside their current parent folders.
    - Apply strict collision policy via GenName helpers.
    """
    moved = 0
    renamed = 0
    collisions = 0

    base = resolve_engine_paths(paths=paths).images_dir
    base.mkdir(parents=True, exist_ok=True)

    for p in _iter_archive_source_images(paths=paths, recursive=True):
        try:
            validate_archive_image_filename(p.name)
        except ValueError:
            pass
        else:
            continue
        desired = sanitize_piece(p.stem) + p.suffix.lower()
        if p.name == desired:
            continue

        _safe_log(logfn, f"NORMALIZE: rename '{p}' -> '{desired}'")

        dst, col = move_within_archive_storage_strict(
            p,
            p.parent,
            logfn=logfn,
        )
        if dst is None:
            continue
        if col is not None:
            collisions += 1
        else:
            renamed += 1

    return moved, renamed, collisions


def _archive_import_target_dir(
    src: Path,
    *,
    paths: EnginePaths | None = None,
    source_root: Path | None = None,
) -> Path:
    target_images_dir = paths.images_dir if paths is not None else Path(ICON_IMAGES_DIR)
    if source_root is None:
        return target_images_dir

    try:
        rel_parent = Path(src).resolve().relative_to(Path(source_root).resolve()).parent
    except Exception:
        return target_images_dir
    return target_images_dir / rel_parent


def mirror_copy_to_archive_sources(
    src: Path,
    *,
    paths: EnginePaths | None = None,
    source_root: Path | None = None,
    logfn: Callable[[str], None] | None = None,
) -> Optional[Path]:
    """
    Copy-only import into managed archive sources.
    External originals are never touched by this call.
    """
    src = Path(src)
    target_images_dir = paths.images_dir if paths is not None else Path(ICON_IMAGES_DIR)

    try:
        if src.resolve().is_relative_to(target_images_dir.resolve()):
            return src
    except Exception:
        pass

    dst, col = copy_into_archive_storage_strict(
        src,
        _archive_import_target_dir(src, paths=paths, source_root=source_root),
        logfn=logfn,
    )

    if col is not None:
        _safe_log(logfn, f"IMPORT COLLISION: {src.name}")
        return dst

    return dst


def mirror_copy_to_archive_sources_ex(
    src: Path,
    *,
    paths: EnginePaths | None = None,
    source_root: Path | None = None,
    logfn: Callable[[str], None] | None = None,
    target_name: str | None = None,
) -> Tuple[Optional[Path], Optional[str]]:
    dst, col = copy_into_archive_storage_strict(
        Path(src),
        _archive_import_target_dir(Path(src), paths=paths, source_root=source_root),
        logfn=logfn,
        target_name=target_name,
    )
    if col is not None:
        return dst, "collision"
    return dst, None


# =========================
# Batch conversion helpers
# =========================
def list_missing_icon_tasks(
    *,
    paths: EnginePaths | None = None,
    images_dir: Path | None = None,
    icons_dir: Path | None = None,
    suffix: str = "",
) -> List[Tuple[Path, Path]]:
    """
    Return (source image, target icon) tasks only for missing ICO files.

    Background scans must not recreate older icons using later settings.
    The Icons tree mirrors the relative structure of Icon Images.
    """
    if paths is not None:
        images_dir = paths.images_dir
        icons_dir = paths.icons_dir
    else:
        images_dir = Path(images_dir) if images_dir is not None else Path(ICON_IMAGES_DIR)
        icons_dir = Path(icons_dir) if icons_dir is not None else Path(ICONS_DIR)

    tasks: List[Tuple[Path, Path]] = []

    try:
        icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []

    images = _iter_archive_source_images(paths=paths, recursive=True)
    icon_names = _icon_names_for_images(images)
    for img in images:
        target = _icon_target_for_image(
            img,
            paths=paths,
            images_dir=images_dir,
            icons_dir=icons_dir,
            suffix=suffix,
            icon_names=icon_names,
        )

        if not target.exists():
            tasks.append((img, target))

    return tasks


def convert_many(
    tasks: Sequence[Tuple[Path, Path]],
    *,
    sizes: Sequence[int],
    suffix: str = "",
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "None",
    progress_cb: ProgressCB | None = None,
    logfn: Callable[[str], None] | None = None,
) -> Tuple[int, int, int]:
    ok = 0
    skipped = 0
    failed = 0

    total = len(tasks)
    for i, (src, out_file) in enumerate(tasks, start=1):
        _safe_progress(progress_cb, f"Converting {src.name}", i, total, src)

        try:
            res_ok, msg = make_ico(
                src,
                out_file,
                sizes=sizes,
                suffix=suffix,
                overwrite=overwrite,
                keep_alpha=keep_alpha,
                autocrop=autocrop,
                padding_mode=padding_mode,
                logfn=logfn,
            )
        except Exception as e:
            res_ok = False
            msg = f"[FATAL] make_ico crashed for {src}: {type(e).__name__}: {e}"
            _safe_log(logfn, msg)

        if res_ok and msg.startswith("SKIP:"):
            skipped += 1
        elif res_ok:
            ok += 1
        else:
            failed += 1

    return ok, skipped, failed


def _scan_counts_only(
    *,
    paths: EnginePaths | None = None,
    overwrite: bool,
    sizes: Sequence[int],
    padding_mode: str,
    autocrop: bool,
    keep_alpha: bool,
    suffix: str,
    logfn: Callable[[str], None] | None,
    progress_cb: ProgressCB | None,
) -> Tuple[int, int, int]:

    images = _iter_archive_source_images(paths=paths, recursive=True)
    scanned = len(images)
    icon_names = _icon_names_for_images(images)

    if overwrite:
        tasks: List[Tuple[Path, Path]] = []
        for img in images:
            out = _icon_target_for_image(img, paths=paths, suffix=suffix, icon_names=icon_names)
            tasks.append((img, out))
    else:
        tasks = list_missing_icon_tasks(
            paths=paths,
            suffix=suffix,
        )

    # In no-overwrite mode, tasks contains only missing icons.
    ok, _skipped, failed = convert_many(
        tasks,
        sizes=_normalize_sizes(list(sizes)),
        suffix=suffix,
        overwrite=overwrite,
        keep_alpha=keep_alpha,
        autocrop=autocrop,
        padding_mode=padding_mode,
        progress_cb=progress_cb,
        logfn=logfn,
    )

    converted = ok
    errors = failed
    return scanned, converted, errors

def remove_orphan_icons(
    images_dir: Path | None = None,
    icons_dir: Path | None = None,
    *,
    paths: EnginePaths | None = None,
    suffix: str = "",
    action: str = "delete",
    logfn: Callable[[str], None] | None = None,
) -> int:
    """
    Remove orphan ICO files only.

    Matching is based on canonicalized relative paths beneath Icon Images and Icons,
    so renames or folder moves leave the old ICO orphaned and eligible for removal.
    PNG/source images are never deleted here.
    """
    action = (action or "").strip().lower()
    if action not in {"delete", "quarantine"}:
        action = "delete"

    if paths is not None:
        images_dir = paths.images_dir
        icons_dir = paths.icons_dir
    else:
        images_dir = Path(images_dir) if images_dir is not None else Path(ICON_IMAGES_DIR)
        icons_dir = Path(icons_dir) if icons_dir is not None else Path(ICONS_DIR)

    try:
        icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return 0

    images = _iter_archive_source_images(paths=paths, recursive=True)
    icon_names = _icon_names_for_images(images)
    expected_icon_keys: set[str] = set()
    for img in images:
        try:
            target = _icon_target_for_image(
                img,
                paths=paths,
                images_dir=images_dir,
                icons_dir=icons_dir,
                suffix=suffix,
                icon_names=icon_names,
            )
            expected_icon_keys.add(_canonical_relative_file_key(target, icons_dir))
        except Exception:
            pass

    removed = 0
    orphan_dir = icons_dir / "_Orphans"

    try:
        ico_iter = icons_dir.rglob("*.ico")
    except Exception:
        return 0

    for ico in ico_iter:
        try:
            if _is_under(ico, orphan_dir):
                continue

            rel = ico.relative_to(icons_dir)
            if suffix and not ico.stem.endswith(suffix):
                continue

            if _canonical_relative_file_key(ico, icons_dir) in expected_icon_keys:
                continue

            if action == "quarantine":
                target = unique_path(orphan_dir / rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                ico.replace(target)
                _safe_log(logfn, f"Orphan: moved {ico} -> {target}")
            else:
                ico.unlink(missing_ok=True)
                _safe_log(logfn, f"Orphan: deleted {ico}")

            removed += 1

        except Exception as e:
            _safe_log(logfn, f"Orphan cleanup warn: {ico} ({type(e).__name__}: {e})")

    return removed


@dataclass(frozen=True)
class ScanReport:
    scanned: int
    converted: int
    errors: int
    orphan_icons_removed: int
    normalized_moves: int


def _scan_archive_sources_and_convert_impl(
    *,
    sizes: Optional[Sequence[int]] = None,
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    paths: EnginePaths | None = None,
    padding_mode: str = "None",
    remove_orphans: bool = True,
    orphan_action: str = "delete",
    suffix: str = "",
    logfn: Callable[[str], None] | None = None,
    progress_cb: ProgressCB | None = None,
) -> ScanReport:
    normalized_moves = 0

    _safe_progress(progress_cb, "normalize", 0, 1, None)
    try:
        moved, renamed, collisions = normalize_archive_source_images(paths=paths, logfn=logfn)
        normalized_moves = moved + renamed
        if collisions:
            _safe_log(logfn, f"Normalize: collisions={collisions} (duplicates skipped/held)")
    except Exception as e:
        _safe_log(logfn, f"[FATAL] normalize_archive_source_images crashed: {type(e).__name__}: {e}")
    _safe_progress(progress_cb, "normalize", 1, 1, None)

    scanned, converted, errors = _scan_counts_only(
        paths=paths,
        overwrite=overwrite,
        sizes=_normalize_sizes(list(sizes) if sizes else DEFAULT_SIZES),
        padding_mode=padding_mode,
        autocrop=autocrop,
        keep_alpha=keep_alpha,
        suffix=suffix,
        logfn=logfn,
        progress_cb=progress_cb,
    )

    orphan_removed = 0
    if remove_orphans:
        _safe_progress(progress_cb, "orphans", 0, 1, None)
        try:
            orphan_removed = remove_orphan_icons(
                paths=paths,
                suffix=suffix,
                action=orphan_action,
                logfn=logfn,
            )
        except Exception as e:
            _safe_log(logfn, f"[FATAL] remove_orphan_icons crashed: {type(e).__name__}: {e}")
        _safe_progress(progress_cb, "orphans", 1, 1, None)

    _safe_log(
        logfn,
        (
            f"Scan done. scanned={scanned} converted={converted} errors={errors} "
            f"orphan_removed={orphan_removed} normalized_moves={normalized_moves}"
        ),
    )
    return ScanReport(
        scanned=scanned,
        converted=converted,
        errors=errors,
        orphan_icons_removed=orphan_removed,
        normalized_moves=normalized_moves,
    )


def scan_archive_sources_and_convert(
    *,
    paths: EnginePaths | None = None,
    sizes: Optional[Sequence[int]] = None,
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "None",
    remove_orphans: bool = True,
    orphan_action: str = "delete",
    suffix: str = "",
    logfn: Callable[[str], None] | None = None,
    progress_cb: ProgressCB | None = None,
) -> ScanReport:
    try:
        return _scan_archive_sources_and_convert_impl(
            paths=paths,
            sizes=sizes,
            overwrite=overwrite,
            keep_alpha=keep_alpha,
            autocrop=autocrop,
            padding_mode=padding_mode,
            remove_orphans=remove_orphans,
            orphan_action=orphan_action,
            suffix=suffix,
            logfn=logfn,
            progress_cb=progress_cb,
        )
    except Exception as e:
        _safe_log(logfn, f"[FATAL] scan_archive_sources_and_convert crashed: {type(e).__name__}: {e}")
        return ScanReport(
            scanned=0,
            converted=0,
            errors=1,
            orphan_icons_removed=0,
            normalized_moves=0,
        )


# =========================
# CLI (subprocess target for Gen1)
# =========================

def _cli() -> int:
    ap = argparse.ArgumentParser(description="IconForge engine CLI (Gen2)")
    ap.add_argument("input", nargs="?", default=None, help="Input file or folder")
    ap.add_argument(
        "--archive-storage-root",
        "--archive-root",
        "--library-root",
        dest="archive_storage_root",
        default=None,
        help="Archive storage root that contains Icon Images/",
    )
    ap.add_argument("--recursive", action="store_true", help="Recursive scan for folder inputs")
    ap.add_argument("--sizes", default="auto", help="Comma list, or 'auto', or 'full'")
    ap.add_argument("--suffix", default="", help="Suffix appended to icon name")
    ap.add_argument("--out", default=None, help="Output directory or .ico path")
    ap.add_argument(
        "--no-overwrite", action="store_true",
        help="Do not overwrite direct outputs; archived icons are refreshed after a conversion",
    )
    ap.add_argument("--no-alpha", action="store_true", help="Discard alpha")
    ap.add_argument("--autocrop", action="store_true", help="Legacy option; transparent borders are always trimmed")
    ap.add_argument("--padding", type=_parse_padding_mode, default="None", choices=list(PADDING_PRESETS.keys()))

    ap.add_argument("--mirror", action="store_true", help="Copy inputs into archive storage before converting")
    ap.add_argument("--progress-json", action="store_true", help="Emit JSON progress lines for UI integration")
    ap.add_argument("--normalize", action="store_true", help="Normalize stored source images first")
    ap.add_argument("--orphans", action="store_true", help="Remove orphans in output folder")
    ap.add_argument("--orphans-action", default="delete", choices=["delete", "quarantine"])
    args = ap.parse_args()

    paths = resolve_engine_paths(
        archive_storage_root=Path(args.archive_storage_root).resolve() if args.archive_storage_root else None
    )
    ensure_archive_storage_dirs(paths)
    inp = Path(args.input) if args.input else paths.images_dir
    out = Path(args.out) if args.out else paths.icons_dir
    sizes = parse_sizes(args.sizes)

    ok = 0
    skipped = 0
    failed = 0

    def log(msg: str) -> None:
        print(msg, flush=True)

    def emit_progress(done: int, total: int, status: str, file: str | None = None) -> None:
        if args.progress_json:
            payload = {"type": "progress", "done": int(done), "total": int(total), "status": str(status)}
            if file is not None:
                payload["file"] = str(file)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            log(status)

    if args.normalize:
        try:
            m, r, c = normalize_archive_source_images(paths=paths, logfn=log)
            log(f"Normalize: moved={m} renamed={r} collisions={c}")
        except Exception as e:
            log(f"ERR: Normalize failed: {type(e).__name__}: {e}")

    imgs = find_images(inp, recursive=args.recursive)
    if not imgs:
        rep = diagnose_image_discovery(inp, recursive=args.recursive)
        log(f"ERR: No images found. Total scanned={rep.found_total} images={rep.found_images}")
        for k, v in sorted(rep.skipped_reason_counts.items()):
            log(f"  skipped[{k}]={v}")
        for p, reason in rep.sample_skipped:
            log(f"  sample-skip: {p} ({reason})")
        return 2

    total = len(imgs)
    import_root = inp if inp.is_dir() else None

    conversion_jobs: List[Tuple[int, Path]] = []
    if args.mirror:
        for idx, img in enumerate(imgs, start=1):
            src = Path(img)
            emit_progress(idx - 1, total, f"Importing {idx}/{total}: {src.name}", file=str(src))
            try:
                mirrored = mirror_copy_to_archive_sources(src, paths=paths, source_root=import_root, logfn=log)
            except Exception as e:
                failed += 1
                log(f"ERR: Import failed: {src}: {type(e).__name__}: {e}")
                continue

            if not mirrored:
                skipped += 1
                log(f"SKIP: Import skipped: {src}")
                continue

            conversion_jobs.append((idx, Path(mirrored)))
    else:
        conversion_jobs = [(idx, Path(img)) for idx, img in enumerate(imgs, start=1)]

    for idx, src in conversion_jobs:
        emit_progress(idx, total, f"Converting {idx}/{total}: {src.name}", file=str(src))

        output_target = out
        if args.mirror and out.suffix.lower() != ".ico":
            output_target = _icon_target_for_image(
                src,
                images_dir=paths.images_dir,
                icons_dir=out,
                suffix=args.suffix,
            )
        res_ok, msg = make_ico(
            src,
            output_target,
            sizes=sizes,
            suffix=args.suffix,
            overwrite=not args.no_overwrite,
            keep_alpha=not args.no_alpha,
            autocrop=args.autocrop,
            padding_mode=args.padding,
            logfn=log,
        )

        if res_ok and msg.startswith("SKIP:"):
            skipped += 1
        elif res_ok:
            ok += 1
        else:
            failed += 1

    if ok:
        log("Running full archive conversion pass...")
        full_report = scan_archive_sources_and_convert(
            paths=paths,
            sizes=sizes,
            overwrite=True,
            keep_alpha=not args.no_alpha,
            autocrop=args.autocrop,
            padding_mode=args.padding,
            remove_orphans=False,
            logfn=log,
        )
        failed += full_report.errors
        log(
            f"Archive checked: scanned={full_report.scanned} "
            f"converted={full_report.converted} errors={full_report.errors}"
        )

    log(f"Done: ok={ok} skipped={skipped} failed={failed}")

    if args.orphans and out.suffix.lower() != ".ico":
        try:
            removed = remove_orphan_icons(
                paths=paths,
                suffix=args.suffix,
                action=args.orphans_action,
                logfn=log,
            )
            log(f"Orphans removed: {removed}")
        except Exception as e:
            log(f"ERR: Orphan cleanup failed: {type(e).__name__}: {e}")
            failed += 1

    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(_cli())
