# PyInstaller spec — builds the desktop app for macOS and Windows.
# Driven by build/build-macos.sh or build/build-windows.ps1.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = os.environ.get("APP_NAME", "Lyrebird")
ROOT = Path(os.environ.get("PROJECT_ROOT", os.getcwd()))

hidden = (
    collect_submodules("faster_whisper")
    + collect_submodules("mlx_whisper")
    + collect_submodules("webview")
    + ["flask", "pynput", "sounddevice", "requests", "ctranslate2"]
)

datas = [
    (str(ROOT / "config" / "config.ini"), "config"),
    (str(ROOT / "config" / "dictionary.txt"), "config"),
]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("mlx_whisper")
datas += collect_data_files("webview")

a = Analysis(
    [str(ROOT / "src" / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    # torch is a declared dependency of mlx-whisper but is never touched at
    # runtime - verified by blocking the import and transcribing successfully.
    # Excluding it removes ~511MB from the bundle.
    excludes=[
        "torch", "torchvision", "torchaudio",
        "tkinter", "matplotlib", "pytest", "IPython", "notebook",
        "scipy", "pandas", "transformers", "datasets",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name=APP_NAME,
)

import sys
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier=f"com.freddygaffey.{APP_NAME.lower()}",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": "1.0.0",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            # Both are mandatory or macOS silently denies access with no error.
            "NSMicrophoneUsageDescription":
                f"{APP_NAME} needs the microphone to turn your speech into text. "
                "Audio never leaves this Mac.",
            "NSAppleEventsUsageDescription":
                f"{APP_NAME} types the transcribed text into the app you are using.",
        },
    )
