"""Full-resolution viewer for an archived source image."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from AppTheme import theme_color, theme_css, theme_manager


_active_loads: set[QtCore.QThread] = set()
_quit_hook_installed = False


def _wait_for_loads() -> None:
    for thread in tuple(_active_loads):
        thread.wait()


class _ImageLoadThread(QtCore.QThread):
    loaded = QtCore.Signal(object, str)

    def __init__(self, path: Path) -> None:
        super().__init__(QtWidgets.QApplication.instance())
        self.path = path

    def run(self) -> None:
        reader = QtGui.QImageReader(str(self.path))
        reader.setAutoTransform(True)
        image = reader.read()
        self.loaded.emit(image, "" if not image.isNull() else reader.errorString())


class _ImageCanvas(QtWidgets.QGraphicsView):
    zoomChanged = QtCore.Signal(float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self._image_item = QtWidgets.QGraphicsPixmapItem()
        self._image_item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self._scene.addItem(self._image_item)
        self.setScene(self._scene)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(theme_color("#101827"))
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._fit_mode = True
        self._physical_zoom = 1.0
        self._last_dpr = self._device_ratio()

    def _device_ratio(self) -> float:
        return max(0.1, self.viewport().devicePixelRatioF())

    def set_image(self, image: QtGui.QImage) -> None:
        pixmap = QtGui.QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(1.0)
        self._image_item.setPixmap(pixmap)
        self._scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self._last_dpr = self._device_ratio()
        self.fit_to_window()

    def fit_to_window(self) -> None:
        pixmap = self._image_item.pixmap()
        if pixmap.isNull():
            return
        available = self.viewport().size()
        logical_scale = min(
            max(1, available.width() - 12) / pixmap.width(),
            max(1, available.height() - 12) / pixmap.height(),
            1.0 / self._device_ratio(),
        )
        self.resetTransform()
        self.scale(logical_scale, logical_scale)
        self.centerOn(self._image_item)
        self._physical_zoom = logical_scale * self._device_ratio()
        self._fit_mode = True
        self.zoomChanged.emit(self._physical_zoom * 100.0)

    def set_physical_zoom(self, zoom: float, *, under_mouse: bool = False) -> None:
        if self._image_item.pixmap().isNull():
            return
        zoom = max(0.02, min(32.0, zoom))
        logical_scale = zoom / self._device_ratio()
        current_scale = self.transform().m11()
        if current_scale <= 0:
            return
        previous_anchor = self.transformationAnchor()
        self.setTransformationAnchor(
            QtWidgets.QGraphicsView.AnchorUnderMouse
            if under_mouse else QtWidgets.QGraphicsView.AnchorViewCenter
        )
        self.scale(logical_scale / current_scale, logical_scale / current_scale)
        self.setTransformationAnchor(previous_anchor)
        self._physical_zoom = zoom
        self._fit_mode = False
        self.zoomChanged.emit(zoom * 100.0)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._image_item.pixmap().isNull():
            event.ignore()
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.set_physical_zoom(self._physical_zoom * 1.25 ** (delta / 120.0), under_mouse=True)
            event.accept()
            return
        super().wheelEvent(event)

    def sync_display_scale(self) -> None:
        ratio = self._device_ratio()
        if abs(ratio - self._last_dpr) < 0.001:
            return
        self._last_dpr = ratio
        if self._fit_mode:
            self.fit_to_window()
        else:
            self.set_physical_zoom(self._physical_zoom)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit_mode and not self._image_item.pixmap().isNull():
            self.fit_to_window()
        else:
            self.sync_display_scale()


class ArchiveImageViewer(QtWidgets.QDialog):
    """Display the archived source at fit scale or one source pixel per screen pixel."""

    def __init__(self, path: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{self.path.name} · Image Viewer")
        self.setMinimumSize(520, 400)
        self.resize(1000, 740)
        self._theme_style = """
            QDialog { background: #0b1423; color: #dbe5f2; }
            QLabel { color: #dbe5f2; }
            QPushButton {
                background: #192c44; color: #e4edf8; border: 1px solid #34506b;
                border-radius: 6px; padding: 7px 12px;
            }
            QPushButton:hover { border-color: #49bfe0; }
        """
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(8)
        name = QtWidgets.QLabel(self.path.name)
        name.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        name.setToolTip(str(self.path))
        toolbar.addWidget(name, 1)
        self.fit_button = QtWidgets.QPushButton("Fit to Window")
        self.fit_button.setAccessibleName("Fit image to window")
        toolbar.addWidget(self.fit_button)
        self.actual_button = QtWidgets.QPushButton("100%")
        self.actual_button.setAccessibleName("Show one source pixel per physical display pixel")
        toolbar.addWidget(self.actual_button)
        layout.addLayout(toolbar)

        self.canvas = _ImageCanvas(self)
        layout.addWidget(self.canvas, 1)
        self.info = QtWidgets.QLabel("Loading full-resolution image…")
        self.info.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.info)
        self._dimensions = ""
        self._screen_signal_connected = False
        self.canvas.zoomChanged.connect(self._update_zoom)
        self.fit_button.clicked.connect(self.canvas.fit_to_window)
        self.actual_button.clicked.connect(lambda: self.canvas.set_physical_zoom(1.0))
        self.fit_button.setEnabled(False)
        self.actual_button.setEnabled(False)
        QtCore.QTimer.singleShot(0, self._start_load)

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css(self._theme_style))
        if hasattr(self, "canvas"):
            self.canvas.setBackgroundBrush(theme_color("#101827"))

    def _start_load(self) -> None:
        global _quit_hook_installed
        if not self.path.is_file():
            self.info.setText("Image unavailable: source file does not exist.")
            return
        app = QtWidgets.QApplication.instance()
        if app is not None and not _quit_hook_installed:
            app.aboutToQuit.connect(_wait_for_loads)
            _quit_hook_installed = True
        loader = _ImageLoadThread(self.path)
        _active_loads.add(loader)
        loader.loaded.connect(self._image_loaded)
        loader.finished.connect(lambda: _active_loads.discard(loader))
        loader.finished.connect(loader.deleteLater)
        loader.start()

    def _image_loaded(self, image: QtGui.QImage, error: str) -> None:
        if image.isNull():
            self.info.setText(f"Could not open image: {error or 'unsupported image data'}")
            return
        self._dimensions = f"{image.width()} × {image.height()} px"
        self.canvas.set_image(image)
        self.fit_button.setEnabled(True)
        self.actual_button.setEnabled(True)

    def _update_zoom(self, percent: float) -> None:
        self.info.setText(f"{self._dimensions}  ·  {percent:.0f}%")

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_signal_connected:
            handle.screenChanged.connect(lambda _screen: QtCore.QTimer.singleShot(0, self.canvas.sync_display_scale))
            self._screen_signal_connected = True
        QtCore.QTimer.singleShot(0, self.canvas.sync_display_scale)
