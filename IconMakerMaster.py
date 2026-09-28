#!/usr/bin/env python3
"""
IconMakerMaster.py — Unified launcher
Runs:
    • Gen1 UI
    • Gen3 tray worker

Supports modes:
    --mode both (default)
    --mode ui
    --mode tray
    --mode generator

Top-level responsibilities in this file:
- choose UI-only, tray-only, or combined launch mode
- prepare shared runtime dependencies for source and frozen builds
- enforce single-instance rules for UI and tray processes
- record crash details before unexpected shutdown reaches the user
- spawn the tray worker only from the process that owns the active UI session
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Final, Optional

import GenLog
from AppIdentity import (
    APP_COPYRIGHT,
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_ORG,
    APP_VERSION,
    apply_qt_application_identity,
)


def _runtime_logger_name() -> str:
    return "launcher"


def _log(message: str, *, level: str = "info") -> None:
    try:
        GenLog.write_line(_runtime_logger_name(), message, level=level)
    except Exception:
        pass

TRAY_MUTEX_NAME: Final[str] = "Global\\IconMakerTrayMutex"
UI_MUTEX_NAME: Final[str] = "Global\\IconMakerUiMutex"
GENERATOR_MUTEX_NAME: Final[str] = "Global\\IconMakerGeneratorMutex"


_RUNTIME_PATCHED = False
_CAIRO_PATCHED = False
_QT_PATCHED = False
_TRAY_MUTEX_HANDLE: Optional[int] = None
_UI_MUTEX_HANDLE: Optional[int] = None
_GENERATOR_MUTEX_HANDLE: Optional[int] = None


def _patch_cairo_dll_path() -> None:
    """Ensure libcairo is discoverable on Windows."""
    global _CAIRO_PATCHED

    if _CAIRO_PATCHED or os.name != "nt":
        return

    cairo_bin = r"C:\msys64\ucrt64\bin"
    if not os.path.isdir(cairo_bin):
        _CAIRO_PATCHED = True
        return

    try:
        os.add_dll_directory(cairo_bin)  # type: ignore[attr-defined]
    except Exception:
        pass

    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if cairo_bin not in path_parts:
        os.environ["PATH"] = cairo_bin + os.pathsep + current_path

    _CAIRO_PATCHED = True


def _patch_qt_plugin_path() -> None:
    """Ensure Qt can find platform plugins in dev and frozen runs."""
    global _QT_PATCHED

    if _QT_PATCHED:
        return

    try:
        from PySide6 import QtCore
    except Exception:
        return

    base: Path | None = None

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "PySide6" / "plugins"  # type: ignore[attr-defined]
    else:
        try:
            import PySide6

            base = Path(PySide6.__path__[0]) / "plugins"
        except Exception:
            base = None

    if base and base.exists():
        os.environ["QT_PLUGIN_PATH"] = str(base)

        paths = [str(Path(p)) for p in QtCore.QCoreApplication.libraryPaths()]
        if str(base) not in paths:
            QtCore.QCoreApplication.setLibraryPaths([str(base), *paths])

    _QT_PATCHED = True


def _prepare_runtime() -> None:
    """Prepare shared runtime dependencies before UI or tray code imports."""
    global _RUNTIME_PATCHED
    if _RUNTIME_PATCHED:
        return

    _patch_cairo_dll_path()
    _patch_qt_plugin_path()
    _log(f"runtime prepared frozen={is_frozen()} base={app_base_dir()}")
    _RUNTIME_PATCHED = True


def parse_mode(argv: list[str]) -> str:
    """Return run mode from CLI args."""
    for i, arg in enumerate(argv):
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1].lower().strip()
            if mode in {"both", "ui", "tray", "generator"}:
                return mode
        if arg == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1].lower().strip()
            if mode in {"both", "ui", "tray", "generator"}:
                return mode
    return "both"


def is_frozen() -> bool:
    """Return True when the app is running from a packaged bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def script_path() -> Path:
    """Resolve the launcher path even when __file__ is unavailable or redirected."""
    try:
        return Path(__file__).resolve()
    except Exception:
        return Path(sys.argv[0]).resolve()


