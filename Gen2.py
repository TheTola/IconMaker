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
import json
import math
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
    "AUTO_FULL_SIZES",
    "DEFAULT_ARCHIVE_STORAGE_ROOT",
    "DEFAULT_OUTPUT_DIR",
    "PADDING_PRESETS",
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
    "find_images",
    "make_ico",
    "unique_path",
    "normalize_archive_source_images",
    "archive_icon_path_for_source_image",
    "mirror_copy_to_archive_sources",
    "mirror_copy_to_archive_sources_ex",
    "list_missing_icon_tasks",
    "convert_many",
    "scan_archive_sources_and_convert",
    "remove_orphan_icons",
]


IMAGE_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".svg"}

DEFAULT_SIZES: List[int] = [16, 24, 32, 48, 64, 128, 256]
ICO_MAX_SIZE = 256
AUTO_FULL_SIZES: List[int] = list(range(8, 1025, 8))
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
    "tight": 0.96,
    "balanced": 0.88,
    "extra": 0.80,
}
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


def _icon_target_for_image(
    img: Path,
    *,
    paths: EnginePaths | None = None,
    images_dir: Path | None = None,
    icons_dir: Path | None = None,
    suffix: str = "",
) -> Path:
    if paths is not None:
        base = paths.images_dir
        icons = paths.icons_dir
    else:
        base = Path(images_dir) if images_dir is not None else Path(ICON_IMAGES_DIR)
        icons = Path(icons_dir) if icons_dir is not None else Path(ICONS_DIR)

    rel_parent = img.relative_to(base).parent
    out_name = f"{sanitize_piece(img.stem)}{suffix}.ico"
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
            if 8 <= n <= 1024:
                out.append(n)
        except Exception:
            pass
    return sorted(set(out))


def _normalize_sizes(sizes: Sequence[int]) -> List[int]:
    out: List[int] = []
    for n in sizes:
        try:
            n2 = int(n)
            if 8 <= n2 <= 1024:
                out.append(n2)
        except Exception:
            pass
    return sorted(set(out))


def _normalize_ico_sizes(sizes: Sequence[int] | None) -> List[int]:
    requested = _normalize_sizes(sizes or DEFAULT_SIZES)
    supported = {n for n in requested if n <= ICO_MAX_SIZE}
    supported.update(DEFAULT_SIZES)
    return sorted(supported)


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

def _load_svg_to_rgba(svg_path: Path) -> Image.Image:
    try:
        import cairosvg
    except Exception as e:
        raise RuntimeError(f"CairoSVG not installed for SVG support: {e}")

    try:
        png_bytes = cairosvg.svg2png(url=str(svg_path))
        im = Image.open(BytesIO(png_bytes))
        return im.convert("RGBA")
    except Exception as e:
        raise RuntimeError(f"SVG render failed: {e}")


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

