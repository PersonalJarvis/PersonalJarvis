# PyInstaller spec for the natively installed Personal Jarvis desktop app.
#
# Strategy:
# - `onedir` instead of `onefile` avoids 3-5 seconds of MEIPASS extraction and
#   allows each DLL to be signed independently.
# - TWO executables share ONE Analysis/COLLECT, because a native install has to
#   cover both surfaces the project promises:
#     * a windowed GUI launcher (Start Menu / Applications / .desktop), and
#     * a console CLI named `jarvis`, so `jarvis serve`, `jarvis --version`,
#       `jarvis missions list` behave exactly like the pip console script.
#   Both run `jarvis/__main__.py`; only the console flag differs.
# - The GUI binary is `PersonalJarvis`, not `Jarvis`: `Jarvis` and `jarvis`
#   are the SAME path on Windows (NTFS) and on a default macOS APFS volume, so
#   the second executable would overwrite the first. `PersonalJarvis` is the
#   name jarvis.core.branding already uses for the Windows branded launcher and
#   the macOS bundle executable.
# - Downloaded ML models are not bundled. The first-run wizard downloads them to
#   the platform-specific Jarvis model directory when the user enables them.
# - Optional native voice engines are loaded lazily and degrade to cloud paths
#   when unavailable. No Jarvis install profile requires torch or a GPU.
# - Excluding unused GUI frameworks saves roughly 500 MB.
#
# Invoke through the per-OS build script (packaging/<os>/build.*) or directly
# with `pyinstaller jarvis.spec --noconfirm --clean`.

# ruff: noqa

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821  (SPECPATH is PyInstaller-injected)
FRONTEND_DIST = PROJECT_ROOT / "jarvis" / "ui" / "web" / "dist"
PACKAGE_ASSETS = PROJECT_ROOT / "jarvis" / "assets"
ICON_DIR = PROJECT_ROOT / "assets" / "icons"

# The COLLECT directory name stays `Jarvis` — the per-OS packaging scripts and
# the release contract address the bundle as `dist/Jarvis/`.
BUNDLE_DIR_NAME = "Jarvis"
# Windowed launcher; see the header note on the case-collision.
GUI_EXE_NAME = "PersonalJarvis"
# Console entry point. Same name as the pip console script on purpose.
CLI_EXE_NAME = "jarvis"

MACOS_APP_NAME = "Personal Jarvis.app"
MACOS_BUNDLE_IDENTIFIER = "ai.personaljarvis.desktop"
MACOS_MIN_SYSTEM_VERSION = "12.0"


def _package_version() -> str:
    """Read ``jarvis.__version__`` without importing the package."""
    text = (PROJECT_ROOT / "jarvis" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("jarvis/__init__.py does not define __version__")
    return match.group(1)


VERSION = _package_version()


# --- Data files -------------------------------------------------------------

datas = []

# Include the frontend build when present. Preserve its package-relative layout
# so the FastAPI static-files mount can serve it from a frozen application.
if FRONTEND_DIST.exists():
    for entry in FRONTEND_DIST.rglob("*"):
        if entry.is_file():
            rel = entry.relative_to(FRONTEND_DIST).parent
            datas.append((str(entry), str(Path("jarvis/ui/web/dist") / rel)))

# Include the default configuration a fresh install is seeded from. The runtime
# hook copies it to the per-user app directory on first launch; the frozen app
# never reads or writes the copy inside the bundle.
datas.append((str(PROJECT_ROOT / "jarvis.toml"), "."))
datas.append((str(PROJECT_ROOT / "docs" / "product"), "docs/product"))

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

# Every non-Python file inside the `jarvis` package. PyInstaller collects only
# modules, so without this the frozen app silently loses its SQL migrations
# (jarvis/memory, jarvis/ultrawiki), the built-in skills (jarvis/skills/builtin
# — the boot log says "builtin skill '...' missing from package"), the CLI and
# skill catalogs, the wiki templates and the marketplace usage cards. Roughly
# 100 files / 400 KB, so collecting them wholesale costs nothing and closes the
# whole "works from source, missing when frozen" class at once.
_PACKAGE_DATA_SKIP_DIRS = {"__pycache__", "node_modules"}
_PACKAGE_DATA_SKIP_ROOTS = (
    PROJECT_ROOT / "jarvis" / "ui" / "web" / "frontend",  # source, not runtime
    FRONTEND_DIST,   # already collected above, with its own layout
    PACKAGE_ASSETS,  # collected explicitly below
)
_PACKAGE_DATA_SKIP_SUFFIXES = {".py", ".pyc", ".pyd", ".so", ".dylib", ".map"}
_package_root = PROJECT_ROOT / "jarvis"
for entry in _package_root.rglob("*"):
    if not entry.is_file():
        continue
    if _PACKAGE_DATA_SKIP_DIRS & set(entry.parts):
        continue
    if entry.suffix.lower() in _PACKAGE_DATA_SKIP_SUFFIXES:
        continue
    if any(entry.is_relative_to(skip) for skip in _PACKAGE_DATA_SKIP_ROOTS):
        continue
    rel = entry.relative_to(PROJECT_ROOT).parent
    datas.append((str(entry), str(rel)))

# Configuration profiles live beside the checkout root, and jarvis.core.config
# resolves them relative to it.
profiles_dir = PROJECT_ROOT / "profiles"
if profiles_dir.exists():
    for entry in profiles_dir.rglob("*"):
        if entry.is_file():
            rel = entry.relative_to(profiles_dir).parent
            datas.append((str(entry), str(Path("profiles") / rel)))

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

# Optional per-OS integrations reached through lazy imports at runtime. Only the
# ones actually installed on the build machine are added, so the spec stays
# buildable on a host without the platform extras.
_optional_hidden = [
    "webview",
    "pystray",
    "PIL",
]
if sys.platform == "win32":
    _optional_hidden += [
        "win32api",
        "win32com.client",
        "win32con",
        "win32gui",
        "win32process",
        "pythoncom",
        "pywintypes",
        "comtypes",
        "pycaw",
    ]
for pkg in _optional_hidden:
    try:
        __import__(pkg)
    except Exception:
        continue
    hiddenimports.append(pkg)


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
    # Development-only. They arrive through the `[dev]` extra that the build
    # machine installs (PyInstaller itself lives there too), and every one of
    # them is dead weight in a shipped app - mypy alone is tens of megabytes of
    # compiled mypyc extensions.
    "PyInstaller",
    "_pytest",
    "coverage",
    "hypothesis",
    "mypy",
    "mypyc",
    "ruff",
]


