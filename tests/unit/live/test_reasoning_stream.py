"""GPT-Live streams the thinking model's reasoning summary live (jarvis/live/session.py).

The Responses API sends a reasoning summary token by token. Every mirror —
the app's thinking steps, the desktop pet's card — follows it as cumulative
``ReasoningSummaryUpdated`` snapshots, coalesced so the bus sees a few per
second (AP-9), and closed by the final ``done`` text. The trace ring and the
session recorder keep the finished summary, not every partial copy.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.events import ReasoningSummaryUpdated, VoiceSessionStarted
from jarvis.live import session as live_session
from jarvis.live.session import LiveVoiceSession
from jarvis.sessions.recorder import SessionRecorder
from jarvis.state.turn_trace import TurnTraceCollector


class _Bus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)

    def reasoning(self) -> list[ReasoningSummaryUpdated]:
        return [e for e in self.events if isinstance(e, ReasoningSummaryUpdated)]


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _session(bus: _Bus) -> LiveVoiceSession:
    async def send(_event: object) -> None:
        return None

    session = LiveVoiceSession(
        session_id="s",
        send_binary=send,
        send_json=send,
        providers=[SimpleNamespace(name="test")],
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        bus=bus,
    )
    # Reasoning events touch neither the ledger nor the tools.
    session._ledger = SimpleNamespace()  # noqa: SLF001
    session._tools = SimpleNamespace()  # noqa: SLF001
    return session


async def _send(session: LiveVoiceSession, payload: dict) -> None:
    await session._event(  # noqa: SLF001
        {"type": "response.event", "delegation_id": "d", "event": payload}
    )


def _delta(text: str, *, index: int = 0) -> dict:
    return {
        "type": "response.reasoning_summary_text.delta",
        "item_id": "rs_1",
        "summary_index": index,
        "delta": text,
    }


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(live_session.time, "monotonic", fake)
    return fake


async def test_deltas_are_published_as_coalesced_cumulative_snapshots(clock: _Clock) -> None:
    bus = _Bus()
    session = _session(bus)
    await _send(session, _delta("**Planning**\n\n"))
    await _send(session, _delta("I need "))  # within the interval: held
    await _send(session, _delta("the train times."))
    assert [e.text for e in bus.reasoning()] == ["**Planning**\n\n"]

    clock.now += live_session.REASONING_SNAPSHOT_INTERVAL_S + 0.01
    await _send(session, _delta(" Then"))
    snapshots = bus.reasoning()
    assert snapshots[-1].text == "**Planning**\n\nI need the train times. Then"
    assert snapshots[-1].done is False
    assert snapshots[-1].response_id == "rs_1"
    assert snapshots[-1].source_layer == "live.delegation"


async def test_a_part_done_publishes_the_whole_summary_so_far(clock: _Clock) -> None:
    bus = _Bus()
    session = _session(bus)
    await _send(session, _delta("**Reading**\n\nThe user wants trains.", index=0))
    await _send(
        session,
        {
            "type": "response.reasoning_summary_text.done",
            "item_id": "rs_1",
            "summary_index": 0,
            "text": "**Reading**\n\nThe user wants trains.",
        },
    )
    clock.now += 1.0
    await _send(session, _delta("**Searching**\n\nI query", index=1))
    await _send(
        session,
        {
            "type": "response.reasoning_summary_text.done",
            "item_id": "rs_1",
            "summary_index": 1,
            "text": "**Searching**\n\nI query the timetable.",
        },
    )
    final = bus.reasoning()[-1]
    assert final.done is True
    assert final.text == (
        "**Reading**\n\nThe user wants trains.\n\n**Searching**\n\nI query the timetable."
    )


async def test_the_finished_item_releases_its_stream_state(clock: _Clock) -> None:
    bus = _Bus()
    session = _session(bus)
    await _send(session, _delta("**Plan**\n\nStep one."))
    await _send(
        session,
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "**Plan**\n\nStep one."}],
            },
        },
    )
    assert session._reasoning_parts == {}  # noqa: SLF001
    assert bus.reasoning()[-1].done is True


async def test_unfinished_streams_are_bounded(clock: _Clock) -> None:
    bus = _Bus()
    session = _session(bus)
    for number in range(live_session._REASONING_ITEMS_MAX + 5):  # noqa: SLF001
        await _send(
            session,
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": f"rs_{number}",
                "delta": "thinking",
            },
        )
    assert len(session._reasoning_parts) == live_session._REASONING_ITEMS_MAX  # noqa: SLF001


def test_the_trace_ring_keeps_one_entry_per_streaming_summary() -> None:
    collector = TurnTraceCollector()
    for text in ("**Plan**", "**Plan**\n\nOne.", "**Plan**\n\nOne. Two."):
        collector.record(
            "ReasoningSummaryUpdated",
            ReasoningSummaryUpdated(response_id="rs_1", text=text, done=False),
        )
    collector.record(
        "ReasoningSummaryUpdated",
        ReasoningSummaryUpdated(response_id="rs_1", text="**Plan**\n\nOne. Two. Three.", done=True),
    )
    collector.record(
        "ReasoningSummaryUpdated",
        ReasoningSummaryUpdated(response_id="rs_2", text="Other.", done=False),
    )
    events = collector.slice(0)
    texts = [e["payload"]["text"] for e in events]
    assert texts == ["**Plan**\n\nOne. Two. Three.", "Other."]


class _Store:
    def __init__(self) -> None:
        self.kinds: list[tuple[str, dict]] = []

    def upsert_session(self, **_kwargs) -> None:
        return None

    def append_event(self, **kwargs) -> int:
        self.kinds.append((kwargs["kind"], kwargs["payload"]))
        return len(self.kinds)


async def test_the_recorder_keeps_only_the_finished_summary() -> None:
    store = _Store()
    recorder = SessionRecorder(store)  # type: ignore[arg-type]
    await recorder._on_event(  # noqa: SLF001
        VoiceSessionStarted(session_id="s1", wake_keyword="hey_jarvis", language="en")
    )
    await recorder._on_event(  # noqa: SLF001
        ReasoningSummaryUpdated(response_id="rs_1", text="**Plan**", done=False)
    )
    await recorder._on_event(  # noqa: SLF001
        ReasoningSummaryUpdated(response_id="rs_1", text="**Plan**\n\nDone.", done=True)
    )
    reasoning = [kind for kind, _payload in store.kinds if kind == "ReasoningSummaryUpdated"]
    assert reasoning == ["ReasoningSummaryUpdated"]
