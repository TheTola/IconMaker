#!/usr/bin/env python3
# Gen3.py — IconMaker Background Tray Worker (tray-only, no UI)

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from PySide6 import QtCore, QtGui, QtWidgets

import Gen2 as eng
import GenLog
import GenOps
from Gen2 import EnginePaths
from Gen4 import get_app_icon

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"
ALL_SIZES: List[int] = list(range(8, 257, 8))
SCAN_INTERVAL_MS = 10 * 60 * 1000
LOG_MAX_BYTES = 2_000_000


def _pre_app_setup() -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ORG}.{APP_NAME}")
        except Exception:
            pass


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _qsettings() -> QtCore.QSettings:
    return QtCore.QSettings(APP_ORG, APP_NAME)


def _load_shared_library_root() -> Path | None:
    raw = str(_qsettings().value("library_root", "") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except Exception:
        return None


def _current_engine_paths() -> EnginePaths:
    root = _load_shared_library_root()
    if root is None:
        return GenOps.default_engine_paths()
    return EnginePaths.from_library_root(root)


def _icon_images_dir() -> Path:
    return _current_engine_paths().images_dir


def _icons_dir() -> Path:
    return _current_engine_paths().icons_dir


def _logs_dir() -> Path:
    return _icon_images_dir() / 'Logs'


def _log_file() -> Path:
    return _logs_dir() / 'tray.log'


def _rotate_log_if_needed() -> None:
    try:
        log_file = _log_file()
        logs_dir = _logs_dir()
        if log_file.exists() and log_file.stat().st_size > LOG_MAX_BYTES:
            bak = logs_dir / 'tray.log.1'
            try:
                bak.unlink(missing_ok=True)
            except Exception:
                pass
            log_file.rename(bak)
    except Exception:
        pass


def _log(msg: str) -> None:
    try:
        logs_dir = _logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed()
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with _log_file().open('a', encoding='utf-8', errors='ignore') as f:
            f.write(f'[{ts}] {msg}\n')
    except Exception:
        pass


def _unique_paths(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        key = str(Path(s).resolve()) if Path(s).exists() else str(Path(s))
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
        for p in root.rglob('*'):
            if p.is_dir():
                out.append(str(p))
    except Exception:
        return [str(root)]
    return _unique_paths(out)


def _load_watch_folders() -> List[str]:
    raw = _qsettings().value('watch_folders', [], type=list)
    return _unique_paths([str(x).strip() for x in (raw or []) if str(x).strip()])


@dataclass(frozen=True)
class ScanResult:
    scanned: int
    converted: int
    deleted_orphans: int
    mirrored_into_library: int


def scan_and_convert(*, autocrop: bool = False, padding_mode: str = 'balanced') -> ScanResult:
    paths = _current_engine_paths()
    images_dir = paths.images_dir
    icons_dir = paths.icons_dir
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        icons_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    mirrored = 0
    for root_raw in _load_watch_folders():
        root = Path(root_raw)
        if not root.exists() or not root.is_dir():
            continue
        try:
            for p in root.rglob('*'):
                if p.is_file() and p.suffix.lower() in eng.IMAGE_EXTS:
                    try:
                        dst = eng.mirror_copy_to_icon_images(p, paths=paths, logfn=_log)
                        if dst is not None:
                            mirrored += 1
                    except Exception as e:
                        _log(f'Import error: {p} ({type(e).__name__}: {e})', level='error')
        except Exception as e:
            _log(f'Watch scan error: {root} ({type(e).__name__}: {e})', level='error')
    report = eng.scan_icon_images_and_convert(
        paths=paths,
        sizes=ALL_SIZES,
        overwrite=False,
        autocrop=autocrop,
        padding_mode=padding_mode,
        remove_orphans=True,
        orphan_action='delete',
        logfn=_log,
    )
    if mirrored or report.converted or report.orphan_icons_removed or report.normalized_moves:
        GenOps.publish_app_event('library-changed', f'mirrored={mirrored};converted={report.converted};orphans={report.orphan_icons_removed}')
    return ScanResult(report.scanned, report.converted, report.orphan_icons_removed, mirrored)


class TrayAgent(QtWidgets.QSystemTrayIcon):
    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(get_app_icon(), parent=app)
        self.setToolTip('IconMaker — background agent')
        self._scan_busy = False
        self._scan_pending = False
        self.menu = QtWidgets.QMenu()
        self.menu.addAction('Open IconMaker', self.open_gen1)
        self.menu.addSeparator()
        self.menu.addAction('Scan Now', self._scan_now)
        self.menu.addAction('Exit', QtWidgets.QApplication.quit)
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
        self._attach_watch()
        self._periodic.start()
        QtCore.QTimer.singleShot(1000, self._scan_now)
        self.show()

    def _watch_paths(self) -> List[str]:
        paths: List[str] = []
        for raw in _load_watch_folders():
            root = Path(raw)
            if root.exists() and root.is_dir():
                paths.extend(_iter_dirs_recursive(root))
        return _unique_paths(paths)

    def _attach_watch(self) -> None:
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
        paths = self._watch_paths()
        if paths:
            try:
                self.watcher.addPaths(paths)
            except Exception as e:
                _log(f'Watcher error: {type(e).__name__}: {e}', level='error')
        self.watcher.directoryChanged.connect(lambda *_: self._debounce.start())

    def _scan_now(self) -> None:
        if GenOps.is_library_paused():
            _log('Scan skipped: library paused')
            return
        if self._scan_busy:
            self._scan_pending = True
            return
        self._scan_busy = True
        self._scan_pending = False
        try:
            res = scan_and_convert()
        except Exception as e:
            _log(f'Scan failure: {type(e).__name__}: {e}', level='error')
            self._scan_busy = False
            return
        self._scan_busy = False
        self._attach_watch()
        if res.converted or res.deleted_orphans or res.mirrored_into_library:
            self.showMessage('IconMaker', f'Imported: {res.mirrored_into_library}   Converted: {res.converted}   Deleted: {res.deleted_orphans}', QtWidgets.QSystemTrayIcon.Information, 2500)
        if self._scan_pending:
            QtCore.QTimer.singleShot(200, self._scan_now)

    def _on_click(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.Trigger and self.contextMenu():
            self.contextMenu().popup(QtGui.QCursor.pos())

    def _run_detached(self, argv: List[str]) -> bool:
        try:
            if sys.platform.startswith('win'):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                subprocess.Popen(argv, close_fds=True, creationflags=creationflags)
            else:
                subprocess.Popen(argv, close_fds=True)
            return True
        except Exception as e:
            _log(f'Launch ERR: {type(e).__name__}: {e}', level='error')
            return False

    def open_gen1(self) -> None:
        base = _exe_dir()
        if getattr(sys, 'frozen', False):
            exe = base / 'IconMaker.exe'
            if exe.exists() and self._run_detached([str(exe), '--mode', 'ui']):
                return
        if self._run_detached([sys.executable, str(base / 'IconMaker.py'), '--mode', 'ui']):
            return
        self._run_detached([sys.executable, str(base / 'Gen1.py')])


def main() -> None:
    _pre_app_setup()
    try:
        _icon_images_dir().mkdir(parents=True, exist_ok=True)
        _icons_dir().mkdir(parents=True, exist_ok=True)
        _logs_dir().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(get_app_icon())
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        _log('System tray not available')
        return
    _ = TrayAgent(app)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
