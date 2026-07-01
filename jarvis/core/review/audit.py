"""Append-only JSON-Lines audit log for the review pipeline (Phase 8.1).

Plan reference: §6.1 (audit format), §AD-11 (separate stores for audit
and run artefacts). Pattern mirrored from `jarvis/core/self_mod/audit.py`:
`threading.Lock` for concurrent safety, dedicated writer (not stdlib
`logging`), no rotation (gap-free trail).

Unlike the self-mod audit, NO redaction takes place here — the review log
contains only metadata (`run_id`, `iteration`, `phase`, `status`, `score`, …),
no user tasks or worker outputs. Those are stored under
`data/review/runs/<run_id>/` (separate store, plan §4.1).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

_LOG = logging.getLogger(__name__)


class AuditPhase(StrEnum):
    """Which pipeline step produced this entry (plan §6.1)."""

    PRECHECK = "precheck"
    WORKER_SPAWN = "worker_spawn"
    POSTCHECK = "postcheck"
    REVIEWER_SPAWN = "reviewer_spawn"


class AuditStatus(StrEnum):
    """Audit status — superset of `ReviewStatus` plus pre/post failures.

    `pass`/`needs_revision`/`fail` cover reviewer verdicts; the two
    `*_fail` values cover deterministic pre/post-check aborts that
    involved no LLM call.
    """

    PASS = "pass"  # noqa: S105 — audit status, not a secret
    NEEDS_REVISION = "needs_revision"
    FAIL = "fail"
    PRECHECK_FAIL = "precheck_fail"
    POSTCHECK_FAIL = "postcheck_fail"


def _utc_now_iso() -> str:
    """ISO-8601 with millisecond precision and explicit UTC Z suffix."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class AuditRecord(BaseModel):
    """One audit log entry, one JSON line.

    Schema faithful to the plan §6.1 format. Fields with sensible defaults
    are optional so that pre-check failures (before the reviewer call) can
    be written without `score`/`tokens`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: str = Field(default_factory=_utc_now_iso)
    run_id: str
    iteration: int = Field(ge=0)
    phase: AuditPhase
    status: AuditStatus
    issue_count: int = Field(default=0, ge=0)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cap_fired: bool = False

    def to_jsonline(self) -> str:
        """One line of JSON, without a trailing newline."""
        # `model_dump_json` with `exclude_none=False` so that `score=null`
        # appears explicitly in the log (otherwise the entry would be ambiguous).
        return self.model_dump_json()


class ReviewAudit:
    """Append-only JSON-Lines logger for review iterations.

    Default path: `data/review.log` relative to the current working
    directory. Overridable via the `path` constructor argument — e.g.
    for tests via `tmp_path`.
    """

    DEFAULT_PATH: ClassVar[Path] = Path("data") / "review.log"

    def __init__(self, path: Path | str | None = None) -> None:
        self._path: Path = (
            Path(path) if path is not None else self.DEFAULT_PATH
        )
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append_iteration(self, record: AuditRecord) -> None:
        """Writes exactly one JSON line, thread-safe.

        I/O errors are reported via `logging.warning` and NOT propagated
        — the pipeline must not crash on an audit write (same pattern as
        `SelfModAudit.record`).
        """
        try:
            line = record.to_jsonline()
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open(
                    "a", encoding="utf-8", newline=""
                ) as fh:
                    fh.write(line)
                    fh.write("\n")
        except Exception as exc:  # noqa: BLE001 — Caller darf nie crashen
            _LOG.warning(
                "ReviewAudit.append_iteration failed: %s (path=%s)",
                exc,
                self._path,
            )

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Returns the last `n` audit entries as dicts.

        I/O errors return an empty list instead of crashing. Corrupt
        lines are skipped and logged.
        """
        if n <= 0:
            return []
        try:
            if not self._path.exists():
                return []
            with self._lock, self._path.open(
                "r", encoding="utf-8"
            ) as fh:
                lines = fh.readlines()
        except Exception as exc:  # noqa: BLE001 — Lesen darf nie crashen
            _LOG.warning("ReviewAudit.tail failed: %s", exc)
            return []

        result: list[dict[str, Any]] = []
        for raw in lines[-n:]:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                result.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                _LOG.warning(
                    "Corrupt review-audit line skipped: %s", exc
                )
        return result
