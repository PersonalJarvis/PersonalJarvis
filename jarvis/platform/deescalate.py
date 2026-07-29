"""Start a child process WITHOUT the elevation this process is carrying.

Why this exists: an app that was ever launched elevated stays elevated across
every in-app restart, and while elevated it is unreachable for dictation apps,
text expanders, and password-manager auto-type (see
``jarvis/platform/input_isolation.py`` for the measurement). Recovering has to
happen from inside the app — telling a user to hunt down how their app came to
be elevated is not a fix.

**How.** We need a *primary* token that represents "an ordinary app of this
user", then launch through ``CreateProcessWithTokenW`` (which needs
``SeImpersonatePrivilege`` — an elevated process holds it). Two sources are
tried in order:

1. **The desktop shell's token.** Explorer always runs as the plain interactive
   user, and an elevated process may open it because access flows downward. This
   is the primary path and the only one that also works with UAC switched off.
2. **Our own token's filtered companion** (``TokenLinkedToken``). Kept as a
   fallback, but it is *not* reliable: unless the caller holds
   ``SeTcbPrivilege``, Windows hands that token out at *identification* level,
   and a primary token cannot be duplicated from one — measured live as error
   1346, ``ERROR_BAD_IMPERSONATION_LEVEL``.

**When it cannot work** (reported honestly, never faked): a session with no
shell and no usable companion token — a Windows service, a SYSTEM context, or a
headless host. POSIX is a documented no-op: dropping from root to "whoever ran
sudo" is guesswork that would strand file ownership, so we tell the user instead
of guessing.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: ``CreateProcessWithTokenW`` logon flag: load the target user's profile.
_LOGON_WITH_PROFILE = 0x00000001
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_DETACHED_PROCESS = 0x00000008
_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_ALL_ACCESS = 0xF01FF
_PROCESS_QUERY_INFORMATION = 0x0400
_TokenLinkedToken = 19
_SecurityImpersonation = 2
_TokenPrimary = 1


@dataclass(frozen=True)
class DeescalationResult:
    """Outcome of an unelevated spawn attempt."""

    ok: bool
    pid: int | None
    detail: str


def environment_block(env: dict[str, str]) -> str:
    """Windows ``CREATE_UNICODE_ENVIRONMENT`` block for ``env``.

    Format: ``NAME=VALUE\\0`` repeated, terminated by one extra ``\\0``. Windows
    expects the names sorted case-insensitively; an unsorted block is accepted by
    most APIs but is documented as undefined, and this runs on the restart path
    where a subtle failure is expensive to diagnose.

    Kept pure and module-level so the format is unit-testable off-Windows.
    """
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    return "".join(f"{name}={value}\0" for name, value in items) + "\0"


def token_creationflags(creationflags: int) -> int:
    """Return flags accepted by ``CreateProcessWithTokenW``.

    That API enables ``CREATE_NEW_CONSOLE`` by default, while Windows forbids
    combining it with ``DETACHED_PROCESS``. The desktop relauncher normally
    requests ``DETACHED_PROCESS | CREATE_NO_WINDOW``; passing that combination
    through produced ``ERROR_INVALID_PARAMETER`` (87). ``CREATE_NO_WINDOW`` is
    sufficient for this helper, and Windows does not tie a child process's
    lifetime to its parent merely because this flag is absent.
    """
    return (creationflags & ~_DETACHED_PROCESS) | _CREATE_UNICODE_ENVIRONMENT


def _spawn_unelevated_windows(
    argv: list[str], *, cwd: str, env: dict[str, str], creationflags: int
) -> DeescalationResult:
    import ctypes  # noqa: PLC0415 — lazy so this module imports on every OS
    import subprocess  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    advapi32.CreateProcessWithTokenW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessWithTokenW.restype = wintypes.BOOL

    # Undeclared ctypes prototypes default to a 32-bit int, which truncates
    # every 64-bit HANDLE passed or returned here. Skipping these turns the
    # whole de-escalation into a silent "could not open token" (the same trap
    # that made the elevation probe report "unknown" on every host).
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.DuplicateTokenEx.restype = wintypes.BOOL

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetShellWindow.argtypes = []
    user32.GetShellWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    def _shell_token() -> tuple[wintypes.HANDLE | None, str]:
        """A primary token borrowed from the desktop shell (Explorer).

        Explorer always runs as the ordinary interactive user, and an elevated
        process may open it (access flows downward), so its token is the most
        reliable source of "what a normally-started app looks like". Also the
        only one that works with UAC turned off, where no linked token exists.
        """
        hwnd = user32.GetShellWindow()
        if not hwnd:
            return None, "no desktop shell window (headless or Explorer not running)"
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None, "could not identify the shell process"
        proc = kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION, False, pid.value)
        if not proc:
            return None, f"OpenProcess(shell) failed ({ctypes.get_last_error()})"
        try:
            shell_tok = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                proc, _TOKEN_DUPLICATE | _TOKEN_QUERY, ctypes.byref(shell_tok)
            ):
                return None, f"OpenProcessToken(shell) failed ({ctypes.get_last_error()})"
            try:
                dup = wintypes.HANDLE()
                if not advapi32.DuplicateTokenEx(
                    shell_tok,
                    _TOKEN_ALL_ACCESS,
                    None,
                    _SecurityImpersonation,
                    _TokenPrimary,
                    ctypes.byref(dup),
                ):
                    return None, f"DuplicateTokenEx(shell) failed ({ctypes.get_last_error()})"
                return dup, "shell token"
            finally:
                kernel32.CloseHandle(shell_tok)
        finally:
            kernel32.CloseHandle(proc)

    def _linked_token() -> tuple[wintypes.HANDLE | None, str]:
        """The filtered companion of our own elevated token.

        Fallback only: Windows hands `TokenLinkedToken` out at *identification*
        level unless the caller holds SeTcbPrivilege, and a primary token cannot
        be duplicated from that — the live failure was error 1346,
        ERROR_BAD_IMPERSONATION_LEVEL. It still succeeds in some configurations,
        so it stays as a second chance rather than being deleted.
        """
        own = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            _TOKEN_QUERY | _TOKEN_DUPLICATE,
            ctypes.byref(own),
        ):
            return None, f"OpenProcessToken(self) failed ({ctypes.get_last_error()})"
        try:
            linked = wintypes.HANDLE()
            returned = wintypes.DWORD()
            if not advapi32.GetTokenInformation(
                own,
                _TokenLinkedToken,
                ctypes.cast(ctypes.byref(linked), wintypes.LPVOID),
                ctypes.sizeof(linked),
                ctypes.byref(returned),
            ):
                return None, f"no linked token ({ctypes.get_last_error()})"
            try:
                dup = wintypes.HANDLE()
                if not advapi32.DuplicateTokenEx(
                    linked,
                    _TOKEN_ALL_ACCESS | _TOKEN_ASSIGN_PRIMARY,
                    None,
                    _SecurityImpersonation,
                    _TokenPrimary,
                    ctypes.byref(dup),
                ):
                    return None, f"DuplicateTokenEx(linked) failed ({ctypes.get_last_error()})"
                return dup, "linked token"
            finally:
                kernel32.CloseHandle(linked)
        finally:
            kernel32.CloseHandle(own)

    primary = None
    source = ""
    failures: list[str] = []
    for candidate in (_shell_token, _linked_token):
        handle, detail = candidate()
        if handle:
            primary, source = handle, detail
            break
        failures.append(detail)

    if primary is None:
        return DeescalationResult(
            False,
            None,
            "Could not obtain an unelevated token on this Windows account "
            f"({'; '.join(failures)}), so the app cannot drop its administrator "
            "rights by itself.",
        )

    try:
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(STARTUPINFOW)
        info = PROCESS_INFORMATION()
        block = ctypes.create_unicode_buffer(environment_block(env))
        # CreateProcessWithTokenW writes into lpCommandLine, so it must be a
        # mutable buffer, and the applicationName stays NULL so the quoted argv
        # is parsed the usual way.
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))

        ok = advapi32.CreateProcessWithTokenW(
            primary,
            _LOGON_WITH_PROFILE,
            None,
            cmdline,
            token_creationflags(creationflags),
            ctypes.cast(block, wintypes.LPVOID),
            cwd,
            ctypes.byref(startup),
            ctypes.byref(info),
        )
        if not ok:
            return DeescalationResult(
                False,
                None,
                f"CreateProcessWithTokenW failed ({ctypes.get_last_error()})",
            )
        kernel32.CloseHandle(info.hProcess)
        kernel32.CloseHandle(info.hThread)
        return DeescalationResult(
            True,
            int(info.dwProcessId),
            f"Started without administrator rights (via the {source}).",
        )
    finally:
        kernel32.CloseHandle(primary)


def spawn_unelevated(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    creationflags: int = 0,
    _platform: str | None = None,
    _spawn=_spawn_unelevated_windows,
) -> DeescalationResult:
    """Launch ``argv`` stripped of this process's elevation.

    Returns an honest failure rather than silently falling back to an elevated
    spawn: a caller that quietly relaunches elevated would leave the user with a
    "repaired" app that still ignores their dictation software.
    """
    platform = sys.platform if _platform is None else _platform
    if platform != "win32":
        return DeescalationResult(
            False,
            None,
            "Dropping privileges is only supported on Windows; on this system "
            "start the app as your normal user account instead.",
        )
    try:
        return _spawn(argv, cwd=cwd, env=env, creationflags=creationflags)
    except Exception as exc:  # noqa: BLE001 — a failed repair must not crash the app
        log.warning("Unelevated relaunch failed", exc_info=True)
        return DeescalationResult(False, None, f"{type(exc).__name__}: {exc}")


def current_process_is_elevated() -> bool | None:
    """Convenience re-export so callers need only one import on this path."""
    from .input_isolation import windows_process_is_elevated  # noqa: PLC0415

    if os.name != "nt":
        return None
    return windows_process_is_elevated()


#: Set on the child so a relaunch that comes back elevated anyway stops here
#: instead of spawning forever. It can come back elevated legitimately — an
#: account whose shell itself runs elevated hands out an elevated token — and a
#: boot loop is far worse than the isolation this repairs.
DEESCALATION_ATTEMPTED_ENV = "JARVIS_DEESCALATION_ATTEMPTED"

#: Opt-out for someone who deliberately runs elevated and accepts the trade.
KEEP_ELEVATION_ENV = "JARVIS_KEEP_ELEVATION"


def maybe_relaunch_unelevated(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    creationflags: int = 0,
    _elevated=current_process_is_elevated,
    _spawn=spawn_unelevated,
) -> DeescalationResult | None:
    """Hand this boot over to an unelevated copy of ourselves, before it costs.

    ``None`` means "carry on booting in this process" and is the answer for
    every ordinary launch: not Windows, not elevated, already tried, or opted
    out. A :class:`DeescalationResult` means a decision was reached — ``ok``
    for "the replacement is starting, exit now", and a failure otherwise, which
    the caller reports and then keeps booting elevated.

    **Why this belongs at the very start of a launch.** The app already knows
    how to escape elevation (:meth:`DesktopApp.request_unelevated_restart`), but
    that path only opens once there is a window to restart — so an elevated
    launch first pays for a COMPLETE boot and throws it away. Measured
    2026-07-29 on the maintainer's box: 102 s of boot discarded, then 18 s to
    come back, for a start the user experienced as several minutes of nothing.
    Deciding here costs one token probe.

    The in-app path stays exactly as it was: it is the recovery for an app
    already running elevated (a failed relaunch here, or an install that starts
    elevated by other means), and the banner it drives is what makes the
    condition visible at all.
    """
    if sys.platform != "win32":
        return None
    if os.environ.get(KEEP_ELEVATION_ENV) == "1":
        return None
    if os.environ.get(DEESCALATION_ATTEMPTED_ENV) == "1":
        return None
    # Only a positive measurement acts. An unreadable token is "unknown", and
    # relaunching on a guess would strand a user whose app is perfectly fine.
    if _elevated() is not True:
        return None

    child_env = dict(os.environ if env is None else env)
    child_env[DEESCALATION_ATTEMPTED_ENV] = "1"
    return _spawn(argv, cwd=cwd, env=child_env, creationflags=creationflags)


__all__ = [
    "DEESCALATION_ATTEMPTED_ENV",
    "KEEP_ELEVATION_ENV",
    "DeescalationResult",
    "current_process_is_elevated",
    "environment_block",
    "maybe_relaunch_unelevated",
    "spawn_unelevated",
    "token_creationflags",
]
