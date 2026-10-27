# PyInstaller build for Telecaster.
#   pip install -e .[build]
#   pyinstaller packaging/telecaster.spec --noconfirm
# Windows -> dist/Telecaster.exe (single file); macOS -> dist/Telecaster.app

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from telecaster import __version__  # noqa: E402

ICON = str(ROOT / "src" / "telecaster" / "assets" / "icon.png")
platform_module = "telecaster.platforms.macos" if sys.platform == "darwin" else "telecaster.platforms.windows"

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(ROOT / "src" / "telecaster" / "assets"), "telecaster/assets")],
    hiddenimports=[platform_module],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtWebEngineCore"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Telecaster", console=False, icon=ICON)
    coll = COLLECT(exe, a.binaries, a.datas, name="Telecaster")
    app = BUNDLE(
        coll,
        name="Telecaster.app",
        icon=ICON,
        bundle_identifier="io.github.franktreadwell.telecaster",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "CFBundleDisplayName": "Telecaster",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name="Telecaster",
        console=False,
        icon=ICON,
        upx=False,
    )
