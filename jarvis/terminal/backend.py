"""PTY backend seam (Wave 1.1, AD-6 + AD-9).

The terminal layer used to import ``winpty`` directly inside
``pty_manager.py`` and call ``proc.read``/``write``/``setwinsize``/
``terminate``/``isalive`` on a raw ``winpty.PtyProcess``. That hard-wired the
feature to Windows. This module introduces the uniform AD-6 seam: a
``PtyBackend`` / ``PtyHandle`` ``Protocol``, one per-OS implementation, a
``sys.platform`` factory (``make_pty_backend``), and a graceful null fallback.

AD-9 is explicit that there is **no async rewrite**: ``ptyprocess.PtyProcess``
mirrors all five ``pywinpty`` methods 1:1, so the existing daemon-thread
read-loop in ``pty_manager.py`` keeps working verbatim once it talks to a
``PtyHandle`` instead of a raw process. The only seam work is normalizing
``str`` <-> ``bytes``:

* ``winpty.PtyProcess`` already deals in ``str`` (ConPTY decodes for us), so
  ``WinptyBackend`` is a thin pass-through that preserves the exact
  ``RuntimeError("pywinpty not installed ...")`` degrade.
* ``ptyprocess.PtyProcess`` deals in ``bytes`` (``.read`` returns bytes,
  ``.write`` takes bytes), so ``UnixPtyBackend`` decodes ``utf-8`` with
  ``errors="replace"`` on read and encodes on write.

Import-cleanliness contract (HN-7): neither ``winpty`` nor ``ptyprocess`` is
imported at module scope. Both imports are lazy, inside ``spawn`` (the same
guarded pattern as ``jarvis/plugins/tool/app_resolver.py:24``), so
``import jarvis.terminal.backend`` succeeds on a Linux VPS with neither package
installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities


@runtime_checkable
class PtyHandle(Protocol):
    """A spawned pseudo-terminal — the surface the read-loop drives.

    Exactly the five methods + two attributes ``pty_manager._reader_loop`` and
    ``PtyManager`` use, normalized to a single ``str``-facing signature so the
    Windows and Unix backends are interchangeable behind the seam.
    """

    @property
    def pid(self) -> int:
        """OS process id of the child, or 0 if unavailable."""

    @property
    def exitstatus(self) -> int | None:
        """Child exit code once it has exited, else ``None``."""

    def write(self, data: str) -> None:
        """Write ``str`` to the PTY (the backend encodes if needed)."""

    def setwinsize(self, rows: int, cols: int) -> None:
        """Resize the PTY window to ``rows`` x ``cols``."""

    def read(self, size: int) -> str:
        """Read up to ``size`` bytes, decoded to ``str`` at the seam."""

    def isalive(self) -> bool:
        """True while the child process is still running."""

    def terminate(self, force: bool) -> None:
        """Terminate the child; ``force`` escalates to a hard kill."""


@runtime_checkable
class PtyBackend(Protocol):
    """Factory for ``PtyHandle`` objects on a given platform."""

    def spawn(
        self,
        argv: tuple[str, ...],
        cwd: str | None,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> PtyHandle:
        """Spawn a PTY running ``argv`` and return a normalized handle.

        ``env`` (when given) replaces the child environment; ``None`` inherits
        the parent's (the integrated-terminal default). Both pywinpty and
        ptyprocess accept ``env=`` directly.
        """


# ----------------------------------------------------------------------
# Windows — pywinpty (str in / str out)
# ----------------------------------------------------------------------


class _WinptyHandle:
    """Thin ``PtyHandle`` over a ``winpty.PtyProcess`` (already ``str``-based)."""

    __slots__ = ("_proc",)

    def __init__(self, proc: object) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return int(getattr(self._proc, "pid", 0) or 0)

    @property
    def exitstatus(self) -> int | None:
        status = getattr(self._proc, "exitstatus", None)
        return None if status is None else int(status)

    def write(self, data: str) -> None:
        self._proc.write(data)  # type: ignore[attr-defined]

    def setwinsize(self, rows: int, cols: int) -> None:
        self._proc.setwinsize(rows, cols)  # type: ignore[attr-defined]

    def read(self, size: int) -> str:
        data = self._proc.read(size)  # type: ignore[attr-defined]
        # pywinpty returns str; tolerate a bytes payload defensively.
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    def isalive(self) -> bool:
        return bool(self._proc.isalive())  # type: ignore[attr-defined]

    def terminate(self, force: bool) -> None:
        self._proc.terminate(force=force)  # type: ignore[attr-defined]


class WinptyBackend:
    """Spawns Windows ConPTY sessions via ``winpty.PtyProcess`` (lazy import)."""

    def spawn(
        self,
        argv: tuple[str, ...],
        cwd: str | None,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> PtyHandle:
        try:
            from winpty import PtyProcess  # type: ignore[import-not-found]
        except ImportError as exc:
            # Preserve the exact degrade message from pty_manager.py:72-75.
            raise RuntimeError(
                "pywinpty not installed — `pip install pywinpty` (Windows-only)."
            ) from exc

        proc = PtyProcess.spawn(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            dimensions=(rows, cols),
        )
        return _WinptyHandle(proc)


# ----------------------------------------------------------------------
# Unix — ptyprocess (bytes in / bytes out -> normalized to str)
# ----------------------------------------------------------------------


class _UnixPtyHandle:
    """``PtyHandle`` over a ``ptyprocess.PtyProcess``, normalizing str<->bytes."""

    __slots__ = ("_proc",)

    def __init__(self, proc: object) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return int(getattr(self._proc, "pid", 0) or 0)

    @property
    def exitstatus(self) -> int | None:
        status = getattr(self._proc, "exitstatus", None)
        return None if status is None else int(status)

    def write(self, data: str) -> None:
        # ptyprocess.PtyProcess.write takes bytes.
        self._proc.write(data.encode("utf-8"))  # type: ignore[attr-defined]

    def setwinsize(self, rows: int, cols: int) -> None:
        self._proc.setwinsize(rows, cols)  # type: ignore[attr-defined]

    def read(self, size: int) -> str:
        # ptyprocess.PtyProcess.read returns bytes; decode at the seam.
        data = self._proc.read(size)  # type: ignore[attr-defined]
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    def isalive(self) -> bool:
        return bool(self._proc.isalive())  # type: ignore[attr-defined]

    def terminate(self, force: bool) -> None:
        self._proc.terminate(force=force)  # type: ignore[attr-defined]


class UnixPtyBackend:
    """Spawns POSIX PTY sessions via ``ptyprocess.PtyProcess`` (lazy import)."""

    def spawn(
        self,
        argv: tuple[str, ...],
        cwd: str | None,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> PtyHandle:
        try:
            import ptyprocess  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ptyprocess not installed — `pip install ptyprocess` "
                "(POSIX terminal backend; part of the [desktop] extra)."
            ) from exc

        # ptyprocess mirrors pywinpty 1:1: dimensions=(rows, cols), cwd=...
        proc = ptyprocess.PtyProcess.spawn(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            dimensions=(rows, cols),
        )
        return _UnixPtyHandle(proc)


# ----------------------------------------------------------------------
# Null fallback — no PTY capability on this box (AD-6: degrade, never crash)
# ----------------------------------------------------------------------


class NullPtyBackend:
    """Returned when ``capabilities.has_pty`` is False.

    ``spawn`` raises a clear English ``RuntimeError`` which the ``PtyManager``
    already surfaces as a typed error (not a bare crash — AD-6). This is the
    headless-VPS / no-PTY-toolchain case.
    """

    def spawn(
        self,
        argv: tuple[str, ...],
        cwd: str | None,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> PtyHandle:
        raise RuntimeError(
            "No pseudo-terminal backend available on this host — the integrated "
            "terminal is unavailable. Install the [desktop] extra (pywinpty on "
            "Windows, ptyprocess on POSIX) to enable it."
        )


def make_pty_backend() -> PtyBackend:
    """Select the PTY backend for this host (AD-6 factory).

    * ``win32`` -> ``WinptyBackend``
    * anything else -> ``UnixPtyBackend``
    * if ``capabilities.has_pty`` is False -> ``NullPtyBackend`` (degrade)

    Never raises; the missing-capability case is a typed error surfaced only
    when ``spawn`` is actually called.
    """
    if not detect_capabilities().has_pty:
        return NullPtyBackend()
    if detect_platform() == "win32":
        return WinptyBackend()
    return UnixPtyBackend()


__all__ = [
    "PtyBackend",
    "PtyHandle",
    "WinptyBackend",
    "UnixPtyBackend",
    "NullPtyBackend",
    "make_pty_backend",
]
