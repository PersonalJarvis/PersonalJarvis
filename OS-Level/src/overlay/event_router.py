"""IPC-Envelope -> StateMachine-Transition Mapping. Plan §6.3.

Der EventRouter ist die einzige Komponente die IPC-Wire-Format-Wissen mit
State-Semantik verbindet. Er kennt KEINE Qt- und KEINE asyncio-APIs —
das macht ihn unabhaengig testbar und in Subprocess- wie Worker-Thread-
Kontexten verwendbar.

Mapping (gemaess Plan §6.3):

| Envelope                   | Transition                       |
|----------------------------|----------------------------------|
| StateEnvelope              | direkt nach payload.state        |
| ActionStartedEnvelope      | TYPING (kind in {type, hotkey})  |
|                            | CLICKING (kind in {click, move,  |
|                            |   navigate, scroll})             |
| ActionEndedEnvelope        | IDLE                             |
| ClickEnvelope              | CLICKING                         |
| ErrorEnvelope (recoverable)| ERROR                            |
| Heartbeat / Cursor / Ack / | no-op                            |
|   Config                   |                                  |
"""

from __future__ import annotations

import logging
from typing import Any

from .schema import (
    AckEnvelope,
    ActionEndedEnvelope,
    ActionStartedEnvelope,
    ClickEnvelope,
    ConfigEnvelope,
    CursorEnvelope,
    ErrorEnvelope,
    HeartbeatEnvelope,
    StateEnvelope,
)
from .state import OverlayState, StateMachine

logger = logging.getLogger(__name__)


# Action-Kind -> State Buckets. Plan §6.3 listet nur TYPING und CLICKING
# als Action-States; alle 6 Computer-Use-Kinds werden auf eines davon
# gemappt damit jeder Action-Started-Event auch wirklich Glow ausloest.
TYPING_KINDS: frozenset[str] = frozenset({"type", "hotkey"})
CLICKING_KINDS: frozenset[str] = frozenset({"click", "move", "navigate", "scroll"})

# Reasons fuer programmatic Transitions — Plan-Erlaubt: ``StateReason`` Literal.
REASON_TOOL = "tool"
REASON_ERROR = "error"


class EventRouter:
    """Konvertiert IPC-Envelopes nach StateMachine-Transitionen.

    Plan AD-8: State-Logic lebt im Overlay-Prozess — diese Klasse ist die
    Bruecke zwischen Wire-Format (``schema.py``) und State-Holder
    (``state.py``).

    Aufruf typischerweise via ``WSClient.set_on_message(router.handle)``.
    """

    def __init__(self, machine: StateMachine) -> None:
        self._machine = machine
        # Phase 9.5 Effect-Hooks. Subscriber bekommen das Envelope-Objekt
        # selbst (nicht nur Payload) damit sie auf alle Felder zugreifen
        # koennen.
        self._click_hooks: list[Any] = []
        self._action_started_hooks: list[Any] = []
        self._action_ended_hooks: list[Any] = []
        self._cursor_hooks: list[Any] = []

    @property
    def machine(self) -> StateMachine:
        return self._machine

    def add_click_hook(self, fn: Any) -> None:
        """Wird mit ``ClickEnvelope`` gerufen, sync, im Caller-Thread."""
        self._click_hooks.append(fn)

    def add_action_started_hook(self, fn: Any) -> None:
        """Wird mit ``ActionStartedEnvelope`` gerufen."""
        self._action_started_hooks.append(fn)

    def add_action_ended_hook(self, fn: Any) -> None:
        """Wird mit ``ActionEndedEnvelope`` gerufen."""
        self._action_ended_hooks.append(fn)

    def add_cursor_hook(self, fn: Any) -> None:
        """Wird mit ``CursorEnvelope`` gerufen (WS-Fallback wenn SHM aus)."""
        self._cursor_hooks.append(fn)

    def _safe_dispatch(self, hooks: list[Any], envelope: Any) -> None:
        for h in hooks:
            try:
                h(envelope)
            except Exception:  # noqa: BLE001
                logger.exception("EventRouter hook raised on %s", type(envelope).__name__)

    def handle(self, envelope: Any) -> bool:
        """Verarbeitet einen einzelnen Envelope.

        Returnt ``True`` wenn eine echte State-Transition ausgeloest wurde,
        ``False`` bei No-Op (Heartbeat, Coalescing-Hit, unbekannter Type).
        """
        # StateEnvelope: Hauptjarvis sagt explizit "neuer State".
        if isinstance(envelope, StateEnvelope):
            try:
                target = OverlayState(envelope.payload.state)
            except ValueError:
                logger.warning(
                    "EventRouter: unknown state literal %r",
                    envelope.payload.state,
                )
                return False
            return self._machine.transition_to(
                target, reason=envelope.payload.reason
            )

        # ActionStarted: Hauptjarvis bedient Maus/Tastatur -> Glow an.
        if isinstance(envelope, ActionStartedEnvelope):
            self._safe_dispatch(self._action_started_hooks, envelope)
            kind = envelope.payload.kind
            if kind in TYPING_KINDS:
                return self._machine.transition_to(
                    OverlayState.TYPING, reason=REASON_TOOL
                )
            if kind in CLICKING_KINDS:
                return self._machine.transition_to(
                    OverlayState.CLICKING, reason=REASON_TOOL
                )
            logger.debug("EventRouter: action_started kind %r ohne Mapping", kind)
            return False

        # ActionEnded: Action vorbei -> zurueck nach IDLE.
        # Hoehere Layer (Wakeword/STT/TTS) restoren danach selbst die
        # naechste Action-State per eigenem Event.
        if isinstance(envelope, ActionEndedEnvelope):
            self._safe_dispatch(self._action_ended_hooks, envelope)
            return self._machine.transition_to(
                OverlayState.IDLE, reason=REASON_TOOL
            )

        # ClickEvent: Atomare Klick-Notification (Plan §6.3 +
        # Click-Visualization §14). State-Bump nach CLICKING + Hook
        # fires fuer Ripple-Trigger.
        if isinstance(envelope, ClickEnvelope):
            self._safe_dispatch(self._click_hooks, envelope)
            return self._machine.transition_to(
                OverlayState.CLICKING, reason=REASON_TOOL
            )

        # Cursor: WS-Fallback Plan §11.5 — wenn SHM disabled. Hook
        # forwarded coords. KEIN State-Change.
        if isinstance(envelope, CursorEnvelope):
            self._safe_dispatch(self._cursor_hooks, envelope)
            return False

        # Recoverable Error -> ERROR State (kurze Amber/Red-Phase, Plan
        # §6.1). Non-recoverable wird nicht visualisiert weil Hauptjarvis
        # ohnehin gleich crasht; Supervisor fangt das auf Process-Level.
        if isinstance(envelope, ErrorEnvelope):
            if envelope.payload.recoverable:
                return self._machine.transition_to(
                    OverlayState.ERROR, reason=REASON_ERROR
                )
            return False

        # No-Op-Envelope-Typen — explizit gelistet damit unbekannte
        # Envelope-Klassen unten als WARNING auffliegen. (CursorEnvelope
        # ist oben behandelt, weil es nur Hook-fired aber kein State-
        # Change.)
        if isinstance(
            envelope,
            (HeartbeatEnvelope, ConfigEnvelope, AckEnvelope),
        ):
            return False

        logger.warning(
            "EventRouter: unhandled envelope type %s", type(envelope).__name__
        )
        return False


__all__ = [
    "CLICKING_KINDS",
    "EventRouter",
    "REASON_ERROR",
    "REASON_TOOL",
    "TYPING_KINDS",
]
