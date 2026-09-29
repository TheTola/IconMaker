"""Hold embedded-site image downloads as unsaved previews."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile

import Gen2
import GenLog
import GenOps
from GeneratedImageInfo import ImageOrigin, descriptive_title, image_stem, numbered_stem, save_record, utc_now
from GenName import MAX_NAME_LEN, sanitize_piece, validate_image_stem


_IMAGE_SUFFIXES = Gen2.IMAGE_EXTS - {".svg"}
_MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


@dataclass(frozen=True)
class _PreviewIdentity:
    origin: ImageOrigin
    base_stem: str
    fallback: bool


class ImageSiteDownloads(QtCore.QObject):
    statusChanged = QtCore.Signal(str)
    imageSaved = QtCore.Signal(str, str)
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
        self._image_url_pending: set[tuple[str, str]] = set()
        self._image_url_inflight: set[tuple[str, str]] = set()
        self._image_url_completed: set[tuple[str, str]] = set()
        self._image_url_downloads: dict[int, tuple[str, str]] = {}
        self._image_url_context: dict[tuple[str, str], ImageOrigin] = {}
        self._preview_identity: dict[Path, _PreviewIdentity] = {}
        self.last_metadata_error = ""
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
            self._preview_identity.clear()
            self._image_url_context.clear()

    def _destination(
        self, provider: str, proposed_name: str, suffix: str, origin: ImageOrigin | None = None
    ) -> Path:
        folder = self.root / sanitize_piece(provider)
        folder.mkdir(parents=True, exist_ok=True)
        folder = folder.resolve()
        origin = origin or ImageOrigin(provider)
        title = origin.title if descriptive_title(origin.title, provider) else proposed_name
        if not descriptive_title(title, provider):
            title = ""
        origin = ImageOrigin(provider, origin.prompt, title, origin.observed_at_utc or utc_now())
        base, fallback = image_stem(provider, title, origin.prompt)
        number = 1
        candidate = folder / f"{numbered_stem(base, number, suffix, fallback)}{suffix}"
        while candidate.exists() or candidate in self._reserved:
            number += 1
            candidate = folder / f"{numbered_stem(base, number, suffix, fallback)}{suffix}"
        self._reserved.add(candidate)
        self._preview_identity[candidate] = _PreviewIdentity(origin, base, fallback)
        return candidate

    def stage_url(self, page: QWebEnginePage, url: QtCore.QUrl, *, origin: ImageOrigin | None = None) -> bool:
        """Download one provider image into the unsaved preview area."""
        if url.scheme().lower() not in {"blob", "http", "https"}:
            raise ValueError("This image cannot be downloaded from the page.")
        provider = str(page.property("imageProvider") or "Website")
        key = (provider, url.toString())
        if key in self._image_url_pending or key in self._image_url_inflight or key in self._image_url_completed:
            return False
        self._image_url_pending.add(key)
        if origin is not None:
            self._image_url_context[key] = origin
        def expire() -> None:
            if key in self._image_url_pending:
                self._image_url_pending.discard(key)
                self._image_url_context.pop(key, None)
        QtCore.QTimer.singleShot(30000, expire)
        try:
            page.download(url)
        except Exception:
            self._image_url_pending.discard(key)
            self._image_url_context.pop(key, None)
            raise
        return True

    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        page = download.page()
        provider = str(page.property("imageProvider") or "Website") if page else "Website"
        image_key = (provider, download.url().toString())
        self._image_url_pending.discard(image_key)
        origin = self._image_url_context.pop(image_key, None)
        if origin is None and page is not None and hasattr(page, "take_image_download_context"):
            context = page.take_image_download_context()
            if context:
                origin = ImageOrigin(
                    provider, str(context.get("prompt") or ""), str(context.get("title") or ""),
                    str(context.get("observed_at_utc") or ""),
                )
        raw_name = re.split(r"[\\/]", download.suggestedFileName())[-1]
        suffix = Path(raw_name).suffix.lower()
        mime = download.mimeType().lower().split(";", 1)[0].strip()
        if download.isSavePageDownload() or (suffix not in _IMAGE_SUFFIXES and mime not in _MIME_SUFFIXES):
            download.cancel()
            self.failed.emit("This was not a supported image download. Use the site's image Download action.")
            return
        if suffix not in _IMAGE_SUFFIXES:
            suffix = _MIME_SUFFIXES[mime]
        try:
            target = self._destination(provider, raw_name or "generated-image", suffix, origin)
        except OSError as exc:
            download.cancel()
            self.failed.emit(f"Could not prepare an image preview: {exc}")
            return
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download_id = download.id()
        self._active[download_id] = download
        self._image_url_inflight.add(image_key)
        self._image_url_downloads[download_id] = image_key
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
        image_key = self._image_url_downloads.pop(item.id(), None)
        if image_key is not None:
            self._image_url_inflight.discard(image_key)
        self._reserved.discard(path)
        self.activeChanged.emit(self.active_count)
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            reader = QtGui.QImageReader(str(path))
            if not reader.read().isNull():
                if image_key is not None:
                    self._image_url_completed.add(image_key)
                self._log(f"image preview ready from {provider}: {path}")
                self.imageSaved.emit(provider, str(path))
            else:
                self._preview_identity.pop(path, None)
                del reader
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                self.failed.emit(f"Downloaded {path.name}, but it is not a decodable image.")
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self._preview_identity.pop(path, None)
            self.statusChanged.emit(f"Download canceled: {path.name}")
        else:
            self._preview_identity.pop(path, None)
            self.failed.emit(f"Download interrupted: {item.interruptReasonString() or path.name}")

    def import_file(self, provider: str, source: Path, *, origin: ImageOrigin | None = None) -> Path:
        """Copy a Codex result or an external image into private preview storage."""
        source = Path(source)
        if not source.is_file() or source.suffix.lower() not in _IMAGE_SUFFIXES:
            raise ValueError("Choose a supported image file.")
        reader = QtGui.QImageReader(str(source))
        if reader.read().isNull():
            raise ValueError("The selected file is not a decodable image.")
        target = self._destination(provider, source.name, source.suffix.lower(), origin)
        try:
            shutil.copyfile(source, target)
        except OSError:
            self._reserved.discard(target)
            self._preview_identity.pop(target, None)
            raise
        self._reserved.discard(target)
        self._log(f"image preview imported from {provider}: {target}")
        self.imageSaved.emit(provider, str(target))
        return target

    def import_image(self, provider: str, image: QtGui.QImage) -> Path:
        """Stage image data supplied by a native drop."""
        if image.isNull():
            raise ValueError("The dropped image could not be decoded.")
        target = self._destination(provider, "Dropped image.png", ".png")
        try:
            if not image.save(str(target), "PNG"):
                raise OSError("Could not prepare the dropped image preview.")
        except OSError:
            target.unlink(missing_ok=True)
            self._preview_identity.pop(target, None)
            raise
        finally:
            self._reserved.discard(target)
        self._log(f"image preview imported from {provider}: {target}")
        self.imageSaved.emit(provider, str(target))
        return target

    def save_to_archive(self, source: Path, name: str, *, auto_name: bool = False) -> Path:
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
        suffix = source.suffix.lower()
        identity = self._preview_identity.get(source)
        if auto_name and identity is not None:
            stem = identity.base_stem
        for number in range(1, 1001):
            if auto_name and identity is not None:
                candidate = numbered_stem(stem, number, suffix, identity.fallback)
            else:
                addition = "" if number == 1 else f" ({number})"
                candidate = stem[:MAX_NAME_LEN - len(suffix) - len(addition)] + addition
            destination, collision = Gen2.mirror_copy_to_archive_sources_ex(
                source, paths=paths, target_name=f"{candidate}{suffix}",
            )
            if not collision:
                break
        else:
            raise FileExistsError("Archive has too many images with this name.")
        if destination is None or not destination.is_file():
            raise OSError("Could not copy the image into Archive Storage.")
        self.last_metadata_error = ""
        if identity is not None:
            try:
                save_record(destination, identity.origin)
            except OSError as exc:
                self.last_metadata_error = str(exc)
                self._log(f"WARN: image metadata could not be saved for {destination.name}: {exc}")
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
