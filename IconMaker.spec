# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


IS_MAC = sys.platform == "darwin"
ASSETS_DIR = Path("assets")
WIN_ICON = ASSETS_DIR / "Ico-Ico.ico"
MAC_ICON = ASSETS_DIR / "IconMaker.icns"
APP_VERSION = "1.0.0"


a = Analysis(
    ['IconMakerMaster.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['Gen1', 'Gen2', 'Gen3', 'Gen4', 'GenArchive', 'GenLog', 'GenName', 'GenOps', 'StateMemory'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe_kwargs = dict(
    name='IconMaker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not IS_MAC,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if IS_MAC and MAC_ICON.exists():
    exe_kwargs['icon'] = str(MAC_ICON)
elif WIN_ICON.exists():
    exe_kwargs['icon'] = [str(WIN_ICON)]

if not IS_MAC:
    exe_kwargs['version'] = 'IconMaker.versioninfo'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    **exe_kwargs,
)

if IS_MAC:
    app = BUNDLE(
        exe,
        name='IconMaker.app',
        icon=str(MAC_ICON) if MAC_ICON.exists() else None,
        bundle_identifier='com.infiniworks.iconmaker',
        info_plist={
            'CFBundleName': 'IconMaker',
            'CFBundleDisplayName': 'IconMaker',
            'CFBundleIdentifier': 'com.infiniworks.iconmaker',
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'CFBundlePackageType': 'APPL',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'NSHumanReadableCopyright': 'InfiniWorks',
        },
    )
