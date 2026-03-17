#!/usr/bin/env python3
r"""
Gen1.py — IconMaker Main UI (Dark Neon)

Queue replaced with Library Previewer:
- Preview thumbnail
- Name (stem)
- Icon Path (optional)

Interactions:
- Double-click preview/name: open source image (preferred over .ico viewing)
- Right-click menu:
    Open Source Image
    Open Icon
    Delete from Library
    Copy Source Image
    Copy Icon
- Drag & drop files/folders into library list OR anywhere in app:
    Imports into canonical Icon Images library once per image detected
- Double-click reset behavior (deterministic):
    Double-click a library item toggles input selection:
      - if input already equals item → clear input
      - else set input to item
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import subprocess

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Iterable

from StateMemory import StateMemory

from PySide6 import QtCore, QtGui, QtWidgets

from GenLibrary import LibraryOverlayHost
import Gen2 as eng
import GenLog
import GenOps

from Gen4 import (
    get_app_icon,
    get_title_pixmap,
)

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"

SAGE_URL = "https://chatgpt.com/g/g-68e8c5f35ff0819195a81c501942a072-sage-of-iconer"

# --- Sage button image (PNG) ---
SAGE_BUTTON_IMAGE_PATH = "assets/IcoSage.png"

# Controls the Sage button size:
HERO_ICON_SIZE = 200
SAGE_BTN_SIZE = HERO_ICON_SIZE


def _count_files(root: Path) -> int:
    n = 0
    for _dirpath, _dirnames, filenames in os.walk(root):
        n += len(filenames)
    return n


def _open_path(path: str) -> None:
    """Open a folder/file with OS default behavior."""
    p = Path(path)
    if not p.exists():
        return
    if sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))


def _is_image_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}


def _gather_images_from_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        try:
            p = Path(p)
        except Exception:
            continue

        if not p.exists():
            continue

        if p.is_file():
            if _is_image_file(p):
                out.append(p)
            continue

        if p.is_dir():
            try:
                out.extend(list(eng.find_images(p, recursive=True)))
            except Exception:
                # fallback minimal walk
                for q in p.rglob("*"):
                    if _is_image_file(q):
                        out.append(q)
            continue

    # de-dupe preserve order
    seen = set()
    deduped: list[Path] = []
    for p in out:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        deduped.append(p)
    return deduped


def _gather_images(input_path: Path, recursive: bool) -> list[Path]:
    """
    Turn a user input (file/folder) into a concrete list of image files.
    """
    if input_path.is_file():
        return [input_path] if _is_image_file(input_path) else []
    if input_path.is_dir():
        return list(eng.find_images(input_path, recursive=recursive))
    return []


def _pre_app_setup() -> None:
    """Windows AppUserModelID (helps taskbar grouping and icon association)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes  # local import intentional
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ORG}.{APP_NAME}")
        except Exception:
            pass


# ---------------- UI helpers ----------------
def _repolish(widget: QtWidgets.QWidget) -> None:
    """Re-apply stylesheet after dynamic property changes."""
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except Exception:
        widget.update()


