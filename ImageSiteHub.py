"""AI image portals and unsaved image previews for IconForge."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWebEngineCore import QWebEngineLoadingInfo, QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

import GenLog
from AppIdentity import APP_NAME, APP_ORG
from AppTheme import theme_color, theme_css, theme_manager
from IconImagePrototype import IconImagePrototype
from ImageSiteDownloads import ImageSiteDownloads
from ImageGenerationBrief import OPENING_BRIEF


SITES = (
    ("ChatGPT", "https://chatgpt.com/"),
    ("Gemini", "https://gemini.google.com/app"),
    ("Adobe Firefly", "https://firefly.adobe.com/"),
    ("Microsoft Designer", "https://designer.microsoft.com/"),
)
CODEX_LABEL = "Codex"
LAST_SOURCE_KEY = "image_generator/last_source"
SAGE_PLUGIN_ID = "gpt-6cc59704912e3cc883948e1aafcabbd9"
IMAGE_PATH_ROLE = QtCore.Qt.UserRole
IMAGE_SAVED_ROLE = QtCore.Qt.UserRole + 1
IMAGE_NAME_ROLE = QtCore.Qt.UserRole + 2
IMAGE_PROVIDER_ROLE = QtCore.Qt.UserRole + 3
SITE_ICONS = {
    "ChatGPT": "provider-chatgpt.png",
    "Gemini": "provider-gemini.png",
    "Adobe Firefly": "provider-adobe-firefly.png",
    "Microsoft Designer": "provider-microsoft-designer.png",
    CODEX_LABEL: "provider-codex.png",
}
SITE_TILE_LABELS = {
    "ChatGPT": "ChatGPT",
    "Gemini": "Gemini",
    "Adobe Firefly": "Firefly",
    "Microsoft Designer": "Designer",
    CODEX_LABEL: "Codex",
}
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
PORTAL_SURFACES = {
    "ChatGPT": "#080d19",
    "Gemini": "#080d19",
    "Adobe Firefly": "#f5f5f3",
    "Microsoft Designer": "#121212",
}


def _site_surface(provider: str) -> str:
    if provider in {"ChatGPT", "Gemini"} and theme_manager().appearance == "Light":
        return theme_manager().colors.window
    return PORTAL_SURFACES.get(provider, "#080d19")


def _site_loading_text(provider: str) -> str:
    if provider == "Adobe Firefly":
        return "#455f77"
    if provider == "Microsoft Designer":
        return "#dbe5f2"
    return theme_manager().colors.muted


def _suggest_name(provider: str, path: Path, prompt: str = "") -> str:
    if provider == CODEX_LABEL and prompt:
        words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", prompt, flags=re.UNICODE)
        while words and words[0].casefold() in {
            "create", "generate", "draw", "make", "an", "a", "the", "image", "icon", "of"
        }:
            words.pop(0)
        if words:
            title: list[str] = []
            for word in words:
                if title and word.casefold() in {"with", "against", "using", "featuring", "showing"}:
                    break
                title.append(word)
                if len(title) == 6:
                    break
            while len(title) > 1 and title[-1].casefold() in {"icon", "image", "picture", "illustration"}:
                title.pop()
            return " ".join(title)[:100]
    stem = path.stem
    if stem.startswith("generated-original"):
        return "Generated image"
    return stem[:100] or "Generated image"


def _site_icon(name: str) -> QtGui.QIcon:
    image = QtGui.QImage(str(ASSETS_DIR / SITE_ICONS[name]))
    if image.isNull():
        return QtGui.QIcon()
    image = image.scaled(64, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    bounds = QtCore.QRect()
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 16:
                bounds = bounds.united(QtCore.QRect(x, y, 1, 1))
    if not bounds.isNull():
        image = image.copy(bounds)
    fitted = QtGui.QPixmap.fromImage(image).scaled(
        30, 30, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
    )
    pixmap = QtGui.QPixmap(32, 32)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.drawPixmap((32 - fitted.width()) // 2, (32 - fitted.height()) // 2, fitted)
    painter.end()
    if name in {"ChatGPT", CODEX_LABEL}:
        # Monochrome marks need contrast on both theme surfaces.
        light = QtGui.QPixmap(pixmap.size())
        light.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(light)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(light.rect(), QtGui.QColor(theme_manager().colors.text))
        painter.end()
        pixmap = light
    return QtGui.QIcon(pixmap)


class SitePage(QWebEnginePage):
    imageDragEnded = QtCore.Signal(str)

    def javaScriptConsoleMessage(self, level: QWebEnginePage.JavaScriptConsoleMessageLevel,
                                 message: str, line_number: int, source_id: str) -> None:
        prefix = "iconforge-image-drag-end:"
        if message.startswith(prefix):
            self.imageDragEnded.emit(message[len(prefix):])
        else:
            super().javaScriptConsoleMessage(level, message, line_number, source_id)


class SiteView(QWebEngineView):
    """Keep one official site and any sign-in popups in the same browser profile."""

    def __init__(
        self,
        profile: QWebEngineProfile,
        provider: str,
        popups: list[SiteView],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._provider = provider
        self._popups = popups
        page = SitePage(profile, self)
        surface = _site_surface(provider)
        page.setBackgroundColor(QtGui.QColor(surface))
        page.setProperty("imageProvider", provider)
        if provider in {"ChatGPT", "Gemini"}:
            drag_script = QWebEngineScript()
            drag_script.setName("icon-forge-image-drag")
            drag_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
            drag_script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
            drag_script.setRunsOnSubFrames(False)
            drag_script.setSourceCode("""
                let draggedImageUrl = '';
                document.addEventListener('dragstart', event => {
                    const target = event.target;
                    const image = target instanceof Element &&
                        (target.closest('img') || document.elementFromPoint(event.clientX, event.clientY)?.closest('img'));
                    if (image && image.complete && image.naturalWidth >= 256 && image.naturalHeight >= 256)
                        draggedImageUrl = image.currentSrc || image.src;
                }, true);
                document.addEventListener('dragend', () => {
                    if (draggedImageUrl)
                        console.info('iconforge-image-drag-end:' + draggedImageUrl);
                    draggedImageUrl = '';
                }, true);
            """)
            page.scripts().insert(drag_script)
        self._color_script: QWebEngineScript | None = None
        if provider == "Adobe Firefly":
            # Chromium's forced dark colors hide Firefly's hero text.
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
        elif provider == "Gemini":
            script = QWebEngineScript()
            script.setName("icon-forge-gemini-colors")
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
            script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode("""
                if (location.hostname === 'gemini.google.com') {
                    document.getElementById('iconforge-provider-colors')?.remove();
                    const style = document.createElement('style');
                    style.id = 'iconforge-provider-colors';
                    style.textContent = 'html, body { background-color: #080d19 !important; }';
                    document.head.appendChild(style);
                }
            """)
            self._color_script = script
        elif provider == "ChatGPT":
            script = QWebEngineScript()
            script.setName("icon-forge-chatgpt-colors")
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
            script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode("""
                if (location.hostname === 'chatgpt.com') {
                    document.getElementById('iconforge-provider-colors')?.remove();
                    const style = document.createElement('style');
                    style.id = 'iconforge-provider-colors';
                    style.textContent = `
                        html {
                            --color-token-bg-primary: #080d19 !important;
                            --color-token-main-surface-primary: #080d19 !important;
                            --color-surface-secondary: #080d19 !important;
                            --composer-background-color: #15243a !important;
                        }
                        html, body, main { background-color: #080d19 !important; }
                        aside, aside > div, aside > div > div { background-color: #0b1423 !important; }
                        [data-composer-body] {
                            --composer-layout-surface-background: #15243a !important;
                            background-color: #15243a !important;
                        }
                        div[class*="home-composer-layout:invisible"]:has([data-composer-body]) {
                            background-color: #080d19 !important;
                        }
                    `;
                    document.head.appendChild(style);
                }
            """)
            self._color_script = script
        self.setPage(page)
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)
        page.windowCloseRequested.connect(self.close)

    def _apply_theme(self, *_args) -> None:
        surface = _site_surface(self._provider)
        self.page().setBackgroundColor(QtGui.QColor(surface))
        if self._color_script is None:
            return
        scripts = self.page().scripts()
        for script in scripts.toList():
            if script.name() == self._color_script.name():
                scripts.remove(script)
        self.page().runJavaScript("document.getElementById('iconforge-provider-colors')?.remove();")
        if theme_manager().appearance == "Dark":
            scripts.insert(self._color_script)
            self.page().runJavaScript(self._color_script.sourceCode())

    def createWindow(self, _window_type: QWebEnginePage.WebWindowType) -> QWebEngineView:
        popup = SiteView(self._profile, self._provider, self._popups)
        popup.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        popup.setWindowTitle(f"{self._provider} sign-in")
        popup.resize(920, 700)
        popup.show()
        self._popups.append(popup)
        popup.destroyed.connect(lambda: self._popups.remove(popup) if popup in self._popups else None)
        return popup


class PortalFade(QtWidgets.QWidget):
    """Let the page fade into the app background at its outer edges."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        fade = min(88, self.width() // 5, self.height() // 5)
        if fade <= 0:
            return
        base = theme_color("#080d19")

        def gradient(x1: int, y1: int, x2: int, y2: int, reverse: bool = False) -> QtGui.QLinearGradient:
            result = QtGui.QLinearGradient(x1, y1, x2, y2)
            stops = ((0.0, 255), (0.06, 102), (0.25, 31), (1.0, 0))
            for position, alpha in stops:
                color = QtGui.QColor(base)
                color.setAlpha(alpha)
                result.setColorAt(1.0 - position if reverse else position, color)
            return result

        width, height = self.width(), self.height()
        painter.fillRect(QtCore.QRect(0, 0, fade, height), gradient(0, 0, fade, 0))
        painter.fillRect(QtCore.QRect(width - fade, 0, fade, height), gradient(width - fade, 0, width, 0, True))
        painter.fillRect(QtCore.QRect(0, 0, width, fade), gradient(0, 0, 0, fade))
        painter.fillRect(QtCore.QRect(0, height - fade, width, fade), gradient(0, height - fade, 0, height, True))


