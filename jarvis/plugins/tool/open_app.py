"""open_app-Tool: öffnet eine Windows-Anwendung.

Risk-Tier: monitor — schreibt Toast-Notification, läuft aber ohne Approval.

Plausibility-Check (2026-04-24): vor dem OS-Call wird app_name gegen Regex
+ Whitelist/PATH geprueft. Blockiert STT-Halluzinationen wie
"WDR mediagroup GmbH im Auftrag des WDR, 2020" die sonst direkt in
os.startfile landen wuerden.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.platform import detect_platform, window_state
from jarvis.plugins.tool.app_resolver import (
    _resolve_via_start_menu,
    resolve_app_launch_target,
)

# Apps for which a second window is normal/expected — never short-circuit these
# to "already running -> focus"; the user almost always wants a fresh instance.
_MULTI_INSTANCE_APPS: frozenset[str] = frozenset({
    "explorer", "cmd", "powershell", "pwsh", "wt", "windowsterminal",
    "terminal", "conhost", "gnome-terminal", "konsole", "xterm",
})

# Bekannte Apps + haeufige Aliases. Reicht fuer 95% der Voice-Commands;
# exotische Apps laufen ueber PATH-Resolve oder expliziten Pfad.
#
# The whitelist is platform-conditional (AD-15): it keeps the anti-STT-
# hallucination gate intact with the correct per-OS names. The plausibility
# gate (``_is_plausible_app_name``), the regexes, and the PATH/URL/path escape
# hatches are reused verbatim — only the active set swaps by platform.
_KNOWN_APPS_WIN: frozenset[str] = frozenset({
    # System
    "notepad", "calc", "calculator", "explorer", "cmd", "powershell",
    "pwsh", "wt", "windowsterminal", "terminal", "regedit", "msinfo32",
    "taskmgr", "control", "settings",
    # Browser
    "chrome", "firefox", "edge", "msedge", "brave", "opera", "vivaldi",
    # Dev
    "code", "vscode", "pycharm", "idea", "webstorm", "clion", "rider",
    "cursor", "windsurf", "zed",
    # Kommunikation
    "outlook", "teams", "slack", "discord", "telegram", "whatsapp", "signal",
    "zoom", "skype",
    # Media
    "spotify", "vlc", "mpv", "potplayer", "obs", "obs64",
    # Office
    "word", "excel", "powerpoint", "onenote", "winword", "excel.exe",
    # Grafik
    "mspaint", "paint", "photoshop", "illustrator", "figma", "gimp", "inkscape",
    # Gaming / Misc
    "steam", "epicgameslauncher", "battle.net", "blender",
    # Built-in Windows Store / UWP apps (launched via a protocol URI by the
    # resolver, see app_resolver._UWP_PROTOCOLS). Whitelisted so the
    # plausibility gate does not reject them as "not found" (live 2026-06-22).
    "microsoft store", "windows store", "store",
    # Jarvis-intern
    "jarvis",
})

_KNOWN_APPS_DARWIN: frozenset[str] = frozenset({
    # System (launched via `open -a <AppName>`)
    "finder", "terminal", "iterm", "calculator", "calc", "preview",
    "system settings", "system preferences", "activity monitor",
    "textedit", "notes", "reminders",
    # Browser
    "safari", "chrome", "firefox", "brave", "edge", "opera", "vivaldi", "arc",
    # Dev
    "code", "vscode", "pycharm", "idea", "webstorm", "clion", "rider",
    "cursor", "windsurf", "zed", "xcode",
    # Kommunikation
    "mail", "messages", "facetime", "teams", "slack", "discord", "telegram",
    "whatsapp", "signal", "zoom", "skype",
    # Media
    "music", "spotify", "vlc", "mpv", "quicktime player", "obs",
    # Office
    "pages", "numbers", "keynote", "word", "excel", "powerpoint", "onenote",
    # Grafik
    "photos", "photoshop", "illustrator", "figma", "gimp", "inkscape",
    # Misc
    "steam", "blender",
    # Jarvis-intern
    "jarvis",
})

_KNOWN_APPS_LINUX: frozenset[str] = frozenset({
    # System
    "nautilus", "files", "gnome-terminal", "konsole", "xterm", "terminal",
    "gnome-calculator", "kcalc", "calc", "calculator", "gedit", "kate",
    "gnome-control-center", "settings", "gnome-system-monitor",
    # Browser
    "firefox", "chromium", "chromium-browser", "chrome", "google-chrome",
    "brave", "brave-browser", "opera", "vivaldi", "epiphany",
    # Dev
    "code", "vscode", "pycharm", "idea", "webstorm", "clion", "rider",
    "cursor", "windsurf", "zed",
    # Kommunikation
    "thunderbird", "evolution", "teams", "slack", "discord", "telegram",
    "telegram-desktop", "signal", "signal-desktop", "zoom", "skype",
    # Media
    "rhythmbox", "spotify", "vlc", "mpv", "totem", "obs",
    # Office
    "libreoffice", "libreoffice-writer", "libreoffice-calc",
    "libreoffice-impress", "onlyoffice",
    # Grafik
    "eog", "gimp", "inkscape", "krita", "blender",
    # Misc
    "steam", "nautilus-desktop",
    # Jarvis-intern
    "jarvis",
})


def _select_known_apps() -> frozenset[str]:
    """Return the active app whitelist for this platform (AD-15)."""
    plat = detect_platform()
    if plat == "win32":
        return _KNOWN_APPS_WIN
    if plat == "darwin":
        return _KNOWN_APPS_DARWIN
    return _KNOWN_APPS_LINUX


# Module attribute resolvable on every OS (acceptance: `from
# jarvis.plugins.tool.open_app import KNOWN_APPS`).
KNOWN_APPS: frozenset[str] = _select_known_apps()

# Regex gegen offensichtlich kaputte app_names:
# - Max 60 Zeichen
# - Nur Woerter + Pfad-Zeichen + URL-Schema-Zeichen erlaubt
# - Keine Komma-Listen oder Satzzeichen-Ketten
_APP_NAME_RE = re.compile(r"^[\w\-\.\:\/\\ ]{1,60}$")

# STT-Halluzinations-Marker die auf Whisper-Misshearings hindeuten (Werbe-
# Outros, Copyright-Strings). Doppelter Schutz zusaetzlich zum Pipeline-
# Level-Guard, falls das Brain direkt aus einem System-Kontext aufgerufen
# wird (z.B. Text-Chat in der Desktop-App).
_HALLUCINATION_RE = re.compile(
    r"\b("
    r"im\s+auftrag\s+des|mediagroup|gmbh|"
    r"untertitel\s+(von|der)|"
    r"copyright\s+\d{4}|all\s+rights\s+reserved"
    r")\b",
    re.IGNORECASE,
)


def _is_plausible_app_name(app_name: str) -> tuple[bool, str, str]:
    """Prueft ob ``app_name`` plausibel ist.

    Returns ``(ok, reason, kind)``. ``reason`` ist leerer String wenn
    ``ok=True``. ``kind`` klassifiziert die Ablehnung fuer eine passende
    Fehlermeldung: ``"misheard"`` (wirkt wie STT-Halluzination — Rueckfrage an
    den User) oder ``"not_found"`` (plausibler Name, nur nicht installiert —
    voller Pfad / genauer Name noetig). ``""`` wenn ``ok=True``.
    """
    if _HALLUCINATION_RE.search(app_name):
        return False, "enthaelt Werbe-/Outro-Marker (wirkt wie STT-Misshearing)", "misheard"
    if not _APP_NAME_RE.match(app_name):
        return False, "Format unplausibel (zu lang, Komma-Liste oder Sonderzeichen)", "misheard"

    # Layer 2: Whitelist ODER URL ODER Pfad ODER PATH-Resolve
    low = app_name.lower().removesuffix(".exe")
    is_url = app_name.startswith(("http://", "https://", "file://"))
    is_path = (
        any(sep in app_name for sep in (":\\", ":/"))
        or app_name.startswith((".", "\\", "/"))
    )
    if low in KNOWN_APPS or is_url or is_path:
        return True, "", ""
    if shutil.which(app_name):
        return True, "", ""
    # Layer 3: Start-Menu shortcut. Many installed apps register ONLY a
    # per-user .lnk (Electron/Tauri/Squirrel builds: Discord, Slack, and the
    # user's own desktop apps like BridgeSpace/BridgeVoice) — absent from both
    # the whitelist and PATH. The resolver already follows these; mirror it
    # here so the gate is not blinder than the launcher (live failure
    # 2026-06-16: a real installed app was rejected as a misshearing).
    if _resolve_via_start_menu({low, app_name.strip()}) is not None:
        return True, "", ""
    return (
        False,
        f"'{app_name}' nicht gefunden (weder Whitelist, PATH noch Startmenue)",
        "not_found",
    )


class OpenAppTool:
    name: str = "open_app"
    risk_tier: str = "monitor"
    description: str = (
        "Öffnet eine Windows-Anwendung per Namen (z.B. 'notepad', 'calc', 'chrome') "
        "oder eine Datei/URL."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name der Anwendung oder Pfad/URL",
            },
            "arguments": {
                "type": "string",
                "description": "Optionale CLI-Argumente",
                "default": "",
            },
            "reuse_existing": {
                "type": "boolean",
                "description": (
                    "If the app is already running, focus its existing window "
                    "instead of launching a new instance (default true). Set "
                    "false to force a fresh window."
                ),
                "default": True,
            },
        },
        "required": ["app_name"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        app_name = (args.get("app_name") or "").strip()
        cli_args = (args.get("arguments") or "").strip()
        if not app_name:
            return ToolResult(success=False, output=None, error="app_name fehlt")

        ok, reason, kind = _is_plausible_app_name(app_name)
        if not ok:
            # Error-Message mit expliziter Handlungsanweisung an das Brain.
            # Differenziert nach Ablehnungsgrund — ein plausibler, nur nicht
            # installierter Name darf NICHT als STT-Misshearing abgetan werden
            # (das schickte den Agenten in die falsche Richtung).
            if kind == "not_found":
                hint = (
                    "Die App ist nicht installiert/auffindbar. Falls sie "
                    "existiert, gib den vollen Pfad zur .exe an; sonst frage "
                    "den User nach dem genauen App-Namen. Rufe open_app NICHT "
                    "erneut mit dem selben Wert auf."
                )
            else:
                hint = (
                    "Wahrscheinlich STT-Misshearing. Frage den User kurz: "
                    "'Welche App genau, Alex?' — rufe open_app NICHT erneut "
                    "mit dem selben Wert auf."
                )
            return ToolResult(
                success=False,
                output=None,
                error=f"App-Name '{app_name[:80]}' abgelehnt ({reason}). {hint}",
            )

        # Already-running short-circuit: if the app is open, focus its existing
        # window instead of launching a second instance (saves a CU step — the
        # "OBS is already in the taskbar" case). Conservative: only for plausible
        # single-instance app names, never for URLs/paths or multi-instance apps,
        # and only when reuse is requested. A focus failure falls through to a
        # normal launch (never blocks). is_app_running is best-effort and never
        # raises, so this can only help, never break, the launch path.
        reuse_existing = bool(args.get("reuse_existing", True))
        low = app_name.lower().removesuffix(".exe")
        is_url = app_name.startswith(("http://", "https://", "file://"))
        is_path = (
            any(sep in app_name for sep in (":\\", ":/"))
            or app_name.startswith((".", "\\", "/"))
        )
        if reuse_existing and not is_url and not is_path and low not in _MULTI_INSTANCE_APPS:
            running = window_state.is_app_running(app_name)
            if running is not None:
                focused, _msg = window_state.focus_window(running.title)
                if focused:
                    return ToolResult(
                        success=True,
                        output=f"{app_name} is already running — brought it to the front.",
                    )
                # focus failed (e.g. foreground-lock) -> fall through to launch

        try:
            launch_target = resolve_app_launch_target(app_name)
            kind = launch_target.kind
            value = launch_target.value
            no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # NOTE: every launch below is an intentional, plausibility-gated,
            # shell=False process start (the whole purpose of this tool). The
            # ASYNC220 (subprocess in async fn) / S606 (no-shell start) lints are
            # suppressed per-line — the gate ran above and shell=False is hard.
            if kind == "open_a":
                # macOS: `open -a <AppName> [--args <split-args>]`. shell=False.
                argv = ["open", "-a", value]
                if cli_args:
                    argv += ["--args", *cli_args.split()]
                subprocess.Popen(argv, shell=False)  # noqa: ASYNC220, S606
            elif kind == "xdg_open":
                # Linux: `xdg-open <name>` (MIME/.desktop handler; takes no args).
                subprocess.Popen(["xdg-open", value], shell=False)  # noqa: ASYNC220, S606
            elif kind == "executable":
                # Direct executable on PATH/absolute. The Windows verb passes the
                # raw cli_args string verbatim (AD-7, unchanged); the POSIX
                # launchers split it into separate argv tokens.
                if cli_args:
                    extra = (
                        [cli_args] if detect_platform() == "win32" else cli_args.split()
                    )
                    subprocess.Popen(  # noqa: ASYNC220, S606
                        [value, *extra], shell=False, creationflags=no_window
                    )
                else:
                    subprocess.Popen(  # noqa: ASYNC220, S606
                        [value], shell=False, creationflags=no_window
                    )
            else:  # kind == "startfile" — Windows-only shell verb (AD-7).
                if cli_args:
                    subprocess.Popen(  # noqa: ASYNC220, S606
                        ["cmd", "/c", "start", "", value, cli_args],
                        shell=False,
                        creationflags=no_window,
                    )
                elif hasattr(os, "startfile"):
                    os.startfile(value)  # type: ignore[attr-defined]  # noqa: S606
                else:
                    # POSIX: URLs/paths resolve to "startfile" on every OS
                    # (the resolver's escape hatch runs before the platform
                    # branch), but exec'ing a URL dies with FileNotFoundError.
                    # Hand it to the OS opener instead (browser+URL fast-path).
                    opener = "open" if detect_platform() == "darwin" else "xdg-open"
                    subprocess.Popen([opener, value], shell=False)  # noqa: ASYNC220, S606
            return ToolResult(success=True, output=f"Gestartet: {app_name}")
        except FileNotFoundError:
            return ToolResult(
                success=False,
                output=None,
                error=f"Anwendung '{app_name}' nicht gefunden.",
            )
        except OSError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
