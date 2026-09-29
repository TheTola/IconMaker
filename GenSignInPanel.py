"""Sign in to image providers using the generator's persistent browser profile."""

from __future__ import annotations

import json
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from AppIdentity import APP_NAME, APP_ORG
from AppTheme import theme_css, theme_manager
from GenSignInState import SIGN_IN_PROVIDERS, set_sign_in_status, sign_in_status


class _DragHeader(QtWidgets.QWidget):
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class _SignInSiteWindow(QtWidgets.QWidget):
    def __init__(
        self,
        provider: str,
        view: QtWidgets.QWidget,
        parent: QtWidgets.QWidget,
    ) -> None:
        super().__init__(parent, QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("SignInSiteWindow")
        self.setWindowTitle(f"IconForge · {provider} Sign In")
        self.setMinimumSize(720, 520)
        available = self.screen().availableGeometry()
        self.resize(min(980, available.width() - 48), min(720, available.height() - 48))
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        header = _DragHeader(self)
        header.setFixedHeight(44)
        heading = QtWidgets.QLabel(f"Sign in to {provider}", header)
        heading.setObjectName("SignInSiteTitle")
        close_button = QtWidgets.QToolButton(header)
        close_button.setObjectName("SignInClose")
        close_button.setText("×")
        close_button.setAccessibleName("Close provider website")
        close_button.setFixedSize(42, 38)
        close_button.clicked.connect(self.close)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 4, 0)
        header_layout.addWidget(heading)
        header_layout.addStretch(1)
        header_layout.addWidget(close_button)
        outer.addWidget(header)
        self.view = view
        outer.addWidget(self.view, 1)
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css("""
            QWidget#SignInSiteWindow { background: #080d19; color: #dbe5f2; }
            QLabel#SignInSiteTitle { color: #e9f2fb; font-size: 15pt; font-weight: 650; }
            QToolButton#SignInClose { color: #fdfefe; background: #168daf; border: 1px solid #6bd7ec; border-radius: 8px; font-size: 18pt; }
            QToolButton#SignInClose:hover { background: #116c8c; border-color: #8be7f3; }
        """))


