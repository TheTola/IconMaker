"""Animated startup splash for the main IconForge window."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from AppIdentity import apply_qt_application_identity
from AppTheme import theme_manager


ARTWORK_PATH = Path(__file__).resolve().parent / "assets" / "startup-hologram.png"
CANVAS_WIDTH = 700
CANVAS_HEIGHT = 740
FADE_IN_PART = 0.075
FADE_OUT_PART = 0.10


def choose_startup_duration_ms() -> int:
    return 4000 + random.SystemRandom().randint(1, 1000)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


class StartupSplash(QtWidgets.QWidget):
    firstFrameShown = QtCore.Signal()

    def __init__(self, duration_ms: int, appearance: str) -> None:
        super().__init__(None, QtCore.Qt.SplashScreen | QtCore.Qt.FramelessWindowHint)
        artwork = QtGui.QPixmap(str(ARTWORK_PATH))
        if artwork.isNull():
            raise FileNotFoundError(f"Startup artwork is unavailable: {ARTWORK_PATH}")

        self.duration_ms = duration_ms
        self._appearance = appearance
        artwork_ratio = max(1.0, self.devicePixelRatioF())
        self._artwork = artwork.scaled(
            round(540 * artwork_ratio),
            round(540 * artwork_ratio),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self._artwork.setDevicePixelRatio(artwork_ratio)
        self._clock = QtCore.QElapsedTimer()
        self._started = False
        self._finished = False
        self._main_window: QtWidgets.QWidget | None = None
        self._pulse_start_ms = round(duration_ms * FADE_IN_PART)
        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._frame_timer.setInterval(16)
        self._frame_timer.timeout.connect(self._advance)
        self._paths = self._make_text_paths()

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
        scale = min(1.0, (available.width() - 32) / CANVAS_WIDTH, (available.height() - 32) / CANVAS_HEIGHT)
        self.resize(round(CANVAS_WIDTH * scale), round(CANVAS_HEIGHT * scale))
        self.move(available.center() - self.rect().center())
        self.setWindowOpacity(0.08)
        theme_manager().changed.connect(self.set_appearance)

    @property
    def elapsed_ms(self) -> int:
        return self._clock.elapsed() if self._started else 0

    def set_appearance(self, appearance: str) -> None:
        self._appearance = appearance
        self.update()

    def set_main_window(self, window: QtWidgets.QWidget) -> None:
        self._main_window = window
        self._advance()

    def abort(self) -> None:
        self._finished = True
        self._frame_timer.stop()
        self.close()

    def _make_text_paths(self) -> tuple[QtGui.QPainterPath, ...]:
        families = set(QtGui.QFontDatabase.families())
        family = next(
            (name for name in ("Bahnschrift", "Segoe UI Variable", "Segoe UI") if name in families),
            QtWidgets.QApplication.font().family(),
        )
        lines = (
            ("POWERING ICON FORGE", 37, QtGui.QFont.DemiBold, 3.5, 570),
            ("ICON GENERATOR", 22, QtGui.QFont.Normal, 5.0, 626),
            ("PREPARING THE FORGE...", 14, QtGui.QFont.Light, 3.0, 670),
        )
        paths: list[QtGui.QPainterPath] = []
        for text, size, weight, spacing, top in lines:
            font = QtGui.QFont(family)
            font.setPixelSize(size)
            font.setWeight(weight)
            font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, spacing)
            path = QtGui.QPainterPath()
            path.addText(0, 0, font, text)
            bounds = path.boundingRect()
            path.translate(CANVAS_WIDTH / 2 - bounds.center().x(), top - bounds.top())
            paths.append(path)
        return tuple(paths)

    def _show_main(self) -> None:
        if self._main_window is None:
            return
        available = self.screen().availableGeometry()
        size = self._main_window.size().expandedTo(self._main_window.minimumSize())
        center = self.frameGeometry().center()
        x = max(available.left(), min(center.x() - size.width() // 2, available.right() - size.width() + 1))
        y = max(available.top(), min(center.y() - size.height() // 2, available.bottom() - size.height() + 1))
        self._main_window.move(x, y)
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _advance(self) -> None:
        if not self._started or self._finished:
            return
        elapsed = self.elapsed_ms
        progress = min(1.0, elapsed / self.duration_ms)

        if progress < FADE_IN_PART:
            opacity = max(0.08, _smoothstep(progress / FADE_IN_PART))
        elif self._main_window is not None and progress >= 1.0 - FADE_OUT_PART:
            opacity = 1.0 - _smoothstep((progress - (1.0 - FADE_OUT_PART)) / FADE_OUT_PART)
        else:
            opacity = 1.0
        self.setWindowOpacity(opacity)
        self.update()

        if elapsed >= self.duration_ms and self._main_window is not None:
            self._finished = True
            self._frame_timer.stop()
            self.close()
            QtWidgets.QApplication.instance().setQuitOnLastWindowClosed(True)
            self._show_main()

    def _paint_underglow(self, painter: QtGui.QPainter) -> None:
        elapsed = self.elapsed_ms
        fade_out_start = self.duration_ms * (1.0 - FADE_OUT_PART)
        if elapsed <= self._pulse_start_ms:
            pulse = 0.0
        else:
            pulse_range = max(1.0, fade_out_start - self._pulse_start_ms)
            phase = (elapsed - self._pulse_start_ms) / pulse_range
            if self._main_window is None and phase > 1.0:
                phase %= 1.0
            else:
                phase = min(1.0, phase)
            pulse = (1.0 - math.cos(4.0 * math.pi * phase)) / 2.0
        strength = 0.32 + 0.68 * pulse
        spread = 1.0 + 0.18 * pulse

        painter.save()
        painter.translate(CANVAS_WIDTH / 2, 658)
        painter.scale(235 * spread, 66 * spread)
        glow = QtGui.QRadialGradient(QtCore.QPointF(0, 0), 1)
        glow.setColorAt(0, QtGui.QColor(31, 159, 255, round(108 * strength)))
        glow.setColorAt(0.42, QtGui.QColor(39, 168, 255, round(49 * strength)))
        glow.setColorAt(1, QtGui.QColor(39, 168, 255, 0))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QtCore.QRectF(-1, -1, 2, 2))
        painter.restore()

    def _paint_text(self, painter: QtGui.QPainter) -> None:
        light = self._appearance == "Light"
        cores = (
            QtGui.QColor("#174a76" if light else "#f0fbff"),
            QtGui.QColor("#2e678e" if light else "#c5e7f8"),
            QtGui.QColor("#456e8e" if light else "#a6cce4"),
        )
        for index, (path, core) in enumerate(zip(self._paths, cores)):
            glow = QtGui.QColor(27, 177, 232, 24 if light else 32)
            painter.strokePath(path, QtGui.QPen(glow, 8 if index == 0 else 5))
            glow.setAlpha(96 if index == 0 else 58)
            painter.strokePath(path, QtGui.QPen(glow, 2.2 if index == 0 else 1.4))
            painter.fillPath(path, core)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.scale(self.width() / CANVAS_WIDTH, self.height() / CANVAS_HEIGHT)

        light = self._appearance == "Light"
        background = QtGui.QLinearGradient(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)
        background.setColorAt(0, QtGui.QColor("#e9f5ff" if light else "#06101e"))
        background.setColorAt(1, QtGui.QColor("#c8e5f9" if light else "#102944"))
        panel = QtGui.QPainterPath()
        panel.addRoundedRect(QtCore.QRectF(1, 1, CANVAS_WIDTH - 2, CANVAS_HEIGHT - 2), 22, 22)
        painter.fillPath(panel, background)
        painter.setPen(QtGui.QPen(QtGui.QColor("#75b6dc" if light else "#357395"), 1.4))
        painter.drawPath(panel)

        artwork_x = (CANVAS_WIDTH - self._artwork.width() / self._artwork.devicePixelRatioF()) / 2
        painter.drawPixmap(QtCore.QPointF(artwork_x, 23), self._artwork)
        self._paint_underglow(painter)
        self._paint_text(painter)
        painter.end()

        if not self._started and self.isVisible():
            self._started = True
            self._clock.start()
            self._frame_timer.start()
            QtCore.QTimer.singleShot(0, self.firstFrameShown.emit)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._finished:
            event.ignore()
            return
        self._frame_timer.stop()
        try:
            theme_manager().changed.disconnect(self.set_appearance)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)


class _ImportWorker(QtCore.QObject):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            import Gen1

            self.loaded.emit(Gen1)
        except BaseException as exc:
            self.failed.emit(exc)


class _StartupCoordinator(QtCore.QObject):
    def __init__(
        self,
        app: QtWidgets.QApplication,
        splash: StartupSplash,
        main_module: object | None,
    ) -> None:
        super().__init__(app)
        self.app = app
        self.splash = splash
        self.main_module = main_module
        self.window: QtWidgets.QWidget | None = None
        self.failure: BaseException | None = None
        self._import_thread: QtCore.QThread | None = None
        self._import_worker: _ImportWorker | None = None
        splash.firstFrameShown.connect(self.start)

    @QtCore.Slot()
    def start(self) -> None:
        if self.main_module is not None:
            QtCore.QTimer.singleShot(0, lambda: self._build_window(self.main_module))
            return
        thread = QtCore.QThread(self)
        worker = _ImportWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.loaded.connect(self._build_window, QtCore.Qt.QueuedConnection)
        worker.failed.connect(self._fail, QtCore.Qt.QueuedConnection)
        worker.loaded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._import_thread = thread
        self._import_worker = worker
        thread.start()

    @QtCore.Slot(object)
    def _build_window(self, module: object) -> None:
        try:
            module._pre_app_setup()
            self.app.setProperty("iconforgeStartupSplashActive", True)
            try:
                self.window = module.MainWindow()
            finally:
                self.app.setProperty("iconforgeStartupSplashActive", False)
            self.app.setWindowIcon(self.window.windowIcon())
            self.splash.set_main_window(self.window)
        except BaseException as exc:
            self._fail(exc)

    @QtCore.Slot(object)
    def _fail(self, exc: BaseException) -> None:
        self.failure = exc
        self.splash.abort()
        self.app.exit(1)


def run_ui(main_module: object | None = None) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    apply_qt_application_identity(app)
    appearance = theme_manager().appearance
    app.setQuitOnLastWindowClosed(False)

    splash = StartupSplash(choose_startup_duration_ms(), appearance)
    coordinator = _StartupCoordinator(app, splash, main_module)
    splash.show()
    exit_code = app.exec()
    if coordinator.failure is not None:
        raise coordinator.failure
    raise SystemExit(exit_code)
