"""Linux login autostart via the freedesktop XDG autostart spec.

Writes ``$XDG_CONFIG_HOME/autostart/personal-jarvis.desktop`` (default
``~/.config/autostart/...``). Desktop environments (GNOME/KDE/XFCE/...) launch
every ``.desktop`` there at graphical login — which keeps Jarvis in the user's
session with microphone access. This is the desktop-login path chosen in
brainstorming; a systemd ``--user`` boot-without-login unit is intentionally not
built here (see the design spec, Non-Goals).

Pure ``pathlib`` text I/O — fully CI-provable on any OS (write into a temp HOME).

All ``.desktop`` text is encoded through :mod:`jarvis.core.desktop_entry` and
read back through its group-aware parser, so writing and drift-checking can never
disagree about escaping. That module's docstring explains why the format needs
two nested escaping layers; the failure mode it prevents is specific to this file
and invisible from both sides — the desktop discards a malformed entry without a
word, and a writer comparing its own unescaped string still reports the dead
entry as installed and current.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from jarvis.core.branding import (
    LINUX_APP_NAME as _APP_NAME,
)
from jarvis.core.branding import (
    LINUX_DESKTOP_ENTRY_FILE_NAME as _ENTRY_NAME,
)
from jarvis.core.branding import (
    LINUX_WM_CLASS as _WM_CLASS,
)
from jarvis.core.desktop_entry import (
    escape_value,
    exec_value,
    read_field,
    reads_as_false,
    reads_as_true,
    unescape_value,
)

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

# XDG basedir spec: a directory created for the user's config must not be
# world-readable. The autostart entry itself carries no secrets, but the
# directory is shared with everything else under $XDG_CONFIG_HOME.
_XDG_DIR_MODE = 0o700

# The X11/Wayland window-class token the running window is pinned to (see
# ``jarvis.ui.icon_utils.pin_linux_wm_class``). ``StartupWMClass`` must match it
# for the desktop to map the running window to THIS .desktop entry — and thus
# show its ``Icon=`` on the taskbar/dock instead of the generic python3 icon.


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
    """``$XDG_CONFIG_HOME/autostart`` or the ``~/.config/autostart`` default.

    A NON-ABSOLUTE ``XDG_CONFIG_HOME`` is ignored, as the XDG base-directory
    spec requires ("if an implementation encounters a relative path in any of
    these variables it should consider the path invalid and ignore it"). It is
    not a theoretical case: a dotfile that exports the value in quotes
    (``XDG_CONFIG_HOME="~/.config"``) leaves the tilde unexpanded, and the entry
    then landed in a ``~/.config``-named folder under the process' working
    directory — a path no desktop environment ever reads, so autostart was dead
    while ``install()`` reported success.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base: Path | None = None
    if xdg:
        candidate = Path(xdg)
        if candidate.is_absolute():
            base = candidate
        else:
            log.debug(
                "Ignoring relative XDG_CONFIG_HOME=%r (XDG spec) — using ~/.config",
                xdg,
            )
    if base is None:
        base = Path.home() / ".config"
    return base / "autostart"


def _exec_value(spec: LaunchSpec) -> str:
    """Canonical, spec-escaped ``Exec=`` value.

    Delegates to :mod:`jarvis.core.desktop_entry`, which implements both nested
    escaping layers (argument quoting + ``%%`` field-code doubling + the
    key-file string encoding). The previous "quote the program if it contains a
    space" shortcut produced an entry the desktop silently discards for any
    install path containing a ``%``, while ``status()`` kept reporting it as
    installed and current because it compared the same unescaped string it had
    written.
    """
    return exec_value(spec.program, spec.args)


def _render(spec: LaunchSpec) -> str:
    icon = _icon_value()
    icon_line = f"Icon={escape_value(icon)}\n" if icon else ""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={escape_value(_APP_NAME)}\n"
        "Comment=Voice-driven meta-orchestrator (autostart)\n"
        f"Exec={_exec_value(spec)}\n"
        f"Path={escape_value(spec.working_dir)}\n"
        "Terminal=false\n"
        f"{icon_line}"
        f"StartupWMClass={escape_value(_WM_CLASS)}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Hidden=false\n"
    )


