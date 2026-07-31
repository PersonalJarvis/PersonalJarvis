"""The PTY advertises its own capability, not its launcher's stdout.

The desktop app may be started from a coding-agent or CI terminal whose
``TERM=dumb`` and ``NO_COLOR=1`` correctly describe that parent.  The child is
subsequently attached to ConPTY/ptyprocess and rendered by xterm.js, so
inheriting either marker is false.  Codex 0.146 pauses every interactive pane
for confirmation in the TERM mismatch; the colour variables are worse, because
every mainstream CLI colour library obeys them unconditionally and the whole
workspace comes up monochrome — inherited once, then handed down across every
in-app restart.  These tests pin both corrections at the shared PTY boundary on
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
    # The LAST call, so a test may contrast two spawns on one backend.
    captured = backend.spawn_calls[-1]["env"]
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


async def test_inherited_no_color_does_not_reach_the_pane(
    backend: FakePtyBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole workspace came up monochrome from this one inherited variable."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")

    env = await _spawn_and_capture(backend, None)

    assert "NO_COLOR" not in env
    assert env["TERM"] == "xterm-256color"


async def test_no_color_is_dropped_even_when_the_terminal_type_is_already_good(
    backend: FakePtyBackend,
) -> None:
    """The correction is per-variable: a sane TERM must not shield NO_COLOR."""
    supplied = {"TERM": "xterm-256color", "PATH": "/safe", "NO_COLOR": "1"}

    env = await _spawn_and_capture(backend, supplied)

    assert env == {"TERM": "xterm-256color", "PATH": "/safe"}
    assert supplied["NO_COLOR"] == "1", "the caller-owned mapping must not be mutated"


async def test_force_color_is_dropped_only_when_it_disables_colour(
    backend: FakePtyBackend,
) -> None:
    disabling = await _spawn_and_capture(backend, {"TERM": "xterm-256color", "FORCE_COLOR": "0"})
    escalating = await _spawn_and_capture(backend, {"TERM": "xterm-256color", "FORCE_COLOR": "3"})

    assert "FORCE_COLOR" not in disabling
    assert escalating["FORCE_COLOR"] == "3", "a deliberate escalation is the user's call"


async def test_empty_colorterm_is_dropped_while_a_real_one_survives(
    backend: FakePtyBackend,
) -> None:
    """Presence alone reads as "sixteen colours" — an empty one downgrades the pane."""
    emptied = await _spawn_and_capture(backend, {"TERM": "xterm-256color", "COLORTERM": ""})
    declared = await _spawn_and_capture(
        backend, {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
    )

    assert "COLORTERM" not in emptied
    assert declared["COLORTERM"] == "truecolor"
