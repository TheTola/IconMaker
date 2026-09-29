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
from typing import Callable, Optional, Iterable

from StateMemory import StateMemory
from AppTheme import APPEARANCES, theme_css, theme_manager
from GenSignInState import APP_OPENED_KEY, SIGN_IN_PROVIDERS, send_generator_command, should_offer_sign_in, sign_in_status
from AppAmbient import AppAmbientRoot
from AppTitleBar import CustomTitleBar, TITLE_BAR_CSS

from PySide6 import QtCore, QtGui, QtWidgets


import Gen2 as eng
import GenOps
from GenFolderIcons import ensure_iconmaker_folder_icons
from AppIdentity import (
    APP_DISPLAY_NAME,
    APP_DISPLAY_VERSION,
    APP_NAME,
    APP_ORG,
    APP_USER_MODEL_ID,
    APP_VERSION,
)
from GenArchive import ArchiveSidebar, ArchiveEntrySnapshot, THUMB_SIZE
from ArchiveImageViewer import ArchiveImageViewer
from GeneratedImageInfo import copy_archive_records, copy_record, delete_record, move_record

from Gen3 import scan_and_convert
from Gen4 import get_app_icon

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
SAGE_TOOLTIP = "Open image generation tools."

APP_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_DIR / "assets"

# These assets define the prominent Sage shortcut.
SAGE_BUTTON_IMAGE_PATH = ASSETS_DIR / "IcoSage.png"
SAGE_BUTTON_IMAGE_LIGHT_PATH = ASSETS_DIR / "IcoSageLight.png"

# Keep the Sage artwork legible within the shortcut card.
HERO_ICON_SIZE = 213
SAGE_BTN_SIZE = HERO_ICON_SIZE
COMPLETION_DISPLAY_MS = 8000


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


def _pre_app_setup() -> None:
    """Windows AppUserModelID (helps taskbar grouping and icon association)."""
    if IS_WINDOWS:
        try:
            import ctypes  # local import intentional
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


def _yield_to_startup_splash() -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None and app.property("iconforgeStartupSplashActive"):
        app.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)


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
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        if title:
            t = QtWidgets.QLabel(title)
            t.setObjectName("CardTitle")
            lay.addWidget(t)
            self.title_label = t

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 85))
        self.setGraphicsEffect(shadow)
        theme_manager().changed.connect(self._apply_shadow_theme)
        self._apply_shadow_theme()

    def _apply_shadow_theme(self, *_args) -> None:
        color = QtGui.QColor(32, 67, 96, 38) if theme_manager().appearance == "Light" else QtGui.QColor(0, 0, 0, 85)
        self.graphicsEffect().setColor(color)

    def body_layout(self) -> QtWidgets.QVBoxLayout:
        return self.layout()  # type: ignore[return-value]


class ImageSettingsComboBox(QtWidgets.QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImageSettingsCombo")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(38)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        colors = theme_manager().colors
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        color = colors.accent if self.isEnabled() else colors.disabled
        painter.setPen(QtGui.QPen(
            QtGui.QColor(color), 2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin
        ))
        x, y = self.width() - 17, self.height() / 2
        chevron = QtGui.QPainterPath(QtCore.QPointF(x - 4, y - 2))
        chevron.lineTo(x, y + 2)
        chevron.lineTo(x + 4, y - 2)
        painter.drawPath(chevron)


class RecursiveCheckBox(QtWidgets.QCheckBox):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        option = QtWidgets.QStyleOptionButton()
        self.initStyleOption(option)
        painter = QtWidgets.QStylePainter(self)
        painter.drawControl(QtWidgets.QStyle.CE_CheckBoxLabel, option)

        rect = self.style().subElementRect(QtWidgets.QStyle.SE_CheckBoxIndicator, option, self).adjusted(0, 0, -1, -1)
        colors = theme_manager().colors
        dark = theme_manager().appearance == "Dark"
        accent = QtGui.QColor(colors.accent if self.isEnabled() else colors.disabled)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(accent, 1.5))
        painter.setBrush(accent if self.isChecked() else QtGui.QColor(colors.field))
        painter.drawRoundedRect(QtCore.QRectF(rect), 4, 4)
        if self.isChecked():
            mark = QtGui.QColor("#000000" if dark else "#ffffff")
            painter.setPen(QtGui.QPen(mark, 2.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            check = QtGui.QPainterPath(QtCore.QPointF(rect.x() + rect.width() * 0.22, rect.y() + rect.height() * 0.52))
            check.lineTo(rect.x() + rect.width() * 0.43, rect.y() + rect.height() * 0.73)
            check.lineTo(rect.x() + rect.width() * 0.79, rect.y() + rect.height() * 0.27)
            painter.drawPath(check)
        if option.state & QtWidgets.QStyle.State_HasFocus:
            painter.setPen(QtGui.QPen(accent, 1, QtCore.Qt.DotLine))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(self.rect().adjusted(0, 0, -1, -1)), 4, 4)


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
            self.setText(APP_DISPLAY_NAME)
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


class ImageThumbnailProvider(QtWidgets.QFileIconProvider):
    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[tuple[str, int, int], QtGui.QIcon] = {}

    def icon(self, info: QtCore.QFileInfo | QtWidgets.QFileIconProvider.IconType) -> QtGui.QIcon:
        if not isinstance(info, QtCore.QFileInfo) or not info.isFile() or info.suffix().lower() not in {
            "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "svg"
        }:
            return super().icon(info)
        key = (info.absoluteFilePath(), info.lastModified().toMSecsSinceEpoch(), info.size())
        if key in self._cache:
            return self._cache[key]
        reader = QtGui.QImageReader(info.absoluteFilePath())
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(QtCore.QSize(192, 192), QtCore.Qt.KeepAspectRatio))
        image = reader.read()
        icon = QtGui.QIcon(QtGui.QPixmap.fromImage(image)) if not image.isNull() else super().icon(info)
        if len(self._cache) >= 256:
            self._cache.clear()
        self._cache[key] = icon
        return icon


class PickerTileDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self._tile_size = QtCore.QSize(194, 222)
        self._icon_size = 176

    def set_tile_size(self, size: QtCore.QSize, icon_size: int) -> None:
        self._tile_size = size
        self._icon_size = icon_size

    def sizeHint(self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> QtCore.QSize:
        return self._tile_size

    def helpEvent(self, event: QtGui.QHelpEvent, view: QtWidgets.QAbstractItemView,
                  option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> bool:
        if event.type() == QtCore.QEvent.Type.ToolTip:
            QtWidgets.QToolTip.showText(event.globalPos(), str(index.data() or ""), view)
            return True
        return super().helpEvent(event, view, option, index)

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:
        colors = theme_manager().colors
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(2, 2, -2, -2)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(QtGui.QPen(QtGui.QColor(colors.accent if selected else colors.border), 1))
        painter.setBrush(QtGui.QColor(colors.selection if selected else colors.raised if hovered else colors.panel))
        painter.drawRoundedRect(QtCore.QRectF(rect), 10, 10)

        icon = index.data(QtCore.Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QtGui.QIcon):
            pixmap = icon.pixmap(QtCore.QSize(self._icon_size, self._icon_size))
            if not pixmap.isNull():
                icon_area = QtCore.QRect(rect.x() + 7, rect.y() + 7, rect.width() - 14, self._icon_size)
                target = QtCore.QRect(
                    QtCore.QPoint(), pixmap.size().scaled(icon_area.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
                )
                target.moveCenter(icon_area.center())
                painter.drawPixmap(target, pixmap)

        text_rect = QtCore.QRect(rect.x() + 8, rect.y() + self._icon_size + 13, rect.width() - 16, 24)
        painter.setPen(QtGui.QColor(colors.selection_text if selected else colors.text))
        label = option.fontMetrics.elidedText(str(index.data() or ""), QtCore.Qt.TextElideMode.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


class ThemedFileDialog(QtWidgets.QFileDialog):
    def __init__(self, parent, title: str, directory: str, *, folder: bool):
        super().__init__(parent, title, directory)
        font = self.font()
        if IS_WINDOWS:
            font.setFamily("Segoe UI Variable")
        font.setPointSize(10)
        self.setFont(font)
        self.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        self.setFileMode(
            QtWidgets.QFileDialog.FileMode.Directory if folder
            else QtWidgets.QFileDialog.FileMode.ExistingFile
        )
        self.setViewMode(QtWidgets.QFileDialog.ViewMode.List)
        if folder:
            self.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
            self.setLabelText(QtWidgets.QFileDialog.DialogLabel.FileName, "Folder")
            self.setLabelText(QtWidgets.QFileDialog.DialogLabel.Accept, "Select folder")
        else:
            self.setNameFilter("Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.svg)")
            self.setLabelText(QtWidgets.QFileDialog.DialogLabel.FileName, "Image")
            self.setLabelText(QtWidgets.QFileDialog.DialogLabel.Accept, "Select image")
            self._thumbnail_provider = ImageThumbnailProvider()
            self.setIconProvider(self._thumbnail_provider)
        self.setLabelText(QtWidgets.QFileDialog.DialogLabel.LookIn, "Location")
        self.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptOpen)
        self.setMinimumSize(680, 480)
        layout = self.layout()
        layout.setContentsMargins(16, 72, 16, 16)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        self.title_bar = CustomTitleBar(self, chooser_title=title)
        self._icon_view = self.findChild(QtWidgets.QListView, "listView")
        self._icon_view.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self._icon_view.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self._icon_view.setMovement(QtWidgets.QListView.Movement.Static)
        self._icon_view.setWrapping(True)
        self._tile_delegate = PickerTileDelegate(self._icon_view)
        self._icon_view.setItemDelegate(self._tile_delegate)
        view_page = self._icon_view.parentWidget()
        view_page.layout().setSpacing(10)
        self._view_bar = QtWidgets.QFrame(self)
        self._view_bar.setObjectName("PickerViewBar")
        view_bar_layout = QtWidgets.QHBoxLayout(self._view_bar)
        view_bar_layout.setContentsMargins(14, 8, 10, 8)
        view_bar_layout.setSpacing(8)
        view_title = QtWidgets.QLabel("Folders" if folder else "Images", self._view_bar)
        view_title.setObjectName("PickerViewTitle")
        view_bar_layout.addWidget(view_title)
        view_bar_layout.addStretch()
        view_hint = QtWidgets.QLabel("Icon size", self._view_bar)
        view_hint.setObjectName("PickerViewHint")
        view_bar_layout.addWidget(view_hint)
        self._size_buttons = QtWidgets.QButtonGroup(self)
        for label in ("Large", "Extra large"):
            button = QtWidgets.QPushButton(label, self._view_bar)
            button.setObjectName("PickerSizeButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, size=label: self._set_icon_scale(size))
            self._size_buttons.addButton(button)
            view_bar_layout.addWidget(button)
            if label == "Extra large":
                button.setChecked(True)
        view_page.layout().insertWidget(0, self._view_bar)
        self._set_icon_scale("Extra large")
        for name in ("listModeButton", "detailModeButton"):
            button = self.findChild(QtWidgets.QToolButton, name)
            if button is not None:
                button.hide()
        for name in ("backButton", "forwardButton", "toParentButton", "newFolderButton"):
            button = self.findChild(QtWidgets.QToolButton, name)
            if button is not None:
                button.setFixedSize(34, 34)
                button.setIconSize(QtCore.QSize(18, 18))
        for name in ("lookInCombo", "fileTypeCombo", "fileNameEdit"):
            field = self.findChild(QtWidgets.QWidget, name)
            if field is not None:
                field.setMinimumHeight(36)
        for name in ("fileTypeLabel", "fileTypeCombo"):
            field = self.findChild(QtWidgets.QWidget, name)
            if field is not None:
                field.hide()
        button_box = self.findChild(QtWidgets.QDialogButtonBox, "buttonBox")
        if button_box is not None:
            button_box.setOrientation(QtCore.Qt.Orientation.Horizontal)
            for button in button_box.buttons():
                button.setMinimumSize(100, 36)
            layout.removeWidget(button_box)
            layout.addWidget(button_box, 2, 2)
        sidebar = self.findChild(QtWidgets.QListView, "sidebar")
        if sidebar is not None:
            sidebar.setIconSize(QtCore.QSize(22, 22))
            sidebar.setSpacing(4)
        self._toolbar_icons = [
            (button, button.icon()) for button in self.findChildren(QtWidgets.QToolButton)
            if button.objectName() in {"backButton", "forwardButton", "toParentButton"}
        ]
        self._theme = theme_manager()
        self._theme.changed.connect(self._apply_theme)
        self._apply_theme()
        available = self.screen().availableGeometry()
        self.resize(min(1020, max(680, available.width() - 64)),
                    min(720, max(480, available.height() - 64)))
        self.title_bar.setGeometry(12, 8, self.width() - 24, 52)

    def _set_icon_scale(self, size: str) -> None:
        icon, cell = (120, 158) if size == "Large" else (176, 214)
        self._icon_view.setIconSize(QtCore.QSize(icon, icon))
        self._icon_view.setGridSize(QtCore.QSize(cell, cell + 28))
        self._tile_delegate.set_tile_size(QtCore.QSize(cell - 20, cell + 8), icon)
        self._icon_view.doItemsLayout()

    def _apply_theme(self) -> None:
        c = self._theme.colors
        for button, original in self._toolbar_icons:
            source = original.pixmap(button.iconSize())
            if source.isNull():
                continue
            tinted = QtGui.QPixmap(source.size())
            tinted.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(tinted)
            painter.drawPixmap(0, 0, source)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), QtGui.QColor(c.text))
            painter.end()
            button.setIcon(QtGui.QIcon(tinted))
        self.setStyleSheet(theme_css(TITLE_BAR_CSS) + f"""
            QFileDialog {{ background: {c.window}; color: {c.text}; border: 1px solid {c.border}; }}
            QFrame#PickerViewBar {{
                background: {c.panel}; border: 1px solid {c.border}; border-radius: 10px;
            }}
            QLabel#PickerViewTitle {{ color: {c.text}; font-size: 15px; font-weight: 600; }}
            QLabel#PickerViewHint {{ color: {c.muted}; padding-right: 4px; }}
            QPushButton#PickerSizeButton {{
                background: transparent; color: {c.muted}; border: 1px solid {c.border};
                border-radius: 7px; padding: 7px 12px;
            }}
            QPushButton#PickerSizeButton:hover {{ color: {c.text}; border-color: {c.accent}; }}
            QPushButton#PickerSizeButton:checked {{
                background: {c.accent_soft}; color: {c.text}; border-color: {c.accent};
            }}
            QFileDialog QTreeView, QFileDialog QListView {{
                background: {c.field}; color: {c.text}; border: 1px solid {c.border}; border-radius: 8px;
                selection-background-color: {c.selection}; selection-color: {c.selection_text};
            }}
            QListView#listView::item {{ border-radius: 8px; padding: 6px; }}
            QListView#listView::item:hover {{ background: {c.raised}; }}
            QListView#listView::item:selected {{ background: {c.selection}; color: {c.selection_text}; }}
            QListView#sidebar::item {{ border-radius: 7px; padding: 7px 8px; }}
            QListView#sidebar::item:hover {{ background: {c.raised}; }}
            QListView#sidebar::item:selected {{ background: {c.selection}; color: {c.selection_text}; }}
            QFileDialog QHeaderView::section {{
                background: {c.panel}; color: {c.text}; border: 1px solid {c.border}; padding: 5px;
            }}
            QFileDialog QComboBox, QFileDialog QLineEdit {{
                background: {c.field}; color: {c.text}; border: 1px solid {c.border};
                border-radius: 6px; padding: 5px;
                selection-background-color: {c.selection}; selection-color: {c.selection_text};
            }}
            QFileDialog QPushButton {{
                background: {c.raised}; color: {c.text}; border: 1px solid {c.border};
                border-radius: 6px; padding: 5px 10px;
            }}
            QFileDialog QPushButton:hover {{
                background: {c.accent_soft}; border-color: {c.accent};
            }}
            QFileDialog QToolButton {{
                background: transparent; border: 1px solid transparent; border-radius: 7px;
            }}
            QFileDialog QToolButton:hover {{ background: {c.raised}; border-color: {c.border}; }}
            QFileDialog QSplitter::handle {{ background: {c.border}; }}
            QFileDialog QLabel {{ color: {c.text}; }}
        """)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.title_bar.setGeometry(12, 8, self.width() - 24, 52)
        self.title_bar.raise_()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        splitter = self.findChild(QtWidgets.QSplitter, "splitter")
        if splitter is not None and splitter.sizes() and splitter.sizes()[0] < 140:
            splitter.setSizes([190, max(1, self.width() - 220)])


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

        self._glow_blur = 14
        self._glow_alpha = 85

        self._ripple_active = False
        self._ripple_center = QtCore.QPointF(0, 0)
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0

        self._glow_fx = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow_fx.setOffset(0, 0)
        self._glow_fx.setBlurRadius(self._glow_blur)
        self._glow_fx.setColor(QtGui.QColor(54, 201, 232, self._glow_alpha))
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
        self._glow_fx.setColor(QtGui.QColor(54, 201, 232, max(0, min(255, int(v)))))

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
        self.setIconSize(QtCore.QSize(btn - 26, btn - 26))

        # circular mask
        region = QtGui.QRegion(QtCore.QRect(0, 0, btn, btn), QtGui.QRegion.Ellipse)
        self.setMask(region)

    def enterEvent(self, e: QtCore.QEvent) -> None:
        self._glow_anim.stop()
        self._alpha_anim.stop()

        self._glow_anim.setStartValue(self._get_glow_blur())
        self._glow_anim.setEndValue(34)

        self._alpha_anim.setStartValue(self._get_glow_alpha())
        self._alpha_anim.setEndValue(160)

        self._glow_anim.start()
        self._alpha_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e: QtCore.QEvent) -> None:
        self._glow_anim.stop()
        self._alpha_anim.stop()

        self._glow_anim.setStartValue(self._get_glow_blur())
        self._glow_anim.setEndValue(14)

        self._alpha_anim.setStartValue(self._get_glow_alpha())
        self._alpha_anim.setEndValue(85)

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

            self._rip_o_anim.setStartValue(0.22)
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

        col = QtGui.QColor(54, 201, 232)
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
    return eng.quality_preset_sizes(preset)


def choose_archive_storage_root(parent) -> Path | None:
    dialog = ThemedFileDialog(parent, "Choose Your Folder.", QtCore.QDir.homePath(), folder=True)
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    selected = dialog.selectedFiles()
    return Path(selected[0]) if selected else None


class OperationWorker(QtCore.QObject):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(int, int, str)
    message = QtCore.Signal(str)

    def __init__(self, work: Callable[..., object]) -> None:
        super().__init__()
        self.work = work

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.work(
                self.progress.emit,
                self.message.emit,
                lambda: bool(self.thread() and self.thread().isInterruptionRequested()),
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ArchiveRefreshWorker(QtCore.QObject):
    """
    Build archive snapshots away from the UI thread.

    Snapshot work reads filesystem metadata, dimensions, and thumbnails for the
    managed archive so refreshes can stay responsive during watcher updates and
    large archive scans.
    """
    finished = QtCore.Signal(int, object, object)
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
            for path in eng.list_archive_source_images(paths=self.paths):
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
            # Archive browsing is newest-first so freshly imported or edited
            # images surface immediately without forcing the user to hunt
            # through an alphabetized grid.
            snapshots.sort(key=lambda item: (-item.signature_mtime, str(item.path).casefold()))
            watch_paths = GenOps.build_archive_storage_watch_paths(self.paths)
            self.finished.emit(self.generation, snapshots, watch_paths)
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
        source_size = reader.size()
        if source_size.isValid():
            reader.setScaledSize(source_size.scaled(THUMB_SIZE, THUMB_SIZE, QtCore.Qt.KeepAspectRatio))
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
        self._sign_in_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._sign_in_status_timer = QtCore.QTimer(self)
        self._sign_in_status_timer.setInterval(2000)
        self._sign_in_status_timer.timeout.connect(self._refresh_sign_in_buttons)
        self._theme = theme_manager()
        self._sage_process: subprocess.Popen | None = None
        self._sign_in_prompt_scheduled = False
        self._image_viewers: set[ArchiveImageViewer] = set()
        GenOps.apply_launch_tray_at_startup(settings=self._settings)

        # The UI keeps a lightweight in-memory log so status labels and dialogs
        # can reflect recent work without depending on the persisted log files.
        self._log_history: list[LogLine] = []
        self._log_pending: list[LogLine] = []

        self._run_in_progress: bool = False
        self._operation_kind: str | None = None
        self._operation_thread: QtCore.QThread | None = None
        self._operation_worker: OperationWorker | None = None
        self._operation_result: object | None = None
        self._operation_error: str | None = None
        self._close_when_idle = False

        root = GenOps.load_archive_storage_root(self._settings)

        if not root or not root.exists() or not root.is_dir():
            chosen = choose_archive_storage_root(self)
            if not chosen:
                QtWidgets.QMessageBox.critical(
                    self,
                    APP_DISPLAY_NAME,
                    "An Archive Storage location is required to continue.",
                )
                sys.exit(1)
            GenOps.save_archive_storage_root(chosen, self._settings)
            GenOps.publish_app_event("archive-storage-root-chosen", str(chosen))
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
        self._archive_auto_convert_timer = QtCore.QTimer(self)
        self._archive_auto_convert_timer.setSingleShot(True)
        self._archive_auto_convert_timer.setInterval(1500)
        self._archive_auto_convert_timer.timeout.connect(self._begin_watched_image_conversion)
        self._archive_image_signatures: dict[str, tuple[int, float]] | None = None
        self._archive_auto_convert_candidates: dict[str, tuple[int, float]] = {}
        self._archive_auto_convert_pending = False
        self._archive_refresh_running = False
        self._archive_refresh_pending = False
        self._archive_refresh_generation = 0
        self._archive_refresh_thread: QtCore.QThread | None = None
        self._archive_refresh_worker: ArchiveRefreshWorker | None = None

        self._app_icon = get_app_icon()
        self.setWindowIcon(self._app_icon)
        _yield_to_startup_splash()

        self.setWindowTitle(APP_DISPLAY_VERSION)
        self.setMinimumSize(1180, 760)
        self.setAcceptDrops(True)

        self._build_ui()
        self._apply_theme()
        self._theme.changed.connect(self._apply_theme)
        self._theme.preferenceChanged.connect(self._sync_theme_selection)
        self._wire()

        self.state = StateMemory(APP_ORG, APP_NAME, logger=self._log)
        self.state.load_to_ui(self)
        self.state.install_auto_save(self)

        try:
            self.state.apply_truthful_source_ui(self)
        except Exception:
            pass

        self._log("Ready.")
        self._lock_output_to_canonical()
        self._refresh_archive_view(immediate=True)
        self._events_timer.start()
        self._poll_app_events(force=True)
        self._update_mode()

        self._update_run_state()
        self.title_bar.sync_state()
        QtCore.QTimer.singleShot(0, self._startup_scan)

    def _startup_scan(self) -> None:
        def work(_progress, log, _cancelled):
            GenOps.reconcile_image_copies(paths=self.paths, on_start_or_close=True, logfn=log)
            return scan_and_convert()

        self._start_operation("startup", work, "Scanning archive...")

    def _set_archive_storage_paths(self, archive_storage_root: Path) -> None:
        root = Path(archive_storage_root).resolve()
        if hasattr(self, "_archive_auto_convert_timer"):
            self._archive_auto_convert_timer.stop()
            self._archive_image_signatures = None
            self._archive_auto_convert_candidates.clear()
            self._archive_auto_convert_pending = False
        self.archive_storage_root = root
        self.paths = eng.EnginePaths.from_archive_storage_root(root)
        eng.ensure_archive_storage_dirs(self.paths)
        ensure_iconmaker_folder_icons(self.paths)

    def _build_ui(self) -> None:
        root = AppAmbientRoot(self._theme)
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
        self.main_area.setMinimumWidth(580)
        left = QtWidgets.QVBoxLayout(self.main_area)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        body_layout.addWidget(self.main_area, 1)

        self.archive_sidebar = ArchiveSidebar(self)
        body_layout.addWidget(self.archive_sidebar)
        self.archive_sidebar.filesDropped.connect(self._import_paths_to_archive_storage)
        _yield_to_startup_splash()

        source = CardFrame('Source')
        self.source_card = source
        left.addWidget(source)
        sg = QtWidgets.QGridLayout()
        sg.setHorizontalSpacing(8)
        sg.setVerticalSpacing(8)
        source.body_layout().addLayout(sg)

        self.mode_seg = SegmentedMode()
        self.mode_seg.setFixedWidth(240)
        sg.addWidget(self.mode_seg, 0, 0, 1, 3)

        self.edit_input = DropLineEdit()
        self.edit_input.setPlaceholderText("Select Image...")
        self.btn_browse_input = QtWidgets.QPushButton("Select Image...")
        self.btn_browse_input.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_browse_input.setFixedWidth(160)
        sg.setColumnStretch(0, 1)
        sg.setColumnStretch(1, 1)
        sg.addWidget(self.edit_input, 1, 0, 1, 2)
        sg.addWidget(self.btn_browse_input, 1, 2)

        self.chk_recursive = RecursiveCheckBox('Recursive')
        self.chk_recursive.setObjectName("RecursiveOption")
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
        source.body_layout().addLayout(self.quick_action_row)

        run_card = CardFrame('Conversion')
        # Nested graphics effects can paint a rectangular shadow over the progress row.
        run_card.graphicsEffect().setEnabled(False)
        run_card.setProperty("sectionKind", "conversion")
        left.addWidget(run_card)
        run_layout = run_card.body_layout()

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_run = NeonCTAButton('Run')
        self.btn_run.setFixedWidth(132)
        self.btn_run.setFixedHeight(40)
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch(1)
        run_layout.addLayout(btn_row)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(20)
        self._progress_opacity = QtWidgets.QGraphicsOpacityEffect(self.bar)
        self._progress_opacity.setOpacity(0.0)
        self.bar.setGraphicsEffect(self._progress_opacity)
        self._progress_fade = QtCore.QPropertyAnimation(self._progress_opacity, b"opacity", self)
        self._progress_fade.finished.connect(self._finish_visibility_fades)
        self.bar.hide()
        self.status_line = QtWidgets.QLabel('Ready.')
        self.status_line.setObjectName('StatusLine')
        self.result_detail = QtWidgets.QLabel()
        self.result_detail.setObjectName('ResultDetail')
        self.result_detail.setWordWrap(True)
        self._detail_opacity = QtWidgets.QGraphicsOpacityEffect(self.result_detail)
        self._detail_opacity.setOpacity(0.0)
        self.result_detail.setGraphicsEffect(self._detail_opacity)
        self._detail_fade = QtCore.QPropertyAnimation(self._detail_opacity, b"opacity", self)
        self._detail_fade.finished.connect(self._finish_visibility_fades)
        self.result_detail.hide()
        self._completion_display_state = "none"
        self._completion_idle_timer = QtCore.QTimer(self)
        self._completion_idle_timer.setSingleShot(True)
        self._completion_idle_timer.setInterval(COMPLETION_DISPLAY_MS)
        self._completion_idle_timer.timeout.connect(self._fade_completed_display)
        run_layout.addWidget(self.bar)
        run_layout.addWidget(self.status_line)
        run_layout.addWidget(self.result_detail)

        sage_card = CardFrame('Sage')
        sage_card.setProperty("sageCard", True)
        left.addWidget(sage_card)
        sage_layout = QtWidgets.QVBoxLayout()
        sage_layout.setContentsMargins(0, 0, 0, 0)
        sage_layout.setSpacing(4)
        sage_card.body_layout().addLayout(sage_layout)

        self.btn_sage = NeonRippleIconButton()
        self.btn_sage.setToolTip(SAGE_TOOLTIP)
        self._apply_sage_artwork()
        sage_layout.addWidget(self.btn_sage, 0, QtCore.Qt.AlignHCenter)
        self.btn_sage.clicked.connect(self._open_sage_popup)
        sage_hint = QtWidgets.QLabel('Open Image Generation Tools')
        sage_hint.setObjectName('SageHint')
        sage_hint.setAlignment(QtCore.Qt.AlignHCenter)
        sage_hint.setWordWrap(True)
        sage_hint.setFixedWidth(400)
        sage_layout.addWidget(sage_hint, 0, QtCore.Qt.AlignHCenter)
        left.addStretch(1)
        _yield_to_startup_splash()

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
        self.btn_settings_archive_storage = QtWidgets.QPushButton("Archive Settings")
        self.btn_settings_image = QtWidgets.QPushButton("Images")
        self.btn_settings_themes = QtWidgets.QPushButton("Themes")
        for b in (self.btn_settings_archive_storage, self.btn_settings_image, self.btn_settings_themes):
            b.setObjectName("SettingsNavButton")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setMinimumHeight(38)
            b.setMinimumWidth(136)
            nav_layout.addWidget(b)
        nav_layout.addStretch(1)
        upper_layout.addWidget(nav,0)

        self.settings_stack = QtWidgets.QStackedWidget()
        upper_layout.addWidget(self.settings_stack,1)
        self.settings_stack.currentChanged.connect(self._sync_settings_navigation)

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
        archive_grid.setColumnStretch(1, 1)
        archive_card.body_layout().addLayout(archive_grid)

        self.lbl_archive_storage_root = QtWidgets.QLabel(str(self.paths.storage_root))
        self.lbl_archive_storage_root.setObjectName("FixedOutPath")
        self.lbl_archive_storage_root.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_archive_storage_root.setWordWrap(True)

        self.lbl_source_images_dir = QtWidgets.QLabel(str(self.paths.images_dir))
        self.lbl_source_images_dir.setObjectName("FixedOutPath")
        self.lbl_source_images_dir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_source_images_dir.setWordWrap(True)

        self.lbl_generated_icons_dir = QtWidgets.QLabel(str(self.paths.icons_dir))
        self.lbl_generated_icons_dir.setObjectName("FixedOutPath")
        self.lbl_generated_icons_dir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_generated_icons_dir.setWordWrap(True)

        self.btn_open_archive_storage_root = QtWidgets.QPushButton("Open Archive Storage Root")
        self.btn_open_archive_storage_root.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_change_archive_storage = QtWidgets.QPushButton("Change Archive Storage Location")
        self.btn_change_archive_storage.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_launch_tray_at_startup = RecursiveCheckBox("Launch Tray at Startup")
        self.chk_launch_tray_at_startup.setChecked(GenOps.load_launch_tray_at_startup(self._settings))
        self.chk_promote_image_copies = RecursiveCheckBox("Rename copies when the original is deleted")
        self.chk_promote_image_copies.setChecked(GenOps.load_promote_image_copies(self._settings))
        self.chk_promote_image_copies.setToolTip("Promote the first remaining copy in Icon Images to the original name.")
        self.chk_remove_extra_image_copies = RecursiveCheckBox("Remove extra copies at startup and exit")
        self.chk_remove_extra_image_copies.setChecked(GenOps.load_remove_extra_image_copies(self._settings))
        self.chk_remove_extra_image_copies.setToolTip("When a numbered copy exists, delete all copies if the original exists.")
        archive_grid.addWidget(QtWidgets.QLabel("Archive Storage Root"), 0, 0)
        archive_grid.addWidget(self.lbl_archive_storage_root, 0, 1)
        archive_grid.addWidget(QtWidgets.QLabel("Source Images Folder"), 1, 0)
        archive_grid.addWidget(self.lbl_source_images_dir, 1, 1)
        archive_grid.addWidget(QtWidgets.QLabel("Generated Icons Folder"), 2, 0)
        archive_grid.addWidget(self.lbl_generated_icons_dir, 2, 1)
        path_actions = QtWidgets.QHBoxLayout()
        path_actions.setSpacing(8)
        path_actions.addWidget(self.btn_open_archive_storage_root)
        path_actions.addWidget(self.btn_change_archive_storage)
        path_actions.addStretch(1)
        archive_grid.addLayout(path_actions, 3, 0, 1, 2)

        maintenance_card = CardFrame("Archive Maintenance")
        if maintenance_card.title_label is not None:
            maintenance_card.title_label.setObjectName("SettingsSectionTitle")
        archive_layout.addWidget(maintenance_card)
        for option in (
            self.chk_launch_tray_at_startup,
            self.chk_promote_image_copies,
            self.chk_remove_extra_image_copies,
        ):
            maintenance_card.body_layout().addWidget(option)

        archive_layout.addStretch(1)
        _yield_to_startup_splash()

        img_page = QtWidgets.QWidget()
        img_layout = QtWidgets.QVBoxLayout(img_page)
        img_layout.setContentsMargins(0,0,0,0)
        img_layout.setSpacing(10)
        self.settings_stack.addWidget(img_page)

        opt = CardFrame('Images')
        if opt.title_label is not None:
            opt.title_label.setObjectName("SettingsSectionTitle")
        img_layout.addWidget(opt)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        opt.body_layout().addLayout(g)

        self.cmb_quality_min = ImageSettingsComboBox()
        self.cmb_quality_max = ImageSettingsComboBox()
        for combo in (self.cmb_quality_min, self.cmb_quality_max):
            combo.addItems([str(size) for size in eng.QUALITY_SIZES])
            combo.setFixedWidth(124)
        self.set_quality_preset("16-256")
        self.cmb_quality_min.currentIndexChanged.connect(self._quality_min_changed)
        self.cmb_quality_max.currentIndexChanged.connect(self._quality_max_changed)
        self.chk_overwrite = RecursiveCheckBox("Overwrite Mode")
        self.chk_overwrite.setChecked(True)
        self.chk_overwrite.setToolTip(
            "Rebuild the selected image's existing icon. Other images found in its folder only create missing icons."
        )
        self.cmb_padding = ImageSettingsComboBox()
        self.cmb_padding.addItems(list(eng.PADDING_PRESETS.keys()))
        self.cmb_padding.setCurrentText("None")
        self.cmb_padding.setFixedWidth(220)
        quality_label = QtWidgets.QLabel("ICO Sizes")
        quality_label.setToolTip("Choose the smallest and largest frames in the Windows icon.")
        g.addWidget(quality_label, 0, 0)
        quality_controls = QtWidgets.QHBoxLayout()
        quality_controls.setSpacing(8)
        quality_controls.addWidget(QtWidgets.QLabel("Min"))
        quality_controls.addWidget(self.cmb_quality_min)
        quality_controls.addWidget(QtWidgets.QLabel("Max"))
        quality_controls.addWidget(self.cmb_quality_max)
        quality_controls.addStretch(1)
        g.addLayout(quality_controls, 0, 1)
        quality_hint = QtWidgets.QLabel("Frames: 8–256 px, including standard sizes in the selected range. Source images keep their original resolution.")
        quality_hint.setObjectName("SettingHint")
        quality_hint.setWordWrap(True)
        g.addWidget(quality_hint, 1, 1)
        g.addWidget(QtWidgets.QLabel("Padding"), 2, 0)
        g.addWidget(self.cmb_padding, 2, 1)
        g.addWidget(self.chk_overwrite, 3, 0, 1, 2)

        accounts_card = CardFrame("Image Generator Sign-In")
        if accounts_card.title_label is not None:
            accounts_card.title_label.setObjectName("SettingsSectionTitle")
        account_actions = QtWidgets.QHBoxLayout()
        account_actions.setSpacing(8)
        for provider in SIGN_IN_PROVIDERS:
            button = QtWidgets.QPushButton(provider)
            button.setObjectName("ProviderSignInButton")
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setMinimumHeight(48)
            button.clicked.connect(lambda _checked=False, name=provider: self._open_generator_window("sign_in", name))
            self._sign_in_buttons[provider] = button
            account_actions.addWidget(button, 1)
        accounts_card.body_layout().addLayout(account_actions)
        img_layout.addWidget(accounts_card)
        self._refresh_sign_in_buttons()

        img_layout.addStretch(1)
        _yield_to_startup_splash()

        themes_page = QtWidgets.QWidget()
        themes_layout = QtWidgets.QVBoxLayout(themes_page)
        themes_layout.setContentsMargins(0, 0, 0, 0)
        themes_layout.setSpacing(10)
        self.settings_stack.addWidget(themes_page)
        themes_card = CardFrame("Themes")
        if themes_card.title_label is not None:
            themes_card.title_label.setObjectName("SettingsSectionTitle")
        themes_layout.addWidget(themes_card)
        self.theme_buttons: dict[str, QtWidgets.QRadioButton] = {}
        for appearance in APPEARANCES:
            button = QtWidgets.QRadioButton(appearance)
            button.setObjectName("ThemeOption")
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setChecked(self._theme.preference == appearance)
            button.toggled.connect(
                lambda checked, selected=appearance: self._theme.select(selected) if checked else None
            )
            themes_card.body_layout().addWidget(button)
            self.theme_buttons[appearance] = button
        themes_layout.addStretch(1)

        self.btn_settings_archive_storage.clicked.connect(lambda: self.settings_stack.setCurrentIndex(0))
        self.btn_settings_image.clicked.connect(lambda: self.settings_stack.setCurrentIndex(1))
        self.btn_settings_themes.clicked.connect(lambda: self.settings_stack.setCurrentIndex(2))
        self.settings_stack.setCurrentIndex(0)
        self._sync_settings_navigation(0)
        self.view_stack.setCurrentWidget(self.page_main)
        self._sync_view_chrome()


    def _apply_sage_artwork(self) -> None:
        path = SAGE_BUTTON_IMAGE_LIGHT_PATH if self._theme.appearance == "Light" else SAGE_BUTTON_IMAGE_PATH
        self.btn_sage.set_icon_from_png(path)

    def _apply_theme(self) -> None:
        f = self.font()
        if IS_WINDOWS:
            f.setFamily("Segoe UI Variable")
        f.setPointSize(10)
        self.setFont(f)

        self.setStyleSheet(theme_css(TITLE_BAR_CSS + r"""
        QMainWindow {
            background: transparent;
        }
        #Hero {
            border-radius: 14px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #132641, stop:0.55 #191c3b, stop:1 #28162f);
            border: 1px solid #354861;
            border-bottom: 1px solid #2b758b;
        }
        #AppMark {background: transparent;
        }
        #HeroTitle { color: #f2f6fc; font-size: 21px; font-weight: 600; }
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
            border-radius: 14px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #152139, stop:1 #0e172b);
            border: 1px solid #38657c;
            border-top: 1px solid #4b9bb1;
        }
        #Card[sectionKind="conversion"] {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #12283c, stop:1 #0e1b30);
        }
        #Card[sageCard="true"] {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #11283d, stop:0.58 #171d3a, stop:1 #25152f);
        }
        #CardTitle {
            color: #f1f6fd;
            font-weight: 600;
            font-size: 15px;
        }
        #SettingsSectionTitle {
            color: #e9f2fb;
            font-weight: 600;
            font-size: 13px;
        }
        #StatusLine {
            color: #d2e0ef;
            font-weight: 600;
        }
        #ResultDetail {
            color: #b9c9da;
            font-size: 11px;
        }
        #SageHint {
            color: #e4ad62;
            font-size: 14px;
            font-weight: 600;
        }
        #SettingHint {
            color: #b7c5d7;
            font-size: 12px;
        }
        QLabel#VersionWatermark {
            padding: 0 2px;
            background: transparent;
            color: #8493a8;
            font-size: 10px;
            font-weight: 500;
        }
        #FixedOutPath {
            border-radius: 8px;
            border: 1px solid #263248;
            padding: 8px 10px;
            background-color: #0b1423;
            color: #dbe5f2;
        }

        QLabel { color: #dbe5f2; }
        QCheckBox { color: #dbe5f2; font-weight: 500; }
        QCheckBox::indicator { width: 18px; height: 18px; }

        QLineEdit, QPlainTextEdit, QComboBox, QListWidget {
            border-radius: 9px;
            border: 1px solid #273348;
            padding: 8px 10px;
            background-color: #0b1423;
            color: #e3ebf5;
        }
        QLineEdit:focus, QComboBox:focus {
            border: 1px solid #36c9e8;
            background-color: #101d2d;
        }
        QComboBox QAbstractItemView {
            background-color: #101827;
            color: #e3ebf5;
            selection-background-color: #23465a;
            selection-color: #ffffff;
        }
        QComboBox#ImageSettingsCombo {
            border-radius: 10px;
            padding: 7px 40px 7px 12px;
        }
        QComboBox#ImageSettingsCombo:hover {
            border-color: #4b9bb1;
        }
        QComboBox#ImageSettingsCombo::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 32px;
            border: none;
            border-left: 1px solid #273348;
            border-top-right-radius: 9px;
            border-bottom-right-radius: 9px;
            background: #182b3d;
        }
        QComboBox#ImageSettingsCombo::down-arrow { image: none; }

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
            border-radius: 9px;
            padding: 8px 12px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #263851, stop:1 #18243a);
            border: 1px solid #40516a;
            color: #e0ebf8;
            font-weight: 600;
        }
        QPushButton:hover { border-color: #4caac4; background-color: #264359; }
        QPushButton:pressed { background-color: #173144; }

        QPushButton:disabled {
            background-color: #131c2a;
            color: #738298;
            border: 1px solid #222d3e;
        }
        QPushButton#QuickActionButton {
            padding: 6px 10px;
            font-size: 11px;
            font-weight: 500;
            background-color: #152031;
        }
        QPushButton#QuickActionButton:hover {
            border-color: rgba(54,201,232,0.45);
            background-color: #1b3445;
        }
        QPushButton#ProviderSignInButton {
            background: #101827;
            color: #dbe5f2;
            border: 1px solid #273348;
            border-radius: 9px;
        }
        QPushButton#ProviderSignInButton:hover { border-color: #36c9e8; }
        QPushButton#ProviderSignInButton[signedIn="true"] {
            background: #36c9e8;
            color: #080d19;
            border-color: #36c9e8;
            font-weight: 600;
        }
        QPushButton#SettingsNavButton {
            font-size: 12px;
            padding: 8px 12px;
            text-align: left;
        }
        QPushButton#SettingsNavButton:checked {
            color: #ecfaff;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #164660, stop:1 #252d55);
            border: 1px solid #4aa5bb;
        }

        #NeonCTA {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #70e4f3, stop:0.48 #35c3e1, stop:1 #168daf);
            border: 1px solid #8be7f3;
            color: #071b26;
            font-weight: 700;
        }
        #NeonCTA:hover:enabled {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #a0f1f6, stop:1 #2bb5d4);
        }
        #NeonCTA:disabled {
            background-color: #173745;
            border: 1px solid #275060;
            color: #8aa9b4;
        }
        #NeonCTA[cancelMode="true"] {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #244358, stop:1 #162d44);
            border: 1px solid #5db4cb;
            color: #e7f6fa;
        }

        QProgressBar {
            border-radius: 7px;
            border: 1px solid #273348;
            text-align: center;
            background-color: #0b1423;
            color: #a9b8ca;
        }
        QProgressBar::chunk {
            border-radius: 7px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #2daccf, stop:1 #69e2eb);
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
            border-radius: 9px;
            padding: 7px 10px;
            font-weight: 600;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #263851, stop:1 #17243a);
            border: 1px solid #40516a;
            color: #dbe5f2;
        }
        #ModeImageBtn:checked, #ModeFolderBtn:checked {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #f2ce75, stop:1 #c89236);
            border: 1px solid #f4d787;
            color: #19202b;
        }
        QRadioButton#ThemeOption { color: #dbe5f2; font-size: 13px; padding: 8px; }
        QRadioButton#ThemeOption::indicator { width: 16px; height: 16px; background: #0b1423; border: 1px solid #4b9bb1; border-radius: 8px; }
        QRadioButton#ThemeOption::indicator:checked { background: #36c9e8; border-color: #36c9e8; }
        QRadioButton#ThemeOption:checked { color: #36c9e8; font-weight: 600; }
        """))
        self._apply_sage_artwork()

    def _wire(self) -> None:
        self.btn_browse_input.clicked.connect(self._browse_input)  # type: ignore[arg-type]

        self.edit_input.pathDropped.connect(self._set_input)  # type: ignore[arg-type]

        self.btn_open_archive_storage_root.clicked.connect(lambda: _open_path(str(self.paths.storage_root)))
        self.btn_change_archive_storage.clicked.connect(self._change_archive_storage_location)  # type: ignore[arg-type]
        self.btn_open_current_source_image.clicked.connect(self._open_current_source_image)
        self.btn_open_current_generated_icon.clicked.connect(self._open_current_generated_icon)
        self.chk_launch_tray_at_startup.toggled.connect(self._set_launch_tray_at_startup)
        self.chk_promote_image_copies.toggled.connect(
            lambda enabled: self._save_copy_preference(GenOps.PROMOTE_IMAGE_COPIES_KEY, enabled)
        )
        self.chk_remove_extra_image_copies.toggled.connect(
            lambda enabled: self._save_copy_preference(GenOps.REMOVE_EXTRA_IMAGE_COPIES_KEY, enabled)
        )
        self.title_bar.btn_nav.clicked.connect(self._toggle_settings_view)
        self.view_stack.currentChanged.connect(lambda _: self._sync_view_chrome())

        self.btn_run.clicked.connect(self._run_convert)  # type: ignore[arg-type]

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

    def _refresh_sign_in_buttons(self) -> None:
        self._settings.sync()
        for provider, button in self._sign_in_buttons.items():
            signed_in = sign_in_status(self._settings, provider) == "signed_in"
            button.setToolTip(f"{provider}: {'Signed in' if signed_in else 'Sign in'}")
            if button.property("signedIn") != signed_in:
                button.setProperty("signedIn", signed_in)
                button.style().unpolish(button)
                button.style().polish(button)

    def _sync_sign_in_status_monitor(self) -> None:
        active = (self.view_stack.currentWidget() is self.page_settings
                  and self.settings_stack.currentIndex() == 1)
        if active:
            self._refresh_sign_in_buttons()
            self._sign_in_status_timer.start()
        else:
            self._sign_in_status_timer.stop()

    def _set_launch_tray_at_startup(self, enabled: bool) -> None:
        """Persist and apply the user's tray-at-startup preference."""
        if GenOps.apply_launch_tray_at_startup(enabled, self._settings):
            GenOps.save_launch_tray_at_startup(enabled, self._settings)
            state = "enabled" if enabled else "disabled"
            self._log(f"Tray startup {state}.")
            return

        blocker = QtCore.QSignalBlocker(self.chk_launch_tray_at_startup)
        self.chk_launch_tray_at_startup.setChecked(not enabled)
        del blocker
        QtWidgets.QMessageBox.warning(
            self,
            APP_DISPLAY_NAME,
            "Could not update the Windows startup setting for the tray.",
        )

    def _save_copy_preference(self, key: str, enabled: bool) -> None:
        self._settings.setValue(key, enabled)
        self._settings.sync()
        if key == GenOps.PROMOTE_IMAGE_COPIES_KEY and enabled:
            self._run_archive_maintenance_now("copy-promotion-enabled")

    def _sync_view_chrome(self) -> None:
        in_settings = self.view_stack.currentWidget() is self.page_settings
        self.title_bar.set_nav_text("←" if in_settings else "⚙")
        self.title_bar.btn_nav.setToolTip("Back" if in_settings else "Settings")
        self.title_bar.btn_nav.setAccessibleName("Back" if in_settings else "Settings")
        self.title_bar.sync_state()
        self._update_window_shape()
        self._sync_sign_in_status_monitor()

    def _sync_settings_navigation(self, index: int) -> None:
        self.btn_settings_archive_storage.setChecked(index == 0)
        self.btn_settings_image.setChecked(index == 1)
        self.btn_settings_themes.setChecked(index == 2)
        self._sync_sign_in_status_monitor()

    def _sync_theme_selection(self, preference: str) -> None:
        for appearance, button in self.theme_buttons.items():
            blocker = QtCore.QSignalBlocker(button)
            button.setChecked(appearance == preference)
            del blocker

    def set_quality_preset(self, preset: str) -> None:
        minimum, maximum = eng.quality_range(preset)
        min_blocker = QtCore.QSignalBlocker(self.cmb_quality_min)
        max_blocker = QtCore.QSignalBlocker(self.cmb_quality_max)
        self.cmb_quality_min.setCurrentText(str(minimum))
        self.cmb_quality_max.setCurrentText(str(maximum))
        del min_blocker, max_blocker

    def quality_preset(self) -> str:
        return f"{self.cmb_quality_min.currentText()}-{self.cmb_quality_max.currentText()}"

    def _quality_min_changed(self, _index: int) -> None:
        if int(self.cmb_quality_min.currentText()) > int(self.cmb_quality_max.currentText()):
            self.cmb_quality_max.setCurrentText(self.cmb_quality_min.currentText())

    def _quality_max_changed(self, _index: int) -> None:
        if int(self.cmb_quality_max.currentText()) < int(self.cmb_quality_min.currentText()):
            self.cmb_quality_min.setCurrentText(self.cmb_quality_max.currentText())

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
        self._set_archive_watch_paths(set(GenOps.build_archive_storage_watch_paths(self.paths)))

    def _set_archive_watch_paths(self, desired_dirs: set[str]) -> None:
        existing_dirs = set(self._archive_fs_watcher.directories())

        remove_dirs = list(existing_dirs - desired_dirs)
        if remove_dirs:
            self._archive_fs_watcher.removePaths(remove_dirs)

        add_dirs = [path for path in desired_dirs - existing_dirs if Path(path).exists()]
        if add_dirs:
            self._archive_fs_watcher.addPaths(add_dirs)

    def _on_archive_storage_fs_changed(self, changed_path: str) -> None:
        try:
            Path(changed_path).resolve().relative_to(self.paths.icons_dir.resolve())
        except Exception:
            pass
        else:
            cleanup = GenOps.clean_generated_icons_dir(paths=self.paths, logfn=lambda s: self._log(s))
            if cleanup.moved_images or cleanup.deleted_files:
                self._rebuild_archive_watch_paths()
        promoted, _ = GenOps.reconcile_image_copies(
            paths=self.paths, directory=Path(changed_path), logfn=self._log
        )
        if promoted:
            self._run_archive_maintenance_now("copy-promotion")
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

    def _on_archive_refresh_finished(self, generation: int, snapshots: object, watch_paths: object) -> None:
        self._clear_archive_refresh_worker()
        if generation != self._archive_refresh_generation:
            return

        try:
            snapshot_list = list(snapshots)
            signatures = {
                str(entry.path): (entry.signature_size, entry.signature_mtime)
                for entry in snapshot_list
            }
            previous = self._archive_image_signatures
            changed_candidates = {
                path: signature
                for path, signature in signatures.items()
                if previous is None or previous.get(path) != signature
            }
            self._archive_auto_convert_candidates.update(changed_candidates)
            if changed_candidates:
                self._archive_auto_convert_timer.start()
            self._archive_image_signatures = signatures
            self.archive_sidebar.apply_snapshot(
                snapshot_list,
                archive_sources_dir=self.paths.images_dir,
                archive_icons_dir=self.paths.icons_dir,
            )
            self._set_archive_watch_paths(
                set(watch_paths)
                | {str(Path(entry.path).parent) for entry in snapshot_list}
            )
            if self.archive_sidebar.current_path() is None:
                self._sync_archive_selection_from_input()
            self._update_workflow_actions()
        except Exception as exc:
            self._log(f"WARN: Archive refresh apply failed: {type(exc).__name__}: {exc}", "WARN")

        if self._archive_refresh_pending:
            self._refresh_archive_view(immediate=True)

    def _begin_watched_image_conversion(self) -> None:
        if self._archive_refresh_running or self._archive_refresh_pending:
            self._archive_auto_convert_timer.start()
            return
        if self._run_in_progress:
            self._archive_auto_convert_pending = True
            return

        self._archive_auto_convert_pending = False
        candidates = self._archive_auto_convert_candidates
        self._archive_auto_convert_candidates = {}
        changed_images: list[Path] = []
        for path_text, signature in candidates.items():
            source = Path(path_text)
            try:
                source.resolve().relative_to(self.paths.images_dir.resolve())
                source_stat = source.stat()
                if (source_stat.st_size, source_stat.st_mtime) != signature:
                    self._refresh_archive_view()
                    continue
                icon = eng.archive_icon_path_for_source_image(source, paths=self.paths)
                if not icon.exists() or icon.stat().st_mtime_ns < source_stat.st_mtime_ns:
                    changed_images.append(source)
            except (OSError, ValueError):
                continue
        if changed_images:
            self._run_archive_maintenance_now("watched-image", changed_images=changed_images)

    def _start_pending_watched_image_conversion(self) -> None:
        if self._archive_auto_convert_pending and not self._run_in_progress:
            self._archive_auto_convert_pending = False
            self._archive_auto_convert_timer.start(0)

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
            self.chk_overwrite.setEnabled(False)
            self.btn_browse_input.setText("Select Folder...")
            self.edit_input.setPlaceholderText("Select Folder...")
        else:
            self.chk_recursive.setChecked(False)
            self.chk_recursive.setVisible(False)
            self.chk_recursive.setEnabled(False)
            self.chk_overwrite.setEnabled(True)
            self.btn_browse_input.setText("Select Image...")
            self.edit_input.setPlaceholderText("Select Image...")
        self._update_workflow_actions()

    def _update_run_state(self) -> None:
        self._update_workflow_actions()
        if self._run_in_progress:
            cancellable = self._operation_kind == "conversion"
            if self._operation_thread is not None:
                cancellable = cancellable and not self._operation_thread.isInterruptionRequested()
            self.btn_run.setEnabled(cancellable)
            return

        inp_txt = self.edit_input.text().strip()

        if not inp_txt:
            self.btn_run.setEnabled(False)
            return

        p = Path(inp_txt)
        if not p.exists():
            self.btn_run.setEnabled(False)
            return

        if not (p.is_dir() or _is_image_file(p)):
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
        if self._run_in_progress:
            self._log("Archive import is unavailable during conversion.", "WARN")
            return
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
        self._open_archive_image(source)

    def _open_sage_popup(self) -> None:
        self._open_generator_window("generator")

    def _open_generator_window(self, action: str, provider: str = "ChatGPT", retries: int = 0) -> None:
        if send_generator_command(action, provider):
            return
        if IS_WINDOWS:
            import ctypes

            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = ctypes.c_void_p
            handle = user32.FindWindowW(None, "IconForge · Image Generation")
            if handle and action == "generator":
                user32.ShowWindow(ctypes.c_void_p(handle), 9)
                user32.SetForegroundWindow(ctypes.c_void_p(handle))
                return
        if self._sage_process is not None and self._sage_process.poll() is None:
            if retries < 20:
                QtCore.QTimer.singleShot(
                    250, lambda: self._open_generator_window(action, provider, retries + 1)
                )
                return
            message = "The image generator is already open but is not responding to sign-in requests. Close and reopen it to use Sign In."
            self._log(message, "WARN")
            QtWidgets.QMessageBox.warning(self, APP_DISPLAY_NAME, message)
            return
        if IS_WINDOWS and handle:
            message = "Close the open image generator and try Sign In again so it can use the updated sign-in panel."
            self._log(message, "WARN")
            QtWidgets.QMessageBox.warning(self, APP_DISPLAY_NAME, message)
            return
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--mode", "generator"]
            cwd = Path(sys.executable).resolve().parent
        else:
            command = [sys.executable, str(APP_DIR / "IconMakerMaster.py"), "--mode", "generator"]
            cwd = APP_DIR
        if action == "sign_in":
            command.extend(["--sign-in", "--provider", provider])
        try:
            self._sage_process = subprocess.Popen(command, cwd=cwd, close_fds=True)
            if IS_WINDOWS:
                try:
                    ctypes.windll.user32.AllowSetForegroundWindow(self._sage_process.pid)
                except (AttributeError, OSError):
                    pass
        except OSError as exc:
            self._log(f"Image generator could not open: {exc}", "ERR")
            QtWidgets.QMessageBox.warning(self, APP_DISPLAY_NAME, f"Image generator could not open:\n\n{exc}")

    def _open_current_generated_icon(self) -> None:
        icon_path = self._current_generated_icon_path()
        if icon_path is None:
            self._log("WARN: No generated icon is available for the current archive image.", "WARN")
            return
        _open_path(str(icon_path))

    def _open_archive_image(self, path: Path) -> None:
        source = Path(path)
        if not source.is_file():
            self._log(f"WARN: Source image is unavailable: {source}", "WARN")
            return
        viewer = ArchiveImageViewer(source, self)
        self._image_viewers.add(viewer)
        viewer.destroyed.connect(lambda _obj=None: self._image_viewers.discard(viewer))
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

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
        ico = self._archive_icon_path_for_image(p)
        QtWidgets.QApplication.clipboard().setText(f'Image: {p}\nIcon: "{ico}"')
        self._log(f"Copied image and icon paths: {p.name}")

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
            try:
                copy_record(p, dst)
            except (OSError, ValueError) as exc:
                self._log(f"WARN: Could not copy image prompt details: {exc}", "WARN")
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
        if "/" in new_stem or "\\" in new_stem:
            QtWidgets.QMessageBox.warning(self, "Rename Image", "Enter a name without path separators.")
            return

        try:
            desired = p.with_name(f"{new_stem}{p.suffix.lower()}")
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Rename Image", str(exc))
            return
        if desired == p:
            return

        if desired.exists():
            QtWidgets.QMessageBox.warning(self, "Rename Image", f"A file already exists with that name:\n\n{desired.name}")
            return

        try:
            old_icon = self._archive_icon_path_for_image(p)
            new_icon = self._archive_icon_path_for_image(desired)
            p.rename(desired)
            try:
                move_record(p, desired)
            except (OSError, ValueError) as exc:
                self._log(f"WARN: Could not move image prompt details: {exc}", "WARN")
            if old_icon.exists() and old_icon != new_icon:
                new_icon.parent.mkdir(parents=True, exist_ok=True)
                old_icon.replace(new_icon)
            self.archive_sidebar.rename_item_path(p, desired)
            self._log(f"Renamed image: {p.name} -> {desired.name}")
            self._set_input(str(desired))
            self._run_archive_maintenance_now("archive-rename")
        except Exception as e:
            self._log(f"ERR: Rename failed: {p} ({e})", "ERR")
            QtWidgets.QMessageBox.warning(self, "Rename Image", f"Could not rename image:\n{e}")

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
            try:
                delete_record(p)
            except OSError as exc:
                self._log(f"WARN: Could not remove image prompt details: {exc}", "WARN")
            self._log(f"Deleted image: {p.name}")
            if self.edit_input.text().strip() == str(p):
                self.edit_input.clear()
            GenOps.reconcile_image_copies(paths=self.paths, directory=p.parent, logfn=self._log)
            self._run_archive_maintenance_now("archive-delete")
        except Exception as e:
            self._log(f"ERR: Delete failed: {p} ({e})", "ERR")

    def _browse_input(self) -> None:
        mode = self.mode_seg.mode()
        dialog = ThemedFileDialog(
            self,
            "Choose Your Folder." if mode == "folder" else "Choose Your Image.",
            str(self.paths.images_dir),
            folder=mode == "folder",
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            selected = dialog.selectedFiles()
            if selected:
                self._set_input(selected[0])

    def _run_archive_maintenance_now(self, reason: str, *, changed_images: list[Path] | None = None) -> None:
        if self._run_in_progress:
            return
        self._log(f"=== MAINTENANCE ({reason}) ===")
        paths = self.paths
        changed = tuple(changed_images or ())
        sizes = preset_sizes(self.quality_preset())
        padding_mode = self.cmb_padding.currentText()
        self._start_operation(
            "maintenance",
            lambda _progress, log, _cancelled: GenOps.run_archive_maintenance(
                paths=paths,
                overwrite=False,
                sizes=sizes,
                padding_mode=padding_mode,
                autocrop=False,
                logfn=log,
                changed_images=changed,
            ),
            f"Updating archive... ({reason})",
        )

    def _poll_app_events(self, force: bool = False) -> None:
        event = GenOps.latest_app_event()
        if not force and event.seq <= self._last_seen_event_seq:
            return
        self._last_seen_event_seq = event.seq
        if not force and event.event_type == "quit-all":
            self.close()
            return
        if force or event.event_type in {
            "archive-storage-changed",
            "archive-storage-maintained",
            "archive-storage-relocated",
            "conversion-finished",
        }:
            self._refresh_archive_view()

    def _change_archive_storage_location(self) -> None:
        if self._run_in_progress:
            return
        start_dir = str(getattr(self, "archive_storage_root", Path.home()))
        dialog = ThemedFileDialog(self, "Choose Your Folder.", start_dir, folder=True)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        selected = dialog.selectedFiles()
        if not selected:
            return
        picked = selected[0]
        new_root = Path(picked).resolve()
        old_root = Path(self.archive_storage_root).resolve()
        if new_root == old_root:
            self._log("Archive storage location unchanged.", "WARN")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            APP_DISPLAY_NAME,
            (
                f"Copy archive storage from:\n{old_root}\n\nSwitch to:\n{new_root}\n\n"
                "Tray and background activity will pause during relocation."
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        dlg = QtWidgets.QProgressDialog("Relocating archive storage...", "Cancel", 0, 100, self)
        dlg.setWindowTitle(APP_DISPLAY_NAME)
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
            QtWidgets.QMessageBox.critical(self, APP_DISPLAY_NAME, f"Relocation failed:\n{result.message}")
            return

        GenOps.save_archive_storage_root(new_root, self._settings)
        self._set_archive_storage_paths(new_root)
        try:
            copy_archive_records(old_images_dir, self.paths.images_dir)
        except (OSError, ValueError) as exc:
            self._log(f"WARN: Could not copy all image prompt details: {exc}", "WARN")
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
        QtWidgets.QMessageBox.information(
            self,
            APP_DISPLAY_NAME,
            f"Archive storage moved to:\n{new_root}\n\nThe old Icon Images folder remains at:\n{old_images_dir}",
        )

    def _animate_visibility(
        self,
        widget: QtWidgets.QWidget,
        effect: QtWidgets.QGraphicsOpacityEffect,
        animation: QtCore.QPropertyAnimation,
        show: bool,
    ) -> None:
        animation.stop()
        if show:
            widget.show()
        animation.setDuration(240 if show else 450)
        animation.setStartValue(effect.opacity())
        animation.setEndValue(1.0 if show else 0.0)
        animation.start()

    def _finish_visibility_fades(self) -> None:
        if self._progress_opacity.opacity() <= 0.01 and not self._run_in_progress:
            self.bar.hide()
        if self._detail_opacity.opacity() <= 0.01 and not self._run_in_progress:
            self.result_detail.hide()
        if (
            self._completion_display_state == "fading"
            and self.bar.isHidden()
        ):
            self._completion_display_state = "compact"

    def _clear_completion_display(self) -> None:
        self._completion_idle_timer.stop()
        self._completion_display_state = "none"
        self._progress_fade.stop()
        self._detail_fade.stop()
        self._progress_opacity.setOpacity(0.0)
        self._detail_opacity.setOpacity(0.0)
        self.bar.hide()
        self.result_detail.hide()
        self.result_detail.clear()

    def _show_completion(self, status: str, detail: str) -> None:
        self.status_line.setText(status)
        self.result_detail.setText(detail)
        self._completion_display_state = "visible"
        if detail:
            self._detail_opacity.setOpacity(0.0)
            self._animate_visibility(
                self.result_detail, self._detail_opacity, self._detail_fade, True
            )
        self._completion_idle_timer.start()

    def _fade_completed_display(self) -> None:
        if self._run_in_progress or self._completion_display_state != "visible":
            return
        self._completion_display_state = "fading"
        self._animate_visibility(self.bar, self._progress_opacity, self._progress_fade, False)

    def _start_operation(self, kind: str, work: Callable[..., object], status: str) -> None:
        if self._run_in_progress:
            return
        self._clear_completion_display()
        self._run_in_progress = True
        self._operation_kind = kind
        self._operation_result = None
        self._operation_error = None
        self.status_line.setText(status)
        if kind == "conversion":
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            self._animate_visibility(self.bar, self._progress_opacity, self._progress_fade, True)
        self._set_run_ui_enabled(False)

        thread = QtCore.QThread(self)
        worker = OperationWorker(work)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_operation_progress)
        worker.message.connect(self._log)
        worker.completed.connect(self._on_operation_result)
        worker.failed.connect(self._on_operation_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._finish_operation)
        self._operation_thread = thread
        self._operation_worker = worker
        thread.start()

    def _on_operation_progress(self, done: int, total: int, current: str) -> None:
        self.status_line.setText(current)
        self.bar.setValue(int((done * 100) / max(1, total)))

    def _on_operation_result(self, result: object) -> None:
        self._operation_result = result

    def _on_operation_failed(self, message: str) -> None:
        self._operation_error = message

    def _finish_operation(self) -> None:
        # Keep the PySide wrappers alive until the finished callback returns.
        # Releasing the worker mid-callback can crash Qt.
        _finished_refs = (self._operation_thread, self._operation_worker)
        kind = self._operation_kind
        result = self._operation_result
        error = self._operation_error
        self._operation_thread = None
        self._operation_worker = None
        self._operation_kind = None
        self._run_in_progress = False
        self._set_run_ui_enabled(True)
        self._update_run_state()

        if error or result is None:
            self._log(f"ERR: {kind} failed: {error or 'No result returned.'}", "ERR")
            if kind == "conversion":
                self._show_completion("Failed.", "The conversion could not finish. See the log for details.")
            else:
                self.status_line.setText(f"ERR: {kind} failed.")
        elif kind == "conversion":
            if result.ok:
                self.bar.setValue(100)
                self._show_completion("Done.", result.message)
            elif "cancel" in result.message.casefold():
                counts = []
                if result.converted:
                    counts.append(f"{result.converted} icon{'s' if result.converted != 1 else ''} created.")
                if result.skipped:
                    counts.append(f"{result.skipped} file{'s' if result.skipped != 1 else ''} skipped.")
                if result.failed:
                    counts.append(f"{result.failed} conversion{'s' if result.failed != 1 else ''} failed.")
                detail = " ".join(counts) if counts else "Conversion stopped before completion."
                self._show_completion("Cancelled.", detail)
            else:
                status = "Finished with errors." if result.converted or result.skipped else "Failed."
                self._show_completion(status, result.message.removeprefix("ERR: "))
            if result.converted > 0:
                self.archive_sidebar.expand()
        elif kind == "maintenance":
            self._log(
                "Archive maintenance done. "
                f"scanned={result.scanned} converted={result.converted} "
                f"orphans_removed={result.orphan_icons_removed} errors={result.errors}",
                "ERR" if result.errors else "INFO",
            )
            self.status_line.setText(
                f"Archive update finished with {result.errors} error(s)." if result.errors else "Ready."
            )
        elif kind == "startup":
            if (
                result.converted
                or result.deleted_orphans
                or result.mirrored_into_archive
                or result.moved_from_icons
                or result.deleted_from_icons
            ):
                self._log(
                    "Startup scan complete. "
                    f"Imported={result.mirrored_into_archive + result.moved_from_icons} "
                    f"Converted={result.converted} "
                    f"Deleted={result.deleted_orphans + result.deleted_from_icons}"
                )
            if result.errors:
                self._log(f"Archive update incomplete: {result.errors} error(s).", "ERR")
                self.status_line.setText(f"Archive update incomplete: {result.errors} error(s).")
            else:
                self.status_line.setText("Ready.")

        if self._close_when_idle:
            QtCore.QTimer.singleShot(0, self.close)
            return
        self._refresh_archive_view(immediate=kind != "conversion")
        self._update_workflow_actions()
        self._start_pending_watched_image_conversion()

    def _cancel(self) -> None:
        if self._operation_kind != "conversion" or self._operation_thread is None:
            return
        if self._operation_thread.isInterruptionRequested():
            return
        self._operation_thread.requestInterruption()
        self.btn_run.setText("Cancelling…")
        self.btn_run.setEnabled(False)
        self.status_line.setText("Cancelling…")
        self._log("Cancel requested.", "WARN")

    def _set_run_ui_enabled(self, enabled: bool) -> None:
        has_input = bool(self.edit_input.text().strip())
        can_cancel = not enabled and self._operation_kind == "conversion"
        self.btn_run.setText("Cancel" if can_cancel else "Run")
        self.btn_run.setProperty("cancelMode", can_cancel)
        _repolish(self.btn_run)
        self.btn_run.setEnabled(can_cancel or (enabled and has_input))
        self.edit_input.setEnabled(enabled)
        self.btn_browse_input.setEnabled(enabled)
        self.mode_seg.setEnabled(enabled)
        self.chk_recursive.setEnabled(enabled)
        self.chk_overwrite.setEnabled(enabled and self.mode_seg.mode() == "file")
        self.cmb_quality_min.setEnabled(enabled)
        self.cmb_quality_max.setEnabled(enabled)
        self.cmb_padding.setEnabled(enabled)
        self.archive_sidebar.setEnabled(enabled)
        self.btn_change_archive_storage.setEnabled(enabled)

    def _run_convert(self) -> None:
        if self._run_in_progress:
            if self._operation_kind == "conversion":
                self._cancel()
            return

        inp_txt = self.edit_input.text().strip()
        if not inp_txt:
            self._log("ERR: No input provided.", "ERR")
            return

        inp = Path(inp_txt)
        if not inp.exists():
            self._log("ERR: Input path does not exist.", "ERR")
            return

        sizes = preset_sizes(self.quality_preset())

        padding_mode = self.cmb_padding.currentText()
        recursive = self.chk_recursive.isChecked()
        overwrite = inp.is_file() and self.chk_overwrite.isChecked()

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

        self._start_operation(
            "conversion",
            lambda progress, log, cancelled: GenOps.run_conversion(
                request,
                progress_cb=progress,
                logfn=log,
                is_cancelled=cancelled,
            ),
            "Starting...",
        )

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

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if not self._sign_in_prompt_scheduled:
            self._sign_in_prompt_scheduled = True
            QtCore.QTimer.singleShot(0, self._offer_sign_in_if_needed)

    def _offer_sign_in_if_needed(self) -> None:
        if not self.isVisible():
            return
        offer = should_offer_sign_in(self._settings)
        self._settings.setValue(APP_OPENED_KEY, True)
        self._settings.sync()
        if offer:
            self._open_generator_window("sign_in")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        try:
            self._update_window_shape()
        except Exception:
            pass
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        if self._run_in_progress:
            self._close_when_idle = True
            if self._operation_kind == "conversion":
                self._cancel()
            event.ignore()
            return
        self._completion_idle_timer.stop()
        self._progress_fade.stop()
        self._detail_fade.stop()
        if hasattr(self, "state"):
            self.state.flush_pending_save(self)
        try:
            self._events_timer.stop()
        except Exception:
            pass
        try:
            self._archive_refresh_timer.stop()
        except Exception:
            pass
        self._archive_auto_convert_timer.stop()
        try:
            if self._archive_refresh_thread is not None:
                self._archive_refresh_thread.requestInterruption()
                self._archive_refresh_thread.quit()
                if not self._archive_refresh_thread.wait(2000):
                    self._archive_refresh_thread.terminate()
                    self._archive_refresh_thread.wait(1000)
        except Exception:
            pass
        GenOps.reconcile_image_copies(paths=self.paths, on_start_or_close=True, logfn=self._log)
        super().closeEvent(event)


def main() -> None:
    from StartupSplash import run_ui as run_ui_with_splash

    run_ui_with_splash(sys.modules[__name__])

if __name__ == "__main__":
    main()
