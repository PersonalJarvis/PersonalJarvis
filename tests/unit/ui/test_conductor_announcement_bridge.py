"""Conductor news must arrive as spoken words, in the user's language.

Audit AU-01, the Jarvis half: Conductor decides WHETHER a finished run is news
(``conductor.core.notify``, pinned in ``tests/unit/conductor/test_notify.py``);
this bridge decides WHAT IS SAID and in WHICH LANGUAGE, and publishes it as an
``AnnouncementRequested`` — the same event the skill/task/workflow layers use.

Two contracts live here:

* the wording is plain user language and never a job id, exit code, or stack
  trace (the technical reason rides in ``detail``, which is never spoken);
* the language comes from the ONE resolver (``resolve_output_language``), so a
  German conversation is never told about a broken job in English.

Real ``EventBus``, real event class — no mocks.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from conductor import NEWS_EVENT
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.ui.desktop_app import _make_conductor_announcer


class _Heard:
    """Everything the voice layer would have said."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[AnnouncementRequested] = []
        bus.subscribe(AnnouncementRequested, self._on)

    async def _on(self, event: AnnouncementRequested) -> None:
        self.events.append(event)

    @property
    def last(self) -> AnnouncementRequested:
        assert self.events, "expected an announcement, heard nothing"
        return self.events[-1]


def _bridge(
    bus: EventBus,
    *,
    pin: str = "auto",
    conversation_language: str = "",
) -> Any:
    brain = SimpleNamespace(conversation_language=conversation_language)
    return _make_conductor_announcer(bus, lambda: brain, lambda: pin)


def _failing(job_name: str = "GitHub-API Zen", detail: str = "") -> dict[str, Any]:
    return {
        "job_id": "7b4d7e01-5c11-4c57-9c1d-10cc00000001",
        "job_name": job_name,
        "kind": "failing",
        "state": "failed",
        "run_id": "run-1",
        "trigger": "interval",
        "detail": detail,
    }


async def test_a_job_that_starts_failing_is_spoken_in_plain_words() -> None:
    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus)(NEWS_EVENT, _failing(detail="status 500 does not match '2xx'"))

    said = heard.last.text
    assert said == "The scheduled job GitHub-API Zen has started failing."
    # No job id, no exit code, no stack trace in what the user hears.
    assert "7b4d7e01" not in said
    assert "500" not in said
    # The technical reason survives — on the transcript track, never spoken.
    assert heard.last.detail == "status 500 does not match '2xx'"


async def test_recovery_is_spoken_too() -> None:
    bus = EventBus()
    heard = _Heard(bus)
    payload = _failing() | {"kind": "recovered", "state": "completed", "detail": ""}
    await _bridge(bus)(NEWS_EVENT, payload)

    assert heard.last.text == "The scheduled job GitHub-API Zen is working again."
    assert heard.last.detail is None


async def test_a_german_pin_is_answered_in_german() -> None:
    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus, pin="de")(NEWS_EVENT, _failing())

    assert heard.last.language == "de"
    # i18n-allow: the German voice output IS the fixture under test.
    assert heard.last.text == "Der geplante Job GitHub-API Zen schlägt gerade fehl."


async def test_an_ongoing_spanish_conversation_keeps_its_language() -> None:
    """No pin, no user turn to read a language off — the conversation decides."""
    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus, conversation_language="es")(NEWS_EVENT, _failing())

    assert heard.last.language == "es"
    assert heard.last.text == "El trabajo programado GitHub-API Zen está fallando."


async def test_a_pin_beats_the_conversation_language() -> None:
    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus, pin="en", conversation_language="de")(NEWS_EVENT, _failing())

    assert heard.last.language == "en"


async def test_the_announcement_survives_a_hangup() -> None:
    """A job that breaks at 03:00 has to reach a user with no session open, so
    it must carry a readback kind (the pipeline's ``_READBACK_KINDS``)."""
    from jarvis.speech.pipeline import _READBACK_KINDS

    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus)(NEWS_EVENT, _failing())

    assert heard.last.kind in _READBACK_KINDS
    # Normal priority: news, not an emergency that talks over the user.
    assert heard.last.priority == "normal"


async def test_run_lifecycle_events_stay_out_of_the_voice() -> None:
    """``run.started`` / ``run.finished`` belong to the dashboard. If those were
    spoken, the 5-minute healthcheck alone would talk 288 times a day."""
    bus = EventBus()
    heard = _Heard(bus)
    bridge = _bridge(bus)
    for name in ("run.started", "run.finished", "run.failed"):
        await bridge(name, _failing())

    assert heard.events == []


async def test_an_unknown_kind_says_nothing_rather_than_something_wrong() -> None:
    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus)(NEWS_EVENT, _failing() | {"kind": "sideways"})

    assert heard.events == []


async def test_a_nameless_job_says_nothing() -> None:
    """An announcement that cannot name the job is worse than silence."""
    bus = EventBus()
    heard = _Heard(bus)
    await _bridge(bus)(NEWS_EVENT, _failing(job_name="   "))

    assert heard.events == []


async def test_a_broken_bus_never_reaches_the_conductor_loop() -> None:
    class _BrokenBus:
        def publish(self, event: Any) -> None:
            raise RuntimeError("bus is down")

    bridge = _make_conductor_announcer(
        _BrokenBus(), lambda: None, lambda: "auto"
    )
    # Must not raise — a job result is never worth taking the scheduler down.
    await bridge(NEWS_EVENT, _failing())


async def test_an_unreadable_brain_still_gets_the_news_out() -> None:
    def _explode() -> Any:
        raise RuntimeError("brain not built yet")

    bus = EventBus()
    heard = _Heard(bus)
    bridge = _make_conductor_announcer(bus, _explode, lambda: "auto")
    await bridge(NEWS_EVENT, _failing())

    assert heard.last.language == "en"


@pytest.mark.parametrize("language", ["de", "en", "es"])
def test_every_supported_locale_has_both_sentences(language: str) -> None:
    from jarvis.ui.desktop_app import _conductor_news_sentence

    for kind in ("failing", "recovered"):
        text = _conductor_news_sentence(kind, "Nightly Backup", language)
        assert text.endswith(".")
        assert "Nightly Backup" in text
        assert "{" not in text
