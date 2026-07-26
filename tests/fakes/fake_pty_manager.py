"""In-memory stand-in for ``jarvis.terminal.pty_manager.PtyManager``.

Lets the Agentic-IDE registry be driven end to end without a real
pseudo-terminal and without Claude Code / Codex installed — which is what makes
the session tests runnable on CI and on a headless Linux box.

It records every write, so a test can assert exactly what would have been typed
into an agent (the injection contract) instead of asserting on a mock's call
signature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FakePtySession:
    terminal_id: str
    shell_id: str
    pid: int = 4242


@dataclass(slots=True)
class FakePtyManager:
    """Mirrors the five methods the registry calls on the real manager."""

    spawns: list[dict[str, Any]] = field(default_factory=list)
    writes: list[tuple[str, str]] = field(default_factory=list)
    resizes: list[tuple[str, int, int]] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    # Set to make spawn fail the way a missing PTY backend would.
    spawn_error: str | None = None
    _live: set[str] = field(default_factory=set)
    _counter: int = 0
    # Callbacks per live terminal, so a test can make an agent SPEAK (see
    # ``emit``) rather than only observe what was typed at it.
    _callbacks: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    async def spawn(
        self,
        shell_argv: tuple[str, ...],
        shell_id: str,
        cwd: str | None,
        cols: int,
        rows: int,
        on_output: Any,
        on_closed: Any,
        env: Any = None,
    ) -> FakePtySession:
        if self.spawn_error:
            raise RuntimeError(self.spawn_error)
        self._counter += 1
        terminal_id = f"fake-pty-{self._counter}"
        self.spawns.append(
            {
                "argv": tuple(shell_argv),
                "shell_id": shell_id,
                "cwd": cwd,
                "cols": cols,
                "rows": rows,
                "on_output": on_output,
                "on_closed": on_closed,
                # The environment a pane was started with — how a test proves
                # which subscription an agent actually runs on.
                "env": dict(env) if env is not None else None,
            }
        )
        self._live.add(terminal_id)
        self._callbacks[terminal_id] = (on_output, on_closed)
        return FakePtySession(terminal_id=terminal_id, shell_id=shell_id)

    def write(self, terminal_id: str, data: str) -> bool:
        if terminal_id not in self._live:
            return False
        self.writes.append((terminal_id, data))
        return True

    def resize(self, terminal_id: str, cols: int, rows: int) -> bool:
        if terminal_id not in self._live:
            return False
        self.resizes.append((terminal_id, cols, rows))
        return True

    def close(self, terminal_id: str) -> bool:
        self.closed.append(terminal_id)
        self._callbacks.pop(terminal_id, None)
        return self._live.discard(terminal_id) is None and True

    def has(self, terminal_id: str) -> bool:
        return terminal_id in self._live

    def close_all(self) -> None:
        for terminal_id in list(self._live):
            self.close(terminal_id)

    # ---- test helpers -------------------------------------------------
    @property
    def typed(self) -> list[str]:
        """Everything written, in order (handy for asserting one prompt)."""
        return [data for _tid, data in self.writes]

    async def emit(self, terminal_id: str | None, text: str) -> None:
        """Make the agent in this terminal print ``text``.

        The real reader thread calls the registry's output callback; this is the
        same call, so everything downstream of it — transcript, replay buffer,
        the viewer — is exercised exactly as it is in production.
        """
        callbacks = self._callbacks.get(terminal_id or "")
        if callbacks is None:
            raise AssertionError(f"no live terminal {terminal_id!r} to emit from")
        await callbacks[0](terminal_id, text)

    async def die(self, terminal_id: str | None, code: int = 1) -> None:
        """Make the agent in this terminal exit with ``code``."""
        callbacks = self._callbacks.pop(terminal_id or "", None)
        if callbacks is None:
            raise AssertionError(f"no live terminal {terminal_id!r} to kill")
        self._live.discard(terminal_id or "")
        await callbacks[1](terminal_id, code)


__all__ = ["FakePtyManager", "FakePtySession"]
