"""Turn integration — the one call a conversation layer makes.

A brain/voice layer should not have to know about intent verdicts, capture
targets, TTL handles or redaction reports. It asks one question — "does this
turn need the screen, and if so, what does the model get?" — and receives a
:class:`TurnScreenContext` with four possible shapes it must handle.

Deliberately **additive**. This does NOT replace ``jarvis.brain.vision_gate``,
which also fires on on-screen ACTION turns ("click the button", "close this
window") so Computer-Use is not blind. Those are a different question: the
vision gate asks "would an image help this turn?", Screen Context asks "did the
user ask me to LOOK?". A turn can be the first without being the second, and
collapsing them would either blind Computer-Use or capture on every click.

Layering: this module returns raw bytes and plain text, never a brain-layer
type. The caller builds whatever image block its provider needs, so
``jarvis.screen_context`` keeps depending on nothing above ``jarvis.core``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from jarvis.screen_context.models import ScreenContext
from jarvis.screen_context.service import ScreenContextService, settings_from_config

log = logging.getLogger(__name__)

TurnStatus = Literal["none", "clarify", "captured", "refused", "unavailable"]

#: Process-wide service. Built on first use, never at import (AP-26).
_service: ScreenContextService | None = None


def get_service(*, bus: Any | None = None) -> ScreenContextService:
    """The shared service, built from live config on first use."""
    global _service
    if _service is None:
        from jarvis.core.config import load_config  # noqa: PLC0415

        _service = ScreenContextService(
            settings=settings_from_config(load_config()), bus=bus
        )
    return _service


def reset_service() -> None:
    """Drop the shared service (settings changed, or a test needs a clean one).

    Discards whatever it still holds: a settings change that tightens the
    privacy rules must not leave a capture taken under the old ones in memory.
    """
    global _service
    if _service is not None:
        _service.discard_all()
    _service = None


@dataclass(frozen=True, slots=True)
class TurnScreenContext:
    """What a conversation turn should do about the screen.

    Five shapes, and a caller must handle all five:

    * ``none`` — no visual request. Continue exactly as before.
    * ``clarify`` — ambiguous. Speak ``question`` and end the turn. Do NOT
      capture, and do NOT fall through to any other screen path: falling
      through would attach an image while asking whether to look at one.
    * ``captured`` — ``image`` and ``note`` are set. Attach both.
    * ``refused`` — the USER's rules said no (denylist, feature off). Speak
      ``message`` and end the turn. A caller must not reach the screen by some
      other route here; that would photograph the very window the rule exists
      to protect.
    * ``unavailable`` — this machine could not (no display, no permission,
      capture error). Nothing was forbidden, so the caller SHOULD continue with
      whatever it did before this feature existed. The reason is logged, and
      the fallback path reports its own failure honestly if it also fails.

    The ``refused`` / ``unavailable`` split is the important one: collapsing
    them either leaks a protected window (treating a privacy rule as a
    technical hiccup) or breaks every look-request on a headless host (treating
    a missing display as a prohibition).
    """

    status: TurnStatus
    image: bytes | None = None
    mime: str = ""
    #: Model-facing preamble: what the image shows, and what was withheld.
    note: str = ""
    #: The visible UI text, already scrubbed.
    text: str = ""
    question: str | None = None
    message: str | None = None
    #: One user-facing line naming what was captured, for the transcript.
    receipt: str = ""
    source_hash: str = ""
    context: ScreenContext | None = field(default=None, repr=False)

    @property
    def has_image(self) -> bool:
        return self.status == "captured" and bool(self.image)

    @property
    def ends_the_turn(self) -> bool:
        """Whether the caller must stop and speak instead of calling the brain."""
        return self.status in ("clarify", "refused")

    @property
    def blocks_other_screen_paths(self) -> bool:
        """Whether any OTHER way of seeing the screen must stay shut this turn.

        True for a privacy refusal and for the clarifying question. False for
        ``unavailable``, which is the whole point of that status.
        """
        return self.status in ("clarify", "refused")


def _model_note(context: ScreenContext) -> str:
    """The English preamble that rides with the image.

    Three jobs, all of them about not letting the model invent things:

    * name the surface, so it does not claim to see the whole desktop when it
      was handed one monitor;
    * name the app and window, which the picture alone often does not show;
    * declare the redactions, so black rectangles are understood as withheld
      content rather than narrated as user interface.
    """
    target = context.target
    where = (
        f"the '{target.window.title}' window"
        if target.kind.value == "window" and target.window.title
        else f"monitor {target.monitor_name or '?'}"
    )
    lines = [f"Screen capture of {where}, taken just now at the user's request."]

    if target.window.is_known and target.kind.value != "window":
        app = target.window.app_name or "an unknown application"
        title = target.window.title or "untitled"
        lines.append(f"Active application: {app} — window title: {title}.")

    if not context.redactions.is_empty:
        lines.append(
            "Privacy filter applied before you received this: "
            f"{context.redactions.summary()}. Black rectangles are withheld "
            "content, not part of the interface — do not describe or guess them."
        )

    if context.degradations:
        limits = "; ".join(d.message for d in context.degradations)
        lines.append(f"Limitations of this capture: {limits}")

    if context.ui_text:
        lines.append(
            "Visible on-screen text, read from the accessibility layer:\n"
            f"{context.ui_text}"
        )

    return "\n".join(lines)


async def screen_context_for_turn(
    utterance: str,
    *,
    locale: str,
    bus: Any | None = None,
    service: ScreenContextService | None = None,
) -> TurnScreenContext:
    """Resolve the screen question for one conversation turn.

    ``locale`` must already be resolved via
    ``jarvis.core.turn_language.resolve_output_language`` — this function never
    derives a language, so a clarifying question cannot flip the conversation's
    language mid-session (CLAUDE.md §1.3).

    Never raises. Any unexpected failure degrades to ``none``, which means the
    caller behaves exactly as it did before this feature existed — a bug in
    Screen Context must not be able to break a voice turn.
    """
    try:
        svc = service or get_service(bus=bus)
        outcome = await svc.capture_for_turn(utterance, locale=locale)

        if outcome.status == "not_requested":
            return TurnScreenContext(status="none")

        if outcome.status == "clarify":
            return TurnScreenContext(
                status="clarify", question=outcome.question or ""
            )

        if outcome.status == "refused":
            if outcome.reason_kind == "technical":
                # Not forbidden — impossible here. Log it and let the caller
                # carry on with whatever it did before; ending the turn would
                # break every look-request on a headless or unpermitted host.
                log.info(
                    "screen_context: capture unavailable on this machine (%s) "
                    "— the turn continues on the existing path",
                    outcome.message,
                )
                return TurnScreenContext(
                    status="unavailable", message=outcome.message or ""
                )
            return TurnScreenContext(status="refused", message=outcome.message or "")

        context = outcome.context
        if context is None:  # defensive: 'captured' always carries one
            return TurnScreenContext(status="none")

        # Consume immediately: this turn IS the single use. Leaving the handle
        # parked would keep the pixels alive for the whole TTL for no reason.
        if outcome.handle_id:
            svc.consume(outcome.handle_id)

        return TurnScreenContext(
            status="captured",
            image=context.image,
            mime=context.mime,
            note=_model_note(context),
            text=context.ui_text,
            receipt=context.describe(),
            source_hash=f"{context.captured_at_ns:x}",
            context=context,
        )
    except Exception:  # noqa: BLE001 — a bug here must never kill a voice turn
        log.error(
            "screen_context: turn integration failed — the turn continues "
            "without screen context",
            exc_info=True,
        )
        return TurnScreenContext(status="none")


__all__ = [
    "TurnScreenContext",
    "TurnStatus",
    "get_service",
    "reset_service",
    "screen_context_for_turn",
]
