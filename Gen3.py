#!/usr/bin/env python3
"""
Background tray worker for IconForge.

Gen3 watches configured folders and managed archive storage, mirrors incoming
images into archive storage when needed, runs unattended maintenance, and
publishes app events so the main UI can refresh without polling the filesystem
too aggressively.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from PySide6 import QtCore, QtGui, QtWidgets
from AppTheme import theme_manager

import Gen2 as eng
import GenLog
import GenOps
from AppIdentity import (
    APP_DISPLAY_NAME,
    APP_DISPLAY_VERSION,
    APP_EXECUTABLE_NAME,
    APP_NAME,
    APP_ORG,
    APP_USER_MODEL_ID,
    apply_qt_application_identity,
)
from Gen2 import EnginePaths
from Gen4 import get_app_icon

ALL_SIZES: List[int] = list(range(8, 257, 8))
SCAN_INTERVAL_MS = 10 * 60 * 1000


def _pre_app_setup() -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _qsettings() -> QtCore.QSettings:
    return QtCore.QSettings(APP_ORG, APP_NAME)


def _load_shared_archive_storage_root() -> Path | None:
    return GenOps.load_archive_storage_root(_qsettings())


def _current_engine_paths() -> EnginePaths:
    root = _load_shared_archive_storage_root()
    if root is None:
        return GenOps.default_engine_paths()
    return EnginePaths.from_archive_storage_root(root)


def _archive_sources_dir() -> Path:
    return _current_engine_paths().images_dir


def _generated_icons_dir() -> Path:
    return _current_engine_paths().icons_dir


def _log(message: str, *, level: str = "info") -> None:
    GenLog.write_line("tray", message, level=level)


def _unique_paths(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in items:
        value = str(raw).strip()
        if not value:
            continue
        key = str(Path(value).resolve()) if Path(value).exists() else str(Path(value))
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _iter_dirs_recursive(root: Path) -> List[str]:
    if not root.exists() or not root.is_dir():
        return []
    out: List[str] = [str(root)]
    try:
        for path in root.rglob("*"):
            if path.is_dir():
                out.append(str(path))
    except Exception:
        return [str(root)]
    return _unique_paths(out)


def _load_watch_folders() -> List[str]:
    raw = _qsettings().value("watch_folders", [], type=list)
    return _unique_paths([str(item).strip() for item in (raw or []) if str(item).strip()])


def _build_watch_paths(paths: EnginePaths, extra_roots: Iterable[str]) -> List[str]:
    watch_paths: List[str] = []
    watch_paths.extend(_iter_dirs_recursive(paths.images_dir))
    for raw in extra_roots:
        root = Path(raw)
        if root.exists() and root.is_dir():
            watch_paths.extend(_iter_dirs_recursive(root))
    return _unique_paths(watch_paths)


@dataclass(frozen=True)
class ScanResult:
    scanned: int
    converted: int
    deleted_orphans: int
    mirrored_into_archive: int
    moved_from_icons: int
    deleted_from_icons: int
    errors: int = 0


class TrayScanWorker(QtCore.QObject):
    completed = QtCore.Signal(object, object)
    failed = QtCore.Signal(str)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = scan_and_convert()
            watch_paths = _build_watch_paths(_current_engine_paths(), _load_watch_folders())
            self.completed.emit(result, watch_paths)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def scan_and_convert(*, autocrop: bool = False, padding_mode: str | None = None) -> ScanResult:
    paths = _current_engine_paths()
    settings = _qsettings()
    selected_padding = eng.normalize_padding_mode(
        padding_mode if padding_mode is not None else settings.value("last_padding", "None")
    )
    archive_sources_dir = paths.images_dir
    generated_icons_dir = paths.icons_dir
    try:
        archive_sources_dir.mkdir(parents=True, exist_ok=True)
        generated_icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    promoted, _ = GenOps.reconcile_image_copies(paths=paths, logfn=_log)
    cleanup = GenOps.clean_generated_icons_dir(paths=paths, logfn=_log)
    mirrored = 0
    for root_raw in _load_watch_folders():
        watch_root = Path(root_raw)
        if not watch_root.exists() or not watch_root.is_dir():
            continue
        try:
            for path in watch_root.rglob("*"):
                if path.is_file() and path.suffix.lower() in eng.IMAGE_EXTS:
                    try:
                        imported = eng.mirror_copy_to_archive_sources(
                            path,
                            paths=paths,
                            source_root=watch_root,
                            logfn=_log,
                        )
                        if imported is not None:
                            mirrored += 1
                    except Exception as exc:
                        _log(f"Import error: {path} ({type(exc).__name__}: {exc})", level="error")
        except Exception as exc:
            _log(f"Watch scan error: {watch_root} ({type(exc).__name__}: {exc})", level="error")

    selected_sizes = eng.quality_preset_sizes(str(settings.value("last_quality", "16-256") or "16-256"))
    report = eng.scan_archive_sources_and_convert(
        paths=paths,
        sizes=selected_sizes,
        overwrite=False,
        autocrop=autocrop,
        padding_mode=selected_padding,
        remove_orphans=True,
        orphan_action="delete",
        logfn=_log,
    )
    archive_errors = report.errors
    if (
        promoted
        or cleanup.moved_images
        or cleanup.deleted_files
        or mirrored
        or report.converted
        or report.orphan_icons_removed
        or report.normalized_moves
    ):
        GenOps.publish_app_event(
            "archive-storage-changed",
            f"moved_images={cleanup.moved_images};deleted_invalid={cleanup.deleted_files};"
            f"mirrored={mirrored};converted={report.converted};orphans={report.orphan_icons_removed}",
        )
    return ScanResult(
        report.scanned,
        report.converted,
        report.orphan_icons_removed,
        mirrored,
        cleanup.moved_images,
        cleanup.deleted_files,
        archive_errors,
    )


class TrayAgent(QtWidgets.QSystemTrayIcon):
    """
    System tray entry point for unattended archive maintenance.

    The tray agent owns watcher attachment, debounce timing, periodic scans,
    and the small command menu used to reopen the UI or trigger a scan on
    demand.
    """
    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(get_app_icon(), parent=app)
        self.setToolTip(f"{APP_DISPLAY_VERSION} - background agent")
        self._scan_busy = False
        self._scan_pending = False
        self._scan_thread: QtCore.QThread | None = None
        self._scan_worker: TrayScanWorker | None = None
        self._scan_result: ScanResult | None = None
        self._scan_watch_paths: List[str] = []
        self._scan_error: str | None = None
        self._quit_requested = False
        self._last_seen_event_seq = 0
        self._last_requested_storage_root = _current_engine_paths().storage_root
        self.menu = QtWidgets.QMenu()
        self.menu.addAction(f"Open {APP_DISPLAY_NAME}", self.open_gen1)
        self.menu.addSeparator()
        self.menu.addAction("Scan Now", self._scan_now)
        self.menu.addAction(f"Quit {APP_DISPLAY_NAME}", self._quit_all)
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_click)
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(1200)
        self._debounce.timeout.connect(self._scan_now)
        self.watcher = QtCore.QFileSystemWatcher(self)
        self._periodic = QtCore.QTimer(self)
        self._periodic.setInterval(SCAN_INTERVAL_MS)
        self._periodic.timeout.connect(self._scan_now)
        self._event_timer = QtCore.QTimer(self)
        self._event_timer.setInterval(900)
        self._event_timer.timeout.connect(self._poll_app_events)
        self._periodic.start()
        self._event_timer.start()
        app.aboutToQuit.connect(self._wait_for_scan)
        app.aboutToQuit.connect(self._cleanup_copies_on_close)
        QtCore.QTimer.singleShot(100, self._scan_now)
        QtCore.QTimer.singleShot(250, lambda: self._poll_app_events(force=True))
        self.show()

    def _attach_watch(self, paths: List[str]) -> None:
        # Watch paths are rebuilt after scans so new subfolders become visible
        # without requiring the user to restart the tray worker.
        try:
            old_dirs = self.watcher.directories()
            if old_dirs:
                self.watcher.removePaths(old_dirs)
        except Exception:
            pass
        try:
            self.watcher.directoryChanged.disconnect()
        except Exception:
            pass
        if paths:
            try:
                self.watcher.addPaths(paths)
            except Exception as exc:
                _log(f"Watcher error: {type(exc).__name__}: {exc}", level="error")
        self.watcher.directoryChanged.connect(lambda *_: self._debounce.start())

    def _scan_now(self) -> None:
        if self._quit_requested:
            return
        if GenOps.is_archive_storage_paused():
            _log("Scan skipped: archive storage paused")
            return
        if self._scan_busy:
            self._scan_pending = True
            return
        self._scan_busy = True
        self._scan_pending = False
        self._scan_result = None
        self._scan_watch_paths = []
        self._scan_error = None
        thread = QtCore.QThread(self)
        worker = TrayScanWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_scan_result)
        worker.failed.connect(self._on_scan_error)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._scan_finished)
        self._scan_thread = thread
        self._scan_worker = worker
        thread.start()

    def _on_scan_result(self, result: ScanResult, paths: List[str]) -> None:
        self._scan_result = result
        self._scan_watch_paths = paths

    def _on_scan_error(self, message: str) -> None:
        self._scan_error = message

    def _scan_finished(self) -> None:
        result = self._scan_result
        error = self._scan_error
        self._scan_thread = None
        self._scan_worker = None
        self._scan_busy = False
        if error:
            _log(f"Scan failure: {error}", level="error")
            self.showMessage(APP_DISPLAY_NAME, f"Archive scan failed: {error}", QtWidgets.QSystemTrayIcon.Warning, 4000)
        elif result is not None:
            self._attach_watch(self._scan_watch_paths)
        if result is not None and result.errors:
            self.showMessage(
                APP_DISPLAY_NAME,
                f"Archive update incomplete: {result.errors} error(s). See the log.",
                QtWidgets.QSystemTrayIcon.Warning,
                4000,
            )
        elif (
            result is not None
            and (
                result.converted
                or result.deleted_orphans
                or result.mirrored_into_archive
                or result.moved_from_icons
                or result.deleted_from_icons
            )
        ):
            self.showMessage(
                APP_DISPLAY_NAME,
                f"Imported: {result.mirrored_into_archive + result.moved_from_icons}   "
                f"Converted: {result.converted}   "
                f"Deleted: {result.deleted_orphans + result.deleted_from_icons}",
                QtWidgets.QSystemTrayIcon.Information,
                2500,
            )
        if self._quit_requested:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.quit()
            return
        if self._scan_pending:
            QtCore.QTimer.singleShot(200, self._scan_now)

    def _wait_for_scan(self) -> None:
        if self._scan_thread is not None:
            self._scan_thread.quit()
            self._scan_thread.wait()

    def _cleanup_copies_on_close(self) -> None:
        GenOps.reconcile_image_copies(paths=_current_engine_paths(), on_start_or_close=True, logfn=_log)

    def _on_click(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.Trigger and self.contextMenu():
            self.contextMenu().popup(QtGui.QCursor.pos())

    def _quit_all(self) -> None:
        """Exit both the tray worker and any running UI through the shared app-event channel."""
        GenOps.publish_app_event("quit-all", "tray-menu")
        self._request_quit()

    def _request_quit(self) -> None:
        self._quit_requested = True
        if self._scan_busy:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()

    def _poll_app_events(self, force: bool = False) -> None:
        current_root = _current_engine_paths().storage_root
        root_changed = current_root != self._last_requested_storage_root
        if root_changed:
            self._last_requested_storage_root = current_root
            self._scan_now()
        event = GenOps.latest_app_event()
        if not force and event.seq <= self._last_seen_event_seq:
            return
        self._last_seen_event_seq = event.seq
        if not force and event.event_type == "quit-all":
            self._request_quit()
            return
        if not force and not root_changed and event.event_type in {
            "archive-storage-root-chosen", "archive-storage-relocated", "archive-storage-resumed"
        }:
            self._scan_now()

    def _run_detached(self, argv: List[str]) -> bool:
        try:
            if sys.platform.startswith("win"):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                subprocess.Popen(argv, close_fds=True, creationflags=creationflags)
            else:
                subprocess.Popen(argv, close_fds=True)
            return True
        except Exception as exc:
            _log(f"Launch ERR: {type(exc).__name__}: {exc}", level="error")
            return False

    def open_gen1(self) -> None:
        base = _exe_dir()
        if getattr(sys, "frozen", False):
            candidates = [Path(sys.executable)]
            if sys.platform.startswith("win"):
                candidates.insert(0, base / APP_EXECUTABLE_NAME)
            for exe in candidates:
                if exe.exists() and self._run_detached([str(exe), "--mode", "ui"]):
                    return
        for launcher_name in ("IconMakerMaster.py", "IconMaker.py"):
            launcher = base / launcher_name
            if launcher.exists() and self._run_detached([sys.executable, str(launcher), "--mode", "ui"]):
                return
        self._run_detached([sys.executable, str(base / "Gen1.py")])


def main() -> None:
    _pre_app_setup()
    GenOps.apply_launch_tray_at_startup()
    try:
        _archive_sources_dir().mkdir(parents=True, exist_ok=True)
        _generated_icons_dir().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    GenOps.reconcile_image_copies(paths=_current_engine_paths(), on_start_or_close=True, logfn=_log)
    app = QtWidgets.QApplication(sys.argv)
    apply_qt_application_identity(app)
    theme_manager()
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(get_app_icon())
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        _log("System tray not available")
        return
    _ = TrayAgent(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
