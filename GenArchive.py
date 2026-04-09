#!/usr/bin/env python3
"""
GenArchive.py - Archive drawer UI for IconMaker.

Archive = the right-side browser for stored source images.
Archive Storage Root is a separate filesystem concept owned by Gen2/GenOps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

COLLAPSED_WIDTH = 32
EXPANDED_WIDTH = 528
PREVIEW_SIZE = 256
THUMB_SIZE = 68
GRID_COLUMNS = 3


def _repolish(widget: QtWidgets.QWidget) -> None:
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except Exception:
        widget.update()


@dataclass(slots=True)
class ArchiveEntrySnapshot:
    key: str
    path: Path
    name: str
    signature_size: int
    signature_mtime: float
    changed: bool = True
    image_size_text: str | None = None
    file_type_text: str | None = None
    file_size_text: str | None = None
    image_path_text: str | None = None
    icon_path_text: str | None = None
    created_text: str | None = None
    modified_text: str | None = None
    thumb_image: QtGui.QImage | None = None


class ArchiveHandleLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAlignment(QtCore.Qt.AlignCenter)

    def sizeHint(self):
        size = super().sizeHint()
        return QtCore.QSize(size.height(), size.width())

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QtCore.QSize(size.height(), size.width())

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.translate(self.width(), 0)
        painter.rotate(90)
        rect = QtCore.QRect(0, 0, self.height(), self.width())
        painter.drawText(rect, QtCore.Qt.AlignCenter, self.text())


class ArchivePreviewLabel(QtWidgets.QLabel):
    doubleClicked = QtCore.Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ArchivePreviewFrame(QtWidgets.QFrame):
    doubleClicked = QtCore.Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ArchiveUtilityTitleBar(QtWidgets.QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ArchivePropertiesTitleBar")
        self.setFixedHeight(30)
        self._drag_offset: QtCore.QPoint | None = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(6)

        label = QtWidgets.QLabel(title)
        label.setObjectName("ArchivePropertiesTitle")
        label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(label, 1)

        self.btn_close = QtWidgets.QToolButton()
        self.btn_close.setObjectName("ArchivePropertiesCloseButton")
        self.btn_close.setText("x")
        self.btn_close.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_close.setAutoRaise(True)
        self.btn_close.setFixedSize(26, 24)
        self.btn_close.clicked.connect(lambda: self.window().close())
        layout.addWidget(self.btn_close, 0, QtCore.Qt.AlignVCenter)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
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
            self._drag_offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & QtCore.Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class ArchivePropertiesWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Properties")
        self.setModal(False)
        self.setSizeGripEnabled(False)
        self.setObjectName("ArchivePropertiesWindow")
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        chrome = QtWidgets.QFrame()
        chrome.setObjectName("ArchivePropertiesChrome")
        outer.addWidget(chrome)

        chrome_layout = QtWidgets.QVBoxLayout(chrome)
        chrome_layout.setContentsMargins(1, 1, 1, 1)
        chrome_layout.setSpacing(0)

        self.title_bar = ArchiveUtilityTitleBar("Properties", chrome)
        chrome_layout.addWidget(self.title_bar)

        card = QtWidgets.QFrame()
        card.setObjectName("ArchivePropertiesCard")
        chrome_layout.addWidget(card)

        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        heading = QtWidgets.QLabel("Selected Image")
        heading.setObjectName("ArchivePropertiesHeading")
        layout.addWidget(heading)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid, 1)
        self._grid = grid

        self._property_labels: List[QtWidgets.QLabel] = []
        self._values: Dict[str, QtWidgets.QLabel] = {}
        property_rows = [
            ("Name:", "name"),
            ("Image Size:", "image_size"),
            ("File Type:", "file_type"),
            ("File Size:", "file_size"),
            ("Image Path:", "image_path"),
            ("Icon Path:", "icon_path"),
            ("Created:", "created"),
            ("Modified:", "modified"),
        ]
        for row, (title, key) in enumerate(property_rows):
            label = QtWidgets.QLabel(title)
            label.setObjectName("ArchivePropertyLabel")
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)
            value = QtWidgets.QLabel("")
            value.setObjectName("ArchivePropertyValue")
            value.setWordWrap(True)
            value.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            value.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            value.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard
            )
            grid.addWidget(label, row, 0, QtCore.Qt.AlignTop)
            grid.addWidget(value, row, 1)
            self._property_labels.append(label)
            self._values[key] = value

        self.setStyleSheet(
            """
            QDialog#ArchivePropertiesWindow {
                background: transparent;
            }
            QFrame#ArchivePropertiesChrome {
                background-color: #09101f;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 14px;
            }
            QFrame#ArchivePropertiesTitleBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(10, 16, 34, 0.96),
                    stop:0.55 rgba(11, 19, 52, 0.96),
                    stop:1 rgba(31, 9, 46, 0.96));
                border-top-left-radius: 13px;
                border-top-right-radius: 13px;
                border-bottom: 1px solid rgba(255,255,255,0.08);
            }
            QLabel#ArchivePropertiesTitle {
                color: rgba(234, 242, 255, 0.95);
                font-size: 11px;
                font-weight: 800;
            }
            QToolButton#ArchivePropertiesCloseButton {
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.08);
                background: rgba(255,255,255,0.05);
                color: rgba(234,242,255,235);
                font-size: 12px;
                font-weight: 900;
            }
            QToolButton#ArchivePropertiesCloseButton:hover {
                border-color: rgba(255,92,92,0.65);
                background: rgba(255,92,92,0.18);
            }
            QFrame#ArchivePropertiesCard {
                background-color: #101a31;
                border: 0;
                border-bottom-left-radius: 13px;
                border-bottom-right-radius: 13px;
            }
            QLabel#ArchivePropertiesHeading {
                color: rgba(255, 255, 255, 0.95);
                font-size: 14px;
                font-weight: 800;
                padding-bottom: 2px;
            }
            QLabel#ArchivePropertyLabel {
                color: rgba(234, 242, 255, 0.70);
                font-size: 11px;
                font-weight: 700;
                padding-top: 1px;
            }
            QLabel#ArchivePropertyValue {
                color: rgba(255, 255, 255, 0.94);
                font-size: 11px;
                background: transparent;
            }
            """
        )
        self._refresh_layout_metrics()
        self.setFixedHeight(320)

    def set_entry(self, entry: ArchiveEntrySnapshot | None) -> None:
        if entry is None:
            placeholders = {
                "name": "No archive item selected",
                "image_size": "Unavailable",
                "file_type": "Unavailable",
                "file_size": "Unavailable",
                "image_path": "Select an archive item to inspect it.",
                "icon_path": "Unavailable",
                "created": "Unavailable",
                "modified": "Unavailable",
            }
            for key, value in placeholders.items():
                self._values[key].setText(value)
            return

        values = {
            "name": entry.name or entry.path.name,
            "image_size": entry.image_size_text or "Unavailable",
            "file_type": entry.file_type_text or "Unavailable",
            "file_size": entry.file_size_text or "Unavailable",
            "image_path": entry.image_path_text or str(entry.path),
            "icon_path": entry.icon_path_text or "Unavailable",
            "created": entry.created_text or "Unavailable",
            "modified": entry.modified_text or "Unavailable",
        }
        for key, value in values.items():
            self._values[key].setText(value)
        self._refresh_layout_metrics()

    def _refresh_layout_metrics(self) -> None:
        label_width = max((label.sizeHint().width() for label in self._property_labels), default=92)
        label_width = max(104, label_width + 6)
        value_width = 252
        self._grid.setColumnMinimumWidth(0, label_width)
        self._grid.setColumnMinimumWidth(1, value_width)
        for label in self._property_labels:
            label.setMinimumWidth(label_width)
        for value in self._values.values():
            value.setMinimumWidth(value_width)

        outer_margins = self.layout().contentsMargins()
        card = self.findChild(QtWidgets.QFrame, "ArchivePropertiesCard")
        card_layout = card.layout() if card is not None else None
        card_margins = card_layout.contentsMargins() if card_layout is not None else QtCore.QMargins(12, 12, 12, 12)
        spacing = self._grid.horizontalSpacing()
        total_width = (
            outer_margins.left()
            + outer_margins.right()
            + card_margins.left()
            + card_margins.right()
            + label_width
            + spacing
            + value_width
        )
        self.setFixedWidth(max(430, total_width))


class ArchiveItem(QtWidgets.QFrame):
    clicked = QtCore.Signal(Path)
    openRequested = QtCore.Signal(Path)
    showInFolderRequested = QtCore.Signal(Path)
    duplicateRequested = QtCore.Signal(Path)
    copyImageRequested = QtCore.Signal(Path)
    copyPathRequested = QtCore.Signal(Path)
    copyIconRequested = QtCore.Signal(Path)
    renameRequested = QtCore.Signal(Path)
    deleteRequested = QtCore.Signal(Path)

    def __init__(self, path: Path, pixmap: QtGui.QPixmap, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self._selected = False

        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setContextMenuPolicy(QtCore.Qt.DefaultContextMenu)
        self.setObjectName("ArchiveItem")
        self.setAttribute(QtCore.Qt.WA_Hover, True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.thumb = QtWidgets.QLabel()
        self.thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.thumb.setMinimumHeight(THUMB_SIZE + 10)
        self.thumb.setPixmap(pixmap)
        self.thumb.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        self.name = QtWidgets.QLabel(self.path.stem)
        self.name.setAlignment(QtCore.Qt.AlignCenter)
        self.name.setWordWrap(True)
        self.name.setObjectName("ArchiveItemName")
        self.name.setToolTip(str(self.path))
        self.name.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        layout.addWidget(self.thumb)
        layout.addWidget(self.name)

        self._apply_style()

    def update_content(self, path: Path, *, pixmap: QtGui.QPixmap | None = None) -> None:
        self.path = Path(path)
        self.name.setText(self.path.stem)
        self.name.setToolTip(str(self.path))
        if pixmap is not None:
            self.thumb.setPixmap(pixmap)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "QFrame#ArchiveItem {"
                "border: 1px solid rgba(255, 191, 4, 220);"
                "border-radius: 10px;"
                "background: rgba(255, 191, 4, 36);"
                "}"
                "QLabel { background: transparent; color: rgba(255,255,255,230); }"
            )
        else:
            self.setStyleSheet(
                "QFrame#ArchiveItem {"
                "border: 1px solid rgba(255, 255, 255, 26);"
                "border-radius: 10px;"
                "background: rgba(255, 255, 255, 10);"
                "}"
                "QFrame#ArchiveItem:hover {"
                "border: 1px solid rgba(0, 220, 255, 120);"
                "background: rgba(0, 220, 255, 18);"
                "}"
                "QLabel { background: transparent; color: rgba(234,242,255,210); }"
            )

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Archive cards are selection targets. Opening the source image is
        # reserved for the preview pane to avoid accidental file launches.
        event.accept()

    def contextMenuEvent(self, event):
        self.clicked.emit(self.path)

        menu = QtWidgets.QMenu(self)
        open_action = menu.addAction("Open Image")
        show_in_folder_action = menu.addAction("Show in Folder")
        menu.addSeparator()
        duplicate_action = menu.addAction("Duplicate")
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete Image")
        menu.addSeparator()
        copy_image_action = menu.addAction("Copy Image")
        copy_path_action = menu.addAction("Copy Path")
        copy_icon_action = menu.addAction("Copy Icon")

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen == open_action:
            self.openRequested.emit(self.path)
            return
        if chosen == show_in_folder_action:
            self.showInFolderRequested.emit(self.path)
            return
        if chosen == duplicate_action:
            self.duplicateRequested.emit(self.path)
            return
        if chosen == rename_action:
            self.renameRequested.emit(self.path)
            return
        if chosen == copy_image_action:
            self.copyImageRequested.emit(self.path)
            return
        if chosen == copy_path_action:
            self.copyPathRequested.emit(self.path)
            return
        if chosen == copy_icon_action:
            self.copyIconRequested.emit(self.path)
            return
        if chosen == delete_action:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Delete Image",
                f"Delete this image?\n\n{self.path.name}",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer == QtWidgets.QMessageBox.Yes:
                self.deleteRequested.emit(self.path)


class ArchiveSidebar(QtWidgets.QFrame):
    """
    Right-side archive browser for managed source images.

    This widget owns archive presentation, selection state, preview updates,
    keyboard navigation, and item-level affordances. File operations are still
    delegated upward so the main window remains responsible for application
    workflow and storage policy.
    """
    filesDropped = QtCore.Signal(list)
    itemSelected = QtCore.Signal(Path)
    openImageRequested = QtCore.Signal(Path)
    showInFolderRequested = QtCore.Signal(Path)
    duplicateRequested = QtCore.Signal(Path)
    copyImageRequested = QtCore.Signal(Path)
    copyPathRequested = QtCore.Signal(Path)
    copyIconRequested = QtCore.Signal(Path)
    renameRequested = QtCore.Signal(Path)
    deleteRequested = QtCore.Signal(Path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._open = False
        self._drop_active = False
        self._archive_sources_dir: Optional[Path] = None
        self._archive_icons_dir: Optional[Path] = None
        self._entries: Dict[str, ArchiveEntrySnapshot] = {}
        self._items: Dict[str, ArchiveItem] = {}
        self._item_order: List[str] = []
        self._selected_key: Optional[str] = None
        self._properties_window: ArchivePropertiesWindow | None = None

        self.setMaximumWidth(COLLAPSED_WIDTH)
        self.setMinimumWidth(COLLAPSED_WIDTH)
        self.setAcceptDrops(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)

        self._build_ui()
        self._width_anim = QtCore.QParallelAnimationGroup(self)
        self._max_width_anim = QtCore.QPropertyAnimation(self, b"maximumWidth")
        self._min_width_anim = QtCore.QPropertyAnimation(self, b"minimumWidth")
        for anim in (self._max_width_anim, self._min_width_anim):
            anim.setDuration(180)
            self._width_anim.addAnimation(anim)
        self._width_anim.finished.connect(self._sync_content_visibility)
        self._apply_surface_style()
        self._update_preview()

    def _build_ui(self) -> None:
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("ArchivePanel")
        self.panel.setVisible(False)
        panel_layout = QtWidgets.QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        panel_layout.addLayout(header_row)
        self.title = QtWidgets.QLabel("Archive")
        self.title.setObjectName("ArchiveTitle")
        header_row.addWidget(self.title, 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        self.count_label = QtWidgets.QLabel("0 items")
        self.count_label.setObjectName("ArchiveCount")
        header_row.addWidget(self.count_label, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self.btn_properties = QtWidgets.QPushButton("Properties")
        self.btn_properties.setObjectName("ArchivePropertiesButton")
        self.btn_properties.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_properties.setEnabled(False)
        self.btn_properties.setFixedHeight(36)
        self.btn_properties.setMinimumWidth(96)
        self.btn_properties.clicked.connect(self._open_properties_window)
        header_row.addWidget(self.btn_properties, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self.preview_frame = ArchivePreviewFrame()
        self.preview_frame.setObjectName("ArchivePreview")
        self.preview_frame.setFixedHeight(PREVIEW_SIZE + 18)
        self.preview_frame.doubleClicked.connect(self._open_preview_image)
        preview_layout = QtWidgets.QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(0)

        self.preview_label = ArchivePreviewLabel()
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self.preview_label.setWordWrap(True)
        self.preview_label.doubleClicked.connect(self._open_preview_image)
        preview_layout.addWidget(self.preview_label, 0, QtCore.Qt.AlignCenter)
        panel_layout.addWidget(self.preview_frame)

        self.selection_name = QtWidgets.QLabel("No archive item selected")
        self.selection_name.setObjectName("ArchiveSelectionName")
        self.selection_name.setWordWrap(True)
        panel_layout.addWidget(self.selection_name)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        panel_layout.addWidget(self.scroll, 1)

        self.container = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for column in range(GRID_COLUMNS):
            self.grid.setColumnStretch(column, 1)
        self.scroll.setWidget(self.container)
        self.scroll.viewport().installEventFilter(self)

        self.empty_state = QtWidgets.QLabel("Archive is empty.\nDrop files or folders here to import stored source images.")
        self.empty_state.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_state.setWordWrap(True)
        self.grid.addWidget(self.empty_state, 0, 0, 1, GRID_COLUMNS)

        self.handle = ArchiveHandleLabel(">  Archive")
        self.handle.setFixedWidth(COLLAPSED_WIDTH)
        self.handle.setStyleSheet(
            "font-weight: 900;"
            "color: rgba(234,242,255,220);"
            "background: rgba(8,12,24,0.92);"
            "border-left: 1px solid rgba(255,255,255,0.08);"
        )
        self.handle.clicked.connect(self.toggle)

        root.addWidget(self.panel, 1)
        root.addWidget(self.handle)

    def _apply_surface_style(self) -> None:
        border = "rgba(255, 191, 4, 0.55)" if self._drop_active else "rgba(255,255,255,0.08)"
        preview_border = "rgba(255, 191, 4, 0.62)" if self._drop_active else "rgba(255,255,255,0.16)"
        self.panel.setStyleSheet(
            f"""
            QFrame#ArchivePanel {{
                border-radius: 18px;
                background-color: rgba(12, 17, 34, 0.86);
                border: 1px solid {border};
            }}
            QFrame#ArchivePreview {{
                border-radius: 16px;
                background-color: rgba(4, 7, 14, 0.92);
                border: 1px dashed {preview_border};
            }}
            QLabel#ArchiveTitle {{
                color: rgba(255,255,255,235);
                font-size: 18px;
                font-weight: 900;
            }}
            QLabel#ArchiveCount {{
                color: rgba(234,242,255,180);
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#ArchiveSelectionName {{
                color: rgba(255,255,255,230);
                font-weight: 800;
            }}
            QPushButton#ArchivePropertiesButton {{
                min-height: 36px;
                padding: 0 12px 2px 12px;
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                background-color: rgba(255, 255, 255, 0.08);
                color: rgba(255,255,255,235);
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#ArchivePropertiesButton:hover:enabled {{
                background-color: rgba(255, 191, 4, 0.18);
                border: 1px solid rgba(255, 191, 4, 0.40);
            }}
            QPushButton#ArchivePropertiesButton:disabled {{
                color: rgba(234,242,255,120);
                background-color: rgba(255, 255, 255, 0.04);
            }}
            """
        )
        _repolish(self.panel)

    def _set_drop_active(self, active: bool) -> None:
        if self._drop_active == active:
            return
        self._drop_active = active
        self._apply_surface_style()

    def toggle(self) -> None:
        self.expand() if not self._open else self.collapse()

    def expand(self) -> None:
        self._open = True
        self.panel.setVisible(True)
        self._update_handle()
        self._animate_width(EXPANDED_WIDTH)

    def collapse(self) -> None:
        self._open = False
        self._update_handle()
        self._animate_width(COLLAPSED_WIDTH)

    def is_open(self) -> bool:
        return self._open

    def _sync_content_visibility(self) -> None:
        self.panel.setVisible(self._open)
        target = EXPANDED_WIDTH if self._open else COLLAPSED_WIDTH
        self.setMinimumWidth(target)
        self.setMaximumWidth(target)
        if self._open:
            self._update_item_widths()

    def _update_handle(self) -> None:
        self.handle.setText("<  Archive" if self._open else ">  Archive")

    def _animate_width(self, target: int) -> None:
        current = max(self.width(), self.minimumWidth())
        self._width_anim.stop()
        self._min_width_anim.setStartValue(current)
        self._min_width_anim.setEndValue(target)
        self._max_width_anim.setStartValue(current)
        self._max_width_anim.setEndValue(target)
        self._width_anim.start()

    def apply_snapshot(
        self,
        entries: List[ArchiveEntrySnapshot],
        *,
        archive_sources_dir: Path | None = None,
        archive_icons_dir: Path | None = None,
    ) -> None:
        if archive_sources_dir is not None:
            self._archive_sources_dir = Path(archive_sources_dir)
        if archive_icons_dir is not None:
            self._archive_icons_dir = Path(archive_icons_dir)

        previous_scroll = self.scroll.verticalScrollBar().value()
        old_selected = self._selected_key
        wanted_keys = [entry.key for entry in entries]
        wanted_key_set = set(wanted_keys)

        for dead_key in list(self._items.keys()):
            if dead_key in wanted_key_set:
                continue
            widget = self._items.pop(dead_key)
            self._entries.pop(dead_key, None)
            widget.setParent(None)
            widget.deleteLater()

        for entry in entries:
            existing = self._entries.get(entry.key)
            if existing is not None and not entry.changed:
                entry = existing
            else:
                self._entries[entry.key] = entry

            widget = self._items.get(entry.key)
            if widget is None:
                widget = ArchiveItem(entry.path, self._pixmap_for_entry(entry))
                self._connect_item(widget)
                self._items[entry.key] = widget
            else:
                pixmap = self._pixmap_for_entry(entry) if entry.changed else None
                widget.update_content(entry.path, pixmap=pixmap)

        self._item_order = wanted_keys
        for index, key in enumerate(self._item_order):
            row = index // GRID_COLUMNS
            col = index % GRID_COLUMNS
            self.grid.addWidget(self._items[key], row, col)
        self._update_item_widths()

        if not self._items:
            self.grid.addWidget(self.empty_state, 0, 0, 1, GRID_COLUMNS)
            self.empty_state.show()
        else:
            self.empty_state.hide()

        if old_selected in self._items:
            self._set_selected_key(old_selected, emit_signal=False, preserve_scroll=True)
        else:
            self._set_selected_key(None, emit_signal=False, preserve_scroll=True)

        self._update_count_label()
        QtCore.QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(previous_scroll))

    def _update_count_label(self) -> None:
        count = len(self._items)
        self.count_label.setText(f"{count} item" + ("" if count == 1 else "s"))

    def _connect_item(self, item: ArchiveItem) -> None:
        item.clicked.connect(self._on_item_clicked)
        item.openRequested.connect(self.openImageRequested)
        item.showInFolderRequested.connect(self.showInFolderRequested)
        item.duplicateRequested.connect(self.duplicateRequested)
        item.copyImageRequested.connect(self.copyImageRequested)
        item.copyPathRequested.connect(self.copyPathRequested)
        item.copyIconRequested.connect(self.copyIconRequested)
        item.renameRequested.connect(self.renameRequested)
        item.deleteRequested.connect(self.deleteRequested)

    def _on_item_clicked(self, path: Path) -> None:
        self.set_current_path(path, emit_signal=True)
        self.setFocus(QtCore.Qt.MouseFocusReason)

    def set_current_path(self, path: Path | None, *, emit_signal: bool = False) -> None:
        if path is None:
            self._set_selected_key(None, emit_signal=False)
            self._update_preview()
            return
        key = str(Path(path).resolve())
        self._set_selected_key(key, emit_signal=emit_signal)

    def _set_selected_key(
        self,
        key: Optional[str],
        *,
        emit_signal: bool = True,
        preserve_scroll: bool = False,
    ) -> None:
        if self._selected_key and self._selected_key in self._items:
            self._items[self._selected_key].set_selected(False)

        self._selected_key = key if key in self._items else None

        if self._selected_key and self._selected_key in self._items:
            item = self._items[self._selected_key]
            item.set_selected(True)
            if not preserve_scroll:
                self.scroll.ensureWidgetVisible(item)
            if emit_signal:
                self.itemSelected.emit(item.path)

        self._update_preview()

    def current_path(self) -> Optional[Path]:
        if self._selected_key and self._selected_key in self._items:
            return self._items[self._selected_key].path
        return None

    def current_entry(self) -> ArchiveEntrySnapshot | None:
        if self._selected_key:
            return self._entries.get(self._selected_key)
        return None

    def entry_signatures(self) -> Dict[str, tuple[int, float]]:
        return {
            key: (entry.signature_size, entry.signature_mtime)
            for key, entry in self._entries.items()
        }

    def rename_item_path(self, old_path: Path, new_path: Path) -> None:
        old_key = str(Path(old_path).resolve())
        if old_key not in self._items or old_key not in self._entries:
            return

        entry = self._entries.pop(old_key)
        new_key = str(Path(new_path).resolve())
        entry.key = new_key
        entry.path = Path(new_path)
        entry.name = Path(new_path).stem
        entry.image_path_text = str(Path(new_path).resolve())
        entry.icon_path_text = self._icon_path_for_image(Path(new_path))
        entry.changed = True
        self._entries[new_key] = entry

        item = self._items.pop(old_key)
        item.update_content(Path(new_path))
        self._items[new_key] = item
        self._item_order = [new_key if key == old_key else key for key in self._item_order]
        if self._selected_key == old_key:
            self._selected_key = new_key
        self._reflow_items()
        self._update_preview()

    def remove_item_path(self, path: Path) -> None:
        key = str(Path(path).resolve())
        widget = self._items.pop(key, None)
        self._entries.pop(key, None)
        self._item_order = [item_key for item_key in self._item_order if item_key != key]
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        if self._selected_key == key:
            self._selected_key = None
        self._reflow_items()
        self._update_count_label()
        self._update_preview()

    def _reflow_items(self) -> None:
        for index, key in enumerate(self._item_order):
            if key not in self._items:
                continue
            row = index // GRID_COLUMNS
            col = index % GRID_COLUMNS
            self.grid.addWidget(self._items[key], row, col)
        if not self._item_order:
            self.grid.addWidget(self.empty_state, 0, 0, 1, GRID_COLUMNS)
            self.empty_state.show()
        else:
            self.empty_state.hide()
        self._update_item_widths()

    def _update_item_widths(self) -> None:
        viewport_width = self.scroll.viewport().width()
        if viewport_width <= 0:
            return
        spacing = max(self.grid.horizontalSpacing(), 0)
        total_spacing = spacing * (GRID_COLUMNS - 1)
        item_width = max(112, (viewport_width - total_spacing) // GRID_COLUMNS)
        for column in range(GRID_COLUMNS):
            self.grid.setColumnMinimumWidth(column, item_width)
        for item in self._items.values():
            item.setFixedWidth(item_width)

    def _placeholder_thumb(self) -> QtGui.QPixmap:
        pixmap = QtGui.QPixmap(THUMB_SIZE, THUMB_SIZE)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor(180, 180, 180, 180)))
        painter.drawRoundedRect(4, 4, THUMB_SIZE - 8, THUMB_SIZE - 8, 6, 6)
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, "?")
        painter.end()
        return pixmap

    def _pixmap_for_entry(self, entry: ArchiveEntrySnapshot) -> QtGui.QPixmap:
        if entry.thumb_image is None or entry.thumb_image.isNull():
            return self._placeholder_thumb()
        return QtGui.QPixmap.fromImage(entry.thumb_image)

    def _scaled_preview(self, path: Path) -> Optional[QtGui.QPixmap]:
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap.scaled(
            PREVIEW_SIZE,
            PREVIEW_SIZE,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )

    def _ensure_properties_window(self) -> ArchivePropertiesWindow:
        if self._properties_window is None:
            self._properties_window = ArchivePropertiesWindow(self)
        return self._properties_window

    def _open_properties_window(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        window = self._ensure_properties_window()
        window.set_entry(entry)
        if not window.isVisible():
            parent_pos = self.mapToGlobal(QtCore.QPoint(24, 24))
            window.move(parent_pos)
        window.show()
        window.raise_()
        window.activateWindow()

    def _sync_properties_window(self) -> None:
        if self._properties_window is None:
            return
        self._properties_window.set_entry(self.current_entry())

    def _update_preview(self) -> None:
        # The preview is the only place where double-click opens the source
        # image. Archive cards remain selection and management targets only.
        current = self.current_path()
        entry = self.current_entry()
        self.btn_properties.setEnabled(entry is not None)
        if current is None:
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.preview_label.setText("No archive item selected")
            self.preview_label.setCursor(QtCore.Qt.ArrowCursor)
            self.preview_label.setToolTip("")
            self.preview_frame.setCursor(QtCore.Qt.ArrowCursor)
            self.preview_frame.setToolTip("")
            self.selection_name.setText("No archive item selected")
            self._sync_properties_window()
            return

        preview = self._scaled_preview(current)
        if preview is None:
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.preview_label.setText("Preview unavailable")
        else:
            self.preview_label.setText("")
            self.preview_label.setPixmap(preview)
        self.preview_label.setCursor(QtCore.Qt.PointingHandCursor)
        self.preview_label.setToolTip("Double-click to open the source image")
        self.preview_frame.setCursor(QtCore.Qt.PointingHandCursor)
        self.preview_frame.setToolTip("Double-click to open the source image")

        self.selection_name.setText(current.name)
        self._sync_properties_window()

    def _open_preview_image(self) -> None:
        current = self.current_path()
        if current is not None:
            self.openImageRequested.emit(current)

    def _icon_path_for_image(self, path: Path) -> str:
        if self._archive_sources_dir is None or self._archive_icons_dir is None:
            return "Unavailable"
        try:
            rel_parent = path.resolve().relative_to(self._archive_sources_dir.resolve()).parent
        except Exception:
            rel_parent = Path()
        return str((self._archive_icons_dir / rel_parent / f"{path.stem}.ico").resolve())

    def keyPressEvent(self, event):
        current = self.current_path()
        if current is not None:
            if event.key() == QtCore.Qt.Key_F2:
                self.renameRequested.emit(current)
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Delete:
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Delete Image",
                    f"Delete this image?\n\n{current.name}",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if answer == QtWidgets.QMessageBox.Yes:
                    self.deleteRequested.emit(current)
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Up:
                self._move_selection(-GRID_COLUMNS)
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Down:
                self._move_selection(GRID_COLUMNS)
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Left:
                self._move_selection(-1)
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Right:
                self._move_selection(1)
                event.accept()
                return
        super().keyPressEvent(event)

    def _move_selection(self, delta: int) -> None:
        if not self._item_order:
            return
        if self._selected_key not in self._items:
            self._set_selected_key(self._item_order[0])
            return
        index = self._item_order.index(self._selected_key)
        index = max(0, min(len(self._item_order) - 1, index + delta))
        self._set_selected_key(self._item_order[index])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._set_drop_active(True)
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drop_active(False)
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            self.expand()
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QtCore.QEvent.Resize:
            self._update_item_widths()
        return super().eventFilter(watched, event)
