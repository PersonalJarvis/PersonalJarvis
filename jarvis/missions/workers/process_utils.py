"""Shared subprocess helpers for mission workers."""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress as contextlib_suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Linux prctl() option to ask the kernel to signal this process when its
# parent dies. See `_linux_pdeathsig_preexec_fn` below (P-10 / os-parity.md).
_PR_SET_PDEATHSIG = 1
# SIGKILL is POSIX-only; ``signal`` has no attribute on Windows, and this
# module is imported (and platform-spoofed via monkeypatch in tests) on a
# Windows host too, so resolve with the same fallback pattern already used in
# ``jarvis/missions/isolation/job_object.py``.
_SIGKILL = getattr(signal, "SIGKILL", 9)


def _resolve_linux_prctl() -> Callable[..., int] | None:
    """Return ``libc.prctl`` on Linux, or ``None`` if unavailable.

    Resolved once in the parent process, BEFORE spawn, so the ``preexec_fn``
    closure only ever calls an already-bound C function — never imports or
    does discovery work between ``fork()`` and ``exec()``.

    Guarded with a broad ``except Exception``: ``ctypes.CDLL(None)`` is a
    POSIX/glibc idiom (load the running process's own symbols) that raises
    different exception types depending on the host — e.g. ``TypeError`` on a
    Windows host with ``sys.platform`` patched to ``"linux"`` in tests, or
    ``OSError``/``AttributeError`` on a real Linux host missing ``prctl`` in
    libc. Any failure here must degrade to "no PDEATHSIG", never crash the spawn.
    """
    if sys.platform != "linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)  # noqa: SIM115 - not a file handle
        return libc.prctl  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 - discovery must never break a worker spawn
        return None


def _linux_pdeathsig_preexec_fn(prctl: Callable[..., int]) -> Callable[[], None]:
    """Build a ``preexec_fn`` that arms ``PR_SET_PDEATHSIG`` in the child.

    Closes gap P-10 (``docs/os-parity.md``): a hard SIGKILL of the orchestrator
    bypasses ``_PosixProcessGroupJobObject.close()`` (it only reaps via
    ``killpg`` on an orderly close), so the worker's session/process group is
    reparented to init and survives. Arming ``PR_SET_PDEATHSIG`` makes the
    kernel itself deliver SIGKILL to the worker leader the instant its parent
    (the orchestrator) dies — including on a hard SIGKILL, which no userspace
    signal handler could ever observe.

    Runs between ``fork()`` and ``exec()`` in the child: kept minimal (no
    logging, no imports — ``prctl`` is bound in the parent and passed in via
    closure) and never raises, because ``subprocess`` re-raises any
    ``preexec_fn`` exception as ``SubprocessError`` and would abort the whole
    spawn over what is purely a containment hardening step.

    Known caveats (acceptable — this only strengthens containment, it is
    never the only reaper):
    - PDEATHSIG fires when the spawning *thread* exits, not only when the
      whole process dies; in the asyncio subprocess-spawn path that thread is
      the process itself, so this does not fire spuriously here.
    - macOS has no equivalent syscall; the future path there is a launchd/
      kqueue ``EVFILT_PROC`` watcher, not prctl.
    """

    def _preexec() -> None:
        try:
            prctl(_PR_SET_PDEATHSIG, _SIGKILL)
        except Exception:  # noqa: BLE001, S110 - must never abort the spawn
            pass

    return _preexec


