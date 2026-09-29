"""Names and app-data provenance for images saved from the generator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import GenLog
from GenName import MAX_NAME_LEN


_GENERIC_TITLES = {
    "image", "img", "photo", "generatedimage", "generatedoriginal", "untitled", "unnamed", "download",
    "imagegeneration", "generatedart", "original", "output", "result",
}
_LEADING_WORDS = {
    "a", "an", "the", "please", "create", "generate", "make", "draw", "design",
    "render", "produce", "show", "me", "of", "for", "image", "icon", "picture",
    "illustration", "highly", "detailed",
}
_SKIP_WORDS = {
    "a", "an", "the", "of", "to", "about", "for", "in", "on", "at", "and",
    "is", "are", "be", "with", "its", "their", "highly", "detailed",
    "transparent", "background", "image", "icon", "picture", "illustration",
    "resolution", "style", "please",
}
_CLAUSE_BREAKS = {"with", "against", "featuring", "showing", "using", "including"}
_STORE_NAME = "Generated Image Metadata"


@dataclass(frozen=True)
class ImageOrigin:
    provider: str
    prompt: str = ""
    title: str = ""
    observed_at_utc: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value)
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def _stem(words: list[str]) -> str:
    return "_".join(word.capitalize() for word in words[:6])[:100].rstrip("_")


def descriptive_title(title: str, provider: str = "") -> str:
    """Return a safe title, or nothing for provider boilerplate and opaque IDs."""
    raw = str(title or "").strip()
    raw = re.sub(r"\.(?:png|jpe?g|webp|bmp|tiff?)$", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*\(\d+\)$", "", raw)
    raw = re.sub(r"[-_ ]*[0-9a-f]{8}-[0-9a-f-]{20,}$", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[-_ ]+[0-9a-f]{12,}$", "", raw, flags=re.IGNORECASE)
    words = _words(raw)
    if not words:
        return ""
    compact = "".join(words).casefold()
    provider_key = "".join(_words(provider)).casefold()
    provider_words = {word.casefold() for word in _words(provider)}
    if (compact in _GENERIC_TITLES or compact in {provider_key, f"{provider_key}image"} or
            (len(words) == 1 and words[0].casefold() in provider_words) or
            (provider_words and all(word.casefold() in provider_words | {
                "generated", "image", "img", "photo", "output", "result", "original", "download"
            } or word.isdigit() for word in words)) or
            re.fullmatch(r"(?:generated|image|img|photo|output|result|download|untitled|unnamed|file)\d*", compact) or
            re.fullmatch(r"screenshot\d{4,}", compact) or
            re.fullmatch(r"(?:[0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f-]{20,})", raw, re.IGNORECASE) or
            (len(words) == 1 and len(words[0]) >= 20 and any(ch.isdigit() for ch in words[0])) or
            all(word.isdigit() for word in words)):
        return ""
    if len(words) >= 2 and words[0].casefold() in {"generated", "image"} and all(
            word.isdigit() or word.casefold() in {"image", "original", "output"} for word in words[1:]):
        return ""
    if (provider_key and compact.startswith(f"{provider_key}image") and
            all(word.isdigit() for word in words[len(_words(provider)) + 1:])):
        return ""
    while words and words[0].casefold() in {"a", "an", "the", "image", "picture", "illustration", "of"}:
        words.pop(0)
    return _stem(words)


def prompt_stem(prompt: str) -> str:
    words = _words(str(prompt or "").replace(",", " ").replace(";", " "))
    while words and words[0].casefold() in _LEADING_WORDS:
        words.pop(0)
    chosen: list[str] = []
    for word in words:
        lower = word.casefold()
        if lower in _CLAUSE_BREAKS and len(chosen) >= 2:
            break
        if lower in _SKIP_WORDS:
            continue
        chosen.append(word)
        if len(chosen) == 6:
            break
    return _stem(chosen)


def image_stem(provider: str, title: str = "", prompt: str = "") -> tuple[str, bool]:
    titled = descriptive_title(title, provider)
    if titled:
        return titled, False
    prompted = prompt_stem(prompt)
    if prompted:
        return prompted, False
    source = "_".join(_words(provider)) or "Provider"
    return f"{source}_Image_001", True


def numbered_stem(base: str, number: int, suffix: str, fallback: bool = False) -> str:
    if fallback:
        prefix = re.sub(r"_\d{3}$", "", base)
        addition = f"_{number:03d}"
    else:
        prefix = base
        addition = "" if number == 1 else f"_{number}"
    return prefix[:MAX_NAME_LEN - len(suffix) - len(addition)].rstrip("_") + addition


def _store_dir() -> Path:
    return GenLog.app_data_dir() / _STORE_NAME


def _record_path(image_path: Path) -> Path:
    canonical = os.path.normcase(str(Path(image_path).resolve()))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return _store_dir() / f"{digest}.json"


def _write_record(image_path: Path, record: dict[str, object]) -> None:
    target = _record_path(image_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def save_record(image_path: Path, origin: ImageOrigin) -> None:
    path = Path(image_path).resolve()
    _write_record(path, {
        "version": 1,
        "image_path": str(path),
        "filename": path.name,
        "provider": origin.provider,
        "prompt": origin.prompt,
        "title": origin.title,
        "observed_at_utc": origin.observed_at_utc or utc_now(),
    })


def load_record(image_path: Path) -> dict[str, object] | None:
    try:
        with _record_path(image_path).open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except FileNotFoundError:
        return None
    return record if isinstance(record, dict) else None


def copy_record(source: Path, destination: Path) -> bool:
    record = load_record(source)
    if record is None:
        return False
    destination = Path(destination).resolve()
    record["image_path"] = str(destination)
    record["filename"] = destination.name
    _write_record(destination, record)
    return True


def move_record(source: Path, destination: Path) -> None:
    if copy_record(source, destination):
        delete_record(source)


def delete_record(image_path: Path) -> None:
    _record_path(image_path).unlink(missing_ok=True)


def copy_archive_records(source_images_dir: Path, target_images_dir: Path) -> int:
    source_root = Path(source_images_dir).resolve()
    target_root = Path(target_images_dir).resolve()
    folder = _store_dir()
    if not folder.exists():
        return 0
    copied = 0
    for record_path in folder.glob("*.json"):
        with record_path.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
        if not isinstance(record, dict) or not isinstance(record.get("image_path"), str):
            continue
        source = Path(record["image_path"]).resolve()
        try:
            relative = source.relative_to(source_root)
        except ValueError:
            continue
        destination = target_root / relative
        if destination.is_file():
            record["image_path"] = str(destination)
            record["filename"] = destination.name
            _write_record(destination, record)
            copied += 1
    return copied
