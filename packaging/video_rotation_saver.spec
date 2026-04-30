# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path.cwd().resolve()


a = Analysis(
    [str(ROOT / "video_rotation_saver_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "config.sample.ini"), "."),
        (str(ROOT / "assets" / "branding"), "assets/branding"),
    ],
    hiddenimports=[
        "PIL._tkinter_finder",
        "winotify",
        "pystray",
        "tkinter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VideoRotationSaver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "build" / "assets" / "app.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
