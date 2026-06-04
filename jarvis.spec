# PyInstaller-Spec für Personal Jarvis Desktop-App (Phase 1a).
#
# Strategie (siehe Plan §5.10 + Phase-1a-Plan §5):
# - `onedir` statt `onefile`: spart 3-5s Startzeit (kein MEIPASS-Unzip) und
#   erlaubt Code-Signing jeder einzelnen DLL individuell.
# - ML-Modelle sind **nicht** im Bundle — First-Run-Wizard lädt sie bei Bedarf
#   nach `%LOCALAPPDATA%\Jarvis\models\` herunter (User-Entscheidung 2026-04-20).
# - torch/CUDA werden nur lazy geladen; wenn vorhanden hidden-import, sonst
#   wird der Backend-Fallback aktiv (OpenAI-Whisper-API statt faster-whisper).
# - GUI-Excludes: tkinter, PyQt5, PySide6, matplotlib ersparen ~500 MB.
#
# Aufruf: `build.bat` (oder direkt `pyinstaller jarvis.spec --noconfirm`)

# ruff: noqa

import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821  (SPECPATH is PyInstaller-injected)
FRONTEND_DIST = PROJECT_ROOT / "jarvis" / "ui" / "web" / "dist"

# Cross-platform build (CLOUD.md Rule #1): the same spec produces a native
# bundle on Windows (onedir + .ico), macOS (.app via BUNDLE + .icns), and Linux
# (onedir + .png, wrapped into an AppImage by packaging/linux/). The OS is
# selected at build time from ``sys.platform``; the per-OS installer recipes
# live under ``packaging/`` and the build matrix in
# ``.github/workflows/build-app.yml``.
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Icon per platform, resolved from the shared path contract under
# assets/icons/. All three faces of the ghost-mascot icon now ship in the repo:
#   - jarvis.ico  (Windows, multi-resolution 16/24/32/48/64/128/256)
#   - jarvis.icns (macOS, full retina ladder ic07..ic14 / 16..1024)
#   - jarvis.png  (Linux, 256x256 RGBA)
# Each is generated from the canonical mascot art (assets/icons/jarvis-gigi-256.png).
# Missing is still tolerated (PyInstaller simply omits the icon then) so a partial
# checkout can still build.
_ICON_DIR = PROJECT_ROOT / "assets" / "icons"
if IS_WIN:
    _icon_path = _ICON_DIR / "jarvis.ico"
elif IS_MAC:
    _icon_path = _ICON_DIR / "jarvis.icns"
else:
    _icon_path = _ICON_DIR / "jarvis.png"
ICON = str(_icon_path) if _icon_path.exists() else None


# --- Data-Files -------------------------------------------------------------

datas = []

# Frontend-Build (wenn vorhanden). PyInstaller kopiert die kompletten
# Static-Assets so dass `sys._MEIPASS/jarvis/ui/web/dist/` existiert und der
# FastAPI-StaticFiles-Mount sie ausliefert.
if FRONTEND_DIST.exists():
    for entry in FRONTEND_DIST.rglob("*"):
        if entry.is_file():
            rel = entry.relative_to(FRONTEND_DIST).parent
            datas.append((str(entry), str(Path("jarvis/ui/web/dist") / rel)))

# jarvis.toml default config so load_config() finds a default in the bundle.
# A fresh checkout (e.g. a CI runner) has only jarvis.toml.example — the real
# jarvis.toml is gitignored / wizard-generated — so fall back to the example and
# never hard-fail the build when neither file is present.
for _cfg in (PROJECT_ROOT / "jarvis.toml", PROJECT_ROOT / "jarvis.toml.example"):
    if _cfg.exists():
        datas.append((str(_cfg), "."))
        break

# Assets (Icons, Chimes) wenn vorhanden
assets_dir = PROJECT_ROOT / "assets"
if assets_dir.exists():
    for entry in assets_dir.rglob("*"):
        if entry.is_file():
            rel = entry.relative_to(assets_dir).parent
            datas.append((str(entry), str(Path("assets") / rel)))

# Dependency-Metadata (entry_points!) — ohne die findet importlib.metadata
# die Jarvis-Plugins im gebundelten Layout nicht.
datas += copy_metadata("personal-jarvis")

# chromadb, sentence_transformers haben interne Data-Files (Tokenizer, Model-
# Index-Templates etc.). Die brauchen wir erst ab Phase 2/6, aber wenn die
# Module da sind werden sie gleich mitgebundelt.
for pkg in ("chromadb", "sentence_transformers", "silero_vad"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass


# --- Hidden-Imports ---------------------------------------------------------

hiddenimports: list[str] = []

# Alle Jarvis-Plugin-Module — die werden via entry_points geladen, PyInstaller
# sieht die statischen Imports nicht.
hiddenimports += collect_submodules("jarvis.plugins")
hiddenimports += collect_submodules("jarvis.channels")

# Companion packages the main app imports at boot (board_backend, overlay,
# skillbook). They live in their own top-level packages, so PyInstaller needs
# them named explicitly to bundle them into the frozen app. Tolerate absence
# so a partial dev checkout can still build the base bundle.
for pkg in ("board_backend", "overlay", "skillbook"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# The Orb overlay is a full-app feature backed by PySide6. We do NOT
# ``collect_submodules("PySide6")`` — PySide6 has hundreds of submodules and
# collecting them all makes the Windows analysis balloon (it effectively hangs
# the build). PyInstaller ships its own PySide6 hooks that bundle only what is
# actually imported, so a plain top-level hidden-import is enough; the overlay
# subprocess degrades gracefully if Qt is unavailable in the frozen app.
for _qt in ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
    if _qt not in hiddenimports:
        hiddenimports.append(_qt)

# Uvicorn/websockets/http-tools — uvicorn[standard] bringt versionsspezifische
# Backends die via importlib geladen werden.
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

# ctranslate2 wird von faster-whisper dynamisch nachgeladen
for pkg in ("faster_whisper", "ctranslate2"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass


# --- Excludes (Bundle-Size-Sparen) ------------------------------------------

# NOTE: PySide6 is intentionally NOT excluded — the Orb overlay is a default
# full-app feature now. The other GUI toolkits stay excluded to save size.
excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "matplotlib",
    "IPython",
    "jupyter",
    "pytest",
    "notebook",
    "torch.test",
    "tornado",
]


# --- Analyse / EXE ----------------------------------------------------------

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
    upx=False,             # UPX macht AV-Falschpositive; lieber unpacked.
    console=False,         # pythonw-aequivalent — kein Console-Fenster
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,             # per-OS icon (jarvis.ico / jarvis.icns / .png) or None
    uac_admin=False,       # asInvoker — kein UAC-Prompt
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

# macOS: wrap the onedir COLLECT into a proper .app bundle. The .dmg is built
# from this by packaging/macos in the CI build step. The Info.plist declares the
# microphone usage string macOS requires before the app may access the mic.
if IS_MAC:
    app = BUNDLE(
        coll,
        name="Jarvis.app",
        icon=ICON,
        bundle_identifier="ai.PersonalJarvis.app",
        info_plist={
            "CFBundleName": "Jarvis",
            "CFBundleDisplayName": "Personal Jarvis",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSMicrophoneUsageDescription":
                "Personal Jarvis uses the microphone for voice input.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
