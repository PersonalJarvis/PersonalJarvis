"""Shared, audited write service for explicit Wiki ingestion.

The brain tool, REST route, and curated CLI command all terminate here so they
cannot disagree about validation or claim that a no-op updated the vault. The
curator remains the only component that decides page changes, and its
``AtomicWriter`` remains the only component that touches Wiki pages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MIN_INGEST_CHARS = 12
MAX_INGEST_CHARS = 32_000
MAX_SOURCE_CHARS = 128


@dataclass(frozen=True, slots=True)
class WikiIngestOutcome:
    """Normalized result shared by tool and HTTP callers."""

    success: bool
    source: str
    applied: tuple[Path, ...] = ()
    skipped_due_to_recent_edit: tuple[Path, ...] = ()
    failed_validation: tuple[Path, ...] = ()
    blocked_pii: tuple[Path, ...] = ()
    error_code: str | None = None
    error: str | None = None

    @property
    def page_names(self) -> list[str]:
        """Return safe display names without exposing an absolute vault path."""
        return [path.name for path in self.applied]

    def render_summary(self) -> str:
        """Render the established ``wiki-ingest`` tool result format."""
        lines = [
            f"Wiki ingest done (source={self.source}):",
            f"- applied: {len(self.applied)}",
        ]
        if self.skipped_due_to_recent_edit:
            lines.append(
                "- skipped (recent user edit): "
                f"{len(self.skipped_due_to_recent_edit)}"
            )
        if self.failed_validation:
            lines.append(
                "- failed validation (rolled back): "
                f"{len(self.failed_validation)}"
            )
        if self.blocked_pii:
            lines.append(f"- blocked sensitive content: {len(self.blocked_pii)}")
        if self.applied:
            lines.append("Pages touched:")
            lines.extend(f"  - {path.name}" for path in self.applied[:10])
        return "\n".join(lines)


def _failure(source: str, code: str, error: str) -> WikiIngestOutcome:
    return WikiIngestOutcome(
        success=False,
        source=source,
        error_code=code,
        error=error,
    )


def _record_write_health(outcome: WikiIngestOutcome) -> None:
    """Record the explicit write outcome without affecting the write path."""
    try:
        from jarvis.memory.wiki.health import health

        health.record_write(
            outcome.success,
            pages=[str(path) for path in outcome.applied],
            error=outcome.error,
            source=outcome.source,
        )
    except Exception:  # noqa: BLE001 - observability must not break ingestion
        log.debug("wiki-ingest: health.record_write failed", exc_info=True)


async def ingest_wiki_text(
    *,
    curator: Any,
    text: str,
    source: str,
) -> WikiIngestOutcome:
    """Validate and ingest one self-contained fact through the live curator.

    A call succeeds only when at least one page is actually written. Recent
    edit conflicts, validation rollbacks, sensitive-content blocks, and an LLM
    salience no-op are distinct failure codes so HTTP and tool callers can
    report the real outcome.
    """
    clean_text = str(text or "").strip()
    clean_source = str(source or "").strip()

    if not clean_text:
        return _failure(clean_source, "missing-text", "missing 'text' argument")
    if len(clean_text) < MIN_INGEST_CHARS:
        return _failure(
            clean_source,
            "text-too-short",
            f"text too short ({len(clean_text)} chars; min {MIN_INGEST_CHARS}). "
            "Pass a full self-contained statement, not a single word.",
        )
    if len(clean_text) > MAX_INGEST_CHARS:
        return _failure(
            clean_source,
            "text-too-long",
            f"text too long ({len(clean_text)} chars; max {MAX_INGEST_CHARS}). "
            "Split it into multiple ingest calls.",
        )
    if (
        not clean_source
        or len(clean_source) > MAX_SOURCE_CHARS
        or any(char in clean_source for char in ("\r", "\n", "\x00"))
    ):
        return _failure(
            clean_source,
            "invalid-source",
            f"source must be a single non-empty line up to {MAX_SOURCE_CHARS} chars",
        )
    if curator is None:
        outcome = _failure(
            clean_source,
            "not-bootstrapped",
            "wiki integration not bootstrapped",
        )
        _record_write_health(outcome)
        return outcome

    log.debug(
        "wiki-ingest: source=%s len=%d body=%r",
        clean_source,
        len(clean_text),
        clean_text,
    )
    log.info(
        "wiki-ingest: ingesting %d chars (source=%s)",
        len(clean_text),
        clean_source,
    )

    try:
        result = await curator.ingest(clean_text, clean_source)
    except Exception as exc:  # noqa: BLE001 - boundary reports a clean outcome
        log.warning("wiki-ingest: curator.ingest raised %s", exc)
        outcome = _failure(
            clean_source,
            "curator-failed",
            f"curator ingest failed: {exc}",
        )
        _record_write_health(outcome)
        return outcome

    applied = tuple(Path(path) for path in (getattr(result, "applied", ()) or ()))
    skipped = tuple(
        Path(path)
        for path in (getattr(result, "skipped_due_to_recent_edit", ()) or ())
    )
    failed = tuple(
        Path(path)
        for path in (getattr(result, "failed_validation", ()) or ())
    )
    blocked = tuple(
        Path(path) for path in (getattr(result, "blocked_pii", ()) or ())
    )

    if applied:
        outcome = WikiIngestOutcome(
            success=True,
            source=clean_source,
            applied=applied,
            skipped_due_to_recent_edit=skipped,
            failed_validation=failed,
            blocked_pii=blocked,
        )
    elif blocked:
        outcome = WikiIngestOutcome(
            success=False,
            source=clean_source,
            skipped_due_to_recent_edit=skipped,
            failed_validation=failed,
            blocked_pii=blocked,
            error_code="sensitive-content-blocked",
            error=(
                "nothing was stored: the write was blocked because sensitive "
                "content was detected"
            ),
        )
    elif failed:
        outcome = WikiIngestOutcome(
            success=False,
            source=clean_source,
            skipped_due_to_recent_edit=skipped,
            failed_validation=failed,
            blocked_pii=blocked,
            error_code="validation-failed",
            error="nothing was stored: every proposed page failed validation",
        )
    elif skipped:
        outcome = WikiIngestOutcome(
            success=False,
            source=clean_source,
            skipped_due_to_recent_edit=skipped,
            failed_validation=failed,
            blocked_pii=blocked,
            error_code="recent-edit-conflict",
            error=(
                "nothing was stored: every proposed page had a recent user edit; "
                "retry after reviewing that edit"
            ),
        )
    else:
        outcome = _failure(
            clean_source,
            "nothing-stored",
            "nothing was stored: the curator judged the content not salient "
            "enough for the wiki",
        )

    _record_write_health(outcome)
    log.info(
        "wiki-ingest: applied=%d skipped=%d failed=%d blocked=%d",
        len(applied),
        len(skipped),
        len(failed),
        len(blocked),
    )
    return outcome


__all__ = [
    "MAX_INGEST_CHARS",
    "MAX_SOURCE_CHARS",
    "MIN_INGEST_CHARS",
    "WikiIngestOutcome",
    "ingest_wiki_text",
]
