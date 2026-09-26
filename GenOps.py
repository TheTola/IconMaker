#!/usr/bin/env python3
"""
Non-UI workflows and archive-storage coordination for IconMaker.

GenOps sits between the UI/tray layers and the engine. It owns persisted
archive storage settings, app-event signaling, relocation workflows, and the
high-level conversion and maintenance entry points used by the rest of the app.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence

from PySide6 import QtCore

import Gen2 as eng
import GenLog
from AppIdentity import APP_NAME, APP_ORG
from Gen2 import EnginePaths, ScanReport

EVENT_SEQ_KEY = "app_events/seq"
EVENT_TYPE_KEY = "app_events/type"
EVENT_DETAIL_KEY = "app_events/detail"
EVENT_TIME_KEY = "app_events/time"
ARCHIVE_STORAGE_ROOT_KEY = "archive_storage_root"
LEGACY_ARCHIVE_ROOT_KEY = "archive_root"
LEGACY_LIBRARY_ROOT_KEY = "library_root"
SAGE_URL_KEY = "sage/url"
DEFAULT_SAGE_URL = "https://chatgpt.com/g/g-68e8c5f35ff0819195a81c501942a072-sage-of-iconer"
STARTUP_TRAY_ENABLED_KEY = "startup/launch_tray"
ARCHIVE_STORAGE_PAUSE_KEY = "app_state/archive_storage_pause"
ARCHIVE_STORAGE_PAUSE_REASON_KEY = "app_state/archive_storage_pause_reason"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

ProgressCB = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ArchiveStorageRelocationResult:
    ok: bool
    verified: bool
    source_deleted: bool
    files_copied: int
    message: str


@dataclass(frozen=True)
class ArchiveStorageRelocationPlan:
    source_root: Path
    target_root: Path
    source_files: List[Path]


@dataclass(frozen=True)
class GeneratedIconsCleanupResult:
    moved_images: int
    deleted_files: int
    failed_files: int


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path
    paths: EnginePaths
    sizes: Sequence[int]
    padding_mode: str
    recursive: bool = False
    overwrite: bool = True
    keep_alpha: bool = True
    autocrop: bool = False
    mirror: bool = True


@dataclass(frozen=True)
class ConversionResult:
    ok: bool
    converted: int
    skipped: int
    failed: int
    message: str


@dataclass(frozen=True)
class AppEvent:
    seq: int
    event_type: str
    detail: str
    timestamp: float


def _settings() -> QtCore.QSettings:
    return QtCore.QSettings(APP_ORG, APP_NAME)


def load_archive_storage_root(settings: QtCore.QSettings | None = None) -> Path | None:
    """
    Load the chosen archive storage root with migration from legacy keys.

    The app now uses archive-specific terminology, but older settings keys are
    still read and rewritten so existing users keep their configured storage.
    """
    s = settings or _settings()
    for key in (ARCHIVE_STORAGE_ROOT_KEY, LEGACY_ARCHIVE_ROOT_KEY, LEGACY_LIBRARY_ROOT_KEY):
        raw = str(s.value(key, "") or "").strip()
        if not raw:
            continue
        try:
            root = Path(raw).resolve()
        except Exception:
            continue
        if key != ARCHIVE_STORAGE_ROOT_KEY:
            s.setValue(ARCHIVE_STORAGE_ROOT_KEY, str(root))
            s.sync()
        return root
    return None


def save_archive_storage_root(path: str | Path, settings: QtCore.QSettings | None = None) -> Path | None:
    s = settings or _settings()
    raw = str(path or "").strip()
    if not raw:
        for key in (ARCHIVE_STORAGE_ROOT_KEY, LEGACY_ARCHIVE_ROOT_KEY, LEGACY_LIBRARY_ROOT_KEY):
            s.remove(key)
        s.sync()
        return None

    root = Path(raw).resolve()
    for key in (ARCHIVE_STORAGE_ROOT_KEY, LEGACY_ARCHIVE_ROOT_KEY, LEGACY_LIBRARY_ROOT_KEY):
        s.setValue(key, str(root))
    s.sync()
    return root


def _normalized_sage_url(value: str) -> str:
    raw = str(value or "").strip()
    url = QtCore.QUrl.fromUserInput(raw)
    if not raw or not url.isValid() or url.scheme().lower() not in {"http", "https"} or not url.host():
        raise ValueError("Enter a valid http:// or https:// URL.")
    return url.toString()


def load_sage_url(settings: QtCore.QSettings | None = None) -> str:
    s = settings or _settings()
    raw = str(s.value(SAGE_URL_KEY, DEFAULT_SAGE_URL) or "").strip()
    try:
        return _normalized_sage_url(raw)
    except ValueError:
        return DEFAULT_SAGE_URL


def save_sage_url(url: str, settings: QtCore.QSettings | None = None) -> str:
    normalized = _normalized_sage_url(url)
    s = settings or _settings()
    s.setValue(SAGE_URL_KEY, normalized)
    s.sync()
    return normalized


def load_launch_tray_at_startup(settings: QtCore.QSettings | None = None) -> bool:
    """Return whether the tray should register itself to launch at Windows startup."""
    s = settings or _settings()
    return bool(s.value(STARTUP_TRAY_ENABLED_KEY, True, type=bool))


def save_launch_tray_at_startup(enabled: bool, settings: QtCore.QSettings | None = None) -> None:
    """Persist the user's startup preference for the tray worker."""
    s = settings or _settings()
    s.setValue(STARTUP_TRAY_ENABLED_KEY, bool(enabled))
    s.sync()