def _disabled_by_desktop(text: str) -> bool:
    """Has the desktop switched this autostart entry off behind our back?

    Two independent off-switches exist and both leave the file in place:
    ``Hidden=true`` is the spec's own "the user deleted this entry" marker, and
    ``X-GNOME-Autostart-enabled=false`` is what GNOME's Startup Applications /
    Tweaks writes. Ignoring them meant a switched-off entry reported
    ``installed=True, matches_spec=True`` — the reconcile loop no-op'd, the
    Settings toggle showed green, and Jarvis never started at login with nothing
    anywhere saying why.
    """
    return reads_as_true(read_field(text, "Hidden")) or reads_as_false(
        read_field(text, "X-GNOME-Autostart-enabled")
    )


class LinuxAutostart:
    """XDG ``.desktop`` autostart manager."""

    def __init__(self) -> None:
        self._path = _autostart_dir() / _ENTRY_NAME

    @property
    def _tmp_path(self) -> Path:
        """Staging path for the atomic write.

        A sibling with a ``.tmp`` tail, so the desktop's autostart scan (which
        only considers ``*.desktop``) never picks up a partial file. Derived by
        name rather than ``with_suffix`` so an entry file name containing dots
        cannot silently truncate it.
        """
        return self._path.with_name(self._path.name + ".tmp")

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
        # Exec is compared in its ENCODED form (both sides come from
        # ``_exec_value``); Path is compared decoded, so an entry another tool
        # re-encoded differently still recognises its own install.
        points_here = (
            read_field(text, "Exec") == _exec_value(spec)
            and unescape_value(read_field(text, "Path") or "") == spec.working_dir
        )
        switched_off = _disabled_by_desktop(text)
        matches = points_here and not switched_off
        if matches:
            detail = "Autostart enabled and current."
        elif switched_off:
            detail = (
                "Autostart entry is present but switched off in the desktop's "
                "startup-application settings (will be re-enabled)."
            )
        else:
            detail = "Autostart entry points at a different install (will be refreshed)."
        return AutostartStatus(
            supported=True,
            installed=True,
            matches_spec=matches,
            entry_path=str(self._path),
            detail=detail,
        )

    def install(  # noqa: ARG002 — per-user XDG .desktop never needs elevation
        self, spec: LaunchSpec, *, interactive: bool = False
    ) -> AutostartStatus:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=_XDG_DIR_MODE)
        # Atomic-ish: write tempfile then replace, so a crash never leaves a
        # half-written .desktop the DE would choke on.
        tmp = self._tmp_path
        try:
            tmp.write_text(_render(spec), encoding="utf-8")
            tmp.replace(self._path)
        except BaseException:
            # A half-written .desktop.tmp left in ~/.config/autostart is debris:
            # the successful replace() consumes the temp file, so anything still
            # there is from a failed write (a read-only home, a full disk).
            # Mirrors the macOS LaunchAgent writer.
            tmp.unlink(missing_ok=True)
            raise
        log.info("Linux autostart entry written: %s", self._path)
        return self.status(spec)

    def uninstall(self, *, interactive: bool = False) -> AutostartStatus:  # noqa: ARG002
        # Clean up debris from an earlier failed install() first — it is never
        # load-bearing, and a failure to remove it must not mask the real result.
        try:
            self._tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Could not remove %s: %s", self._tmp_path, exc)

        error = ""
        if self._path.exists():
            try:
                self._path.unlink()
                log.info("Linux autostart entry removed: %s", self._path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", self._path, exc)
                error = str(exc)
        if error:
            # Reporting "Autostart disabled." while the .desktop survives was a
            # lie: the desktop environment still launches Jarvis at the next
            # login, and the Settings toggle showed off with nothing to explain
            # the mismatch (AP-30). Same fix the macOS LaunchAgent already got.
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=False,
                entry_path=str(self._path),
                detail=(
                    f"The autostart entry could not be removed ({error}) — Jarvis "
                    "may still start at login; delete the file manually and retry."
                ),
            )
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=str(self._path),
            detail="Autostart disabled.",
        )


__all__ = ["LinuxAutostart"]
