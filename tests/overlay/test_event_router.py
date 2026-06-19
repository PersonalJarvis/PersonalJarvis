"""EventRouter — Plan §6.3 Mapping IPC -> State-Transition."""

from __future__ import annotations

import pytest

from overlay.event_router import (
    CLICKING_KINDS,
    EventRouter,
    REASON_ERROR,
    REASON_TOOL,
    TYPING_KINDS,
)
from overlay.schema import (
    AckEnvelope,
    AckPayload,
    ActionEndedEnvelope,
    ActionEndedPayload,
    ActionStartedEnvelope,
    ActionStartedPayload,
    ClickEnvelope,
    ClickPayload,
    ConfigEnvelope,
    ConfigPayload,
    CursorEnvelope,
    CursorPayload,
    ErrorEnvelope,
    ErrorPayload,
    HeartbeatEnvelope,
    HeartbeatPayload,
    StateEnvelope,
    StatePayload,
)
from overlay.state import OverlayState, StateMachine


@pytest.fixture()
def machine() -> StateMachine:
    return StateMachine()


@pytest.fixture()
def router(machine: StateMachine) -> EventRouter:
    return EventRouter(machine)


# -------------------------------------------------------------------------
# StateEnvelope — direkter Pass-Through
# -------------------------------------------------------------------------


def test_state_envelope_transitions(
    router: EventRouter, machine: StateMachine
) -> None:
    env = StateEnvelope(payload=StatePayload(state="listening", reason="wakeword"))
    assert router.handle(env) is True
    assert machine.state is OverlayState.LISTENING


def test_state_envelope_passes_reason_through(
    router: EventRouter, machine: StateMachine
) -> None:
    captured: list[str | None] = []
    machine.subscribe(lambda _o, _n, r: captured.append(r))

    env = StateEnvelope(payload=StatePayload(state="thinking", reason="tool"))
    router.handle(env)
    assert captured == ["tool"]


def test_state_envelope_no_reason(router: EventRouter, machine: StateMachine) -> None:
    captured: list[str | None] = []
    machine.subscribe(lambda _o, _n, r: captured.append(r))

    env = StateEnvelope(payload=StatePayload(state="speaking"))
    assert router.handle(env) is True
    assert captured == [None]


# -------------------------------------------------------------------------
# ActionStartedEnvelope — kind -> bucket
# -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(TYPING_KINDS))
def test_action_started_typing_kinds(
    kind: str, router: EventRouter, machine: StateMachine
) -> None:
    env = ActionStartedEnvelope(payload=ActionStartedPayload(kind=kind))  # type: ignore[arg-type]
    assert router.handle(env) is True
    assert machine.state is OverlayState.TYPING


@pytest.mark.parametrize("kind", sorted(CLICKING_KINDS))
def test_action_started_clicking_kinds(
    kind: str, router: EventRouter, machine: StateMachine
) -> None:
    env = ActionStartedEnvelope(payload=ActionStartedPayload(kind=kind))  # type: ignore[arg-type]
    assert router.handle(env) is True
    assert machine.state is OverlayState.CLICKING


def test_action_started_uses_tool_reason(
    router: EventRouter, machine: StateMachine
) -> None:
    captured: list[str | None] = []
    machine.subscribe(lambda _o, _n, r: captured.append(r))

    env = ActionStartedEnvelope(payload=ActionStartedPayload(kind="type"))
    router.handle(env)
    assert captured == [REASON_TOOL]


# -------------------------------------------------------------------------
# ActionEndedEnvelope -> IDLE
# -------------------------------------------------------------------------


def test_action_ended_returns_to_idle(
    router: EventRouter, machine: StateMachine
) -> None:
    machine.transition_to(OverlayState.TYPING)
    env = ActionEndedEnvelope(
        payload=ActionEndedPayload(action_id="01HZX000000000000000000000")
    )
    assert router.handle(env) is True
    assert machine.state is OverlayState.IDLE


# -------------------------------------------------------------------------
# ClickEnvelope -> CLICKING
# -------------------------------------------------------------------------


def test_click_envelope_transitions_to_clicking(
    router: EventRouter, machine: StateMachine
) -> None:
    env = ClickEnvelope(payload=ClickPayload(x=100, y=200))
    assert router.handle(env) is True
    assert machine.state is OverlayState.CLICKING


# -------------------------------------------------------------------------
# ErrorEnvelope
# -------------------------------------------------------------------------


def test_error_recoverable_transitions_to_error(
    router: EventRouter, machine: StateMachine
) -> None:
    captured: list[str | None] = []
    machine.subscribe(lambda _o, _n, r: captured.append(r))

    env = ErrorEnvelope(
        payload=ErrorPayload(code="boom", message="x", recoverable=True)
    )
    assert router.handle(env) is True
    assert machine.state is OverlayState.ERROR
    assert captured == [REASON_ERROR]


def test_error_non_recoverable_is_noop(
    router: EventRouter, machine: StateMachine
) -> None:
    env = ErrorEnvelope(
        payload=ErrorPayload(code="fatal", message="x", recoverable=False)
    )
    assert router.handle(env) is False
    assert machine.state is OverlayState.IDLE


# -------------------------------------------------------------------------
# No-Op Envelopes
# -------------------------------------------------------------------------


def test_heartbeat_is_noop(router: EventRouter, machine: StateMachine) -> None:
    env = HeartbeatEnvelope(payload=HeartbeatPayload())
    assert router.handle(env) is False
    assert machine.state is OverlayState.IDLE


def test_cursor_is_noop(router: EventRouter, machine: StateMachine) -> None:
    env = CursorEnvelope(payload=CursorPayload(x=1, y=2))
    assert router.handle(env) is False


def test_config_is_noop(router: EventRouter, machine: StateMachine) -> None:
    env = ConfigEnvelope(payload=ConfigPayload())
    assert router.handle(env) is False


def test_ack_is_noop(router: EventRouter, machine: StateMachine) -> None:
    env = AckEnvelope(payload=AckPayload(ack_id="01HZX000000000000000000000"))
    assert router.handle(env) is False


def test_unknown_envelope_is_noop(router: EventRouter, machine: StateMachine) -> None:
    """Wenn jemand etwas Fremdes durchschiebt, kein Crash, einfach False."""

    class _Foreign:
        pass

    assert router.handle(_Foreign()) is False
    assert machine.state is OverlayState.IDLE


# -------------------------------------------------------------------------
# Sequenz-Test
# -------------------------------------------------------------------------


def test_typical_voice_sequence(router: EventRouter, machine: StateMachine) -> None:
    """Plan §6.2: idle -> listening -> thinking -> typing -> idle -> speaking."""
    seen: list[OverlayState] = [machine.state]
    machine.subscribe(lambda _o, n, _r: seen.append(n))

    router.handle(StateEnvelope(payload=StatePayload(state="listening")))
    router.handle(StateEnvelope(payload=StatePayload(state="thinking")))
    router.handle(ActionStartedEnvelope(payload=ActionStartedPayload(kind="type")))
    router.handle(
        ActionEndedEnvelope(payload=ActionEndedPayload(action_id="01HZX000000000000000000000"))
    )
    router.handle(StateEnvelope(payload=StatePayload(state="speaking")))

    assert seen == [
        OverlayState.IDLE,
        OverlayState.LISTENING,
        OverlayState.THINKING,
        OverlayState.TYPING,
        OverlayState.IDLE,
        OverlayState.SPEAKING,
    ]