def _windows_node_dir_candidates() -> list[str]:
    """Well-known Windows directories that may contain ``node.exe``.

    Probed only when ``shutil.which`` misses node — which happens when jarvis is
    launched with a degraded PATH that lacks the Node.js dir (live forensic
    2026-06-20: jarvis started by the hermes-agent runtime, PATH had no nodejs
    entry, so the codex worker's ``node`` lookup failed and every mission died).
    """
    candidates: list[str] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            candidates.append(os.path.join(base, "nodejs"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "nodejs"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        # npm-global shims (codex.cmd etc.) and sometimes a node copy live here.
        candidates.append(os.path.join(appdata, "npm"))
    # Hardcoded default install path as a last resort.
    candidates.append(r"C:\Program Files\nodejs")
    return candidates


def resolve_node_executable() -> str | None:
    """Return an absolute path to ``node``, robust against a degraded PATH.

    ``shutil.which`` searches the *inherited* PATH; when jarvis is launched with
    a PATH that lacks the Node.js dir, that returns ``None`` and any node-direct
    worker spawn would fall back to the fragile ``.cmd`` shim (whose own bare
    ``node`` lookup ALSO fails) — the 2026-06-20 "alle Missionen scheitern"
    incident. So when ``which`` misses, probe the well-known install locations.
    """
    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return found
    if sys.platform == "win32":
        for directory in _windows_node_dir_candidates():
            candidate = os.path.join(directory, "node.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def worker_creationflags() -> int:
    """Return Windows creation flags used for detached worker processes."""
    if sys.platform != "win32":
        return 0
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", _CREATE_BREAKAWAY_FROM_JOB)
    )


async def create_worker_subprocess(
    cmd: list[str], **kwargs: Any
) -> asyncio.subprocess.Process:
    """Spawn a worker subprocess with graceful breakaway-flag degradation.

    The worker uses ``CREATE_BREAKAWAY_FROM_JOB`` so the per-mission Job Object
    can take ownership of the process tree (kill-on-close, no zombies). But when
    the host process (pythonw.exe) is itself inside a job that forbids breakaway,
    Windows denies ``CreateProcess`` with ``PermissionError`` (WinError 5) — and
    every worker spawn dies instantly, killing the mission as ``task_error``
    (live mission 019ec602, 2026-06-14, after the native ``claude.exe`` install
    replaced the old ``node cli.js`` path).

    Breakaway is an OPTIMIZATION, not a requirement: without it the worker still
    runs, just inside the parent's job. So on a WinError-5 ``PermissionError`` we
    retry once with the breakaway bit cleared rather than failing the spawn. A
    ``FileNotFoundError`` (missing binary) is a different problem and propagates
    unchanged. The caller passes ``creationflags=worker_creationflags()`` via
    kwargs is NOT required — this helper sources the flags itself so the retry
    can mutate them.
    """
    flags = worker_creationflags()
    kwargs.pop("creationflags", None)  # this helper owns the flags
    if sys.platform != "win32":
        # POSIX: spawn the worker as its own session/process-group leader so the
        # per-mission job object can reap the whole tree via os.killpg on close
        # (the Windows equivalent is the Job Object + CREATE_BREAKAWAY_FROM_JOB).
        kwargs.setdefault("start_new_session", True)
    if sys.platform == "linux":
        # Close gap P-10 (docs/os-parity.md): a hard SIGKILL of the orchestrator
        # bypasses the job object's close()-based reaping, reparenting the worker
        # tree to init. PR_SET_PDEATHSIG makes the kernel SIGKILL the worker
        # leader the instant its parent dies, no matter how the parent died.
        # ``preexec_fn`` is compatible with ``start_new_session=True`` — Python
        # applies setsid() then runs preexec_fn, both in the child post-fork.
        prctl = _resolve_linux_prctl()
        if prctl is not None:
            kwargs.setdefault("preexec_fn", _linux_pdeathsig_preexec_fn(prctl))
    try:
        return await asyncio.create_subprocess_exec(
            *cmd, creationflags=flags, **kwargs
        )
    except PermissionError as exc:
        if sys.platform != "win32" or not (flags & _CREATE_BREAKAWAY_FROM_JOB):
            raise
        safe_flags = flags & ~_CREATE_BREAKAWAY_FROM_JOB
        logger.warning(
            "worker spawn denied with CREATE_BREAKAWAY_FROM_JOB (%s) — retrying "
            "without breakaway; the host process is in a job that forbids it, so "
            "per-mission Job-Object isolation is degraded for this worker.",
            exc,
        )
        return await asyncio.create_subprocess_exec(
            *cmd, creationflags=safe_flags, **kwargs
        )


async def drain_stderr(
    stream: asyncio.StreamReader | None,
    log_path: Path,
) -> None:
    """Drain stderr to disk without blocking worker stdout consumption."""
    if stream is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as f:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            f.write(chunk)
            f.flush()


__all__ = [
    "contextlib_suppress",
    "create_worker_subprocess",
    "drain_stderr",
    "resolve_node_executable",
    "worker_creationflags",
]