# --- Analysis ---------------------------------------------------------------

block_cipher = None

a = Analysis(
    ["jarvis/__main__.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    # Outranks pyinstaller-hooks-contrib (HOOK_PRIORITY_USER_HOOKS); see the
    # files in that directory for why each override exists.
    hookspath=[str(PROJECT_ROOT / "packaging" / "pyinstaller_hooks")],
    hooksconfig={},
    # Redirects jarvis.toml + the data directory to the per-user app directory
    # BEFORE jarvis.core.config freezes its import-time path constants.
    runtime_hooks=[str(PROJECT_ROOT / "packaging" / "pyinstaller_rthook_frozen.py")],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


# --- Windows version resource ----------------------------------------------
# Explorer's Details tab, the SmartScreen prompt and AV reputation engines read
# this. An unversioned binary is treated as less trustworthy.

version_resource = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    _numeric = [int(part) for part in re.findall(r"\d+", VERSION)[:4]]
    _numeric += [0] * (4 - len(_numeric))
    _filevers = tuple(_numeric)

    version_resource = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_filevers,
            prodvers=_filevers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Personal Jarvis"),
                            StringStruct("FileDescription", "Personal Jarvis"),
                            StringStruct("FileVersion", VERSION),
                            StringStruct("InternalName", GUI_EXE_NAME),
                            StringStruct(
                                "LegalCopyright",
                                "Personal Jarvis contributors. MIT licensed.",
                            ),
                            StringStruct("OriginalFilename", f"{GUI_EXE_NAME}.exe"),
                            StringStruct("ProductName", "Personal Jarvis"),
                            StringStruct("ProductVersion", VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def _icon_path():
    """The platform's icon, or ``None`` when it has not been generated yet."""
    candidate = ICON_DIR / ("jarvis.icns" if sys.platform == "darwin" else "jarvis.ico")
    if candidate.is_file():
        return str(candidate)
    print(f"[jarvis.spec] icon {candidate} is missing - building without one")
    return None


ICON = _icon_path()


# --- Executables ------------------------------------------------------------
# Two targets, one COLLECT: the windowed launcher and the console CLI.

exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=GUI_EXE_NAME,
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
    icon=ICON,
    version=version_resource,
    uac_admin=False,       # Run asInvoker; elevate only individual actions.
)

exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=CLI_EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # `jarvis serve` must print to the terminal it ran in.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=version_resource,
    uac_admin=False,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=BUNDLE_DIR_NAME,
)


# --- macOS application bundle ----------------------------------------------
# Cannot be produced anywhere else: BUNDLE only runs on darwin. packaging/macos
# wraps the resulting .app in the release DMG.

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=MACOS_APP_NAME,
        icon=ICON,
        bundle_identifier=MACOS_BUNDLE_IDENTIFIER,
        version=VERSION,
        info_plist={
            "CFBundleName": "Personal Jarvis",
            "CFBundleDisplayName": "Personal Jarvis",
            "CFBundleExecutable": GUI_EXE_NAME,
            "CFBundleIdentifier": MACOS_BUNDLE_IDENTIFIER,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundlePackageType": "APPL",
            "LSMinimumSystemVersion": MACOS_MIN_SYSTEM_VERSION,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            # Personal Jarvis is voice-first: without a usage string macOS kills
            # the process the moment it touches the matching API.
            "NSMicrophoneUsageDescription": (
                "Personal Jarvis listens for your wake word and your spoken "
                "requests. Audio stays on this Mac unless you configure a cloud "
                "speech provider yourself."
            ),
            "NSSpeechRecognitionUsageDescription": (
                "Personal Jarvis turns what you say into text so it can act on "
                "your request."
            ),
            "NSCameraUsageDescription": (
                "Personal Jarvis uses the camera only for features you start "
                "yourself, such as showing it what is in front of you."
            ),
            "NSAppleEventsUsageDescription": (
                "Personal Jarvis controls other applications on your behalf "
                "when you ask it to, for example to open a file or a window."
            ),
            "NSSystemAdministrationUsageDescription": (
                "Personal Jarvis needs Accessibility and Input Monitoring "
                "access to type, click and read the screen for the automation "
                "tasks you ask it to run."
            ),
            "NSDesktopFolderUsageDescription": (
                "Personal Jarvis saves the files it produces for you to your "
                "Desktop."
            ),
            "NSDocumentsFolderUsageDescription": (
                "Personal Jarvis reads and writes the documents you point it at."
            ),
            "NSDownloadsFolderUsageDescription": (
                "Personal Jarvis opens the downloads you ask it to work with."
            ),
            "NSLocalNetworkUsageDescription": (
                "Personal Jarvis serves its own interface to your browser on "
                "this machine."
            ),
        },
    )