class SignInPanel(QtWidgets.QWidget):
    authenticated = QtCore.Signal(str)
    closed = QtCore.Signal()

    def __init__(
        self,
        sites: dict[str, str],
        create_view: Callable[[str, QtWidgets.QWidget, list], QtWidgets.QWidget],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent, QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("SignInPanel")
        self.setWindowTitle("IconForge · Sign In")
        self.setMinimumWidth(720)
        self.setFixedHeight(150)
        self.resize(min(900, max(720, self.screen().availableGeometry().width() - 32)), 150)
        self._sites = sites
        self._create_view = create_view
        self._settings = QtCore.QSettings(APP_ORG, APP_NAME)
        self._views: dict[str, QtWidgets.QWidget] = {}
        self._site_windows: dict[str, _SignInSiteWindow] = {}
        self._popups: list = []
        self._selected = "ChatGPT"
        self._probe_pending: set[str] = set()

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)
        header = _DragHeader(self)
        header.setFixedHeight(42)
        heading = QtWidgets.QLabel("Sign In", header)
        heading.setObjectName("SignInTitle")
        close_button = QtWidgets.QToolButton(header)
        close_button.setObjectName("SignInClose")
        close_button.setText("×")
        close_button.setAccessibleName("Close sign-in panel")
        close_button.setFixedSize(42, 38)
        close_button.clicked.connect(self.close)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 4, 0)
        header_layout.addWidget(heading)
        header_layout.addStretch(1)
        header_layout.addWidget(close_button)
        outer.addWidget(header)

        provider_row = QtWidgets.QHBoxLayout()
        provider_row.setSpacing(8)
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        for name in SIGN_IN_PROVIDERS:
            button = QtWidgets.QPushButton(self)
            button.setObjectName("SignInProvider")
            button.setCheckable(True)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(66)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            button.clicked.connect(lambda _checked=False, provider=name: self.select_provider(provider))
            self._buttons[name] = button
            self._update_button(name)
            provider_row.addWidget(button, 1)
        outer.addLayout(provider_row)

        self._probe_timer = QtCore.QTimer(self)
        self._probe_timer.setInterval(2500)
        self._probe_timer.timeout.connect(self._probe_all)
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css("""
            QWidget#SignInPanel { background: #080d19; color: #dbe5f2; }
            QLabel#SignInTitle { color: #e9f2fb; font-size: 18pt; font-weight: 650; }
            QPushButton#SignInProvider { background: #101827; color: #dbe5f2; border: 1px solid #273348; border-radius: 9px; padding: 5px; text-align: center; }
            QPushButton#SignInProvider:hover { background: #182b3d; border-color: #36c9e8; }
            QPushButton#SignInProvider:checked { background: #193344; border-color: #36c9e8; }
            QPushButton#SignInProvider[signedIn="true"] { background: #36c9e8; color: #080d19; border-color: #36c9e8; font-weight: 600; }
            QPushButton#SignInProvider[signedIn="true"]:checked { border-color: #6bd7ec; }
            QToolButton#SignInClose { color: #fdfefe; background: #168daf; border: 1px solid #6bd7ec; border-radius: 8px; font-size: 18pt; }
            QToolButton#SignInClose:hover { background: #116c8c; border-color: #8be7f3; }
        """))

    def _update_button(self, name: str) -> None:
        status = sign_in_status(self._settings, name)
        button = self._buttons[name]
        button.setText(name)
        button.setToolTip(f"{name}: {'Signed in' if status == 'signed_in' else 'Sign in'}")
        signed_in = status == "signed_in"
        if button.property("signedIn") != signed_in:
            button.setProperty("signedIn", signed_in)
            button.style().unpolish(button)
            button.style().polish(button)

    def focus_provider(self, name: str) -> None:
        if name not in SIGN_IN_PROVIDERS:
            return
        self._settings.sync()
        for provider in SIGN_IN_PROVIDERS:
            self._update_button(provider)
        self._selected = name
        for provider, button in self._buttons.items():
            button.setChecked(provider == name)

    def select_provider(self, name: str) -> None:
        if name not in SIGN_IN_PROVIDERS:
            return
        self.focus_provider(name)
        self._ensure_view(name)
        site_window = self._site_windows.get(name)
        if site_window is None:
            site_window = _SignInSiteWindow(name, self._views[name], self)
            self._site_windows[name] = site_window
            available = self.screen().availableGeometry()
            site_window.move(available.center() - site_window.rect().center())
        site_window.show()
        site_window.view.show()
        site_window.raise_()
        site_window.activateWindow()
        QtCore.QTimer.singleShot(600, lambda: self._probe_provider(name))

    def _ensure_view(self, name: str) -> None:
        if name in self._views:
            return
        view = self._create_view(name, self, self._popups)
        view.resize(900, 600)
        view.hide()
        self._views[name] = view
        view.loadFinished.connect(lambda _ok, provider=name: self._probe_provider(provider))
        view.urlChanged.connect(lambda _url, provider=name: QtCore.QTimer.singleShot(800, lambda: self._probe_provider(provider)))
        view.load(QtCore.QUrl(self._sites[name]))

    def _probe_all(self) -> None:
        self._settings.sync()
        for name in SIGN_IN_PROVIDERS:
            self._update_button(name)
            self._probe_provider(name)

    def _probe_provider(self, name: str) -> None:
        view = self._views.get(name)
        if view is None or name in self._probe_pending or not self.isVisible():
            return
        self._probe_pending.add(name)
        script = f"""(() => {{
            const provider = {json.dumps(name)};
            const expected = {{
                'ChatGPT': 'chatgpt.com', 'Gemini': 'gemini.google.com',
                'Microsoft Designer': 'designer.microsoft.com'
            }}[provider];
            if (location.hostname !== expected || document.readyState === 'loading') return 'unknown';
            const visible = element => element && element.getClientRects().length &&
                getComputedStyle(element).visibility !== 'hidden' &&
                getComputedStyle(element).display !== 'none';
            const login = Array.from(document.querySelectorAll('a, button, [role="button"]')).find(element =>
                visible(element) && (/^(sign in|log in|sign up)$/i.test((element.textContent || '').trim()) ||
                /^(sign in|log in)$/i.test(element.getAttribute('aria-label') || '') ||
                /(?:login|signin)/i.test(element.getAttribute('href') || '')));
            if (login) return 'signed_out';
            const composer = Array.from(document.querySelectorAll(
                'textarea, [contenteditable="true"][role="textbox"], input[placeholder*="prompt" i], ' +
                'input[placeholder*="describe" i]'
            )).find(element => visible(element) && !/search/i.test(
                (element.getAttribute('placeholder') || '') + ' ' + (element.getAttribute('aria-label') || '')));
            const account = Array.from(document.querySelectorAll('button, [role="button"]')).find(element =>
                visible(element) && /account|profile|avatar/i.test(
                    (element.getAttribute('aria-label') || '') + ' ' + (element.getAttribute('data-testid') || '')));
            return composer || account ? 'signed_in' : 'unknown';
        }})()"""
        view.page().runJavaScript(script, lambda result, provider=name: self._on_probe(provider, result))

    def _on_probe(self, name: str, result: object) -> None:
        self._probe_pending.discard(name)
        if not isinstance(result, str) or result not in {"signed_in", "signed_out"}:
            return
        previous = sign_in_status(self._settings, name)
        set_sign_in_status(self._settings, name, str(result))
        self._update_button(name)
        if result == "signed_in" and previous != "signed_in":
            self.authenticated.emit(name)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._settings.sync()
        for name in SIGN_IN_PROVIDERS:
            self._update_button(name)
            self._ensure_view(name)
        self._probe_timer.start()
        QtCore.QTimer.singleShot(500, self._probe_all)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._probe_timer.stop()
        for window in self._site_windows.values():
            window.hide()
        super().hideEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._probe_timer.stop()
        for window in self._site_windows.values():
            window.close()
        for popup in list(self._popups):
            popup.close()
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()
