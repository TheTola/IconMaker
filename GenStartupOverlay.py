"""Theme-aware startup sequence for the image-generator window."""

from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from AppTheme import theme_manager


ARTWORK_PATH = Path(__file__).resolve().parent / "assets" / "startup-loading.png"


def choose_startup_duration_ms() -> int:
    return random.SystemRandom().randint(4500, 6000)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


class StartupOverlay(QtWidgets.QWidget):
    """Covers the live generator window until the awakening sequence ends."""

    finished = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget, appearance: str) -> None:
        super().__init__(parent)
        self._artwork = QtGui.QPixmap(str(ARTWORK_PATH))
        if self._artwork.isNull():
            raise FileNotFoundError(f"Startup artwork is unavailable: {ARTWORK_PATH}")
        self.duration_ms = choose_startup_duration_ms()
        self._appearance = appearance
        self._clock = QtCore.QElapsedTimer()
        self._running = False
        self._fade_opacity = 1.0
        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.setInterval(33)
        self._frame_timer.timeout.connect(self.update)
        self._end_timer = QtCore.QTimer(self)
        self._end_timer.setSingleShot(True)
        self._end_timer.timeout.connect(self._begin_fade)
        self._fade = QtCore.QPropertyAnimation(self, b"fadeOpacity", self)
        self._fade.setDuration(270)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._fade.finished.connect(self._finish)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.sync_geometry()
        parent.installEventFilter(self)
        QtWidgets.QApplication.instance().installEventFilter(self)
        theme_manager().changed.connect(self.set_appearance)

    def get_fade_opacity(self) -> float:
        return self._fade_opacity

    def set_fade_opacity(self, value: float) -> None:
        self._fade_opacity = value
        self.update()

    fadeOpacity = QtCore.Property(float, get_fade_opacity, set_fade_opacity)

    def set_appearance(self, appearance: str) -> None:
        self._appearance = appearance
        self.update()

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        title_bar = getattr(parent, "title_bar", None)
        top = 0
        if title_bar is not None:
            layout = parent.layout()
            margin = layout.contentsMargins().top() if layout is not None else 0
            top = max(margin + title_bar.height(), title_bar.y() + title_bar.height())
        top = min(top, parent.height())
        self.setGeometry(0, top, parent.width(), parent.height() - top)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.raise_()
        self.setFocus(QtCore.Qt.OtherFocusReason)
        self._clock.start()
        self._frame_timer.start()
        self._end_timer.start(self.duration_ms - self._fade.duration())

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._frame_timer.stop()
        self._end_timer.stop()
        self._fade.stop()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        try:
            theme_manager().changed.disconnect(self.set_appearance)
        except (RuntimeError, TypeError):
            pass

    def _begin_fade(self) -> None:
        self._frame_timer.stop()
        self._fade.start()

    def _finish(self) -> None:
        self.stop()
        self.hide()
        parent = self.parentWidget()
        if parent is not None:
            parent.startup_overlay = None
        self.finished.emit()
        self.deleteLater()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        parent = self.parentWidget()
        if watched is parent:
            if event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show, QtCore.QEvent.LayoutRequest):
                self.sync_geometry()
                self.raise_()
            return False
        if self._running and isinstance(watched, QtWidgets.QWidget) and watched.window() is parent:
            if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride):
                if (event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_F4
                        and event.modifiers() & QtCore.Qt.AltModifier):
                    parent.close()
                return True
        return False

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        event.accept()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.setOpacity(self._fade_opacity)
        width, height = self.width(), self.height()
        light = self._appearance == "Light"
        progress = min(1.0, self._clock.elapsed() / self.duration_ms) if self._running else 0.0
        power = _smoothstep((progress - 0.08) / 0.50)
        awakening = _smoothstep((progress - 0.24) / 0.22) * (1.0 - _smoothstep((progress - 0.70) / 0.22))
        pulse = (math.sin(self._clock.elapsed() * 0.009) + 1.0) / 2.0 if self._running else 0.0

        background = QtGui.QLinearGradient(0, 0, width, height)
        background.setColorAt(0, QtGui.QColor("#f2faff" if light else "#050b17"))
        background.setColorAt(1, QtGui.QColor("#d8edf9" if light else "#0b1d32"))
        painter.fillRect(self.rect(), background)

        center = QtCore.QPointF(width / 2, height / 2)
        radius = max(width * 0.48, height * 0.58)
        glow = QtGui.QRadialGradient(center, radius)
        glow.setColorAt(0, QtGui.QColor(25, 159, 222, round((34 if light else 49) * power)))
        glow.setColorAt(0.55, QtGui.QColor(16, 126, 206, round((12 if light else 21) * power)))
        glow.setColorAt(1, QtGui.QColor(16, 126, 206, 0))
        painter.fillRect(self.rect(), glow)

        artwork_width = min(width * 0.84, 1550.0)
        artwork_height = artwork_width * self._artwork.height() / self._artwork.width()
        scale = 1.0 + 0.012 * awakening * pulse
        art_rect = QtCore.QRectF(
            center.x() - artwork_width * scale / 2,
            center.y() - artwork_height * scale / 2,
            artwork_width * scale,
            artwork_height * scale,
        )

        if light:
            painter.setPen(QtGui.QPen(QtGui.QColor("#168daf"), 1.5))
            painter.setBrush(QtGui.QColor(8, 25, 45, 238))
            painter.drawRoundedRect(art_rect.adjusted(-22, -18, 22, 18), 24, 24)

        accent = QtGui.QColor("#1688b9" if light else "#34ceff")
        ring_center = QtCore.QPointF(art_rect.left() + art_rect.width() * 0.17, art_rect.center().y())
        ring_size = min(art_rect.height() * 0.79, height * 0.27)
        painter.save()
        painter.translate(ring_center)
        for index, direction in enumerate((1, -1, 1)):
            painter.save()
            painter.rotate(direction * (progress * (65 + index * 24)))
            pen_color = QtGui.QColor(accent)
            pen_color.setAlpha(round((26 + 64 * power + 35 * awakening * pulse) * (1.0 - index * 0.20)))
            painter.setPen(QtGui.QPen(pen_color, 1.4 if index == 0 else 1.0))
            ring = ring_size * (1.0 + index * 0.22)
            painter.drawArc(QtCore.QRectF(-ring, -ring, 2 * ring, 2 * ring), 24 * 16, (66 + index * 18) * 16)
            painter.drawArc(QtCore.QRectF(-ring, -ring, 2 * ring, 2 * ring), 202 * 16, (45 + index * 18) * 16)
            painter.restore()
        painter.restore()

        painter.save()
        painter.setOpacity(self._fade_opacity * (0.32 + 0.68 * power))
        painter.drawPixmap(art_rect, self._artwork, QtCore.QRectF(self._artwork.rect()))
        painter.restore()

        if 0.16 < progress < 0.88:
            scan_fraction = (progress - 0.16) / 0.72
            scan_x = art_rect.left() + art_rect.width() * scan_fraction
            scan = QtGui.QLinearGradient(scan_x - 20, 0, scan_x + 20, 0)
            scan.setColorAt(0, QtGui.QColor(56, 211, 255, 0))
            scan.setColorAt(0.5, QtGui.QColor(85, 228, 255, round(25 + 40 * awakening)))
            scan.setColorAt(1, QtGui.QColor(56, 211, 255, 0))
            painter.fillRect(QtCore.QRectF(scan_x - 20, art_rect.top(), 40, art_rect.height()), scan)

        line_y = art_rect.bottom() + min(54.0, height * 0.065)
        line_width = min(width * 0.55, 900.0)
        line = QtGui.QLinearGradient(center.x() - line_width / 2, 0, center.x() + line_width / 2, 0)
        line.setColorAt(0, QtGui.QColor(accent.red(), accent.green(), accent.blue(), 0))
        line.setColorAt(0.5, QtGui.QColor(accent.red(), accent.green(), accent.blue(), round(35 + 95 * power)))
        line.setColorAt(1, QtGui.QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.fillRect(QtCore.QRectF(center.x() - line_width / 2, line_y, line_width, 2), line)

        for index in range(16):
            angle = index * math.tau / 16 + progress * (0.35 if index % 2 else -0.25)
            orbit_x = ring_center.x() + math.cos(angle) * ring_size * 1.57
            orbit_y = ring_center.y() + math.sin(angle) * ring_size * 1.12
            particle = QtGui.QColor(accent)
            particle.setAlpha(round((13 + 72 * power) * (0.5 + 0.5 * math.sin(index + progress * 16) ** 2)))
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(particle)
            painter.drawEllipse(QtCore.QPointF(orbit_x, orbit_y), 1.5, 1.5)

        painter.end()
