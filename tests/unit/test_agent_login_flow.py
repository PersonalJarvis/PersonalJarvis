"""The in-app guided sign-in, driven against a scripted fake PTY.

What these pin down is the contract the dialog depends on, not the CLI's
cosmetics: the sign-in URL survives ANSI noise and chunk boundaries intact (a
copy button that copies a truncated URL is the bug this flow replaces), the
pasted code reaches the child exactly as typed, and success is judged by the
account directory — never by trusting the transcript.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from jarvis import agent_accounts, agent_login_flow
from jarvis.agent_accounts import AgentAccount
from jarvis.agent_login_flow import GuidedLoginUnavailable

_URL = (
    "https://claude.ai/oauth/authorize?code=true&client_id=abc123"
    "&redirect_uri=https%3A%2F%2Fclaude.ai%2Foauth&state=xyz789"
)


class FakeHandle:
    """A scripted PTY child: hands out chunks, records writes."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)
        self._lock = threading.Lock()
        self.writes: list[str] = []
        self.alive = True
        self.terminated = False
        self.pid = 4242
        self.exitstatus: int | None = None

    def read(self, size: int) -> str:
        with self._lock:
            if self._chunks:
                return self._chunks.pop(0)
        if not self.alive:
            raise EOFError
        return ""

    def feed(self, chunk: str) -> None:
        with self._lock:
            self._chunks.append(chunk)

    def isalive(self) -> bool:
        return self.alive

    def write(self, data: str) -> None:
        self.writes.append(data)

    def terminate(self, force: bool) -> None:
        self.terminated = True
        self.alive = False


class FakeBackend:
    def __init__(self, handle: FakeHandle) -> None:
        self.handle = handle
        self.spawn_calls: list[dict[str, Any]] = []

    def spawn(self, **kwargs: Any) -> FakeHandle:
        self.spawn_calls.append(kwargs)
        return self.handle


class FakeTree:
    def assign(self, pid: int) -> None:  # pragma: no cover - trivial
        pass

    def close(self) -> None:  # pragma: no cover - trivial
        pass


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """No real CLI, no real PTY, no multi-second verify cadence."""
    monkeypatch.setattr(
        agent_accounts, "login_command", lambda account: (["fake-cli", "login"], "t")
    )
    monkeypatch.setattr(agent_login_flow, "_VERIFY_EVERY_S", 0.02)
    monkeypatch.setattr(agent_login_flow, "_POLL_S", 0.005)
    # Every test decides for itself when the login "landed".
    monkeypatch.setattr(agent_login_flow, "_connected", lambda account: False)
    monkeypatch.setattr("jarvis.core.process_tree.make_process_tree", lambda name: FakeTree())
    agent_login_flow._REGISTRY.clear()
    yield
    for flow_id in list(agent_login_flow._REGISTRY):
        agent_login_flow.cancel_flow(flow_id)
    agent_login_flow._REGISTRY.clear()


def _account(tmp_path: Path) -> AgentAccount:
    return AgentAccount(
        id="claude:test1",
        platform="claude",
        label="Second seat",
        config_dir=tmp_path / "seat",
    )


def _start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, chunks: list[str]):
    handle = FakeHandle(chunks)
    backend = FakeBackend(handle)
    monkeypatch.setattr("jarvis.terminal.backend.make_pty_backend", lambda: backend)
    state = agent_login_flow.start_flow(_account(tmp_path))
    return handle, backend, state