class CardFrame(QtWidgets.QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        if title:
            t = QtWidgets.QLabel(title)
            t.setObjectName("CardTitle")
            lay.addWidget(t)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

    def body_layout(self) -> QtWidgets.QVBoxLayout:
        return self.layout()  # type: ignore[return-value]


class DropLineEdit(QtWidgets.QLineEdit):
    pathDropped = QtCore.Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setProperty("dropActive", False)

    def _set_drag(self, on: bool) -> None:
        self.setProperty("dropActive", on)
        _repolish(self)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            self._set_drag(True)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        self._set_drag(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        self._set_drag(False)
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                self.pathDropped.emit(u.toLocalFile())
                break
            return
        super().dropEvent(e)


class NeonCTAButton(QtWidgets.QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("NeonCTA")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._fx = QtWidgets.QGraphicsDropShadowEffect(self)
        self._fx.setOffset(0, 0)
        self._fx.setBlurRadius(44)
        self._fx.setColor(QtGui.QColor(0, 220, 255, 180))
        self.setGraphicsEffect(self._fx)

        self._pulse = QtCore.QPropertyAnimation(self, b"glow")
        self._pulse.setStartValue(26)
        self._pulse.setEndValue(64)
        self._pulse.setDuration(1200)
        self._pulse.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.start()

    def getGlow(self) -> int:
        return int(self._fx.blurRadius())

    def setGlow(self, v: int) -> None:
        self._fx.setBlurRadius(v)
        self._fx.setColor(QtGui.QColor(0, 220, 255, 150 if v < 44 else 220))

    glow = QtCore.Property(int, fget=getGlow, fset=setGlow)


class SegmentedMode(QtWidgets.QWidget):
    modeChanged = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SegMode")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.btn_file = QtWidgets.QPushButton("Image")
        self.btn_folder = QtWidgets.QPushButton("Folder")

        self.btn_file.setObjectName("ModeImageBtn")
        self.btn_folder.setObjectName("ModeFolderBtn")

        for b in (self.btn_file, self.btn_folder):
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)

        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.addButton(self.btn_file)
        self.group.addButton(self.btn_folder)

        lay.addWidget(self.btn_file)
        lay.addWidget(self.btn_folder)

        self.btn_file.setChecked(True)
        self.group.buttonClicked.connect(self._emit)  # type: ignore[arg-type]

    def _emit(self) -> None:
        self.modeChanged.emit("folder" if self.btn_folder.isChecked() else "file")

    def set_mode(self, mode: str) -> None:
        m = (mode or "").strip().lower()
        if m == "folder":
            self.btn_folder.setChecked(True)
        else:
            self.btn_file.setChecked(True)
        self._emit()


class NeonRippleIconButton(QtWidgets.QPushButton):
    """
    Icon-only button:
    - icon fills the entire button space
    - animated hover glow
    - neon ripple on click
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SageIconBtn")
        self.setCursor(QtCore.Qt.PointingHandCursor)

        self.setText("")
        self.setCheckable(False)
        self.setFlat(True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAutoDefault(False)
        self.setDefault(False)

        self.setStyleSheet("padding:0px; margin:0px; border:none; background: transparent;")

        self._glow_blur = 18
        self._glow_alpha = 120

        self._ripple_active = False
        self._ripple_center = QtCore.QPointF(0, 0)
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0

        self._glow_fx = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow_fx.setOffset(0, 0)
        self._glow_fx.setBlurRadius(self._glow_blur)
        self._glow_fx.setColor(QtGui.QColor(0, 220, 255, self._glow_alpha))
        self.setGraphicsEffect(self._glow_fx)

        self._glow_anim = QtCore.QPropertyAnimation(self, b"glowBlur", self)
        self._glow_anim.setDuration(180)
        self._glow_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._alpha_anim = QtCore.QPropertyAnimation(self, b"glowAlpha", self)
        self._alpha_anim.setDuration(180)
        self._alpha_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._rip_r_anim = QtCore.QPropertyAnimation(self, b"rippleRadius", self)
        self._rip_r_anim.setDuration(420)
        self._rip_r_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._rip_o_anim = QtCore.QPropertyAnimation(self, b"rippleOpacity", self)
        self._rip_o_anim.setDuration(420)
        self._rip_o_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._rip_group = QtCore.QParallelAnimationGroup(self)
        self._rip_group.addAnimation(self._rip_r_anim)
        self._rip_group.addAnimation(self._rip_o_anim)
        self._rip_group.finished.connect(self._ripple_end)  # type: ignore[arg-type]

    def _get_glow_blur(self) -> int:
        return int(self._glow_blur)

    def _set_glow_blur(self, v: int) -> None:
        self._glow_blur = int(v)
        self._glow_fx.setBlurRadius(int(v))

    glowBlur = QtCore.Property(int, fget=_get_glow_blur, fset=_set_glow_blur)

    def _get_glow_alpha(self) -> int:
        return int(self._glow_alpha)

    def _set_glow_alpha(self, v: int) -> None:
        self._glow_alpha = int(v)
        self._glow_fx.setColor(QtGui.QColor(0, 220, 255, max(0, min(255, int(v)))))

    glowAlpha = QtCore.Property(int, fget=_get_glow_alpha, fset=_set_glow_alpha)

    def _get_ripple_radius(self) -> float:
        return float(self._ripple_radius)

    def _set_ripple_radius(self, v: float) -> None:
        self._ripple_radius = float(v)
        self.update()

    rippleRadius = QtCore.Property(float, fget=_get_ripple_radius, fset=_set_ripple_radius)

    def _get_ripple_opacity(self) -> float:
        return float(self._ripple_opacity)

    def _set_ripple_opacity(self, v: float) -> None:
        self._ripple_opacity = float(v)
        self.update()

    rippleOpacity = QtCore.Property(float, fget=_get_ripple_opacity, fset=_set_ripple_opacity)

    def set_icon_from_png(self, png_path: str) -> None:
        p = Path(png_path)
        if not p.exists():
            return
        pm = QtGui.QPixmap(str(p))
        if pm.isNull():
            return

        btn = int(SAGE_BTN_SIZE)
        self.setFixedSize(btn, btn)

        self.setIcon(QtGui.QIcon(pm))
        self.setIconSize(QtCore.QSize(btn - 20, btn - 20))

        # circular mask
        region = QtGui.QRegion(QtCore.QRect(0, 0, btn, btn), QtGui.QRegion.Ellipse)
        self.setMask(region)

    def enterEvent(self, e: QtCore.QEvent) -> None:
        self._glow_anim.stop()
        self._alpha_anim.stop()

        self._glow_anim.setStartValue(self._get_glow_blur())
        self._glow_anim.setEndValue(54)

        self._alpha_anim.setStartValue(self._get_glow_alpha())
        self._alpha_anim.setEndValue(235)

        self._glow_anim.start()
        self._alpha_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e: QtCore.QEvent) -> None:
        self._glow_anim.stop()
        self._alpha_anim.stop()

        self._glow_anim.setStartValue(self._get_glow_blur())
        self._glow_anim.setEndValue(18)

        self._alpha_anim.setStartValue(self._get_glow_alpha())
        self._alpha_anim.setEndValue(120)

        self._glow_anim.start()
        self._alpha_anim.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            self._ripple_active = True
            self._ripple_center = e.position()

            max_r = (self.width() ** 2 + self.height() ** 2) ** 0.5 * 0.75

            self._rip_group.stop()
            self._rip_r_anim.setStartValue(0.0)
            self._rip_r_anim.setEndValue(float(max_r))

            self._rip_o_anim.setStartValue(0.42)
            self._rip_o_anim.setEndValue(0.0)

            self._rip_group.start()
        super().mousePressEvent(e)

    def _ripple_end(self) -> None:
        self._ripple_active = False
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0
        self.update()

    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        super().paintEvent(e)

        if not self._ripple_active and self._ripple_opacity <= 0.001:
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        col = QtGui.QColor(0, 220, 255)
        col.setAlphaF(max(0.0, min(1.0, self._ripple_opacity)))

        p.setBrush(QtGui.QBrush(col))
        p.setPen(QtCore.Qt.NoPen)

        cx, cy = self._ripple_center.x(), self._ripple_center.y()
        r = self._ripple_radius
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        p.end()


@dataclass(slots=True)
class LogLine:
    text: str
    level: str = "INFO"  # INFO/WARN/ERR


def preset_sizes(preset: str) -> list[int]:
    preset = (preset or "").strip()
    try:
        max_size = int(preset.split("–", 1)[1])
    except Exception:
        max_size = 256

    ladder = [16, 24, 32, 48, 64, 96, 128, 256, 512, 1024]
    out = [s for s in ladder if s <= max_size]
    if 16 not in out:
        out.insert(0, 16)
    return out


def choose_library_root(parent) -> Path | None:
    p = QtWidgets.QFileDialog.getExistingDirectory(
        parent,
        "Choose IconMaker Library Location",
        QtCore.QDir.homePath(),
    )
    return Path(p) if p else None


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # ---------------- Settings ----------------
        self._settings = QtCore.QSettings(APP_ORG, APP_NAME)

        # ---------------- Library root ----------------
        root_str = str(self._settings.value("library_root", "") or "").strip()
        root = Path(root_str) if root_str else None

        if not root or not root.exists():
            chosen = choose_library_root(self)
            if not chosen:
                QtWidgets.QMessageBox.critical(self, "IconMaker", "A library folder is required to continue.")
                sys.exit(1)
            self._settings.setValue("library_root", str(chosen))
            root = chosen

        # ---------------- Persistent UI state ----------------
        # Apply canonical paths BEFORE watchers/UI
        self._set_library_paths(root)

        # --- app event state ---
        self._last_seen_event_seq = 0
        self._events_timer = QtCore.QTimer(self)
        self._events_timer.setInterval(1200)
        self._events_timer.timeout.connect(self._poll_app_events)

        self._app_icon = get_app_icon()
        self.setWindowIcon(self._app_icon)

        self.setWindowTitle("IconMaker")
        self.setMinimumSize(1180, 760)
        self.setAcceptDrops(True)

        # --- logging buffers ---
        self._log_history: list[LogLine] = []
        self._log_pending: list[LogLine] = []

        self._run_in_progress: bool = False
        self._cancel_requested = False

        # Build UI first
        self._build_ui()
        self._apply_theme()
        self._wire()

        self.state = StateMemory(APP_ORG, APP_NAME, logger=self._log)
        self.state.load_to_ui(self)
        self.state.install_auto_save(self)

        # Restore UI values
        pad = self._settings.value("last_padding", "balanced", str)
        if self.cmb_padding.findText(pad) >= 0:
            self.cmb_padding.setCurrentText(pad)

        qual = self._settings.value("last_quality", "16–1024", str)
        if self.cmb_quality.findText(qual) >= 0:
            self.cmb_quality.setCurrentText(qual)

        try:
            self.state.apply_truthful_source_ui(self)
        except Exception:
            pass

        self._restore_library_overlay_state()
        self._log("Ready.")
        self._lock_output_to_canonical()
        self._events_timer.start()
        self._poll_app_events(force=True)
        self._update_mode()

        # initialize Run enabled/disabled correctly
        self._update_run_state()

    # ---------------- core: canonical paths ----------------
    def _set_library_paths(self, library_root: Path) -> None:
        root = Path(library_root).resolve()
        self.LIBRARY_ROOT = root
        self.paths = eng.EnginePaths.from_library_root(root)

    # -------- UI build --------
    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        # ---------------- HERO ----------------
        hero = QtWidgets.QFrame()
        hero.setObjectName("Hero")

        h = QtWidgets.QHBoxLayout(hero)
        h.setContentsMargins(18, 16, 18, 16)
        h.setSpacing(12)

        # ---- IconMaker image ----
        self.mark = QtWidgets.QLabel()
        self.mark.setObjectName("AppMark")
        self.mark.setFixedSize(HERO_ICON_SIZE, HERO_ICON_SIZE)
        self.mark.setAlignment(QtCore.Qt.AlignCenter)

        pm = get_title_pixmap()
        if not pm.isNull():
            self.mark.setPixmap(
                pm.scaled(
                    HERO_ICON_SIZE,
                    HERO_ICON_SIZE,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation
                )
            )

        self.mark.setAlignment(QtCore.Qt.AlignCenter)

        # ---- Sage button ----
        self.btn_sage = NeonRippleIconButton()
        self.btn_sage.setToolTip(SAGE_URL)
        self.btn_sage.set_icon_from_png(SAGE_BUTTON_IMAGE_PATH)

        # ---- Layout ----
        h.addStretch(1)
        h.addWidget(self.mark, 0, QtCore.Qt.AlignCenter)
        h.addStretch(1)
        h.addWidget(self.btn_sage, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        outer.addWidget(hero)

        self.btn_sage.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(SAGE_URL))
        )

        # ---------------- TOP PANEL (Run/Cancel/Progress) ----------------
        top_controls = CardFrame("")
        top_controls.setObjectName("TopControlsCard")
        outer.addWidget(top_controls)

        tcl = QtWidgets.QVBoxLayout()
        tcl.setContentsMargins(0, 0, 0, 0)
        tcl.setSpacing(10)
        top_controls.body_layout().addLayout(tcl)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_run = NeonCTAButton("Run")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.setObjectName("CancelBtn")
        self.btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cancel.setEnabled(False)

        for b in (self.btn_run, self.btn_cancel):
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumWidth(110)
            b.setMaximumWidth(140)

        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        tcl.addLayout(btn_row)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)

        self.status_line = QtWidgets.QLabel("Ready.")
        self.status_line.setObjectName("StatusLine")

        tcl.addWidget(self.bar)
        tcl.addWidget(self.status_line)

        # ---------------- Body scroll ----------------
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        body = QtWidgets.QWidget()
        scroll.setWidget(body)

        main = QtWidgets.QHBoxLayout(body)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(14)

        self.main_area = QtWidgets.QWidget()
        self.main_area.setMinimumWidth(620)

        left = QtWidgets.QVBoxLayout(self.main_area)
        left.setSpacing(10)

        main.addWidget(self.main_area, 1)

        self.library_overlay = LibraryOverlayHost(scroll.viewport(), self.main_area, self)

        paths = eng.list_library_images(paths=self.paths)
        self.library_overlay.refresh(paths)

        self.library_overlay.filesDropped.connect(self._import_paths_to_library)
        # Source
        source = CardFrame("Select Image or Folder of Images:")
        left.addWidget(source)
        sg = QtWidgets.QGridLayout()
        sg.setHorizontalSpacing(10)
        sg.setVerticalSpacing(10)
        source.body_layout().addLayout(sg)

        self.mode_seg = SegmentedMode()
        sg.addWidget(self.mode_seg, 0, 0, 1, 3)

        self.edit_input = DropLineEdit()
        self.edit_input.setPlaceholderText("Drop a file/folder here… or click Browse.")
        self.btn_browse_input = QtWidgets.QPushButton("Browse…")
        self.btn_browse_input.setCursor(QtCore.Qt.PointingHandCursor)
        sg.addWidget(self.edit_input, 1, 0, 1, 2)
        sg.addWidget(self.btn_browse_input, 1, 2)

        self.lbl_drop_hint = QtWidgets.QLabel("Tip: Drag files or folders anywhere into the app to import them.")
        self.lbl_drop_hint.setObjectName("DropHint")
        self.lbl_drop_hint.setWordWrap(True)
        sg.addWidget(self.lbl_drop_hint, 2, 0, 1, 3)

        self.chk_recursive = QtWidgets.QCheckBox("Recursive (subfolders)")
        sg.addWidget(self.chk_recursive, 3, 0, 1, 2)

        # Output (fixed)
        out = CardFrame("Output")
        left.addWidget(out)

        og = QtWidgets.QGridLayout()
        og.setHorizontalSpacing(10)
        og.setVerticalSpacing(10)
        out.body_layout().addLayout(og)

        self.lbl_outdir = QtWidgets.QLabel(str(self.paths.icons_dir))
        self.lbl_outdir.setObjectName("FixedOutPath")
        self.lbl_outdir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.btn_open_my_icons = QtWidgets.QPushButton("Open My Icons")
        self.btn_open_my_icons.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_open_images = QtWidgets.QPushButton("Open Icon Images")
        self.btn_open_images.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_change_library = QtWidgets.QPushButton("Change Library Location…")
        self.btn_change_library.setCursor(QtCore.Qt.PointingHandCursor)

        og.addWidget(QtWidgets.QLabel("Icon Output"), 0, 0)
        og.addWidget(self.lbl_outdir, 0, 1, 1, 2)
        og.addWidget(self.btn_open_my_icons, 1, 0, 1, 3)
        og.addWidget(self.btn_open_images, 2, 0, 1, 3)
        og.addWidget(self.btn_change_library, 3, 0, 1, 3)

        # Options
        opt = CardFrame("Icon Quality")
        left.addWidget(opt)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        opt.body_layout().addLayout(g)

        self.cmb_quality = QtWidgets.QComboBox()
        self.cmb_quality.setCursor(QtCore.Qt.PointingHandCursor)

        presets = [
            "16–1024",
            "16–512",
            "16–256",
            "16–128",
            "16–64",
            "16–48",
            "16–32",
            "16–24",
            "16–16",
        ]
        self.cmb_quality.addItems(presets)
        self.cmb_quality.setCurrentText("16–1024")

        self.chk_overwrite = QtWidgets.QCheckBox("Overwrite Mode")
        self.chk_overwrite.setChecked(True)

        self.cmb_padding = QtWidgets.QComboBox()
        self.cmb_padding.addItems(list(eng.PADDING_PRESETS.keys()))
        self.cmb_padding.setCurrentText("balanced")

        g.addWidget(QtWidgets.QLabel("Quality Preset"), 0, 0)
        g.addWidget(self.cmb_quality, 0, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("Padding"), 1, 0)
        g.addWidget(self.cmb_padding, 1, 1)
        g.addWidget(self.chk_overwrite, 1, 2, 1, 2)

        left.addStretch(1)

    def _apply_theme(self) -> None:
        f = self.font()
        f.setFamily("Segoe UI Variable" if sys.platform.startswith("win") else "Segoe UI")
        f.setPointSize(11)
        self.setFont(f)

        self.setStyleSheet(r"""
        QMainWindow {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #060812, stop:0.45 #070B18, stop:1 #0B0620);
        }
        #Hero {
            border-radius: 18px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #081026, stop:0.55 #0B1240, stop:1 #24063D);
            border: 1px solid rgba(255,255,255,0.06);
        }
        #AppMark {background: transparent;
        }
        #HeroTitle { color: #FFFFFF; font-size: 24px; font-weight: 900; letter-spacing: 0.6px; }
        #HeroSub   { color: rgba(234,242,255,190); font-size: 13px; }

        #Card {
            border-radius: 18px;
            background-color: rgba(13, 18, 38, 0.72);
            border: 1px solid rgba(255,255,255,0.07);
        }
        #CardTitle {
            color: rgba(234,242,255,230);
            font-weight: 900;
            font-size: 13px;
            letter-spacing: 0.4px;
        }


        #DropHint {
            color: rgba(234,242,255,150);
            font-size: 11px;
            padding-top: 2px;
        }

        QLabel { color: rgba(234,242,255,220); }
        QCheckBox { color: rgba(234,242,255,220); font-weight: 800; }

        QLineEdit, QPlainTextEdit, QComboBox, QListWidget {
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.08);
            padding: 9px 11px;
            background-color: rgba(7, 10, 18, 0.62);
            color: rgba(234,242,255,230);
        }

        QListWidget::item {
            border-radius: 10px;
            margin: 4px;
            padding: 0px;
            background: rgba(0,0,0,0.0);
        }
        QListWidget::item:selected {
            background: rgba(0,220,255,0.14);
            border: 1px solid rgba(0,220,255,0.40);
        }





        QPushButton {
            border-radius: 12px;
            padding: 10px 12px;
            background-color: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.10);
            color: rgba(234,242,255,230);
            font-weight: 900;
        }
        QPushButton:hover { border-color: rgba(0,220,255,0.45); }
        QPushButton:pressed { background-color: rgba(0,220,255,0.10); }

        QPushButton:disabled {
            background-color: #2a2a2a;
            color: #666666;
            border: 1px solid #333333;
        }

        #NeonCTA {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(0,220,255,0.22),
                stop:1 rgba(123,92,255,0.20));
            border: 1px solid rgba(0,220,255,0.40);
        }

        #CancelBtn { border: 1px solid rgba(255,255,255,0.14); }

        QProgressBar {
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.10);
            text-align: center;
            background-color: rgba(0,0,0,0.25);
        }
        QProgressBar::chunk {
            border-radius: 10px;
            background-color: rgba(0,220,255,0.55);
        }

        #SageIconBtn {
            padding: 0px;
            margin: 0px;
            border: none;
            background: transparent;
        }
        #SageIconBtn:hover { border: none; background: transparent; }
        #SageIconBtn:pressed { border: none; background: transparent; }

        #ModeImageBtn, #ModeFolderBtn {
            border-radius: 12px;
            padding: 10px 12px;
            font-weight: 900;
            background-color: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.10);
            color: rgba(234,242,255,230);
        }
        #ModeImageBtn:checked {
            background-color: #EFBF04;
            border: 1px solid #EFBF04;
            color: black;
        }
        #ModeFolderBtn:checked {
            background-color: #C0C0C0;
            border: 1px solid #C0C0C0;
            color: black;
        }
        """)

    def _wire(self) -> None:
        self.btn_browse_input.clicked.connect(self._browse_input)  # type: ignore[arg-type]

        self.edit_input.pathDropped.connect(self._set_input)  # type: ignore[arg-type]

        self.btn_open_my_icons.clicked.connect(lambda: _open_path(str(self.paths.icons_dir)))
        self.btn_open_images.clicked.connect(lambda: _open_path(str(self.paths.images_dir)))
        self.btn_change_library.clicked.connect(self._change_library_location)  # type: ignore[arg-type]

        self.btn_run.clicked.connect(self._run_convert)  # type: ignore[arg-type]
        self.btn_cancel.clicked.connect(self._cancel)  # type: ignore[arg-type]

        self.edit_input.textChanged.connect(self._update_run_state)
        self.chk_recursive.toggled.connect(self._update_run_state)
        self.mode_seg.modeChanged.connect(lambda _: self._update_run_state())

        self.mode_seg.modeChanged.connect(self._update_mode)  # type: ignore[arg-type]

        # Library hooks
        self.library_overlay.itemSelected.connect(self._on_library_item_selected)
        self.library_overlay.itemActivated.connect(lambda p: self._set_input(str(p)))
        self.library_overlay.openImageRequested.connect(self._open_library_image)
        self.library_overlay.openWithRequested.connect(self._open_with_library_image)
        self.library_overlay.showInFolderRequested.connect(self._show_library_image_in_folder)
        self.library_overlay.duplicateRequested.connect(self._duplicate_library_image)
        self.library_overlay.copyImageRequested.connect(self._copy_library_image)
        self.library_overlay.copyPathRequested.connect(self._copy_library_image_path)
        self.library_overlay.copyIconRequested.connect(self._copy_library_icon)
        self.library_overlay.renameRequested.connect(self._rename_library_image)
        self.library_overlay.deleteRequested.connect(self._delete_library_image)
        self.library_overlay.openStateChanged.connect(self._save_library_overlay_state)

    def _restore_library_overlay_state(self) -> None:
        is_open = bool(self._settings.value("ui/library_overlay_open", False, type=bool))
        try:
            self.library_overlay.set_open(is_open, animate=False)
        except Exception:
            pass

    def _save_library_overlay_state(self, is_open: bool) -> None:
        try:
            self._settings.setValue("ui/library_overlay_open", bool(is_open))
        except Exception:
            pass

    # ---------------- redesign helpers ----------------
    def _lock_output_to_canonical(self) -> None:
        try:
            self.lbl_outdir.setText(str(self.paths.icons_dir))
        except Exception:
            pass

    def _update_mode(self, *_args) -> None:
        try:
            mode = "folder" if self.mode_seg.btn_folder.isChecked() else "file"
        except Exception:
            mode = "Image"

        if mode == "folder":
            self.chk_recursive.setEnabled(True)
        else:
            self.chk_recursive.setChecked(False)
            self.chk_recursive.setEnabled(False)

    def _update_run_state(self) -> None:
        inp_txt = self.edit_input.text().strip()

        if not inp_txt:
            self.btn_run.setEnabled(False)
            return

        p = Path(inp_txt)
        if not p.exists():
            self.btn_run.setEnabled(False)
            return

        recursive = self.chk_recursive.isChecked()
        images = _gather_images(p, recursive=recursive)

        if not images:
            self.btn_run.setEnabled(False)
            return

        if self._run_in_progress:
            self.btn_run.setEnabled(False)
            return

        self.btn_run.setEnabled(True)

        # ---------------- logging ----------------

    def _log(self, msg: str, level: str = "INFO") -> None:
        item = LogLine(text=msg, level=level)
        self._log_history.append(item)
        self._log_pending.append(item)

    def _clear_log(self) -> None:
        self._log_history.clear()
        self._log_pending.clear()

    def _passes_filter(self, item: LogLine, filt: str) -> bool:
        return True

    def _rebuild_log_view(self) -> None:
        return

    def _flush_log_pending(self) -> None:
        self._log_pending.clear()

    # ---------------- Import (drop) ----------------
    def _import_paths_to_library(self, paths: list) -> None:
        if not paths:
            return

        img_files = _gather_images_from_paths([Path(p) for p in paths])
        if not img_files:
            self._log("Drop import: no valid images detected.", "WARN")
            return

        imported = 0
        for img in img_files:
            try:
                dst = eng.mirror_copy_to_icon_images(
                    img,
                    paths=self.paths,
                    logfn=lambda s: self._log(s, "INFO")
                )
                if dst is not None:
                    imported += 1
            except Exception as e:
                self._log(f"ERR: Import failed: {img}: {type(e).__name__}: {e}", "ERR")

        self._log(f"Drop import finished. images_detected={len(img_files)} imported_or_existing={imported}", "INFO")

        self._run_maintenance_now('library-drop')

    # ---------------- basic actions ----------------
    def _set_input(self, p: str) -> None:
        self.edit_input.setText(p)
        self.mode_seg.set_mode("folder" if Path(p).is_dir() else "file")
        self._update_mode()
        self._update_run_state()

    def _on_library_item_selected(self, path: Path) -> None:
        self.status_line.setText(f"Selected: {Path(path).name}")

    def _open_library_image(self, path: Path) -> None:
        _open_path(str(path))

    def _open_with_library_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(p)])
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))
        except Exception as e:
            self._log(f"WARN: Open With failed: {p} ({e})", "WARN")

    def _show_library_image_in_folder(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                _open_path(str(p.parent))
        except Exception as e:
            self._log(f"WARN: Show in Folder failed: {p} ({e})", "WARN")

    def _copy_urls_to_clipboard(self, paths: list[Path]) -> None:
        mime = QtCore.QMimeData()
        existing = [Path(p) for p in paths if Path(p).exists()]
        if not existing:
            return
        urls = [QtCore.QUrl.fromLocalFile(str(p)) for p in existing]
        mime.setUrls(urls)
        mime.setText("\n".join(str(p) for p in existing))
        QtWidgets.QApplication.clipboard().setMimeData(mime)

    def _copy_library_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            self._copy_urls_to_clipboard([p])
            self._log(f"Copied image: {p.name}")
        except Exception as e:
            self._log(f"WARN: Copy Image failed: {p} ({e})", "WARN")

    def _copy_library_image_path(self, path: Path) -> None:
        p = Path(path)
        QtWidgets.QApplication.clipboard().setText(str(p))
        self._log(f"Copied path: {p}")

    def _library_icon_path_for_image(self, path: Path) -> Path:
        p = Path(path).resolve()
        try:
            rel_parent = p.relative_to(self.paths.images_dir).parent
        except Exception:
            rel_parent = Path()
        icon_name = f"{eng.sanitize_piece(p.stem)}.ico"
        return self.paths.icons_dir / rel_parent / icon_name

    def _copy_library_icon(self, path: Path) -> None:
        ico = self._library_icon_path_for_image(path)
        if not ico.exists():
            self._log(f"WARN: Icon does not exist yet: {ico.name}", "WARN")
            return
        try:
            self._copy_urls_to_clipboard([ico])
            self._log(f"Copied icon: {ico.name}")
        except Exception as e:
            self._log(f"WARN: Copy Icon failed: {ico} ({e})", "WARN")

    def _duplicate_library_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            dst = eng.unique_path(p.parent / p.name)
            shutil.copy2(p, dst)
            self._log(f"Duplicated image: {p.name} -> {dst.name}")
            self._run_maintenance_now('library-duplicate')
            self._set_input(str(dst))
        except Exception as e:
            self._log(f"ERR: Duplicate failed: {p} ({e})", "ERR")

    def _rename_library_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return

        new_stem, ok = QtWidgets.QInputDialog.getText(
            self,
            "Rename Image",
            "New image name:",
            text=p.stem,
        )
        if not ok:
            return

        new_stem = str(new_stem or "").strip()
        if not new_stem:
            return

        desired = p.with_name(f"{new_stem}{p.suffix.lower()}")
        if desired == p:
            return

        if desired.exists():
            QtWidgets.QMessageBox.warning(self, "Rename Image", f"A file already exists with that name:\n\n{desired.name}")
            return

        try:
            p.rename(desired)
            self._log(f"Renamed image: {p.name} -> {desired.name}")
            self._run_maintenance_now('library-rename')
            self._set_input(str(desired))
        except Exception as e:
            self._log(f"ERR: Rename failed: {p} ({e})", "ERR")

    def _delete_library_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            moved = False
            if hasattr(QtCore.QFile, 'moveToTrash'):
                try:
                    moved = bool(QtCore.QFile.moveToTrash(str(p)))
                except TypeError:
                    moved = bool(QtCore.QFile.moveToTrash(str(p), None))
            if not moved:
                p.unlink()
            self._log(f"Deleted image: {p.name}")
            if self.edit_input.text().strip() == str(p):
                self.edit_input.clear()
            self._run_maintenance_now('library-delete')
        except Exception as e:
            self._log(f"ERR: Delete failed: {p} ({e})", "ERR")

    def _browse_input(self) -> None:
        mode = "folder" if self.mode_seg.btn_folder.isChecked() else "Image"
        if mode == "folder":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", str(self.paths.images_dir))
            if p:
                self._set_input(p)
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose Image", str(self.paths.images_dir))
            if p:
                self._set_input(p)

    def _run_maintenance_now(self, reason: str) -> None:
        self._log(f"=== MAINTENANCE ({reason}) ===")
        self.status_line.setText(f"Maintaining library… ({reason})")
        try:
            report = GenOps.run_library_maintenance(
                paths=self.paths,
                overwrite=self.chk_overwrite.isChecked(),
                sizes=preset_sizes(self.cmb_quality.currentText()),
                padding_mode=self.cmb_padding.currentText(),
                autocrop=False,
                logfn=lambda s: self._log(s),
            )
            self._log(f"Maintenance done. scanned={report.scanned} converted={report.converted} orphans_removed={report.orphan_icons_removed}")
        except Exception as e:
            self._log(f"ERR: Maintenance failed: {e}", "ERR")
        finally:
            self._refresh_library_view()
            self.status_line.setText("Ready.")

    def _refresh_library_view(self) -> None:
        try:
            self.library_overlay.refresh(eng.list_library_images(paths=self.paths))
        except Exception as e:
            self._log(f"WARN: Library refresh failed: {e}", "WARN")

    def _poll_app_events(self, force: bool = False) -> None:
        event = GenOps.latest_app_event()
        if not force and event.seq <= self._last_seen_event_seq:
            return
        self._last_seen_event_seq = event.seq
        if force or event.event_type in {"library-changed", "library-maintained", "library-relocated", "conversion-finished"}:
            self._refresh_library_view()

    def _change_library_location(self) -> None:
        start_dir = str(getattr(self, "LIBRARY_ROOT", Path.home()))
        picked = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose NEW IconMaker Library Location", start_dir)
        if not picked:
            return
        new_root = Path(picked).resolve()
        old_root = Path(self.LIBRARY_ROOT).resolve()
        if new_root == old_root:
            self._log("Library location unchanged.", "WARN")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "IconMaker",
            f"Move library from:\n{old_root}\n\nTo:\n{new_root}\n\nThis will pause tray activity during relocation.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        dlg = QtWidgets.QProgressDialog("Relocating library…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("IconMaker")
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.show()
        def on_progress(done: int, total: int, current: str) -> None:
            pct = int((done * 100) / max(1, total))
            dlg.setValue(pct)
            dlg.setLabelText(f"Relocating…\n{current}")
            QtWidgets.QApplication.processEvents()
        result = GenOps.relocate_library(old_root, new_root, delete_source=False, progress_cb=on_progress, is_cancelled=lambda: dlg.wasCanceled())
        dlg.close()
        if not result.ok:
            self._log(f"Library relocate failed: {result.message}", "ERR")
            QtWidgets.QMessageBox.critical(self, "IconMaker", f"Relocate failed:\n{result.message}")
            return
        self._settings.setValue("library_root", str(new_root))
        self._settings.sync()
        self._set_library_paths(new_root)
        self._lock_output_to_canonical()
        self._refresh_library_view()
        self._log(f"Library relocated to: {new_root}")
        GenOps.publish_app_event("library-relocated", str(new_root))
        if QtWidgets.QMessageBox.question(
            self,
            "IconMaker",
            f"Relocation verified. Delete old library?\n\n{old_root}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        ) == QtWidgets.QMessageBox.Yes:
            try:
                shutil.rmtree(old_root)
                self._log(f"Old library deleted: {old_root}")
            except Exception as e:
                self._log(f"WARN: Could not delete old library ({e})", "WARN")

    def _cancel(self) -> None:
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)

        if self._run_proc is not None:
            try:
                if self._run_proc.state() != QtCore.QProcess.NotRunning:
                    self._run_proc.terminate()
                    QtCore.QTimer.singleShot(
                        1200,
                        lambda: self._run_proc.kill()
                        if self._run_proc is not None and self._run_proc.state() != QtCore.QProcess.NotRunning
                        else None
                    )
            except Exception:
                pass
            self._log("Cancel requested (terminating engine process).", "WARN")
            return

        self._log("Cancel requested.", "WARN")

    def _run_convert(self) -> None:
        if self._run_in_progress:
            return

        self._cancel_requested = False
        self.btn_cancel.setEnabled(True)
        self.bar.setValue(0)

        inp_txt = self.edit_input.text().strip()
        if not inp_txt:
            self._log("ERR: No input provided.", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        inp = Path(inp_txt)
        if not inp.exists():
            self._log("ERR: Input path does not exist.", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        # Lock UI
        self._run_in_progress = True
        self.btn_run.setEnabled(False)
        self.edit_input.setEnabled(False)
        self.btn_browse_input.setEnabled(False)
        self.mode_seg.setEnabled(False)
        self.chk_recursive.setEnabled(False)
        self.chk_overwrite.setEnabled(False)
        self.cmb_quality.setEnabled(False)
        self.cmb_padding.setEnabled(False)
        self.status_line.setText("Starting…")

        # Prepare args for Gen2 CLI
        sizes = preset_sizes(self.cmb_quality.currentText())
        sizes = [s for s in sizes if 1 <= s <= 1024]
        if not sizes:
            sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]

        sizes_arg = ",".join(str(s) for s in sizes)
        padding_mode = self.cmb_padding.currentText()
        recursive = self.chk_recursive.isChecked()
        overwrite = self.chk_overwrite.isChecked()

        gen2_path = str(Path(__file__).with_name("Gen2.py"))
        py = sys.executable

        args = [
            gen2_path,
            str(inp),
            "--out", str(self.paths.icons_dir),
            "--sizes", sizes_arg,
            "--padding", padding_mode,
            "--mirror",
            "--progress-json",
        ]
        if recursive:
            args.append("--recursive")
        if not overwrite:
            args.append("--no-overwrite")

        self._log("=== RUN (engine subprocess) ===", "INFO")
        self._log(f"Input: {inp}", "INFO")
        self._log(f"Output (icons): {self.paths.icons_dir}", "INFO")
        self._log(f"Sizes: {sizes}", "INFO")
        self._log(f"Padding: {padding_mode}", "INFO")
        self._log(f"Overwrite: {overwrite} | Recursive: {recursive} | Mirror: True", "INFO")

        proc = QtCore.QProcess(self)
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self._run_proc = proc
        self._run_proc_buf = ""

        def handle_output() -> None:
            if self._run_proc is None:
                return
            data = bytes(self._run_proc.readAllStandardOutput()).decode("utf-8", "replace")
            if not data:
                return
            self._run_proc_buf += data
            while "\n" in self._run_proc_buf:
                line, self._run_proc_buf = self._run_proc_buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if line.startswith("{") and line.endswith("}"):
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "progress":
                            done = int(obj.get("done", 0))
                            total = int(obj.get("total", 1))
                            status = str(obj.get("status", ""))
                            self.status_line.setText(status)
                            pct = int((done * 100) / max(1, total))
                            self.bar.setValue(max(0, min(100, pct)))
                            continue
                    except Exception:
                        pass

                lvl = "INFO"
                if line.startswith("ERR:") or line.startswith("ERROR"):
                    lvl = "ERR"
                elif line.startswith("WARN:") or line.startswith("WARNING"):
                    lvl = "WARN"
                self._log(line, lvl)

        def finish(ok: bool, final_status: str) -> None:
            try:
                if self._run_proc is not None:
                    self._run_proc.readyReadStandardOutput.disconnect()
            except Exception:
                pass
            try:
                if self._run_proc is not None:
                    self._run_proc.finished.disconnect()
            except Exception:
                pass

            self._run_proc = None
            self._run_proc_buf = ""
            self._run_in_progress = False

            self._log(final_status, "INFO" if ok else "ERR")
            self.status_line.setText(final_status)
            if ok:
                self.bar.setValue(100)
            self.btn_cancel.setEnabled(False)

            # Re-enable UI
            self.edit_input.setEnabled(True)
            self.btn_browse_input.setEnabled(True)
            self.mode_seg.setEnabled(True)
            self._update_mode()
            self.chk_overwrite.setEnabled(True)
            self.cmb_quality.setEnabled(True)
            self.cmb_padding.setEnabled(True)

            # Refresh library UI (icons may have been generated)
            paths = eng.list_library_images(paths=self.paths)
            self.library_overlay.refresh(paths)

            # Re-evaluate Run button state
            QtCore.QTimer.singleShot(0, self._update_run_state)

            # If maintenance was requested during run, do it now.
            if getattr(self, "_maint_pending_reason", None):
                reason = self._maint_pending_reason
                self._maint_pending_reason = None
                QtCore.QTimer.singleShot(0, lambda: self._maintenance_request(reason))

        def on_finished(code: int, status: QtCore.QProcess.ExitStatus) -> None:
            handle_output()

            if self._cancel_requested:
                finish(False, "Stopped by cancel.")
                return

            if status != QtCore.QProcess.NormalExit:
                finish(False, f"ERR: Engine crashed (ExitStatus={int(status)}) code={code}.")
                return

            finish(code == 0, f"Done. (engine exit code {code})")

        proc.readyReadStandardOutput.connect(handle_output)
        proc.finished.connect(on_finished)

        proc.start(py, args)
        if not proc.waitForStarted(2000):
            finish(False, "ERR: Failed to start engine subprocess.")

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, lambda: self.library_overlay.sync_to_parent(force=True))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, lambda: self.library_overlay.sync_to_parent(force=True))

    # ---------------- Window-level drag/drop ----------------
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        if e.mimeData().hasUrls():
            paths: list[Path] = []
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if p:
                    paths.append(Path(p))
            if paths:
                self._import_paths_to_library(paths)
                e.acceptProposedAction()
                return

        super().dropEvent(e)

    # ---------------- shutdown ----------------
    def closeEvent(self, event):
        proc = getattr(self, "_run_proc", None)
        if proc is not None:
            try:
                proc.finished.disconnect()
            except Exception:
                pass
            try:
                proc.readyReadStandardOutput.disconnect()
            except Exception:
                pass
            try:
                proc.readyReadStandardError.disconnect()
            except Exception:
                pass
            self._run_proc = None

        try:
            self.state.save_from_ui(self)
        except Exception:
            pass
        try:
            self._settings.setValue("last_quality", self.cmb_quality.currentText())
            self._settings.setValue("last_padding", self.cmb_padding.currentText())
            self._settings.sync()
        except Exception:
            pass

        super().closeEvent(event)


def main() -> None:
    _pre_app_setup()

    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(get_app_icon())

    w = MainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
