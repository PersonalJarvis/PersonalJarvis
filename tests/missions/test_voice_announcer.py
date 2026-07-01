"""Tests for ``MissionAnnouncer`` — mission-bus -> speech-bus bridge.

AD-17: mission notifications run through the existing
``AnnouncementRequested`` event path. The announcer is a complementary,
additive component alongside ``MissionVoiceListener``; both hang off the
same MissionBus, but this one publishes to the global
``EventBus`` instead of calling a TTS function directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.missions.event_bus import MissionBus
from jarvis.missions.event_store import MissionEventStore
from jarvis.missions.events import (
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionDispatched,
    MissionFailed,
    MissionTimedOut,
    now_ms,
)
from jarvis.missions.ids import uuid7_str
from jarvis.missions.voice.announcer import MissionAnnouncer


class _FakeAckProvider:
    """Minimal flash-provider stand-in for ReadbackComposer wiring tests."""

    def __init__(self, reply: str | None) -> None:
        self.reply = reply

    async def run(self, utterance: str, language: str, *, persona_prompt: str) -> str | None:
        return self.reply


def _composer_with(reply: str | None):
    from jarvis.brain.ack_brain import CircuitBreaker
    from jarvis.brain.ack_brain.config import AckBrainConfig
    from jarvis.voice.contextual_readback import ReadbackComposer

    return ReadbackComposer(
        provider=_FakeAckProvider(reply),
        config=AckBrainConfig(timeout_ms=1500),
        breaker=CircuitBreaker(threshold=3, cooldown_s=60),
    )


@pytest.fixture
async def store_and_bus(tmp_missions_db: Path):
    bus = MissionBus()
    store = MissionEventStore(tmp_missions_db, bus)
    await store.open()
    yield store, bus
    await store.close()


async def _seed_voice_mission(
    store: MissionEventStore, *, language: str = "de",
) -> str:
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


def _collect_announcements(speech_bus: EventBus) -> list[AnnouncementRequested]:
    captured: list[AnnouncementRequested] = []

    async def _on_ann(event: AnnouncementRequested) -> None:
        captured.append(event)

    speech_bus.subscribe(AnnouncementRequested, _on_ann)
    return captured


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approved_emits_announcement(store_and_bus) -> None:
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=f"mission://{mid}",
                tokens_used=100,
                cost_usd=0.05,
                wall_ms=1000,
                summary_de="Mission abgeschlossen.",
                summary_en="Mission completed.",
            ),
        )
    )

    assert len(captured) == 1
    ann = captured[0]
    # The announcer passes the Kontrollierer-signed summary_de through
    # verbatim (ADR-0009). The summary is name-neutral: it carries the
    # status phrase, no owner name, and never "Sir".
    assert ann.text == "Mission abgeschlossen."
    assert "Ruben" not in ann.text
    assert "Sir" not in ann.text
    assert ann.language == "de"
    assert ann.priority == "normal"


@pytest.mark.asyncio
async def test_approved_readback_is_contextually_rephrased(store_and_bus) -> None:
    """With a composer wired, the signed summary is spoken in a fresh phrasing
    (maintainer mandate: no fixed stock phrases) — but still faithfully."""
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(
        bus=bus, store=store, speech_bus=speech_bus,
        readback_composer=_composer_with("All done — the mission completed."),
    )
    await announcer.start()

    mid = await _seed_voice_mission(store, language="en")
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=f"mission://{mid}", tokens_used=100, cost_usd=0.05,
                wall_ms=1000, summary_de="Mission abgeschlossen.",
                summary_en="Mission completed.",
            ),
        )
    )

    assert len(captured) == 1
    text = captured[0].text
    assert text != "Mission completed."  # rephrased, not the canned summary
    assert "completed" in text.lower()  # but still faithful to the signed fact


@pytest.mark.asyncio
async def test_approved_falls_back_to_signed_summary_when_generation_fails(
    store_and_bus,
) -> None:
    """A composer whose provider yields nothing must fall back to the exact
    Kontrollierer-signed summary (ADR-0009 + AD-OE6 zero silent drops)."""
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(
        bus=bus, store=store, speech_bus=speech_bus,
        readback_composer=_composer_with(""),  # provider returns empty -> fallback
    )
    await announcer.start()

    mid = await _seed_voice_mission(store, language="de")
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=f"mission://{mid}", tokens_used=100, cost_usd=0.05,
                wall_ms=1000, summary_de="Mission abgeschlossen.",
                summary_en="Mission completed.",
            ),
        )
    )

    assert len(captured) == 1
    assert captured[0].text == "Mission abgeschlossen."


@pytest.mark.asyncio
async def test_approved_uses_summary_en_when_lang_en(store_and_bus) -> None:
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store, language="en")
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=f"mission://{mid}",
                tokens_used=100,
                cost_usd=0.05,
                wall_ms=1000,
                summary_de="Mission abgeschlossen.",
                summary_en="Mission completed.",
            ),
        )
    )

    assert len(captured) == 1
    assert captured[0].text == "Mission completed."
    assert captured[0].language == "en"


# ---------------------------------------------------------------------------
# Failure / Cancel / Timeout: priority + language
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_uses_normal_priority(store_and_bus) -> None:
    # AD-OE5 (2026-05-29): a failure announcement must NOT barge in
    # mid-utterance. priority="normal" queues it for the next turn-boundary
    # (still spoken — AD-OE6 — just not interrupting current speech). This,
    # with the silent spawn-ACK (2026-05-12), is why a failed background
    # mission used to feel like a "random" interruption.
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason="critic_loop_exhausted",
                last_state="CRITIQUING",
                partial_artifacts=[],
            ),
        )
    )

    assert len(captured) == 1
    assert captured[0].priority == "normal"
    assert "fehlgeschlagen" in captured[0].text  # i18n-allow: asserts the German TTS readback text


@pytest.mark.asyncio
async def test_failed_critic_unavailable_german_phrasing(store_and_bus) -> None:
    """Live forensic 2026-05-16 — the `critic_unavailable` reason must map to
    the German phrase that tells the user the worker succeeded and the
    work survives in the worktree (not the generic "fehlgeschlagen" cue  # i18n-allow: quotes the actual German TTS readback phrase
    that would suggest the worker itself failed)."""
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason="critic_unavailable",
                last_state="CRITIQUING",
                partial_artifacts=["C:/tmp/diff.iter0.patch"],
            ),
        )
    )

    assert len(captured) == 1
    assert captured[0].priority == "normal"
    assert "Prüfer" in captured[0].text  # i18n-allow: asserts the German TTS readback text
    assert "abgestürzt" in captured[0].text  # i18n-allow: asserts the German TTS readback text


@pytest.mark.asyncio
async def test_crash_recovery_is_not_announced(store_and_bus) -> None:
    """Boot-recovery housekeeping must be SILENT. On every startup
    ``startup_recover`` marks each still-in-flight mission FAILED with
    reason ``crash_recovery`` and emits a MissionFailed. Those missions
    were dispatched by voice in a PRIOR session, so ``is_voice`` is True and
    the announcer would otherwise barge in with "Die Mission ist
    fehlgeschlagen." at interrupt priority — the user's "random Mission  # i18n-allow: quotes the actual German TTS readback phrase
    fehlgeschlagen, although I never started one" complaint (deep-dive
    2026-05-29). crash_recovery is not actionable to the user; suppress it.
    """
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="system",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason="crash_recovery",
                error_class="OrchestratorCrash",
                last_state="RUNNING",
                partial_artifacts=[],
            ),
        )
    )

    assert captured == [], (
        "crash_recovery (swept-on-boot) must not be spoken — it is boot "
        "housekeeping, not a live failure"
    )


@pytest.mark.asyncio
async def test_failed_attempts_timed_out_speaks_honest_timeout(store_and_bus) -> None:
    """Live deep-dive 2026-06-07 (mission 019ea1da): a Computer-Use mission whose
    final iteration hit the 630s wall-clock cap was failed as ``task_error``, so
    the announcer spoke the generic "mission failed / worker aborted" phrase for
    what was really a timeout. The honest reason ``attempts_timed_out`` must
    produce a timeout phrase, never the alarming worker-abort wording."""
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason="attempts_timed_out",
                last_state="CRITIQUING",
                partial_artifacts=[],
            ),
        )
    )

    assert len(captured) == 1
    assert captured[0].priority == "normal"
    assert "Zeitlimit" in captured[0].text, (
        f"expected an honest timeout phrase, got {captured[0].text!r}"
    )
    assert "abgebrochen" not in captured[0].text.lower(), (
        f"a timeout must NOT be announced as a worker abort: {captured[0].text!r}"
    )


@pytest.mark.asyncio
async def test_interrupted_is_not_announced(store_and_bus) -> None:
    """'interrupted' is startup-sweep housekeeping — the same suppression rule
    that applies to 'crash_recovery' must also apply here. A mission swept as
    'interrupted' was dispatched in a prior session; the announcer must stay
    silent (no AnnouncementRequested on the speech bus) so the user is not
    woken up by a boot-time event."""
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="system",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason="interrupted",
                error_class="OrchestratorInterrupt",
                last_state="RUNNING",
                partial_artifacts=[],
            ),
        )
    )

    assert captured == [], (
        "interrupted (swept-on-boot) must not be spoken — it is boot "
        "housekeeping, not a live failure"
    )


@pytest.mark.asyncio
async def test_cancelled_emits_announcement(store_and_bus) -> None:
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionCancelled(reason="user_request", cascade=False),
        )
    )

    assert len(captured) == 1
    assert "abgebrochen" in captured[0].text.lower()


@pytest.mark.asyncio
async def test_timeout_uses_normal_priority(store_and_bus) -> None:
    # AD-OE5: timeout announcements also queue for the next turn-boundary
    # instead of barging in (see test_failed_uses_normal_priority).
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionTimedOut(deadline_ms=1000, last_progress_ms=500),
        )
    )

    assert len(captured) == 1
    assert captured[0].priority == "normal"


# ---------------------------------------------------------------------------
# Filter: ui-source must not beep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ui_source_does_not_emit(store_and_bus) -> None:
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()

    mid = await _seed_ui_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=f"mission://{mid}",
                tokens_used=100,
                cost_usd=0.05,
                wall_ms=1000,
                summary_de="Mission abgeschlossen.",
                summary_en="Mission completed.",
            ),
        )
    )

    assert len(captured) == 0


# ---------------------------------------------------------------------------
# Stop entfernt Subscription
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_unsubscribes(store_and_bus) -> None:
    store, bus = store_and_bus
    speech_bus = EventBus()
    captured = _collect_announcements(speech_bus)

    announcer = MissionAnnouncer(bus=bus, store=store, speech_bus=speech_bus)
    await announcer.start()
    announcer.stop()

    mid = await _seed_voice_mission(store)
    await store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=f"mission://{mid}",
                tokens_used=100,
                cost_usd=0.05,
                wall_ms=1000,
                summary_de="Done.",
                summary_en="Done.",
            ),
        )
    )

    assert len(captured) == 0
