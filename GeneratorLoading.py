"""Live loading and failure state for embedded image providers."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from AppTheme import theme_css, theme_manager


ARTWORK_PATH = Path(__file__).resolve().parent / "assets" / "iconner.png"


class GeneratorLoading(QtWidgets.QWidget):
    def __init__(
        self, provider: str, parent: QtWidgets.QWidget | None = None,
        artwork_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.setObjectName("GeneratorLoading")
        self.setAccessibleName(f"{provider} loading status")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        layout.addStretch(1)

        self.artwork = QtGui.QPixmap(str(artwork_path or ARTWORK_PATH))
        self.artwork_label = QtWidgets.QLabel(self)
        self.artwork_label.setObjectName("GeneratorArtwork")
        self.artwork_label.setAlignment(QtCore.Qt.AlignCenter)
        self.artwork_label.setFixedSize(124, 124)
        self.artwork_label.setVisible(not self.artwork.isNull())
        layout.addWidget(self.artwork_label, 0, QtCore.Qt.AlignCenter)

        self.heading = QtWidgets.QLabel(self)
        self.heading.setObjectName("GeneratorLoadingHeading")
        self.heading.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.heading)

        self.detail = QtWidgets.QLabel(self)
        self.detail.setObjectName("GeneratorLoadingDetail")
        self.detail.setAlignment(QtCore.Qt.AlignCenter)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.progress = QtWidgets.QProgressBar(self)
        self.progress.setObjectName("GeneratorLoadingProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(280)
        layout.addWidget(self.progress, 0, QtCore.Qt.AlignCenter)
        layout.addStretch(1)

        self.set_state("preparing", f"Loading {provider}...")
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)

    def set_state(self, state: str, message: str = "") -> None:
        failed = state in {"error", "unverified"}
        self.heading.setText(f"Could not open {self.provider}" if failed else f"Opening {self.provider}")
        self.detail.setText(message or ("Use Reload or Open in Browser above." if failed else f"Loading {self.provider}..."))
        self.progress.setVisible(not failed)

    def _apply_theme(self, *_args) -> None:
        if not self.artwork.isNull():
            pixmap = self.artwork.scaled(82, 82, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            if self.provider in {"ChatGPT", "Codex"}:
                tinted = QtGui.QPixmap(pixmap.size())
                tinted.fill(QtCore.Qt.transparent)
                painter = QtGui.QPainter(tinted)
                painter.drawPixmap(0, 0, pixmap)
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), QtGui.QColor("#fdfefe"))
                painter.end()
                pixmap = tinted
            self.artwork_label.setPixmap(pixmap)
        self.setStyleSheet(theme_css("""
            QWidget#GeneratorLoading { background: #101827; border: 1px solid #273348; border-radius: 16px; }
            QLabel#GeneratorLoadingHeading { color: #e9f2fb; background: transparent; font-size: 21px; font-weight: 650; }
            QLabel#GeneratorLoadingDetail { color: #a9b8ca; background: transparent; font-size: 12px; }
            QProgressBar#GeneratorLoadingProgress { background: #0b1423; border: 1px solid #273348; border-radius: 5px; height: 8px; }
            QProgressBar#GeneratorLoadingProgress::chunk { background: #36c9e8; border-radius: 4px; }
        """) + """
            QLabel#GeneratorArtwork { background: #10243e; border: 1px solid #168daf; border-radius: 22px; }
        """)
