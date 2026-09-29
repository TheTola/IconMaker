"""Animated background for the Icon Forge main window."""

from __future__ import annotations

import math
import random

from PySide6 import QtCore, QtGui, QtWidgets

from AppTheme import ThemeManager


class AppAmbientRoot(QtWidgets.QWidget):
    def __init__(self, theme: ThemeManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppRoot")
        self._theme = theme
        rng = random.Random(1701)
        self._stars = tuple(
            (rng.random(), rng.random(), rng.uniform(0.5, 1.8), rng.uniform(0, math.tau))
            for _ in range(145)
        )
        self._dust = tuple(
            (x, 0.75 + 0.08 * math.sin(x * 5) + rng.gauss(0, 0.09), rng.uniform(0.3, 0.8))
            for x in (rng.random() for _ in range(95))
        )
        self._motes = tuple(
            (rng.random(), rng.uniform(0.6, 1.05), rng.uniform(0.7, 1.7), rng.uniform(0, math.tau))
            for _ in range(24)
        )
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._advance)
        theme.changed.connect(self._appearance_changed)

    def _appearance_changed(self, _appearance: str) -> None:
        self.update()

    def _advance(self) -> None:
        if not self.window().isMinimized():
            self.update()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        width, height = float(self.width()), float(self.height())
        if width < 2 or height < 2:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        outline = QtGui.QPainterPath()
        outline.addRoundedRect(QtCore.QRectF(0.5, 0.5, width - 1, height - 1), 18, 18)
        painter.setClipPath(outline)
        seconds = self._clock.elapsed() / 1000.0
        if self._theme.appearance == "Light":
            self._paint_radiance(painter, width, height, seconds)
        else:
            self._paint_stars(painter, width, height, seconds)
        painter.setClipping(False)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(self._theme.colors.border), 1))
        painter.drawPath(outline)

    @staticmethod
    def _paint_glow(
        painter: QtGui.QPainter,
        center: QtCore.QPointF,
        x_radius: float,
        y_radius: float,
        color: QtGui.QColor,
        opacity: int,
    ) -> None:
        painter.save()
        painter.translate(center)
        painter.scale(x_radius, y_radius)
        glow = QtGui.QRadialGradient(QtCore.QPointF(0, 0), 1)
        core = QtGui.QColor(color)
        core.setAlpha(opacity)
        glow.setColorAt(0, core)
        core.setAlpha(opacity // 3)
        glow.setColorAt(0.5, core)
        core.setAlpha(0)
        glow.setColorAt(1, core)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QtCore.QRectF(-1, -1, 2, 2))
        painter.restore()

    def _paint_stars(self, painter: QtGui.QPainter, width: float, height: float, seconds: float) -> None:
        colors = self._theme.colors
        background = QtGui.QLinearGradient(0, 0, width, height)
        background.setColorAt(0, QtGui.QColor(colors.window))
        background.setColorAt(0.55, QtGui.QColor(colors.panel))
        background.setColorAt(1, QtGui.QColor(colors.window))
        painter.fillRect(QtCore.QRectF(0, 0, width, height), background)

        for x, y, x_radius, y_radius, color, opacity in (
            (0.16 + 0.035 * math.sin(seconds * 0.16), 0.78, 0.5, 0.42, colors.accent, 70),
            (0.83 + 0.03 * math.cos(seconds * 0.13), 0.74, 0.52, 0.4, colors.selection, 95),
            (0.55, 0.31, 0.62, 0.48, colors.accent, 25),
        ):
            self._paint_glow(
                painter,
                QtCore.QPointF(x * width, y * height),
                x_radius * width,
                y_radius * height,
                QtGui.QColor(color),
                opacity,
            )

        dust_color = QtGui.QColor(colors.accent)
        dust_color.setAlpha(35)
        painter.setPen(QtCore.Qt.NoPen)
        for nx, ny, size in self._dust:
            painter.setBrush(dust_color)
            painter.drawEllipse(QtCore.QPointF(nx * width, ny * height), size, size)

        for index, (nx, ny, size, phase) in enumerate(self._stars):
            x = nx * width + 3 * math.sin(seconds * 0.11 + phase)
            y = ny * height + 2 * math.cos(seconds * 0.09 + phase)
            shimmer = 0.5 + 0.5 * math.sin(seconds * (0.65 + index % 5 * 0.1) + phase)
            color = QtGui.QColor(colors.accent if index % 7 == 0 else colors.text)
            color.setAlpha(round((45 if index % 11 else 90) + 100 * shimmer))
            if index % 17 == 0:
                self._paint_glow(
                    painter, QtCore.QPointF(x, y), 9, 9, color, round(20 + 28 * shimmer)
                )
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QtCore.QPointF(x, y), size, size)
            if index % 13 == 0:
                color.setAlpha(round(30 + 60 * shimmer))
                painter.setPen(QtGui.QPen(color, 0.9))
                painter.drawLine(QtCore.QPointF(x - 7, y), QtCore.QPointF(x + 7, y))
                painter.drawLine(QtCore.QPointF(x, y - 7), QtCore.QPointF(x, y + 7))

        meteor = (seconds + 3) % 15
        if meteor < 2.4:
            progress = meteor / 2.4
            head = QtCore.QPointF(width * (-0.1 + 1.25 * progress), height * (0.89 - 0.17 * progress))
            tail = QtCore.QPointF(head.x() - 105, head.y() + 35)
            trail = QtGui.QLinearGradient(tail, head)
            dim = QtGui.QColor(colors.accent)
            dim.setAlpha(0)
            bright = QtGui.QColor(colors.text)
            bright.setAlpha(round(150 * math.sin(math.pi * progress)))
            trail.setColorAt(0, dim)
            trail.setColorAt(1, bright)
            painter.setPen(QtGui.QPen(QtGui.QBrush(trail), 2.3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            painter.drawLine(tail, head)
            self._paint_glow(painter, head, 12, 12, bright, bright.alpha() // 3)

    def _paint_radiance(self, painter: QtGui.QPainter, width: float, height: float, seconds: float) -> None:
        colors = self._theme.colors
        background = QtGui.QLinearGradient(0, 0, width, height)
        background.setColorAt(0, QtGui.QColor(colors.panel))
        background.setColorAt(0.56, QtGui.QColor(colors.window))
        background.setColorAt(1, QtGui.QColor(colors.raised))
        painter.fillRect(QtCore.QRectF(0, 0, width, height), background)

        center = QtCore.QPointF(
            width * (0.52 + 0.03 * math.sin(seconds * 0.18)),
            height * (0.89 + 0.015 * math.cos(seconds * 0.22)),
        )
        self._paint_glow(
            painter, center, width * 0.68, height * 0.67,
            QtGui.QColor(colors.selection_text), 180,
        )
        self._paint_glow(
            painter,
            QtCore.QPointF(width * (0.18 + 0.025 * math.sin(seconds * 0.14)), height * 0.77),
            width * 0.44, height * 0.45, QtGui.QColor(colors.accent_soft), 105,
        )
        self._paint_glow(
            painter,
            QtCore.QPointF(width * 0.9, height * 0.41),
            width * 0.32, height * 0.49, QtGui.QColor(colors.selection_text), 95,
        )

        reach = 1.4 * max(width, height)
        painter.setPen(QtCore.Qt.NoPen)
        for index in range(13):
            angle = -math.pi + index * math.pi / 12 + seconds * 0.012
            spread = 0.045 + 0.02 * math.sin(index * 2.7)
            far = QtCore.QPointF(center.x() + math.cos(angle) * reach, center.y() + math.sin(angle) * reach)
            ray = QtGui.QPainterPath(center)
            ray.lineTo(center.x() + math.cos(angle - spread) * reach, center.y() + math.sin(angle - spread) * reach)
            ray.lineTo(center.x() + math.cos(angle + spread) * reach, center.y() + math.sin(angle + spread) * reach)
            ray.closeSubpath()
            fade = QtGui.QLinearGradient(center, far)
            tint = QtGui.QColor(colors.selection_text)
            tint.setAlpha(48 if index % 3 else 68)
            fade.setColorAt(0, tint)
            tint.setAlpha(0)
            fade.setColorAt(0.75, tint)
            painter.fillPath(ray, fade)

        for index in range(2):
            radius = width * (0.37 + index * 0.18) + 8 * math.sin(seconds * 0.4 + index)
            ring = QtCore.QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            arc = QtGui.QPainterPath()
            arc.arcMoveTo(ring, 12)
            arc.arcTo(ring, 12, 156)
            tint = QtGui.QColor(colors.selection_text)
            tint.setAlpha(45 - index * 15)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(tint, 1.3))
            painter.drawPath(arc)

        for nx, ny, size, phase in self._motes:
            y = ((ny - seconds * 0.009) % 1.12) * height
            x = nx * width + 7 * math.sin(seconds * 0.25 + phase)
            alpha = round(45 + 45 * (0.5 + 0.5 * math.sin(seconds * 0.8 + phase)))
            color = QtGui.QColor(colors.selection_text)
            color.setAlpha(alpha)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QtCore.QPointF(x, y), size, size)
