"""Cross-platform "open a file" / "reveal in folder" helpers (AD-5/AD-6 style).

Used by the Outputs view's native file actions (desktop-only). Each function is a
thin per-OS dispatch with a graceful no-op fallback when no display is present
(headless VPS), mirroring jarvis/plugins/tool/app_resolver.py. Import-cleanliness
(HN-7): only stdlib at module scope; no platform-only package imported here.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

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


# Standard install locations for mainstream browsers, relative to the Windows
# root dirs probed in _windows_browser_candidates. Edge ships on stock Win10/11,
# so in practice this list almost always yields a real browser exe to fall back to
# when the default association is broken/hijacked.
_WINDOWS_BROWSER_RELPATHS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"Mozilla Firefox\firefox.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
)


def _parse_exe_from_shell_command(command: str) -> str | None:
    r"""Pull the executable path out of a Windows registry ``shell\open\command``.

    The value looks like ``"C:\...\chrome.exe" --user-data-dir=... %1`` (usually
    quoted). We want only the exe and deliberately drop any ``--user-data-dir`` /
    ``--profile-directory`` override: a hijacked default handler can point those
    at an isolated profile that no longer launches (the real-world failure this
    guards against). Returns the exe path or None.
    """
    command = (command or "").strip()
    if not command:
        return None
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end != -1 else None
    # Unquoted: take everything up to and including the first ``.exe`` *token* (so
    # an unquoted path containing spaces is still captured), but only when ``.exe``
    # ends a path segment — i.e. the next char is a separator/quote/end. This
    # avoids mis-splitting a directory that itself contains ``.exe``. Else the
    # first whitespace token.
    lowered = command.lower()
    start = 0
    while True:
        idx = lowered.find(".exe", start)
        if idx == -1:
            break
        tail = lowered[idx + 4 : idx + 5]
        if tail in ("", " ", '"', "\t"):
            return command[: idx + 4]
        start = idx + 4
    parts = command.split()
    return parts[0] if parts else None


def _windows_default_browser_exe() -> str | None:
    r"""Resolve the user's chosen default https-handler down to its ``.exe`` path.

    Honors the browser CHOICE (Chrome/Edge/Firefox/…) while ignoring a dead or
    hijacked ``--user-data-dir`` profile override. Returns an existing exe path or
    None on any registry/parse miss. Never raises.
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\https\UserChoice",
        ) as key:
            progid, _ = winreg.QueryValueEx(key, "ProgId")
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command"
        ) as key:
            command, _ = winreg.QueryValueEx(key, "")  # "" = the (Default) value
    except OSError:
        return None
    exe = _parse_exe_from_shell_command(command)
    if exe and exe.lower().endswith(".exe") and Path(exe).exists():
        return exe
    return None


def _windows_browser_candidates() -> list[str]:
    r"""Ordered, existing browser exe paths: the user's default first, then the
    standard install locations of mainstream browsers (Chrome/Edge/Firefox/Brave).

    This is what lets the desktop shell ALWAYS reach a visible browser, even when
    the OS default association silently opens nothing — e.g. a tool repointed the
    default https handler at an isolated ``--user-data-dir`` profile that no longer
    starts, so every association-follow (rundll32/ShellExecute) is a no-op.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(path: str | None) -> None:
        if not path:
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            return
        if Path(path).exists():
            seen.add(norm)
            out.append(path)

    _add(_windows_default_browser_exe())
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for relpath in _WINDOWS_BROWSER_RELPATHS:
        for root in roots:
            if root:
                _add(str(Path(root) / relpath))
    return out


def _open_url_windows(url: str) -> bool:
    """Open *url* in a visible browser on Windows. See :func:`open_url`.

    Launches a real browser exe DIRECTLY (CreateProcess, URL as one argv — ``&``
    in OAuth URLs is safe), rather than following the OS file association via
    rundll32/ShellExecute. The association path is what silently fails when the
    default handler is hijacked to a dead profile; a direct exe launch lands the
    URL in the browser's normal, visible profile. Falls back across candidates and
    finally to ShellExecute. Returns True once a launcher is dispatched.
    """
    host = urlparse(url).netloc
    for exe in _windows_browser_candidates():
        try:
            subprocess.Popen(  # noqa: S603
                [exe, url], creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True
            )
            # Log only scheme://host at INFO — the full URL carries the OAuth
            # ``state`` (a CSRF token) and ephemeral redirect_uri; keep it at DEBUG.
            log.info("open_url: launched %s -> %s", exe, host)
            log.debug("open_url: full URL %s", url)
            return True
        except OSError as exc:
            log.warning("open_url: %s failed (%s); trying next candidate", exe, exc)
            continue
    # Last resort: hand the URL to the OS association. It may be a UWP handler with
    # no classic exe — but it is also the path that silently opens nothing for a
    # dead/hijacked association, so we cannot verify it actually opened a browser.
    try:
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
        log.warning(
            "open_url: no browser exe found; handed off to ShellExecute "
            "(cannot verify it opened) for %s", host
        )
        return True  # best-effort; ShellExecute may open nothing for a dead handler
    except OSError as exc:
        log.warning("open_url: ShellExecute fallback failed for %s: %s", host, exc)
        return False


# Browser fallbacks for when the OS default opener (open / xdg-open) silently
# fails — the same robustness the Windows path has. macOS: app display-names for
# ``open -a``. Linux: browser executables probed on PATH.
_MACOS_BROWSER_APPS = (
    "Google Chrome",
    "Safari",
    "Firefox",
    "Microsoft Edge",
    "Brave Browser",
)
_LINUX_BROWSER_BINS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "firefox",
    "brave-browser",
    "microsoft-edge",
    "x-www-browser",
    "sensible-browser",
)
# How long to wait for a quick-returning opener (open / xdg-open) before treating
# it as failed. These wrappers dispatch and return in well under a second.
_OPENER_TIMEOUT_S = 8


def _run_opener_checked(argv: list[str]) -> bool:
    """Run a quick-returning URL opener (``open`` / ``xdg-open`` / ``open -a``) and
    report success by its EXIT CODE. Unlike a fire-and-forget ``Popen`` — which
    cannot tell a real open from a silent no-op — these wrappers dispatch to the
    browser and return promptly, so a non-zero exit means no handler took the URL.
    Never raises.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            argv,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            close_fds=True,
            timeout=_OPENER_TIMEOUT_S,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("open_url: opener %r failed: %s", argv[0], exc)
        return False
    return proc.returncode == 0


