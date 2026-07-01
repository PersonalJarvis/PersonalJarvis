"""SelfModFlowController — state machine for the voice echo confirmation.

Plan-§7.4 conversation flow as a pure library layer. The real
voice pipeline (`jarvis/speech/pipeline.py`) will get an adapter
in Phase 7.6 that hooks in this controller.

States:
    PARSED      — initial state (internal)
    CONFIRMING  — echo question handed to TTS, waiting for the user's answer
    APPLYING    — confirmation detected, mutate() is running
    APPLIED     — terminal state: successfully persisted
    VETOED      — terminal state: user declined
    TIMEOUT     — terminal state: no answer within 30s
    FAILED      — terminal state: pre-validate / reload / rollback error

Audit trail (Plan-§AP-1, §AP-12 codified):
- Pre-audit "voice_confirmed" on confirm (with the voice_confirmation field)
- Reject-audit "voice_vetoed" or "voice_timeout" via the pending store
- Mutate-audit (success/failure) is written by the AtomicConfigWriter
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from jarvis.core.self_mod.schema import AuditActor, AuditEvent, AuditSource

if TYPE_CHECKING:
    from jarvis.core.self_mod import (
        PendingMutation,
        PendingMutationStore,
        SelfModAudit,
    )
from jarvis.core.self_mod.errors import (
    PreValidateError,
    ReloadError,
    RollbackError,
)

from .echo_confirmation import (
    OutcomeKind,
    classify_response,
    format_confirmation,
    format_outcome,
    short_error_from_exception,
)

_LOG = logging.getLogger(__name__)


class FlowState(StrEnum):
    PARSED = "parsed"
    CONFIRMING = "confirming"
    APPLYING = "applying"
    APPLIED = "applied"
    VETOED = "vetoed"
    TIMEOUT = "timeout"
    FAILED = "failed"


_TERMINAL_STATES = frozenset(
    {FlowState.APPLIED, FlowState.VETOED, FlowState.TIMEOUT, FlowState.FAILED}
)


@dataclass(frozen=True)
class FlowSession:
    """Snapshot of the flow state.

    `frozen=True` + `replace()` for state transitions — pure functions,
    no mutation, deterministically testable.
    """

    pending: PendingMutation
    state: FlowState
    echo_question: str
    deadline_ts: float
    language: str = "de"
    transcript: str | None = None
    confidence: float | None = None
    final_message: str | None = None
    error: str | None = None
    backup_path: str | None = None

    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


def _utc_iso_z() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _voice_confirmation_payload(
    transcript: str | None, confidence: float | None
) -> dict[str, Any] | None:
    if transcript is None:
        return None
    return {
        "transcript": transcript,
        "confidence": confidence if confidence is not None else 1.0,
        "timestamp_utc": _utc_iso_z(),
    }


class SelfModFlowController:
    """Orchestrator for the voice echo confirmation flow.

    Plan-§7.4 default timeout 30s — configurable via the constructor.
    """

    DEFAULT_TIMEOUT_SECONDS: ClassVar[float] = 30.0

    def __init__(
        self,
        *,
        pending_store: PendingMutationStore,
        audit: SelfModAudit | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        default_language: str = "de",
    ) -> None:
        self._pending_store = pending_store
        # Audit for pre-events (voice_confirmed). Reject events run through
        # the pending store, which in turn has the same writer audit.
        self._audit: SelfModAudit = audit if audit is not None else pending_store._audit  # noqa: SLF001
        self._timeout_seconds = timeout_seconds
        self._default_language = default_language

    # ------------------------------------------------------------------
    # Flow transitions
    # ------------------------------------------------------------------

    def begin(
        self,
        pending: PendingMutation,
        *,
        language: str | None = None,
        now: float | None = None,
    ) -> FlowSession:
        """Initiates the flow after receiving a `PendingMutation`.

        SAFE tier (`pending.applied=True`): the flow starts directly in the
        `APPLIED` state, because the store has already persisted the mutation.
        The voice pipeline just renders the `safe_applied` outcome.
        """
        lang = language or self._default_language
        if pending.applied:
            return FlowSession(
                pending=pending,
                state=FlowState.APPLIED,
                echo_question="",
                deadline_ts=0.0,
                language=lang,
                final_message=format_outcome(
                    "safe_applied", pending, language=lang
                ),
                backup_path=pending.backup_path,
            )
        clock = now if now is not None else time.time()
        return FlowSession(
            pending=pending,
            state=FlowState.CONFIRMING,
            echo_question=format_confirmation(pending, language=lang),
            deadline_ts=clock + self._timeout_seconds,
            language=lang,
        )

    def receive_answer(
        self,
        session: FlowSession,
        transcript: str,
        *,
        confidence: float = 1.0,
        now: float | None = None,
    ) -> FlowSession:
        """Processes a user's answer.

        - Confirm  → the pending mutation is consumed, mutate runs, → APPLIED/FAILED.
        - Veto     → the pending mutation is rejected via the store with `voice_vetoed`.
        - Ambiguous/Unknown → the session stays in CONFIRMING, **without** extending
          the timeout (a plan safety property, no soft-lock).
        - If the timeout has already elapsed: → TIMEOUT.
        """
        if session.state != FlowState.CONFIRMING:
            raise ValueError(
                f"receive_answer is only allowed in CONFIRMING, not in {session.state}"
            )
        clock = now if now is not None else time.time()
        if clock > session.deadline_ts:
            return self._timeout(session)

        verdict = classify_response(transcript, language=session.language)
        if verdict == "confirm":
            return self._apply(session, transcript=transcript, confidence=confidence)
        if verdict == "veto":
            return self._veto(session, transcript=transcript, confidence=confidence)
        # ambiguous OR unknown: stays in CONFIRMING without extending the timeout.
        return replace(
            session, transcript=transcript, confidence=confidence
        )

    def check_timeout(
        self, session: FlowSession, *, now: float | None = None
    ) -> FlowSession:
        """Should be called regularly (e.g. every 1s).

        Terminal states are returned unchanged.
        """
        if session.is_terminal() or session.state != FlowState.CONFIRMING:
            return session
        clock = now if now is not None else time.time()
        if clock > session.deadline_ts:
            return self._timeout(session)
        return session

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply(
        self, session: FlowSession, *, transcript: str, confidence: float
    ) -> FlowSession:
        # Pre-audit "voice_confirmed" enriched with voice_confirmation
        voice_payload = _voice_confirmation_payload(transcript, confidence)
        try:
            self._audit.record(
                AuditEvent(
                    source=AuditSource.VOICE,
                    requested_by=AuditActor.USER,
                    path=session.pending.path,
                    old_value=session.pending.old_value,
                    new_value=session.pending.new_value,
                    ok=True,
                    rolled_back=False,
                    error=None,
                    voice_confirmation=voice_payload,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("voice_confirmed pre-audit failed: %s", exc)

        try:
            mutation_result = self._pending_store.confirm(session.pending.id)
        except (PreValidateError, ReloadError, RollbackError) as exc:
            return replace(
                session,
                state=FlowState.FAILED,
                transcript=transcript,
                confidence=confidence,
                error=str(exc),
                final_message=format_outcome(
                    "rollback"
                    if isinstance(exc, (ReloadError, RollbackError))
                    else "validate_failed",
                    session.pending,
                    language=session.language,
                    short_error=short_error_from_exception(exc),
                ),
            )
        except KeyError as exc:
            # Pending mutation no longer there after TTL expiry
            return replace(
                session,
                state=FlowState.TIMEOUT,
                transcript=transcript,
                confidence=confidence,
                error=str(exc),
                final_message=format_outcome(
                    "timeout", session.pending, language=session.language
                ),
            )

        outcome: OutcomeKind = (
            "applied_restart"
            if session.pending.requires_restart
            else "applied"
        )
        return replace(
            session,
            state=FlowState.APPLIED,
            transcript=transcript,
            confidence=confidence,
            final_message=format_outcome(
                outcome, session.pending, language=session.language
            ),
            backup_path=mutation_result.backup_path,
        )

    def _veto(
        self, session: FlowSession, *, transcript: str, confidence: float
    ) -> FlowSession:
        voice_payload = _voice_confirmation_payload(transcript, confidence)
        # Reject-audit comes from the pending store with reason="voice_vetoed".
        try:
            self._pending_store.reject(
                session.pending.id,
                reason="voice_vetoed",
                source=AuditSource.VOICE,
                voice_confirmation=voice_payload,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Pending-store reject (vetoed) failed: %s", exc)
        return replace(
            session,
            state=FlowState.VETOED,
            transcript=transcript,
            confidence=confidence,
            final_message=format_outcome(
                "vetoed", session.pending, language=session.language
            ),
        )

    def _timeout(self, session: FlowSession) -> FlowSession:
        try:
            self._pending_store.reject(
                session.pending.id,
                reason="voice_timeout",
                source=AuditSource.VOICE,
                voice_confirmation=None,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Pending-store reject (timeout) failed: %s", exc)
        return replace(
            session,
            state=FlowState.TIMEOUT,
            final_message=format_outcome(
                "timeout", session.pending, language=session.language
            ),
        )


# Re-export for test-friendly access
__all__ = [
    "FlowSession",
    "FlowState",
    "SelfModFlowController",
]


# Suppress lint: `field` import is reserved for future FrozenSet defaults
_ = field
_ = UUID
