"""Launches the UAC-elevated admin helper from the parent process.

Windows UAC: the elevation prompt appears on the Secure Desktop and cannot be
suppressed or programmatically confirmed by the parent. The launcher can only
start the process and determine the outcome (accepted / rejected) via a
handle check.

Cross-platform note (Wave 3, AD-7): the ``ShellExecuteW("runas", ...)`` glue
below is the *Windows* elevation mechanism. It is now driven through
:class:`jarvis.admin.elevator.UacElevator` (the ``Elevator`` seam) rather than
being called directly — ``UacElevator.ensure_elevated_helper`` invokes
:func:`launch_elevated_helper`. The Windows behavior is unchanged (AD-7); the
POSIX siblings (polkit / sudo / osascript / Null) live in ``elevator.py``.
``ensure_admin_secret`` stays transport-agnostic (it only touches the keyring).

Steps:
1. Secret preparation: if no ``jarvis_admin_hmac`` is in the Credential Manager,
   generate 32 random bytes and store them base64-encoded.
2. ``ShellExecuteW(runas, python.exe, "-m jarvis.admin.helper --pipe-name ...")``.
3. Poll the pipe until ``WaitNamedPipe`` succeeds (max 10s).
4. Return a ``subprocess.Popen``-like handle to the caller — in our case the
   OS process handle that ``ShellExecuteEx`` provides, wrapped in a small
   status object.
"""
from __future__ import annotations

import base64
import secrets as _secrets
import sys
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jarvis.core.config import get_secret, set_secret

from .client import ADMIN_HMAC_ENV, ADMIN_HMAC_KEY
from .transport import default_pipe_name

_DEFAULT_SECRET_BYTES = 32
_POLL_INTERVAL_MS = 200
_DEFAULT_PIPE_TIMEOUT_MS = 10_000


# ---------------------------------------------------------------------
# Secret-Handling
# ---------------------------------------------------------------------

def ensure_admin_secret(*, force: bool = False) -> str:
    """Ensures that ``jarvis_admin_hmac`` is present in the Credential Manager.

    Returns the base64-URL-safe-encoded string. If ``force=True``, a new key
    is generated even if one already exists (key rotation).
    """
    if not force:
        existing = get_secret(ADMIN_HMAC_KEY, env_fallback=ADMIN_HMAC_ENV)
        if existing:
            return existing
    raw = _secrets.token_bytes(_DEFAULT_SECRET_BYTES)
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    if not set_secret(ADMIN_HMAC_KEY, encoded):
        # Keyring write error — fallback is logged only; the caller still
        # receives the encoded string and can set it via ENV.
        logger.warning(
            "admin_launcher.keyring_write_failed",
            key=ADMIN_HMAC_KEY,
            hint="Helper can only start via the ENV fallback.",
        )
    return encoded


# ---------------------------------------------------------------------
# ShellExecute-Wrapper
# ---------------------------------------------------------------------

@dataclass
class ElevatedHelperHandle:
    """Lightweight wrapper for the started helper process.

    Not returned as ``subprocess.Popen`` because ``ShellExecute`` does not
    provide a Popen object — only a Windows handle plus an optional PID.
    """
    pipe_name: str
    process_handle: Any          # pywintypes.HANDLE, None on non-Windows
    pid: int | None = None


class UACCancelledError(RuntimeError):
    """The user declined the UAC prompt (ShellExecuteEx returned an error)."""


class HelperStartTimeoutError(RuntimeError):
    """The helper process started, but the pipe did not become reachable
    within the timeout."""


def _build_helper_argv() -> tuple[str, str]:
    """Returns (python_exe, command_line) for starting the helper.

    We use the same Python interpreter executable as the parent so that the
    helper is guaranteed to see the same packages.
    """
    python_exe = sys.executable
    module_args = "-m jarvis.admin.helper"
    return python_exe, module_args


