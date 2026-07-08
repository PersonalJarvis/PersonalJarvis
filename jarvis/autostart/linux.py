"""Linux login autostart via the freedesktop XDG autostart spec.

Writes ``$XDG_CONFIG_HOME/autostart/personal-jarvis.desktop`` (default
``~/.config/autostart/...``). Desktop environments (GNOME/KDE/XFCE/...) launch
every ``.desktop`` there at graphical login — which keeps Jarvis in the user's
session with microphone access. This is the desktop-login path chosen in
brainstorming; a systemd ``--user`` boot-without-login unit is intentionally not
built here (see the design spec, Non-Goals).

Pure ``pathlib`` text I/O — fully CI-provable on any OS (write into a temp HOME).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

_ENTRY_NAME = "personal-jarvis.desktop"
_APP_NAME = "Personal Jarvis"
# The X11/Wayland window-class token the running window is pinned to (see
# ``jarvis.ui.icon_utils.pin_linux_wm_class``). ``StartupWMClass`` must match it
# for the desktop to map the running window to THIS .desktop entry — and thus
# show its ``Icon=`` on the taskbar/dock instead of the generic python3 icon.
_WM_CLASS = "personal-jarvis"


def _icon_value() -> str | None:
    """Absolute path to the bundled PNG for the ``Icon=`` key, or ``None``.

    Linux desktops read the launcher/menu/taskbar icon from ``Icon=`` and mostly
    cannot decode a Windows ``.ico`` — so we ship and point at ``jarvis.png``.
    Resolved fresh from the installed package (so the baked absolute path is
    correct on any layout); a partial checkout without the PNG simply omits the
    key (the entry still works, just unbranded — never a crash).
    """
    try:
        from jarvis.assets import bundled_app_icon_png

        png = bundled_app_icon_png()
        return str(png) if png is not None else None
    except Exception as exc:  # noqa: BLE001 — a missing icon must never block autostart
        log.debug("Linux autostart icon could not be resolved: %s", exc)
        return None


def _autostart_dir() -> Path:
    """``$XDG_CONFIG_HOME/autostart`` or the ``~/.config/autostart`` default."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "autostart"


def _exec_value(spec: LaunchSpec) -> str:
    """Canonical ``Exec=`` value. Double-quote the program if it has spaces
    (Desktop Entry spec quoting). Args are fixed and space-free."""
    program = f'"{spec.program}"' if " " in spec.program else spec.program
    return " ".join([program, *spec.args])


def _render(spec: LaunchSpec) -> str:
    icon = _icon_value()
    icon_line = f"Icon={icon}\n" if icon else ""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_APP_NAME}\n"
        "Comment=Voice-driven meta-orchestrator (autostart)\n"
        f"Exec={_exec_value(spec)}\n"
        f"Path={spec.working_dir}\n"
        "Terminal=false\n"
        f"{icon_line}"
        f"StartupWMClass={_WM_CLASS}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Hidden=false\n"
    )


def _read_field(text: str, key: str) -> str | None:
    prefix = key + "="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


class LinuxAutostart:
    """XDG ``.desktop`` autostart manager."""

    def __init__(self) -> None:
        self._path = _autostart_dir() / _ENTRY_NAME

    def status(self, spec: LaunchSpec) -> AutostartStatus:
        if not self._path.exists():
            return AutostartStatus(
                supported=True,
                installed=False,
                matches_spec=False,
                entry_path=str(self._path),
                detail="No autostart entry yet.",
            )
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("Could not read %s: %s", self._path, exc)
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=False,
                entry_path=str(self._path),
                detail=f"Autostart entry present but unreadable: {exc}.",
            )
        matches = (
            _read_field(text, "Exec") == _exec_value(spec)
            and _read_field(text, "Path") == spec.working_dir
        )
        return AutostartStatus(
            supported=True,
            installed=True,
            matches_spec=matches,
            entry_path=str(self._path),
            detail=(
                "Autostart enabled and current."
                if matches
                else "Autostart entry points at a different install (will be refreshed)."
            ),
        )

    def install(  # noqa: ARG002 — per-user XDG .desktop never needs elevation
        self, spec: LaunchSpec, *, interactive: bool = False
    ) -> AutostartStatus:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish: write tempfile then replace, so a crash never leaves a
        # half-written .desktop the DE would choke on.
        tmp = self._path.with_suffix(".desktop.tmp")
        tmp.write_text(_render(spec), encoding="utf-8")
        tmp.replace(self._path)
        log.info("Linux autostart entry written: %s", self._path)
        return self.status(spec)

    def uninstall(self, *, interactive: bool = False) -> AutostartStatus:  # noqa: ARG002
        if self._path.exists():
            try:
                self._path.unlink()
                log.info("Linux autostart entry removed: %s", self._path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", self._path, exc)
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=str(self._path),
            detail="Autostart disabled.",
        )


__all__ = ["LinuxAutostart"]
