"""Shared IconForge title bar and themed window chrome."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from AppIdentity import APP_DISPLAY_VERSION
from AppTheme import theme_css, theme_manager
from Gen4 import get_app_icon

APP_BRANDING_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "iconner.png"
APP_BRANDING_LIGHT_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "IconnerLight.png"
LIGHT_BRANDING_CONTENT_RECT = QtCore.QRect(0, 109, 1672, 666)

TITLE_BAR_CSS = r"""
        #AppTitleBar {
            border-radius: 14px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #10243e, stop:0.5 #151b3a, stop:1 #291530);
            border: 1px solid #34445f;
            border-bottom: 1px solid #2f8096;
        }
        #AppTitleBar[nativeChrome="true"] {
            border-radius: 14px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #10243e, stop:0.5 #151b3a, stop:1 #291530);
            border: 1px solid #34445f;
            border-bottom: 1px solid #2f8096;
        }
        #TitleBarBrand { background: transparent; }
        #ChooserTitle { color: #dbe5f2; font-size: 15px; font-weight: 600; }
        QPushButton#TitleBarNavButton {
            border-radius: 9px;
            padding: 3px 12px;
            background-color: #168daf;
            border: 1px solid #6bd7ec;
            color: #fdfefe;
            font-size: 17px;
            font-weight: 600;
        }
        QPushButton#TitleBarNavButton:hover {
            border-color: #8be7f3;
            background-color: #116c8c;
        }
        QToolButton#WindowControl, QToolButton#WindowCloseControl {
            border-radius: 9px;
            border: 1px solid #6bd7ec;
            background-color: #168daf;
            color: #fdfefe;
            font-size: 14px;
            font-weight: 600;
        }
        QToolButton#WindowControl:hover, QToolButton#WindowCloseControl:hover {
            border-color: #8be7f3;
            background-color: #116c8c;
        }
"""


class CustomTitleBar(QtWidgets.QFrame):
    def __init__(self, parent=None, *, native_window_controls: bool = False, chooser_title: str | None = None):
        super().__init__(parent)
        self.setObjectName("AppTitleBar")
        self._native_window_controls = native_window_controls
        self.setProperty("nativeChrome", native_window_controls)
        self.setFixedHeight(52 if chooser_title is not None else 72)
        self._drag_offset: QtCore.QPoint | None = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(12)

        if chooser_title is not None:
            icon_label = QtWidgets.QLabel()
            icon_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            icon_label.setPixmap(get_app_icon().pixmap(26, 26))
            layout.addWidget(icon_label, 0, QtCore.Qt.AlignVCenter)
            title_label = QtWidgets.QLabel(chooser_title)
            title_label.setObjectName("ChooserTitle")
            title_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(title_label, 0, QtCore.Qt.AlignVCenter)
        else:
            self.brand_label = QtWidgets.QLabel()
            self.brand_label.setObjectName("TitleBarBrand")
            self.brand_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.brand_label.setFixedSize(264, 62)
            self.brand_label.setAlignment(QtCore.Qt.AlignCenter)
            self._apply_branding()
            theme_manager().changed.connect(self._apply_branding)
            self.brand_label.setToolTip(APP_DISPLAY_VERSION)
            layout.addWidget(self.brand_label, 0, QtCore.Qt.AlignVCenter)
        layout.addStretch(1)

        if chooser_title is None:
            self.btn_nav = QtWidgets.QPushButton("⚙")
            self.btn_nav.setObjectName("TitleBarNavButton")
            self.btn_nav.setCursor(QtCore.Qt.PointingHandCursor)
            self.btn_nav.setToolTip("Settings")
            self.btn_nav.setAccessibleName("Settings")
            self.btn_nav.setFixedHeight(36 if native_window_controls else 38)
            layout.addWidget(self.btn_nav, 0, QtCore.Qt.AlignVCenter)

        self.btn_min: QtWidgets.QToolButton | None = None
        self.btn_max: QtWidgets.QToolButton | None = None
        self.btn_close: QtWidgets.QToolButton | None = None
        if not native_window_controls:
            if chooser_title is None:
                layout.addSpacing(12)
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
            self.btn_close.clicked.connect(
                lambda: self.window().reject() if isinstance(self.window(), QtWidgets.QDialog) else self.window().close()
            )

            for btn in (self.btn_min, self.btn_max, self.btn_close):
                if btn is None:
                    continue
                btn.setCursor(QtCore.Qt.PointingHandCursor)
                btn.setAutoRaise(True)
                btn.setFixedSize(
                    38 if chooser_title is not None else 43,
                    34 if chooser_title is not None else 38,
                )
                layout.addWidget(btn, 0, QtCore.Qt.AlignVCenter)

        self.sync_state()

    def _apply_branding(self, *_args) -> None:
        light = theme_manager().appearance == "Light"
        path = APP_BRANDING_LIGHT_IMAGE_PATH if light else APP_BRANDING_IMAGE_PATH
        brand_pixmap = QtGui.QPixmap(str(path))
        if brand_pixmap.isNull() and light:
            brand_pixmap = QtGui.QPixmap(str(APP_BRANDING_IMAGE_PATH))
        if light and brand_pixmap.size() == QtCore.QSize(1672, 941):
            brand_pixmap = brand_pixmap.copy(LIGHT_BRANDING_CONTENT_RECT)
        if brand_pixmap.isNull():
            self.brand_label.clear()
            return
        self.brand_label.setPixmap(
            brand_pixmap.scaled(
                self.brand_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

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


class ThemedDialog(QtWidgets.QDialog):
    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        self.setWindowTitle(title)
        self.content_layout = QtWidgets.QVBoxLayout(self)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(9)
        self.title_bar = CustomTitleBar(self, chooser_title=title)
        self.content_layout.addWidget(self.title_bar)
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)

    def _apply_theme(self, *_args) -> None:
        colors = theme_manager().colors
        self.setStyleSheet(theme_css(TITLE_BAR_CSS) + f"""
            QDialog {{ background: {colors.window}; color: {colors.text}; }}
        """)
