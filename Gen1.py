#!/usr/bin/env python3
r"""
Main application window and user-facing workflow for IconMaker.

Gen1 owns the primary working surface: source intake, run controls, archive
integration, settings, status updates, and the current platform-specific
window chrome. It coordinates the engine and archive subsystems without
duplicating their storage or background-processing rules.
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable

from StateMemory import StateMemory

from PySide6 import QtCore, QtGui, QtWidgets


import Gen2 as eng
import GenOps
from AppIdentity import (
    APP_DISPLAY_VERSION,
    APP_NAME,
    APP_ORG,
    APP_USER_MODEL_ID,
    APP_VERSION,
    apply_qt_application_identity,
)
from GenArchive import ArchiveSidebar, ArchiveEntrySnapshot, THUMB_SIZE

from Gen4 import get_app_icon

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
SAGE_URL = "https://chatgpt.com/g/g-68e8c5f35ff0819195a81c501942a072-sage-of-iconer"

APP_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_DIR / "assets"

# These assets define the branded title artwork and the prominent Sage shortcut.
SAGE_BUTTON_IMAGE_PATH = ASSETS_DIR / "IcoSage.png"
APP_BRANDING_IMAGE_PATH = ASSETS_DIR / "Iconner.png"

# The Sage button stays intentionally prominent because it is a primary shortcut.
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
    if IS_WINDOWS:
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))


def _reveal_in_file_manager(path: Path) -> None:
    """Reveal a file in the platform file manager when possible."""
    p = Path(path)
    if not p.exists():
        return
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", str(p)])
            return
        if IS_MAC:
            subprocess.Popen(["open", "-R", str(p)])
            return
    except Exception:
        pass
    _open_path(str(p.parent))


def _is_image_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}


def _build_archive_import_jobs(paths: Iterable[Path]) -> list[tuple[Path, Path | None]]:
    jobs: list[tuple[Path, Path | None]] = []
    seen: set[str] = set()
    for p in paths:
        try:
            p = Path(p)
        except Exception:
            continue

        if not p.exists():
            continue

        if p.is_file():
            if _is_image_file(p):
                try:
                    key = str(p.resolve())
                except Exception:
                    key = str(p)
                if key not in seen:
                    seen.add(key)
                    jobs.append((p, None))
            continue

        if p.is_dir():
            try:
                images = list(eng.find_images(p, recursive=True))
            except Exception:
                images = [q for q in p.rglob("*") if _is_image_file(q)]

            for image in images:
                try:
                    key = str(Path(image).resolve())
                except Exception:
                    key = str(image)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append((Path(image), p))

    return jobs


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
    if IS_WINDOWS:
        try:
            import ctypes  # local import intentional
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


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
        self.title_label: QtWidgets.QLabel | None = None

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        if title:
            t = QtWidgets.QLabel(title)
            t.setObjectName("CardTitle")
            lay.addWidget(t)
            self.title_label = t

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

    def body_layout(self) -> QtWidgets.QVBoxLayout:
        return self.layout()  # type: ignore[return-value]


class ScaledAssetLabel(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QtGui.QPixmap()
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

    def set_asset_path(self, path: Path) -> None:
        pixmap = QtGui.QPixmap(str(Path(path)))
        if pixmap.isNull():
            self._source = QtGui.QPixmap()
            self.setPixmap(QtGui.QPixmap())
            self.setText(APP_NAME)
            return
        self._source = pixmap
        self.setText("")
        self._refresh_pixmap()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._refresh_pixmap()
        super().resizeEvent(event)

    def _refresh_pixmap(self) -> None:
        if self._source.isNull():
            return
        target = self.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        self.setPixmap(
            self._source.scaled(
                target,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )


class CustomTitleBar(QtWidgets.QFrame):
    def __init__(self, parent=None, *, native_window_controls: bool = False):
        super().__init__(parent)
        self.setObjectName("AppTitleBar")
        self._native_window_controls = native_window_controls
        self.setProperty("nativeChrome", native_window_controls)
        self.setFixedHeight(60 if native_window_controls else 88)
        self._drag_offset: QtCore.QPoint | None = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(10)

        self.brand_label = QtWidgets.QLabel()
        self.brand_label.setObjectName("TitleBarBrand")
        self.brand_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        brand_size = 48 if native_window_controls else 78
        self.brand_label.setFixedSize(brand_size, brand_size)
        self.brand_label.setAlignment(QtCore.Qt.AlignCenter)
        brand_pixmap = QtGui.QPixmap(str(APP_BRANDING_IMAGE_PATH))
        if not brand_pixmap.isNull():
            self.brand_label.setPixmap(
                brand_pixmap.scaled(
                    self.brand_label.size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
            )
        self.brand_label.setToolTip(APP_DISPLAY_VERSION)
        layout.addWidget(self.brand_label, 0, QtCore.Qt.AlignVCenter)
        layout.addStretch(1)

        self.btn_nav = QtWidgets.QPushButton("Settings")
        self.btn_nav.setObjectName("TitleBarNavButton")
        self.btn_nav.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_nav.setFixedHeight(30 if native_window_controls else 32)
        layout.addWidget(self.btn_nav, 0, QtCore.Qt.AlignVCenter)

        self.btn_min: QtWidgets.QToolButton | None = None
        self.btn_max: QtWidgets.QToolButton | None = None
        self.btn_close: QtWidgets.QToolButton | None = None
        if not native_window_controls:
            self.btn_min = QtWidgets.QToolButton()
            self.btn_min.setObjectName("WindowControl")
            self.btn_min.setText("—")
            self.btn_min.setToolTip("Minimize")
            self.btn_min.clicked.connect(lambda: self.window().showMinimized())

            self.btn_max = QtWidgets.QToolButton()
            self.btn_max.setObjectName("WindowControl")
            self.btn_max.clicked.connect(self._toggle_max_restore)

            self.btn_close = QtWidgets.QToolButton()
            self.btn_close.setObjectName("WindowCloseControl")
            self.btn_close.setText("×")
            self.btn_close.setToolTip("Close")
            self.btn_close.clicked.connect(lambda: self.window().close())

            for btn in (self.btn_min, self.btn_max, self.btn_close):
                btn.setCursor(QtCore.Qt.PointingHandCursor)
                btn.setAutoRaise(True)
                btn.setFixedSize(36, 32)
                layout.addWidget(btn, 0, QtCore.Qt.AlignVCenter)

        self.sync_state()

    def sync_state(self) -> None:
        if self.btn_max is None:
            return
        win = self.window()
        maximized = bool(win and win.isMaximized())
        self.btn_max.setText("□" if not maximized else "❐")
        self.btn_max.setToolTip("Maximize" if not maximized else "Restore")

    def set_nav_text(self, text: str) -> None:
        self.btn_nav.setText(text)

    def _toggle_max_restore(self) -> None:
        if self.btn_max is None:
            return
        win = self.window()
        if win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()
        self.sync_state()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._native_window_controls:
            super().mousePressEvent(event)
            return
        if event.button() == QtCore.Qt.LeftButton:
            win = self.window()
            handle = win.windowHandle()
            if handle is not None:
                try:
                    handle.startSystemMove()
                    event.accept()
                    return
                except Exception:
                    pass
            if not win.isMaximized():
                self._drag_offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._native_window_controls:
            super().mouseMoveEvent(event)
            return
        if self._drag_offset is not None and event.buttons() & QtCore.Qt.LeftButton:
            win = self.window()
            if not win.isMaximized():
                win.move(event.globalPosition().toPoint() - self._drag_offset)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._native_window_controls:
            super().mouseDoubleClickEvent(event)
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._toggle_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


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

    def mode(self) -> str:
        return "folder" if self.btn_folder.isChecked() else "file"

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
        normalized = preset.replace("–", "-").replace("â€“", "-")
        max_size = int(normalized.split("-", 1)[1])
    except Exception:
        max_size = 256

    ladder = [16, 24, 32, 48, 64, 96, 128, 256, 512, 1024]
    out = [s for s in ladder if s <= max_size]
    if 16 not in out:
        out.insert(0, 16)
    return out


def choose_archive_storage_root(parent) -> Path | None:
    p = QtWidgets.QFileDialog.getExistingDirectory(
        parent,
        "Choose Archive Storage Location",
        QtCore.QDir.homePath(),
    )
    return Path(p) if p else None


class ArchiveRefreshWorker(QtCore.QObject):
    """
    Build archive snapshots away from the UI thread.

    Snapshot work reads filesystem metadata, dimensions, and thumbnails for the
    managed archive so refreshes can stay responsive during watcher updates and
    large archive scans.
    """
    finished = QtCore.Signal(int, object)
    failed = QtCore.Signal(int, str)

    def __init__(
        self,
        generation: int,
        paths: eng.EnginePaths,
        known_signatures: dict[str, tuple[int, float]],
    ) -> None:
        super().__init__()
        self.generation = generation
        self.paths = paths
        self.known_signatures = dict(known_signatures)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            snapshots: list[ArchiveEntrySnapshot] = []
            for path in sorted(eng.list_archive_source_images(paths=self.paths), key=lambda item: str(item).casefold()):
                thread = self.thread()
                if thread is not None and thread.isInterruptionRequested():
                    return
                try:
                    snapshots.append(self._build_snapshot(path))
                except FileNotFoundError:
                    continue
            thread = self.thread()
            if thread is not None and thread.isInterruptionRequested():
                return
            self.finished.emit(self.generation, snapshots)
        except Exception as exc:
            self.failed.emit(self.generation, f"{type(exc).__name__}: {exc}")

    def _build_snapshot(self, path: Path) -> ArchiveEntrySnapshot:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        signature = (stat.st_size, stat.st_mtime)
        key = str(resolved)
        changed = self.known_signatures.get(key) != signature

        snapshot = ArchiveEntrySnapshot(
            key=key,
            path=resolved,
            name=resolved.stem,
            signature_size=signature[0],
            signature_mtime=signature[1],
            changed=changed,
        )
        if not changed:
            return snapshot

        snapshot.image_size_text = self._image_size_text(resolved)
        snapshot.file_type_text = self._file_type_text(resolved)
        snapshot.file_size_text = self._format_bytes(stat.st_size)
        snapshot.image_path_text = str(resolved)
        snapshot.icon_path_text = str(eng.archive_icon_path_for_source_image(resolved, paths=self.paths))
        snapshot.created_text = self._format_timestamp(getattr(stat, "st_ctime", None))
        snapshot.modified_text = self._format_timestamp(getattr(stat, "st_mtime", None))
        snapshot.thumb_image = self._build_thumb(resolved)
        return snapshot

    def _build_thumb(self, path: Path) -> QtGui.QImage | None:
        reader = QtGui.QImageReader(str(path))
        image = reader.read()
        if image.isNull():
            image = QtGui.QImage(str(path))
        if image.isNull():
            return None
        return image.scaled(
            THUMB_SIZE,
            THUMB_SIZE,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )

    def _image_size_text(self, path: Path) -> str:
        reader = QtGui.QImageReader(str(path))
        size = reader.size()
        if size.isValid():
            return f"{size.width()} x {size.height()} px"
        return "Unavailable"

    def _file_type_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return f"{suffix[1:].upper()} image" if suffix else "Image file"

    def _format_timestamp(self, timestamp: float | None) -> str:
        if not timestamp:
            return "Unavailable"
        try:
            from datetime import datetime

            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "Unavailable"

    def _format_bytes(self, size: int | None) -> str:
        if size is None or size < 0:
            return "Unavailable"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{int(size)} B"


class MainWindow(QtWidgets.QMainWindow):
    """
    Primary application window for IconMaker.

    The main window owns the user workflow, while Gen2/GenOps own storage and
    conversion behavior, GenArchive owns archive presentation, and Gen3 owns
    unattended background scanning through the tray worker.
    """
    def __init__(self):
        super().__init__()
        self._use_native_window_controls = IS_MAC
        if self._use_native_window_controls:
            self.setWindowFlags(QtCore.Qt.Window)
        else:
            self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        self._settings = QtCore.QSettings(APP_ORG, APP_NAME)

        # The UI keeps a lightweight in-memory log so status labels and dialogs
        # can reflect recent work without depending on the persisted log files.
        self._log_history: list[LogLine] = []
        self._log_pending: list[LogLine] = []

        self._run_in_progress: bool = False
        self._cancel_requested = False

        root = GenOps.load_archive_storage_root(self._settings)

        if not root or not root.exists() or not root.is_dir():
            chosen = choose_archive_storage_root(self)
            if not chosen:
                QtWidgets.QMessageBox.critical(
                    self,
                    "IconMaker",
                    "An Archive Storage location is required to continue.",
                )
                sys.exit(1)
            GenOps.save_archive_storage_root(chosen, self._settings)
            root = chosen

        # Archive storage paths are resolved before widgets and watchers are
        # built so every surface points at the same managed storage root.
        self._set_archive_storage_paths(root)

        # App events keep the UI and tray worker synchronized without relying
        # on marker files or direct process-to-process hooks.
        self._last_seen_event_seq = 0
        self._events_timer = QtCore.QTimer(self)
        self._events_timer.setInterval(1200)
        self._events_timer.timeout.connect(self._poll_app_events)
        self._archive_fs_watcher = QtCore.QFileSystemWatcher(self)
        self._archive_fs_watcher.directoryChanged.connect(self._on_archive_storage_fs_changed)
        self._archive_refresh_timer = QtCore.QTimer(self)
        self._archive_refresh_timer.setSingleShot(True)
        self._archive_refresh_timer.setInterval(300)
        self._archive_refresh_timer.timeout.connect(self._begin_archive_refresh)
        self._archive_refresh_running = False
        self._archive_refresh_pending = False
        self._archive_refresh_generation = 0
        self._archive_refresh_thread: QtCore.QThread | None = None
        self._archive_refresh_worker: ArchiveRefreshWorker | None = None

        self._app_icon = get_app_icon()
        self.setWindowIcon(self._app_icon)

        self.setWindowTitle(APP_DISPLAY_VERSION)
        self.setMinimumSize(1180, 760)
        self.setAcceptDrops(True)

        self._build_ui()
        self._apply_theme()
        self._wire()

        self.state = StateMemory(APP_ORG, APP_NAME, logger=self._log)
        self.state.load_to_ui(self)
        self.state.install_auto_save(self)

        pad = self._settings.value("last_padding", "balanced", str)
        if self.cmb_padding.findText(pad) >= 0:
            self.cmb_padding.setCurrentText(pad)

        qual = self._settings.value("last_quality", "16-1024", str)
        if self.cmb_quality.findText(qual) >= 0:
            self.cmb_quality.setCurrentText(qual)

        try:
            self.state.apply_truthful_source_ui(self)
        except Exception:
            pass

        self._log("Ready.")
        self._lock_output_to_canonical()
        self._rebuild_archive_watch_paths()
        self._refresh_archive_view(immediate=True)
        self._events_timer.start()
        self._poll_app_events(force=True)
        self._update_mode()

        self._update_run_state()
        self.title_bar.sync_state()

    def _set_archive_storage_paths(self, archive_storage_root: Path) -> None:
        root = Path(archive_storage_root).resolve()
        self.archive_storage_root = root
        self.paths = eng.EnginePaths.from_archive_storage_root(root)

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        self.title_bar = CustomTitleBar(self, native_window_controls=self._use_native_window_controls)
        outer.addWidget(self.title_bar)

        self.view_stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.view_stack, 1)

        footer_row = QtWidgets.QHBoxLayout()
        footer_row.setContentsMargins(4, 0, 4, 0)
        footer_row.setSpacing(0)
        self.version_watermark = QtWidgets.QLabel(f"v{APP_VERSION}")
        self.version_watermark.setObjectName("VersionWatermark")
        self.version_watermark.setToolTip(APP_DISPLAY_VERSION)
        footer_row.addWidget(self.version_watermark, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        footer_row.addStretch(1)
        outer.addLayout(footer_row)

        self.page_main = QtWidgets.QWidget()
        self.view_stack.addWidget(self.page_main)
        main_outer = QtWidgets.QVBoxLayout(self.page_main)
        main_outer.setContentsMargins(0, 0, 0, 0)
        main_outer.setSpacing(10)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        main_outer.addWidget(body, 1)

        self.main_area = QtWidgets.QWidget()
        self.main_area.setMinimumWidth(720)
        left = QtWidgets.QVBoxLayout(self.main_area)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        body_layout.addWidget(self.main_area, 1)

        self.archive_sidebar = ArchiveSidebar(self)
        body_layout.addWidget(self.archive_sidebar)
        self.archive_sidebar.filesDropped.connect(self._import_paths_to_archive_storage)

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
        self.edit_input.setPlaceholderText("Select Image...")
        self.btn_browse_input = QtWidgets.QPushButton("Select Image...")
        self.btn_browse_input.setCursor(QtCore.Qt.PointingHandCursor)
        sg.addWidget(self.edit_input, 1, 0, 1, 2)
        sg.addWidget(self.btn_browse_input, 1, 2)

        self.chk_recursive = QtWidgets.QCheckBox('Recursive')
        sg.addWidget(self.chk_recursive, 2, 0, 1, 2)

        self.quick_action_row = QtWidgets.QHBoxLayout()
        self.quick_action_row.setContentsMargins(0, 0, 0, 0)
        self.quick_action_row.setSpacing(8)
        self.btn_open_current_source_image = QtWidgets.QPushButton("Open Source Image")
        self.btn_open_current_source_image.setObjectName("QuickActionButton")
        self.btn_open_current_generated_icon = QtWidgets.QPushButton("Open Generated Icon")
        self.btn_open_current_generated_icon.setObjectName("QuickActionButton")
        for btn in (
            self.btn_open_current_source_image,
            self.btn_open_current_generated_icon,
        ):
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            self.quick_action_row.addWidget(btn)
        self.quick_action_row.addStretch(1)
        left.addLayout(self.quick_action_row)

        sage_card = CardFrame('')
        sage_card.setProperty("sageCard", True)
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
        sh.addWidget(settings_title)
        sh.addStretch(1)
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
        nav.setMinimumWidth(170)
        nav.setMaximumWidth(176)
        nav_layout = QtWidgets.QVBoxLayout()
        nav_layout.setContentsMargins(0,0,0,0)
        nav_layout.setSpacing(8)
        nav.body_layout().addLayout(nav_layout)
        self.btn_settings_archive_storage = QtWidgets.QPushButton("Archive Storage")
        self.btn_settings_image = QtWidgets.QPushButton("Image")
        for b in (self.btn_settings_archive_storage, self.btn_settings_image):
            b.setObjectName("SettingsNavButton")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setMinimumHeight(38)
            b.setMinimumWidth(136)
            nav_layout.addWidget(b)
        nav_layout.addStretch(1)
        upper_layout.addWidget(nav,0)

        self.settings_stack = QtWidgets.QStackedWidget()
        upper_layout.addWidget(self.settings_stack,1)

        archive_page = QtWidgets.QWidget()
        archive_layout = QtWidgets.QVBoxLayout(archive_page)
        archive_layout.setContentsMargins(0,0,0,0)
        archive_layout.setSpacing(10)
        self.settings_stack.addWidget(archive_page)

        archive_card = CardFrame("Archive Storage")
        if archive_card.title_label is not None:
            archive_card.title_label.setObjectName("SettingsSectionTitle")
        archive_layout.addWidget(archive_card)
        archive_grid = QtWidgets.QGridLayout()
        archive_grid.setHorizontalSpacing(10)
        archive_grid.setVerticalSpacing(10)
        archive_card.body_layout().addLayout(archive_grid)

        self.lbl_archive_storage_root = QtWidgets.QLabel(str(self.paths.storage_root))
        self.lbl_archive_storage_root.setObjectName("FixedOutPath")
        self.lbl_archive_storage_root.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.lbl_source_images_dir = QtWidgets.QLabel(str(self.paths.images_dir))
        self.lbl_source_images_dir.setObjectName("FixedOutPath")
        self.lbl_source_images_dir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.lbl_generated_icons_dir = QtWidgets.QLabel(str(self.paths.icons_dir))
        self.lbl_generated_icons_dir.setObjectName("FixedOutPath")
        self.lbl_generated_icons_dir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.btn_open_archive_storage_root = QtWidgets.QPushButton("Open Archive Storage Root")
        self.btn_open_archive_storage_root.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_change_archive_storage = QtWidgets.QPushButton("Change Archive Storage Location")
        self.btn_change_archive_storage.setCursor(QtCore.Qt.PointingHandCursor)

        archive_grid.addWidget(QtWidgets.QLabel("Archive Storage Root"), 0, 0)
        archive_grid.addWidget(self.lbl_archive_storage_root, 0, 1)
        archive_grid.addWidget(QtWidgets.QLabel("Source Images Folder"), 1, 0)
        archive_grid.addWidget(self.lbl_source_images_dir, 1, 1)
        archive_grid.addWidget(QtWidgets.QLabel("Generated Icons Folder"), 2, 0)
        archive_grid.addWidget(self.lbl_generated_icons_dir, 2, 1)
        archive_grid.addWidget(self.btn_open_archive_storage_root, 3, 0, 1, 2)
        archive_grid.addWidget(self.btn_change_archive_storage, 4, 0, 1, 2)
        archive_layout.addStretch(1)

        img_page = QtWidgets.QWidget()
        img_layout = QtWidgets.QVBoxLayout(img_page)
        img_layout.setContentsMargins(0,0,0,0)
        img_layout.setSpacing(10)
        self.settings_stack.addWidget(img_page)

        opt = CardFrame('Image')
        if opt.title_label is not None:
            opt.title_label.setObjectName("SettingsSectionTitle")
        img_layout.addWidget(opt)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        opt.body_layout().addLayout(g)

        self.cmb_quality = QtWidgets.QComboBox()
        self.cmb_quality.setCursor(QtCore.Qt.PointingHandCursor)
        presets = ["16-1024", "16-512", "16-256", "16-128", "16-64", "16-48", "16-32", "16-24", "16-16"]
        self.cmb_quality.addItems(presets)
        self.cmb_quality.setCurrentText("16-1024")
        self.chk_overwrite = QtWidgets.QCheckBox("Overwrite Mode")
        self.chk_overwrite.setChecked(True)
        self.cmb_padding = QtWidgets.QComboBox()
        self.cmb_padding.addItems(list(eng.PADDING_PRESETS.keys()))
        self.cmb_padding.setCurrentText("balanced")
        g.addWidget(QtWidgets.QLabel("Quality Preset"), 0, 0)
        g.addWidget(self.cmb_quality, 0, 1)
        g.addWidget(QtWidgets.QLabel("Padding"), 1, 0)
        g.addWidget(self.cmb_padding, 1, 1)
        g.addWidget(self.chk_overwrite, 2, 0, 1, 2)
        img_layout.addStretch(1)

        self.btn_settings_archive_storage.clicked.connect(lambda: self.settings_stack.setCurrentIndex(0))
        self.btn_settings_image.clicked.connect(lambda: self.settings_stack.setCurrentIndex(1))
        self.settings_stack.setCurrentIndex(0)
        self.view_stack.setCurrentWidget(self.page_main)
        self._sync_view_chrome()


    def _apply_theme(self) -> None:
        f = self.font()
        if IS_WINDOWS:
            f.setFamily("Segoe UI Variable")
        f.setPointSize(11)
        self.setFont(f)

        self.setStyleSheet(r"""
        QMainWindow {
            background: transparent;
        }
        #AppRoot {
            border-radius: 22px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #060812, stop:0.45 #070B18, stop:1 #0B0620);
            border: 1px solid rgba(255,255,255,0.07);
        }
        #AppTitleBar {
            border-radius: 16px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(10, 16, 34, 0.96),
                stop:0.55 rgba(11, 19, 52, 0.96),
                stop:1 rgba(31, 9, 46, 0.96));
            border: 1px solid rgba(255,255,255,0.08);
        }
        #AppTitleBar[nativeChrome="true"] {
            border-radius: 14px;
            background: rgba(10, 16, 34, 0.74);
            border: 1px solid rgba(255,255,255,0.06);
        }
        #TitleBarBrand {
            background: transparent;
        }
        QPushButton#TitleBarNavButton {
            border-radius: 10px;
            padding: 6px 12px;
            background-color: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.09);
            color: rgba(234,242,255,230);
            font-size: 11px;
            font-weight: 800;
        }
        QPushButton#TitleBarNavButton:hover {
            border-color: rgba(0,220,255,0.42);
            background-color: rgba(0,220,255,0.10);
        }
        QToolButton#WindowControl, QToolButton#WindowCloseControl {
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.05);
            color: rgba(234,242,255,235);
            font-size: 12px;
            font-weight: 900;
        }
        QToolButton#WindowControl:hover {
            border-color: rgba(0,220,255,0.45);
            background: rgba(0,220,255,0.10);
        }
        QToolButton#WindowCloseControl:hover {
            border-color: rgba(255,92,92,0.65);
            background: rgba(255,92,92,0.18);
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
        #MainHeaderStrip {
            border-radius: 16px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(10, 16, 34, 0.88),
                stop:0.55 rgba(11, 19, 52, 0.88),
                stop:1 rgba(31, 9, 46, 0.88));
            border: 1px solid rgba(255,255,255,0.06);
        }
        #BrandingImage {
            background: transparent;
        }
        #MainHeaderTitle {
            color: rgba(255,255,255,240);
            font-size: 20px;
            font-weight: 900;
            letter-spacing: 0.4px;
        }

        #Card {
            border-radius: 18px;
            background-color: rgba(13, 18, 38, 0.72);
            border: 1px solid rgba(255,255,255,0.07);
        }
        #Card[sageCard="true"] {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(9, 16, 34, 0.92),
                stop:1 rgba(13, 20, 44, 0.84));
            border: 1px solid rgba(0,220,255,0.16);
        }
        #CardTitle {
            color: rgba(234,242,255,230);
            font-weight: 900;
            font-size: 13px;
            letter-spacing: 0.4px;
        }
        #SettingsSectionTitle {
            color: rgba(234,242,255,220);
            font-weight: 900;
            font-size: 11px;
            letter-spacing: 0.2px;
        }
        #StatusLine {
            color: rgba(234,242,255,220);
            font-weight: 700;
        }
        QLabel#VersionWatermark {
            padding: 0 2px;
            background: transparent;
            color: rgba(234,242,255,153);
            font-size: 10px;
            font-weight: 700;
        }
        #FixedOutPath {
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.08);
            padding: 8px 10px;
            background-color: rgba(7, 10, 18, 0.42);
            color: rgba(234,242,255,230);
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
        QPushButton#QuickActionButton {
            padding: 6px 12px;
            font-size: 11px;
            font-weight: 800;
            background-color: rgba(255,255,255,0.05);
        }
        QPushButton#QuickActionButton:hover {
            border-color: rgba(0,220,255,0.45);
            background-color: rgba(0,220,255,0.10);
        }
        QPushButton#SettingsNavButton {
            font-size: 10px;
            padding: 8px 10px;
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

        self.btn_open_archive_storage_root.clicked.connect(lambda: _open_path(str(self.paths.storage_root)))
        self.btn_change_archive_storage.clicked.connect(self._change_archive_storage_location)  # type: ignore[arg-type]
        self.btn_open_current_source_image.clicked.connect(self._open_current_source_image)
        self.btn_open_current_generated_icon.clicked.connect(self._open_current_generated_icon)
        self.title_bar.btn_nav.clicked.connect(self._toggle_settings_view)
        self.view_stack.currentChanged.connect(lambda _: self._sync_view_chrome())

        self.btn_run.clicked.connect(self._run_convert)  # type: ignore[arg-type]
        self.btn_cancel.clicked.connect(self._cancel)  # type: ignore[arg-type]

        self.edit_input.textChanged.connect(self._update_run_state)
        self.chk_recursive.toggled.connect(self._update_run_state)
        self.mode_seg.modeChanged.connect(lambda _: self._update_run_state())

        self.mode_seg.modeChanged.connect(self._update_mode)  # type: ignore[arg-type]

        # Archive actions stay presentation-focused in GenArchive. The main
        # window decides how those actions map to app-level behavior.
        self.archive_sidebar.itemSelected.connect(self._on_archive_item_selected)
        self.archive_sidebar.openImageRequested.connect(self._open_archive_image)
        self.archive_sidebar.showInFolderRequested.connect(self._show_archive_image_in_folder)
        self.archive_sidebar.duplicateRequested.connect(self._duplicate_archive_image)
        self.archive_sidebar.copyImageRequested.connect(self._copy_archive_image)
        self.archive_sidebar.copyPathRequested.connect(self._copy_archive_image_path)
        self.archive_sidebar.copyIconRequested.connect(self._copy_archive_icon)
        self.archive_sidebar.renameRequested.connect(self._rename_archive_image)
        self.archive_sidebar.deleteRequested.connect(self._delete_archive_image)

    def _lock_output_to_canonical(self) -> None:
        self._refresh_archive_storage_labels()

    def _refresh_archive_storage_labels(self) -> None:
        labels = {
            "lbl_archive_storage_root": self.paths.storage_root,
            "lbl_source_images_dir": self.paths.images_dir,
            "lbl_generated_icons_dir": self.paths.icons_dir,
        }
        for attr_name, value in labels.items():
            label = getattr(self, attr_name, None)
            if label is not None:
                try:
                    label.setText(str(value))
                except Exception:
                    pass
        self._update_workflow_actions()

    def _current_source_image_path(self) -> Path | None:
        current = self.archive_sidebar.current_path()
        if current is not None:
            candidate = Path(current)
            if candidate.exists() and candidate.is_file() and _is_image_file(candidate):
                return candidate

        raw = self.edit_input.text().strip()
        if not raw:
            return None
        candidate = Path(raw)
        if candidate.exists() and candidate.is_file() and _is_image_file(candidate):
            return candidate
        return None

    def _current_archive_source_image_path(self) -> Path | None:
        source = self._current_source_image_path()
        if source is None:
            return None
        try:
            source.resolve().relative_to(self.paths.images_dir.resolve())
            return source
        except Exception:
            return None

    def _current_generated_icon_path(self) -> Path | None:
        archive_source = self._current_archive_source_image_path()
        if archive_source is None:
            return None
        icon_path = self._archive_icon_path_for_image(archive_source)
        return icon_path if icon_path.exists() else None

    def _update_workflow_actions(self) -> None:
        source = self._current_source_image_path()
        icon_path = self._current_generated_icon_path()

        if hasattr(self, "btn_open_current_source_image"):
            self.btn_open_current_source_image.setEnabled(source is not None)
            self.btn_open_current_source_image.setToolTip(str(source) if source is not None else "Select an image to enable this action.")

        if hasattr(self, "btn_open_current_generated_icon"):
            self.btn_open_current_generated_icon.setEnabled(icon_path is not None)
            self.btn_open_current_generated_icon.setToolTip(str(icon_path) if icon_path is not None else "Select an archived image with a generated icon to enable this action.")

    def _toggle_settings_view(self) -> None:
        if self.view_stack.currentWidget() is self.page_settings:
            self.view_stack.setCurrentWidget(self.page_main)
        else:
            self.view_stack.setCurrentWidget(self.page_settings)
        self._sync_view_chrome()

    def _sync_view_chrome(self) -> None:
        in_settings = self.view_stack.currentWidget() is self.page_settings
        self.title_bar.set_nav_text("Back" if in_settings else "Settings")
        self.title_bar.sync_state()
        self._update_window_shape()

    def _update_window_shape(self) -> None:
        if self._use_native_window_controls or self.isMaximized():
            self.clearMask()
            return
        path = QtGui.QPainterPath()
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(rect, 20, 20)
        region = QtGui.QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def _sync_archive_selection_from_input(self) -> None:
        raw = self.edit_input.text().strip()
        if not raw:
            self.archive_sidebar.set_current_path(None)
            self._update_workflow_actions()
            return

        candidate = Path(raw)
        if not candidate.exists() or not candidate.is_file() or not _is_image_file(candidate):
            self.archive_sidebar.set_current_path(None)
            self._update_workflow_actions()
            return

        try:
            candidate.resolve().relative_to(self.paths.images_dir.resolve())
        except Exception:
            self.archive_sidebar.set_current_path(None)
            self._update_workflow_actions()
            return

        self.archive_sidebar.set_current_path(candidate)
        self._update_workflow_actions()

    def _rebuild_archive_watch_paths(self) -> None:
        self._set_archive_watch_paths({str(self.paths.images_dir)})

    def _set_archive_watch_paths(self, desired_dirs: set[str]) -> None:
        existing_dirs = set(self._archive_fs_watcher.directories())

        remove_dirs = list(existing_dirs - desired_dirs)
        if remove_dirs:
            self._archive_fs_watcher.removePaths(remove_dirs)

        add_dirs = [path for path in desired_dirs - existing_dirs if Path(path).exists()]
        if add_dirs:
            self._archive_fs_watcher.addPaths(add_dirs)

    def _on_archive_storage_fs_changed(self, _path: str) -> None:
        self._refresh_archive_view()

    def _refresh_archive_view(self, immediate: bool = False) -> None:
        # Refresh is debounced so batch file activity and watcher storms do not
        # repeatedly rebuild the archive while the user is still interacting.
        self._archive_refresh_pending = True
        self._archive_refresh_timer.start(0 if immediate else 300)

    def _begin_archive_refresh(self) -> None:
        if self._archive_refresh_running:
            self._archive_refresh_pending = True
            return

        self._archive_refresh_pending = False
        self._archive_refresh_running = True
        self._archive_refresh_generation += 1

        generation = self._archive_refresh_generation
        thread = QtCore.QThread(self)
        worker = ArchiveRefreshWorker(
            generation,
            self.paths,
            self.archive_sidebar.entry_signatures(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_archive_refresh_finished)
        worker.failed.connect(self._on_archive_refresh_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._archive_refresh_thread = thread
        self._archive_refresh_worker = worker
        thread.start()

    def _clear_archive_refresh_worker(self) -> None:
        self._archive_refresh_running = False
        self._archive_refresh_worker = None
        self._archive_refresh_thread = None

    def _on_archive_refresh_finished(self, generation: int, snapshots: object) -> None:
        self._clear_archive_refresh_worker()
        if generation != self._archive_refresh_generation:
            return

        try:
            snapshot_list = list(snapshots)
            self.archive_sidebar.apply_snapshot(
                snapshot_list,
                archive_sources_dir=self.paths.images_dir,
                archive_icons_dir=self.paths.icons_dir,
            )
            self._set_archive_watch_paths(
                {str(self.paths.images_dir)}
                | {str(Path(entry.path).parent) for entry in snapshot_list}
            )
            if self.archive_sidebar.current_path() is None:
                self._sync_archive_selection_from_input()
            self._update_workflow_actions()
        except Exception as exc:
            self._log(f"WARN: Archive refresh apply failed: {type(exc).__name__}: {exc}", "WARN")

        if self._archive_refresh_pending:
            self._refresh_archive_view(immediate=True)

    def _on_archive_refresh_failed(self, generation: int, message: str) -> None:
        self._clear_archive_refresh_worker()
        if generation != self._archive_refresh_generation:
            return
        self._log(f"WARN: Archive refresh failed: {message}", "WARN")
        if self._archive_refresh_pending:
            self._refresh_archive_view(immediate=True)

    def _update_mode(self, *_args) -> None:
        try:
            mode = self.mode_seg.mode()
        except Exception:
            mode = "file"

        if mode == "folder":
            self.chk_recursive.setVisible(True)
            self.chk_recursive.setEnabled(True)
            self.btn_browse_input.setText("Select Folder...")
            self.edit_input.setPlaceholderText("Select Folder...")
        else:
            self.chk_recursive.setChecked(False)
            self.chk_recursive.setVisible(False)
            self.chk_recursive.setEnabled(False)
            self.btn_browse_input.setText("Select Image...")
            self.edit_input.setPlaceholderText("Select Image...")
        self._update_workflow_actions()

    def _update_run_state(self) -> None:
        self._update_workflow_actions()
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

    def _import_paths_to_archive_storage(self, paths: list) -> None:
        if not paths:
            return

        import_jobs = _build_archive_import_jobs([Path(p) for p in paths])
        if not import_jobs:
            self._log("Archive import skipped: no valid images detected.", "WARN")
            return

        imported = 0
        last_imported: Path | None = None
        for image_path, source_root in import_jobs:
            try:
                dst = eng.mirror_copy_to_archive_sources(
                    image_path,
                    paths=self.paths,
                    source_root=source_root,
                    logfn=lambda s: self._log(s, "INFO"),
                )
                if dst is not None:
                    imported += 1
                    last_imported = Path(dst)
            except Exception as e:
                self._log(f"ERR: Archive import failed: {image_path}: {type(e).__name__}: {e}", "ERR")

        self._log(
            f"Archive import finished. images_detected={len(import_jobs)} imported_or_existing={imported}",
            "INFO",
        )

        self.archive_sidebar.expand()
        self._run_archive_maintenance_now("archive-import")
        if last_imported is not None:
            self._set_input(str(last_imported))

    def _set_input(self, p: str) -> None:
        self.edit_input.setText(p)
        self.mode_seg.set_mode("folder" if Path(p).is_dir() else "file")
        self._update_mode()
        self._sync_archive_selection_from_input()
        self._update_run_state()

    def _on_archive_item_selected(self, path: Path) -> None:
        # Archive selection currently drives the active source field so the user
        # can inspect a stored image and immediately run related actions.
        self._set_input(str(path))

    def _open_current_source_image(self) -> None:
        source = self._current_source_image_path()
        if source is None:
            return
        _open_path(str(source))

    def _open_current_generated_icon(self) -> None:
        icon_path = self._current_generated_icon_path()
        if icon_path is None:
            self._log("WARN: No generated icon is available for the current archive image.", "WARN")
            return
        _open_path(str(icon_path))

    def _open_archive_image(self, path: Path) -> None:
        _open_path(str(path))

    def _show_archive_image_in_folder(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            _reveal_in_file_manager(p)
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

    def _copy_archive_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            self._copy_urls_to_clipboard([p])
            self._log(f"Copied image: {p.name}")
        except Exception as e:
            self._log(f"WARN: Copy Image failed: {p} ({e})", "WARN")

    def _copy_archive_image_path(self, path: Path) -> None:
        p = Path(path)
        QtWidgets.QApplication.clipboard().setText(str(p))
        self._log(f"Copied path: {p}")

    def _archive_icon_path_for_image(self, path: Path) -> Path:
        return eng.archive_icon_path_for_source_image(Path(path), paths=self.paths)

    def _copy_archive_icon(self, path: Path) -> None:
        ico = self._archive_icon_path_for_image(path)
        if not ico.exists():
            self._log(f"WARN: Icon does not exist yet: {ico.name}", "WARN")
            return
        try:
            self._copy_urls_to_clipboard([ico])
            self._log(f"Copied icon: {ico.name}")
        except Exception as e:
            self._log(f"WARN: Copy Icon failed: {ico} ({e})", "WARN")

    def _duplicate_archive_image(self, path: Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            dst = eng.unique_path(p.parent / p.name)
            shutil.copy2(p, dst)
            self._log(f"Duplicated image: {p.name} -> {dst.name}")
            self._run_archive_maintenance_now("archive-duplicate")
            self._set_input(str(dst))
        except Exception as e:
            self._log(f"ERR: Duplicate failed: {p} ({e})", "ERR")

    def _rename_archive_image(self, path: Path) -> None:
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
            old_icon = self._archive_icon_path_for_image(p)
            new_icon = self._archive_icon_path_for_image(desired)
            p.rename(desired)
            if old_icon.exists() and old_icon != new_icon:
                new_icon.parent.mkdir(parents=True, exist_ok=True)
                old_icon.replace(new_icon)
            self.archive_sidebar.rename_item_path(p, desired)
            self._log(f"Renamed image: {p.name} -> {desired.name}")
            self._set_input(str(desired))
            self._refresh_archive_view()
        except Exception as e:
            self._log(f"ERR: Rename failed: {p} ({e})", "ERR")

    def _delete_archive_image(self, path: Path) -> None:
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
            self._run_archive_maintenance_now("archive-delete")
        except Exception as e:
            self._log(f"ERR: Delete failed: {p} ({e})", "ERR")

    def _browse_input(self) -> None:
        mode = self.mode_seg.mode()
        if mode == "folder":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Folder", str(self.paths.images_dir))
            if p:
                self._set_input(p)
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose Image", str(self.paths.images_dir))
            if p:
                self._set_input(p)

    def _run_archive_maintenance_now(self, reason: str) -> None:
        self._log(f"=== MAINTENANCE ({reason}) ===")
        self.status_line.setText(f"Updating archive... ({reason})")
        try:
            report = GenOps.run_archive_maintenance(
                paths=self.paths,
                overwrite=self.chk_overwrite.isChecked(),
                sizes=preset_sizes(self.cmb_quality.currentText()),
                padding_mode=self.cmb_padding.currentText(),
                autocrop=False,
                logfn=lambda s: self._log(s),
            )
            self._log(
                "Archive maintenance done. "
                f"scanned={report.scanned} converted={report.converted} "
                f"orphans_removed={report.orphan_icons_removed}"
            )
        except Exception as e:
            self._log(f"ERR: Maintenance failed: {e}", "ERR")
        finally:
            self._refresh_archive_view(immediate=True)
            self.status_line.setText("Ready.")
            self._update_workflow_actions()

    def _poll_app_events(self, force: bool = False) -> None:
        event = GenOps.latest_app_event()
        if not force and event.seq <= self._last_seen_event_seq:
            return
        self._last_seen_event_seq = event.seq
        if force or event.event_type in {
            "archive-storage-changed",
            "archive-storage-maintained",
            "archive-storage-relocated",
            "conversion-finished",
        }:
            self._refresh_archive_view()

    def _change_archive_storage_location(self) -> None:
        start_dir = str(getattr(self, "archive_storage_root", Path.home()))
        picked = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose New Archive Storage Location",
            start_dir,
        )
        if not picked:
            return
        new_root = Path(picked).resolve()
        old_root = Path(self.archive_storage_root).resolve()
        if new_root == old_root:
            self._log("Archive storage location unchanged.", "WARN")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "IconMaker",
            (
                f"Move archive storage from:\n{old_root}\n\nTo:\n{new_root}\n\n"
                "Tray and background activity will pause during relocation."
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        dlg = QtWidgets.QProgressDialog("Relocating archive storage...", "Cancel", 0, 100, self)
        dlg.setWindowTitle("IconMaker")
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.show()

        def on_progress(done: int, total: int, current: str) -> None:
            pct = int((done * 100) / max(1, total))
            dlg.setValue(pct)
            dlg.setLabelText(f"Relocating...\n{current}")
            QtWidgets.QApplication.processEvents()

        old_images_dir = self.paths.images_dir
        result = GenOps.relocate_archive_storage(
            old_root,
            new_root,
            delete_source=False,
            progress_cb=on_progress,
            is_cancelled=lambda: dlg.wasCanceled(),
        )
        dlg.close()
        if not result.ok:
            self._log(f"Archive storage relocation failed: {result.message}", "ERR")
            QtWidgets.QMessageBox.critical(self, "IconMaker", f"Relocation failed:\n{result.message}")
            return

        GenOps.save_archive_storage_root(new_root, self._settings)
        self._set_archive_storage_paths(new_root)
        self._lock_output_to_canonical()
        self._refresh_archive_view()
        current_input = self.edit_input.text().strip()
        if current_input:
            try:
                relative_input = Path(current_input).resolve().relative_to(old_images_dir.resolve())
            except Exception:
                relative_input = None
            if relative_input is not None:
                remapped_input = self.paths.images_dir / relative_input
                if remapped_input.exists():
                    self._set_input(str(remapped_input))

        self.archive_sidebar.expand()
        self._log(f"Archive storage relocated to: {new_root}")
        if QtWidgets.QMessageBox.question(
            self,
            "IconMaker",
            f"Relocation verified. Delete old archive storage?\n\n{old_root}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        ) == QtWidgets.QMessageBox.Yes:
            try:
                shutil.rmtree(old_root)
                self._log(f"Old archive storage deleted: {old_root}")
            except Exception as e:
                self._log(f"WARN: Could not delete old archive storage ({e})", "WARN")

    def _cancel(self) -> None:
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)
        self._log("Cancel requested.", "WARN")

    def _set_run_ui_enabled(self, enabled: bool) -> None:
        has_input = bool(self.edit_input.text().strip())
        self.btn_run.setEnabled(enabled and has_input)
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
        self.status_line.setText("Starting...")
        self._set_run_ui_enabled(False)

        sizes = [s for s in preset_sizes(self.cmb_quality.currentText()) if 1 <= s <= 1024]
        if not sizes:
            sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]

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

        try:
            def progress(done: int, total: int, current: str) -> None:
                self.status_line.setText(current)
                self.bar.setValue(int((done * 100) / max(1, total)))
                QtWidgets.QApplication.processEvents()

            result = GenOps.run_conversion(
                request,
                progress_cb=progress,
                logfn=lambda s: self._log(s),
                is_cancelled=lambda: self._cancel_requested,
            )
        except Exception as e:
            self._log(f"ERR: Conversion failed: {type(e).__name__}: {e}", "ERR")
            self.status_line.setText("ERR: Conversion crashed.")
            self.bar.setValue(0)
            return
        finally:
            self._run_in_progress = False
            self._set_run_ui_enabled(True)
            QtCore.QTimer.singleShot(0, self._update_run_state)
            QtCore.QTimer.singleShot(0, self._update_workflow_actions)

        self.status_line.setText(result.message)
        if result.ok:
            self.bar.setValue(100)
        if result.converted > 0:
            self.archive_sidebar.expand()
        self._refresh_archive_view()

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            try:
                self.edit_input._set_drag(True)
            except Exception:
                pass
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        try:
            self.edit_input._set_drag(False)
        except Exception:
            pass
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        try:
            self.edit_input._set_drag(False)
        except Exception:
            pass

        if e.mimeData().hasUrls():
            paths: list[Path] = []
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if p:
                    paths.append(Path(p))
            if paths:
                self._import_paths_to_archive_storage(paths)
                e.acceptProposedAction()
                return

        super().dropEvent(e)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        if event.type() == QtCore.QEvent.WindowStateChange:
            try:
                self.title_bar.sync_state()
                self._update_window_shape()
            except Exception:
                pass
        super().changeEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        try:
            self._update_window_shape()
        except Exception:
            pass
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        try:
            self._events_timer.stop()
        except Exception:
            pass
        try:
            self._archive_refresh_timer.stop()
        except Exception:
            pass
        try:
            if self._archive_refresh_thread is not None:
                self._archive_refresh_thread.requestInterruption()
                self._archive_refresh_thread.quit()
                if not self._archive_refresh_thread.wait(2000):
                    self._archive_refresh_thread.terminate()
                    self._archive_refresh_thread.wait(1000)
        except Exception:
            pass
        super().closeEvent(event)


def main() -> None:
    _pre_app_setup()

    app = QtWidgets.QApplication(sys.argv)
    apply_qt_application_identity(app)
    app.setWindowIcon(get_app_icon())

    w = MainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

