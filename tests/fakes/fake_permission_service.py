"""FakePermissionService: a scripted ``PermissionGate`` for consumer tests.

Per AGENTS.md the project uses real fakes, never ``unittest.mock``. A consumer
(audio capture, dictation, the computer-use actuator, a hotkey backend) asks the
permission layer through the ``jarvis.core.protocols.PermissionGate`` hook; this
fake answers it with a scripted outcome per permission, no OS, no bus and no
threads, and records every call so the test can assert what the consumer asked.

Usage::

    gate = FakePermissionService()                              # everything granted
    gate.script(PermissionId.MICROPHONE, PermissionOutcome.PENDING)   # sticky
    gate.script(PermissionId.ACCESSIBILITY, "denied", "granted")      # one per call,
                                                                      # the last one sticks
    consumer = Dictation(access_gate=gate)
    ...
    assert gate.ensure_calls(PermissionId.MICROPHONE)[0].interactive is False
    assert gate.native_free()          # an ``interactive=False`` consumer never asks

Results are real :class:`EnsureResult` objects whose sentences come from the same
fixed templates as the real service, so a consumer that renders ``user_detail`` or
forwards ``agent_detail`` is tested against the production wording. The fake's own
rule is only the one the real service guarantees: ``result.granted`` is true for
GRANTED and NOT_REQUIRED and for nothing else. Like the real service it folds
EVENT_POSTING into ACCESSIBILITY (one script, one answer for both ids) and it says
``asked`` only for an answer a real request could have produced (PENDING or
NEEDS_SETTINGS): a denial, an impossibility and a grant never ask.

The two reports a consumer makes about a REAL attempt (``report_failed_use``: a send was
refused although the probe read granted; ``report_use_ok``: a later send landed) are
recorded in the same call log (``report_calls()``) and never count as an ask.

Pair it with the real service and ``FakeTCC`` (``tests/fakes/fake_tcc.py``) when the
test is about macOS behaviour; use this fake when the test is about the consumer.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from jarvis.platform.permission_service import (
    EnsureResult,
    PermissionOutcome,
    agent_detail_for,
    user_detail_for,
)
from jarvis.platform.permissions import PANE_FAMILY, PermissionId, PermissionState

_STATE_OF_OUTCOME: dict[PermissionOutcome, PermissionState] = {
    PermissionOutcome.GRANTED: PermissionState.GRANTED,
    PermissionOutcome.PENDING: PermissionState.NOT_DETERMINED,
    PermissionOutcome.DENIED: PermissionState.DENIED,
    PermissionOutcome.NEEDS_SETTINGS: PermissionState.NOT_GRANTED,
    PermissionOutcome.UNAVAILABLE: PermissionState.UNAVAILABLE,
    PermissionOutcome.NOT_REQUIRED: PermissionState.NOT_REQUIRED,
}
_REASON_OF_OUTCOME: dict[PermissionOutcome, str] = {
    PermissionOutcome.PENDING: "not_determined",
    PermissionOutcome.DENIED: "denied",
    PermissionOutcome.NEEDS_SETTINGS: "needs_settings",
    PermissionOutcome.UNAVAILABLE: "unavailable",
}


@dataclass(frozen=True, slots=True)
class GateCall:
    """One call a consumer made, in order."""

    # "check" | "ensure" | "ensure_async" | "open_settings" | "report_failed_use" | "report_use_ok"
    method: str
    permission: PermissionId
    feature: str = ""
    interactive: bool = True
    wait_s: float = 0.0
    target: str | None = None
    trace_id: UUID | str | None = None
    allow_outside_app: bool = False
    force_ask: bool = False
    # Only a ``report_failed_use`` call carries these (the reason and the episode origin
    # the consumer asked for); a report never asks, so its ``interactive`` is False.
    reason: str | None = None
    origin: str | None = None


# Outcomes a native request can have produced: nothing else is ever "asked".
_ASKING_OUTCOMES = frozenset({PermissionOutcome.PENDING, PermissionOutcome.NEEDS_SETTINGS})


def make_result(
    permission: PermissionId | str,
    outcome: PermissionOutcome | str,
    *,
    asked: bool = False,
    outside_installed_app: bool = False,
    target: str = "",
    state: PermissionState | None = None,
) -> EnsureResult:
    """A real ``EnsureResult`` for ``outcome``, with the production sentences."""
    perm = PermissionId(permission)
    out = PermissionOutcome(str(outcome))
    resolved_state = state if state is not None else _STATE_OF_OUTCOME[out]
    if out in (PermissionOutcome.GRANTED, PermissionOutcome.NOT_REQUIRED):
        return EnsureResult(
            permission=perm,
            outcome=out,
            state=resolved_state,
            asked=asked,
            outside_installed_app=False,
            agent_detail="",
            user_detail="",
            target=target,
        )
    reason = (
        "restricted" if resolved_state is PermissionState.RESTRICTED else _REASON_OF_OUTCOME[out]
    )
    family = PANE_FAMILY[perm]
    return EnsureResult(
        permission=perm,
        outcome=out,
        state=resolved_state,
        asked=asked,
        outside_installed_app=outside_installed_app,
        agent_detail=agent_detail_for(family, reason, target=target),
        user_detail=user_detail_for(
            family, reason, target=target, outside_app=outside_installed_app
        ),
        reason=reason,
        can_prompt=out is PermissionOutcome.PENDING,
        can_open_settings=reason not in ("restricted", "unavailable"),
        target=target,
    )


class FakePermissionService:
    """A scripted, OS-free implementation of ``PermissionGate``."""

    def __init__(
        self,
        outcomes: Mapping[PermissionId | str, PermissionOutcome | str | EnsureResult] | None = None,
        *,
        default: PermissionOutcome | str = PermissionOutcome.GRANTED,
        settings_opens: bool = True,
    ) -> None:
        self._default = PermissionOutcome(str(default))
        self._sticky: dict[PermissionId, PermissionOutcome | EnsureResult] = {}
        self._queued: dict[PermissionId, deque[PermissionOutcome | EnsureResult]] = {}
        self.settings_opens = settings_opens
        self.calls: list[GateCall] = []
        # (permission, feature, target) of every failed use reported and not yet
        # followed by a use that worked: the fake's stand-in for the open episode.
        self._reported_failures: set[tuple[PermissionId, str, str]] = set()
        for permission, outcome in (outcomes or {}).items():
            self.script(permission, outcome)

    # ----------------------------------------------------------- scripting

    def script(
        self, permission: PermissionId | str, *outcomes: PermissionOutcome | str | EnsureResult
    ) -> None:
        """Script the answers for one permission.

        One outcome is sticky. Several are served one per ``ensure`` call, and the
        last one sticks. A ready-made :class:`EnsureResult` is returned as it is.
        """
        perm = PANE_FAMILY[PermissionId(permission)]
        if not outcomes:
            raise ValueError("script() needs at least one outcome")
        resolved = [
            item if isinstance(item, EnsureResult) else PermissionOutcome(str(item))
            for item in outcomes
        ]
        self._sticky[perm] = resolved[-1]
        self._queued[perm] = deque(resolved[:-1])

    def grant(self, permission: PermissionId | str) -> None:
        """The user allowed it: every later answer is GRANTED."""
        self.script(permission, PermissionOutcome.GRANTED)

    def _head(self, permission: PermissionId) -> PermissionOutcome | EnsureResult:
        permission = PANE_FAMILY[permission]
        queue = self._queued.get(permission)
        if queue:
            return queue[0]
        return self._sticky.get(permission, self._default)

    def _next(self, permission: PermissionId) -> PermissionOutcome | EnsureResult:
        permission = PANE_FAMILY[permission]
        queue = self._queued.get(permission)
        if queue:
            return queue.popleft()
        return self._sticky.get(permission, self._default)

    # ----------------------------------------------------------- the gate

    def check(
        self, permission: PermissionId | str, *, target: str | None = None
    ) -> PermissionState:
        perm = PermissionId(permission)
        self.calls.append(GateCall("check", perm, target=target))
        head = self._head(perm)
        if isinstance(head, EnsureResult):
            return head.state
        return _STATE_OF_OUTCOME[head]

    def ensure(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        interactive: bool = True,
        wait_s: float = 0.0,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        allow_outside_app: bool = False,
        force_ask: bool = False,
    ) -> EnsureResult:
        return self._answer(
            "ensure",
            permission,
            feature=feature,
            interactive=interactive,
            wait_s=wait_s,
            target=target,
            trace_id=trace_id,
            allow_outside_app=allow_outside_app,
            force_ask=force_ask,
        )

    async def ensure_async(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        interactive: bool = True,
        wait_s: float = 0.0,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        allow_outside_app: bool = False,
        force_ask: bool = False,
    ) -> EnsureResult:
        return self._answer(
            "ensure_async",
            permission,
            feature=feature,
            interactive=interactive,
            wait_s=wait_s,
            target=target,
            trace_id=trace_id,
            allow_outside_app=allow_outside_app,
            force_ask=force_ask,
        )

    def ensure_all(self, permissions: Any, *, feature: str, **kwargs: Any) -> list[EnsureResult]:
        """One ``ensure`` per permission, in order (the fake has no episodes)."""
        return [self.ensure(perm, feature=feature, **kwargs) for perm in permissions]

    def open_settings(self, permission: PermissionId | str) -> bool:
        perm = PermissionId(permission)
        self.calls.append(GateCall("open_settings", perm))
        return self.settings_opens

    def report_failed_use(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        reason: str | None = None,
        origin: str | None = None,
    ) -> EnsureResult:
        """Record a failed-use report; it never asks, so the call is not ``interactive``.

        The real service describes the Automation case (a send refused with -1743 while
        the probe reads granted) as NEEDS_SETTINGS with reason ``needs_settings``, and
        so does this fake. Any other permission answers like a non-interactive
        ``ensure`` (the real service does the same for a state that does not fit; the
        Screen Recording and Input Monitoring ``restart_hint`` is not modelled here).
        """
        perm = PermissionId(permission)
        self.calls.append(
            GateCall(
                "report_failed_use",
                perm,
                feature=feature,
                interactive=False,
                target=target,
                trace_id=trace_id,
                reason=reason,
                origin=origin,
            )
        )
        if PANE_FAMILY[perm] is PermissionId.AUTOMATION:
            self._reported_failures.add((PermissionId.AUTOMATION, feature, target or ""))
            return make_result(
                perm,
                PermissionOutcome.NEEDS_SETTINGS,
                target=target or "",
                state=PermissionState.GRANTED,
            )
        scripted = self._next(perm)
        if isinstance(scripted, EnsureResult):
            return scripted
        return make_result(perm, scripted, target=target or "")

    def report_use_ok(
        self, permission: PermissionId | str, *, feature: str, target: str | None = None
    ) -> bool:
        """Record a use that worked; ``True`` when it ended a reported failed use."""
        perm = PermissionId(permission)
        self.calls.append(
            GateCall("report_use_ok", perm, feature=feature, interactive=False, target=target)
        )
        key = (PANE_FAMILY[perm], feature, target or "")
        if key in self._reported_failures:
            self._reported_failures.discard(key)
            return True
        return False

    def _answer(
        self,
        method: str,
        permission: PermissionId | str,
        *,
        feature: str,
        interactive: bool,
        wait_s: float,
        target: str | None,
        trace_id: UUID | str | None,
        allow_outside_app: bool,
        force_ask: bool,
    ) -> EnsureResult:
        perm = PermissionId(permission)
        self.calls.append(
            GateCall(
                method,
                perm,
                feature=feature,
                interactive=interactive,
                wait_s=wait_s,
                target=target,
                trace_id=trace_id,
                allow_outside_app=allow_outside_app,
                force_ask=force_ask,
            )
        )
        scripted = self._next(perm)
        if isinstance(scripted, EnsureResult):
            return scripted
        return make_result(
            perm,
            scripted,
            asked=interactive and scripted in _ASKING_OUTCOMES,
            target=target or "",
        )

    # ---------------------------------------------------------- inspection

    def ensure_calls(self, permission: PermissionId | str | None = None) -> list[GateCall]:
        """Every ``ensure`` / ``ensure_async`` call, optionally for one permission."""
        wanted = PermissionId(permission) if permission is not None else None
        return [
            call
            for call in self.calls
            if call.method in ("ensure", "ensure_async")
            and (wanted is None or call.permission is wanted)
        ]

    def check_calls(self, permission: PermissionId | str | None = None) -> list[GateCall]:
        wanted = PermissionId(permission) if permission is not None else None
        return [
            call
            for call in self.calls
            if call.method == "check" and (wanted is None or call.permission is wanted)
        ]

    def report_calls(self, method: str | None = None) -> list[GateCall]:
        """Every ``report_failed_use`` / ``report_use_ok`` call, optionally of one kind."""
        return [
            call
            for call in self.calls
            if call.method in ("report_failed_use", "report_use_ok")
            and (method is None or call.method == method)
        ]

    def native_free(self) -> bool:
        """``True`` when no call could have made macOS ask (``check`` or ``interactive=False``)."""
        return all(
            call.method in ("check", "open_settings") or not call.interactive for call in self.calls
        )


__all__ = ["FakePermissionService", "GateCall", "make_result"]