def make_ico(
    src: Path,
    outdir: Path,
    *,
    sizes: Optional[Sequence[int]] = None,
    suffix: str = "",
    overwrite: bool = True,
    keep_alpha: bool = True,
    autocrop: bool = False,
    padding_mode: str = "balanced",
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

    padding_mode = (padding_mode or "balanced").strip().lower()
    content_scale = PADDING_PRESETS.get(padding_mode, PADDING_PRESETS["balanced"])

    try:
        im = _load_image_any(src)
    except RuntimeError as e:
        return False, f"ERR: Failed to open {src.name}: {e}"
    except UnidentifiedImageError as e:
        return False, f"ERR: Unrecognized image file {src.name}: {e}"
    except Exception as e:
        return False, f"ERR: Failed to open {src.name}: {e}"

    if autocrop:
        try:
            im = _autocrop_alpha(im)
        except Exception:
            pass

    if keep_alpha:
        im = im.convert("RGBA")
        base_canvas = _pad_to_square_rgba(im, content_scale=content_scale)
    else:
        im = im.convert("RGBA").convert("RGB")
        base_canvas = _pad_to_square_rgb(im, content_scale=content_scale)

    try:
        if base_canvas.width < ICO_MAX_SIZE:
            base_canvas = base_canvas.resize((ICO_MAX_SIZE, ICO_MAX_SIZE), Image.LANCZOS)

        base_large = base_canvas.convert("RGBA" if keep_alpha else "RGB")

        base_large.save(
            out_path,
            format="ICO",
            sizes=[(s, s) for s in sizes_to_use],
        )

        msg = (
            f"OK: {src.name} -> {out_path.name} "
            f"sizes={sizes_to_use[0]}..{sizes_to_use[-1]} ({len(sizes_to_use)} frames)"
        )
        _safe_log(logfn, msg)
        return True, msg

    except Exception as e:
        msg = f"ERR: Failed to write {out_path.name}: {type(e).__name__}: {e}"
        _safe_log(logfn, msg)
        return False, msg


# =========================
# Archive source management (Icon Images)
# =========================
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
    - Canonicalize image filenames in-place inside their current parent folders.
    - Apply strict collision policy via GenName helpers.
    """
    moved = 0
    renamed = 0
    collisions = 0

    base = resolve_engine_paths(paths=paths).images_dir
    base.mkdir(parents=True, exist_ok=True)

    for p in _iter_archive_source_images(paths=paths, recursive=True):
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
) -> Tuple[Optional[Path], Optional[str]]:
    dst, col = copy_into_archive_storage_strict(
        Path(src),
        _archive_import_target_dir(Path(src), paths=paths, source_root=source_root),
        logfn=logfn,
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
    Return (src_png, target_ico) tasks for:
      - missing .ico files
      - outdated .ico files (PNG modified after ICO)

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

    for img in _iter_archive_source_images(paths=paths, recursive=True):
        target = _icon_target_for_image(
            img,
            paths=paths,
            images_dir=images_dir,
            icons_dir=icons_dir,
            suffix=suffix,
        )

        if not target.exists():
            tasks.append((img, target))
            continue

        try:
            img_mtime = img.stat().st_mtime
            ico_mtime = target.stat().st_mtime
        except Exception:
            tasks.append((img, target))
            continue

        if img_mtime > ico_mtime:
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
    padding_mode: str = "balanced",
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

    if overwrite:
        tasks: List[Tuple[Path, Path]] = []
        for img in images:
            out = _icon_target_for_image(img, paths=paths, suffix=suffix)
            tasks.append((img, out))
    else:
        tasks = list_missing_icon_tasks(
            paths=paths,
            suffix=suffix,
        )

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

    source_keys: set[str] = set()
    for img in _iter_archive_source_images(paths=paths, recursive=True):
        try:
            source_keys.add(_canonical_relative_file_key(img, images_dir))
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
            folder_parts = [canonical_key(part) for part in rel.parts[:-1]]
            stem = ico.stem

            if suffix:
                if not stem.endswith(suffix):
                    continue
                base_stem = stem[: -len(suffix)]
            else:
                base_stem = stem

            ico_key = "/".join([*folder_parts, canonical_key(base_stem)])
            if ico_key in source_keys:
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
    padding_mode: str = "balanced",
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
    padding_mode: str = "balanced",
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
    ap = argparse.ArgumentParser(description="IconMaker engine CLI (Gen2)")
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
    ap.add_argument("--no-overwrite", action="store_true", help="Do not overwrite existing icons")
    ap.add_argument("--no-alpha", action="store_true", help="Discard alpha")
    ap.add_argument("--autocrop", action="store_true", help="Auto-crop transparent borders")
    ap.add_argument("--padding", default="balanced", choices=list(PADDING_PRESETS.keys()))

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

    for idx, img in enumerate(imgs, start=1):
        src = Path(img)

        if args.mirror:
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

            src = Path(mirrored)

        emit_progress(idx, total, f"Converting {idx}/{total}: {src.name}", file=str(src))

        res_ok, msg = make_ico(
            src,
            out,
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
