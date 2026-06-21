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


__all__ = [
    "open_file",
    "open_file_with",
    "reveal_in_folder",
]
