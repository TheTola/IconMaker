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

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

import Gen2 as eng
from AppTheme import theme_color, theme_css, theme_manager
from AppTitleBar import CustomTitleBar, TITLE_BAR_CSS

COLLAPSED_WIDTH = 44
EXPANDED_WIDTH = 528
PREVIEW_SIZE = 184
THUMB_SIZE = 68
GRID_COLUMNS = 3


def _repolish(widget: QtWidgets.QWidget) -> None:
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except Exception:
        widget.update()


def _confirm_delete_image(parent: QtWidgets.QWidget, path: Path) -> bool:
    dialog = QtWidgets.QMessageBox(parent)
    dialog.setWindowTitle("Delete Image")
    dialog.setIcon(QtWidgets.QMessageBox.Warning)
    dialog.setText("Are you sure you want to delete this image?")
    dialog.setInformativeText(Path(path).name)
    cancel_button = dialog.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    delete_button = dialog.addButton("Delete Image", QtWidgets.QMessageBox.DestructiveRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    return dialog.clickedButton() is delete_button


def _icon_sizes_text(path_text: str | None) -> str:
    if not path_text or path_text == "Unavailable":
        return "Unavailable"
    path = Path(path_text)
    if not path.is_file():
        return "Not generated"
    try:
        with Image.open(path) as icon:
            if icon.format != "ICO":
                return "Unavailable"
            dimensions = set(icon.ico.sizes())
    except Exception:
        return "Unavailable"
    if not dimensions:
        return "Unavailable"
    # Older ICOs can include extra intermediate frames; the standard range is complete.
    standard_sizes = (8, 16, 24, 32, 48, 64, 96, 128, 256)
    if {(size, size) for size in standard_sizes}.issubset(dimensions):
        return "All"
    return ", ".join(
        str(width) if width == height else f"{width}×{height}"
        for width, height in sorted(dimensions)
    )


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
    icon_sizes_text: str | None = None
    created_text: str | None = None
    modified_text: str | None = None
    thumb_image: QtGui.QImage | None = None


class ArchiveHandleLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__("", parent)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._word = self._normalize_word(text)
        self._expanded = False

    @staticmethod
    def _normalize_word(text: str) -> str:
        letters = "".join(ch for ch in str(text or "") if ch.isalpha())
        return letters.upper() or "ARCHIVE"

    def set_word(self, text: str) -> None:
        self._word = self._normalize_word(text)
        self.update()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.update()

    def sizeHint(self):
        return QtCore.QSize(COLLAPSED_WIDTH, 168)

    def minimumSizeHint(self):
        return self.sizeHint()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)

        rail = QtCore.QRectF(3.5, 8.5, self.width() - 7, self.height() - 17)
        gradient = QtGui.QLinearGradient(rail.topLeft(), rail.bottomRight())
        gradient.setColorAt(0, theme_color("#192e48"))
        gradient.setColorAt(0.6, theme_color("#12243d"))
        gradient.setColorAt(1, theme_color("#0b172a"))
        painter.setBrush(gradient)
        painter.setPen(QtGui.QPen(QtGui.QColor(75, 164, 203, 110), 1))
        painter.drawRoundedRect(rail, 11, 11)

        font = painter.font()
        font.setPointSize(9)
        font.setWeight(QtGui.QFont.DemiBold)
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.4)
        painter.setFont(font)
        word_center = self.height() / 2
        arrow_y = word_center
        arrow_x = self.width() - 12
        direction = -1 if self._expanded else 1
        chevron = QtGui.QPainterPath(QtCore.QPointF(arrow_x - direction * 3, arrow_y - 5))
        chevron.lineTo(arrow_x + direction * 3, arrow_y)
        chevron.lineTo(arrow_x - direction * 3, arrow_y + 5)
        painter.setBrush(QtCore.Qt.NoBrush)
        chevron_pen = QtGui.QPen(theme_color("#8bdff0"), 1.8)
        chevron_pen.setCapStyle(QtCore.Qt.RoundCap)
        chevron_pen.setJoinStyle(QtCore.Qt.RoundJoin)
        painter.setPen(chevron_pen)
        painter.drawPath(chevron)

        painter.save()
        painter.setPen(theme_color("#d8e9f4"))
        painter.translate(16, word_center)
        painter.rotate(-90)
        painter.drawText(
            QtCore.QRect(-72, -12, 144, 24),
            QtCore.Qt.AlignCenter,
            self._word.title(),
        )
        painter.restore()


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

        self.title_bar = CustomTitleBar(chrome, chooser_title="Properties")
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
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setObjectName("ArchivePropertiesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        scroll_body = QtWidgets.QWidget()
        scroll_body.setObjectName("ArchivePropertiesBody")
        scroll_body.setLayout(grid)
        self.scroll.setWidget(scroll_body)
        layout.addWidget(self.scroll, 1)
        self._grid = grid

        self._property_labels: List[QtWidgets.QLabel] = []
        self._values: Dict[str, QtWidgets.QLabel] = {}
        property_rows = [
            ("Name:", "name"),
            ("Image Size:", "image_size"),
            ("Sizes:", "icon_sizes"),
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

        self._theme_style = """
            QDialog#ArchivePropertiesWindow {
                background: transparent;
            }
            QFrame#ArchivePropertiesChrome {
                background-color: #0b1323;
                border: 1px solid rgba(75, 164, 203, 0.30);
                border-radius: 14px;
            }
            QFrame#ArchivePropertiesCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #132138, stop:1 #0d1729);
                border: 0;
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }
            QLabel#ArchivePropertiesHeading {
                color: #b7d9ec;
                font-size: 14px;
                font-weight: 600;
                padding-bottom: 2px;
            }
            QLabel#ArchivePropertyLabel {
                color: #8fb7cf;
                font-size: 11px;
                font-weight: 500;
                padding-top: 1px;
            }
            QLabel#ArchivePropertyValue {
                color: #e8eef8;
                font-size: 11px;
                background: transparent;
            }
            QScrollArea#ArchivePropertiesScroll {
                background: transparent;
                border: 0;
            }
            QWidget#ArchivePropertiesBody {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 7px;
            }
            QScrollBar::handle:vertical {
                background: rgba(153, 169, 191, 0.45);
                border-radius: 3px;
                min-height: 24px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)
        self._refresh_layout_metrics()

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css(TITLE_BAR_CSS + self._theme_style))

    def set_entry(self, entry: ArchiveEntrySnapshot | None) -> None:
        if entry is None:
            placeholders = {
                "name": "No archive item selected",
                "image_size": "Unavailable",
                "icon_sizes": "Unavailable",
                "file_type": "Unavailable",
                "file_size": "Unavailable",
                "image_path": "Select an archive item to inspect it.",
                "icon_path": "Unavailable",
                "created": "Unavailable",
                "modified": "Unavailable",
            }
            for key, value in placeholders.items():
                self._values[key].setText(value)
            self._refresh_layout_metrics()
            return

        values = {
            "name": entry.name or entry.path.name,
            "image_size": entry.image_size_text or "Unavailable",
            "icon_sizes": entry.icon_sizes_text or "Unavailable",
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
        value_width = 352
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
        self.setFixedWidth(max(500, total_width))

        rows_height = 0
        for label, value in zip(self._property_labels, self._values.values()):
            text_height = value.fontMetrics().boundingRect(
                QtCore.QRect(0, 0, value_width, 10000),
                QtCore.Qt.TextWordWrap,
                value.text(),
            ).height()
            rows_height += max(label.sizeHint().height(), text_height)
        rows_height += self._grid.verticalSpacing() * (len(self._property_labels) - 1)

        heading = self.findChild(QtWidgets.QLabel, "ArchivePropertiesHeading")
        heading_height = heading.sizeHint().height() if heading is not None else 24
        desired_height = 30 + card_margins.top() + card_margins.bottom() + heading_height + 8 + rows_height + 10
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        max_height = max(340, screen.availableGeometry().height() - 80) if screen else 640
        self.setFixedHeight(min(max(340, desired_height), max_height))


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
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)
        self.setFixedHeight(136)

        self.thumb = QtWidgets.QLabel()
        self.thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.thumb.setFixedHeight(78)
        self.thumb.setPixmap(pixmap)
        self.thumb.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        self.name = QtWidgets.QLabel(self.path.stem)
        self.name.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        self.name.setWordWrap(True)
        self.name.setFixedHeight(40)
        self.name.setObjectName("ArchiveItemName")
        self.name.setToolTip(str(self.path))
        self.name.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        layout.addWidget(self.thumb)
        layout.addWidget(self.name)

        self._apply_style()
        theme_manager().changed.connect(self._apply_style)

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

    def _apply_style(self, *_args) -> None:
        if self._selected:
            self.setStyleSheet(theme_css(
                "QFrame#ArchiveItem {"
                "border: 1px solid #d8ad51;"
                "border-radius: 8px;"
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                "stop:0 #1c314b, stop:1 #14233b);"
                "}"
                "QLabel { background: transparent; color: #e8eef8; font-size: 11px; }"
            ))
        else:
            self.setStyleSheet(theme_css(
                "QFrame#ArchiveItem {"
                "border: 1px solid rgba(90, 150, 186, 0.24);"
                "border-radius: 8px;"
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                "stop:0 #17263b, stop:1 #111d31);"
                "}"
                "QFrame#ArchiveItem:hover {"
                "border: 1px solid rgba(54, 201, 232, 0.62);"
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                "stop:0 #203b56, stop:1 #162943);"
                "}"
                "QLabel { background: transparent; color: #c9d6e7; font-size: 11px; }"
            ))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.openRequested.emit(self.path)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        path = self.path
        self.clicked.emit(path)

        menu = QtWidgets.QMenu(self)
        menu.setObjectName("ArchiveContextMenu")
        menu.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        menu.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        menu.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        menu.setStyleSheet(theme_css("""
            QMenu#ArchiveContextMenu {
                background-color: rgba(9, 19, 35, 220);
                border: 1px solid rgba(112, 185, 217, 180);
                border-radius: 9px;
                padding: 6px;
                color: #eaf4fc;
            }
            QMenu#ArchiveContextMenu::item {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                color: #eaf4fc;
                padding: 7px 20px 7px 13px;
                margin: 1px 0;
            }
            QMenu#ArchiveContextMenu::item:selected {
                background-color: rgba(42, 98, 126, 242);
                border-color: rgba(103, 212, 239, 195);
                color: #ffffff;
            }
            QMenu#ArchiveContextMenu::separator {
                height: 1px;
                background-color: rgba(131, 176, 199, 120);
                margin: 5px 10px;
            }
        """))
        open_action = menu.addAction("Open Image")
        show_in_folder_action = menu.addAction("Show in Folder")
        menu.addSeparator()
        copy_image_action = menu.addAction("Copy Image")
        copy_icon_action = menu.addAction("Copy Icon")
        copy_path_action = menu.addAction("Copy Path")
        menu.addSeparator()
        duplicate_action = menu.addAction("Duplicate")
        rename_action = menu.addAction("Rename")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Image")
        delete_icon = QtGui.QPixmap(16, 16)
        delete_icon.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(delete_icon)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ff6b76"), 1.6))
        painter.drawLine(3, 5, 13, 5)
        painter.drawLine(6, 3, 10, 3)
        painter.drawRoundedRect(QtCore.QRectF(5, 6, 6, 8), 1, 1)
        painter.end()
        delete_action.setIcon(QtGui.QIcon(delete_icon))

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen == open_action:
            self.openRequested.emit(path)
            return
        if chosen == show_in_folder_action:
            self.showInFolderRequested.emit(path)
            return
        if chosen == duplicate_action:
            self.duplicateRequested.emit(path)
            return
        if chosen == rename_action:
            self.renameRequested.emit(path)
            return
        if chosen == copy_image_action:
            self.copyImageRequested.emit(path)
            return
        if chosen == copy_path_action:
            self.copyPathRequested.emit(path)
            return
        if chosen == copy_icon_action:
            self.copyIconRequested.emit(path)
            return
        if chosen == delete_action:
            if _confirm_delete_image(self, path):
                self.deleteRequested.emit(path)


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
        theme_manager().changed.connect(self._theme_changed)
        self._update_preview()

    def _theme_changed(self, *_args) -> None:
        self._apply_surface_style()
        self.handle.update()

    def _build_ui(self) -> None:
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.panel_host = QtWidgets.QWidget()
        self.panel_host.setVisible(False)
        host_layout = QtWidgets.QVBoxLayout(self.panel_host)
        host_layout.setContentsMargins(4, 8, 8, 8)

        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("ArchivePanel")
        host_layout.addWidget(self.panel)
        panel_layout = QtWidgets.QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(12, 14, 12, 12)
        panel_layout.setSpacing(12)

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

        header_rule = QtWidgets.QFrame()
        header_rule.setObjectName("ArchiveHeaderRule")
        header_rule.setFixedHeight(1)
        panel_layout.addWidget(header_rule)

        self.preview_frame = ArchivePreviewFrame()
        self.preview_frame.setObjectName("ArchivePreview")
        self.preview_frame.setFixedHeight(54)
        self.preview_frame.doubleClicked.connect(self._open_preview_image)
        preview_layout = QtWidgets.QHBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(9, 9, 9, 9)
        preview_layout.setSpacing(12)

        self.preview_label = ArchivePreviewLabel()
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self.preview_label.doubleClicked.connect(self._open_preview_image)
        preview_layout.addWidget(self.preview_label, 0, QtCore.Qt.AlignVCenter)

        self.selection_details = QtWidgets.QWidget()
        details_layout = QtWidgets.QVBoxLayout(self.selection_details)
        details_layout.setContentsMargins(0, 2, 0, 0)
        details_layout.setSpacing(6)

        self.selection_name = QtWidgets.QLabel()
        self.selection_name.setObjectName("ArchiveSelectionName")
        self.selection_name.setWordWrap(True)
        self.selection_name.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.selection_name.setMaximumHeight(42)
        details_layout.addWidget(self.selection_name)

        self.selection_dimensions = QtWidgets.QLabel()
        self.selection_dimensions.setObjectName("ArchiveSelectionMeta")
        details_layout.addWidget(self.selection_dimensions)

        self.selection_sizes = QtWidgets.QLabel()
        self.selection_sizes.setObjectName("ArchiveSelectionMeta")
        self.selection_sizes.setWordWrap(True)
        details_layout.addWidget(self.selection_sizes)

        self.selection_file = QtWidgets.QLabel()
        self.selection_file.setObjectName("ArchiveSelectionMeta")
        details_layout.addWidget(self.selection_file)

        self.selection_modified = QtWidgets.QLabel()
        self.selection_modified.setObjectName("ArchiveSelectionMeta")
        self.selection_modified.setWordWrap(True)
        details_layout.addWidget(self.selection_modified)
        details_layout.addStretch(1)

        self.btn_properties = QtWidgets.QPushButton("Properties")
        self.btn_properties.setObjectName("ArchivePropertiesButton")
        self.btn_properties.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_properties.setEnabled(False)
        self.btn_properties.setFixedHeight(30)
        self.btn_properties.clicked.connect(self._open_properties_window)
        details_layout.addWidget(self.btn_properties, 0, QtCore.Qt.AlignLeft)
        preview_layout.addWidget(self.selection_details, 1)

        self.preview_hint = QtWidgets.QLabel("Select an image to preview it")
        self.preview_hint.setObjectName("ArchivePreviewHint")
        self.preview_hint.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        preview_layout.addWidget(self.preview_hint, 1)
        panel_layout.addWidget(self.preview_frame)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setObjectName("ArchiveGridScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        panel_layout.addWidget(self.scroll, 1)

        self.container = QtWidgets.QWidget()
        self.container.setObjectName("ArchiveGridContainer")
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setAlignment(QtCore.Qt.AlignTop)
        for column in range(GRID_COLUMNS):
            self.grid.setColumnStretch(column, 1)
        self.scroll.setWidget(self.container)
        self.scroll.viewport().installEventFilter(self)

        self.empty_state = QtWidgets.QLabel("Archive is empty.\nDrop files or folders here to import stored source images.")
        self.empty_state.setObjectName("ArchiveEmptyState")
        self.empty_state.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_state.setWordWrap(True)
        self.grid.addWidget(self.empty_state, 0, 0, 1, GRID_COLUMNS)

        self.handle = ArchiveHandleLabel("Archive")
        self.handle.setFixedWidth(COLLAPSED_WIDTH)
        self.handle.setToolTip("Open Archive")
        self.handle.clicked.connect(self.toggle)

        root.addWidget(self.handle)
        root.addWidget(self.panel_host, 1)

    def _apply_surface_style(self) -> None:
        border = "rgba(54,201,232,0.75)" if self._drop_active else "rgba(75,164,203,0.28)"
        preview_border = "rgba(54,201,232,0.75)" if self._drop_active else "rgba(95,160,192,0.32)"
        self.panel.setStyleSheet(theme_css(
            f"""
            QFrame#ArchivePanel {{
                border-radius: 10px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #141e35, stop:0.42 #0f1930, stop:1 #0b1427);
                border: 1px solid {border};
            }}
            QFrame#ArchivePreview {{
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #172b44, stop:1 #101b30);
                border: 1px solid {preview_border};
            }}
            QLabel#ArchiveTitle {{
                color: #edf7ff;
                font-size: 17px;
                font-weight: 600;
            }}
            QLabel#ArchiveCount {{
                color: #a5c3d7;
                font-size: 11px;
            }}
            QFrame#ArchiveHeaderRule {{
                border: 0;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(83,171,212,0.66),
                    stop:0.34 rgba(83,171,212,0.30),
                    stop:1 rgba(83,171,212,0.04));
            }}
            QLabel#ArchiveSelectionName {{
                color: #e8eef8;
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#ArchiveSelectionMeta {{
                color: #a5c3d7;
                font-size: 11px;
            }}
            QLabel#ArchivePreviewHint {{
                color: #99a9bf;
                font-size: 12px;
            }}
            QPushButton#ArchivePropertiesButton {{
                padding: 0 12px;
                border-radius: 6px;
                border: 1px solid rgba(92, 154, 190, 0.34);
                background-color: rgba(39, 72, 106, 0.38);
                color: #d4deed;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton#ArchivePropertiesButton:hover:enabled {{
                background-color: rgba(54, 201, 232, 0.16);
                border: 1px solid rgba(54, 201, 232, 0.64);
            }}
            QPushButton#ArchivePropertiesButton:disabled {{
                color: #75849a;
            }}
            QScrollArea#ArchiveGridScroll {{
                background: transparent;
                border: 0;
            }}
            QWidget#ArchiveGridContainer {{
                background: transparent;
            }}
            QLabel#ArchiveEmptyState {{
                color: #99a9bf;
                font-size: 12px;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 7px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(153, 169, 191, 0.45);
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """
        ))
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
        self.panel_host.setVisible(True)
        self._update_handle()
        self._animate_width(EXPANDED_WIDTH)

    def collapse(self) -> None:
        self._open = False
        self._update_handle()
        self._animate_width(COLLAPSED_WIDTH)

    def is_open(self) -> bool:
        return self._open

    def _sync_content_visibility(self) -> None:
        self.panel_host.setVisible(self._open)
        target = EXPANDED_WIDTH if self._open else COLLAPSED_WIDTH
        self.setMinimumWidth(target)
        self.setMaximumWidth(target)
        if self._open:
            self._update_item_widths()

    def _update_handle(self) -> None:
        self.handle.set_expanded(self._open)
        self.handle.setToolTip("Close Archive" if self._open else "Open Archive")

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
        reader = QtGui.QImageReader(str(path))
        source_size = reader.size()
        if source_size.isValid():
            reader.setScaledSize(source_size.scaled(PREVIEW_SIZE, PREVIEW_SIZE, QtCore.Qt.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            return None
        return QtGui.QPixmap.fromImage(image).scaled(
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
        self._refresh_icon_details(entry)
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

    def _refresh_icon_details(self, entry: ArchiveEntrySnapshot) -> None:
        entry.icon_path_text = self._icon_path_for_image(entry.path)
        entry.icon_sizes_text = _icon_sizes_text(entry.icon_path_text)

    def _update_preview(self) -> None:
        # The preview and archive cards open the full-resolution source image.
        current = self.current_path()
        entry = self.current_entry()
        self.btn_properties.setEnabled(entry is not None)
        if current is None:
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.preview_label.setCursor(QtCore.Qt.ArrowCursor)
            self.preview_label.setToolTip("")
            self.preview_frame.setCursor(QtCore.Qt.ArrowCursor)
            self.preview_frame.setToolTip("")
            self.preview_label.hide()
            self.selection_details.hide()
            self.preview_hint.show()
            self.preview_frame.setFixedHeight(54)
            self._sync_properties_window()
            return

        self.preview_hint.hide()
        self.preview_label.show()
        self.selection_details.show()
        if entry is not None:
            self._refresh_icon_details(entry)
        self.preview_frame.setFixedHeight(PREVIEW_SIZE + 18)
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
        self.selection_name.setToolTip(str(current))
        self.selection_dimensions.setText(
            f"Dimensions  {(entry.image_size_text if entry else None) or 'Unavailable'}"
        )
        self.selection_sizes.setText(f"Sizes: {(entry.icon_sizes_text if entry else None) or 'Unavailable'}")
        self.selection_file.setText(
            f"{(entry.file_type_text if entry else None) or 'Unknown type'}  ·  "
            f"{(entry.file_size_text if entry else None) or 'Unknown size'}"
        )
        self.selection_modified.setText(
            f"Modified  {(entry.modified_text if entry else None) or 'Unavailable'}"
        )
        self._sync_properties_window()

    def _open_preview_image(self) -> None:
        current = self.current_path()
        if current is not None:
            self.openImageRequested.emit(current)

    def _icon_path_for_image(self, path: Path) -> str:
        if self._archive_sources_dir is None or self._archive_icons_dir is None:
            return "Unavailable"
        try:
            paths = eng.EnginePaths(
                self._archive_sources_dir.parent,
                self._archive_sources_dir,
                self._archive_icons_dir,
            )
            return str(eng.archive_icon_path_for_source_image(path.resolve(), paths=paths).resolve())
        except ValueError:
            return "Unavailable"

    def keyPressEvent(self, event):
        current = self.current_path()
        if current is not None:
            if event.key() == QtCore.Qt.Key_F2:
                self.renameRequested.emit(current)
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Delete:
                if _confirm_delete_image(self, current):
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
