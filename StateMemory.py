from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets


@dataclass(frozen=True)
class StateKeys:
    last_input: str = "last_input"
    last_mode: str = "last_mode"              # "file" | "folder"
    last_input_dir: str = "last_input_dir"
    last_output_dir: str = "last_output_dir"
    last_recursive: str = "last_recursive"
    last_overwrite: str = "last_overwrite"
    last_padding: str = "last_padding"
    library_root: str = "library_root"


class StateMemory:
    """
    Centralized state persistence for Gen1 using QSettings.

    This class persists stable UI state and a small amount of app-level state.
    It avoids lying to the UI by validating restored paths before applying them.
    Autosave is debounced to avoid syncing settings on every keystroke.
    """

    AUTOSAVE_DELAY_MS = 250

    def __init__(self, org: str, app: str, logger: Optional[Callable[[str], None]] = None):
        self.settings = QtCore.QSettings(org, app)
        self.k = StateKeys()
        self._logger = logger
        self._timers: dict[int, QtCore.QTimer] = {}

    # ----------------- Logging -----------------

    def _log(self, message: str) -> None:
        text = f"[StateMemory] {message}"
        if callable(self._logger):
            try:
                self._logger(text)
                return
            except Exception:
                pass
        print(text)

    def _warn(self, context: str, exc: Exception) -> None:
        self._log(f"{context}: {exc.__class__.__name__}: {exc}")

    # ----------------- Settings helpers -----------------

    def get_library_root(self) -> str:
        return str(self.settings.value(self.k.library_root, "") or "").strip()

    def set_library_root(self, path: str) -> None:
        value = str(path or "").strip()
        self.settings.setValue(self.k.library_root, value)
        self.settings.sync()

    # ----------------- Widget helpers -----------------

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        m = (mode or "").strip().lower()
        return "folder" if m == "folder" else "file"

    def _require_attr(self, w: QtWidgets.QWidget, name: str):
        if not hasattr(w, name):
            raise AttributeError(f"UI missing required attribute: {name}")
        return getattr(w, name)

    def get_mode_from_ui(self, w: QtWidgets.QWidget) -> str:
        mode_seg = self._require_attr(w, "mode_seg")
        try:
            return self._normalize_mode(mode_seg.mode())
        except Exception as exc:
            self._warn("mode_seg.mode() failed", exc)
            return "file"

    def set_mode_to_ui(self, w: QtWidgets.QWidget, mode: str) -> None:
        mode = self._normalize_mode(mode)
        mode_seg = self._require_attr(w, "mode_seg")
        try:
            mode_seg.set_mode(mode)
        except Exception as exc:
            self._warn("mode_seg.set_mode() failed", exc)

        self.apply_truthful_source_ui(w, mode)

    # ----------------- Truthful UI -----------------

    def apply_truthful_source_ui(self, w: QtWidgets.QWidget, mode: Optional[str] = None) -> None:
        mode = self._normalize_mode(mode or self.get_mode_from_ui(w))

        btn_browse_input = self._require_attr(w, "btn_browse_input")
        if btn_browse_input is not None:
            try:
                btn_browse_input.setText("Select Image…" if mode == "file" else "Select Folder…")
            except Exception as exc:
                self._warn("updating browse button text failed", exc)

        input_edit = self._require_attr(w, "edit_input")
        if input_edit is not None:
            try:
                input_edit.setPlaceholderText(
                    "Drop an image here or click Select Image…"
                    if mode == "file"
                    else "Drop a folder here or click Select Folder…"
                )
            except Exception as exc:
                self._warn("updating input placeholder failed", exc)

    # ----------------- Load / Save -----------------

    def load_to_ui(self, w: QtWidgets.QWidget) -> None:
        mode = self._normalize_mode(str(self.settings.value(self.k.last_mode, "file") or "file"))
        last_input = str(self.settings.value(self.k.last_input, "") or "").strip()
        last_out = str(self.settings.value(self.k.last_output_dir, "") or "").strip()
        recursive = bool(self.settings.value(self.k.last_recursive, False, type=bool))
        overwrite = bool(self.settings.value(self.k.last_overwrite, True, type=bool))
        padding = str(self.settings.value(self.k.last_padding, "") or "").strip()

        input_edit = self._require_attr(w, "edit_input")
        output_edit = self._require_attr(w, "lbl_outdir")
        chk_recursive = self._require_attr(w, "chk_recursive")
        chk_overwrite = self._require_attr(w, "chk_overwrite")
        cmb_padding = self._require_attr(w, "cmb_padding")

        blockers: list[QtCore.QSignalBlocker] = []
        for obj in (input_edit, output_edit, chk_recursive, chk_overwrite, cmb_padding):
            if obj is not None:
                try:
                    blockers.append(QtCore.QSignalBlocker(obj))
                except Exception:
                    pass

        self.set_mode_to_ui(w, mode)

        safe_input = self._validated_input_for_mode(last_input, mode)
        safe_out = self._validated_output_dir(last_out)

        if input_edit is not None:
            try:
                input_edit.setText(safe_input)
            except Exception as exc:
                self._warn("restoring input path failed", exc)

        if output_edit is not None:
            try:
                output_edit.setText(safe_out)
            except Exception as exc:
                self._warn("restoring output path failed", exc)

        if chk_recursive is not None:
            try:
                chk_recursive.setChecked(recursive)
            except Exception as exc:
                self._warn("restoring recursive flag failed", exc)

        if chk_overwrite is not None:
            try:
                chk_overwrite.setChecked(overwrite)
            except Exception as exc:
                self._warn("restoring overwrite flag failed", exc)

        if cmb_padding is not None:
            try:
                if padding:
                    idx = cmb_padding.findText(padding)
                    if idx >= 0:
                        cmb_padding.setCurrentIndex(idx)
                    else:
                        self._log(f"saved padding '{padding}' not found in combo box; leaving current selection")
            except Exception as exc:
                self._warn("restoring padding failed", exc)

        del blockers
        self.apply_truthful_source_ui(w, mode)

    def save_from_ui(self, w: QtWidgets.QWidget) -> None:
        mode = self.get_mode_from_ui(w)
        input_edit = self._require_attr(w, "edit_input")
        output_edit = self._require_attr(w, "lbl_outdir")
        chk_recursive = self._require_attr(w, "chk_recursive")
        chk_overwrite = self._require_attr(w, "chk_overwrite")
        cmb_padding = self._require_attr(w, "cmb_padding")

        input_path = self._read_text_widget(input_edit, "input_edit")
        out_path = self._read_text_widget(output_edit, "output_edit")

        self.settings.setValue(self.k.last_mode, mode)
        self.settings.setValue(self.k.last_input, input_path)
        self.settings.setValue(self.k.last_output_dir, out_path)

        in_dir = self._dir_for_path(
            input_path,
            fallback=str(self.settings.value(self.k.last_input_dir, "") or ""),
        )
        self.settings.setValue(self.k.last_input_dir, in_dir)

        if chk_recursive is not None:
            try:
                self.settings.setValue(self.k.last_recursive, bool(chk_recursive.isChecked()))
            except Exception as exc:
                self._warn("saving recursive flag failed", exc)

        if chk_overwrite is not None:
            try:
                self.settings.setValue(self.k.last_overwrite, bool(chk_overwrite.isChecked()))
            except Exception as exc:
                self._warn("saving overwrite flag failed", exc)

        if cmb_padding is not None:
            try:
                self.settings.setValue(self.k.last_padding, str(cmb_padding.currentText()).strip())
            except Exception as exc:
                self._warn("saving padding failed", exc)

        self.settings.sync()

    def _read_text_widget(self, widget, name: str) -> str:
        try:
            return str(widget.text()).strip()
        except Exception as exc:
            self._warn(f"reading {name} failed", exc)
            return ""

    # ----------------- Auto-save wiring -----------------

    def install_auto_save(self, w: QtWidgets.QWidget) -> None:
        """
        Connect common signals to debounced state persistence.
        You still should call save_from_ui() in closeEvent as a final guarantee.
        """
        self._ensure_timer(w)

        mode_seg = self._require_attr(w, "mode_seg")
        for btn in (mode_seg.btn_file, mode_seg.btn_folder):
            try:
                btn.toggled.connect(lambda _=False, win=w: self._schedule_save(win))
            except Exception as exc:
                self._warn("connecting autosave for mode buttons failed", exc)

        input_edit = self._require_attr(w, "edit_input")
        if input_edit is not None:
            try:
                input_edit.textChanged.connect(lambda _=None, win=w: self._schedule_save(win))
            except Exception as exc:
                self._warn("connecting autosave for 'input_edit' failed", exc)

        for name in ("chk_recursive", "chk_overwrite"):
            chk = self._require_attr(w, name)
            try:
                chk.stateChanged.connect(lambda _=None, win=w: self._schedule_save(win))
            except Exception as exc:
                self._warn(f"connecting autosave for '{name}' failed", exc)

        cmb_padding = self._require_attr(w, "cmb_padding")
        try:
            cmb_padding.currentIndexChanged.connect(lambda _=None, win=w: self._schedule_save(win))
        except Exception as exc:
            self._warn("connecting autosave for cmb_padding failed", exc)

    def _ensure_timer(self, w: QtWidgets.QWidget) -> QtCore.QTimer:
        key = id(w)
        timer = self._timers.get(key)
        if timer is None:
            timer = QtCore.QTimer(w)
            timer.setSingleShot(True)
            timer.setInterval(self.AUTOSAVE_DELAY_MS)
            timer.timeout.connect(lambda win=w: self._flush_scheduled_save(win))
            self._timers[key] = timer
            try:
                w.destroyed.connect(lambda *_args, wid=key: self._timers.pop(wid, None))
            except Exception:
                pass
        return timer

    def _schedule_save(self, w: QtWidgets.QWidget) -> None:
        mode = self.get_mode_from_ui(w)
        self.apply_truthful_source_ui(w, mode)
        self._ensure_timer(w).start()

    def _flush_scheduled_save(self, w: QtWidgets.QWidget) -> None:
        self.save_from_ui(w)

    # ----------------- Utilities -----------------

    @staticmethod
    def _validated_input_for_mode(path_str: str, mode: str) -> str:
        value = str(path_str or "").strip()
        if not value:
            return ""
        try:
            p = Path(value)
            if mode == "folder":
                return str(p) if p.exists() and p.is_dir() else ""
            return str(p) if p.exists() and p.is_file() else ""
        except Exception:
            return ""

    @staticmethod
    def _validated_output_dir(path_str: str) -> str:
        value = str(path_str or "").strip()
        if not value:
            return ""
        try:
            p = Path(value)
            return str(p) if p.exists() and p.is_dir() else ""
        except Exception:
            return ""

    @staticmethod
    def _dir_for_path(path_str: str, fallback: str = "") -> str:
        p = Path(path_str) if path_str else None
        if p:
            try:
                if p.exists():
                    return str(p if p.is_dir() else p.parent)
                return str(p.parent if p.suffix else p)
            except Exception:
                pass
        return str(fallback or "")
