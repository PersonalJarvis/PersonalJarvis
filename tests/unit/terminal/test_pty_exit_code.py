"""What a pane is told when its agent is gone.

The reported bug: a Codex pane printed "[Codex exited — code 4294967295]". That
number is a Windows exit code read as unsigned — the agent had ended with -1 —
and the pane handed all ten digits to the user unchanged.

Underneath it sat a second, quieter defect. The code was read as
``int(proc.exitstatus or -1)``, so a CLEAN exit (0) was reported as -1: quitting
an agent with ``/exit`` looked exactly like a crash, and the resume
self-healing in ``jarvis/agentic_ide/session.py`` — which restarts a pane whose
exit code is not 0 — restarted agents the user had deliberately closed.

These pin both halves, on every OS: the handle is a fake, so no real
pseudo-terminal (or coding agent) is needed to run them.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

import jarvis.terminal.pty_manager as pty_mod
from jarvis.terminal.pty_manager import (
    UNKNOWN_EXIT_CODE,
    PtyManager,
    normalize_exit_code,
)


class ExitingHandle:
    """A ``PtyHandle`` that ends immediately with whatever the test asks for."""

    def __init__(self, exitstatus: int | None) -> None:
        self.pid = 4242
        self.exitstatus = exitstatus
        self._chunks: deque[str] = deque(["done\r\n"])
        self._alive = True

    def write(self, data: str) -> None:
        return None

    def setwinsize(self, rows: int, cols: int) -> None:
        return None

    def read(self, size: int) -> str:
        if self._chunks:
            return self._chunks.popleft()
        self._alive = False
        raise EOFError

    def isalive(self) -> bool:
        return self._alive

    def terminate(self, force: bool) -> None:
        self._alive = False


class ScriptedBackend:
    def __init__(self, handle: ExitingHandle) -> None:
        self.handle = handle

    def spawn(self, argv, cwd, cols, rows, env=None):  # noqa: ANN001, ANN201
        return self.handle


async def _closed_code(monkeypatch, exitstatus: int | None) -> int:  # noqa: ANN001
    """Spawn a pane whose child ends with ``exitstatus``; return what the viewer got."""
    handle = ExitingHandle(exitstatus)
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: ScriptedBackend(handle))
    reported: list[int] = []
    manager = PtyManager()

    async def on_output(_tid: str, _text: str) -> None:
        return

    async def on_closed(_tid: str, code: int) -> None:
        reported.append(code)

    await manager.spawn(
        shell_argv=("agent",),
        shell_id="test",
        cwd=None,
        cols=80,
        rows=24,
        on_output=on_output,
        on_closed=on_closed,
    )
    deadline = asyncio.get_running_loop().time() + 3.0
    while not reported:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the pane was never told its agent had exited")
        await asyncio.sleep(0.005)
    manager.close_all()
    return reported[0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The reported bug, exactly: pywinpty hands over the unsigned DWORD.
        (0xFFFF_FFFF, -1),
        # A clean stop stays a clean stop — the `or -1` idiom made it a failure.
        (0, 0),
        # Ordinary codes are untouched on every OS.
        (1, 1),
        (3, 3),
        (255, 255),
        # A Windows NTSTATUS re-signs like any other value above 2**31.
        (0xC000_013A, -1073741510),
        # Nothing to report is its own answer, not an invented number.
        (None, UNKNOWN_EXIT_CODE),
    ],
)
def test_exit_codes_are_normalized(raw: int | None, expected: int) -> None:
    assert normalize_exit_code(raw) == expected


async def test_a_clean_exit_is_reported_as_zero(monkeypatch) -> None:  # noqa: ANN001
    """The half that made `/exit` look like a crash and restarted closed panes."""
    assert await _closed_code(monkeypatch, 0) == 0


async def test_an_unsigned_windows_code_reaches_the_pane_signed(monkeypatch) -> None:  # noqa: ANN001
    """4294967295 never leaves this layer — the pane is told -1."""
    assert await _closed_code(monkeypatch, 0xFFFF_FFFF) == -1


async def test_an_unknown_code_is_not_invented(monkeypatch) -> None:  # noqa: ANN001
    """A backend that cannot say reports the one agreed 'not a clean stop'."""
    assert await _closed_code(monkeypatch, None) == UNKNOWN_EXIT_CODE


def test_the_exit_line_carries_its_fields_in_the_message() -> None:
    """The line this bug needed and did not have.

    loguru reads keyword arguments as FORMAT arguments and the app's file sink
    formats ``{message}`` alone, so ``logger.info("PTY reader terminated",
    exit_code=...)`` logged the bare sentence — every past pane death was
    recorded without saying which pane or with what code. Pinned by reading the
    source, because the sink that dropped them is configured elsewhere.
    """
    from pathlib import Path

    source = Path(pty_mod.__file__).read_text(encoding="utf-8")
    marker = "PTY reader terminated"
    line = next(ln for ln in source.splitlines() if marker in ln)
    assert "exit_code={}" in line, "the exit code must be part of the message"
