"""RealtimeSessionPostmortem: end() publishes one content-free counter event.

The postmortem is the only place a live call's transport health survives the
call (the wildcard flight recorder persists it). Pinned here: exactly one
event per session with the adapter's counters, accumulation across a
transport swap — the provider session OBJECT is replaced on a rebuild and its
counters would otherwise die with it — and honest ``close_clean`` reporting
when teardown has to abandon a hanging provider socket.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import RealtimeSessionPostmortem
from jarvis.realtime.session import RealtimeVoiceSession

TIMEOUT_S = 5.0
RATE = 24_000


class _Wire:
    """Codex-shaped provider session carrying postmortem diagnostics."""

    session_id = "postmortem-wire"
    creates_responses_automatically = True
    isolates_response_generations = True
    supports_direct_tools = False
    supports_tool_updates = False
    direct_speech_is_authoritative = True
    rebuild_on_transport_death = True

    def __init__(self, diag: dict[str, int] | None = None) -> None:
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._diag = dict(diag or {})
        self.closed = False
        self.close_hangs = False

    def diagnostics(self) -> dict[str, int]:
        return dict(self._diag)

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def receive(self):  # noqa: ANN201 - async generator, protocol shape
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, *, required_tool: str | None = None) -> None:
        del required_tool

    async def send_text(self, text: str) -> None:
        del text

    async def send_speech(self, text: str) -> None:
        del text

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        if self.close_hangs:
            await asyncio.sleep(30.0)
        self.closed = True
        self._events.put_nowait(None)


class _Provider:
    name = "codex-subscription"
    supports_realtime = True
    input_sample_rate = RATE
    output_sample_rate = RATE

    def __init__(self, wire: _Wire) -> None:
        self._wire = wire

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _Wire:
        del config
        return self._wire


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            reply_language="en",
            providers={"codex-subscription": SimpleNamespace(model="", voice="cove")},
        ),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
    )


async def _open(wire: _Wire) -> tuple[RealtimeVoiceSession, list[RealtimeSessionPostmortem]]:
    bus = EventBus()
    seen: list[RealtimeSessionPostmortem] = []

    async def _collect(event: RealtimeSessionPostmortem) -> None:
        seen.append(event)

    bus.subscribe(RealtimeSessionPostmortem, _collect)
    session = RealtimeVoiceSession(
        session_id="postmortem-session",
        send_binary=lambda data: asyncio.sleep(0),
        send_json=lambda message: asyncio.sleep(0),
        providers=[_Provider(wire)],
        config=_config(),
        bus=bus,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
    )
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": RATE}),
        TIMEOUT_S,
    )
    return session, seen


async def test_end_publishes_one_postmortem_with_adapter_counters() -> None:
    wire = _Wire(
        {
            "ungrounded_responses_refused": 3,
            "ungrounded_captions_dropped": 2,
            "quiescence_boundary_turns": 2,
            "sender_pacing_resyncs": 1,
        }
    )
    session, seen = await _open(wire)
    session._handoff_action_turns = 2  # noqa: SLF001 - postmortem inputs
    session._handoff_requests = 1  # noqa: SLF001 - postmortem inputs
    session._handoff_delegate_dispatches = 2  # noqa: SLF001 - postmortem inputs
    session._handoff_declines = 1  # noqa: SLF001 - postmortem inputs
    session._public_fact_grounding_attempts = 2  # noqa: SLF001
    session._public_fact_grounding_successes = 1  # noqa: SLF001
    session._public_fact_grounding_failures = 1  # noqa: SLF001
    session._output_language_mismatches = 2  # noqa: SLF001
    session._output_language_retries = 1  # noqa: SLF001
    session._output_language_failures = 1  # noqa: SLF001
    session._delegate_delivery_claims = 2  # noqa: SLF001
    session._delegate_deliveries_completed = 1  # noqa: SLF001
    session._delegate_delivery_recoveries = 1  # noqa: SLF001
    session._delegate_delivery_duplicates_suppressed = 1  # noqa: SLF001
    session._delegate_deliveries_detached = 1  # noqa: SLF001

    await asyncio.wait_for(session.end(reason="hotkey"), TIMEOUT_S)

    assert len(seen) == 1, "exactly one postmortem per session"
    event = seen[0]
    assert event.provider == "codex-subscription"
    assert event.hangup_reason == "hotkey"
    assert event.ungrounded_responses_refused == 3
    assert event.ungrounded_captions_dropped == 2
    assert event.quiescence_boundary_turns == 2
    assert event.sender_pacing_resyncs == 1
    assert event.handoff_action_turns == 2
    assert event.handoff_requests == 1
    assert event.handoff_delegate_dispatches == 2
    assert event.handoff_declines == 1
    assert event.handoff_obligation_misses == 1
    assert event.public_fact_grounding_attempts == 2
    assert event.public_fact_grounding_successes == 1
    assert event.public_fact_grounding_failures == 1
    assert event.output_language_mismatches == 2
    assert event.output_language_retries == 1
    assert event.output_language_failures == 1
    assert event.delegate_delivery_claims == 2
    assert event.delegate_deliveries_completed == 1
    assert event.delegate_delivery_recoveries == 1
    assert event.delegate_delivery_duplicates_suppressed == 1
    assert event.delegate_deliveries_detached == 1
    assert event.ready_ms >= 0
    assert event.close_clean is True

    # A second end() must not publish a second postmortem.
    await asyncio.wait_for(session.end(reason="hotkey"), TIMEOUT_S)
    assert len(seen) == 1


async def test_postmortem_accumulates_across_a_transport_swap() -> None:
    """A rebuild replaces the provider session object; its counters must not
    vanish with it — the harvest folds them into the accumulator the final
    postmortem reads."""
    dead_wire = _Wire({"response_splices": 2, "ungrounded_responses_refused": 4})
    live_wire = _Wire({"response_splices": 1})
    session, seen = await _open(live_wire)

    session._harvest_adapter_diagnostics(dead_wire)  # noqa: SLF001 — the rebuild path's harvest
    await asyncio.wait_for(session.end(reason="error"), TIMEOUT_S)

    assert len(seen) == 1
    assert seen[0].response_splices == 3
    assert seen[0].ungrounded_responses_refused == 4


async def test_a_hanging_provider_close_reports_unclean() -> None:
    wire = _Wire()
    wire.close_hangs = True
    session, seen = await _open(wire)

    await asyncio.wait_for(session.end(reason="hotkey"), TIMEOUT_S)

    assert len(seen) == 1
    assert seen[0].close_clean is False, (
        "an abandoned provider socket is not a clean close"
    )
