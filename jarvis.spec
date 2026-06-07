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

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821  (SPECPATH is PyInstaller-injected)
FRONTEND_DIST = PROJECT_ROOT / "jarvis" / "ui" / "web" / "dist"


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

# jarvis.toml + profiles/ damit load_config() einen Default findet
datas.append((str(PROJECT_ROOT / "jarvis.toml"), "."))

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
    icon=str(PROJECT_ROOT / "assets" / "icons" / "jarvis.ico"),  # Gigi-Maskottchen (Schwarz/Gelb)
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
