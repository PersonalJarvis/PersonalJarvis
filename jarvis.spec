# PyInstaller spec for the Personal Jarvis desktop app (Phase 1a).
#
# Strategy (see plan section 5.10 and the Phase 1a plan section 5):
# - `onedir` instead of `onefile` avoids 3-5 seconds of MEIPASS extraction and
#   allows each DLL to be signed independently.
# - Downloaded ML models are not bundled. The first-run wizard downloads them to
#   the platform-specific Jarvis model directory when the user enables them.
# - Optional native voice engines are loaded lazily and degrade to cloud paths
#   when unavailable. No Jarvis install profile requires torch or a GPU.
# - Excluding unused GUI frameworks saves roughly 500 MB.
#
# Invoke with `build.bat` or `pyinstaller jarvis.spec --noconfirm`.

# ruff: noqa

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821  (SPECPATH is PyInstaller-injected)
FRONTEND_DIST = PROJECT_ROOT / "jarvis" / "ui" / "web" / "dist"
PACKAGE_ASSETS = PROJECT_ROOT / "jarvis" / "assets"


# --- Data files -------------------------------------------------------------

datas = []

# Include the frontend build when present. Preserve its package-relative layout
# so the FastAPI static-files mount can serve it from a frozen application.
if FRONTEND_DIST.exists():
    for entry in FRONTEND_DIST.rglob("*"):
        if entry.is_file():
            rel = entry.relative_to(FRONTEND_DIST).parent
            datas.append((str(entry), str(Path("jarvis/ui/web/dist") / rel)))

# Include the default configuration loaded by a fresh desktop installation.
datas.append((str(PROJECT_ROOT / "jarvis.toml"), "."))

# Include build-time desktop assets such as icons and chimes when present.
assets_dir = PROJECT_ROOT / "assets"
if assets_dir.exists():
    for entry in assets_dir.rglob("*"):
        if entry.is_file():
            rel = entry.relative_to(assets_dir).parent
            datas.append((str(entry), str(Path("assets") / rel)))

# Package assets are runtime dependencies, not downloadable models. This
# explicitly includes the bundled CPU ONNX VAD model, wake backbones, licenses,
# and icons in the same paths that ``jarvis.assets`` resolves after freezing.
if PACKAGE_ASSETS.exists():
    for entry in PACKAGE_ASSETS.rglob("*"):
        if entry.is_file() and "__pycache__" not in entry.parts:
            rel = entry.relative_to(PACKAGE_ASSETS).parent
            datas.append((str(entry), str(Path("jarvis/assets") / rel)))

# Preserve distribution metadata so importlib.metadata can discover the Jarvis
# entry-point plugins in the frozen layout.
datas += copy_metadata("personal-jarvis")

# Legacy optional data packages are collected only when installed.
for pkg in ("chromadb", "sentence_transformers"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass


# --- Hidden imports ---------------------------------------------------------

hiddenimports: list[str] = []

# Entry-point-loaded Jarvis plugins and channels are invisible to static import
# analysis and must be collected explicitly.
hiddenimports += collect_submodules("jarvis.plugins")
hiddenimports += collect_submodules("jarvis.channels")

# Uvicorn standard installs version-specific backends through dynamic imports.
for pkg in (
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols",
    "uvicorn.loops",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "websockets.legacy",
    "httptools",
    "h11",
    "wsproto",
):
    hiddenimports.append(pkg)

# faster-whisper loads ctranslate2 dynamically when local voice is installed.
for pkg in ("faster_whisper", "ctranslate2"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass


# --- Bundle-size exclusions -------------------------------------------------

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "matplotlib",
    "IPython",
    "jupyter",
    "pytest",
    "notebook",
    "torch.test",
    "tornado",
]


# --- Analysis / executable --------------------------------------------------

block_cipher = None

a = Analysis(
    ["jarvis/__main__.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,             # UPX commonly triggers antivirus false positives.
    console=False,         # Match pythonw behavior without a console window.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icons" / "jarvis.ico"),  # Gigi mascot icon.
    uac_admin=False,       # Run asInvoker; elevate only individual actions.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Jarvis",
)
