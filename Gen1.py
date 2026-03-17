#!/usr/bin/env python3
r"""
Gen1.py — IconMaker Main UI (Dark Neon)

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
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if p:
                    self.pathDropped.emit(p)
                    e.acceptProposedAction()
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

        self._log("Ready.")
        self._lock_output_to_canonical()
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
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self.view_stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.view_stack, 1)

        # ================= Main page =================
        self.page_main = QtWidgets.QWidget()
        self.view_stack.addWidget(self.page_main)
        main_outer = QtWidgets.QVBoxLayout(self.page_main)
        main_outer.setContentsMargins(0, 0, 0, 0)
        main_outer.setSpacing(10)

        top_bar = QtWidgets.QFrame()
        top_bar.setObjectName('Hero')
        top_row = QtWidgets.QHBoxLayout(top_bar)
        top_row.setContentsMargins(12, 10, 12, 10)
        top_row.setSpacing(10)

        self.mark = QtWidgets.QLabel('IconMaker')
        self.mark.setObjectName('HeroTitle')
        self.mark.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        self.btn_settings = QtWidgets.QPushButton('Settings')
        self.btn_settings.setCursor(QtCore.Qt.PointingHandCursor)
        top_row.addWidget(self.mark)
        top_row.addStretch(1)
        top_row.addWidget(self.btn_settings)
        main_outer.addWidget(top_bar)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        main_outer.addWidget(body, 1)

        self.main_area = QtWidgets.QWidget()
        self.main_area.setMinimumWidth(560)
        left = QtWidgets.QVBoxLayout(self.main_area)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        body_layout.addWidget(self.main_area, 1)


        source = CardFrame('')
        self.source_card = source
        left.addWidget(source)
        sg = QtWidgets.QGridLayout()
        sg.setHorizontalSpacing(8)
        sg.setVerticalSpacing(8)
        source.body_layout().addLayout(sg)

        self.mode_seg = SegmentedMode()
        sg.addWidget(self.mode_seg, 0, 0, 1, 3)

        self.edit_input = DropLineEdit()
        self.edit_input.setPlaceholderText('Select Image…')
        self.btn_browse_input = QtWidgets.QPushButton('Select Image…')
        self.btn_browse_input.setCursor(QtCore.Qt.PointingHandCursor)
        sg.addWidget(self.edit_input, 1, 0, 1, 2)
        sg.addWidget(self.btn_browse_input, 1, 2)

        self.chk_recursive = QtWidgets.QCheckBox('Recursive')
        sg.addWidget(self.chk_recursive, 2, 0, 1, 2)

        sage_card = CardFrame('')
        left.addWidget(sage_card)
        sage_layout = QtWidgets.QVBoxLayout()
        sage_layout.setContentsMargins(0, 0, 0, 0)
        sage_layout.setSpacing(10)
        sage_card.body_layout().addLayout(sage_layout)

        self.btn_sage = NeonRippleIconButton()
        self.btn_sage.setToolTip(SAGE_URL)
        self.btn_sage.set_icon_from_png(SAGE_BUTTON_IMAGE_PATH)
        sage_layout.addWidget(self.btn_sage, 0, QtCore.Qt.AlignCenter)
        self.btn_sage.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(SAGE_URL)))

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_run = NeonCTAButton('Run')
        self.btn_cancel = QtWidgets.QPushButton('Cancel')
        self.btn_cancel.setObjectName('CancelBtn')
        self.btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cancel.setEnabled(False)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        sage_layout.addLayout(btn_row)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status_line = QtWidgets.QLabel('Ready.')
        self.status_line.setObjectName('StatusLine')
        sage_layout.addWidget(self.bar)
        sage_layout.addWidget(self.status_line)
        left.addStretch(1)

        # ================= Settings page =================
        self.page_settings = QtWidgets.QWidget()
        self.view_stack.addWidget(self.page_settings)
        settings_outer = QtWidgets.QVBoxLayout(self.page_settings)
        settings_outer.setContentsMargins(0, 0, 0, 0)
        settings_outer.setSpacing(10)

        settings_header = QtWidgets.QFrame()
        settings_header.setObjectName('Hero')
        sh = QtWidgets.QHBoxLayout(settings_header)
        sh.setContentsMargins(12, 10, 12, 10)
        sh.setSpacing(10)
        settings_title = QtWidgets.QLabel('Settings')
        settings_title.setObjectName('HeroTitle')
        self.btn_back_settings = QtWidgets.QPushButton('Back')
        self.btn_back_settings.setCursor(QtCore.Qt.PointingHandCursor)
        sh.addWidget(settings_title)
        sh.addStretch(1)
        sh.addWidget(self.btn_back_settings)
        settings_outer.addWidget(settings_header)

        settings_body = QtWidgets.QWidget()
        sb = QtWidgets.QVBoxLayout(settings_body)
        sb.setContentsMargins(0,0,0,0)
        sb.setSpacing(10)
        settings_outer.addWidget(settings_body,1)

        upper = QtWidgets.QWidget()
        upper_layout = QtWidgets.QHBoxLayout(upper)
        upper_layout.setContentsMargins(0,0,0,0)
        upper_layout.setSpacing(10)
        sb.addWidget(upper,1)

        nav = CardFrame('')
        nav.setMinimumWidth(120)
        nav.setMaximumWidth(140)
        nav_layout = QtWidgets.QVBoxLayout()
        nav_layout.setContentsMargins(0,0,0,0)
        nav_layout.setSpacing(8)
        nav.body_layout().addLayout(nav_layout)
        self.btn_settings_image = QtWidgets.QPushButton('Image')
        self.btn_settings_image.setCursor(QtCore.Qt.PointingHandCursor)
        nav_layout.addWidget(self.btn_settings_image)
        nav_layout.addStretch(1)
        upper_layout.addWidget(nav,0)

        self.settings_stack = QtWidgets.QStackedWidget()
        upper_layout.addWidget(self.settings_stack,1)

        # Library settings page
        lib_page = QtWidgets.QWidget()
        lib_layout = QtWidgets.QVBoxLayout(lib_page)
        lib_layout.setContentsMargins(0,0,0,0)
        lib_layout.setSpacing(10)
        self.settings_stack.addWidget(lib_page)

        out = CardFrame('Library')
        lib_layout.addWidget(out)
        og = QtWidgets.QGridLayout()
        og.setHorizontalSpacing(10)
        og.setVerticalSpacing(10)
        out.body_layout().addLayout(og)

        self.lbl_outdir = QtWidgets.QLabel(str(self.paths.icons_dir))
        self.lbl_outdir.setObjectName('FixedOutPath')
        self.lbl_outdir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.btn_open_my_icons = QtWidgets.QPushButton('Open My Icons')
        self.btn_open_my_icons.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_open_images = QtWidgets.QPushButton('Open Icon Images')
        self.btn_open_images.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_change_library = QtWidgets.QPushButton('Change Library Location')
        self.btn_change_library.setCursor(QtCore.Qt.PointingHandCursor)
        og.addWidget(QtWidgets.QLabel('Icon Output'), 0, 0)
        og.addWidget(self.lbl_outdir, 0, 1)
        og.addWidget(self.btn_open_my_icons, 1, 0, 1, 2)
        og.addWidget(self.btn_open_images, 2, 0, 1, 2)
        og.addWidget(self.btn_change_library, 3, 0, 1, 2)
        lib_layout.addStretch(1)

        # Image settings page
        img_page = QtWidgets.QWidget()
        img_layout = QtWidgets.QVBoxLayout(img_page)
        img_layout.setContentsMargins(0,0,0,0)
        img_layout.setSpacing(10)
        self.settings_stack.addWidget(img_page)

        opt = CardFrame('Image')
        img_layout.addWidget(opt)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        opt.body_layout().addLayout(g)

        self.cmb_quality = QtWidgets.QComboBox()
        self.cmb_quality.setCursor(QtCore.Qt.PointingHandCursor)
        presets = ['16–1024','16–512','16–256','16–128','16–64','16–48','16–32','16–24','16–16']
        self.cmb_quality.addItems(presets)
        self.cmb_quality.setCurrentText('16–1024')
        self.chk_overwrite = QtWidgets.QCheckBox('Overwrite Mode')
        self.chk_overwrite.setChecked(True)
        self.cmb_padding = QtWidgets.QComboBox()
        self.cmb_padding.addItems(list(eng.PADDING_PRESETS.keys()))
        self.cmb_padding.setCurrentText('balanced')
        g.addWidget(QtWidgets.QLabel('Quality Preset'), 0, 0)
        g.addWidget(self.cmb_quality, 0, 1)
        g.addWidget(QtWidgets.QLabel('Padding'), 1, 0)
        g.addWidget(self.cmb_padding, 1, 1)
        g.addWidget(self.chk_overwrite, 2, 0, 1, 2)
        img_layout.addStretch(1)

        help_card = CardFrame('Help')
        help_layout = QtWidgets.QVBoxLayout()
        help_layout.setContentsMargins(0,0,0,0)
        help_layout.setSpacing(6)
        help_card.body_layout().addLayout(help_layout)
        help_text = QtWidgets.QLabel('Choose Image or Folder, then run. Recursive appears only for folders.')
        help_text.setWordWrap(True)
        help_layout.addWidget(help_text)
        sb.addWidget(help_card, 0)

        self.btn_settings.clicked.connect(lambda: self.view_stack.setCurrentWidget(self.page_settings))
        self.btn_back_settings.clicked.connect(lambda: self.view_stack.setCurrentWidget(self.page_main))
        self.btn_settings_image.clicked.connect(lambda: self.settings_stack.setCurrentIndex(0))
        self.settings_stack.setCurrentIndex(0)
        self.view_stack.setCurrentWidget(self.page_main)


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
            mode = "file"

        if mode == "folder":
            self.chk_recursive.setVisible(True)
            self.chk_recursive.setEnabled(True)
            self.btn_browse_input.setText('Select Folder…')
            self.edit_input.setPlaceholderText('Select Folder…')
        else:
            self.chk_recursive.setChecked(False)
            self.chk_recursive.setVisible(False)
            self.chk_recursive.setEnabled(False)
            self.btn_browse_input.setText('Select Image…')
            self.edit_input.setPlaceholderText('Select Image…')

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

    def _set_input(self, value: str) -> None:
        text = str(value or '').strip()
        self.edit_input.setText(text)
        self.edit_input.setCursorPosition(len(text))
        self._update_run_state()

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
            self.status_line.setText("Ready.")

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
        self._log("Cancel requested.", "WARN")

    def _set_run_ui_enabled(self, enabled: bool) -> None:
        self.btn_run.setEnabled(enabled and bool(self.edit_input.text().strip()))
        self.edit_input.setEnabled(enabled)
        self.btn_browse_input.setEnabled(enabled)
        self.mode_seg.setEnabled(enabled)
        self.chk_recursive.setEnabled(enabled)
        self.chk_overwrite.setEnabled(enabled)
        self.cmb_quality.setEnabled(enabled)
        self.cmb_padding.setEnabled(enabled)
        self.btn_cancel.setEnabled(not enabled)

    def _run_convert(self) -> None:
        if self._run_in_progress:
            return
        inp_txt = self.edit_input.text().strip()
        if not inp_txt:
            self._log("ERR: No input provided.", "ERR")
            return
        inp = Path(inp_txt)
        if not inp.exists():
            self._log("ERR: Input path does not exist.", "ERR")
            return
        self._cancel_requested = False
        self._run_in_progress = True
        self.bar.setValue(0)
        self.status_line.setText("Starting…")
        self._set_run_ui_enabled(False)
        sizes = [s for s in preset_sizes(self.cmb_quality.currentText()) if 1 <= s <= 1024] or [16, 24, 32, 48, 64, 128, 256, 512, 1024]
        padding_mode = self.cmb_padding.currentText()
        recursive = self.chk_recursive.isChecked()
        overwrite = self.chk_overwrite.isChecked()
        self._log("=== RUN ===", "INFO")
        self._log(f"Input: {inp}", "INFO")
        self._log(f"Output (icons): {self.paths.icons_dir}", "INFO")
        self._log(f"Sizes: {sizes}", "INFO")
        self._log(f"Padding: {padding_mode}", "INFO")
        self._log(f"Overwrite: {overwrite} | Recursive: {recursive} | Mirror: True", "INFO")
        request = GenOps.ConversionRequest(
            input_path=inp,
            paths=self.paths,
            sizes=sizes,
            padding_mode=padding_mode,
            recursive=recursive,
            overwrite=overwrite,
            keep_alpha=True,
            autocrop=False,
            mirror=True,
        )
        def progress(done: int, total: int, current: str) -> None:
            self.status_line.setText(current)
            self.bar.setValue(int((done * 100) / max(1, total)))
            QtWidgets.QApplication.processEvents()
        result = GenOps.run_conversion(request, progress_cb=progress, logfn=lambda s: self._log(s), is_cancelled=lambda: self._cancel_requested)
        self._run_in_progress = False
        self._set_run_ui_enabled(True)
        self.status_line.setText(result.message)
        if result.ok:
            self.bar.setValue(100)
        QtCore.QTimer.singleShot(0, self._update_run_state)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            self.edit_input._set_drag(True)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        self.edit_input._set_drag(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        self.edit_input._set_drag(False)
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                self._set_input(u.toLocalFile())
                break
            return
        super().dropEvent(e)

def main() -> None:
    _pre_app_setup()

    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(get_app_icon())

    w = MainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