def _startup_launcher_command() -> str:
    """
    Build the startup command that launches only the tray worker.

    Frozen runs use the packaged executable. Source runs fall back to the local
    Python interpreter plus the project launcher so development sessions can
    still exercise startup behavior.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --mode tray'

    base = Path(__file__).resolve().parent
    launcher = base / "IconMakerMaster.py"
    if not launcher.exists():
        launcher = base / "IconMaker.py"
    return f'"{Path(sys.executable).resolve()}" "{launcher.resolve()}" --mode tray'


def apply_launch_tray_at_startup(
    enabled: bool | None = None,
    settings: QtCore.QSettings | None = None,
) -> bool:
    """
    Apply the saved startup preference to the current platform.

    On Windows this writes or removes the current-user Run entry so the tray
    worker opens automatically at login. Other platforms keep the preference
    persisted without attempting platform-specific registration here.
    """
    s = settings or _settings()
    should_enable = load_launch_tray_at_startup(s) if enabled is None else bool(enabled)
    if os.name != "nt":
        return True

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if should_enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _startup_launcher_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        _ops_log(f"startup registration failed: {type(exc).__name__}: {exc}", level="error")
        return False


def _ops_log(message: str, *, paths: EnginePaths | None = None, level: str = "info") -> None:
    base_dir = None if paths is None else paths.images_dir
    try:
        GenLog.write_line("ops", message, level=level, base_dir=base_dir)
    except Exception:
        pass


def _safe_progress(progress_cb: ProgressCB | None, done: int, total: int, current: str) -> None:
    if progress_cb:
        try:
            progress_cb(done, total, current)
        except Exception:
            pass


def _iter_all_dirs(root: Path) -> List[str]:
    if not root.exists() or not root.is_dir():
        return []
    out = {str(root)}
    try:
        for p in root.rglob("*"):
            if p.is_dir():
                out.add(str(p))
    except Exception:
        pass
    return sorted(out)


def build_archive_storage_watch_paths(paths: EnginePaths) -> List[str]:
    try:
        paths.images_dir.mkdir(parents=True, exist_ok=True)
        paths.icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return _iter_all_dirs(paths.images_dir)


def publish_app_event(event_type: str, detail: str = "") -> AppEvent:
    """
    Publish a single app-wide event through QSettings.

    The UI and tray worker use this lightweight shared channel to coordinate
    refreshes and status changes without direct process coupling.
    """
    s = _settings()
    seq = int(s.value(EVENT_SEQ_KEY, 0) or 0) + 1
    ts = time.time()
    s.setValue(EVENT_SEQ_KEY, seq)
    s.setValue(EVENT_TYPE_KEY, event_type)
    s.setValue(EVENT_DETAIL_KEY, detail)
    s.setValue(EVENT_TIME_KEY, ts)
    s.sync()
    return AppEvent(seq, event_type, detail, ts)


def latest_app_event() -> AppEvent:
    s = _settings()
    return AppEvent(
        int(s.value(EVENT_SEQ_KEY, 0) or 0),
        str(s.value(EVENT_TYPE_KEY, "") or ""),
        str(s.value(EVENT_DETAIL_KEY, "") or ""),
        float(s.value(EVENT_TIME_KEY, 0.0) or 0.0),
    )


def set_archive_storage_pause(paused: bool, reason: str = "") -> None:
    s = _settings()
    s.setValue(ARCHIVE_STORAGE_PAUSE_KEY, bool(paused))
    s.setValue(ARCHIVE_STORAGE_PAUSE_REASON_KEY, reason)
    s.sync()
    publish_app_event("archive-storage-paused" if paused else "archive-storage-resumed", reason)


def is_archive_storage_paused() -> bool:
    return bool(_settings().value(ARCHIVE_STORAGE_PAUSE_KEY, False, type=bool))


def default_engine_paths() -> EnginePaths:
    try:
        return eng.default_engine_paths()
    except Exception:
        return EnginePaths.from_archive_storage_root(Path.home() / "Desktop")


def clean_generated_icons_dir(
    *,
    paths: EnginePaths,
    logfn: Callable[[str], None] | None = None,
) -> GeneratedIconsCleanupResult:
    """
    Move misplaced images out of Icons and delete all other non-.ico files.
    """
    try:
        paths.images_dir.mkdir(parents=True, exist_ok=True)
        paths.icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        if logfn:
            logfn(f"Icon folder cleanup failed: {type(exc).__name__}: {exc}")
        return GeneratedIconsCleanupResult(0, 0, 1)

    try:
        files = sorted(
            (path for path in paths.icons_dir.rglob("*") if path.is_file()),
            key=lambda path: str(path).casefold(),
        )
    except Exception as exc:
        if logfn:
            logfn(f"Icon folder cleanup failed: {type(exc).__name__}: {exc}")
        return GeneratedIconsCleanupResult(0, 0, 1)

    moved_images = 0
    deleted_files = 0
    failed_files = 0
    for path in files:
        if path.suffix.lower() == ".ico":
            continue

        try:
            if path.suffix.lower() in eng.IMAGE_EXTS:
                relative = path.relative_to(paths.icons_dir)
                target = eng.unique_path(paths.images_dir / relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
                moved_images += 1
                if logfn:
                    logfn(f"Moved misplaced image: {path} -> {target}")
            else:
                path.unlink(missing_ok=True)
                deleted_files += 1
                if logfn:
                    logfn(f"Deleted invalid icon-library file: {path}")
        except FileNotFoundError:
            continue
        except Exception as exc:
            failed_files += 1
            if logfn:
                logfn(f"Icon folder cleanup failed for {path}: {type(exc).__name__}: {exc}")

    return GeneratedIconsCleanupResult(moved_images, deleted_files, failed_files)


def run_archive_maintenance(
    *,
    paths: EnginePaths,
    overwrite: bool,
    sizes: Sequence[int],
    padding_mode: str,
    autocrop: bool,
    logfn: Callable[[str], None] | None = None,
) -> ScanReport:
    """
    Run maintenance against managed archive storage.

    Maintenance normalizes source-image names inside managed storage, generates
    missing or outdated icons, and removes orphaned icons. External originals
    are never renamed or deleted here.
    """
    cleanup = clean_generated_icons_dir(paths=paths, logfn=logfn)
    report = eng.scan_archive_sources_and_convert(
        paths=paths,
        overwrite=overwrite,
        sizes=sizes,
        padding_mode=padding_mode,
        autocrop=autocrop,
        logfn=logfn,
        remove_orphans=True,
        orphan_action="delete",
    )
    _ops_log(
        "archive maintenance: "
        f"moved_images={cleanup.moved_images} deleted_invalid={cleanup.deleted_files} "
        f"converted={report.converted} orphans={report.orphan_icons_removed} normalized={report.normalized_moves}",
        paths=paths,
    )
    publish_app_event(
        "archive-storage-maintained",
        f"moved_images={cleanup.moved_images};deleted_invalid={cleanup.deleted_files};"
        f"converted={report.converted};orphans={report.orphan_icons_removed};normalized={report.normalized_moves}",
    )
    return report


def plan_archive_storage_relocation(source_root: Path, target_root: Path) -> ArchiveStorageRelocationPlan:
    src = Path(source_root).resolve()
    dst = Path(target_root).resolve()
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Archive storage root does not exist: {src}")
    if src == dst:
        raise ValueError("Source and destination are the same folder.")
    if str(dst).startswith(str(src) + os.sep):
        raise ValueError("Destination cannot be inside the current archive storage root.")
    if str(src).startswith(str(dst) + os.sep):
        raise ValueError("Destination cannot be a parent of the current archive storage root.")

    source_images_dir = EnginePaths.from_archive_storage_root(src).images_dir
    if not source_images_dir.is_dir():
        raise FileNotFoundError(f"Archive source images folder does not exist: {source_images_dir}")

    files: List[Path] = []
    for dirpath, _dirnames, filenames in os.walk(source_images_dir):
        base = Path(dirpath)
        for filename in filenames:
            files.append(base / filename)

    return ArchiveStorageRelocationPlan(source_root=src, target_root=dst, source_files=sorted(files))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def relocate_archive_storage(
    source_root: Path,
    target_root: Path,
    *,
    overwrite: bool = False,
    delete_source: bool = False,
    progress_cb: ProgressCB | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ArchiveStorageRelocationResult:
    """Copy the managed archive tree; ``overwrite`` is retained for callers but never permits replacing destination content."""
    def cancelled() -> bool:
        if not is_cancelled:
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    try:
        set_archive_storage_pause(True, "relocating-archive-storage")
        plan = plan_archive_storage_relocation(source_root, target_root)
    except Exception as exc:
        set_archive_storage_pause(False, "")
        _ops_log(f"relocation setup failed: {type(exc).__name__}: {exc}", level="error")
        return ArchiveStorageRelocationResult(False, False, False, 0, str(exc))

    total = len(plan.source_files)
    copied = 0
    try:
        target_paths = EnginePaths.from_archive_storage_root(plan.target_root)
        if target_paths.images_dir.exists():
            if not target_paths.images_dir.is_dir() or any(target_paths.images_dir.iterdir()):
                return ArchiveStorageRelocationResult(
                    False, False, False, 0, f"Destination archive storage is not empty: {target_paths.images_dir}"
                )

        plan.target_root.mkdir(parents=True, exist_ok=True)
        target_paths.images_dir.mkdir(parents=True, exist_ok=True)
        target_paths.icons_dir.mkdir(parents=True, exist_ok=True)
        source_images_dir = EnginePaths.from_archive_storage_root(plan.source_root).images_dir
        for dirpath, _dirnames, _filenames in os.walk(source_images_dir):
            (plan.target_root / Path(dirpath).relative_to(plan.source_root)).mkdir(parents=True, exist_ok=True)
        for src_file in plan.source_files:
            if cancelled():
                return ArchiveStorageRelocationResult(False, False, False, copied, "Cancelled.")
            rel = src_file.relative_to(plan.source_root)
            dst_file = plan.target_root / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if dst_file.exists():
                return ArchiveStorageRelocationResult(False, False, False, copied, f"Destination exists: {dst_file}")
            shutil.copy2(src_file, dst_file)
            copied += 1
            _safe_progress(progress_cb, copied, total, str(src_file))

        for src_file in plan.source_files:
            if cancelled():
                return ArchiveStorageRelocationResult(False, False, False, copied, "Cancelled during verification.")
            rel = src_file.relative_to(plan.source_root)
            dst_file = plan.target_root / rel
            if not dst_file.exists():
                return ArchiveStorageRelocationResult(False, False, False, copied, f"Verification failed: missing {dst_file}")
            if src_file.stat().st_size != dst_file.stat().st_size:
                return ArchiveStorageRelocationResult(False, False, False, copied, f"Verification failed: size mismatch for {rel}")
            if _sha256(src_file) != _sha256(dst_file):
                return ArchiveStorageRelocationResult(False, False, False, copied, f"Verification failed: content mismatch for {rel}")

        for dirpath, _dirnames, _filenames in os.walk(source_images_dir):
            rel = Path(dirpath).relative_to(plan.source_root)
            if not (plan.target_root / rel).is_dir():
                return ArchiveStorageRelocationResult(False, False, False, copied, f"Verification failed: missing folder {rel}")

        if plan_archive_storage_relocation(plan.source_root, plan.target_root).source_files != plan.source_files:
            return ArchiveStorageRelocationResult(False, False, False, copied, "Archive changed during relocation; source retained.")

        if delete_source:
            shutil.rmtree(source_images_dir)

        save_archive_storage_root(plan.target_root)
        publish_app_event("archive-storage-relocated", str(plan.target_root))
        _ops_log(f"archive storage relocation complete: {plan.source_root} -> {plan.target_root} files={copied}")
        return ArchiveStorageRelocationResult(True, True, delete_source, copied, "Archive storage relocation verified.")
    except Exception as exc:
        _ops_log(f"relocation failed after {copied} files: {type(exc).__name__}: {exc}", level="error")
        return ArchiveStorageRelocationResult(False, False, False, copied, str(exc))
    finally:
        set_archive_storage_pause(False, "")


def run_conversion(
    request: ConversionRequest,
    *,
    progress_cb: ProgressCB | None = None,
    logfn: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ConversionResult:
    def cancelled() -> bool:
        if not is_cancelled:
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    input_path = Path(request.input_path)
    if not input_path.exists():
        _ops_log(f"conversion input missing: {input_path}", paths=request.paths, level="error")
        return ConversionResult(False, 0, 0, 0, "ERR: Input path does not exist.")

    images = eng.find_images(input_path, recursive=request.recursive)
    if not images:
        report = eng.diagnose_image_discovery(input_path, recursive=request.recursive)
        if logfn:
            logfn(f"ERR: No images found. Total scanned={report.found_total} images={report.found_images}")
            for key, value in sorted(report.skipped_reason_counts.items()):
                logfn(f"  skipped[{key}]={value}")
            for path, reason in report.sample_skipped:
                logfn(f"  sample-skip: {path} ({reason})")
        return ConversionResult(False, 0, 0, 0, "ERR: No images found.")

    converted = 0
    skipped = 0
    failed = 0
    total = len(images)
    import_root = input_path if input_path.is_dir() else None
    conversion_jobs: List[tuple[int, Path]] = []
    if request.mirror:
        for idx, image in enumerate(images, start=1):
            if cancelled():
                return ConversionResult(False, converted, skipped, failed, "Stopped by cancel.")
            source_image = Path(image)
            _safe_progress(progress_cb, idx - 1, total * 2, f"Importing {idx}/{total}: {source_image.name}")
            try:
                mirrored = eng.mirror_copy_to_archive_sources(
                    source_image,
                    paths=request.paths,
                    source_root=import_root,
                    logfn=logfn,
                )
            except Exception as exc:
                failed += 1
                if logfn:
                    logfn(f"ERR: Import failed: {source_image}: {type(exc).__name__}: {exc}")
                continue

            if cancelled():
                return ConversionResult(False, converted, skipped, failed, "Stopped by cancel.")
            if not mirrored:
                skipped += 1
                if logfn:
                    logfn(f"SKIP: Import skipped: {source_image}")
                continue
            conversion_jobs.append((idx, Path(mirrored)))
    else:
        conversion_jobs = [(idx, Path(image)) for idx, image in enumerate(images, start=1)]

    for idx, source_image in conversion_jobs:
        if cancelled():
            return ConversionResult(False, converted, skipped, failed, "Stopped by cancel.")
        current_status = f"Converting {idx}/{total}: {source_image.name}"
        progress_done = total + idx if request.mirror else idx
        progress_total = total * 2 if request.mirror else total
        _safe_progress(progress_cb, progress_done, progress_total, current_status)
        try:
            output_target = (
                eng.archive_icon_path_for_source_image(source_image, paths=request.paths)
                if request.mirror else request.paths.icons_dir
            )
            ok, message = eng.make_ico(
                source_image,
                output_target,
                sizes=request.sizes,
                overwrite=request.overwrite,
                keep_alpha=request.keep_alpha,
                autocrop=request.autocrop,
                padding_mode=request.padding_mode,
                logfn=logfn,
            )
        except Exception as exc:
            ok = False
            message = f"ERR: make_ico crashed for {source_image}: {type(exc).__name__}: {exc}"
            if logfn:
                logfn(message)

        if ok and str(message).startswith("SKIP:"):
            skipped += 1
        elif ok:
            converted += 1
        else:
            failed += 1

    message = f"Done: ok={converted} skipped={skipped} failed={failed}"
    if logfn:
        logfn(message)
    _ops_log(f"conversion finished: {message}", paths=request.paths)
    publish_app_event("conversion-finished", message)
    return ConversionResult(failed == 0, converted, skipped, failed, message)
