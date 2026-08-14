"""ErrandAnnouncer — errand events become spoken words.

The voice half of the return path: subscribes to ``ErrandCompleted`` and
``ErrandNeedsInput`` on the global bus and publishes ``AnnouncementRequested``,
which the speech pipeline already knows how to deliver (scrubbing, barge-in,
hangup gate). Mirrors ``jarvis/missions/voice/announcer.py`` in shape, smaller
in scope: an errand has no Kontrollierer-signed summary, its honest artefact is
the verifier-gated ``outcome`` line (C3), so that is what gets spoken.

What is deliberately NOT announced:

- The opening clarification round (``mid_run=False``): the start_errand tool
  returns those questions inside the still-open turn and the brain asks them
  there. Speaking them again would ask the user everything twice.
- Cancellations: the surface that carried the user's "stop" already confirms
  it. The machine-readable ``ErrandCompleted`` still fires for automation.
"""

from __future__ import annotations

import logging
from typing import Literal

from jarvis.brain.output_filter import scrub_for_voice
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested, ErrandCompleted, ErrandNeedsInput

log = logging.getLogger(__name__)

ERRAND_ANNOUNCEMENT_SOURCE_LAYER = "errands.announcer"

_Lang = Literal["de", "en"]

#: Static frames per terminal status. Only the frame is canned; the substance
#: is the errand's own outcome line. German literals are runtime voice output,
#: the one surface where German is permitted (CLAUDE.md §1).
_FRAMES: dict[str, dict[_Lang, str]] = {
    "completed": {
        "de": "Dein Auftrag ist erledigt.",  # i18n-allow: real German TTS voice output
        "en": "Your errand is done.",
    },
    "stalled": {
        # i18n-allow: real German TTS voice output
        "de": "Dein Auftrag kommt gerade nicht weiter.",
        "en": "Your errand got stuck.",
    },
    "impossible": {
        # i18n-allow: real German TTS voice output
        "de": "Dein Auftrag ist so leider nicht machbar.",
        "en": "Your errand turned out not to be doable.",
    },
}

_NEEDS_INPUT_FRAME: dict[_Lang, str] = {
    # i18n-allow: real German TTS voice output
    "de": "Bei deinem Auftrag brauche ich kurz deine Hilfe.",
    "en": "Your errand needs you for a moment.",
}


class ErrandAnnouncer:
    """Global-bus subscriber turning errand outcomes into announcements."""

    def __init__(self, *, bus: EventBus, scrub: bool = True) -> None:
        self._bus = bus
        self._scrub = scrub

    def start(self) -> None:
        """Subscribe. Lives for the process — the global bus has no unsubscribe."""
        self._bus.subscribe(ErrandCompleted, self._on_completed)
        self._bus.subscribe(ErrandNeedsInput, self._on_needs_input)
        log.info("ErrandAnnouncer: bus subscriptions registered")

    async def _on_completed(self, event: ErrandCompleted) -> None:
        try:
            frame = _FRAMES.get(event.status)
            if frame is None:  # cancelled, or an unknown future status
                return
            await self._say(_compose(frame, event.language, event.outcome), event.language)
        except Exception:  # noqa: BLE001 — a broken announcer must never block the bus
            log.warning("ErrandAnnouncer failed on completion", exc_info=True)

    async def _on_needs_input(self, event: ErrandNeedsInput) -> None:
        try:
            if not event.mid_run:
                return  # the opening round is asked inside the open turn
            questions = " ".join(event.questions.splitlines()).strip()
            await self._say(
                _compose(_NEEDS_INPUT_FRAME, event.language, questions), event.language
            )
        except Exception:  # noqa: BLE001
            log.warning("ErrandAnnouncer failed on needs-input", exc_info=True)

    async def _say(self, text: str, language: str) -> None:
        if self._scrub:
            scrubbed = scrub_for_voice(text, language=language)
            text = scrubbed.cleaned
        if not text.strip():
            return
        await self._bus.publish(
            AnnouncementRequested(
                source_layer=ERRAND_ANNOUNCEMENT_SOURCE_LAYER,
                text=text,
                # AD-OE5: never barge in mid-utterance — queue for the next
                # natural turn boundary. AD-OE6: still spoken, no silent drop.
                priority="normal",
                language=language,
                # "subagent" is the readback kind that punches through the
                # pipeline's hangup gate: a booking that finishes after the
                # user said goodbye is still the answer they asked for.
                kind="subagent",
            )
        )


def _compose(frame: dict[_Lang, str], language: str, substance: str) -> str:
    """Frame in the errand's language (en fallback for other locales) plus the
    outcome line. The substance is model text and may arrive in another
    language — spoken faithfully rather than silently dropped."""
    lead = frame["de"] if language == "de" else frame["en"]
    substance = substance.strip()
    return f"{lead} {substance}".strip() if substance else lead


__all__ = ["ERRAND_ANNOUNCEMENT_SOURCE_LAYER", "ErrandAnnouncer"]