def launch_elevated_helper(
    pipe_name: str | None = None,
    *,
    pipe_timeout_ms: int = _DEFAULT_PIPE_TIMEOUT_MS,
) -> ElevatedHelperHandle:
    """Starts the helper with UAC elevation and waits until the pipe is ready.

    :param pipe_name: optional pipe path. Default = ``default_pipe_name()``.
    :param pipe_timeout_ms: how long to wait for the pipe (default 10s).
    :raises UACCancelledError: if the user declined the UAC prompt.
    :raises HelperStartTimeoutError: if the helper started but the pipe did not
        become connectable.
    """
    # Secret must exist before the helper reads it.
    ensure_admin_secret()

    pipe = pipe_name or default_pipe_name()
    # H7-Fix: pipe_name is interpolated into lpParameters — without strict
    # validation a quote-injected name could open additional args in the
    # elevated helper. Whitelist: only the characters a valid Windows named
    # pipe name would contain anyway.
    import re as _re
    if not _re.fullmatch(r"\\\\\.\\pipe\\[A-Za-z0-9._\-]{1,200}", pipe):
        raise ValueError(f"Invalid pipe_name: {pipe!r}")
    python_exe, args = _build_helper_argv()
    full_args = f'{args} --pipe-name "{pipe}"'

    try:
        # pywin32 must be imported lazily here — CI without Windows support
        # would otherwise fail at import time.
        import win32con  # type: ignore[import-not-found]
        import win32event  # type: ignore[import-not-found]
        import win32process  # type: ignore[import-not-found]
        from win32com.shell import shell, shellcon  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — only on real Windows
        raise RuntimeError(
            "pywin32 not available — launch_elevated_helper requires Windows."
        ) from exc

    sei = shell.ShellExecuteEx(
        fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_NOASYNC,
        lpVerb="runas",
        lpFile=python_exe,
        lpParameters=full_args,
        nShow=win32con.SW_HIDE,
    )
    h_process = sei.get("hProcess")
    if not h_process:
        # ShellExecuteEx error → user declined, or path is wrong, etc.
        raise UACCancelledError(
            "UAC prompt was declined or the helper could not be started."
        )

    try:
        pid = win32process.GetProcessId(h_process)
    except Exception:  # noqa: BLE001
        pid = None

    # Pipe polling via WaitNamedPipe. ADR-0001: the pipe only exists once the
    # helper has called CreateNamedPipe — this can take 1–3s.
    if not _wait_for_pipe(pipe, pipe_timeout_ms):
        # Helper is running, but pipe is not ready → likely a startup error.
        # Handle stays open so the caller can inspect the exit code.
        if h_process is not None:
            state = win32event.WaitForSingleObject(h_process, 0)
            if state == win32event.WAIT_OBJECT_0:
                exit_code = win32process.GetExitCodeProcess(h_process)
                raise HelperStartTimeoutError(
                    f"Helper exited early (exit={exit_code}) — pipe never ready."
                )
        raise HelperStartTimeoutError(
            f"Pipe {pipe} was not ready after {pipe_timeout_ms}ms."
        )

    logger.info("admin_launcher.started", pid=pid, pipe=pipe)
    return ElevatedHelperHandle(
        pipe_name=pipe, process_handle=h_process, pid=pid,
    )


def _wait_for_pipe(pipe_name: str, timeout_ms: int) -> bool:
    """Polls the pipe at intervals of ``_POLL_INTERVAL_MS``."""
    try:
        import pywintypes  # type: ignore[import-not-found]
        import win32pipe  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        return False

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        try:
            # WaitNamedPipe: blocks until the pipe is available, with its own timeout.
            win32pipe.WaitNamedPipe(pipe_name, _POLL_INTERVAL_MS)
            return True
        except pywintypes.error:
            # Not yet available — keep polling.
            continue
    return False


__all__ = [
    "ElevatedHelperHandle",
    "UACCancelledError",
    "HelperStartTimeoutError",
    "ensure_admin_secret",
    "launch_elevated_helper",
]
