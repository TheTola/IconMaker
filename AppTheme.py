"""Appearance preference and shared colors for IconForge-owned Qt surfaces."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from AppIdentity import APP_NAME, APP_ORG


THEME_KEY = "appearance/theme"
APPEARANCES = ("Light", "Dark", "System")


@dataclass(frozen=True)
class ThemeColors:
    window: str
    panel: str
    raised: str
    field: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_soft: str
    selection: str
    selection_text: str
    disabled: str
    danger: str
    success: str
    warning: str


THEMES = {
    "Dark": ThemeColors(
        "#080d19", "#101827", "#182b3d", "#0b1423", "#273348",
        "#4b9bb1", "#dbe5f2", "#a9b8ca", "#738298", "#36c9e8",
        "#6bd7ec", "#193344", "#23465a", "#ffffff", "#738298",
        "#ffadad", "#77d9cf", "#e4ad62",
    ),
    "Light": ThemeColors(
        "#b9dcf8", "#cce7fb", "#a8d5f4", "#c1e2fa", "#78b3da",
        "#398fc1", "#153b59", "#345f7e", "#416a87", "#00689f",
        "#005789", "#a1d5f6", "#006ba7", "#e1f3ff", "#5c819c",
        "#a52940", "#146b5f", "#94600b",
    ),
}

# The existing dark style sheets are the source templates. Each old color is
# assigned one semantic role here, so another palette can be added in this file.
_COLOR_ROLES = {
    "window": "#070c18 #080d19 #0a1020 #130d26 #0b172a #0b1427 #0f1930 #141e35",
    "panel": "#0e172b #0e1b30 #101827 #101d2d #0b1323 #0d1729 #101b30 #102039 #111a2d #111d31 #132138 #14233b #152139 #162943 #17263b #172943 #172b44 #192e48 #12243d #132641 #191c3b #171d3a #25152f #28162f #291530 #10243e #11283d #12283c #111d31 #15243a #151b3a #f2ce75",
    "raised": "#152031 #162d44 #17243a #18243a #182335 #182b3d #1b3445 #203b56 #244358 #263851 #264359 #263248 #354861 #34445f #38657c #40516a #34506b #192c44 #173144",
    "field": "#0b1423 #131c2a #101827",
    "border": "#222d3e #243049 #263248 #273348 #2b394d #2b758b #2f8096 #275060 #40516a #354861 #34445f #38657c #34506b",
    "border_strong": "#4aa5bb #4b9bb1 #4caac4 #5db4cb #49bfe0 #f4d787",
    "text": "#d2e0ef #d4deed #d8e9f4 #dbe5f2 #e0ebf8 #e1eaf6 #e3ebf5 #e4edf8 #e7f6fa #e8eef8 #e9f2fb #eaf4fc #edf7ff #f1f6fd #f2f6fc",
    "muted": "#8493a8 #8fb7cf #99a9bf #a5c3d7 #a9b8ca #b7c5d7 #b7d9ec #b9c9da #c9d6e7",
    "faint": "#738298 #75849a #8aa9b4",
    "accent": "#168daf #2daccf #35c3e1 #36c9e8 #53d9df #69e2eb #70e4f3 #7bb3d0 #7bd7ea #8bdff0 #8be7f3",
    "accent_hover": "#2bb5d4 #6bd7ec #a0f1f6",
    "accent_soft": "#173745 #1b3445 #193344 #152031 #c89236",
    "selection": "#164660 #23465a #252d55 #1c314b",
    "selection_text": "#ecfaff #ffffff",
    "disabled": "#738298 #75849a",
    "danger": "#ffadad #ff6b76 #e27f88 #f4d6d6",
    "success": "#62d6b0 #77d9cf",
    "warning": "#e4ad62 #d8ad51",
}

_COLOR_TO_ROLE = {
    value.lower(): role
    for role, values in _COLOR_ROLES.items()
    for value in values.split()
}
_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\b|rgba?\([^)]*\)")


def _system_appearance() -> str:
    if sys.platform.startswith("win"):
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "Light" if int(value) else "Dark"
        except (OSError, ValueError, TypeError):
            pass
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return "Dark" if app.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark else "Light"
    return "Light"


class ThemeManager(QtCore.QObject):
    changed = QtCore.Signal(str)
    preferenceChanged = QtCore.Signal(str)

    def __init__(self, app: QtWidgets.QApplication, settings: QtCore.QSettings) -> None:
        super().__init__(app)
        self.app = app
        self.settings = settings
        stored = str(settings.value(THEME_KEY, "System") or "System")
        self.preference = stored if stored in APPEARANCES else "System"
        if not settings.contains(THEME_KEY) or stored != self.preference:
            settings.setValue(THEME_KEY, self.preference)
            settings.sync()
        self.appearance = ""
        self._system = _system_appearance()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._check_system)
        self._timer.start()
        app.styleHints().colorSchemeChanged.connect(self._check_system)
        self._apply()

    @property
    def colors(self) -> ThemeColors:
        return THEMES[self.appearance or self._system]

    def select(self, preference: str) -> None:
        if preference not in APPEARANCES:
            raise ValueError(f"Unsupported appearance: {preference}")
        if self.preference == preference:
            return
        self.preference = preference
        self.settings.setValue(THEME_KEY, preference)
        self.settings.sync()
        self.preferenceChanged.emit(preference)
        self._check_system()
        self._apply()

    def _check_system(self, *_args) -> None:
        self.settings.sync()
        stored = str(self.settings.value(THEME_KEY, "System") or "System")
        preference_changed = stored in APPEARANCES and stored != self.preference
        if preference_changed:
            self.preference = stored
            self.preferenceChanged.emit(stored)
        current = _system_appearance()
        if current != self._system:
            self._system = current
        if preference_changed or self.preference == "System":
            self._apply()

    def _apply(self) -> None:
        effective = self._system if self.preference == "System" else self.preference
        if effective == self.appearance:
            return
        self.appearance = effective
        c = self.colors
        palette = QtGui.QPalette()
        for role, value in (
            (QtGui.QPalette.Window, c.window),
            (QtGui.QPalette.WindowText, c.text),
            (QtGui.QPalette.Base, c.field),
            (QtGui.QPalette.AlternateBase, c.panel),
            (QtGui.QPalette.Text, c.text),
            (QtGui.QPalette.Button, c.raised),
            (QtGui.QPalette.ButtonText, c.text),
            (QtGui.QPalette.ToolTipBase, c.panel),
            (QtGui.QPalette.ToolTipText, c.text),
            (QtGui.QPalette.Highlight, c.selection),
            (QtGui.QPalette.HighlightedText, c.selection_text),
            (QtGui.QPalette.PlaceholderText, c.muted),
            (QtGui.QPalette.Link, c.accent),
        ):
            palette.setColor(role, QtGui.QColor(value))
        for role in (QtGui.QPalette.Text, QtGui.QPalette.WindowText, QtGui.QPalette.ButtonText):
            palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(c.disabled))
        self.app.setPalette(palette)
        self.app.setStyleSheet(f"""
            QToolTip {{ background: {c.panel}; color: {c.text}; border: 1px solid {c.border}; padding: 5px; }}
            QMenu {{ background: {c.panel}; color: {c.text}; border: 1px solid {c.border}; padding: 4px; }}
            QMenu::item {{ padding: 6px 16px; }}
            QMenu::item:selected {{ background: {c.selection}; color: {c.selection_text}; }}
            QMenu::item:disabled {{ color: {c.disabled}; }}
            QDialog, QMessageBox {{ background: {c.window}; color: {c.text}; }}
            QDialog QLabel, QMessageBox QLabel {{ color: {c.text}; }}
            QDialog QPushButton, QMessageBox QPushButton {{ background: {c.raised}; color: {c.text}; border: 1px solid {c.border}; border-radius: 6px; padding: 6px 12px; }}
            QDialog QPushButton:hover, QMessageBox QPushButton:hover {{ border-color: {c.accent}; }}
            QDialog QPushButton:disabled, QMessageBox QPushButton:disabled {{ color: {c.disabled}; background: {c.panel}; border-color: {c.border}; }}
            QDialog QLineEdit {{ background: {c.field}; color: {c.text}; border: 1px solid {c.border}; selection-background-color: {c.selection}; selection-color: {c.selection_text}; }}
            QDialog QAbstractItemView, QDialog QComboBox {{ background: {c.field}; color: {c.text}; border: 1px solid {c.border}; selection-background-color: {c.selection}; selection-color: {c.selection_text}; }}
        """)
        self.changed.emit(effective)


_manager: ThemeManager | None = None


def theme_manager() -> ThemeManager:
    global _manager
    app = QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication must exist before creating the theme manager")
    if _manager is None or _manager.app is not app:
        _manager = ThemeManager(app, QtCore.QSettings(APP_ORG, APP_NAME))
    return _manager


def theme_css(dark_css: str) -> str:
    manager = theme_manager()
    if manager.appearance == "Dark":
        return dark_css
    colors = manager.colors

    def replace(match: re.Match[str]) -> str:
        value = match.group().lower()
        if value.startswith("#"):
            role = _COLOR_TO_ROLE.get(value)
            return getattr(colors, role) if role else value
        parts = [part.strip() for part in value[value.index("(") + 1:-1].split(",")]
        if len(parts) not in (3, 4):
            return value
        rgb = tuple(parts[:3])
        substitutions = {
            ("255", "255", "255"): ("27", "49", "72"),
            ("0", "0", "0"): ("255", "255", "255"),
            ("54", "201", "232"): ("8", "127", "189"),
            ("43", "58", "88"): ("161", "213", "246"),
            ("42", "98", "126"): ("8", "120", "179"),
            ("10", "16", "34"): ("185", "220", "248"),
            ("11", "19", "52"): ("193", "226", "250"),
            ("31", "9", "46"): ("204", "231", "251"),
            ("9", "19", "35"): ("204", "231", "251"),
            ("39", "72", "106"): ("161", "213", "246"),
            ("148", "181", "219"): ("57", "130", "180"),
            ("0", "220", "255"): ("8", "127", "189"),
            ("75", "164", "203"): ("45", "133", "190"),
            ("83", "171", "212"): ("45", "133", "190"),
            ("90", "150", "186"): ("57", "143", "193"),
            ("92", "154", "190"): ("57", "143", "193"),
            ("95", "160", "192"): ("57", "143", "193"),
            ("103", "212", "239"): ("8", "127", "189"),
            ("112", "185", "217"): ("45", "133", "190"),
            ("131", "176", "199"): ("61", "133", "179"),
            ("153", "169", "191"): ("61", "133", "179"),
        }
        changed = substitutions.get(rgb)
        if changed is None:
            return value
        return value[:value.index("(") + 1] + ", ".join((*changed, *parts[3:])) + ")"

    return _COLOR_PATTERN.sub(replace, dark_css)


def theme_color(dark_color: str) -> QtGui.QColor:
    return QtGui.QColor(theme_css(dark_color))
