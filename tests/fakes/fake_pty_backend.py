"""Hand-built ``PtyBackend`` / ``PtyHandle`` fake (EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. A real PTY
cannot be spawned on the Windows dev box for ``UnixPtyBackend`` (ptyprocess is
POSIX-only), and even the real-PTY roundtrip test only runs on the CI Linux/
macOS legs. This fake gives the str<->bytes normalization and lifecycle tests a
deterministic, OS-free ``PtyHandle`` that echoes whatever is written to it.

The fake is intentionally ``str``-facing — it stands in for a ``PtyHandle``
*after* the backend seam has normalized bytes to str, so a test can drive
``PtyManager``'s read-loop (or the handle directly) with no native PTY. To
exercise the *bytes* normalization specifically, ``FakeBytesPtyHandle`` mimics
the raw ``ptyprocess``/``winpty`` surface (bytes in/out) so a test can wrap it
in the real ``UnixPtyBackend``-style decode/encode logic.
"""

from __future__ import annotations

from collections import deque


class FakePtyHandle:
    """A ``PtyHandle`` that echoes ``write()`` back out of ``read()`` as str.

    Structurally compatible with ``jarvis.terminal.backend.PtyHandle``:
    ``pid``, ``exitstatus``, ``write``, ``setwinsize``, ``read``, ``isalive``,
    ``terminate``.
    """

    def __init__(self, *, pid: int = 4242, exitstatus: int | None = None) -> None:
        self._pid = pid
        self._exitstatus = exitstatus
        self._buffer: deque[str] = deque()
        self._alive = True
        self.winsize: tuple[int, int] | None = None
        self.terminated_force: bool | None = None

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def exitstatus(self) -> int | None:
        return self._exitstatus

    def write(self, data: str) -> None:
        # Echo: whatever is written becomes readable output (str in / str out).
        if not isinstance(data, str):  # defends the seam contract
            raise TypeError(f"FakePtyHandle.write expects str, got {type(data)!r}")
        self._buffer.append(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        self.winsize = (rows, cols)

    def read(self, size: int) -> str:
        if self._buffer:
            return self._buffer.popleft()
        return ""

    def isalive(self) -> bool:
        return self._alive

    def terminate(self, force: bool) -> None:
        self.terminated_force = force
        self._alive = False
        if self._exitstatus is None:
            self._exitstatus = 0


class FakePtyBackend:
    """A ``PtyBackend`` that hands out ``FakePtyHandle`` objects.

    Records the spawn arguments so a test can assert ``argv``/``cwd``/``cols``/
    ``rows`` were threaded through unchanged.
    """

    def __init__(self, *, pid: int = 4242, exitstatus: int | None = None) -> None:
        self._pid = pid
        self._exitstatus = exitstatus
        self.spawn_calls: list[dict[str, object]] = []
        self.last_handle: FakePtyHandle | None = None

    def spawn(
        self,
        argv: tuple[str, ...],
        cwd: str | None,
        cols: int,
        rows: int,
        env: object | None = None,
    ) -> FakePtyHandle:
        self.spawn_calls.append(
            {"argv": argv, "cwd": cwd, "cols": cols, "rows": rows, "env": env}
        )
        handle = FakePtyHandle(pid=self._pid, exitstatus=self._exitstatus)
        self.last_handle = handle
        return handle


class FakeBytesPtyProcess:
    """Mimics the raw ``ptyprocess.PtyProcess`` surface (bytes in/out).

    Used to prove ``UnixPtyBackend``'s str<->bytes normalization: ``read``
    returns ``bytes`` and ``write`` is asserted to receive ``bytes`` (encoded by
    the backend), exactly like the real ptyprocess. Includes a ``spawn``
    classmethod so it can stand in for ``ptyprocess.PtyProcess`` itself.
    """

    last_spawn: dict[str, object] | None = None

    def __init__(self, *, pid: int = 777, exitstatus: int | None = 0) -> None:
        self.pid = pid
        self.exitstatus = exitstatus
        self._out: deque[bytes] = deque()
        self.written: list[bytes] = []
        self.winsize: tuple[int, int] | None = None
        self.terminated_force: bool | None = None
        self._alive = True

    @classmethod
    def spawn(cls, argv, cwd=None, dimensions=None, env=None):  # noqa: ANN001
        inst = cls()
        FakeBytesPtyProcess.last_spawn = {
            "argv": argv,
            "cwd": cwd,
            "dimensions": dimensions,
            "env": env,
        }
        return inst

    def queue_output(self, data: bytes) -> None:
        self._out.append(data)

    def read(self, size: int) -> bytes:
        if self._out:
            return self._out.popleft()
        return b""

    def write(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError(
                f"ptyprocess.write expects bytes, got {type(data)!r}"
            )
        self.written.append(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        self.winsize = (rows, cols)

    def isalive(self) -> bool:
        return self._alive

    def terminate(self, force: bool = False) -> None:
        self.terminated_force = force
        self._alive = False


__all__ = ["FakePtyHandle", "FakePtyBackend", "FakeBytesPtyProcess"]
