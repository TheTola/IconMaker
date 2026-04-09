"""
Persistent UI-state helpers for IconMaker.

StateMemory restores recent workflow choices for the main window, migrates
renamed archive-storage keys, and debounces settings writes so routine UI
changes do not compete with background refresh activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets

import GenOps


@dataclass(frozen=True)
class StateKeys:
    """Stable QSettings keys used by the main window across app launches."""

    last_input: str = "last_input"
    last_mode: str = "last_mode"
    last_input_dir: str = "last_input_dir"
    last_output_dir: str = "last_output_dir"
    last_recursive: str = "last_recursive"
    last_overwrite: str = "last_overwrite"
    last_padding: str = "last_padding"
    last_quality: str = "last_quality"
    archive_storage_root: str = "archive_storage_root"
    archive_root_legacy: str = "archive_root"
    library_root_legacy: str = "library_root"


class StateMemory:
    """
    Centralized QSettings-backed state persistence for Gen1.

    This class restores stable workflow state, keeps archive-storage settings
    consistent with current naming, and debounces writes so routine UI changes
    do not spam disk or fight with background refresh behavior.
    """

    AUTOSAVE_DELAY_MS = 250

    def __init__(self, org: str, app: str, logger: Optional[Callable[[str], None]] = None):
        self.settings = QtCore.QSettings(org, app)
        self.k = StateKeys()
        self._logger = logger
        self._timers: dict[int, QtCore.QTimer] = {}

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

    def get_archive_storage_root(self) -> str:
        """Return the current archive-storage root after legacy-key migration."""
        root = GenOps.load_archive_storage_root(self.settings)
        return str(root) if root is not None else ""

    def set_archive_storage_root(self, path: str) -> None:
        """Persist the archive-storage root using the current naming scheme."""
        GenOps.save_archive_storage_root(path, self.settings)

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        value = (mode or "").strip().lower()
        return "folder" if value == "folder" else "file"

    def _require_attr(self, widget: QtWidgets.QWidget, name: str):
        if not hasattr(widget, name):
            raise AttributeError(f"UI missing required attribute: {name}")
        return getattr(widget, name)

    def _optional_attr(self, widget: QtWidgets.QWidget, *names: str):
        for name in names:
            if hasattr(widget, name):
                return getattr(widget, name)
        return None

    def get_mode_from_ui(self, widget: QtWidgets.QWidget) -> str:
        """Read the current input mode from the source-mode segmented control."""
        mode_seg = self._require_attr(widget, "mode_seg")
        try:
            if hasattr(mode_seg, "mode"):
                return self._normalize_mode(mode_seg.mode())
            return "folder" if bool(mode_seg.btn_folder.isChecked()) else "file"
        except Exception as exc:
            self._warn("reading mode from UI failed", exc)
            return "file"

    def set_mode_to_ui(self, widget: QtWidgets.QWidget, mode: str) -> None:
        """Apply a normalized input mode and refresh source-field copy to match it."""
        mode_seg = self._require_attr(widget, "mode_seg")
        value = self._normalize_mode(mode)
        try:
            mode_seg.set_mode(value)
        except Exception as exc:
            self._warn("mode_seg.set_mode() failed", exc)
        self.apply_truthful_source_ui(widget, value)

    def apply_truthful_source_ui(self, widget: QtWidgets.QWidget, mode: Optional[str] = None) -> None:
        """
        Keep source-field labels honest about what the current mode accepts.

        File mode expects a single incoming image. Folder mode expects a source
        directory for batch conversion.
        """
        value = self._normalize_mode(mode or self.get_mode_from_ui(widget))

        btn_browse_input = self._require_attr(widget, "btn_browse_input")
        try:
            btn_browse_input.setText("Select Image..." if value == "file" else "Select Folder...")
        except Exception as exc:
            self._warn("updating browse button text failed", exc)

        input_edit = self._require_attr(widget, "edit_input")
        try:
            input_edit.setPlaceholderText(
                "Drop an image here or click Select Image..."
                if value == "file"
                else "Drop a folder here or click Select Folder..."
            )
        except Exception as exc:
            self._warn("updating input placeholder failed", exc)

    def load_to_ui(self, widget: QtWidgets.QWidget) -> None:
        """
        Restore the main workflow controls from persisted settings.

        Signals are blocked during restore so loading state does not trigger a
        new run, refresh, or autosave cycle before the window is ready.
        """
        mode = self._normalize_mode(str(self.settings.value(self.k.last_mode, "file") or "file"))
        last_input = str(self.settings.value(self.k.last_input, "") or "").strip()
        last_output = str(self.settings.value(self.k.last_output_dir, "") or "").strip()
        recursive = bool(self.settings.value(self.k.last_recursive, False, type=bool))
        overwrite = bool(self.settings.value(self.k.last_overwrite, True, type=bool))
        padding = str(self.settings.value(self.k.last_padding, "") or "").strip()
        quality = str(self.settings.value(self.k.last_quality, "16-1024") or "16-1024").strip()

        input_edit = self._require_attr(widget, "edit_input")
        output_label = self._optional_attr(widget, "lbl_generated_icons_dir", "lbl_outdir")
        chk_recursive = self._require_attr(widget, "chk_recursive")
        chk_overwrite = self._require_attr(widget, "chk_overwrite")
        cmb_padding = self._require_attr(widget, "cmb_padding")
        cmb_quality = self._require_attr(widget, "cmb_quality")

        blockers: list[QtCore.QSignalBlocker] = []
        for obj in (input_edit, output_label, chk_recursive, chk_overwrite, cmb_padding, cmb_quality):
            if obj is None:
                continue
            try:
                blockers.append(QtCore.QSignalBlocker(obj))
            except Exception:
                pass

        self.set_mode_to_ui(widget, mode)

        safe_input = self._validated_input_for_mode(last_input, mode)
        safe_output = self._validated_output_dir(last_output)

        try:
            input_edit.setText(safe_input)
        except Exception as exc:
            self._warn("restoring input path failed", exc)

        if output_label is not None:
            try:
                output_label.setText(safe_output)
            except Exception as exc:
                self._warn("restoring output path failed", exc)

        try:
            chk_recursive.setChecked(recursive)
        except Exception as exc:
            self._warn("restoring recursive flag failed", exc)

        try:
            chk_overwrite.setChecked(overwrite)
        except Exception as exc:
            self._warn("restoring overwrite flag failed", exc)

        try:
            if padding:
                index = cmb_padding.findText(padding)
                if index >= 0:
                    cmb_padding.setCurrentIndex(index)
        except Exception as exc:
            self._warn("restoring padding failed", exc)

        try:
            index = cmb_quality.findText(quality)
            if index >= 0:
                cmb_quality.setCurrentIndex(index)
        except Exception as exc:
            self._warn("restoring quality failed", exc)

        del blockers
        self.apply_truthful_source_ui(widget, mode)

    def save_from_ui(self, widget: QtWidgets.QWidget) -> None:
        """Persist the current main-window workflow controls back into QSettings."""
        mode = self.get_mode_from_ui(widget)
        input_edit = self._require_attr(widget, "edit_input")
        output_label = self._optional_attr(widget, "lbl_generated_icons_dir", "lbl_outdir")
        chk_recursive = self._require_attr(widget, "chk_recursive")
        chk_overwrite = self._require_attr(widget, "chk_overwrite")
        cmb_padding = self._require_attr(widget, "cmb_padding")
        cmb_quality = self._require_attr(widget, "cmb_quality")

        input_path = self._read_text_widget(input_edit, "input_edit")
        output_path = self._read_text_widget(output_label, "output_label") if output_label is not None else ""

        self.settings.setValue(self.k.last_mode, mode)
        self.settings.setValue(self.k.last_input, input_path)
        self.settings.setValue(self.k.last_output_dir, output_path)
        self.settings.setValue(
            self.k.last_input_dir,
            self._dir_for_path(input_path, fallback=str(self.settings.value(self.k.last_input_dir, "") or "")),
        )

        try:
            self.settings.setValue(self.k.last_recursive, bool(chk_recursive.isChecked()))
        except Exception as exc:
            self._warn("saving recursive flag failed", exc)

        try:
            self.settings.setValue(self.k.last_overwrite, bool(chk_overwrite.isChecked()))
        except Exception as exc:
            self._warn("saving overwrite flag failed", exc)

        try:
            self.settings.setValue(self.k.last_padding, str(cmb_padding.currentText()).strip())
        except Exception as exc:
            self._warn("saving padding failed", exc)

        try:
            self.settings.setValue(self.k.last_quality, str(cmb_quality.currentText()).strip())
        except Exception as exc:
            self._warn("saving quality failed", exc)

        self.settings.sync()

    def _read_text_widget(self, widget, name: str) -> str:
        try:
            return str(widget.text()).strip()
        except Exception as exc:
            self._warn(f"reading {name} failed", exc)
            return ""

    def install_auto_save(self, widget: QtWidgets.QWidget) -> None:
        """
        Attach a debounced autosave pipeline to the main workflow controls.

        Small delays keep drag/drop, typing, and checkbox toggles responsive
        without writing settings on every individual signal.
        """
        self._ensure_timer(widget)

        mode_seg = self._require_attr(widget, "mode_seg")
        for button in (mode_seg.btn_file, mode_seg.btn_folder):
            try:
                button.toggled.connect(lambda _=False, win=widget: self._schedule_save(win))
            except Exception as exc:
                self._warn("connecting autosave for mode buttons failed", exc)

        input_edit = self._require_attr(widget, "edit_input")
        try:
            input_edit.textChanged.connect(lambda _=None, win=widget: self._schedule_save(win))
        except Exception as exc:
            self._warn("connecting autosave for input_edit failed", exc)

        for name in ("chk_recursive", "chk_overwrite"):
            checkbox = self._require_attr(widget, name)
            try:
                checkbox.stateChanged.connect(lambda _=None, win=widget: self._schedule_save(win))
            except Exception as exc:
                self._warn(f"connecting autosave for {name} failed", exc)

        cmb_padding = self._require_attr(widget, "cmb_padding")
        try:
            cmb_padding.currentIndexChanged.connect(lambda _=None, win=widget: self._schedule_save(win))
        except Exception as exc:
            self._warn("connecting autosave for cmb_padding failed", exc)

        cmb_quality = self._require_attr(widget, "cmb_quality")
        try:
            cmb_quality.currentIndexChanged.connect(lambda _=None, win=widget: self._schedule_save(win))
        except Exception as exc:
            self._warn("connecting autosave for cmb_quality failed", exc)

    def _ensure_timer(self, widget: QtWidgets.QWidget) -> QtCore.QTimer:
        """Return a per-window single-shot timer used to debounce settings writes."""
        key = id(widget)
        timer = self._timers.get(key)
        if timer is None:
            timer = QtCore.QTimer(widget)
            timer.setSingleShot(True)
            timer.setInterval(self.AUTOSAVE_DELAY_MS)
            timer.timeout.connect(lambda win=widget: self._flush_scheduled_save(win))
            self._timers[key] = timer
            try:
                widget.destroyed.connect(lambda *_args, wid=key: self._timers.pop(wid, None))
            except Exception:
                pass
        return timer

    def _schedule_save(self, widget: QtWidgets.QWidget) -> None:
        mode = self.get_mode_from_ui(widget)
        self.apply_truthful_source_ui(widget, mode)
        self._ensure_timer(widget).start()

    def _flush_scheduled_save(self, widget: QtWidgets.QWidget) -> None:
        self.save_from_ui(widget)

    @staticmethod
    def _validated_input_for_mode(path_str: str, mode: str) -> str:
        """Only restore saved input paths that still match the active source mode."""
        value = str(path_str or "").strip()
        if not value:
            return ""
        try:
            path = Path(value)
            if mode == "folder":
                return str(path) if path.exists() and path.is_dir() else ""
            return str(path) if path.exists() and path.is_file() else ""
        except Exception:
            return ""

    @staticmethod
    def _validated_output_dir(path_str: str) -> str:
        """Only restore generated-icon folders that still exist on disk."""
        value = str(path_str or "").strip()
        if not value:
            return ""
        try:
            path = Path(value)
            return str(path) if path.exists() and path.is_dir() else ""
        except Exception:
            return ""

    @staticmethod
    def _dir_for_path(path_str: str, fallback: str = "") -> str:
        """Infer a useful folder seed from a saved path for later file dialogs."""
        path = Path(path_str) if path_str else None
        if path is not None:
            try:
                if path.exists():
                    return str(path if path.is_dir() else path.parent)
                return str(path.parent if path.suffix else path)
            except Exception:
                pass
        return str(fallback or "")
