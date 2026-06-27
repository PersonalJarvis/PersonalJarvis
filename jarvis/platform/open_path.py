"""Cross-platform "open a file" / "reveal in folder" helpers (AD-5/AD-6 style).

Used by the Outputs view's native file actions (desktop-only). Each function is a
thin per-OS dispatch with a graceful no-op fallback when no display is present
(headless VPS), mirroring jarvis/plugins/tool/app_resolver.py. Import-cleanliness
(HN-7): only stdlib at module scope; no platform-only package imported here.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities

log = logging.getLogger(__name__)


def open_file(path: Path) -> bool:
    """Open *path* with the OS default application.

    Returns True if a launcher was invoked, False on a headless host (no display)
    or on a launch error. Never raises.
    """
    if not detect_capabilities().display_present:
        log.info("open_file: no display present — skipping %s", path)
        return False
    plat = detect_platform()
    try:
        if plat == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            return True
        path_str = path.as_posix()
        cmd = ["open", path_str] if plat == "darwin" else ["xdg-open", path_str]
        subprocess.Popen(  # noqa: S603
            cmd, creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True
        )
        return True
    except OSError as exc:
        log.warning("open_file failed for %s: %s", path, exc)
        return False


def reveal_in_folder(path: Path) -> bool:
    """Open the OS file manager with *path* selected/highlighted.

    Returns True if a launcher was invoked, False on a headless host. Never raises.
    On Linux there is no portable "select the file" verb, so the containing folder
    is opened. On Windows, ``explorer /select,`` returns a non-zero exit code even
    on success — spawning it is treated as success, the exit code is ignored.
    """
    if not detect_capabilities().display_present:
        log.info("reveal_in_folder: no display present — skipping %s", path)
        return False
    plat = detect_platform()
    try:
        if plat == "win32":
            subprocess.Popen(  # noqa: S603
                ["explorer", "/select,", str(path)],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        if plat == "darwin":
            subprocess.Popen(  # noqa: S603
                ["open", "-R", path.as_posix()],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        subprocess.Popen(  # noqa: S603
            ["xdg-open", path.parent.as_posix()],
            creationflags=NO_WINDOW_CREATIONFLAGS,
            close_fds=True,
        )
        return True
    except OSError as exc:
        log.warning("reveal_in_folder failed for %s: %s", path, exc)
        return False


def open_file_with(file: Path, launch_kind: str, launch_value: str) -> bool:
    """Open *file* in a specific, already-resolved app. Returns True if launched.

    ``launch_kind``/``launch_value`` come from ``resolve_app_launch_target`` in
    the caller (so this module stays free of the plugins layer): ``executable``
    + absolute exe, ``open_a`` + macOS app display-name, ``xdg_open`` (Linux
    default handler), ``startfile`` + a Windows ``.lnk``/app. The file path is
    always passed as the launch argument.

    Unlike ``os.startfile(bare_name)`` — a silent ShellExecute no-op from the
    pythonw background process — every branch starts a real ``subprocess`` so a
    window actually appears. Returns False on a headless host, an unknown kind,
    or a launch error. Never raises (mirrors :func:`open_file`).
    """
    if not detect_capabilities().display_present:
        log.info("open_file_with: no display present — skipping %s", file)
        return False
    try:
        if launch_kind == "executable":
            # Direct exe + the file as its argument (VSCode, Sublime, a browser
            # exe, …). CREATE_NO_WINDOW only suppresses a console, never the GUI.
            subprocess.Popen(  # noqa: S603
                [launch_value, str(file)],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        if launch_kind == "open_a":
            # macOS: `open -a <AppName> <file>` resolves the .app by name.
            subprocess.Popen(  # noqa: S603
                ["open", "-a", launch_value, file.as_posix()],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        if launch_kind == "xdg_open":
            # Linux fallback: hand the file to the desktop's default handler.
            subprocess.Popen(  # noqa: S603
                ["xdg-open", file.as_posix()],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        if launch_kind == "startfile":
            # Windows .lnk/app launched with the file as an argument via `start`.
            subprocess.Popen(  # noqa: S603
                ["cmd", "/c", "start", "", launch_value, str(file)],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
            return True
        log.warning("open_file_with: unknown launch_kind %r", launch_kind)
        return False
    except OSError as exc:
        log.warning("open_file_with failed for %s (%s): %s", file, launch_value, exc)
        return False


def open_url(url: str) -> bool:
    """Open *url* in the OS default web browser. ``http``/``https`` only.

    The embedded desktop WebView2 cannot open external browser tabs (a
    ``window.open`` / ``target="_blank"`` is silently dropped), so the desktop
    shell routes OAuth-authorize and token-creation pages through this helper to
    reach the user's real default browser. Mirrors :func:`open_file`: per-OS
    dispatch, gated on a present display, graceful False on a headless host /
    unsupported scheme / launch error. Never raises.

    Scheme is validated to ``http``/``https`` so a hostile ``open_url`` payload
    (``file:``, ``javascript:``, an app protocol) can never be launched.
    """
    from urllib.parse import urlparse

    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        log.warning("open_url: refusing non-http(s) scheme %r", scheme)
        return False
    if not detect_capabilities().display_present:
        log.info("open_url: no display present — skipping")
        return False
    plat = detect_platform()
    try:
        if plat == "win32":
            # A REAL subprocess, NOT os.startfile. ShellExecute from the pythonw
            # background process (the desktop tray app) can be a silent no-op —
            # the same trap open_file_with documents — so a returned "success"
            # may open no browser at all. rundll32 FileProtocolHandler launches
            # the default browser as a real process and takes the URL as a single
            # argv (CreateProcess, no shell), so '&' in OAuth URLs is safe.
            subprocess.Popen(  # noqa: S603
                ["rundll32.exe", "url.dll,FileProtocolHandler", url],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
        else:
            cmd = ["open", url] if plat == "darwin" else ["xdg-open", url]
            subprocess.Popen(  # noqa: S603
                cmd, creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True
            )
        log.info("open_url: launched default browser for %s", url)
        return True
    except OSError as exc:
        log.warning("open_url failed for %s: %s", url, exc)
        return False


__all__ = [
    "open_file",
    "open_file_with",
    "open_url",
    "reveal_in_folder",
]
