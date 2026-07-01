"""Creates the Jarvis icon + desktop shortcut + autostart shortcut.

Run once:

    python scripts/install_shortcuts.py

Result:
  1. assets/icons/jarvis.ico — multi-size Windows icon (black + signal yellow)
  2. Desktop\\Personal Jarvis.lnk — double-click opens the window
  3. shell:startup\\Personal Jarvis.lnk — starts Jarvis at Windows login

Both shortcuts point to run.bat in the project folder. run.bat uses
pythonw.exe, so no console window pops up.

Uninstall:
    python scripts/install_shortcuts.py --uninstall
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = PROJECT_ROOT / "assets" / "icons"
ICON_PATH = ICON_DIR / "jarvis.ico"
RUN_BAT = PROJECT_ROOT / "run.bat"

SHORTCUT_NAME = "Personal Jarvis.lnk"
APP_USER_MODEL_ID = "PersonalJarvis.PersonalJarvis"
DESCRIPTION = "Personal Jarvis — voice-controlled meta-orchestrator"

# Jarvis launcher module (pywebview window, no console)
LAUNCHER_MODULE = "jarvis.ui.web.launcher"


def desktop_path() -> Path:
    return Path(os.environ["USERPROFILE"]) / "Desktop" / SHORTCUT_NAME


def startup_path() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / SHORTCUT_NAME
    )


def generate_icon() -> None:
    """Jarvis-Icon: Matte-Schwarz-Kreis, gelber Signal-Sparkle, sanftes Glow.

    Design entspricht dem Frontend-Theme (#0A0A0A + #FFD60A).
    """
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    size = 512
    master = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Glow layer (separate canvas, then blur and place beneath the icon)
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((40, 40, size - 40, size - 40), fill=(255, 214, 10, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=24))
    master.paste(glow, (0, 0), glow)

    draw = ImageDraw.Draw(master)

    # Hauptkreis — matte Schwarz mit gelbem Ring
    pad = 32
    draw.ellipse(
        (pad, pad, size - pad, size - pad),
        fill=(10, 10, 10, 255),
        outline=(255, 214, 10, 255),
        width=8,
    )

    # Sparkle: vier Rauten-artige Spitzen (vertikal + horizontal)
    cx, cy = size // 2, size // 2
    r_long = 140
    r_short = 24

    yellow = (255, 214, 10, 255)

    # Vertikale Spitze
    draw.polygon(
        [(cx, cy - r_long), (cx + r_short, cy), (cx, cy + r_long), (cx - r_short, cy)],
        fill=yellow,
    )
    # Horizontale Spitze
    draw.polygon(
        [(cx - r_long, cy), (cx, cy - r_short), (cx + r_long, cy), (cx, cy + r_short)],
        fill=yellow,
    )
    # Heller Kern
    draw.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), fill=(255, 240, 140, 255))

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ICON_PATH, format="ICO", sizes=sizes)
    print(f"[ok] Icon written: {ICON_PATH}")


def _detect_pythonw() -> Path:
    """Finds pythonw.exe — prefers a venv in the project, else sys.executable.

    Why pythonw instead of python: python.exe shows a black console
    window for the lifetime of the process. pythonw.exe is a Windows
    GUI-subsystem binary → no console window, just the pywebview window.
    """
    # 1. Venv in the project
    venv_pyw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pyw.exists():
        return venv_pyw

    # 2. System Python next to sys.executable
    sys_py = Path(sys.executable)
    candidate = sys_py.with_name("pythonw.exe")
    if candidate.exists():
        return candidate

    # 3. Last fallback: PATH search via where.exe
    try:
        result = subprocess.run(
            ["where", "pythonw.exe"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if first:
            return Path(first)
    except Exception:  # noqa: BLE001
        pass

    raise RuntimeError(
        "pythonw.exe not found. The shortcut would show a console window. "
        "Install Python with the 'tcl/tk and IDLE' option or use a .venv.",
    )


def _set_shortcut_app_id(link: Path) -> bool:
    """Best-effort: writes the AppUserModelID into the .lnk property store."""
    try:
        import pywintypes  # type: ignore[import-not-found]
        from win32com.propsys import propsys, pscon  # type: ignore[import-not-found]

        store = propsys.SHGetPropertyStoreFromParsingName(
            str(link),
            None,
            2,  # GPS_READWRITE
            pywintypes.IID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"),
        )
        store.SetValue(
            pscon.PKEY_AppUserModel_ID,
            propsys.PROPVARIANTType(APP_USER_MODEL_ID),
        )
        store.Commit()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Shortcut AppID not set: {exc}")
        return False


def create_shortcut(
    link: Path,
    *,
    target: Path,
    args: str,
    working_dir: Path,
    icon: Path,
    description: str,
    window_style: int = 1,
) -> None:
    """Creates a .lnk shortcut via PowerShell/WScript.Shell.

    No pywin32 needed — WScript.Shell has been built in since Windows 2000.
    WindowStyle=1 = normal, 7 = minimized (autostart-/tray-friendly).
    """
    link.parent.mkdir(parents=True, exist_ok=True)

    # PowerShell script as a heredoc — paths are inserted via PS string quoting
    ps_script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{link}')\n"
        f"$sc.TargetPath = '{target}'\n"
        f"$sc.Arguments = '{args}'\n"
        f"$sc.WorkingDirectory = '{working_dir}'\n"
        f"$sc.IconLocation = '{icon}'\n"
        f"$sc.Description = '{description}'\n"
        f"$sc.WindowStyle = {window_style}\n"
        "$sc.Save()\n"
    )

    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        check=True,
    )
    _set_shortcut_app_id(link)
    print(f"[ok] Shortcut: {link}")


def _enable_autostart_via_port() -> None:
    """Delegate login-autostart to the cross-platform ``jarvis.autostart`` port.

    Collapses the two historical Windows autostart mechanisms (this script's own
    ``create_shortcut(startup_path())`` + the wizard's ``Jarvis.bat`` hack) into
    the single port implementation, and persists ``[autostart].enabled = true``.
    """
    from jarvis.autostart import make_autostart_manager, resolve_launch_spec
    from jarvis.core import config_writer
    from jarvis.platform.capabilities import detect_capabilities

    try:
        config_writer.set_autostart(True)
    except Exception as exc:  # noqa: BLE001 — persistence best-effort
        print(f"[warn] could not persist [autostart].enabled: {exc}")

    status = make_autostart_manager(detect_capabilities()).install(
        resolve_launch_spec(None), interactive=True
    )
    if status.installed:
        print(f"\n[ok] Autostart enabled at login: {status.entry_path}")
    else:
        print(f"\n[warn] Autostart not installed: {status.detail}")


def _disable_autostart_via_port() -> None:
    from jarvis.autostart import make_autostart_manager
    from jarvis.core import config_writer
    from jarvis.platform.capabilities import detect_capabilities

    try:
        config_writer.set_autostart(False)
    except Exception as exc:  # noqa: BLE001 — persistence best-effort
        print(f"[warn] could not persist [autostart].enabled: {exc}")
    make_autostart_manager(detect_capabilities()).uninstall(interactive=True)


def uninstall() -> None:
    # Desktop double-click shortcut — this script owns it.
    dp = desktop_path()
    if dp.exists():
        dp.unlink()
        print(f"[rm] {dp}")
    else:
        print(f"[--] not present: {dp}")
    # Autostart entry — delegate to the port (also clears the persisted toggle
    # and any legacy .bat/.lnk names).
    _disable_autostart_via_port()


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Jarvis shortcuts")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Removes the desktop and autostart shortcut",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="Desktop shortcut only, no autostart",
    )
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
        return 0

    if sys.platform != "win32":
        print("Error: only Windows is supported.", file=sys.stderr)
        return 1

    try:
        pythonw = _detect_pythonw()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"[ok] Launcher: {pythonw}")

    generate_icon()

    shortcut_args = f"-m {LAUNCHER_MODULE}"

    create_shortcut(
        desktop_path(),
        target=pythonw,
        args=shortcut_args,
        working_dir=PROJECT_ROOT,
        icon=ICON_PATH,
        description=DESCRIPTION,
        window_style=1,
    )

    if not args.no_autostart:
        # Delegate to the cross-platform autostart port (single source of truth),
        # instead of writing a second, divergent startup .lnk here.
        _enable_autostart_via_port()
    else:
        _disable_autostart_via_port()
        print("\nAutostart skipped — desktop shortcut created only.")

    print(f"\nDouble-clicking '{SHORTCUT_NAME}' on the desktop starts Jarvis.")
    print("(pythonw.exe -> no console window, just the Jarvis app)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
