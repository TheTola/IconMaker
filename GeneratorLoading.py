"""Animated, timed presentation while image providers prepare in the background."""

from __future__ import annotations

import random
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from AppTheme import theme_css, theme_manager


ARTWORK_PATH = Path(__file__).resolve().parent / "assets" / "startup-hologram.png"
MESSAGES = (
    "Polishing the doomsday button.",
    "Teaching the pixels to cackle.",
    "Installing the suspiciously large lever.",
    "Replacing the death ray with a desk lamp.",
    "Sharpening the pixels. Figuratively.",
    "Giving the cursor an intimidating hat.",
    "Checking the trapdoor. It opens Settings.",
    "The machine demands a sacrifice. One PNG should do.",
    "Preparing the confetti of inevitable doom.",
    "Convincing the machine that icons are enough.",
)


def choose_generator_duration_ms(rng: random.Random | None = None) -> int:
    """Return a 10 ms step from 5–10 seconds with a long, sparse tail."""
    source = rng or random.SystemRandom()
    return min(10_000, 5_000 + round(500 * source.random() ** 9) * 10)


class GeneratorLoading(QtWidgets.QWidget):
    finished = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GeneratorLoading")
        self.duration_ms = 0
        self._running = False
        self._rng = random.SystemRandom()
        self._order: list[str] = []
        self._message_index = 0
        self._active_label = 0

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(15)
        layout.addStretch(1)

        artwork = QtGui.QPixmap(str(ARTWORK_PATH))
        self.artwork = QtWidgets.QLabel()
        self.artwork.setObjectName("GeneratorArtwork")
        self.artwork.setAlignment(QtCore.Qt.AlignCenter)
        self.artwork.setPixmap(artwork.scaled(300, 300, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        layout.addWidget(self.artwork, 0, QtCore.Qt.AlignCenter)
        self._artwork_effect = QtWidgets.QGraphicsOpacityEffect(self.artwork)
        self.artwork.setGraphicsEffect(self._artwork_effect)
        self._pulse = QtCore.QPropertyAnimation(self._artwork_effect, b"opacity", self)
        self._pulse.setDuration(1800)
        self._pulse.setStartValue(0.72)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setEndValue(0.72)
        self._pulse.setLoopCount(-1)

        self.heading = QtWidgets.QLabel("Opening the image forge")
        self.heading.setObjectName("GeneratorLoadingHeading")
        self.heading.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.heading)

        self.provider_label = QtWidgets.QLabel()
        self.provider_label.setObjectName("GeneratorLoadingProvider")
        self.provider_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.provider_label)

        message_area = QtWidgets.QWidget()
        message_area.setFixedHeight(54)
        message_layout = QtWidgets.QStackedLayout(message_area)
        message_layout.setStackingMode(QtWidgets.QStackedLayout.StackAll)
        self._labels = [QtWidgets.QLabel(), QtWidgets.QLabel()]
        self._effects = []
        self._fades = []
        for index, label in enumerate(self._labels):
            label.setObjectName("GeneratorLoadingMessage")
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setWordWrap(True)
            message_layout.addWidget(label)
            effect = QtWidgets.QGraphicsOpacityEffect(label)
            effect.setOpacity(1.0 if index == 0 else 0.0)
            label.setGraphicsEffect(effect)
            self._effects.append(effect)
            fade = QtCore.QPropertyAnimation(effect, b"opacity", self)
            fade.setDuration(270)
            fade.setEasingCurve(QtCore.QEasingCurve.InOutQuad)
            self._fades.append(fade)
        layout.addWidget(message_area)
        layout.addStretch(1)

        self._message_timer = QtCore.QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._next_message)
        self._deadline = QtCore.QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._complete)
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css("""
            QWidget#GeneratorLoading { background: #101827; border: 1px solid #273348; border-radius: 16px; }
            QLabel#GeneratorArtwork { background: transparent; border: 0; }
            QLabel#GeneratorLoadingHeading { color: #e9f2fb; background: transparent; font-size: 21px; font-weight: 650; }
            QLabel#GeneratorLoadingProvider { color: #a9b8ca; background: transparent; font-size: 12px; }
            QLabel#GeneratorLoadingMessage { color: #36c9e8; background: transparent; font-size: 16px; font-weight: 600; }
        """))

    def start(self, provider: str) -> None:
        if self._running:
            self.provider_label.setText(f"Preparing {provider}")
            return
        self._running = True
        self.duration_ms = choose_generator_duration_ms(self._rng)
        self.provider_label.setText(f"Preparing {provider}")
        self._order = self._rng.sample(MESSAGES, len(MESSAGES))
        self._message_index = 0
        self._active_label = 0
        self._labels[0].setText(self._order[0])
        self._labels[1].clear()
        self._effects[0].setOpacity(1.0)
        self._effects[1].setOpacity(0.0)
        self._pulse.start()
        self._message_timer.start(self._rng.randint(1500, 2000))
        self._deadline.start(self.duration_ms)

    def _next_message(self) -> None:
        if not self._running or self._message_index + 1 >= len(self._order) or self._deadline.remainingTime() < 400:
            return
        self._message_index += 1
        incoming = 1 - self._active_label
        self._labels[incoming].setText(self._order[self._message_index])
        for index, fade in enumerate(self._fades):
            fade.stop()
            fade.setStartValue(self._effects[index].opacity())
            fade.setEndValue(1.0 if index == incoming else 0.0)
            fade.start()
        self._active_label = incoming
        self._message_timer.start(self._rng.randint(1500, 2000))

    def stop(self) -> None:
        self._running = False
        self._deadline.stop()
        self._message_timer.stop()
        self._pulse.stop()
        for fade in self._fades:
            fade.stop()

    def _complete(self) -> None:
        self.stop()
        self.finished.emit()
