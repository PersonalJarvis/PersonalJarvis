"""Tests fuer ``MissionAnnouncer`` — Mission-Bus -> Speech-Bus-Bridge.

AD-17: Mission-Notifications laufen ueber den existierenden
``AnnouncementRequested``-Event-Pfad. Der Announcer ist eine ergaenzende,
additive Komponente neben ``MissionVoiceListener``; beide haengen am
selben MissionBus, aber dieser hier publisht auf den globalen
``EventBus`` statt direkt eine TTS-Funktion zu rufen.
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
    assert "Alex" not in ann.text
    assert "Sir" not in ann.text
    assert ann.language == "de"
    assert ann.priority == "normal"


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
# Failure / Cancel / Timeout: priority + Sprache
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
    assert "fehlgeschlagen" in captured[0].text


@pytest.mark.asyncio
async def test_failed_critic_unavailable_german_phrasing(store_and_bus) -> None:
    """Live forensic 2026-05-16 — the `critic_unavailable` reason must map to
    the German phrase that tells the user the worker succeeded and the
    work survives in the worktree (not the generic "fehlgeschlagen" cue
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
    assert "Prüfer" in captured[0].text
    assert "abgestürzt" in captured[0].text


@pytest.mark.asyncio
async def test_crash_recovery_is_not_announced(store_and_bus) -> None:
    """Boot-recovery housekeeping must be SILENT. On every startup
    ``startup_recover`` marks each still-in-flight mission FAILED with
    reason ``crash_recovery`` and emits a MissionFailed. Those missions
    were dispatched by voice in a PRIOR session, so ``is_voice`` is True and
    the announcer would otherwise barge in with "Die Mission ist
    fehlgeschlagen." at interrupt priority — the user's "random Mission
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
# Filter: ui-source darf nicht piepsen
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
