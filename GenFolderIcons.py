"""Windows Explorer icons for IconMaker folders."""

from __future__ import annotations

import ctypes
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Gen2 import EnginePaths


LOCAL_ICON_NAME = ".iconmaker-folder.ico"
DESKTOP_INI_NAME = "desktop.ini"
_OWNED_MARKER = "; IconMaker folder icon"
_DESKTOP_INI = (
    f"{_OWNED_MARKER}\r\n"
    "[.ShellClassInfo]\r\n"
    f"IconResource={LOCAL_ICON_NAME},0\r\n"
    "ConfirmFileOp=0\r\n"
).encode("ascii")
_FILE_ATTRIBUTE_READONLY = 0x1
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def is_folder_icon_metadata(path: Path) -> bool:
    """Identify files that archive cleanup must leave in place."""
    return path.name.casefold() in {LOCAL_ICON_NAME, DESKTOP_INI_NAME}


def _is_linked_folder(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def folder_icon_asset(name: str) -> Path:
    """Locate a packaged ICO in source and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / "folder_icons" / name


def _kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    dll.GetFileAttributesW.restype = ctypes.c_uint32
    dll.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    dll.SetFileAttributesW.restype = ctypes.c_int
    return dll


def _attributes(path: Path) -> int:
    result = _kernel32().GetFileAttributesW(str(path))
    if result == _INVALID_FILE_ATTRIBUTES:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def _set_attributes(path: Path, attributes: int) -> bool:
    existing = _attributes(path)
    desired = existing | attributes
    if desired == existing:
        return False
    if not _kernel32().SetFileAttributesW(str(path), desired):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def _write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False

    temp_path = None
    original_attributes = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".iconmaker-", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
        if path.exists():
            original_attributes = _attributes(path)
            if original_attributes & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM):
                if not _kernel32().SetFileAttributesW(
                    str(path), original_attributes & ~(_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM)
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
        try:
            os.replace(temp_path, path)
        except OSError:
            if original_attributes is not None and path.exists():
                _kernel32().SetFileAttributesW(str(path), original_attributes)
            raise
        return True
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _desktop_ini_text(content: bytes) -> tuple[str, str] | None:
    try:
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            return content.decode("utf-16"), "utf-16"
        if content.startswith(b"\xef\xbb\xbf"):
            return content.decode("utf-8-sig"), "utf-8-sig"
        return content.decode("utf-8"), "utf-8"
    except UnicodeError:
        return None


def _updated_desktop_ini(content: bytes, *, replace_existing_icon: bool) -> bytes | None:
    decoded = _desktop_ini_text(content)
    if decoded is None:
        return None
    text, encoding = decoded
    owned = _OWNED_MARKER.casefold() in text.casefold() or bool(
        re.search(r"^\s*IconResource\s*=\s*\.iconmaker-folder\.ico\s*,\s*\d+\s*$", text, re.I | re.M)
    )
    if not (owned or replace_existing_icon):
        return None

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    in_shell_section = False
    found_shell_section = False
    wrote_icon = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_shell_section and not wrote_icon:
                output.append(f"IconResource={LOCAL_ICON_NAME},0{newline}")
                wrote_icon = True
            in_shell_section = stripped.casefold() == "[.shellclassinfo]"
            found_shell_section |= in_shell_section
        if in_shell_section and "=" in line:
            key = line.split("=", 1)[0].strip().casefold()
            if key in {"iconresource", "iconfile", "iconindex"}:
                if not wrote_icon:
                    ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                    output.append(f"IconResource={LOCAL_ICON_NAME},0{ending}")
                    wrote_icon = True
                continue
        output.append(line)
    if not wrote_icon:
        if output and not output[-1].endswith(("\r", "\n")):
            output.append(newline)
        if not found_shell_section:
            output.append(f"[.ShellClassInfo]{newline}")
        output.append(f"IconResource={LOCAL_ICON_NAME},0{newline}")
    return "".join(output).encode(encoding)


def _refresh_explorer(folder: Path) -> None:
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
        shell32.SHChangeNotify(0x00002000, 0x0005, str(folder), None)
    except OSError:
        pass


def apply_folder_icon(folder: Path, icon_asset: Path, *, replace_existing_icon: bool = False) -> bool:
    """Apply a portable folder icon without replacing unrelated customization."""
    if os.name != "nt":
        return True

    folder = Path(folder)
    icon_asset = Path(icon_asset)
    if not folder.is_dir() or _is_linked_folder(folder) or not icon_asset.is_file():
        return False

    desktop_ini = folder / DESKTOP_INI_NAME
    local_icon = folder / LOCAL_ICON_NAME
    try:
        if desktop_ini.is_symlink() or local_icon.is_symlink():
            return False
        if desktop_ini.exists():
            desktop_ini_content = _updated_desktop_ini(
                desktop_ini.read_bytes(), replace_existing_icon=replace_existing_icon
            )
            if desktop_ini_content is None:
                return False
        else:
            desktop_ini_content = _DESKTOP_INI

        changed = _write_if_changed(local_icon, icon_asset.read_bytes())
        changed |= _write_if_changed(desktop_ini, desktop_ini_content)
        changed |= _set_attributes(local_icon, _FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM)
        changed |= _set_attributes(desktop_ini, _FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM)
        changed |= _set_attributes(folder, _FILE_ATTRIBUTE_READONLY)
        if changed:
            _refresh_explorer(folder)
        return True
    except Exception:
        return False


def ensure_iconmaker_folder_icons(paths: EnginePaths) -> None:
    """Customize existing archive folders without creating a folder tree."""
    if os.name != "nt":
        return

    try:
        generic_asset = folder_icon_asset("icofolder.ico")
        generated_asset = folder_icon_asset("icon.ico")
        if not paths.images_dir.is_dir():
            return

        for current, folders, _files in os.walk(paths.images_dir):
            folder = Path(current)
            asset = generated_asset if folder == paths.icons_dir else generic_asset
            apply_folder_icon(
                folder, asset, replace_existing_icon=folder in {paths.images_dir, paths.icons_dir}
            )
            folders[:] = [
                name for name in folders
                if not _is_linked_folder(folder / name)
                and name.casefold() != "logs"
            ]
    except Exception:
        pass


def ensure_install_folder_icon() -> None:
    if os.name != "nt":
        return
    try:
        folder = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        apply_folder_icon(folder, folder_icon_asset("iconmaker.ico"), replace_existing_icon=True)
    except Exception:
        pass


def ensure_startup_folder_icons() -> None:
    """Best-effort presentation of the installed app and saved archive."""
    if os.name != "nt":
        return
    try:
        ensure_install_folder_icon()
        import GenOps
        from Gen2 import EnginePaths

        root = GenOps.load_archive_storage_root()
        if root is not None and root.is_dir():
            ensure_iconmaker_folder_icons(EnginePaths.from_archive_storage_root(root))
    except Exception:
        pass
