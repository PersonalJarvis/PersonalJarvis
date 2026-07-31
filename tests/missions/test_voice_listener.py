"""Integration tests for MissionVoiceListener.

Verifies:
- Filter: voice-source -> TTS, ui-source -> no TTS.
- Event routing: MissionApproved -> render_approved, MissionFailed -> render_failed, ...
- BudgetWarning pct routing: 50 vs 80.
- WorkerKilled reason routing: injection_detected vs path_guard.
- Cache: a second event on the same mission does not call the store again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.missions.event_bus import MissionBus
from jarvis.missions.event_store import MissionEventStore
from jarvis.missions.events import (
    EventEnvelope,
    MissionApproved,
    MissionBudgetWarning,
    MissionDispatched,
    MissionFailed,
    MissionStateChanged,
    MissionTimedOut,
    WorkerKilled,
    now_ms,
)
from jarvis.missions.ids import uuid7_str
from jarvis.missions.voice.listener import MissionVoiceListener
from jarvis.missions.voice.readback import MissionReadback


# --- Helpers ---


class _CapturingTTS:
    """Speichert alle TTS-Calls als (text, lang)-Tuples."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, text: str, lang: str) -> None:
        self.calls.append((text, lang))


@pytest.fixture
async def store_and_bus(tmp_missions_db: Path):
    bus = MissionBus()
    store = MissionEventStore(tmp_missions_db, bus)
    await store.open()
    yield store, bus
    await store.close()


async def _seed_voice_mission(store: MissionEventStore, *, language: str = "de") -> str:
    """Legt eine voice-getriggerte Mission im Store an. Returns mission_id."""
    mid = uuid7_str()
    env = EventEnvelope(
        mission_id=mid,
        source_actor="hauptjarvis",
        ts_ms=now_ms(),
        payload=MissionDispatched(prompt="test mission", language=language),  # type: ignore[arg-type]
    )
    await store.upsert_mission(
        mission_id=mid, prompt="test mission", state="PENDING",
        language=language, ts_ms=now_ms(),
    )
    await store.append_and_publish(env)
    return mid


async def _seed_ui_mission(store: MissionEventStore) -> str:
    """Legt eine UI-getriggerte Mission an. Returns mission_id."""
    mid = uuid7_str()
    env = EventEnvelope(
        mission_id=mid,
        source_actor="ui",
        ts_ms=now_ms(),
        payload=MissionDispatched(prompt="ui task"),
    )
    await store.upsert_mission(
        mission_id=mid, prompt="ui task", state="PENDING",
        language="de", ts_ms=now_ms(),
    )
    await store.append_and_publish(env)
    return mid


def _make_listener(
    *,
    store: MissionEventStore,
    bus: MissionBus,
    tts: _CapturingTTS,
    announce_critic_loop: bool = False,
) -> MissionVoiceListener:
    return MissionVoiceListener(
        bus=bus, store=store, readback=MissionReadback(),
        tts_speak_fn=tts, announce_critic_loop=announce_critic_loop,
    )


# --- Filter: voice vs ui ---


