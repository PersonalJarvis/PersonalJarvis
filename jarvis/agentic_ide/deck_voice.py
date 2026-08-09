"""Give the Command Deck's report queue a voice — EventBus <-> :mod:`.standup`.

:mod:`.standup` decides what should be said and when. It deliberately knows
nothing about how speech works: it runs inside the pane sweep, which has to keep
working in a headless install, in the tests, and on a machine with no audio at
all. This module is the one place the two meet.

Modelled on :class:`jarvis.missions.voice.announcer.MissionAnnouncer`, and for
the same reasons it exists: an ``AnnouncementRequested`` on the speech bus gets
``scrub_for_voice``, the barge-in rules, the transcript's spoken track and the
TTS provider for free, and it is the path every other unprompted line in this
app already takes. A second, private TTS route would be a second set of edge
cases to get wrong.

Three things travel the other way, and all three are READ rather than derived:

* **The language.** One resolver decides a turn's output language for every
  layer (CLAUDE.md §1, ``core/turn_language.py``). This module never calls it —
  it caches what the turn already resolved to, off ``ResponseGenerated`` and
  ``VoiceSessionStarted``. Re-deriving it here is exactly the divergence the
  doctrine exists to stop.
* **Whether a conversation is open.** The deck talks to a person who is
  listening. Outside a session the queue fills and says nothing, and the next
  session opens with one "while you were away" line.
* **Hanging up.** ``VoiceSessionEnded`` ends the conversation here too. Speech
  itself is already dead by then — the killswitch is the pipeline's contract,
  not ours — but the queue must stop offering, or it would greet the next
  session with a report from an hour ago as though it were news.

Everything is best-effort. A bus subscriber that raises would take a voice turn
down with it (AP-18), and none of this is worth that: the worst honest outcome
is a deck whose lane fills up silently, which is exactly what an install with no
speech stack gets anyway (§3).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jarvis.core.events import (
    AnnouncementRequested,
    ResponseGenerated,
    VoiceSessionEnded,
    VoiceSessionStarted,
)

from . import standup

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.bus import EventBus

logger = logging.getLogger(__name__)

#: What the pipeline is told this line is. ``info`` rather than ``completion``:
#: a completion is the answer to something the user asked for in this turn, and
#: a deck report is news arriving on its own. The distinction drives the
#: pipeline's own idle-window and barge-in handling.
ANNOUNCEMENT_KIND = "info"

#: Fallback when nothing has been spoken yet in this process. Only reachable
#: for a report that lands before the user's first answer, which is a very
#: short window and not worth guessing about.
DEFAULT_LANGUAGE = "en"


class DeckVoice:
    """Subscribes on ``start()``, publishes announcements, remembers nothing else."""

    def __init__(self, bus: EventBus, *, composer: Any = None) -> None:
        self._bus = bus
        self._composer = composer
        self._language = DEFAULT_LANGUAGE
        # (event type, handler) pairs rather than unsubscribe callables: this
        # bus's `subscribe` returns None and `unsubscribe` takes both halves
        # back. Kept so `stop()` is a real detach — a bridge that only stopped
        # publishing would still be holding a dead handler on a live bus.
        self._bound: list[tuple[type[Any], Any]] = []
        self._started = False

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Wire the queue to this bus. Idempotent."""
        if self._started:
            return
        try:
            for event_type, handler in (
                (VoiceSessionStarted, self._on_started),
                (VoiceSessionEnded, self._on_ended),
                (ResponseGenerated, self._on_response),
            ):
                self._bus.subscribe(event_type, handler)
                self._bound.append((event_type, handler))
        except Exception as exc:  # noqa: BLE001 - an optional surface never breaks boot
            logger.warning("Command Deck voice not subscribed: %s", exc)
            self._unsubscribe()
            return
        standup.wire(
            speak=self._speak,
            language=lambda: self._language,
            composer=self._composer,
        )
        self._started = True

    def stop(self) -> None:
        """Unsubscribe and take the queue's voice away. Idempotent."""
        self._unsubscribe()
        standup.unwire()
        self._started = False

    def _unsubscribe(self) -> None:
        for event_type, handler in self._bound:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.debug("Command Deck voice: unsubscribe failed", exc_info=True)
        self._bound.clear()

    # ---------------------------------------------------------------- events

    async def _on_started(self, event: VoiceSessionStarted) -> None:
        self._remember_language(getattr(event, "language", ""))
        standup.queue().conversation_started()

    async def _on_ended(self, event: VoiceSessionEnded) -> None:
        _ = event
        standup.queue().conversation_ended()

    async def _on_response(self, event: ResponseGenerated) -> None:
        """Cache the language the turn resolved to, and wake the queue.

        Both halves matter. The language is read here rather than resolved
        because one resolver owns that decision. And an answer means the user
        is engaged with the deck right now, which is precisely when a queue
        that went quiet after an unanswered line should be allowed to speak
        again (``standup`` rule 8).
        """
        self._remember_language(getattr(event, "language", ""))
        queue = standup.queue()
        if queue.in_conversation():
            queue.wake()

    def _remember_language(self, language: object) -> None:
        text = str(language or "").strip().lower()[:2]
        if text:
            self._language = text

    # ----------------------------------------------------------------- speak

    async def _speak(self, text: str, language: str) -> None:
        """Publish one report. Never ``interrupt`` — see ``standup`` rule 5."""
        line = (text or "").strip()
        if not line:
            return
        try:
            await self._bus.publish(
                AnnouncementRequested(
                    text=line,
                    priority="normal",
                    language=language or self._language,
                    kind=ANNOUNCEMENT_KIND,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a mute deck beats a dead sweep
            logger.warning("Command Deck report not announced: %s", exc)


def attach(bus: EventBus, *, composer: Any = None) -> DeckVoice:
    """Build and start the bridge — the one call a host needs."""
    voice = DeckVoice(bus, composer=composer)
    voice.start()
    return voice


__all__ = ["ANNOUNCEMENT_KIND", "DEFAULT_LANGUAGE", "DeckVoice", "attach"]
