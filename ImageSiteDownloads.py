"""Hold embedded-site image downloads as unsaved previews."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from PySide6 import QtCore, QtGui
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile

import Gen2
import GenLog
import GenOps
from GenName import sanitize_piece, validate_image_stem


_IMAGE_SUFFIXES = Gen2.IMAGE_EXTS - {".svg"}
_MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


class ImageSiteDownloads(QtCore.QObject):
    statusChanged = QtCore.Signal(str)
    imageSaved = QtCore.Signal(str, str)
    archiveReady = QtCore.Signal(str, str)
    failed = QtCore.Signal(str)
    activeChanged = QtCore.Signal(int)

    def __init__(
        self,
        profile: QWebEngineProfile,
        parent: QtCore.QObject | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._temporary_workspace = tempfile.TemporaryDirectory(prefix="iconforge-previews-") if root is None else None
        self.root = Path(root) if root is not None else Path(self._temporary_workspace.name)
        self.root.mkdir(parents=True, exist_ok=True)
        self._reserved: set[Path] = set()
        self._active: dict[int, QWebEngineDownloadRequest] = {}
        self._archive_pending: set[tuple[str, str]] = set()
        self._archive_inflight: set[tuple[str, str]] = set()
        self._archive_downloads: dict[int, tuple[str, str]] = {}
        profile.setDownloadPath(str(self.root))
        profile.downloadRequested.connect(self._on_download_requested)

    @property
    def active_count(self) -> int:
        return len(self._active)

    def cancel_all(self) -> None:
        if self._active:
            self.statusChanged.emit("Download cancellation requested; waiting for confirmation...")
        for download in list(self._active.values()):
            download.cancel()

    def cleanup(self) -> None:
        """Remove unsaved previews after active downloads have finished."""
        if not self._active and self._temporary_workspace is not None:
            self._temporary_workspace.cleanup()
            self._temporary_workspace = None

    def _destination(self, provider: str, proposed_name: str, suffix: str) -> Path:
        folder = self.root / sanitize_piece(provider)
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(proposed_name).stem
        try:
            validate_image_stem(stem, suffix=suffix)
        except ValueError:
            stem = sanitize_piece(stem)[:100]
        candidate = folder / f"{stem}{suffix}"
        number = 2
        while candidate.exists() or candidate in self._reserved:
            candidate = folder / f"{stem} ({number}){suffix}"
            number += 1
        self._reserved.add(candidate)
        return candidate

    def download_to_archive(self, page: QWebEnginePage, url: QtCore.QUrl) -> bool:
        """Download a dragged site image, then request an Archive copy."""
        if url.scheme().lower() not in {"blob", "http", "https"}:
            raise ValueError("This image cannot be downloaded from the page.")
        provider = str(page.property("imageProvider") or "Website")
        key = (provider, url.toString())
        if key in self._archive_inflight:
            return False
        self._archive_pending.add(key)
        self._archive_inflight.add(key)
        QtCore.QTimer.singleShot(30000, lambda: self._expire_archive_request(key))
        page.download(url)
        return True

    def _expire_archive_request(self, key: tuple[str, str]) -> None:
        if key in self._archive_pending:
            self._archive_pending.discard(key)
            self._archive_inflight.discard(key)

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        page = download.page()
        provider = str(page.property("imageProvider") or "Website") if page else "Website"
        archive_key = (provider, download.url().toString())
        archive_requested = archive_key in self._archive_pending
        self._archive_pending.discard(archive_key)
        raw_name = re.split(r"[\\/]", download.suggestedFileName())[-1]
        suffix = Path(raw_name).suffix.lower()
        mime = download.mimeType().lower().split(";", 1)[0].strip()
        if download.isSavePageDownload() or (suffix not in _IMAGE_SUFFIXES and mime not in _MIME_SUFFIXES):
            if archive_requested:
                self._archive_inflight.discard(archive_key)
            download.cancel()
            self.failed.emit("This was not a supported image download. Use the site's image Download action.")
            return
        if suffix not in _IMAGE_SUFFIXES:
            suffix = _MIME_SUFFIXES[mime]
        try:
            target = self._destination(provider, raw_name or "generated-image", suffix)
        except OSError as exc:
            if archive_requested:
                self._archive_inflight.discard(archive_key)
            download.cancel()
            self.failed.emit(f"Could not prepare an image preview: {exc}")
            return
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download_id = download.id()
        self._active[download_id] = download
        if archive_requested:
            self._archive_downloads[download_id] = archive_key
        self.activeChanged.emit(self.active_count)
        download.receivedBytesChanged.connect(
            lambda item=download, name=target.name: self._on_progress(item, name, item.receivedBytes())
        )
        download.stateChanged.connect(
            lambda state, item=download, name=provider, path=target: self._on_state(item, name, path, state)
        )
        download.accept()
        self.statusChanged.emit(f"Downloading {target.name} from {provider}...")

    def _on_progress(self, item: QWebEngineDownloadRequest, name: str, received: int) -> None:
        total = item.totalBytes()
        if total > 0:
            self.statusChanged.emit(f"Downloading {name}: {received * 100 // total}%")
        else:
            self.statusChanged.emit(f"Downloading {name}: {received // 1024} KB received")

    def _on_state(
        self,
        item: QWebEngineDownloadRequest,
        provider: str,
        path: Path,
        state: QWebEngineDownloadRequest.DownloadState,
    ) -> None:
        if state not in {
            QWebEngineDownloadRequest.DownloadState.DownloadCompleted,
            QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
        }:
            return
        self._active.pop(item.id(), None)
        archive_key = self._archive_downloads.pop(item.id(), None)
        archive_requested = archive_key is not None
        if archive_key is not None:
            self._archive_inflight.discard(archive_key)
        self._reserved.discard(path)
        self.activeChanged.emit(self.active_count)
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            reader = QtGui.QImageReader(str(path))
            if not reader.read().isNull():
                self._log(f"image preview ready from {provider}: {path}")
                self.imageSaved.emit(provider, str(path))
                if archive_requested:
                    self.archiveReady.emit(provider, str(path))
            else:
                del reader
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                self.failed.emit(f"Downloaded {path.name}, but it is not a decodable image.")
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self.statusChanged.emit(f"Download canceled: {path.name}")
        else:
            self.failed.emit(f"Download interrupted: {item.interruptReasonString() or path.name}")

    def import_file(self, provider: str, source: Path) -> Path:
        """Copy a Codex result or an external image into private preview storage."""
        source = Path(source)
        if not source.is_file() or source.suffix.lower() not in _IMAGE_SUFFIXES:
            raise ValueError("Choose a supported image file.")
        reader = QtGui.QImageReader(str(source))
        if reader.read().isNull():
            raise ValueError("The selected file is not a decodable image.")
        target = self._destination(provider, source.name, source.suffix.lower())
        try:
            shutil.copyfile(source, target)
        except OSError:
            self._reserved.discard(target)
            raise
        self._reserved.discard(target)
        self._log(f"image preview imported from {provider}: {target}")
        self.imageSaved.emit(provider, str(target))
        return target

    def save_to_archive(self, source: Path, name: str) -> Path:
        """Copy an unsaved preview to the configured Icon Images archive."""
        source = Path(source).resolve(strict=True)
        if not source.is_relative_to(self.root.resolve()):
            raise ValueError("Choose an image from this window.")
        stem = validate_image_stem(name, suffix=source.suffix)
        archive_root = GenOps.load_archive_storage_root()
        if archive_root is None or not archive_root.is_dir():
            raise RuntimeError("Set an Archive Storage location in Icon Forge before saving.")
        if GenOps.is_archive_storage_paused():
            raise RuntimeError("Archive Storage is busy. Try again shortly.")
        paths = Gen2.EnginePaths.from_archive_storage_root(archive_root)
        destination, collision = Gen2.mirror_copy_to_archive_sources_ex(
            source, paths=paths, target_name=f"{stem}{source.suffix.lower()}",
        )
        if collision:
            raise FileExistsError(f"Archive already contains a different image named {destination.name}.")
        if destination is None or not destination.is_file():
            raise OSError("Could not copy the image into Archive Storage.")
        self._log(f"image added to archive: {destination}")
        try:
            GenOps.publish_app_event("archive-storage-changed", str(destination))
        except OSError:
            pass
        return destination

    @staticmethod
    def _log(message: str) -> None:
        try:
            GenLog.write_line("image_site_downloads", message)
        except OSError:
            pass
