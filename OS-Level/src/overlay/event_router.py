"""IPC envelope -> StateMachine transition mapping. Plan §6.3.

The EventRouter is the only component that connects IPC wire-format
knowledge with state semantics. It knows NO Qt and NO asyncio APIs —
that makes it independently testable and usable in both subprocess and
worker-thread contexts.

Mapping (per Plan §6.3):

| Envelope                   | Transition                       |
|----------------------------|----------------------------------|
| StateEnvelope              | direct, from payload.state       |
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


# Action-kind -> state buckets. Plan §6.3 lists only TYPING and CLICKING
# as action states; all 6 Computer-Use kinds are mapped onto one of
# those two so every action-started event actually triggers the glow.
TYPING_KINDS: frozenset[str] = frozenset({"type", "hotkey"})
CLICKING_KINDS: frozenset[str] = frozenset({"click", "move", "navigate", "scroll"})

# Reasons for programmatic transitions — plan-allowed: ``StateReason`` literal.
REASON_TOOL = "tool"
REASON_ERROR = "error"


class EventRouter:
    """Converts IPC envelopes into StateMachine transitions.

    Plan AD-8: state logic lives in the overlay process — this class is
    the bridge between the wire format (``schema.py``) and the state
    holder (``state.py``).

    Typically invoked via ``WSClient.set_on_message(router.handle)``.
    """

    def __init__(self, machine: StateMachine) -> None:
        self._machine = machine
        # Phase 9.5 effect hooks. Subscribers receive the envelope
        # object itself (not just the payload) so they can access all
        # fields.
        self._click_hooks: list[Any] = []
        self._action_started_hooks: list[Any] = []
        self._action_ended_hooks: list[Any] = []
        self._cursor_hooks: list[Any] = []

    @property
    def machine(self) -> StateMachine:
        return self._machine

    def add_click_hook(self, fn: Any) -> None:
        """Called with ``ClickEnvelope``, sync, on the caller thread."""
        self._click_hooks.append(fn)

    def add_action_started_hook(self, fn: Any) -> None:
        """Called with ``ActionStartedEnvelope``."""
        self._action_started_hooks.append(fn)

    def add_action_ended_hook(self, fn: Any) -> None:
        """Called with ``ActionEndedEnvelope``."""
        self._action_ended_hooks.append(fn)

    def add_cursor_hook(self, fn: Any) -> None:
        """Called with ``CursorEnvelope`` (WS fallback when SHM is off)."""
        self._cursor_hooks.append(fn)

    def _safe_dispatch(self, hooks: list[Any], envelope: Any) -> None:
        for h in hooks:
            try:
                h(envelope)
            except Exception:  # noqa: BLE001
                logger.exception("EventRouter hook raised on %s", type(envelope).__name__)

    def handle(self, envelope: Any) -> bool:
        """Processes a single envelope.

        Returns ``True`` if a real state transition was triggered,
        ``False`` on a no-op (heartbeat, coalescing hit, unknown type).
        """
        # StateEnvelope: Hauptjarvis explicitly says "new state".
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

        # ActionStarted: Hauptjarvis is operating the mouse/keyboard -> glow on.
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
            logger.debug("EventRouter: action_started kind %r has no mapping", kind)
            return False

        # ActionEnded: action is over -> back to IDLE.
        # Higher layers (wakeword/STT/TTS) then restore the next action
        # state themselves via their own event.
        if isinstance(envelope, ActionEndedEnvelope):
            self._safe_dispatch(self._action_ended_hooks, envelope)
            return self._machine.transition_to(
                OverlayState.IDLE, reason=REASON_TOOL
            )

        # ClickEvent: atomic click notification (Plan §6.3 +
        # click-visualization §14). State bump to CLICKING + hook
        # fires for the ripple trigger.
        if isinstance(envelope, ClickEnvelope):
            self._safe_dispatch(self._click_hooks, envelope)
            return self._machine.transition_to(
                OverlayState.CLICKING, reason=REASON_TOOL
            )

        # Cursor: WS fallback Plan §11.5 — when SHM is disabled. Hook
        # forwards the coords. NO state change.
        if isinstance(envelope, CursorEnvelope):
            self._safe_dispatch(self._cursor_hooks, envelope)
            return False

        # Recoverable error -> ERROR state (brief amber/red phase, Plan
        # §6.1). Non-recoverable isn't visualized because Hauptjarvis
        # is about to crash anyway; the supervisor catches that at the
        # process level.
        if isinstance(envelope, ErrorEnvelope):
            if envelope.payload.recoverable:
                return self._machine.transition_to(
                    OverlayState.ERROR, reason=REASON_ERROR
                )
            return False

        # No-op envelope types — listed explicitly so unknown envelope
        # classes surface as a WARNING below. (CursorEnvelope is
        # handled above because it only fires a hook but has no state
        # change.)
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
