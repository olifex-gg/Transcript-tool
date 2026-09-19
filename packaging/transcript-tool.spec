# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the desktop build. Run from the repo root:
#     pyinstaller --noconfirm --clean packaging/transcript-tool.spec
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

APP_NAME = "Transcript Tool"
DIST_NAME = "transcript-tool"
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from yt_transcript import __version__  # noqa: E402

datas = [(os.path.join(ROOT, "yt_transcript", "static"), os.path.join("yt_transcript", "static"))]
binaries = []
# yt-dlp ships lazy extractor stubs and imports the real modules at runtime,
# and uvicorn picks loops/protocols by name, so static analysis misses them.
# (yt-dlp's own PyInstaller hook covers the rest of its dependencies.)
hiddenimports = collect_submodules("yt_dlp.extractor") + collect_submodules("uvicorn") + ["qrcode.image.svg"]

# Optional speech-to-text stack; skipped automatically when not installed.
for pkg in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface_hub", "numpy"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # noqa: BLE001
        continue
    datas += d
    binaries += b
    hiddenimports += h

is_mac = sys.platform == "darwin"
is_win = sys.platform.startswith("win")
icon = None
if is_mac and os.path.exists(os.path.join(SPECPATH, "icon.icns")):
    icon = os.path.join(SPECPATH, "icon.icns")
elif is_win and os.path.exists(os.path.join(SPECPATH, "icon.ico")):
    icon = os.path.join(SPECPATH, "icon.ico")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME if is_mac else DIST_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=not is_mac,  # Windows/Linux: a console window shows the address; macOS: a proper .app
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=DIST_NAME)
if is_mac:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=icon,
        bundle_identifier="dev.transcript-tool.desktop",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "MIT License",
        },
    )
