"""A native tool call can never hold the live voice past its deadline.

Live 2026-08-22 20:01:52 (gemini-live): ``youtube_music`` sat 199 s on a
stuck background-player host. The instant ack spoke at 3 s, then the call
stayed mute until 20:05:12 because a native call blocks the live model until
its result arrives (ADR-0035 §3) and nothing bounded that wait. Pinned here:

* past ``_NATIVE_TOOL_DEADLINE_S`` the model receives an honest ``pending``
  result and is released — the tool itself is NOT cancelled, it runs to
  completion in the background;
* the late outcome is booked (log, failure counter, latency record) but never
  enters the per-turn evidence (``_executed_tool_names`` /
  ``_direct_tool_results``), which a later turn reads as its own;
* a tool that answers within the deadline is untouched — same result, no
  pending flag, counted as executed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.realtime import session as session_module
from jarvis.realtime.session import RealtimeVoiceSession

RATE = 24_000


class _Wire:
    session_id = "deadline-wire"
    supports_tool_updates = True
    creates_responses_automatically = True
    isolates_response_generations = True

    def __init__(self) -> None:
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []

    async def send_tool_result(self, call_id: str, name: str, payload: dict[str, Any]) -> None:
        self.tool_results.append((call_id, name, payload))

    async def send_json(self, *_args: Any) -> None:
        return None


class _Provider:
    name = "deadline-provider"
    supports_realtime = True
    supports_direct_tools = True
    input_sample_rate = RATE
    output_sample_rate = RATE

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _Wire:
        return _Wire()


class _SlowBridge:
    """A bridge whose tool takes ``delay_s`` and then reports ``result``."""

    declarations: tuple[dict[str, Any], ...] = ()

    def __init__(self, delay_s: float, result: dict[str, Any] | None = None) -> None:
        self.delay_s = delay_s
        self.result = result or {"success": True, "output": "played", "error": None}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.finished = asyncio.Event()

    def set_language(self, _language: str) -> None:
        return None

    async def handle_user_transcript(self, _text: str) -> None:
        return None

    async def execute(
        self, *, wire_name: str, arguments: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        self.calls.append((wire_name, arguments))
        await asyncio.sleep(self.delay_s)
        self.finished.set()
        return wire_name, dict(self.result)

    async def close(self) -> None:
        return None


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="hybrid"),
        latency=SimpleNamespace(enabled=False),
    )


def _session(bridge: _SlowBridge) -> tuple[RealtimeVoiceSession, _Wire]:
    session = RealtimeVoiceSession(
        session_id="native-tool-deadline",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[_Provider()],
        config=_config(),
        bus=None,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
        tool_bridge=bridge,
        brain=SimpleNamespace(conversation_language="en"),
    )
    wire = _Wire()
    session._session = wire
    session._language = "en"
    session._turn_id = "turn-1"
    session._last_user_text = "play some music"
    return session, wire


def _event(name: str = "youtube_music") -> SimpleNamespace:
    return SimpleNamespace(call_id="call-1", tool_name=name, tool_args={"action": "play"})


@pytest.mark.asyncio
async def test_a_tool_past_the_deadline_releases_the_model_with_a_pending_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "_NATIVE_TOOL_DEADLINE_S", 0.2)
    monkeypatch.setattr(session_module, "_INSTANT_ACK_GRACE_S", 60.0)
    bridge = _SlowBridge(delay_s=0.6)
    session, wire = _session(bridge)

    started = asyncio.get_running_loop().time()
    await session._handle_tool_call(_event())
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5, f"the model waited {elapsed:.2f}s — the deadline did not release it"
    assert len(wire.tool_results) == 1
    _call_id, name, payload = wire.tool_results[0]
    assert name == "youtube_music"
    assert payload["success"] is False
    assert payload["pending"] is True
    assert "still running" in payload["error"]
    # Released, not counted as a failure or a denial — and not as executed
    # either: nothing has happened yet.
    assert session._native_tool_failures == 0
    assert session._native_tool_denied == 0
    assert "youtube_music" not in session._executed_tool_names
    assert not bridge.finished.is_set(), "the tool must keep running, not be cancelled"

    # The tool runs to completion in the background and books its late outcome
    # without touching the per-turn evidence.
    await asyncio.wait_for(bridge.finished.wait(), timeout=2.0)
    await asyncio.sleep(0)  # let the done-callback run
    assert session._native_tool_failures == 0
    assert "youtube_music" not in session._executed_tool_names
    assert session._direct_tool_results == []


@pytest.mark.asyncio
async def test_a_late_failure_is_counted_once_it_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "_NATIVE_TOOL_DEADLINE_S", 0.2)
    monkeypatch.setattr(session_module, "_INSTANT_ACK_GRACE_S", 60.0)
    bridge = _SlowBridge(
        delay_s=0.5, result={"success": False, "output": None, "error": "player gone"}
    )
    session, wire = _session(bridge)

    await session._handle_tool_call(_event())
    assert wire.tool_results[0][2]["pending"] is True
    assert session._native_tool_failures == 0

    await asyncio.wait_for(bridge.finished.wait(), timeout=2.0)
    await asyncio.sleep(0)
    assert session._native_tool_failures == 1


@pytest.mark.asyncio
async def test_a_tool_within_the_deadline_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "_NATIVE_TOOL_DEADLINE_S", 2.0)
    monkeypatch.setattr(session_module, "_INSTANT_ACK_GRACE_S", 60.0)
    bridge = _SlowBridge(delay_s=0.05)
    session, wire = _session(bridge)

    await session._handle_tool_call(_event())

    assert len(wire.tool_results) == 1
    payload = wire.tool_results[0][2]
    assert payload == {"success": True, "output": "played", "error": None}
    assert "pending" not in payload
    assert "youtube_music" in session._executed_tool_names
    assert session._direct_tool_results == [("youtube_music", payload)]
    assert session._native_tool_failures == 0


def test_the_deadline_leaves_room_for_the_slowest_honest_tool() -> None:
    """YouTube Music's cold-start confirm is ~9 s by design plus a window show;
    the ceiling must sit above it, and well below the turn-stall watchdog."""
    assert 12.0 <= session_module._NATIVE_TOOL_DEADLINE_S < session_module._TURN_STALL_TIMEOUT_S