def app_base_dir() -> Path:
    """Return the folder that should be treated as the runtime root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return script_path().parent


def crash_log_dir() -> Path:
    """Use a user-writable crash-log location that survives launcher failures."""
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            return Path(local_appdata) / APP_ORG / APP_NAME / "CrashLogs"
    return app_base_dir() / "CrashLogs"


def _safe_mkdir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def write_crash_log(exc: BaseException, *, mode: str) -> Path | None:
    log_dir = crash_log_dir()
    _safe_mkdir(log_dir)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = log_dir / f"crash_{timestamp}_{mode}.log"

    lines = [
        f"App: {APP_DISPLAY_NAME}",
        f"Version: {APP_VERSION}",
        f"Organization: {APP_ORG}",
        f"Mode: {mode}",
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"Frozen: {is_frozen()}",
        f"Executable: {sys.executable}",
        f"Script: {script_path()}",
        f"Working Directory: {Path.cwd()}",
        f"Python: {sys.version}",
        f"Platform: {sys.platform}",
        f"Args: {sys.argv}",
        "",
        "Traceback:",
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    ]

    try:
        log_path.write_text("\n".join(lines), encoding="utf-8", errors="ignore")
        return log_path
    except Exception:
        return None


def show_fatal_error(message: str, *, title: str = APP_DISPLAY_NAME) -> None:
    """Show a fatal error dialog without assuming the main UI is already running."""
    try:
        from PySide6 import QtWidgets

        app = QtWidgets.QApplication.instance()
        owns_app = False
        if app is None:
            app = QtWidgets.QApplication(sys.argv)
            owns_app = True
        apply_qt_application_identity(app)

        QtWidgets.QMessageBox.critical(None, title, message)

        if owns_app:
            app.quit()
    except Exception:
        pass


def run_with_crash_logging(fn, *, mode: str, show_ui_error: bool) -> None:
    """Run a top-level entry point and persist a crash record for unexpected failures."""
    try:
        fn()
    except SystemExit:
        raise
    except BaseException as exc:
        log_path = write_crash_log(exc, mode=mode)
        if show_ui_error:
            if log_path is not None:
                msg = (
                    f"{APP_DISPLAY_NAME} {APP_VERSION} crashed.\n\n"
                    f"A crash log was written to:\n{log_path}"
                )
            else:
                msg = f"{APP_DISPLAY_NAME} {APP_VERSION} crashed and the crash log could not be written."
            show_fatal_error(msg)
        raise


def _close_handle(handle: int | None) -> None:
    if os.name != "nt" or not handle:
        return

    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass


def _acquire_named_mutex(name: str) -> int | None:
    """Return a retained OS mutex handle when this process owns the named slot."""
    if os.name != "nt":
        return 1

    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p

        handle = kernel32.CreateMutexW(None, 0, name)
        if not handle:
            return None

        error_code = ctypes.get_last_error()
        if error_code == 183:  # ERROR_ALREADY_EXISTS
            _close_handle(int(handle))
            return None

        return int(handle)
    except Exception:
        return 1


def _release_tray_mutex() -> None:
    global _TRAY_MUTEX_HANDLE
    _close_handle(_TRAY_MUTEX_HANDLE)
    _TRAY_MUTEX_HANDLE = None


def _release_ui_mutex() -> None:
    global _UI_MUTEX_HANDLE
    _close_handle(_UI_MUTEX_HANDLE)
    _UI_MUTEX_HANDLE = None


atexit.register(_release_tray_mutex)
atexit.register(_release_ui_mutex)


def acquire_tray_mutex() -> bool:
    """Acquire and retain the tray single-instance mutex for process lifetime."""
    global _TRAY_MUTEX_HANDLE

    if _TRAY_MUTEX_HANDLE:
        return True

    handle = _acquire_named_mutex(TRAY_MUTEX_NAME)
    if handle is None:
        return False

    _TRAY_MUTEX_HANDLE = handle
    return True


def acquire_ui_mutex() -> bool:
    """Acquire and retain the UI single-instance mutex for process lifetime."""
    global _UI_MUTEX_HANDLE

    if _UI_MUTEX_HANDLE:
        return True

    handle = _acquire_named_mutex(UI_MUTEX_NAME)
    if handle is None:
        return False

    _UI_MUTEX_HANDLE = handle
    return True


def acquire_generator_mutex() -> bool:
    """Keep one browser profile owner per desktop session."""
    global _GENERATOR_MUTEX_HANDLE

    if _GENERATOR_MUTEX_HANDLE:
        return True
    handle = _acquire_named_mutex(GENERATOR_MUTEX_NAME)
    if handle is None:
        return False
    _GENERATOR_MUTEX_HANDLE = handle
    return True


DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def spawn_detached(args: list[str], cwd: str | None = None) -> None:
    """Spawn a detached background process."""
    _log(f"spawn detached: {' '.join(map(str, args))}")
    if os.name == "nt":
        subprocess.Popen(
            args,
            close_fds=True,
            cwd=cwd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
    else:
        subprocess.Popen(args, close_fds=True, cwd=cwd)


def tray_command() -> list[str]:
    """Build the tray-worker launch command for frozen, source, and legacy entry points."""
    if is_frozen():
        return [sys.executable, "--mode", "tray"]
    launcher = script_path()
    if launcher.name.lower() not in {'iconmakermaster.py', 'iconmaker.py'}:
        candidate = app_base_dir() / 'IconMakerMaster.py'
        if not candidate.exists():
            candidate = app_base_dir() / 'IconMaker.py'
        launcher = candidate
    return [sys.executable, str(launcher), "--mode", "tray"]


def run_ui() -> None:
    """Boot the main application window."""
    def launch() -> None:
        _prepare_runtime()
        from StartupSplash import run_ui as run_ui_with_splash

        run_ui_with_splash()

    run_with_crash_logging(launch, mode="ui", show_ui_error=True)


def run_tray() -> None:
    """Boot the unattended tray/watch worker."""
    def launch() -> None:
        _prepare_runtime()
        from Gen3 import main as gen3_main

        gen3_main()

    run_with_crash_logging(launch, mode="tray", show_ui_error=False)


def run_generator() -> None:
    """Run embedded image sites away from the main UI process."""
    def launch() -> None:
        _prepare_runtime()
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        existing_flags = {part.split("=", 1)[0] for part in flags.split()}
        for flag in (
            "--disable-gpu",
            "--disable-accelerated-video-encode",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ):
            if flag.split("=", 1)[0] not in existing_flags:
                flags = f"{flags} {flag}".strip()
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
        from ImageSiteHub import main as generator_main

        generator_main()

    run_with_crash_logging(launch, mode="generator", show_ui_error=True)


def _show_already_running_ui_message() -> None:
    _log('ui launch blocked: already running', level='warning')
    show_fatal_error(f"{APP_DISPLAY_NAME} is already running.")


def main() -> None:
    mode = parse_mode(sys.argv)
    _log(f'launcher start mode={mode} frozen={is_frozen()} exe={sys.executable}')

    if mode == "tray":
        if not acquire_tray_mutex():
            _log('tray launch blocked: already running', level='warning')
            return
        run_tray()
        return

    if mode == "generator":
        if not acquire_generator_mutex():
            _log("generator launch blocked: already running", level="warning")
            return
        run_generator()
        return

    if mode == "ui":
        if not acquire_ui_mutex():
            _show_already_running_ui_message()
            return
        run_ui()
        return

    # In combined mode the process that wins the UI mutex owns the session and is
    # the only process allowed to spawn the tray worker. This prevents duplicate
    # tray children when the user launches the app more than once.
    if not acquire_ui_mutex():
        _show_already_running_ui_message()
        return

    try:
        spawn_detached(tray_command(), cwd=str(app_base_dir()))
    except Exception as e:
        _log(f'tray spawn failed: {type(e).__name__}: {e}', level='error')
    run_ui()


if __name__ == "__main__":
    main()
