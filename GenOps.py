#!/usr/bin/env python3
"""GenOps.py — non-UI workflows and app coordination for IconMaker."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence

from PySide6 import QtCore

import Gen2 as eng
import GenLog
from Gen2 import EnginePaths, ScanReport

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"
EVENT_SEQ_KEY = "app_events/seq"
EVENT_TYPE_KEY = "app_events/type"
EVENT_DETAIL_KEY = "app_events/detail"
EVENT_TIME_KEY = "app_events/time"
PAUSE_KEY = "app_state/library_pause"
PAUSE_REASON_KEY = "app_state/library_pause_reason"

ProgressCB = Callable[[int, int, str], None]


@dataclass(frozen=True)
class RelocationResult:
    ok: bool
    verified: bool
    source_deleted: bool
    files_copied: int
    message: str


@dataclass(frozen=True)
class RelocationPlan:
    source_root: Path
    target_root: Path
    source_files: List[Path]


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
        for p in root.rglob('*'):
            if p.is_dir():
                out.add(str(p))
    except Exception:
        pass
    return sorted(out)


def build_library_watch_paths(paths: EnginePaths) -> List[str]:
    try:
        paths.images_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return _iter_all_dirs(paths.images_dir)


def publish_app_event(event_type: str, detail: str = '') -> AppEvent:
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
        str(s.value(EVENT_TYPE_KEY, '') or ''),
        str(s.value(EVENT_DETAIL_KEY, '') or ''),
        float(s.value(EVENT_TIME_KEY, 0.0) or 0.0),
    )


def set_library_pause(paused: bool, reason: str = '') -> None:
    s = _settings()
    s.setValue(PAUSE_KEY, bool(paused))
    s.setValue(PAUSE_REASON_KEY, reason)
    s.sync()
    publish_app_event('library-paused' if paused else 'library-resumed', reason)


def is_library_paused() -> bool:
    return bool(_settings().value(PAUSE_KEY, False, type=bool))


def default_engine_paths() -> EnginePaths:
    try:
        return eng.default_engine_paths()
    except Exception:
        return EnginePaths.from_library_root(Path.home() / 'Desktop')


def run_library_maintenance(*, paths: EnginePaths, overwrite: bool, sizes: Sequence[int], padding_mode: str, autocrop: bool, logfn: Callable[[str], None] | None = None) -> ScanReport:
    rep = eng.scan_icon_images_and_convert(
        paths=paths,
        overwrite=overwrite,
        sizes=sizes,
        padding_mode=padding_mode,
        autocrop=autocrop,
        logfn=logfn,
        remove_orphans=True,
        orphan_action='delete',
    )
    _ops_log(f'library maintenance: converted={rep.converted} orphans={rep.orphan_icons_removed} normalized={rep.normalized_moves}', paths=paths)
    publish_app_event('library-maintained', f'converted={rep.converted};orphans={rep.orphan_icons_removed};normalized={rep.normalized_moves}')
    return rep


def plan_library_relocation(source_root: Path, target_root: Path) -> RelocationPlan:
    src = Path(source_root).resolve()
    dst = Path(target_root).resolve()
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f'Source root does not exist: {src}')
    if src == dst:
        raise ValueError('Source and destination are the same folder.')
    if str(dst).startswith(str(src) + os.sep):
        raise ValueError('Destination cannot be inside the current library root.')
    if str(src).startswith(str(dst) + os.sep):
        raise ValueError('Destination cannot be a parent of the current library root.')
    files: List[Path] = []
    for dirpath, _dirnames, filenames in os.walk(src):
        base = Path(dirpath)
        for fn in filenames:
            files.append(base / fn)
    return RelocationPlan(source_root=src, target_root=dst, source_files=sorted(files))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def relocate_library(source_root: Path, target_root: Path, *, overwrite: bool = True, delete_source: bool = True, progress_cb: ProgressCB | None = None, is_cancelled: Callable[[], bool] | None = None) -> RelocationResult:
    def cancelled() -> bool:
        if not is_cancelled:
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    try:
        set_library_pause(True, 'relocating-library')
        plan = plan_library_relocation(source_root, target_root)
    except Exception as e:
        set_library_pause(False, '')
        _ops_log(f'relocation setup failed: {type(e).__name__}: {e}', level='error')
        return RelocationResult(False, False, False, 0, str(e))

    total = len(plan.source_files)
    copied = 0
    try:
        plan.target_root.mkdir(parents=True, exist_ok=True)
        for src_file in plan.source_files:
            if cancelled():
                return RelocationResult(False, False, False, copied, 'Cancelled.')
            rel = src_file.relative_to(plan.source_root)
            dst_file = plan.target_root / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if dst_file.exists() and not overwrite:
                return RelocationResult(False, False, False, copied, f'Destination exists: {dst_file}')
            shutil.copy2(src_file, dst_file)
            copied += 1
            _safe_progress(progress_cb, copied, total, str(src_file))
        for src_file in plan.source_files:
            if cancelled():
                return RelocationResult(False, False, False, copied, 'Cancelled during verification.')
            rel = src_file.relative_to(plan.source_root)
            dst_file = plan.target_root / rel
            if not dst_file.exists():
                return RelocationResult(False, False, False, copied, f'Verification failed: missing {dst_file}')
            if src_file.stat().st_size != dst_file.stat().st_size:
                return RelocationResult(False, False, False, copied, f'Verification failed: size mismatch for {rel}')
            if _sha256(src_file) != _sha256(dst_file):
                return RelocationResult(False, False, False, copied, f'Verification failed: content mismatch for {rel}')
        if delete_source:
            shutil.rmtree(plan.source_root)
        publish_app_event('library-relocated', str(plan.target_root))
        _ops_log(f'relocation complete: {plan.source_root} -> {plan.target_root} files={copied}', level='info')
        return RelocationResult(True, True, delete_source, copied, 'Relocation verified.')
    except Exception as e:
        _ops_log(f'relocation failed after {copied} files: {type(e).__name__}: {e}', level='error')
        return RelocationResult(False, False, False, copied, str(e))
    finally:
        set_library_pause(False, '')


def run_conversion(request: ConversionRequest, *, progress_cb: ProgressCB | None = None, logfn: Callable[[str], None] | None = None, is_cancelled: Callable[[], bool] | None = None) -> ConversionResult:
    def cancelled() -> bool:
        if not is_cancelled:
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    inp = Path(request.input_path)
    if not inp.exists():
        _ops_log(f'conversion input missing: {inp}', paths=request.paths, level='error')
        return ConversionResult(False, 0, 0, 0, 'ERR: Input path does not exist.')

    imgs = eng.find_images(inp, recursive=request.recursive)
    if not imgs:
        rep = eng.diagnose_image_discovery(inp, recursive=request.recursive)
        if logfn:
            logfn(f'ERR: No images found. Total scanned={rep.found_total} images={rep.found_images}')
            for k, v in sorted(rep.skipped_reason_counts.items()):
                logfn(f'  skipped[{k}]={v}')
            for p, reason in rep.sample_skipped:
                logfn(f'  sample-skip: {p} ({reason})')
        return ConversionResult(False, 0, 0, 0, 'ERR: No images found.')

    ok = 0
    skipped = 0
    failed = 0
    total = len(imgs)
    for idx, img in enumerate(imgs, start=1):
        if cancelled():
            return ConversionResult(False, ok, skipped, failed, 'Stopped by cancel.')
        src = Path(img)
        current_status = f'Converting {idx}/{total}: {src.name}'
        if request.mirror:
            _safe_progress(progress_cb, idx - 1, total, f'Copying {idx}/{total}: {src.name}')
            try:
                mirrored = eng.mirror_copy_to_icon_images(src, paths=request.paths, logfn=logfn)
            except Exception as e:
                failed += 1
                if logfn:
                    logfn(f'ERR: Copy failed: {src}: {type(e).__name__}: {e}')
                continue
            if cancelled():
                return ConversionResult(False, ok, skipped, failed, 'Stopped by cancel.')
            if not mirrored:
                skipped += 1
                if logfn:
                    logfn(f'SKIP: Copy skipped: {src}')
                continue
            src = Path(mirrored)
            current_status = f'Converting {idx}/{total}: {src.name}'
        _safe_progress(progress_cb, idx, total, current_status)
        try:
            res_ok, msg = eng.make_ico(
                src,
                request.paths.icons_dir,
                sizes=request.sizes,
                overwrite=request.overwrite,
                keep_alpha=request.keep_alpha,
                autocrop=request.autocrop,
                padding_mode=request.padding_mode,
                logfn=logfn,
            )
        except Exception as e:
            res_ok = False
            msg = f'ERR: make_ico crashed for {src}: {type(e).__name__}: {e}'
            if logfn:
                logfn(msg)
        if res_ok and str(msg).startswith('SKIP:'):
            skipped += 1
        elif res_ok:
            ok += 1
        else:
            failed += 1
    message = f'Done: ok={ok} skipped={skipped} failed={failed}'
    if logfn:
        logfn(message)
    _ops_log(f'conversion finished: {message}', paths=request.paths)
    publish_app_event('conversion-finished', message)
    return ConversionResult(failed == 0, ok, skipped, failed, message)