def _open_url_macos(url: str) -> bool:
    """Open *url* on macOS: the default handler via ``open``, then explicit
    ``open -a <App>`` fallbacks if the default association is broken. See
    :func:`open_url`. Honest: returns False if nothing opened.
    """
    host = urlparse(url).netloc
    if _run_opener_checked(["open", url]):
        log.info("open_url: macOS launched default browser -> %s", host)
        log.debug("open_url: full URL %s", url)
        return True
    for app in _MACOS_BROWSER_APPS:
        if _run_opener_checked(["open", "-a", app, url]):
            log.info("open_url: macOS opened via %s -> %s", app, host)
            return True
    log.warning("open_url: macOS found no browser to open %s", host)
    return False


def _open_url_linux(url: str) -> bool:
    """Open *url* on Linux: the default handler via ``xdg-open``, then a real
    browser binary probed on PATH if that fails (broken/absent xdg associations on
    a minimal desktop). See :func:`open_url`. Honest: returns False if nothing
    opened. The direct-binary launch is fire-and-forget (the browser keeps running)
    so it mirrors the Windows direct launch — success means the process started.
    """
    host = urlparse(url).netloc
    if _run_opener_checked(["xdg-open", url]):
        log.info("open_url: Linux launched default browser -> %s", host)
        log.debug("open_url: full URL %s", url)
        return True
    for binname in _LINUX_BROWSER_BINS:
        exe = shutil.which(binname)
        if not exe:
            continue
        try:
            subprocess.Popen(  # noqa: S603
                [exe, url], creationflags=NO_WINDOW_CREATIONFLAGS, close_fds=True
            )
            log.info("open_url: Linux opened via %s -> %s", binname, host)
            return True
        except OSError as exc:
            log.warning("open_url: %s failed (%s); trying next", binname, exc)
            continue
    log.warning("open_url: Linux found no browser to open %s", host)
    return False


def open_url(url: str) -> bool:
    """Open *url* in the user's web browser. ``http``/``https`` only.

    The embedded desktop WebView2 cannot open external browser tabs (a
    ``window.open`` / ``target="_blank"`` is silently dropped), so the desktop
    shell routes OAuth-authorize and token-creation pages through this helper to
    reach the user's real browser. Mirrors :func:`open_file`: per-OS dispatch,
    gated on a present display, graceful False on a headless host / unsupported
    scheme / launch error. Never raises.

    On EVERY OS the dispatch is robust against a broken/hijacked default browser
    association: the platform opener is tried first (honoring the user's chosen
    browser), then a direct browser launch if that opener silently fails — Windows
    via a real exe (:func:`_open_url_windows`), macOS via ``open -a``
    (:func:`_open_url_macos`), Linux via a browser binary on PATH
    (:func:`_open_url_linux`). macOS/Linux detect the opener failure by its exit
    code; Windows bypasses the (unverifiable) association entirely.

    Scheme is validated to ``http``/``https`` so a hostile ``open_url`` payload
    (``file:``, ``javascript:``, an app protocol) can never be launched.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        log.warning("open_url: refusing non-http(s) scheme %r", parsed.scheme)
        return False
    if not detect_capabilities().display_present:
        log.info("open_url: no display present — skipping")
        return False
    plat = detect_platform()
    if plat == "win32":
        return _open_url_windows(url)
    if plat == "darwin":
        return _open_url_macos(url)
    return _open_url_linux(url)


__all__ = [
    "open_file",
    "open_file_with",
    "open_url",
    "reveal_in_folder",
]
