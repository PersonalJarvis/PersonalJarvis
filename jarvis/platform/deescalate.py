"""Start a child process WITHOUT the elevation this process is carrying.

Why this exists: an app that was ever launched elevated stays elevated across
every in-app restart, and while elevated it is unreachable for dictation apps,
text expanders, and password-manager auto-type (see
``jarvis/platform/input_isolation.py`` for the measurement). Recovering has to
happen from inside the app — telling a user to hunt down how their app came to
be elevated is not a fix.

**How.** When UAC filters an administrator account, Windows issues *two* tokens
at logon: the full one the elevated process runs on, and a filtered
medium-integrity one hanging off it as ``TokenLinkedToken``. Duplicating that
linked token and launching through ``CreateProcessWithTokenW`` produces a child
at exactly the integrity level of any normally-started app — no Explorer COM
detour and no third-party dependency. ``CreateProcessWithTokenW`` needs
``SeImpersonatePrivilege``, which an elevated process holds.

**When it cannot work** (reported honestly, never faked): UAC disabled, the true
built-in Administrator account, or a Windows-service/SYSTEM context — there is
no filtered companion token to fall back to. POSIX hosts are a documented no-op:
dropping from root to "whoever ran sudo" is guesswork that would strand file
ownership, so we tell the user instead of guessing.
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
_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_ALL_ACCESS = 0xF01FF
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

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        _TOKEN_QUERY | _TOKEN_DUPLICATE,
        ctypes.byref(token),
    ):
        return DeescalationResult(
            False, None, f"OpenProcessToken failed ({ctypes.get_last_error()})"
        )

    linked = wintypes.HANDLE()
    primary = wintypes.HANDLE()
    try:
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            _TokenLinkedToken,
            ctypes.byref(linked),
            ctypes.sizeof(linked),
            ctypes.byref(returned),
        ):
            # No filtered companion token: UAC off, the built-in Administrator,
            # or a SYSTEM context. Nothing to de-escalate to.
            return DeescalationResult(
                False,
                None,
                "This Windows account has no unelevated companion token "
                f"(error {ctypes.get_last_error()}), so the app cannot drop its "
                "administrator rights by itself.",
            )

        if not advapi32.DuplicateTokenEx(
            linked,
            _TOKEN_ALL_ACCESS | _TOKEN_ASSIGN_PRIMARY,
            None,
            _SecurityImpersonation,
            _TokenPrimary,
            ctypes.byref(primary),
        ):
            return DeescalationResult(
                False, None, f"DuplicateTokenEx failed ({ctypes.get_last_error()})"
            )

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
            creationflags | _CREATE_UNICODE_ENVIRONMENT,
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
            True, int(info.dwProcessId), "Started without administrator rights."
        )
    finally:
        for handle in (linked, primary, token):
            if handle:
                kernel32.CloseHandle(handle)


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


__all__ = [
    "DeescalationResult",
    "current_process_is_elevated",
    "environment_block",
    "spawn_unelevated",
]
