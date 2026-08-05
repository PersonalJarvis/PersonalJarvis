"""The app's colour theme, as the NATIVE layer needs it.

The web UI resolves its own theme from CSS custom properties
(``frontend/src/index.css``). This module exists for the surfaces that are
painted before — or entirely outside — that stylesheet:

* the pywebview window background, drawn while the web view is still empty
  (``jarvis/ui/shell/window.py``, ``jarvis/ui/desktop_app.py``);
* the "backend is still starting" holding page served by the web server
  (``jarvis/ui/web/server.py``), which has no bundle to fall back on.

Both used to hardcode ``#0a0e14``, which meant a user on the light theme spent
the whole boot looking at a black frame around a paper-white app. The two
grounds here are kept in lockstep with the boot splash in ``frontend/index.html``
and with ``--background`` in ``index.css``.

``system`` is resolved per OS behind a capability probe. Every probe is allowed
to fail: an unreadable registry, a missing ``defaults`` binary, a headless box
with no desktop session at all — all of them degrade to the product default
(dark) rather than raising, because a colour is never worth failing a boot over.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from typing import Literal

log = logging.getLogger(__name__)

Theme = Literal["dark", "light"]

#: What the user may choose. ``system`` is an intent, not a colour — it is
#: resolved to one of the two concrete themes by :func:`resolve_theme`.
THEME_CHOICES: tuple[str, ...] = ("dark", "light", "system")

#: Window/holding-page ground per theme. Dark is the deep slate the app shell
#: sits on; light is the warm paper of ``--background``.
WINDOW_BACKGROUND: dict[str, str] = {
    "dark": "#0a0e14",
    "light": "#fcfbf8",
}

#: Text colours for the holding page, so it is legible on either ground.
HOLDING_PAGE_FOREGROUND: dict[str, str] = {
    "dark": "#e6e6e6",
    "light": "#2b2b33",
}

HOLDING_PAGE_MUTED: dict[str, str] = {
    "dark": "#9aa3ad",
    "light": "#6b6b76",
}

_DEFAULT_THEME: Theme = "dark"


def detect_os_theme() -> Theme | None:
    """The desktop's own light/dark preference, or ``None`` if unknowable.

    ``None`` is a real answer, not an error: a headless Linux server, a Wayland
    session with no portal, and a locked-down Windows profile genuinely have no
    appearance preference to read, and the caller should fall back rather than
    guess.
    """
    try:
        if sys.platform == "win32":
            return _detect_windows()
        if sys.platform == "darwin":
            return _detect_macos()
        return _detect_linux()
    except Exception as exc:  # noqa: BLE001 — a colour probe never breaks a boot
        log.debug("OS theme probe failed (%s); caller falls back", exc)
        return None


def _detect_windows() -> Theme | None:
    """Windows 10/11: ``AppsUseLightTheme`` under the Personalize key."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        # Silent on purpose: no winreg means this is not Windows, which is the
        # expected answer on macOS and Linux, not a fault worth logging.
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError as exc:
        # Key absent on older/stripped builds — no preference to read.
        log.debug("Windows theme key unreadable: %s", exc)
        return None
    return "light" if int(value) == 1 else "dark"


def _detect_macos() -> Theme | None:
    """macOS: ``AppleInterfaceStyle`` is set to "Dark" ONLY in dark mode.

    In light mode the key does not exist and ``defaults`` exits non-zero — that
    absence is the light answer, not a failure.
    """
    out = _run(["defaults", "read", "-g", "AppleInterfaceStyle"])
    if out is None:
        return None
    return "dark" if "dark" in out.strip().lower() else "light"


def _detect_linux() -> Theme | None:
    """Linux: the freedesktop ``color-scheme`` hint, GNOME's key, then theme name.

    Tried in order of how much they actually mean. A server with no session has
    none of them and returns ``None``.
    """
    scheme = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    if scheme:
        low = scheme.strip().strip("'\"").lower()
        if "dark" in low:
            return "dark"
        if "light" in low:
            return "light"
        # 'default' means "no preference expressed" — keep looking.

    name = _run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"])
    if name:
        return "dark" if "dark" in name.strip().strip("'\"").lower() else "light"
    return None


def _run(cmd: list[str]) -> str | None:
    """Run a probe command, or ``None`` if it is missing / fails / hangs.

    ``NO_WINDOW_CREATIONFLAGS`` keeps this from flashing a console window under
    ``pythonw.exe`` (AP-1); the timeout keeps a wedged desktop service from
    holding up startup.
    """
    try:
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
    except Exception:  # noqa: BLE001 — probe must work even standalone
        NO_WINDOW_CREATIONFLAGS = 0  # noqa: N806
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("theme probe %s unavailable: %s", cmd[0], exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def resolve_theme(configured: str | None) -> Theme:
    """Turn a configured value (``dark`` | ``light`` | ``system``) into a colour.

    Anything unrecognised — including ``None`` and a value from a newer config
    written by a future build — resolves to the product default.
    """
    value = (configured or "").strip().lower()
    if value in ("dark", "light"):
        return value  # type: ignore[return-value]
    if value == "system":
        return detect_os_theme() or _DEFAULT_THEME
    return _DEFAULT_THEME


def window_background(configured: str | None) -> str:
    """The native window ground for a configured theme value."""
    return WINDOW_BACKGROUND[resolve_theme(configured)]


def configured_theme(cfg: object | None) -> str:
    """Read ``[ui] theme`` off a config object without assuming it is present.

    Tolerates an older config model and a ``None`` config, both of which happen
    on the degraded boot paths that call this.
    """
    return str(getattr(getattr(cfg, "ui", None), "theme", "dark") or "dark")
