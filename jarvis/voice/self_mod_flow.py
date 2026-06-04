"""SelfModFlowController — State-Machine für die Voice-Echo-Confirmation.

Plan-§7.4-Konversationsfluss als reiner Library-Layer. Die echte
Voice-Pipeline (`jarvis/speech/pipeline.py`) bekommt in Phase 7.6
einen Adapter, der diesen Controller einhängt.

States:
    PARSED      — initialer Zustand (intern)
    CONFIRMING  — Echo-Frage an TTS gegeben, wartet auf User-Antwort
    APPLYING    — Confirmation entdeckt, mutate() läuft
    APPLIED     — End-Zustand: erfolgreich persistiert
    VETOED      — End-Zustand: User hat abgelehnt
    TIMEOUT     — End-Zustand: keine Antwort innerhalb 30s
    FAILED      — End-Zustand: Pre-Validate / Reload / Rollback-Fehler

Audit-Trail (Plan-§AP-1, §AP-12 codifiziert):
- Pre-Audit "voice_confirmed" beim Confirm (mit voice_confirmation-Feld)
- Reject-Audit "voice_vetoed" oder "voice_timeout" via Pending-Store
- Mutate-Audit (success/failure) wird vom AtomicConfigWriter geschrieben
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from jarvis.core.self_mod import (
    AuditActor,
    AuditEvent,
    AuditSource,
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
    """Snapshot des Flow-State.

    `frozen=True` + `replace()` für State-Übergänge — pure Funktionen,
    keine Mutation, deterministisch testbar.
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
    """Orchestrator für den Voice-Echo-Confirmation-Flow.

    Plan-§7.4 Default-Timeout 30s — über Konstruktor anpassbar.
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
        # Audit für Pre-Events (voice_confirmed). Reject-Events laufen über
        # den Pending-Store, der seinerseits den gleichen Writer-Audit hat.
        self._audit: SelfModAudit = audit if audit is not None else pending_store._audit  # noqa: SLF001
        self._timeout_seconds = timeout_seconds
        self._default_language = default_language

    # ------------------------------------------------------------------
    # Flow-Übergänge
    # ------------------------------------------------------------------

    def begin(
        self,
        pending: PendingMutation,
        *,
        language: str | None = None,
        now: float | None = None,
    ) -> FlowSession:
        """Initiiert den Flow nach erhaltener `PendingMutation`.

        SAFE-Tier (`pending.applied=True`): Flow startet bereits in
        `APPLIED`-State, weil der Store die Mutation schon persistiert hat.
        Voice-Pipeline rendert nur noch das `safe_applied`-Outcome.
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
        """Verarbeitet eine User-Antwort.

        - Confirm  → Pending wird konsumiert, Mutate läuft, → APPLIED/FAILED.
        - Veto     → Pending wird via Store mit `voice_vetoed` rejected.
        - Ambiguous/Unknown → Session bleibt in CONFIRMING, **ohne** Timeout
          zu verlängern (Plan-Sicherheits-Eigenschaft, kein Soft-Lock).
        - Wenn Timeout bereits überschritten ist: → TIMEOUT.
        """
        if session.state != FlowState.CONFIRMING:
            raise ValueError(
                f"receive_answer ist nur in CONFIRMING erlaubt, nicht in {session.state}"
            )
        clock = now if now is not None else time.time()
        if clock > session.deadline_ts:
            return self._timeout(session)

        verdict = classify_response(transcript, language=session.language)
        if verdict == "confirm":
            return self._apply(session, transcript=transcript, confidence=confidence)
        if verdict == "veto":
            return self._veto(session, transcript=transcript, confidence=confidence)
        # ambiguous OR unknown: bleibt im CONFIRMING ohne Timeout-Verlängerung.
        return replace(
            session, transcript=transcript, confidence=confidence
        )

    def check_timeout(
        self, session: FlowSession, *, now: float | None = None
    ) -> FlowSession:
        """Soll regelmäßig (z.B. alle 1s) gerufen werden.

        End-Zustände werden unverändert zurückgegeben.
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
        # Pre-Audit "voice_confirmed" mit Reichern voice_confirmation
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
            _LOG.warning("voice_confirmed Pre-Audit fehlgeschlagen: %s", exc)

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
            # Pending nach TTL-Ablauf nicht mehr da
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
        # Reject-Audit kommt vom Pending-Store mit reason="voice_vetoed".
        try:
            self._pending_store.reject(
                session.pending.id,
                reason="voice_vetoed",
                source=AuditSource.VOICE,
                voice_confirmation=voice_payload,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Pending-Store-Reject (vetoed) fehlgeschlagen: %s", exc)
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
            _LOG.warning("Pending-Store-Reject (timeout) fehlgeschlagen: %s", exc)
        return replace(
            session,
            state=FlowState.TIMEOUT,
            final_message=format_outcome(
                "timeout", session.pending, language=session.language
            ),
        )


# Re-Export für test-friendly access
__all__ = [
    "FlowSession",
    "FlowState",
    "SelfModFlowController",
]


# Suppress lint: `field` import wird für zukünftige FrozenSet-Defaults reserviert
_ = field
_ = UUID
