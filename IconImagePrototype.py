"""Standalone window for subscription-backed Codex image generation."""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from AppTheme import theme_color, theme_css, theme_manager
from CodexImageService import CodexImageService


IMAGE_REFERENCE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _dropped_files(mime: QtCore.QMimeData) -> tuple[Path, ...]:
    urls = mime.urls() if mime.hasUrls() else []
    if not urls or not all(url.isLocalFile() for url in urls):
        return ()
    return tuple(Path(url.toLocalFile()) for url in urls)


class ImagePromptEdit(QtWidgets.QPlainTextEdit):
    filesDropped = QtCore.Signal(object)
    dragActive = QtCore.Signal(bool)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if _dropped_files(event.mimeData()):
            event.acceptProposedAction()
            self.dragActive.emit(True)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if _dropped_files(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self.dragActive.emit(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        files = _dropped_files(event.mimeData())
        if files:
            event.acceptProposedAction()
            self.dragActive.emit(False)
            self.filesDropped.emit(files)
        else:
            super().dropEvent(event)


class ImageDropOverlay(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        overlay = theme_color("#080d19")
        overlay.setAlpha(237)
        painter.fillRect(self.rect(), overlay)
        bounds = QtCore.QRectF(self.rect()).adjusted(18, 18, -18, -18)
        painter.setPen(QtGui.QPen(theme_color("#36c9e8"), 2, QtCore.Qt.DashLine))
        painter.setBrush(theme_color("#101d2d"))
        painter.drawRoundedRect(bounds, 18, 18)
        center = bounds.center()
        for offset in (-18, 0, 18):
            icon = QtCore.QRectF(center.x() - 24 + offset, center.y() - 78 + abs(offset) // 3, 48, 48)
            painter.setPen(QtGui.QPen(theme_color("#7bd7ea"), 2))
            painter.setBrush(theme_color("#182b3d"))
            painter.drawRoundedRect(icon, 7, 7)
        painter.setPen(theme_color("#e3ebf5"))
        title_font = painter.font()
        title_font.setPointSize(19)
        title_font.setWeight(QtGui.QFont.DemiBold)
        painter.setFont(title_font)
        painter.drawText(QtCore.QRectF(bounds.left(), center.y() - 5, bounds.width(), 36), QtCore.Qt.AlignCenter, "Add images")
        detail_font = painter.font()
        detail_font.setPointSize(10)
        detail_font.setWeight(QtGui.QFont.Normal)
        painter.setFont(detail_font)
        painter.setPen(theme_color("#a9b8ca"))
        painter.drawText(
            QtCore.QRectF(bounds.left() + 12, center.y() + 34, bounds.width() - 24, 48),
            QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
            "Drag PNG or JPEG files here to add them to your prompt",
        )


class ReferenceImageTile(QtWidgets.QFrame):
    removed = QtCore.Signal(object)

    def __init__(self, path: Path, image: QtGui.QImage) -> None:
        super().__init__()
        self.setObjectName("ReferenceImage")
        self.setFixedSize(174, 72)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(7, 7, 5, 7)
        row.setSpacing(6)
        thumbnail = QtWidgets.QLabel()
        thumbnail.setFixedSize(54, 54)
        thumbnail.setAlignment(QtCore.Qt.AlignCenter)
        thumbnail.setPixmap(QtGui.QPixmap.fromImage(image).scaled(
            54, 54, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation,
        ))
        row.addWidget(thumbnail)
        name = QtWidgets.QLabel()
        name.setText(name.fontMetrics().elidedText(path.name, QtCore.Qt.ElideMiddle, 74))
        name.setToolTip(path.name)
        row.addWidget(name, 1)
        self.remove_button = QtWidgets.QToolButton()
        self.remove_button.setObjectName("RemoveReference")
        self.remove_button.setText("×")
        self.remove_button.setToolTip(f"Remove {path.name}")
        self.remove_button.setAccessibleName(f"Remove {path.name}")
        self.remove_button.clicked.connect(lambda: self.removed.emit(path))
        row.addWidget(self.remove_button)


class GenerationActivity(QtWidgets.QWidget):
    """An indeterminate animation: time passes, but no progress is invented."""

    def __init__(self) -> None:
        super().__init__()
        self._clock = QtCore.QElapsedTimer()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)
        self._caption = "Creating your image"
        self.setMinimumHeight(150)

    def start(self) -> None:
        self._clock.start()
        self._caption = "Creating your image"
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def set_caption(self, caption: str) -> None:
        self._caption = caption
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bounds = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        background = QtGui.QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        background.setColorAt(0, theme_color("#101827"))
        background.setColorAt(1, theme_color("#101d2d"))
        painter.setPen(QtGui.QPen(theme_color("#273348"), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(bounds, 16, 16)

        seconds = self._clock.elapsed() / 1000 if self._clock.isValid() else 0
        scale = min(1.0, max(0.4, (bounds.height() - 100) / 230))
        radius = 91 * scale
        center_y = max(radius + 12, min(bounds.height() * 0.42, bounds.height() - radius - 75))
        center = QtCore.QPointF(bounds.center().x(), center_y)
        painter.save()
        painter.translate(center)
        painter.rotate(seconds * 28)
        for arc_radius, span, width, color, start in (
            (76, 145, 5, "#36c9e8", 0),
            (61, 100, 4, "#7bb3d0", 165),
            (91, 55, 3, "#53d9df", 290),
        ):
            pen = QtGui.QPen(theme_color(color), max(2, width * scale))
            pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            scaled_radius = arc_radius * scale
            painter.drawArc(
                QtCore.QRectF(-scaled_radius, -scaled_radius, scaled_radius * 2, scaled_radius * 2),
                start * 16, span * 16,
            )
        painter.restore()

        glow = QtGui.QRadialGradient(center, 48 * scale)
        glow_color = theme_color("#36c9e8")
        glow_color.setAlpha(105 + int(35 * math.sin(seconds * 2)))
        glow.setColorAt(0, glow_color)
        glow_color.setAlpha(0)
        glow.setColorAt(1, glow_color)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, 52 * scale, 52 * scale)
        painter.setPen(QtGui.QPen(theme_color("#dbe5f2"), 2))
        painter.drawLine(QtCore.QPointF(center.x() - 15 * scale, center.y()), QtCore.QPointF(center.x() + 15 * scale, center.y()))
        painter.drawLine(QtCore.QPointF(center.x(), center.y() - 15 * scale), QtCore.QPointF(center.x(), center.y() + 15 * scale))

        painter.setPen(theme_color("#e3ebf5"))
        title_font = painter.font()
        title_font.setPointSize(14)
        title_font.setWeight(QtGui.QFont.DemiBold)
        painter.setFont(title_font)
        title_y = center.y() + radius + 10
        painter.drawText(QtCore.QRectF(12, title_y, self.width() - 24, 26), QtCore.Qt.AlignCenter, self._caption)
        detail_font = painter.font()
        detail_font.setPointSize(10)
        detail_font.setWeight(QtGui.QFont.Normal)
        painter.setFont(detail_font)
        painter.setPen(theme_color("#a9b8ca"))
        painter.drawText(
            QtCore.QRectF(12, title_y + 29, self.width() - 24, 22),
            QtCore.Qt.AlignCenter,
            f"{int(seconds // 60):02}:{int(seconds % 60):02} elapsed  ·  Waiting for Codex",
        )


class IconImagePrototype(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IconForge · Codex")
        self.resize(720, 680)
        self.setAcceptDrops(True)
        self._theme_style = """
            QWidget { background: #080d19; color: #dbe5f2; font-size: 11pt; }
            QLabel#SectionLabel { color: #a9b8ca; }
            QFrame#ConnectionCard { background: #101827; border: 1px solid #2b394d; border-radius: 12px; }
            QPlainTextEdit { background: #0b1423; border: 1px solid #273348; border-radius: 9px; padding: 10px; }
            QPlainTextEdit:focus { border-color: #36c9e8; }
            QPushButton { background: #182335; border: 1px solid #2b394d; border-radius: 9px; padding: 9px 14px; }
            QPushButton:hover:enabled { background: #1b3445; }
            QPushButton:disabled { color: #738298; background: #131c2a; border-color: #222d3e; }
            QPushButton#GenerateButton { background: #36c9e8; border-color: #36c9e8; color: #071b26; font-weight: 700; }
            QPushButton#GenerateButton:hover:enabled { background: #6bd7ec; }
            QLabel#Preview { background: #0b1423; border: 1px solid #273348; border-radius: 15px; color: #a9b8ca; }
            QLabel#Progress { color: #a9b8ca; }
            QScrollArea#ReferenceStrip, QWidget#ReferenceContent { background: transparent; border: 0; }
            QFrame#ReferenceImage { background: #101827; border: 1px solid #273348; border-radius: 9px; }
            QFrame#ReferenceImage QLabel { background: transparent; border: 0; }
            QToolButton#RemoveReference { background: transparent; border: 0; color: #a9b8ca; font-size: 14pt; padding: 0; }
            QToolButton#RemoveReference:hover { color: #e3ebf5; }
        """
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)
        self._service = CodexImageService(self)
        self._service_start_requested = False
        self._reference_images: dict[Path, ReferenceImageTile] = {}
        self._original_path: Path | None = None
        self._source_pixmap = QtGui.QPixmap()
        self._cancel_waiting = False
        self._generation_active = False
        self._close_when_idle = False
        self._force_close = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(13)
        connection_card = QtWidgets.QFrame()
        connection_card.setObjectName("ConnectionCard")
        connection_row = QtWidgets.QHBoxLayout(connection_card)
        connection_row.setContentsMargins(14, 11, 14, 11)
        self.connection_card = connection_card
        self.connection_row = connection_row
        self.connection_dot = QtWidgets.QLabel("●")
        self.connection_dot.setStyleSheet(theme_css("color: #e4ad62; background: transparent;"))
        connection_row.addWidget(self.connection_dot)
        self.connection_label = QtWidgets.QLabel("Connecting to Codex...")
        self.connection_label.setWordWrap(True)
        self.connection_label.setStyleSheet("background: transparent;")
        connection_row.addWidget(self.connection_label, 1)

        self.sign_in_button = QtWidgets.QPushButton("Sign in with ChatGPT")
        self.sign_in_button.setEnabled(False)
        self.sign_in_button.clicked.connect(self._service.sign_in)
        connection_row.addWidget(self.sign_in_button, alignment=QtCore.Qt.AlignRight)
        connection_card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
        layout.addWidget(connection_card)

        self.preview_label = QtWidgets.QLabel("The generated image will appear here.")
        self.preview_label.setObjectName("Preview")
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumHeight(150)
        self.preview_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.activity = GenerationActivity()
        self.preview_stack = QtWidgets.QStackedWidget()
        self.preview_stack.addWidget(self.preview_label)
        self.preview_stack.addWidget(self.activity)
        self.preview_stack.hide()
        layout.addWidget(self.preview_stack, 1)

        prompt_label = QtWidgets.QLabel("Image prompt")
        prompt_label.setObjectName("SectionLabel")
        prompt_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(prompt_label)
        self.reference_scroll = QtWidgets.QScrollArea()
        self.reference_scroll.setObjectName("ReferenceStrip")
        self.reference_scroll.setWidgetResizable(False)
        self.reference_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.reference_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.reference_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.reference_scroll.setFixedHeight(86)
        self.reference_content = QtWidgets.QWidget()
        self.reference_content.setObjectName("ReferenceContent")
        self.reference_layout = QtWidgets.QHBoxLayout(self.reference_content)
        self.reference_layout.setContentsMargins(0, 0, 0, 0)
        self.reference_layout.setSpacing(8)
        self.reference_scroll.setWidget(self.reference_content)
        self.reference_scroll.hide()
        layout.addWidget(self.reference_scroll)
        self.prompt_edit = ImagePromptEdit()
        self.prompt_edit.setPlainText("Generate a laughing skull icon with a transparent background.")
        self.prompt_edit.setMaximumHeight(100)
        self.prompt_edit.textChanged.connect(self._refresh_buttons)
        self.prompt_edit.filesDropped.connect(self._add_reference_images)
        self.prompt_edit.dragActive.connect(self._show_drop_overlay)
        layout.addWidget(self.prompt_edit)

        actions = QtWidgets.QHBoxLayout()
        self.generate_button = QtWidgets.QPushButton("Generate")
        self.generate_button.setObjectName("GenerateButton")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self._generate_or_cancel)
        actions.addWidget(self.generate_button)
        layout.addLayout(actions)

        self.progress_label = QtWidgets.QLabel("Waiting for connection...")
        self.progress_label.setObjectName("Progress")
        self.progress_label.setWordWrap(True)
        self.progress_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        layout.addWidget(self.progress_label)
        self._idle_spacer = QtWidgets.QWidget()
        self._idle_spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        layout.addWidget(self._idle_spacer, 1)

        self._service.connectionChanged.connect(self._on_connection)
        self._service.availabilityChanged.connect(self._refresh_buttons)
        self._service.busyChanged.connect(self._on_busy)
        self._service.progress.connect(self.progress_label.setText)
        self._service.failed.connect(self._on_failed)
        self._service.signInUrl.connect(self._open_sign_in)
        self._service.imageReady.connect(self._on_image_ready)
        self._service.cancellationConfirmed.connect(self._on_cancellation_confirmed)

        self._close_timeout = QtCore.QTimer(self)
        self._close_timeout.setSingleShot(True)
        self._close_timeout.timeout.connect(self._close_after_timeout)
        self.drop_overlay = ImageDropOverlay(self)
        self.drop_overlay.hide()
        QtCore.QTimer.singleShot(0, self.start_service)

    def start_service(self) -> None:
        if self._service_start_requested:
            return
        self._service_start_requested = True
        self._service.start()

    @property
    def service(self) -> CodexImageService:
        return self._service

    @property
    def original_path(self) -> Path | None:
        return self._original_path

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css(self._theme_style))
        if hasattr(self, "connection_dot"):
            color = "#62d6b0" if self._service.is_ready else "#e4ad62" if self._service.is_connected else "#e27f88"
            self.connection_dot.setStyleSheet(theme_css(f"color: {color}; background: transparent;"))
        if hasattr(self, "drop_overlay"):
            self.drop_overlay.update()
        if hasattr(self, "activity"):
            self.activity.update()

    def _on_connection(self, message: str) -> None:
        self.connection_label.setText(message)
        ready = self._service.is_ready
        self.connection_label.setVisible(not ready)
        self.connection_dot.setVisible(not ready)
        self.connection_card.setStyleSheet(
            "QFrame#ConnectionCard { background: transparent; border: 0; }" if ready else ""
        )
        margin = 0 if ready else 14
        self.connection_row.setContentsMargins(margin, 0 if ready else 11, margin, 0 if ready else 11)
        color = "#62d6b0" if ready else "#e4ad62" if self._service.is_connected else "#e27f88"
        self.connection_dot.setStyleSheet(theme_css(f"color: {color}; background: transparent;"))
        self.sign_in_button.setText("Switch ChatGPT account" if self._service.is_chatgpt_signed_in else "Sign in with ChatGPT")
        if ready and self.progress_label.text() == "Waiting for connection...":
            self.progress_label.setText("Ready. Enter a prompt and select Generate.")
        self._refresh_buttons()

    def _refresh_buttons(self, *_args) -> None:
        busy = self._service.is_busy
        self.sign_in_button.setEnabled(self._service.is_connected and not busy)
        for tile in self._reference_images.values():
            tile.remove_button.setEnabled(not busy)
        if busy and self._generation_active:
            self.generate_button.setText("Cancelling…" if self._cancel_waiting else "Cancel")
            self.generate_button.setEnabled(not self._cancel_waiting)
        else:
            self.generate_button.setText("Generate")
            self.generate_button.setEnabled(
                self._service.is_ready and not busy and bool(self.prompt_edit.toPlainText().strip())
            )

    def _on_busy(self, busy: bool) -> None:
        if busy:
            self._show_drop_overlay(False)
        if not busy:
            self._cancel_waiting = False
            self._generation_active = False
            self.activity.stop()
        self._refresh_buttons()
        if not busy and self._close_when_idle:
            QtCore.QTimer.singleShot(0, self.close)

    def _show_drop_overlay(self, active: bool) -> None:
        if active and not self._service.is_busy:
            self.drop_overlay.setGeometry(self.rect())
            self.drop_overlay.raise_()
            self.drop_overlay.show()
        else:
            self.drop_overlay.hide()

    def _add_reference_images(self, files: tuple[Path, ...]) -> None:
        if self._service.is_busy:
            self.progress_label.setText("Finish the current request before adding reference images.")
            return
        errors: list[str] = []
        added = 0
        for source in files:
            try:
                path = source.resolve(strict=True)
            except (OSError, RuntimeError):
                errors.append(f"{source.name}: file not found")
                continue
            if path in self._reference_images:
                continue
            if not path.is_file() or path.suffix.lower() not in IMAGE_REFERENCE_SUFFIXES:
                errors.append(f"{source.name}: PNG or JPEG images only")
                continue
            reader = QtGui.QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(54, 54, QtCore.Qt.KeepAspectRatio))
            thumbnail = reader.read()
            if thumbnail.isNull():
                errors.append(f"{source.name}: could not read image")
                continue
            tile = ReferenceImageTile(path, thumbnail)
            tile.removed.connect(self._remove_reference_image)
            self.reference_layout.addWidget(tile)
            self._reference_images[path] = tile
            added += 1
        self.reference_content.adjustSize()
        self.reference_scroll.setVisible(bool(self._reference_images))
        if errors:
            self.progress_label.setText("; ".join(errors[:3]) + ("; more files skipped" if len(errors) > 3 else ""))
        elif added:
            self.progress_label.setText(f"{added} reference image{'s' if added != 1 else ''} added to the prompt.")
        self._refresh_buttons()

    def _remove_reference_image(self, path: Path) -> None:
        if self._service.is_busy:
            return
        tile = self._reference_images.pop(path, None)
        if tile is None:
            return
        self.reference_layout.removeWidget(tile)
        tile.deleteLater()
        self.reference_content.adjustSize()
        self.reference_scroll.setVisible(bool(self._reference_images))

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if _dropped_files(event.mimeData()) and not self._service.is_busy:
            event.acceptProposedAction()
            self._show_drop_overlay(True)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if _dropped_files(event.mimeData()) and not self._service.is_busy:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._show_drop_overlay(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        files = _dropped_files(event.mimeData())
        self._show_drop_overlay(False)
        if files:
            event.acceptProposedAction()
            self._add_reference_images(files)
        else:
            super().dropEvent(event)

    def _generate_or_cancel(self) -> None:
        if self._generation_active and self._service.is_busy:
            self._cancel()
        else:
            self._generate()

    def _generate(self) -> None:
        if self._service.is_busy or not self._service.is_ready or not self.prompt_edit.toPlainText().strip():
            return
        self._original_path = None
        self._source_pixmap = QtGui.QPixmap()
        self.preview_label.setPixmap(QtGui.QPixmap())
        self.preview_label.setText("The generated image will appear here.")
        self.activity.start()
        self.preview_stack.setCurrentWidget(self.activity)
        self._idle_spacer.hide()
        self.preview_stack.show()
        self._generation_active = True
        self._refresh_buttons()
        self._service.generate(self.prompt_edit.toPlainText(), tuple(self._reference_images))
        self._refresh_buttons()

    def _cancel(self) -> None:
        if not self._generation_active or not self._service.is_busy or self._cancel_waiting:
            return
        self._cancel_waiting = True
        if self.preview_stack.currentWidget() is self.activity:
            self.activity.set_caption("Requesting cancellation")
        self._refresh_buttons()
        self._service.cancel()

    def _open_sign_in(self, url: str) -> None:
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)):
            self.progress_label.setText("Could not open the ChatGPT sign-in page in your browser.")
            self._cancel()

    def _on_failed(self, message: str) -> None:
        self._generation_active = False
        self.progress_label.setText(message)
        if self.preview_stack.currentWidget() is self.activity:
            self.activity.stop()
            self.preview_stack.hide()
            self._idle_spacer.show()
        self._refresh_buttons()

    def _on_cancellation_confirmed(self) -> None:
        self.progress_label.setText("Cancellation confirmed.")
        if self.preview_stack.currentWidget() is self.activity:
            self.activity.stop()
            self.preview_stack.hide()
            self._idle_spacer.show()

    def _on_image_ready(self, path: str) -> None:
        reader = QtGui.QImageReader(path)
        image = reader.read()
        if image.isNull():
            self._on_failed(f"Codex returned a file that could not be decoded: {reader.errorString()}")
            return
        pixmap = QtGui.QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_failed("Codex returned an image that could not be displayed.")
            return
        self._original_path = Path(path)
        self._source_pixmap = pixmap
        self.activity.stop()
        self.preview_stack.setCurrentWidget(self.preview_label)
        self._update_preview()
        self.progress_label.setText(f"Image ready: {image.width()} × {image.height()} pixels.")
        self._refresh_buttons()

    def _update_preview(self) -> None:
        if self._source_pixmap.isNull():
            return
        target = self.preview_label.size() - QtCore.QSize(24, 24)
        if target.width() <= 0 or target.height() <= 0:
            return
        self.preview_label.setPixmap(
            self._source_pixmap.scaled(target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_preview()
        if hasattr(self, "drop_overlay"):
            self.drop_overlay.setGeometry(self.rect())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._service.is_busy and not self._force_close:
            event.ignore()
            self._close_when_idle = True
            self._cancel()
            self._close_timeout.start(20_000)
            return
        self._close_timeout.stop()
        self._service.shutdown()
        super().closeEvent(event)

    def _close_after_timeout(self) -> None:
        if not self._service.is_busy:
            return
        self._force_close = True
        self.progress_label.setText("Cancellation was not confirmed; ending the Codex connection.")
        self.close()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    theme_manager()
    window = IconImagePrototype()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
