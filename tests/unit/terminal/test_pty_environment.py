"""The PTY advertises its own capability, not its launcher's stdout.

The desktop app may be started from a coding-agent or CI terminal whose
``TERM=dumb`` correctly describes that parent.  The child is subsequently
attached to ConPTY/ptyprocess and rendered by xterm.js, so inheriting the old
marker is false.  Codex 0.146 pauses every interactive pane for confirmation in
that mismatch.  These tests pin the correction at the shared PTY boundary on
all operating systems.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

import jarvis.terminal.pty_manager as pty_mod
from jarvis.terminal.pty_manager import PtyManager
from tests.fakes.fake_pty_backend import FakePtyBackend


class _NoopTree:
    def assign(self, _pid: int) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakePtyBackend:
    fake = FakePtyBackend()
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: fake)
    monkeypatch.setattr(pty_mod, "make_process_tree", lambda _name: _NoopTree())
    return fake


async def _spawn_and_capture(
    backend: FakePtyBackend, env: Mapping[str, str] | None
) -> Mapping[str, str]:
    async def _output(_terminal_id: str, _text: str) -> None:
        return None

    async def _closed(_terminal_id: str, _code: int) -> None:
        return None

    manager = PtyManager()
    await manager.spawn(
        shell_argv=("agent",),
        shell_id="agentic-ide:T1",
        cwd=None,
        cols=80,
        rows=24,
        on_output=_output,
        on_closed=_closed,
        env=env,
    )
    captured = backend.spawn_calls[0]["env"]
    manager.close_all()
    assert isinstance(captured, Mapping)
    return captured


async def test_dumb_parent_term_is_replaced_without_losing_the_environment(
    backend: FakePtyBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("JARVIS_PTY_TEST_SENTINEL", "preserved")

    env = await _spawn_and_capture(backend, None)

    assert env["TERM"] == "xterm-256color"
    assert env["JARVIS_PTY_TEST_SENTINEL"] == "preserved"
    assert env.get("PATH")


async def test_missing_parent_term_is_filled_at_the_pty_boundary(
    backend: FakePtyBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TERM", raising=False)

    env = await _spawn_and_capture(backend, None)

    assert env["TERM"] == "xterm-256color"


async def test_explicit_account_environment_is_preserved_while_term_is_fixed(
    backend: FakePtyBackend,
) -> None:
    supplied = {"TERM": "dumb", "PATH": "/safe", "CODEX_HOME": "/account"}

    env = await _spawn_and_capture(backend, supplied)

    assert env == {"TERM": "xterm-256color", "PATH": "/safe", "CODEX_HOME": "/account"}
    assert supplied["TERM"] == "dumb", "the caller-owned mapping must not be mutated"


async def test_meaningful_terminal_type_remains_caller_controlled(
    backend: FakePtyBackend,
) -> None:
    supplied = {"TERM": "screen-256color", "PATH": "/safe"}

    env = await _spawn_and_capture(backend, supplied)

    assert env is supplied
