"""Computer-Use pause-and-resume on a human-handoff screen (login / 2FA / CAPTCHA,
audit 🔴 #5).

When the loop reaches a screen only the USER may complete — a login/password
entry, a one-time-code / 2FA prompt, or a CAPTCHA — it must NOT type a secret it
does not hold (AP-2). Instead it speaks a one-time "please take over" request,
polls the accessibility tree until the cue clears, then RESUMES — or, if the user
never acts within the wait budget, stops honestly (timeout). The caller passes the
already-detected ``reason`` so there is no extra observe on the hot path; the
helper only re-observes while polling.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.harness import screenshot_only_loop as loop
from jarvis.voice.action_phrases import action_phrase


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _FakeToken:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


def _node(role: str, name: str) -> Any:
    return SimpleNamespace(role=role, name=name, value="", bounds=(0, 0, 10, 10), enabled=True)


_LOGIN_NODES = (_node("Edit", "Password"), _node("Button", "Sign in"))
_CLEAR_NODES = (_node("Button", "Home"), _node("Edit", "Search"))


class _FakeSource:
    """Yields a configurable sequence of node-tuples from observe(); the LAST
    entry repeats once the sequence is exhausted (so 'cleared forever' is easy)."""

    def __init__(self, seq: list[tuple]) -> None:
        self._seq = seq
        self._i = 0

    async def observe(self) -> Any:
        nodes = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return SimpleNamespace(nodes=nodes)


@pytest.fixture(autouse=True)
def _fast_handoff_timings(monkeypatch):
    # Keep the poll loop sub-second so the timeout test does not sleep 120s.
    monkeypatch.setattr(loop, "_HANDOFF_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(loop, "_HANDOFF_POLL_S", 0.005)


def _use_source(monkeypatch, seq: list[tuple]) -> None:
    # ONE shared instance: each _get_ui_tree_source() call must advance the same
    # cursor, else every poll re-reads node[0] (a fresh source resets _i to 0).
    src = _FakeSource(seq)
    monkeypatch.setattr(loop, "_get_ui_tree_source", lambda: src)


@pytest.mark.asyncio
async def test_cleared_when_handoff_screen_disappears(monkeypatch):
    # Login screen up on the first poll, gone afterwards -> the user signed in.
    _use_source(monkeypatch, [_LOGIN_NODES, _CLEAR_NODES])
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_human_handoff_clearance(
        ctx, "open my mail", 3, None, reason="login / password entry"
    )

    assert result == "cleared"


@pytest.mark.asyncio
async def test_timeout_when_handoff_screen_persists(monkeypatch):
    _use_source(monkeypatch, [_LOGIN_NODES])  # never clears
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_human_handoff_clearance(
        ctx, "open my mail", 3, None, reason="login / password entry"
    )

    assert result == "timeout"


@pytest.mark.asyncio
async def test_cancelled_while_waiting(monkeypatch):
    _use_source(monkeypatch, [_LOGIN_NODES])
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_human_handoff_clearance(
        ctx, "open my mail", 3, _FakeToken(cancelled=True),
        reason="login / password entry",
    )

    assert result == "cancelled"


@pytest.mark.asyncio
async def test_observe_failure_is_treated_as_cleared(monkeypatch):
    class _BoomSource:
        async def observe(self) -> Any:
            raise OSError("tree blew up")

    monkeypatch.setattr(loop, "_get_ui_tree_source", lambda: _BoomSource())
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_human_handoff_clearance(
        ctx, "open my mail", 1, None, reason="captcha challenge"
    )

    assert result == "cleared"  # a flaky tree must never strand the mission


@pytest.mark.asyncio
async def test_announces_handoff_request_in_german(monkeypatch):
    _use_source(monkeypatch, [_CLEAR_NODES])  # clears at once; we only check speech
    bus = _RecordingBus()
    ctx = SimpleNamespace(bus=bus)

    await loop._await_human_handoff_clearance(
        ctx, "Kannst du bitte meine Mails oeffnen?", 1, None,  # i18n-allow: simulated German user utterance, content under test
        reason="login / password entry",
    )
    await asyncio.sleep(0.01)  # let the detached announcement task run

    texts = [getattr(e, "text", "") for e in bus.events]
    assert action_phrase("cu_awaiting_human", "de") in texts


@pytest.mark.asyncio
async def test_announces_handoff_request_in_english(monkeypatch):
    _use_source(monkeypatch, [_CLEAR_NODES])
    bus = _RecordingBus()
    ctx = SimpleNamespace(bus=bus)

    await loop._await_human_handoff_clearance(
        ctx, "Could you open my mail?", 1, None,
        reason="login / password entry",
    )
    await asyncio.sleep(0.01)

    texts = [getattr(e, "text", "") for e in bus.events]
    assert action_phrase("cu_awaiting_human", "en") in texts


def test_phrase_table_has_all_three_languages():
    for lang in ("de", "en", "es"):
        assert action_phrase("cu_awaiting_human", lang).strip()