class PortalFrame(QtWidgets.QWidget):
    """Show a site with provider-specific edge treatment."""

    def __init__(self, view: SiteView, surface: str, fade_edges: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._surface = QtGui.QColor(surface)
        self._fade_edges = fade_edges
        layout = QtWidgets.QVBoxLayout(self)
        margin = 0 if fade_edges else 7
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.addWidget(view)
        self._fade = PortalFade(self) if fade_edges else None
        if self._fade is not None:
            self._fade.raise_()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fade is not None:
            self._fade.setGeometry(self.rect())
            self._fade.raise_()

    def set_surface(self, surface: str) -> None:
        color = QtGui.QColor(surface)
        if color.isValid() and color.alpha() == 255:
            self._surface = color
            self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        if self._fade_edges:
            painter.fillRect(self.rect(), self._surface)
            return
        outer = theme_color("#080d19")
        inner = self.rect().adjusted(7, 7, -7, -7)
        painter.fillRect(self.rect(), outer)
        painter.fillRect(inner, self._surface)
        left = QtGui.QLinearGradient(0, 0, 7, 0)
        left.setColorAt(0, outer)
        left.setColorAt(1, self._surface)
        right = QtGui.QLinearGradient(self.width() - 7, 0, self.width(), 0)
        right.setColorAt(0, self._surface)
        right.setColorAt(1, outer)
        top = QtGui.QLinearGradient(0, 0, 0, 7)
        top.setColorAt(0, outer)
        top.setColorAt(1, self._surface)
        bottom = QtGui.QLinearGradient(0, self.height() - 7, 0, self.height())
        bottom.setColorAt(0, self._surface)
        bottom.setColorAt(1, outer)
        painter.fillRect(QtCore.QRect(0, 7, 7, max(0, self.height() - 14)), left)
        painter.fillRect(QtCore.QRect(self.width() - 7, 7, 7, max(0, self.height() - 14)), right)
        painter.fillRect(QtCore.QRect(7, 0, max(0, self.width() - 14), 7), top)
        painter.fillRect(QtCore.QRect(7, self.height() - 7, max(0, self.width() - 14), 7), bottom)


class ImagePreview(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QtGui.QPixmap()
        self.setMinimumHeight(235)

    def set_image(self, image: QtGui.QImage) -> None:
        self._image = QtGui.QPixmap.fromImage(image)
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), theme_color("#101827"))
        if not self._image.isNull():
            target = self.rect().adjusted(9, 9, -9, -9)
            scaled = self._image.scaled(target.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            x = target.x() + (target.width() - scaled.width()) // 2
            y = target.y() + (target.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)


class WindowDragLabel(QtWidgets.QLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._drag_offset: QtCore.QPoint | None = None
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setToolTip("Drag to move image generator")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            win = self.window()
            if not win.isMaximized():
                self._drag_offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
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


class ImageSiteHub(QtWidgets.QWidget):
    providerStateChanged = QtCore.Signal(str, str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        if parent is not None:
            self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self._sites = dict(SITES)
        self._settings = QtCore.QSettings(APP_ORG, APP_NAME)
        self.setWindowTitle("IconForge · Image Generation")
        self.resize(1260, 820)
        self.setMinimumSize(900, 610)
        self._theme_style = """
            QWidget#Hub { background: #080d19; color: #dbe5f2; font-size: 10pt; }
            QWidget#Rail, QWidget#Results { background: #101827; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; }
            QLabel { color: #dbe5f2; }
            QLabel#Heading { font-size: 17pt; font-weight: 650; }
            QLabel#SourceHeading { font-size: 15pt; font-weight: 650; }
            QLabel#Muted { color: #a9b8ca; }
            QListWidget { background: #0b1423; color: #e3ebf5; border: 1px solid #273348; border-radius: 9px; padding: 4px; }
            QListWidget::item { padding: 9px 7px; border-radius: 5px; }
            QListWidget::item:selected { background: #23465a; color: white; }
            QListWidget#ImageNames { background: transparent; border: 0; padding: 0; }
            QListWidget#ImageNames::item { padding: 6px 4px; }
            QListWidget#ImageNames::item:selected { background: rgba(255, 255, 255, 0.05); color: #e3ebf5; }
            QPushButton { background: #182335; color: #dbe5f2; border: 1px solid #2b394d; border-radius: 9px; padding: 7px 11px; }
            QPushButton:hover:enabled { background: #1b3445; border-color: #36c9e8; }
            QPushButton:disabled { color: #738298; background: #131c2a; }
            QLineEdit { background: #0b1423; color: #dbe5f2; border: 1px solid #273348; border-radius: 9px; padding: 6px; }
            QToolButton#SourceTile { background: #0b1423; color: #dbe5f2; border: 1px solid #273348; border-radius: 9px; padding: 5px 2px; font-size: 9pt; }
            QToolButton#SourceTile:hover { background: #182b3d; }
            QToolButton#SourceTile:checked { background: #193344; border-color: #36c9e8; }
            QToolButton#ReloadProvider { background: #182335; color: #dbe5f2; border: 1px solid #2b394d; border-radius: 9px; font-size: 18pt; }
            QToolButton#ReloadProvider:hover { background: #1b3445; border-color: #36c9e8; }
            QToolButton#CloseHub { background: transparent; color: #dbe5f2; border: none; border-radius: 8px; font-size: 17pt; }
            QToolButton#CloseHub:hover { background: #1b3445; }
            QMenu { background: #101827; color: #dbe5f2; border: 1px solid #273348; }
            QMenu::item:selected { background: #1b3445; }
        """
        self._apply_theme()
        theme_manager().changed.connect(self._apply_theme)
        self.setObjectName("Hub")
        self._views: dict[str, SiteView] = {}
        self._portal_frames: dict[str, PortalFrame] = {}
        self._site_frames: dict[str, QtWidgets.QStackedWidget] = {}
        self._site_reveal_timers: dict[str, QtCore.QTimer] = {}
        self._site_probe_timers: dict[str, QtCore.QTimer] = {}
        self._site_generations: dict[str, int] = {}
        self._site_errors: dict[str, str] = {}
        self._brief_phase = {"ChatGPT": "idle", "Gemini": "idle"}
        self._brief_completed_url = {"ChatGPT": "", "Gemini": ""}
        self._brief_attempts = {"ChatGPT": 0, "Gemini": 0}
        self._existing_brief_attempts = {"ChatGPT": 0, "Gemini": 0}
        self._preparation_retries = {"ChatGPT": 0, "Gemini": 0}
        self._brief_timers: dict[str, QtCore.QTimer] = {}
        self._image_scan_timers: dict[str, QtCore.QTimer] = {}
        self._seen_image_urls = {"ChatGPT": set(), "Gemini": set()}
        self._pending_image_urls = {"ChatGPT": set(), "Gemini": set()}
        self._image_scan_ready = {"ChatGPT": False, "Gemini": False}
        self._prepared_urls: dict[str, set[str]] = {}
        self._prepared_url_order: dict[str, list[str]] = {}
        for source in self._brief_phase:
            saved = self._settings.value(f"image_generator/prepared_{source.lower()}", [])
            ordered = [str(value) for value in (saved if isinstance(saved, list) else [saved]) if value]
            self._prepared_url_order[source] = ordered
            self._prepared_urls[source] = set(ordered)
        self._provider_state = {name: "idle" for name in (*self._sites, CODEX_LABEL)}
        self._provider_message: dict[str, str] = {}
        self._provider_started_at: dict[str, float] = {}
        self._popups: list[SiteView] = []
        self._codex: IconImagePrototype | None = None
        self._chatgpt_site_signed_in = False
        self._sage_new_chat_started = False
        self._sage_selection_started = False
        self._sage_selection_attempts = 0
        self._sage_selection_busy = False
        self._sage_selection_timer = QtCore.QTimer(self)
        self._sage_selection_timer.setInterval(400)
        self._sage_selection_timer.timeout.connect(self._poll_sage_selection)
        self._last_codex_prompt = ""
        self._closing_after_request = False
        self._allow_close = False
        self._shutting_down = False
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._shutdown)

        profile_root = GenLog.app_data_dir() / "AI Site Browser"
        profile_root.mkdir(parents=True, exist_ok=True)
        self._profile = QWebEngineProfile("iconmaker-ai-sites", self)
        self._profile.setPersistentStoragePath(str(profile_root / "Storage"))
        self._profile.setCachePath(str(profile_root / "Cache"))
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self._profile.settings().setAttribute(
            QWebEngineSettings.WebAttribute.ForceDarkMode, theme_manager().appearance == "Dark"
        )
        self.downloads = ImageSiteDownloads(self._profile, self)
        self.downloads.statusChanged.connect(self._status)
        self.downloads.failed.connect(self._status)
        self.downloads.imageSaved.connect(self._on_preview_ready)
        self.downloads.archiveReady.connect(self._on_drag_preview_ready)
        self.downloads.activeChanged.connect(self._download_active_changed)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(10)
        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(12)
        outer.addLayout(columns, 1)

        rail = QtWidgets.QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(195)
        rail_layout = QtWidgets.QVBoxLayout(rail)
        rail_layout.setContentsMargins(12, 14, 12, 14)
        rail_layout.setSpacing(10)
        heading = WindowDragLabel("Image Generator")
        heading.setObjectName("SourceHeading")
        rail_layout.addWidget(heading)
        subtitle = QtWidgets.QLabel("Select Image Generation Tool")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        rail_layout.addWidget(subtitle)
        self._source_name = "ChatGPT"
        self.source_buttons: dict[str, QtWidgets.QAbstractButton] = {}
        source_group = QtWidgets.QButtonGroup(self)
        source_group.setExclusive(True)
        source_grid = QtWidgets.QGridLayout()
        source_grid.setSpacing(8)
        for index, name in enumerate([site_name for site_name, _url in SITES] + [CODEX_LABEL]):
            button = QtWidgets.QToolButton()
            button.setObjectName("SourceTile")
            button.setText(SITE_TILE_LABELS[name])
            button.setIcon(_site_icon(name))
            button.setIconSize(QtCore.QSize(32, 32))
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setFixedSize(80, 78)
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.clicked.connect(lambda _checked=False, source=name: self._select_source(source))
            source_group.addButton(button)
            self.source_buttons[name] = button
            if name == CODEX_LABEL:
                source_grid.addWidget(button, 2, 0, 1, 2, QtCore.Qt.AlignHCenter)
            else:
                source_grid.addWidget(button, index // 2, index % 2, QtCore.Qt.AlignHCenter)
        rail_layout.addLayout(source_grid)
        rail_layout.addStretch(1)
        columns.addWidget(rail)

        center = QtWidgets.QVBoxLayout()
        center.setSpacing(9)
        columns.addLayout(center, 1)
        self.source_title = WindowDragLabel()
        self.source_title.setObjectName("Heading")
        center.addWidget(self.source_title)
        toolbar = QtWidgets.QHBoxLayout()
        self.reload_button = QtWidgets.QToolButton()
        self.reload_button.setObjectName("ReloadProvider")
        self.reload_button.setText("↻")
        self.reload_button.setToolTip("Reload provider page")
        self.reload_button.setAccessibleName("Reload provider page")
        self.reload_button.setFixedSize(76, 48)
        self.reload_button.clicked.connect(self._reload_selected_site)
        self.open_site_button = QtWidgets.QPushButton("Open in Browser")
        self.open_site_button.clicked.connect(self._open_in_browser)
        toolbar.addWidget(self.reload_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.open_site_button)
        self.toolbar = QtWidgets.QWidget()
        self.toolbar.setLayout(toolbar)
        center.addWidget(self.toolbar)
        self.content = QtWidgets.QStackedWidget()
        center.addWidget(self.content, 1)

        downloads_rail = QtWidgets.QWidget()
        self.downloads_rail = downloads_rail
        downloads_rail.setObjectName("Results")
        downloads_rail.setFixedWidth(265)
        downloads_layout = QtWidgets.QVBoxLayout(downloads_rail)
        downloads_layout.setContentsMargins(12, 14, 12, 14)
        downloads_layout.setSpacing(9)
        title = WindowDragLabel("Images")
        title.setObjectName("Heading")
        downloads_header = QtWidgets.QHBoxLayout()
        downloads_header.addWidget(title)
        downloads_header.addStretch(1)
        self.close_button = QtWidgets.QToolButton()
        self.close_button.setObjectName("CloseHub")
        self.close_button.setText("×")
        self.close_button.setToolTip("Close image generator")
        self.close_button.setAccessibleName("Close image generator")
        self.close_button.setFixedSize(32, 32)
        self.close_button.clicked.connect(self.close)
        downloads_header.addWidget(self.close_button)
        downloads_layout.addLayout(downloads_header)
        self.preview = ImagePreview()
        self.preview.hide()
        downloads_layout.addWidget(self.preview)
        downloads_layout.addStretch()
        self.preview_info = QtWidgets.QLabel("New images appear here until saved.")
        self.preview_info.setObjectName("Muted")
        self.preview_info.setWordWrap(True)
        self.preview_info.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        downloads_layout.addWidget(self.preview_info)
        self.saved_list = QtWidgets.QListWidget()
        self.saved_list.setObjectName("ImageNames")
        self.saved_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.saved_list.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.saved_list.setIconSize(QtCore.QSize(48, 48))
        self.saved_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.saved_list.hide()
        self.saved_list.currentItemChanged.connect(self._show_selected_image)
        self.saved_list.itemDoubleClicked.connect(self._open_image_preview)
        self.saved_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.saved_list.customContextMenuRequested.connect(self._show_image_menu)
        downloads_layout.addWidget(self.saved_list, 1)
        self.save_all_button = QtWidgets.QPushButton("Save All to Archive")
        self.save_all_button.clicked.connect(self._save_all_to_archive)
        self.save_all_button.hide()
        downloads_layout.addWidget(self.save_all_button)
        columns.addWidget(downloads_rail)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        outer.addWidget(self.status_label)
        self._close_timeout = QtCore.QTimer(self)
        self._close_timeout.setSingleShot(True)
        self._close_timeout.timeout.connect(self._close_after_timeout)
        last_source = str(self._settings.value(LAST_SOURCE_KEY, "ChatGPT") or "ChatGPT")
        self._select_source(last_source if last_source in self.source_buttons else "ChatGPT", remember=False)

    def _apply_theme(self, *_args) -> None:
        self.setStyleSheet(theme_css(self._theme_style))
        for name, button in getattr(self, "source_buttons", {}).items():
            button.setIcon(_site_icon(name))
        if hasattr(self, "_profile"):
            self._profile.settings().setAttribute(
                QWebEngineSettings.WebAttribute.ForceDarkMode, theme_manager().appearance == "Dark"
            )
        for name, frame in getattr(self, "_site_frames", {}).items():
            frame.setStyleSheet(theme_css("background: #080d19;"))
            loading = frame.widget(0)
            if isinstance(loading, QtWidgets.QLabel):
                loading.setStyleSheet(
                    f"background: {_site_surface(name)}; color: {_site_loading_text(name)};"
                )
        for name, portal in getattr(self, "_portal_frames", {}).items():
            portal.set_surface(_site_surface(name))
            portal.update()
            if portal._fade is not None:
                portal._fade.update()
        if hasattr(self, "preview"):
            self.preview.update()

    def _selected_source(self) -> str:
        return self._source_name

    def _current_view(self) -> SiteView:
        return self._views[self._selected_source()]

    def provider_state(self, name: str) -> str:
        return self._provider_state[name]

    def _start_provider(self, name: str) -> None:
        if name in self._provider_started_at:
            return
        self._provider_started_at[name] = time.monotonic()
        self._provider_state[name] = "preparing"
        self.providerStateChanged.emit(name, "preparing")
        GenLog.write_line("image_site_hub", f"Preparing {name}")

    def _set_provider_state(self, name: str, state: str, message: str = "") -> None:
        previous = self._provider_state[name]
        if previous == state and self._provider_message.get(name, "") == message:
            return
        self._provider_state[name] = state
        self._provider_message[name] = message
        if state == "ready" and name in self._preparation_retries:
            self._preparation_retries[name] = 0
        if state in {"ready", "sign_in", "error", "unverified"} and previous != state:
            elapsed = round((time.monotonic() - self._provider_started_at[name]) * 1000)
            GenLog.write_line("image_site_hub", f"{name} preparation: {state} after {elapsed} ms")
        self.providerStateChanged.emit(name, state)
        if name in self._site_frames and state in {"ready", "sign_in", "unverified"}:
            self._site_reveal_timers[name].start(250)
        if self._selected_source() != name:
            return
        self._show_selected_content()

    def _show_selected_content(self) -> None:
        name = self._selected_source()
        state = self._provider_state[name]
        if name == CODEX_LABEL:
            self.content.setCurrentWidget(self._codex)
        else:
            frame = self._site_frames[name]
            frame.setCurrentWidget(frame.widget(0) if state in {"preparing", "error"} else self._portal_frames[name])
            self.content.setCurrentWidget(frame)
        if state == "ready":
            self._status("")
        elif state == "sign_in":
            self._status(self._provider_message.get(name) or f"Sign in to {name} here to continue.")
        elif state == "error":
            self._status(self._provider_message.get(name) or f"{name} could not load. Retry or open it in Browser.")
        elif state == "unverified":
            self._status(self._provider_message.get(name) or f"{name} loaded, but its image prompt could not be verified. Check the page or reload it.")
        else:
            self._status(f"Still preparing {name}. You can wait, reload, or open it in Browser.")

    def _codex_connection_changed(self, message: str) -> None:
        service = self._codex.service
        if service.is_ready:
            self._set_provider_state(CODEX_LABEL, "ready")
        elif not service.is_connected and any(word in message.lower() for word in ("not found", "failed", "disconnected")):
            self._set_provider_state(CODEX_LABEL, "error", message)
        elif service.is_connected and service.account_checked and not service.is_chatgpt_signed_in:
            self._set_provider_state(CODEX_LABEL, "sign_in", "Sign in with ChatGPT in the Codex panel to continue.")
        elif service.is_chatgpt_signed_in and service.capability_checked:
            self._set_provider_state(CODEX_LABEL, "error", message)
        else:
            self._set_provider_state(CODEX_LABEL, "preparing")

    def _codex_availability_changed(self, _ready: bool) -> None:
        self._codex_connection_changed(self._codex.connection_label.text())

    def preload_generators(self) -> None:
        selected = self._selected_source()
        if selected == CODEX_LABEL:
            self._ensure_codex()
        else:
            self._ensure_site(selected)

    def _ensure_codex(self) -> None:
        if self._codex is None:
            self._start_provider(CODEX_LABEL)
            self._codex = IconImagePrototype()
            self._codex.service.imageReady.connect(self._import_codex_result)
            self._codex.service.busyChanged.connect(self._codex_busy_changed)
            self._codex.service.connectionChanged.connect(self._codex_connection_changed)
            self._codex.service.availabilityChanged.connect(self._codex_availability_changed)
            self.content.addWidget(self._codex)
            self._codex.start_service()

    def _ensure_site(self, name: str) -> None:
        if name in self._views:
            return
        self._start_provider(name)
        frame = QtWidgets.QStackedWidget(self.content)
        surface = _site_surface(name)
        frame.setStyleSheet(theme_css("background: #080d19;"))
        view = SiteView(self._profile, name, self._popups, frame)
        portal = PortalFrame(view, surface, name == "Microsoft Designer", frame)
        loading = QtWidgets.QLabel(f"Loading {name}...")
        loading.setAlignment(QtCore.Qt.AlignCenter)
        loading.setStyleSheet(f"background: {surface}; color: {_site_loading_text(name)};")
        frame.addWidget(loading)
        frame.addWidget(portal)
        frame.setCurrentWidget(loading)
        reveal_timer = QtCore.QTimer(frame)
        reveal_timer.setSingleShot(True)
        reveal_timer.timeout.connect(lambda source=name: self._reveal_site(source))
        probe_timer = QtCore.QTimer(frame)
        probe_timer.setInterval(1000)
        probe_timer.timeout.connect(lambda source=name: self._probe_site(source))
        view.loadStarted.connect(lambda source=name: self._site_loading(source))
        view.loadFinished.connect(lambda ok, source=name: self._site_loaded(source, ok))
        view.page().loadingChanged.connect(lambda info, source=name: self._site_load_changed(source, info))
        view.page().renderProcessTerminated.connect(
            lambda status, code, source=name: self._site_renderer_terminated(source, status, code)
        )
        view.urlChanged.connect(lambda url, source=name: self._site_url_changed(source, url))
        view.page().imageDragEnded.connect(
            lambda url, source=name: self._on_site_image_drag_ended(source, url)
        )
        self._views[name] = view
        self._portal_frames[name] = portal
        self._site_frames[name] = frame
        self._site_reveal_timers[name] = reveal_timer
        self._site_probe_timers[name] = probe_timer
        self._site_generations[name] = 0
        self._site_errors[name] = ""
        if name in self._brief_phase:
            timer = QtCore.QTimer(frame)
            timer.setInterval(1000)
            timer.timeout.connect(lambda source=name: self._check_brief_submitted(source))
            self._brief_timers[name] = timer
            image_timer = QtCore.QTimer(frame)
            image_timer.setInterval(3000)
            image_timer.timeout.connect(lambda source=name: self._scan_generated_images(source))
            self._image_scan_timers[name] = image_timer
        self.content.addWidget(frame)
        view.load(QtCore.QUrl(self._sites[name]))
        QtCore.QTimer.singleShot(20_000, lambda source=name: self._site_start_timeout(source))

    def _select_source(self, name: str, *, remember: bool = True) -> None:
        self._source_name = name
        self.source_buttons[name].setChecked(True)
        self.source_title.setText(name)
        if remember:
            self._settings.setValue(LAST_SOURCE_KEY, name)
            self._settings.sync()
        is_codex = name == CODEX_LABEL
        self.toolbar.setVisible(not is_codex)
        if is_codex:
            self._ensure_codex()
        else:
            self._ensure_site(name)
        for source, timer in self._image_scan_timers.items():
            if source != name:
                timer.stop()
        if name in self._image_scan_timers:
            self._image_scan_timers[name].start()
        self._show_selected_content()
        if name == "ChatGPT":
            QtCore.QTimer.singleShot(0, self._probe_chatgpt_sign_in)

    def _reload_selected_site(self) -> None:
        source = self._selected_source()
        if source in self._brief_phase and self._brief_phase[source] in {"typing", "typed", "failed"}:
            self._brief_phase[source] = "idle"
        if source == "ChatGPT":
            self._sage_selection_started = False
        self._current_view().reload()

    def _continue_preparation(self, source: str) -> None:
        if source not in self._brief_phase:
            return
        phase = self._brief_phase[source]
        if phase == "submitted":
            self._brief_attempts[source] = 0
            self._brief_timers[source].start()
            self._set_provider_state(source, "preparing", f"Checking {source}'s setup response...")
            self._check_brief_submitted(source)
        elif phase == "typed":
            self._brief_attempts[source] = 0
            self._set_provider_state(source, "preparing", f"Submitting {source}'s artwork brief...")
            self._send_brief(source)
        else:
            self._brief_phase[source] = "idle"
            if source == "ChatGPT":
                self._sage_selection_started = False
                self._probe_chatgpt_sign_in()
            else:
                self._probe_site(source)
            self._site_probe_timers[source].start()
            self._set_provider_state(source, "preparing", f"Retrying {source} preparation...")

    def _retry_preparation(self, source: str) -> None:
        if source == self._selected_source() and self._provider_state[source] == "unverified":
            self._continue_preparation(source)

    def _site_loading(self, source: str) -> None:
        self._site_generations[source] += 1
        if source in self._image_scan_timers:
            self._image_scan_timers[source].stop()
            self._image_scan_ready[source] = False
            self._seen_image_urls[source].clear()
            self._pending_image_urls[source].clear()
        self._site_reveal_timers[source].stop()
        self._site_probe_timers[source].stop()
        if source in self._brief_timers:
            self._brief_timers[source].stop()
        self._set_provider_state(source, "preparing")

    def _site_load_changed(self, source: str, info: QWebEngineLoadingInfo) -> None:
        if info.status() == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus:
            self._site_errors[source] = info.errorString().strip() or "The provider page failed to load."
            GenLog.write_line("image_site_hub", f"{source} page load failed: {self._site_errors[source]}", level="warning")
            self._set_provider_state(
                source,
                "error",
                f"{source} could not load: {self._site_errors[source]} Reload or open it in Browser.",
            )

    def _site_renderer_terminated(self, source: str, status: object, code: int) -> None:
        if source in self._image_scan_timers:
            self._image_scan_timers[source].stop()
        detail = f"{source}'s browser process stopped ({status.name}, code {code}). Reload the page or open it in Browser."
        self._site_errors[source] = detail
        GenLog.write_line("image_site_hub", detail, level="error")
        self._set_provider_state(source, "error", detail)

    def _site_loaded(self, source: str, ok: bool) -> None:
        if ok:
            self._site_errors[source] = ""
            self._match_portal_surface(source)
            self._site_probe_timers[source].start()
            if source in self._brief_phase and self._brief_phase[source] == "submitted":
                self._brief_attempts[source] = 0
                self._brief_timers[source].start()
                self._check_brief_submitted(source)
            if source in self._image_scan_timers and source == self._selected_source():
                self._image_scan_timers[source].start()
                QtCore.QTimer.singleShot(0, lambda name=source: self._scan_generated_images(name))
            QtCore.QTimer.singleShot(0, lambda name=source: self._probe_site(name))
            generation = self._site_generations[source]
            QtCore.QTimer.singleShot(
                15_000,
                lambda name=source, current=generation: self._site_unverified_if_pending(name, current),
            )
        else:
            self._site_frames[source].widget(0).setText(f"Could not load {source}.")
            reason = self._site_errors[source] or "The provider page failed to load."
            detail = f"{source} could not load: {reason} Reload or open it in Browser."
            GenLog.write_line("image_site_hub", detail, level="warning")
            self._set_provider_state(source, "error", detail)

    def _site_unverified_if_pending(self, source: str, generation: int) -> None:
        if generation != self._site_generations[source]:
            return
        if self._provider_state[source] == "preparing" and self._brief_phase.get(source) == "idle":
            reason = f" Last page error: {self._site_errors[source]}" if self._site_errors[source] else ""
            self._set_provider_state(source, "unverified", f"Could not find {source}'s image prompt.{reason} Reload the page or open it in Browser.")

    def _site_start_timeout(self, source: str) -> None:
        if self._provider_state[source] == "preparing" and self._brief_phase.get(source, "idle") == "idle":
            reason = f" Last page error: {self._site_errors[source]}" if self._site_errors[source] else ""
            self._set_provider_state(source, "unverified", f"{source} has not finished loading.{reason} Check the page, sign in, or reload it.")

    def _probe_site(self, source: str) -> None:
        if source == "ChatGPT":
            self._probe_chatgpt_sign_in()
            return
        view = self._views.get(source)
        if view is None:
            return
        generation = self._site_generations[source]
        script = f"""(() => {{
            const provider = {json.dumps(source)};
            const expected = {{
                'Gemini': 'gemini.google.com',
                'Adobe Firefly': 'firefly.adobe.com',
                'Microsoft Designer': 'designer.microsoft.com'
            }}[provider];
            if (location.hostname !== expected) return 'sign_in';
            if (document.readyState === 'loading') return 'preparing';
            const visible = element => element && !element.disabled &&
                element.getAttribute('aria-hidden') !== 'true' &&
                getComputedStyle(element).display !== 'none' &&
                getComputedStyle(element).visibility !== 'hidden';
            const signIn = Array.from(document.querySelectorAll('a, button')).find(element =>
                visible(element) && (/sign in|log in/i.test(
                    (element.getAttribute('aria-label') || '') + ' ' + element.textContent.trim()
                ) || /login|signin/i.test(element.getAttribute('href') || '')));
            if (signIn) return 'sign_in';
            const prompt = Array.from(document.querySelectorAll(
                'textarea, [contenteditable="true"][role="textbox"], input[placeholder*="prompt" i], ' +
                'input[aria-label*="prompt" i], input[placeholder*="describe" i]'
            )).find(element => visible(element) &&
                !/search/i.test((element.getAttribute('placeholder') || '') + ' ' +
                    (element.getAttribute('aria-label') || '')));
            if (prompt) return 'ready';
            return 'preparing';
        }})()"""
        view.page().runJavaScript(
            script,
            lambda result, name=source, current=generation: self._on_site_probe(name, current, result),
        )

    def _on_site_probe(self, source: str, generation: int, result: object) -> None:
        if source not in self._views or generation != self._site_generations[source]:
            return
        if result == "ready":
            if source == "Gemini" and self._brief_phase[source] != "complete":
                if self._brief_phase[source] == "idle":
                    if self._conversation_key(source, self._views[source].url()):
                        self._probe_existing_brief(source)
                    else:
                        self._set_provider_state(source, "preparing", "Preparing the Gemini conversation...")
                        self._begin_brief_submission(source)
            else:
                self._set_provider_state(source, "ready")
                self._site_probe_timers[source].stop()
        elif result == "sign_in":
            self._set_provider_state(source, "sign_in", f"Sign in to {source} in the page to continue.")
        elif result == "preparing" and self._provider_state[source] == "sign_in":
            self._set_provider_state(source, "preparing")

    def _scan_generated_images(self, source: str) -> None:
        if source != self._selected_source() or source not in self._views:
            return
        generation = self._site_generations[source]
        page_url = self._views[source].url().toString()
        script = f"""(() => {{
            const selector = {json.dumps(source)} === 'ChatGPT'
                ? '[data-testid="generated-image-gallery"] img, [data-message-author-role="assistant"] img, article[data-testid^="conversation-turn-"] img'
                : 'model-response img, .model-response img, [data-message-author-role="model"] img';
            return JSON.stringify([...new Set(Array.from(document.querySelectorAll(selector))
                .filter(image => image.complete && image.naturalWidth >= 256 && image.naturalHeight >= 256 &&
                    !image.closest('[data-message-author-role="user"], [data-user-message-bubble], user-query'))
                .map(image => image.currentSrc || image.src)
                .filter(url => /^https?:|^blob:/i.test(url)))]);
        }})()"""
        self._views[source].page().runJavaScript(
            script,
            lambda urls, name=source, current=generation, url=page_url:
                self._on_generated_images_found(name, urls, current, url),
        )

    def _on_generated_images_found(
        self, source: str, urls: object, generation: int | None = None, page_url: str | None = None
    ) -> None:
        if isinstance(urls, str):
            try:
                urls = json.loads(urls)
            except json.JSONDecodeError:
                return
        if (source != self._selected_source() or not isinstance(urls, list) or
                (generation is not None and generation != self._site_generations[source]) or
                (page_url is not None and page_url != self._views[source].url().toString())):
            return
        current = {url for url in urls if isinstance(url, str)}
        if not self._image_scan_ready[source]:
            self._pending_image_urls[source] = current
            self._image_scan_ready[source] = True
            return
        new_urls = current - self._seen_image_urls[source]
        for url in new_urls & self._pending_image_urls[source]:
            self._views[source].page().download(QtCore.QUrl(url))
            self._seen_image_urls[source].add(url)
        self._pending_image_urls[source] = new_urls - self._seen_image_urls[source]

    def _reveal_site(self, source: str) -> None:
        self._site_frames[source].setCurrentWidget(self._portal_frames[source])

    def _site_url_changed(self, source: str, url: QtCore.QUrl) -> None:
        if source in self._brief_phase:
            self._image_scan_ready[source] = False
            self._seen_image_urls[source].clear()
            self._pending_image_urls[source].clear()
            self._existing_brief_attempts[source] = 0
            key = self._conversation_key(source, url)
            if key and key in self._prepared_urls[source]:
                self._brief_phase[source] = "complete"
            elif (key and self._brief_phase[source] == "complete" and
                  QtCore.QUrl(self._brief_completed_url[source]).path() ==
                  ("/" if source == "ChatGPT" else "/app")):
                self._brief_completed_url[source] = url.toString()
                self._remember_prepared_url(source, key)
            elif self._brief_phase[source] in {"complete", "failed"} and url.toString() != self._brief_completed_url[source]:
                self._brief_phase[source] = "idle"
                if source == "ChatGPT":
                    self._sage_new_chat_started = url.path() == "/"
                    self._sage_selection_started = False
        if source == "ChatGPT" and "/auth/" in url.path():
            self._chatgpt_site_signed_in = False
            self._set_provider_state(source, "sign_in", "Sign in to ChatGPT in the page to continue.")
        self._site_probe_timers[source].start()
        QtCore.QTimer.singleShot(650, lambda name=source: self._probe_site(name))

    @staticmethod
    def _conversation_key(source: str, url: QtCore.QUrl) -> str:
        path = url.path()
        if source == "ChatGPT" and url.host() == "chatgpt.com" and "/c/" in path:
            conversation_id = path.rsplit("/c/", 1)[-1]
            if conversation_id and "/" not in conversation_id:
                return f"https://chatgpt.com/c/{conversation_id}"
        if source == "Gemini" and url.host() == "gemini.google.com" and path.startswith("/app/"):
            return f"https://gemini.google.com{path}"
        return ""

    def _remember_prepared_url(self, source: str, key: str) -> None:
        if not key or key in self._prepared_urls[source]:
            return
        recent = (self._prepared_url_order[source] + [key])[-100:]
        self._prepared_url_order[source] = recent
        self._prepared_urls[source] = set(recent)
        self._settings.setValue(f"image_generator/prepared_{source.lower()}", recent)
        self._settings.sync()

    def _preparation_failed(self, source: str, message: str, *, retry: bool = False) -> None:
        if self._brief_phase[source] == "complete":
            return
        self._site_probe_timers[source].stop()
        self._brief_timers[source].stop()
        if self._brief_phase[source] in {"idle", "typing"}:
            self._brief_phase[source] = "failed"
        self._set_provider_state(source, "unverified", message)
        if retry and self._preparation_retries[source] < 3:
            self._preparation_retries[source] += 1
            QtCore.QTimer.singleShot(2000, lambda name=source: self._retry_preparation(name))

    def _probe_existing_brief(self, source: str) -> None:
        if self._brief_phase[source] != "idle":
            return
        key = self._conversation_key(source, self._views[source].url())
        if not key:
            return
        self._existing_brief_attempts[source] += 1
        script = """(() => {
            const opening = 'This conversation is for creating artwork for a desktop icon-making app.';
            const messages = document.querySelectorAll(
                '[data-message-author-role="user"], [data-user-message-bubble], [data-turnrole="user"], ' +
                'user-query, .user-query, [class*="user-query-content"]'
            );
            return Array.from(messages).some(message => (message.textContent || '').includes(opening));
        })()"""
        self._views[source].page().runJavaScript(
            script, lambda found, name=source, current=key: self._on_existing_brief_probe(name, current, found)
        )

    def _on_existing_brief_probe(self, source: str, key: str, found: object) -> None:
        if (self._brief_phase[source] != "idle" or
                key != self._conversation_key(source, self._views[source].url())):
            return
        if found:
            self._brief_phase[source] = "complete"
            self._brief_completed_url[source] = self._views[source].url().toString()
            self._remember_prepared_url(source, key)
            self._site_probe_timers[source].stop()
            self._set_provider_state(source, "ready")
        elif self._existing_brief_attempts[source] >= 15:
            self._preparation_failed(source, f"Existing {source} conversation left unchanged. Start a new chat to apply the artwork brief.")

    def _begin_brief_submission(self, source: str) -> None:
        if self._brief_phase[source] != "idle":
            return
        view = self._views[source]
        url = view.url()
        key = self._conversation_key(source, url)
        if key and key in self._prepared_urls[source]:
            self._brief_phase[source] = "complete"
            self._set_provider_state(source, "ready")
            return
        home = url.path() == ("/" if source == "ChatGPT" else "/app")
        if not home:
            self._preparation_failed(source, f"Existing {source} conversation left unchanged. Start a new chat to apply the artwork brief.")
            return
        self._brief_phase[source] = "typing"
        QtCore.QTimer.singleShot(15_000, lambda name=source: self._brief_input_timeout(name))
        script = f"""(() => {{
            const provider = {json.dumps(source)};
            const brief = {json.dumps(OPENING_BRIEF, ensure_ascii=False)};
            if (location.hostname !== (provider === 'ChatGPT' ? 'chatgpt.com' : 'gemini.google.com')) return 'wrong_site';
            if (location.pathname !== (provider === 'ChatGPT' ? '/' : '/app')) return 'other_chat';
            const editor = provider === 'ChatGPT'
                ? document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]')
                : document.querySelector('[contenteditable="true"][role="textbox"][aria-label*="prompt" i], [contenteditable="true"][aria-label*="prompt" i], textarea[aria-label*="prompt" i]');
            if (!editor || !editor.getClientRects().length) return 'waiting';
            const previousMessages = document.querySelectorAll(
                '[data-message-author-role], user-query, model-response, .user-query, .model-response'
            );
            if (Array.from(previousMessages).some(element => (element.innerText || '').trim()))
                return 'existing_conversation';
            if ((editor.textContent || editor.value || '').includes('This conversation is for creating artwork'))
                return 'typed';
            const existing = (editor.textContent || editor.value || '').replace(/Sage of Iconer/g, '').trim();
            if (existing) return 'occupied';
            editor.focus();
            if (editor.isContentEditable) {{
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(editor);
                range.collapse(false);
                selection.removeAllRanges();
                selection.addRange(range);
                if (!document.execCommand('insertText', false, brief)) return 'failed';
            }} else {{
                const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
                setter.call(editor, brief);
                editor.dispatchEvent(new Event('input', {{bubbles: true}}));
            }}
            return (editor.textContent || editor.value || '').includes('This conversation is for creating artwork')
                ? 'typed' : 'failed';
        }})()"""
        view.page().runJavaScript(script, lambda result, name=source: self._on_brief_typed(name, result))

    def _brief_input_timeout(self, source: str) -> None:
        if self._brief_phase[source] in {"typing", "typed"}:
            self._preparation_failed(source, f"Could not confirm {source}'s artwork brief submission. Check the input or refresh the page.", retry=True)

    def _on_brief_typed(self, source: str, result: object) -> None:
        if self._brief_phase[source] != "typing":
            return
        if result == "typed":
            self._brief_phase[source] = "typed"
            self._brief_attempts[source] = 0
            QtCore.QTimer.singleShot(350, lambda name=source: self._send_brief(name))
        elif result == "waiting":
            self._brief_phase[source] = "idle"
            self._brief_attempts[source] += 1
            if self._brief_attempts[source] < 25:
                QtCore.QTimer.singleShot(500, lambda name=source: self._begin_brief_submission(name))
            else:
                self._preparation_failed(source, f"Could not find {source}'s image prompt. Check the page or refresh it.", retry=True)
        elif result == "occupied":
            self._preparation_failed(source, f"Your {source} draft was preserved. Send it or start an empty chat before applying the artwork brief.")
        elif result == "existing_conversation":
            self._preparation_failed(source, f"Existing {source} conversation left unchanged. Start a new chat to apply the artwork brief.")
        else:
            self._preparation_failed(source, f"Could not add the artwork brief to {source} ({result}). The conversation was not marked ready.", retry=True)

    def _send_brief(self, source: str) -> None:
        if self._brief_phase[source] != "typed":
            return
        script = """(() => {
            const editor = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"][aria-label*="prompt" i], [contenteditable="true"][role="textbox"], textarea[aria-label*="prompt" i]');
            if (!editor || !(editor.textContent || editor.value || '').includes('This conversation is for creating artwork')) return 'changed';
            const visible = element => element.getClientRects().length && !element.disabled &&
                element.getAttribute('aria-disabled') !== 'true';
            const button = Array.from(document.querySelectorAll('button')).find(element =>
                visible(element) && /send|submit/i.test(
                    [element.getAttribute('aria-label'), element.getAttribute('data-testid'), element.title].join(' ')
                ) && !/voice|feedback/i.test(element.getAttribute('aria-label') || '')
            );
            if (!button) return 'waiting';
            button.click();
            return 'clicked';
        })()"""
        self._views[source].page().runJavaScript(script, lambda result, name=source: self._on_brief_sent(name, result))

    def _on_brief_sent(self, source: str, result: object) -> None:
        if self._brief_phase[source] != "typed":
            return
        if result == "clicked":
            self._brief_phase[source] = "submitted"
            self._brief_attempts[source] = 0
            self._site_probe_timers[source].stop()
            self._brief_timers[source].start()
            self._set_provider_state(source, "preparing", f"Waiting for {source}'s setup response...")
        elif result == "waiting" and self._brief_attempts[source] < 8:
            self._brief_attempts[source] += 1
            QtCore.QTimer.singleShot(450, lambda name=source: self._send_brief(name))
        else:
            self._preparation_failed(source, f"The artwork brief is in {source}'s input but was not submitted. Check the page and send it once.", retry=True)

    def _check_brief_submitted(self, source: str) -> None:
        if self._brief_phase[source] != "submitted":
            self._brief_timers[source].stop()
            return
        self._brief_attempts[source] += 1
        script = f"""(() => {{
            const provider = {json.dumps(source)};
            const editorSelector = provider === 'ChatGPT'
                ? '#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]'
                : '[contenteditable="true"][role="textbox"][aria-label*="prompt" i], [contenteditable="true"][aria-label*="prompt" i], textarea[aria-label*="prompt" i]';
            const editor = Array.from(document.querySelectorAll(editorSelector)).find(
                element => element.getClientRects().length &&
                    getComputedStyle(element).visibility !== 'hidden'
            );
            const inputUsable = !!editor && !editor.disabled &&
                editor.getAttribute('aria-disabled') !== 'true';
            const userSelector = provider === 'ChatGPT'
                ? '[data-user-message-bubble], [data-message-author-role="user"], [data-turnrole="user"]'
                : 'user-query, .user-query, [data-message-author-role="user"]';
            const opening = 'This conversation is for creating artwork for a desktop icon-making app.';
            const userMessages = Array.from(document.querySelectorAll(userSelector));
            const userSent = userMessages.some(
                element => (element.innerText || '').includes(opening)
            ) || (provider === 'ChatGPT' ? /^\\/c\\/[^/]+/.test(location.pathname)
                : /^\\/app\\/[^/]+/.test(location.pathname));
            const briefStillTyped = !!editor && (editor.innerText || editor.value || '').includes(opening);
            const busy = Array.from(document.querySelectorAll('button')).some(element =>
                element.getClientRects().length && /stop (generating|response)|cancel response/i.test(
                    element.getAttribute('aria-label') || ''
                )
            );
            return JSON.stringify({{userSent: userSent && !briefStillTyped, inputUsable, busy,
                laterRequest: userMessages.length > 1}});
        }})()"""
        self._views[source].page().runJavaScript(script, lambda result, name=source: self._on_brief_checked(name, result))

    def _on_brief_checked(self, source: str, result: object) -> None:
        if self._brief_phase[source] != "submitted":
            return
        try:
            state = json.loads(result) if isinstance(result, str) else result
        except json.JSONDecodeError:
            state = None
        if (isinstance(state, dict) and state.get("userSent") and state.get("inputUsable")
                and (not state.get("busy") or state.get("laterRequest"))):
            self._brief_phase[source] = "complete"
            self._brief_completed_url[source] = self._views[source].url().toString()
            self._brief_timers[source].stop()
            self._remember_prepared_url(source, self._conversation_key(source, self._views[source].url()))
            self._set_provider_state(source, "ready")
        elif self._brief_attempts[source] >= 45:
            self._preparation_failed(source, f"The {source} brief was sent, but its usable input could not be confirmed. Check the conversation or refresh the page.", retry=True)

    def _match_portal_surface(self, source: str) -> None:
        if source == "Microsoft Designer":
            # Chromium's forced dark palette is not reflected in computed CSS colors.
            return
        script = """(() => {
            for (const element of [document.body, document.querySelector('main'), document.documentElement]) {
                if (!element) continue;
                const values = getComputedStyle(element).backgroundColor.match(/[\\d.]+/g);
                if (!values || values.length < 3 || (values.length > 3 && Number(values[3]) === 0)) continue;
                return '#' + values.slice(0, 3).map(value =>
                    Math.max(0, Math.min(255, Number(value))).toString(16).padStart(2, '0')
                ).join('');
            }
            return '';
        })()"""
        self._views[source].page().runJavaScript(
            script,
            lambda color, name=source: self._portal_frames[name].set_surface(color)
            if name in self._portal_frames and isinstance(color, str) else None,
        )

    def _probe_chatgpt_sign_in(self) -> None:
        view = self._views.get("ChatGPT")
        if view is None:
            return
        script = """(() => {
            if (location.hostname !== 'chatgpt.com') return 'unknown';
            const login = document.querySelector('a[href*="/auth/login"], [data-testid="login-button"]') ||
                Array.from(document.querySelectorAll('button')).find(button => /^(?:log in|sign in)$/i.test(button.textContent.trim()));
            const composer = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"], [contenteditable="true"][data-placeholder], textarea[placeholder="Ask ChatGPT"]');
            if (composer && !login) return 'signed_in';
            return login ? 'sign_in' : 'unknown';
        })()"""
        view.page().runJavaScript(script, self._on_chatgpt_sign_in_probe)

    def _on_chatgpt_sign_in_probe(self, state: object) -> None:
        if state in {"signed_in", "sign_in"}:
            self._chatgpt_site_signed_in = state == "signed_in"
        if state == "sign_in":
            self._set_provider_state("ChatGPT", "sign_in", "Sign in to ChatGPT in the page to continue.")
        elif state == "signed_in":
            if self._brief_phase["ChatGPT"] == "complete":
                self._set_provider_state("ChatGPT", "ready")
                self._site_probe_timers["ChatGPT"].stop()
            elif (self._brief_phase["ChatGPT"] == "idle" and
                  self._conversation_key("ChatGPT", self._views["ChatGPT"].url())):
                self._probe_existing_brief("ChatGPT")
            elif self._brief_phase["ChatGPT"] == "submitted" and self._provider_state["ChatGPT"] == "unverified":
                self._brief_attempts["ChatGPT"] = 0
                self._site_probe_timers["ChatGPT"].stop()
                self._brief_timers["ChatGPT"].start()
                self._set_provider_state("ChatGPT", "preparing", "Checking ChatGPT's setup response...")
                self._check_brief_submitted("ChatGPT")
            elif not self._sage_selection_started and self._brief_phase["ChatGPT"] == "idle":
                self._set_provider_state("ChatGPT", "preparing", "Preparing Sage of Iconer and the artwork brief...")
                self._begin_sage_selection()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._show_selected_content()
        if self._selected_source() == "ChatGPT":
            QtCore.QTimer.singleShot(0, self._probe_chatgpt_sign_in)

    def _begin_sage_selection(self) -> None:
        if self._brief_phase["ChatGPT"] != "idle":
            return
        view = self._views.get("ChatGPT")
        if view is None:
            return
        if view.url().path() != "/":
            self._preparation_failed("ChatGPT", "Existing ChatGPT conversation left unchanged. Start a new chat to apply Sage and the artwork brief.")
            return
        self._sage_selection_started = True
        if not self._sage_new_chat_started:
            self._sage_new_chat_started = True
            view.page().runJavaScript("""(() => {
                if (location.hostname !== 'chatgpt.com' || location.pathname !== '/') return 'other_page';
                const editor = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]');
                if (editor && editor.textContent.trim()) return 'occupied';
                if (Array.from(document.querySelectorAll('[data-message-author-role]')).some(
                    element => (element.innerText || '').trim()
                )) return 'existing_conversation';
                const button = Array.from(document.querySelectorAll('button[aria-label="New chat"], a[aria-label="New chat"]'))
                    .find(item => item.getClientRects().length);
                if (!button) {
                    const sidebar = Array.from(document.querySelectorAll('button[aria-label="Show sidebar"]'))
                        .find(item => item.getClientRects().length);
                    if (!sidebar) return 'missing';
                    sidebar.click();
                    return 'opened_sidebar';
                }
                button.click();
                return 'clicked';
            })()""", self._on_sage_new_chat_started)
            return
        script = f"""(() => {{
            if (location.hostname !== 'chatgpt.com' || location.pathname !== '/') return 'other_page';
            if (document.querySelector('button[aria-label="Remove Sage of Iconer"], [plugin-mention-name="{SAGE_PLUGIN_ID}"]')) return 'selected';
            const editor = document.querySelector('[contenteditable="true"][role="textbox"]');
            if (!editor) return 'waiting';
            editor.focus();
            if (editor.textContent.trim()) return 'occupied';
            document.execCommand('insertText', false, '@Sage of Iconer');
            return editor.textContent.trim() === '@Sage of Iconer' ? 'searching' : 'failed';
        }})()"""
        view.page().runJavaScript(script, self._on_sage_selection_started)

    def _on_sage_new_chat_started(self, state: object) -> None:
        if self._brief_phase["ChatGPT"] != "idle":
            return
        if state == "opened_sidebar":
            self._sage_new_chat_started = False
            QtCore.QTimer.singleShot(250, self._begin_sage_selection)
        elif state == "clicked":
            QtCore.QTimer.singleShot(400, self._begin_sage_selection)
        elif state == "occupied":
            self._preparation_failed("ChatGPT", "Your ChatGPT draft was preserved. Start an empty chat to apply Sage and the artwork brief.")
        elif state == "existing_conversation":
            self._preparation_failed("ChatGPT", "Existing ChatGPT conversation left unchanged. Start a new chat to apply Sage and the artwork brief.")
        else:
            self._send_chatgpt_brief_without_sage()

    def _on_sage_selection_started(self, state: object) -> None:
        if self._brief_phase["ChatGPT"] != "idle":
            return
        if state == "selected":
            self._sage_selection_finished(True)
        elif state == "searching":
            self._sage_selection_attempts = 0
            self._sage_selection_timer.start()
            if self._selected_source() == "ChatGPT":
                self._status("Selecting Sage of Iconer in a new ChatGPT chat...")
        elif state == "occupied":
            self._preparation_failed("ChatGPT", "Your ChatGPT draft was preserved. Start an empty chat to apply Sage and the artwork brief.")
        elif state == "waiting" and self._sage_selection_attempts < 25:
            self._sage_selection_attempts += 1
            QtCore.QTimer.singleShot(400, self._begin_sage_selection)
        elif state in {"waiting", "failed"}:
            self._send_chatgpt_brief_without_sage()
        else:
            self._preparation_failed("ChatGPT", "Could not prepare the ChatGPT page. Start an empty chat or refresh the page.", retry=True)

    def _poll_sage_selection(self) -> None:
        if self._brief_phase["ChatGPT"] != "idle":
            self._sage_selection_timer.stop()
            return
        if self._sage_selection_busy:
            return
        view = self._views.get("ChatGPT")
        if view is None:
            self._sage_selection_timer.stop()
            return
        self._sage_selection_attempts += 1
        self._sage_selection_busy = True
        script = f"""(() => {{
            if (location.hostname !== 'chatgpt.com' || location.pathname !== '/') return 'other_page';
            if (document.querySelector('button[aria-label="Remove Sage of Iconer"], [plugin-mention-name="{SAGE_PLUGIN_ID}"]')) return 'selected';
            const editor = document.querySelector('[contenteditable="true"][role="textbox"]');
            if (!editor || editor.textContent.trim() !== '@Sage of Iconer') return 'changed';
            const choices = Array.from(document.querySelectorAll('button[data-list-navigation-item="true"]'))
                .filter(button => button.getClientRects().length &&
                    button.innerText.split('\\n')[0].trim() === 'Sage of Iconer' &&
                    button.querySelector('svg') && !button.querySelector('img'));
            if (choices.length !== 1) return 'waiting';
            choices[0].click();
            return 'clicked';
        }})()"""
        view.page().runJavaScript(script, self._on_sage_selection_polled)

    def _on_sage_selection_polled(self, state: object) -> None:
        self._sage_selection_busy = False
        if self._brief_phase["ChatGPT"] != "idle":
            return
        if state == "selected":
            self._sage_selection_finished(True)
        elif state in {"changed", "other_page"}:
            self._sage_selection_timer.stop()
            self._preparation_failed("ChatGPT", "Sage was not enabled. Select Sage of Iconer with @ in a new chat.")
        elif self._sage_selection_attempts >= 25:
            self._sage_selection_timer.stop()
            self._send_chatgpt_brief_without_sage()

    def _sage_selection_finished(self, enabled: bool) -> None:
        self._sage_selection_timer.stop()
        if enabled:
            self._begin_brief_submission("ChatGPT")
        else:
            self._send_chatgpt_brief_without_sage()

    def _send_chatgpt_brief_without_sage(self) -> None:
        self._sage_selection_timer.stop()
        view = self._views.get("ChatGPT")
        if view is None:
            return
        script = """(() => {
            if (location.hostname !== 'chatgpt.com' || location.pathname !== '/') return 'other_page';
            const editor = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]');
            if (!editor) return 'waiting';
            if (editor.textContent.trim() === '@Sage of Iconer') {
                editor.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(editor);
                selection.removeAllRanges();
                selection.addRange(range);
                document.execCommand('delete');
            }
            return editor.textContent.trim() ? 'occupied' : 'ready';
        })()"""
        view.page().runJavaScript(script, self._on_chatgpt_brief_without_sage)

    def _on_chatgpt_brief_without_sage(self, state: object) -> None:
        if self._brief_phase["ChatGPT"] != "idle":
            return
        if state in {"ready", "waiting"}:
            self._set_provider_state("ChatGPT", "preparing", "Sending the artwork brief to ChatGPT...")
            self._begin_brief_submission("ChatGPT")
        elif state == "occupied":
            self._preparation_failed("ChatGPT", "Your ChatGPT draft was preserved. Start an empty chat to apply the artwork brief.")
        else:
            self._preparation_failed("ChatGPT", "Could not prepare ChatGPT's artwork brief. Start an empty chat or refresh the page.", retry=True)

    def _open_in_browser(self) -> None:
        name = self._selected_source()
        url = self._current_view().url()
        if not url.isValid() or url.isEmpty():
            url = QtCore.QUrl(self._sites[name])
        if QtGui.QDesktopServices.openUrl(url):
            self._status(f"Opened {name} in your browser.")
        else:
            self._status(f"Could not open {name} in your browser.")

    def _import_codex_result(self, path: str) -> None:
        try:
            self.downloads.import_file("Codex", Path(path))
        except (OSError, ValueError) as exc:
            self._status(f"Codex generated an image, but its preview could not be prepared: {exc}")

    def _add_result(self, provider: str, path: Path) -> QtWidgets.QListWidgetItem:
        prompt = self._last_codex_prompt if provider == CODEX_LABEL else ""
        name = _suggest_name(provider, path, prompt)
        item = QtWidgets.QListWidgetItem()
        item.setData(IMAGE_PATH_ROLE, str(path))
        item.setData(IMAGE_SAVED_ROLE, "")
        item.setData(IMAGE_NAME_ROLE, name)
        item.setData(IMAGE_PROVIDER_ROLE, provider)
        thumbnail = QtGui.QImageReader(str(path)).read()
        if not thumbnail.isNull():
            pixmap = QtGui.QPixmap.fromImage(thumbnail).scaled(
                48, 48, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            item.setIcon(QtGui.QIcon(pixmap))
        self._update_result_item(item)
        self.saved_list.insertItem(0, item)
        self._resize_saved_list()
        self.saved_list.setCurrentItem(item)
        return item

    def _update_result_item(self, item: QtWidgets.QListWidgetItem) -> None:
        name = str(item.data(IMAGE_NAME_ROLE))
        saved = bool(item.data(IMAGE_SAVED_ROLE))
        item.setText(f"✓ {name}" if saved else f"{name} · Unsaved")
        item.setToolTip("Saved to Archive" if saved else "Double-click to preview and save")

    def _update_save_all_button(self) -> None:
        unsaved = any(
            not self.saved_list.item(index).data(IMAGE_SAVED_ROLE)
            for index in range(self.saved_list.count())
        )
        self.save_all_button.setVisible(unsaved)

    def _resize_saved_list(self) -> None:
        count = self.saved_list.count()
        self.saved_list.setVisible(count > 0)

    def _on_preview_ready(self, provider: str, path_text: str) -> None:
        path = Path(path_text)
        self._add_result(provider, path)
        self._update_save_all_button()
        self._status(f"Image ready from {provider}. Save it to Archive when you are ready.")

    def _on_site_image_drag_ended(self, source: str, url: str) -> None:
        if not self.downloads_rail.rect().contains(
            self.downloads_rail.mapFromGlobal(QtGui.QCursor.pos())
        ):
            return
        try:
            started = self.downloads.download_to_archive(self._views[source].page(), QtCore.QUrl(url))
        except ValueError as exc:
            self._status(str(exc))
        else:
            self._status(f"Downloading {source} image to Archive..." if started else "Image download already in progress.")

    def _on_drag_preview_ready(self, provider: str, path_text: str) -> None:
        item = next(
            (self.saved_list.item(index) for index in range(self.saved_list.count())
             if self.saved_list.item(index).data(IMAGE_PATH_ROLE) == path_text),
            None,
        )
        if item is None:
            self._status("Downloaded image could not be found in Images.")
            return
        name = str(item.data(IMAGE_NAME_ROLE))
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?: \(\d+\))?", name, flags=re.IGNORECASE):
            name = f"{provider} image"
        for number in range(1, 1001):
            candidate = name if number == 1 else f"{name} ({number})"
            try:
                self._save_item_to_archive(item, candidate)
            except FileExistsError:
                continue
            except (OSError, RuntimeError, ValueError) as exc:
                self._status(f"Image downloaded, but could not be saved to Archive: {exc}")
            return
        self._status("Image downloaded, but Archive has too many images with this name.")

    def _show_selected_image(self, current: QtWidgets.QListWidgetItem | None, _previous: QtWidgets.QListWidgetItem | None) -> None:
        if current is None:
            self.preview.hide()
            return
        path = Path(current.data(IMAGE_PATH_ROLE))
        reader = QtGui.QImageReader(str(path))
        image = reader.read()
        if image.isNull():
            self.preview.set_image(QtGui.QImage())
            self.preview_info.setText(f"Could not preview {path.name}: {reader.errorString()}")
            return
        self.preview.set_image(image)
        self.preview.show()
        state = "Saved to Archive" if current.data(IMAGE_SAVED_ROLE) else "Unsaved preview"
        self.preview_info.setText(f"{image.width()} × {image.height()} pixels · {state}")

    def _save_item_to_archive(self, item: QtWidgets.QListWidgetItem, name: str) -> Path:
        saved = item.data(IMAGE_SAVED_ROLE)
        if saved:
            return Path(saved)
        destination = self.downloads.save_to_archive(Path(item.data(IMAGE_PATH_ROLE)), name)
        item.setData(IMAGE_SAVED_ROLE, str(destination))
        item.setData(IMAGE_NAME_ROLE, destination.stem)
        self._update_result_item(item)
        self._update_save_all_button()
        if item is self.saved_list.currentItem():
            self._show_selected_image(item, None)
        self._status(f"Saved to Archive: {destination.name}")
        return destination

    def _open_image_preview(self, item: QtWidgets.QListWidgetItem) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Image preview")
        dialog.resize(560, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        image = ImagePreview(dialog)
        image.setMinimumHeight(340)
        path = Path(item.data(IMAGE_PATH_ROLE))
        decoded = QtGui.QImageReader(str(path)).read()
        image.set_image(decoded)
        layout.addWidget(image, 1)
        if decoded.isNull():
            layout.addWidget(QtWidgets.QLabel("This image could not be previewed."))
        name_label = QtWidgets.QLabel("Image name")
        layout.addWidget(name_label)
        name_edit = QtWidgets.QLineEdit(str(item.data(IMAGE_NAME_ROLE)))
        name_edit.setAccessibleName("Image name")
        name_edit.setEnabled(not bool(item.data(IMAGE_SAVED_ROLE)))
        layout.addWidget(name_edit)
        suffix_label = QtWidgets.QLabel(f"File type: {path.suffix.lower()}")
        suffix_label.setObjectName("Muted")
        layout.addWidget(suffix_label)
        error_label = QtWidgets.QLabel()
        error_label.setStyleSheet(theme_css("color: #ffadad;"))
        error_label.setWordWrap(True)
        error_label.hide()
        layout.addWidget(error_label)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        save_button = QtWidgets.QPushButton("Save to Archive")
        save_button.setEnabled(not bool(item.data(IMAGE_SAVED_ROLE)))
        buttons.addWidget(save_button)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(dialog.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def save() -> None:
            try:
                self._save_item_to_archive(item, name_edit.text())
            except (OSError, RuntimeError, ValueError) as exc:
                error_label.setText(str(exc))
                error_label.show()
                return
            dialog.accept()

        save_button.clicked.connect(save)
        dialog.exec()

    def _show_image_menu(self, position: QtCore.QPoint) -> None:
        item = self.saved_list.itemAt(position)
        if item is None:
            return
        self.saved_list.setCurrentItem(item)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Preview and Rename…", lambda: self._open_image_preview(item))
        if not item.data(IMAGE_SAVED_ROLE):
            menu.addAction("Save This Image to Archive", lambda: self._open_image_preview(item))
        else:
            destination = Path(item.data(IMAGE_SAVED_ROLE))
            menu.addAction(
                "Open Saved Image",
                lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(destination))),
            )
        menu.addAction("Copy Image", lambda: self._copy_image(item))
        menu.exec(self.saved_list.viewport().mapToGlobal(position))

    def _copy_image(self, item: QtWidgets.QListWidgetItem) -> None:
        image = QtGui.QImageReader(str(item.data(IMAGE_PATH_ROLE))).read()
        if image.isNull():
            self._status("Could not copy this image.")
            return
        QtWidgets.QApplication.clipboard().setImage(image)
        self._status("Image copied to clipboard.")

    def _save_all_to_archive(self) -> None:
        items = [
            self.saved_list.item(index)
            for index in range(self.saved_list.count())
            if not self.saved_list.item(index).data(IMAGE_SAVED_ROLE)
        ]
        if not items:
            self._status("All images are already saved to Archive.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Save images to Archive")
        dialog.resize(530, min(640, 180 + len(items) * 70))
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("Review each image name before saving."))
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        rows = QtWidgets.QWidget()
        rows_layout = QtWidgets.QVBoxLayout(rows)
        editors: list[tuple[QtWidgets.QListWidgetItem, QtWidgets.QLineEdit, QtWidgets.QLabel]] = []
        for item in items:
            provider = str(item.data(IMAGE_PROVIDER_ROLE))
            suffix = Path(item.data(IMAGE_PATH_ROLE)).suffix.lower()
            row_label = QtWidgets.QLabel(f"{provider} · {suffix}")
            rows_layout.addWidget(row_label)
            name_edit = QtWidgets.QLineEdit(str(item.data(IMAGE_NAME_ROLE)))
            name_edit.setAccessibleName(f"Name for {provider} image")
            rows_layout.addWidget(name_edit)
            result = QtWidgets.QLabel()
            result.setWordWrap(True)
            result.hide()
            rows_layout.addWidget(result)
            editors.append((item, name_edit, result))
        rows_layout.addStretch(1)
        scroll.setWidget(rows)
        layout.addWidget(scroll, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        save_button = QtWidgets.QPushButton("Save All to Archive")
        buttons.addWidget(save_button)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(dialog.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def save_all() -> None:
            remaining = False
            for item, name_edit, result in editors:
                if item.data(IMAGE_SAVED_ROLE):
                    continue
                try:
                    self._save_item_to_archive(item, name_edit.text())
                except (OSError, RuntimeError, ValueError) as exc:
                    result.setText(str(exc))
                    result.setStyleSheet(theme_css("color: #ffadad;"))
                    result.show()
                    remaining = True
                else:
                    name_edit.setEnabled(False)
                    result.setText("Saved to Archive")
                    result.setStyleSheet(theme_css("color: #77d9cf;"))
                    result.show()
            if not remaining:
                dialog.accept()

        save_button.clicked.connect(save_all)
        dialog.exec()

    def _status(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))

    def _codex_busy_changed(self, busy: bool) -> None:
        if busy and self._codex is not None:
            self._last_codex_prompt = self._codex.prompt_edit.toPlainText()
        if not busy and self._closing_after_request and self.downloads.active_count == 0:
            QtCore.QTimer.singleShot(0, self.close)

    def _download_active_changed(self, count: int) -> None:
        if count == 0 and self._closing_after_request and (self._codex is None or not self._codex.service.is_busy):
            QtCore.QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        codex_busy = self._codex is not None and self._codex.service.is_busy
        if (codex_busy or self.downloads.active_count) and not self._allow_close:
            event.ignore()
            self._closing_after_request = True
            self._status("Cancellation requested; waiting for active requests to end before closing...")
            self._close_timeout.start(20_000)
            if codex_busy:
                self._codex.service.cancel()
            self.downloads.cancel_all()
            return
        if self.parentWidget() is not None and not self._allow_close and not self._shutting_down:
            self._closing_after_request = False
            self._close_timeout.stop()
            self._show_selected_content()
            event.ignore()
            self.hide()
            return
        if self._allow_close:
            self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._close_timeout.stop()
        self._sage_selection_timer.stop()
        for timer in self._site_probe_timers.values():
            timer.stop()
        for timer in self._brief_timers.values():
            timer.stop()
        for timer in self._image_scan_timers.values():
            timer.stop()
        for popup in list(self._popups):
            popup.close()
        if self._codex is not None:
            self._codex.service.shutdown()
        self.downloads.cleanup()

    def _close_after_timeout(self) -> None:
        if (self._codex is not None and self._codex.service.is_busy) or self.downloads.active_count:
            self._allow_close = True
            self._status("Cancellation was not confirmed; closing the connection.")
            self.close()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.styleHints().setColorScheme(QtCore.Qt.ColorScheme.Dark)
    window = ImageSiteHub()
    available = app.primaryScreen().availableGeometry()
    window.resize(min(window.width(), available.width() - 32), min(window.height(), available.height() - 32))
    window.move(available.center() - window.rect().center())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