@pytest.mark.asyncio
async def test_voice_mission_triggers_tts(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()

    mid = await _seed_voice_mission(store)
    env = EventEnvelope(
        mission_id=mid, source_actor="kontrollierer", ts_ms=now_ms(),
        payload=MissionApproved(
            result_uri=f"mission://{mid}", tokens_used=100, cost_usd=0.05,
            wall_ms=1000, summary_de="Aufgabe erledigt.", summary_en="Done.",
        ),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, lang = tts.calls[-1]
    # Name-neutral contract: the readback carries the approved status phrase
    # and the signed summary, no owner name, never "Sir". scrub_for_voice
    # (F-AUDIT-2) is applied on top — "Sir" would be scrubbed anyway.
    assert "Fertig" in text or "Erledigt" in text or "Abgeschlossen" in text  # i18n-allow: asserts the German TTS readback text
    assert "erledigt" in text.lower()  # the signed summary survives
    assert "Alex" not in text
    assert "Sir" not in text
    assert lang == "de"


@pytest.mark.asyncio
async def test_ui_mission_does_not_trigger_tts(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()

    mid = await _seed_ui_mission(store)
    env = EventEnvelope(
        mission_id=mid, source_actor="kontrollierer", ts_ms=now_ms(),
        payload=MissionApproved(
            result_uri=f"mission://{mid}", tokens_used=100, cost_usd=0.05,
            wall_ms=1000, summary_de="Done.", summary_en="Done.",
        ),
    )
    await store.append_and_publish(env)

    # No voice call for UI source
    voice_calls = [c for c in tts.calls if c[1] in ("de", "en")]
    # Filter: only mission-approval calls (not dispatch events)
    assert len(voice_calls) == 0, f"Unexpected TTS calls: {voice_calls}"


# --- Event-Routing per Payload-Type ---


@pytest.mark.asyncio
async def test_failed_routes_to_render_failed(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, source_actor="kontrollierer", ts_ms=now_ms(),
        payload=MissionFailed(
            reason="critic_loop_exhausted", last_state="CRITIQUING", partial_artifacts=[],
        ),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    # Name-neutral contract: failure phrasing, no owner name, never "Sir".
    assert "gescheitert" in text.lower() or "fehl" in text.lower()
    assert "Alex" not in text
    assert "Sir" not in text


@pytest.mark.asyncio
async def test_timeout_routes_to_render_timeout(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, source_actor="system", ts_ms=now_ms(),
        payload=MissionTimedOut(deadline_ms=now_ms(), last_progress_ms=0),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    assert "timeout" in text.lower() or "zeit" in text.lower()


# --- BudgetWarning pct routing ---


@pytest.mark.asyncio
async def test_budget_warn_50_routes_correctly(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, source_actor="system", ts_ms=now_ms(),
        payload=MissionBudgetWarning(mission_id=mid, pct_used=50.0, limit_usd=5.0),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    assert "halb" in text.lower() or "fuenfzig" in text.lower() or "50" in text
    assert "Alex" not in text
    assert "Sir" not in text


@pytest.mark.asyncio
async def test_budget_warn_80_routes_correctly(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, source_actor="system", ts_ms=now_ms(),
        payload=MissionBudgetWarning(mission_id=mid, pct_used=82.0, limit_usd=5.0),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    assert "achtzig" in text.lower() or "80" in text or "knapp" in text.lower()
    assert "Alex" not in text
    assert "Sir" not in text


# --- WorkerKilled reason routing ---


@pytest.mark.asyncio
async def test_worker_killed_injection_routes(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, worker_id="w1", source_actor="kontrollierer", ts_ms=now_ms(),
        payload=WorkerKilled(worker_id="w1", reason="injection_detected"),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    assert "injection" in text.lower() or "verdaecht" in text.lower()


@pytest.mark.asyncio
async def test_worker_killed_budget_routes(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, worker_id="w1", source_actor="kontrollierer", ts_ms=now_ms(),
        payload=WorkerKilled(worker_id="w1", reason="budget"),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    assert "budget" in text.lower() or "limit" in text.lower()


# --- announce_critic_loop flag ---


@pytest.mark.asyncio
async def test_correction_required_silent_by_default(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts, announce_critic_loop=False)
    await listener.start()
    mid = await _seed_voice_mission(store)

    from jarvis.missions.events import WorkerCorrectionRequired
    env = EventEnvelope(
        mission_id=mid, worker_id="w1", source_actor="critic", ts_ms=now_ms(),
        payload=WorkerCorrectionRequired(
            worker_id="w1", correction_instruction="fix the bug",
            iteration=0, next_model="sonnet",
        ),
    )
    await store.append_and_publish(env)

    # Default off -> no TTS call for correction
    correction_calls = [c for c in tts.calls if "iteration" in c[0].lower()]
    assert correction_calls == []


@pytest.mark.asyncio
async def test_correction_required_speaks_when_opted_in(store_and_bus) -> None:
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts, announce_critic_loop=True)
    await listener.start()
    mid = await _seed_voice_mission(store)

    from jarvis.missions.events import WorkerCorrectionRequired
    env = EventEnvelope(
        mission_id=mid, worker_id="w1", source_actor="critic", ts_ms=now_ms(),
        payload=WorkerCorrectionRequired(
            worker_id="w1", correction_instruction="fix the bug",
            iteration=0, next_model="sonnet",
        ),
    )
    await store.append_and_publish(env)

    assert tts.calls
    # Iteration 0+1 = 1 (display)
    text, _ = tts.calls[-1]
    assert "iteration" in text.lower() or "versuch" in text.lower()


# --- LLM-Narrative-Leak Defense ---


@pytest.mark.asyncio
async def test_correction_instruction_never_in_voice_output(store_and_bus) -> None:
    """ADR-0009 §1: correction_instruction (LLM output) must NEVER end up in TTS."""
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts, announce_critic_loop=True)
    await listener.start()
    mid = await _seed_voice_mission(store)

    from jarvis.missions.events import WorkerCorrectionRequired
    secret_instruction = "RAW_LLM_NARRATIVE_TOKEN_xyz123"
    env = EventEnvelope(
        mission_id=mid, worker_id="w1", source_actor="critic", ts_ms=now_ms(),
        payload=WorkerCorrectionRequired(
            worker_id="w1", correction_instruction=secret_instruction,
            iteration=0, next_model="sonnet",
        ),
    )
    await store.append_and_publish(env)

    for text, _ in tts.calls:
        assert secret_instruction not in text, "LLM-Narrative leaked into voice"


# --- Cache verifier ---


@pytest.mark.asyncio
async def test_voice_meta_cached_per_mission(store_and_bus) -> None:
    """A second event on the same mission triggers NO new store query."""
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    # First event populates cache
    await store.append_and_publish(EventEnvelope(
        mission_id=mid, source_actor="system", ts_ms=now_ms(),
        payload=MissionStateChanged(from_state="PENDING", to_state="RUNNING", reason="x"),
    ))

    cached_pre = dict(listener._mission_voice_cache)
    assert mid in cached_pre

    # Second event reuses cache (we can't directly verify "no DB call" but the
    # cache stays consistent)
    await store.append_and_publish(EventEnvelope(
        mission_id=mid, source_actor="kontrollierer", ts_ms=now_ms(),
        payload=MissionApproved(
            result_uri=f"mission://{mid}", tokens_used=10, cost_usd=0.01,
            wall_ms=100, summary_de="ok", summary_en="ok",
        ),
    ))

    assert listener._mission_voice_cache == cached_pre  # unchanged


# --- Listener-Crash-Defense ---


@pytest.mark.asyncio
async def test_tts_crash_does_not_block_bus(store_and_bus) -> None:
    """If the TTS fn raises, the bus must keep running."""
    store, bus = store_and_bus

    class _CrashingTTS:
        async def __call__(self, text: str, lang: str) -> None:
            raise RuntimeError("TTS provider dead")

    listener = MissionVoiceListener(
        bus=bus, store=store, readback=MissionReadback(),
        tts_speak_fn=_CrashingTTS(),
    )
    await listener.start()
    mid = await _seed_voice_mission(store)

    # Should NOT raise (listener catches it in the _on_event handler)
    await store.append_and_publish(EventEnvelope(
        mission_id=mid, source_actor="kontrollierer", ts_ms=now_ms(),
        payload=MissionApproved(
            result_uri=f"mission://{mid}", tokens_used=10, cost_usd=0.01,
            wall_ms=100, summary_de="ok", summary_en="ok",
        ),
    ))


# --- F-AUDIT-2: scrub_for_voice is active in the TTS path ---


@pytest.mark.asyncio
async def test_scrub_for_voice_is_applied_to_mission_readback(store_and_bus) -> None:
    """F-AUDIT-2 (audit 2026-04-29): MissionVoiceListener must
    apply scrub_for_voice to the readback template before TTS
    is called. Otherwise tool-use markup, engineering jargon,
    or A1 violations leaked unfiltered to the user.

    Realistic test trigger: a MissionApproved with summary_de that
    contains engineering jargon (e.g. "Subprocess fertig"). The filter  # i18n-allow: quotes the actual German jargon test fixture
    must scrub "Subprocess" as a standalone word — see
    JARGON_WORDS in jarvis/brain/output_filter.py.
    """
    store, bus = store_and_bus
    tts = _CapturingTTS()
    listener = _make_listener(store=store, bus=bus, tts=tts)
    await listener.start()
    mid = await _seed_voice_mission(store)

    env = EventEnvelope(
        mission_id=mid, source_actor="kontrollierer", ts_ms=now_ms(),
        payload=MissionApproved(
            result_uri=f"mission://{mid}", tokens_used=100, cost_usd=0.05,
            wall_ms=1000,
            summary_de="Der Subprocess ist fertig.",  # i18n-allow: German summary_de voice-readback fixture under test
            summary_en="The subprocess is done.",
        ),
    )
    await store.append_and_publish(env)

    assert tts.calls
    text, _ = tts.calls[-1]
    # Engineering jargon "Subprocess" was scrubbed — otherwise it would be
    # a standalone word in the output. The rest of the text is preserved.
    assert "Subprocess" not in text
    assert "subprocess" not in text.lower().split()
    # "Sir" darf NIE im Output sein (Mandat-A1)
    assert "Sir" not in text