def _wait_for(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


def _state(flow_id: str) -> dict[str, Any]:
    state = agent_login_flow.flow_state(flow_id)
    assert state is not None
    return state


# ------------------------------------------------------------------ the URL


def test_url_survives_ansi_noise_and_a_chunk_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The URL arrives coloured and torn across two PTY reads — the state must
    still carry it byte-for-byte, or the copy button copies a broken link."""
    first, second = _URL[:60], _URL[60:]
    handle, _backend, state = _start(
        monkeypatch,
        tmp_path,
        [
            "\x1b[2J\x1b[1mSign in\x1b[0m\r\nOpen this url:\r\n\x1b[36m" + first,
            second + "\x1b[0m\r\nPaste code here if prompted:\r\n",
        ],
    )
    _wait_for(lambda: _state(state["flow_id"])["url"] is not None)
    final = _state(state["flow_id"])
    assert final["url"] == _URL
    assert final["code_expected"] is True
    assert final["status"] == "awaiting_input"


def test_the_sign_in_url_wins_over_a_docs_link_printed_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle, _backend, state = _start(
        monkeypatch,
        tmp_path,
        ["See https://docs.example.com/help for details.\r\nOpen " + _URL + "\r\n"],
    )
    _wait_for(lambda: _state(state["flow_id"])["url"] is not None)
    assert _state(state["flow_id"])["url"] == _URL


def test_the_pty_is_wide_enough_that_no_oauth_url_wraps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle, backend, _state_ = _start(monkeypatch, tmp_path, [])
    assert backend.spawn_calls[0]["cols"] >= 500


# ------------------------------------------------------------------ the code


def test_the_pasted_code_reaches_the_child_exactly_as_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle, _backend, state = _start(monkeypatch, tmp_path, ["Paste code here if prompted:\r\n"])
    _wait_for(lambda: _state(state["flow_id"])["code_expected"])
    result = agent_login_flow.submit_code(state["flow_id"], "  abc123#state456  ")
    assert result is not None and result["status"] == "verifying"
    assert handle.writes == ["abc123#state456\r"]


def test_an_empty_or_control_character_code_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle, _backend, state = _start(monkeypatch, tmp_path, [])
    with pytest.raises(ValueError):
        agent_login_flow.submit_code(state["flow_id"], "   ")
    with pytest.raises(ValueError):
        agent_login_flow.submit_code(state["flow_id"], "abc\x1b[200~def")
    assert handle.writes == []


# ------------------------------------------------------------------- verdicts


def test_success_is_judged_by_the_account_directory_not_the_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI may print anything; only credentials on disk flip the flow."""
    handle, _backend, state = _start(monkeypatch, tmp_path, ["Login successful! Welcome back.\r\n"])
    time.sleep(0.1)
    assert _state(state["flow_id"])["status"] not in ("success", "failed")
    monkeypatch.setattr(agent_login_flow, "_connected", lambda account: True)
    _wait_for(lambda: _state(state["flow_id"])["status"] == "success")
    assert _state(state["flow_id"])["finished"] is True


def test_a_child_that_exits_without_a_login_fails_with_its_last_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle, _backend, state = _start(monkeypatch, tmp_path, ["OAuth error: Invalid code\r\n"])
    _wait_for(lambda: "Invalid code" in _state(state["flow_id"])["tail"])
    handle.alive = False
    _wait_for(lambda: _state(state["flow_id"])["status"] == "failed")
    assert "Invalid code" in _state(state["flow_id"])["message"]


def test_cancelling_terminates_the_hidden_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handle, _backend, state = _start(monkeypatch, tmp_path, [])
    result = agent_login_flow.cancel_flow(state["flow_id"])
    assert result is not None and result["status"] == "cancelled"
    _wait_for(lambda: handle.terminated)


def test_a_host_with_no_pty_capability_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class NoPty:
        def spawn(self, **kwargs: Any) -> Any:
            raise RuntimeError("no PTY capability on this host")

    monkeypatch.setattr("jarvis.terminal.backend.make_pty_backend", lambda: NoPty())
    with pytest.raises(GuidedLoginUnavailable):
        agent_login_flow.start_flow(_account(tmp_path))


def test_a_second_start_for_the_same_account_cancels_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first_handle, _b, first = _start(monkeypatch, tmp_path, [])
    second_handle, _b2, second = _start(monkeypatch, tmp_path, [])
    assert first["flow_id"] != second["flow_id"]
    _wait_for(lambda: first_handle.terminated)
    assert _state(second["flow_id"])["status"] == "starting"


def test_success_stamps_the_onboarding_marker_into_the_account_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the marker a signed-in account still boots panes into the CLI's
    first-run wizard — which reads exactly like the login having failed."""
    import json

    handle, _backend, state = _start(monkeypatch, tmp_path, [])
    monkeypatch.setattr(agent_login_flow, "_connected", lambda account: True)
    _wait_for(lambda: _state(state["flow_id"])["status"] == "success")
    doc = json.loads((tmp_path / "seat" / ".claude.json").read_text(encoding="utf-8"))
    assert doc["hasCompletedOnboarding"] is True
