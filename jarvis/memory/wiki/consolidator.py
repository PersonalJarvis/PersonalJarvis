"""Stage-2 consolidator — the body-aware ADD/UPDATE/NOOP/INVALIDATE judge.

Drains pending candidate facts from the :class:`CandidateJournal` in
batches, retrieves the k-nearest existing pages per candidate (FTS5/BM25
via :class:`VaultSearch` + subject-slug overlap — no embeddings, no new
dependency), shows the judge their FULL BODIES (undoing the legacy
curator's ``del repo`` blindness), and applies its decisions through the
shared guarded write pipeline (``WikiCurator.apply_external_updates``:
link demotion → AtomicWriter backup/secret-guard/validate/rollback/FTS).

Decision semantics (vocab: ``constants.CURATOR_DECISIONS``):

- ``add``      → create a new page (entities/concepts/projects).
- ``update``   → merge the fact into an existing page IN PLACE; the
                 prompt requires every existing fact/section to survive.
- ``noop``     → the vault already knows this; journal row closed.
- ``invalidate`` → the fact contradicts an existing page: the superseded
                 page gets frontmatter ``valid_until`` + ``superseded-by``
                 (set mechanically here, never by the LLM) — invalidate,
                 never delete (Zep pattern).

Every candidate leaves the batch with an explicit journal status —
``consolidated`` / ``rejected`` / ``skipped`` — nothing is dropped
silently. Runs only inside the CuratorScheduler's lock+cooldown gates
as a background task (AP-9).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.brain.streaming import aggregate, is_length_truncated
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.memory.wiki.constants import CURATOR_DECISIONS
from jarvis.memory.wiki.curator_llm import (
    _extract_json_array,
    _resolve_provider_and_model,
    instantiate_curator_brain,
)
from jarvis.memory.wiki.intent import match_wiki_intent
from jarvis.memory.wiki.journal import JournalRow, normalise_subjects
from jarvis.memory.wiki.prompt import (
    build_consolidator_prompt,
    resolve_user_entity_slug,
)
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.telemetry import telemetry
from jarvis.memory.wiki.wikilink import extract_wikilinks

if TYPE_CHECKING:
    from jarvis.core.config import JarvisConfig
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.journal import CandidateJournal
    from jarvis.memory.wiki.search import VaultSearch

log = logging.getLogger(__name__)

# Page directories a decision target may live in. The judge never writes
# sessions (conversation facts are durable knowledge, not session digests)
# and never _archive (frozen).
_TARGET_DIRS = ("entities", "concepts", "projects")

_GRAPH_TARGET_DIR_BY_KIND = {
    "person": "entities",
    "asset": "entities",
    "place": "entities",
    "organization": "entities",
    "project": "projects",
}

# Numeric claims are a small but damaging hallucination class: a page about an
# RTX 5070 Ti was once embellished with unsupported "24 GB VRAM" prose.  Keep
# the matcher deliberately lexical and deterministic so Stage 2 can reject the
# response and try another provider without a second model call.
_NUMERIC_VALUE_RE = re.compile(r"(?<!\d)\d+(?:[.,:/-]\d+)*(?:\s*%)?(?!\d)")
_ORDERED_LIST_PREFIX_RE = re.compile(r"^\s*\d+[.)]\s+")
# Inline code carries identifiers (``session `f260abcc-…```` in a Sources
# line), not numeric claims about the world. Fragmenting a cited UUID into
# pseudo-numbers once wedged the whole provider chain: every judge that
# followed the schema's citation style was rejected for "unsupported"
# digit runs of its own source reference.
_INLINE_CODE_SPAN_RE = re.compile(r"(`+)([^`\n]+?)\1")
_SCHEMA_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SCHEMA_DATE_FIELDS = frozenset(
    {"created", "updated", "started", "last_activity", "valid_until"}
)
_FOCUS_EVIDENCE_RE = re.compile(
    r"^Evidence user turn \[[^\]\r\n]*\]:\s*(.+)$",
    re.MULTILINE,
)
_MARKDOWN_FACT_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_UNSUPPORTED_EVIDENCE_REASON_RE = re.compile(
    r"(?:unsupported by (?:the )?user evidence|"
    r"not (?:directly )?supported by (?:the )?user evidence|"
    r"user evidence (?:does not|doesn't|cannot) support)",
    re.IGNORECASE,
)
_EXPLICIT_PERSISTENCE_CLAUSE_RE = re.compile(
    r"(?:"
    r"\b(?:remember|note(?:\s+down)?|save|record)\s+"
    r"(?:(?:this|it)\s+)?that\b|"
    r"\b(?:merk(?:e)?\s+dir|notier(?:e)?|speicher(?:e)?|"
    r"halt(?:e)?\s+fest)\s*[,;:]?\s*dass\b|"
    r"\beintrag(?:en|e|st|t)\b.{0,40}?\bdass\b|"
    r"\b(?:fueg(?:e)?|füg(?:e)?)\b.{0,40}?"  # i18n-allow: German input vocabulary
    r"\bhinzu\s*[,;:]?\s*dass\b|"  # i18n-allow: German input vocabulary
    r"\bhinzuf(?:ue|ü)gen\s*"  # i18n-allow: German input vocabulary
    r"[,;:]?\s*dass\b|"  # i18n-allow: German input vocabulary
    r"\b(?:recuerda|anota|guarda|registra|añade|anade|agrega)\s*"
    r"[,;:]?\s*que\b"
    r")",
    re.IGNORECASE,
)


def _canonical_decision_order(parsed: list[Any]) -> list[Any]:
    """Stable-sort ``update`` decisions ahead of everything else.

    The judge's array order carries no semantics, but primary/secondary
    classification is positional: ``{add companion, update profile}`` in
    that order once read the add as the primary and rejected the update
    as an illegal second primary — while the identical pair in reverse
    order was the legal companion shape. Canonicalising makes both
    orderings validate (and execute) identically.
    """
    return sorted(
        parsed,
        key=lambda item: (
            0 if isinstance(item, dict) and item.get("decision") == "update" else 1
        ),
    )


@dataclass(frozen=True, slots=True)
class _BatchOutcome:
    """Internal Stage-2 result without collapsing transient states."""

    processed: int = 0
    deferred: int = 0
    transient: int = 0
    unavailable: bool = False
    truncated: bool = False
    rejected: int = 0

    def merge(self, other: _BatchOutcome) -> _BatchOutcome:
        return _BatchOutcome(
            processed=self.processed + other.processed,
            deferred=self.deferred + other.deferred,
            transient=self.transient + other.transient,
            unavailable=self.unavailable or other.unavailable,
            truncated=self.truncated or other.truncated,
            rejected=self.rejected + other.rejected,
        )


class Consolidator:
    """Batched journal drain through the body-aware judge."""

    def __init__(
        self,
        *,
        config: JarvisConfig,
        journal: CandidateJournal,
        curator: WikiCurator,
        search: VaultSearch | None,
        vault_root: Path,
        registry: BrainProviderRegistry | None = None,
        # A judged batch answers with FULL page bodies per add/update, so
        # 20 candidates cannot fit any sane output budget — every provider
        # truncated and the bisection retry burned the whole chain first
        # (live 2026-07-17). Eight keeps the response inside
        # ``[memory.wiki.curator] max_output_tokens`` on the first attempt.
        batch_limit: int = 8,
        k_nearest: int = 4,
        on_run_complete: Any = None,
    ) -> None:
        self._root_cfg = config
        self._curator_cfg = config.memory.wiki.curator
        self._journal = journal
        self._curator = curator
        self._search = search
        self._vault_root = Path(vault_root).resolve()
        self._registry = registry if registry is not None else BrainProviderRegistry()
        self._credential_filter = registry is None
        self._batch_limit = max(1, int(batch_limit))
        self._k_nearest = max(1, int(k_nearest))
        # Optional callback fired after a completed run (B7 wires the
        # self-documentation refresh here). Called best-effort.
        self._on_run_complete = on_run_complete
        self._brain: Any = None
        self._resolved_provider: str | None = None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def run_once(self, *, review_keys: Sequence[str] | None = None) -> str:
        """Drain one batch, optionally scoped to exact capture reviews."""
        rows = self._journal.pending(
            limit=self._batch_limit,
            review_keys=review_keys,
        )
        if not rows:
            return "journal-empty"

        # A captured row without persisted user evidence predates the grounded
        # policy. It is unsafe to let Stage 2 guess, and a policy-v3 backfill can
        # recreate it from the transcript. Direct/internal journal rows without
        # a capture review retain their legacy path for compatibility.
        ungrounded = [
            row for row in rows if row.review_key and not row.evidence_excerpt
        ]
        if ungrounded:
            self._journal.mark(
                [row.id for row in ungrounded],
                status="rejected",
            )
            telemetry.inc("wiki_consolidator_rejected_missing_evidence", len(ungrounded))
            log.warning(
                "Consolidator: rejected %d captured candidate(s) without "
                "user evidence; policy-v3 backfill can review the source",
                len(ungrounded),
            )
        grounded = [row for row in rows if row not in ungrounded]
        outcome = await self._process_rows(grounded)
        telemetry.inc("wiki_consolidator_runs")

        if self._on_run_complete is not None:
            try:
                maybe = self._on_run_complete()
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception as exc:  # noqa: BLE001
                log.warning("Consolidator: on_run_complete hook failed: %s", exc)

        if outcome.truncated:
            return "judge-truncated"
        if outcome.unavailable:
            return "judge-unavailable"
        if outcome.rejected and not outcome.processed:
            return f"judge-rejected:{outcome.rejected}"
        if outcome.transient:
            return f"journal-transient:{outcome.transient}"
        if outcome.deferred:
            return f"journal-deferred:{outcome.deferred}"
        if ungrounded and not grounded:
            return f"journal-evidence-rejected:{len(ungrounded)}"
        return f"journal-batch:{len(rows)}"

    async def _process_rows(self, rows: list[JournalRow]) -> _BatchOutcome:
        """Judge rows, bisecting capacity failures without losing candidates."""
        if not rows:
            return _BatchOutcome()
        neighbours = self._collect_neighbours(rows)
        decisions = await self._judge(rows, neighbours)
        if decisions is None:
            # Provider timeout/unavailability is not a content verdict. Keep
            # every candidate pending for the next bounded trigger.
            return _BatchOutcome(unavailable=True)
        if decisions in ("truncated", "rejected"):
            if len(rows) == 1:
                # A single overlong or judge-rejected result remains
                # observable and retryable; never convert it into terminal
                # data loss.
                if decisions == "rejected":
                    return _BatchOutcome(rejected=1)
                return _BatchOutcome(truncated=True)
            midpoint = len(rows) // 2
            left = await self._process_rows(rows[:midpoint])
            right = await self._process_rows(rows[midpoint:])
            return left.merge(right)

        deferred, transient = await self._execute(
            rows,
            decisions,
            f"journal-batch:{len(rows)}",
            neighbours=neighbours,
        )
        return _BatchOutcome(
            processed=len(rows) - deferred - transient,
            deferred=deferred,
            transient=transient,
        )

    # ------------------------------------------------------------------
    # retrieval
    # ------------------------------------------------------------------

    def _collect_neighbours(self, rows: list[JournalRow]) -> dict[str, str]:
        """k-nearest existing pages per candidate, deduped across the batch.

        Two complementary signals, no embeddings (CLOUD.md base install):
        subject-slug overlap (deterministic — ``subjects=("lena",)`` pulls
        ``entities/lena.md`` when it exists) and FTS5/BM25 hits on the fact
        text. Returns ``{vault-relative-posix-path: full page text}``.
        """
        found: dict[str, str] = {}

        def _add(rel_path: str) -> None:
            normalised = str(rel_path or "").replace("\\", "/")
            rel = PurePosixPath(normalised)
            parts = rel.parts
            if (
                rel.is_absolute()
                or len(parts) != 2
                or parts[0] not in _TARGET_DIRS
                or parts[1].startswith(".")
                or not parts[1].endswith(".md")
                or normalise_subjects((parts[1][:-3],)) != (parts[1][:-3],)
            ):
                return
            safe_rel = rel.as_posix()
            if safe_rel in found or len(found) >= self._k_nearest * len(rows):
                return
            try:
                abs_path = (self._vault_root / Path(*parts)).resolve()
                abs_path.relative_to(self._vault_root)
                if abs_path.is_file():
                    found[safe_rel] = abs_path.read_text(encoding="utf-8")
            except (OSError, ValueError) as exc:
                log.debug("Consolidator: cannot read neighbour %s: %s", safe_rel, exc)

        for row in rows:
            for subject in row.subjects:
                safe = normalise_subjects((subject,))
                if not safe:
                    continue
                slug = safe[0]
                for directory in _TARGET_DIRS:
                    _add(f"{directory}/{slug}.md")

        if self._search is not None:
            for row in rows:
                try:
                    hits = self._search.search(row.fact)
                except Exception as exc:  # noqa: BLE001
                    # Per-candidate failure only: the OTHER candidates in the
                    # batch must still get their FTS neighbours (a break here
                    # would judge them context-blind).
                    log.debug("Consolidator: FTS search failed: %s", exc)
                    continue
                for hit in hits[: self._k_nearest]:
                    rel = str(getattr(hit, "path", "") or "")
                    if not rel:
                        continue
                    rel = rel.replace("\\", "/")
                    # _archive pages are frozen history — never judge targets.
                    if rel.startswith("_archive/"):
                        continue
                    _add(rel)

        return found

    # ------------------------------------------------------------------
    # judge
    # ------------------------------------------------------------------

    async def _judge(
        self, rows: list[JournalRow], neighbours: dict[str, str],
    ) -> list[dict[str, Any]] | str | None:
        """One batched LLM call.

        Returns the decision list, ``"truncated"`` (output-cap hit),
        ``"rejected"`` (every provider answered but failed validation),
        or ``None`` (no provider reachable).
        """
        user_slug = resolve_user_entity_slug(
            getattr(
                self._root_cfg.memory.wiki.session_rollup,
                "user_entity_slug",
                "",
            )
        )
        system, user = build_consolidator_prompt(
            rows, neighbours, user_entity_slug=user_slug,
        )
        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user),),
            system=system,
            max_tokens=int(self._curator_cfg.max_output_tokens),
            temperature=0.3,
            stream=True,
        )

        start_ns = time.time_ns()
        from jarvis.memory.wiki.provider_chain import (
            build_wiki_provider_chain,
            complete_with_fallback,
            credential_ready_wiki_providers,
        )

        available = set(self._registry.available())
        chain = build_wiki_provider_chain(
            primary=(self._curator_cfg.provider.strip() or self._root_cfg.brain.primary),
            model_override=self._curator_cfg.model,
            available=available,
            credential_ready=(
                credential_ready_wiki_providers(
                    available=available,
                    config=self._root_cfg,
                )
                if self._credential_filter
                else available
            ),
        )
        rejection_reasons: list[str] = []

        def _validate_response(agg: Any) -> str | None:
            if is_length_truncated(agg.finish_reason, agg.text):
                reason = (
                    f"truncated structured output ({len(agg.text or '')} chars, "
                    f"finish_reason={agg.finish_reason!r})"
                )
                rejection_reasons.append(reason)
                return reason
            try:
                parsed = _canonical_decision_order(_extract_json_array(agg.text))
            except ValueError as exc:
                reason = f"malformed JSON array: {exc}"
                rejection_reasons.append(reason)
                return reason
            reason = self._validate_decisions(parsed, rows, neighbours=neighbours)
            if reason is not None:
                rejection_reasons.append(reason)
                return reason
            return None

        result = await complete_with_fallback(
            registry=self._registry,
            chain=chain,
            request=request,
            timeout_s=float(self._curator_cfg.timeout_s),
            label="Consolidator",
            aggregate=aggregate,
            validate=_validate_response,
        )
        if result is None:
            if any(reason.startswith("truncated") for reason in rejection_reasons):
                log.warning(
                    "Consolidator: every provider hit the output-token cap or failed "
                    "after a truncated response; the batch will be split or kept pending"
                )
                telemetry.inc("wiki_writes_blocked_truncated")
                return "truncated"
            if rejection_reasons:
                # At least one provider answered but every answer failed
                # validation. Retrying the identical batch would burn the
                # chain on the same verdict forever; bisecting isolates a
                # poison candidate to a single-row batch while the rest of
                # the queue keeps draining.
                telemetry.inc("wiki_writes_blocked_rejected")
                return "rejected"
            return None
        agg, self._resolved_provider = result

        if is_length_truncated(agg.finish_reason, agg.text):
            telemetry.inc("wiki_writes_blocked_truncated")
            log.warning(
                "Consolidator: judge output hit the token cap "
                "(finish_reason=%r, %d chars) — batch will be split or kept pending",
                agg.finish_reason, len(agg.text or ""),
            )
            return "truncated"

        try:
            parsed = _extract_json_array(agg.text)
        except ValueError as exc:
            log.warning("Consolidator: malformed judge JSON (%s)", exc)
            return None

        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        log.info(
            "Consolidator: judge returned %d decision(s) for %d candidate(s) "
            "in %dms", len(parsed), len(rows), duration_ms,
        )
        return [
            item
            for item in _canonical_decision_order(parsed)
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------
    # decision execution
    # ------------------------------------------------------------------

    async def _execute(
        self,
        rows: list[JournalRow],
        decisions: list[dict[str, Any]],
        label: str,
        *,
        neighbours: dict[str, str],
    ) -> tuple[int, int]:
        """Apply one judged batch and return ``(deferred, transient)`` counts."""
        validation_error = self._validate_decisions(
            decisions,
            rows,
            neighbours=neighbours,
        )
        if validation_error is not None:
            # Provider output is untrusted.  This path is a final defensive
            # guard in case a future caller bypasses ``_judge`` validation:
            # never turn a partial/unusable response into a content verdict.
            log.warning(
                "Consolidator: refusing unusable decision batch (%s); "
                "leaving every candidate pending",
                validation_error,
            )
            return 0, len(rows)

        by_id = {row.id: row for row in rows}
        row_order = {row.id: index for index, row in enumerate(rows)}
        judged_ids: set[int] = set()
        updates_by_candidate: dict[int, list[PageUpdate]] = {}
        # candidate id -> (decision or None when unwritable, target or None)
        write_plan: dict[int, tuple[str | None, str | None]] = {}
        noop_ids: list[int] = []
        required_targets: dict[int, set[str]] = {}
        # Secondary targets (extra writes beyond a candidate's primary
        # decision); counted only after the write actually lands.
        secondary_invalidations: list[str] = []
        secondary_adds: list[str] = []
        # Companion topic pages (graph-visibility secondary "add"s) are
        # written in their OWN non-atomic call, never bundled into the
        # primary's all-or-nothing transaction: a failing bonus page must
        # not block the durable primary fact.
        companion_by_candidate: dict[int, list[PageUpdate]] = {}

        # Never submit two independently generated full-page bodies for the
        # same target in one AtomicWriter call: the later body was based on the
        # same old page and can overwrite the earlier fact. Keep later
        # candidates pending; the next pass judges them against the landed page.
        targets_by_candidate: dict[int, list[str]] = {}
        for item in decisions:
            cid = item.get("candidate_id")
            decision = item.get("decision")
            if (
                not isinstance(cid, int)
                or cid not in by_id
                or not isinstance(decision, str)
                or decision not in CURATOR_DECISIONS
                or decision == "noop"
            ):
                continue
            target = self._safe_target(item.get("target"))
            if target is not None:
                targets_by_candidate.setdefault(cid, []).append(target)

        claimed_targets: set[str] = set()
        deferred_ids: set[int] = set()
        duplicate_target_ids: set[int] = set()
        for cid in sorted(targets_by_candidate, key=row_order.__getitem__):
            targets = targets_by_candidate[cid]
            unique = set(targets)
            if len(unique) != len(targets):
                duplicate_target_ids.add(cid)
                continue
            if unique & claimed_targets:
                deferred_ids.add(cid)
                continue
            claimed_targets.update(unique)

        for item in decisions:
            cid = item.get("candidate_id")
            decision = item.get("decision")
            if not isinstance(cid, int) or cid not in by_id:
                continue
            if not isinstance(decision, str) or decision not in CURATOR_DECISIONS:
                continue
            if cid in deferred_ids or cid in duplicate_target_ids:
                continue
            # One PRIMARY decision per candidate; "invalidate" and companion
            # "add" items are allowed as secondary actions — a contradiction
            # typically ADDs/UPDATEs the corrected page AND invalidates the
            # superseded one, and a profile update may create the missing
            # topic page in the same batch, for the same candidate.
            is_secondary = cid in judged_ids
            required_place_update = (
                is_secondary
                and decision == "update"
                and self._is_required_place_secondary_update(
                    by_id[cid], item.get("target")
                )
            )
            if (
                is_secondary
                and decision not in ("invalidate", "add")
                and not required_place_update
            ):
                continue
            judged_ids.add(cid)

            if decision == "noop":
                noop_ids.append(cid)
                continue

            target = self._safe_target(item.get("target"))
            if target is None:
                if not is_secondary:
                    # Unusable target — judged but unwritable. ``None`` is
                    # the sentinel (deliberately not a vocab string, so it
                    # can never leak into telemetry or the journal).
                    write_plan[cid] = (None, None)
                continue

            if decision in ("add", "update"):
                new_body = item.get("new_body")
                if not isinstance(new_body, str) or not new_body.strip():
                    if not is_secondary:
                        write_plan[cid] = (None, None)
                    continue
                new_body = self._with_source_marker(new_body, by_id[cid])
                update = PageUpdate(
                    target_path=Path(target),
                    operation="create" if decision == "add" else "update",
                    new_body=new_body,
                    reason=str(item.get("reason", ""))[:200],
                )
                if is_secondary:
                    if required_place_update:
                        # The two existing sides of a residence edge are one
                        # candidate-level transaction: either both links land
                        # or neither page changes.
                        updates_by_candidate.setdefault(cid, []).append(update)
                    else:
                        companion_by_candidate.setdefault(cid, []).append(update)
                        secondary_adds.append(target)
                    if (
                        target == self._required_graph_target(by_id[cid])
                        or target
                        in self._required_place_write_targets(by_id[cid])
                    ):
                        required_targets.setdefault(cid, set()).add(target)
                else:
                    updates_by_candidate.setdefault(cid, []).append(update)
                    write_plan[cid] = (decision, target)
                    # The journal always tracks the primary. A graph-mandatory
                    # companion is added above as a required target; optional
                    # companion pages retain their best-effort semantics.
                    required_targets.setdefault(cid, set()).add(target)
            else:  # invalidate
                superseded_by = str(item.get("superseded_by", "") or "").strip()
                invalidated = self._build_invalidation(target, superseded_by)
                if invalidated is None:
                    if not is_secondary:
                        write_plan[cid] = (None, None)
                    continue
                updates_by_candidate.setdefault(cid, []).append(invalidated)
                required_targets.setdefault(cid, set()).add(target)
                if not is_secondary:
                    write_plan[cid] = ("invalidate", target)
                else:
                    secondary_invalidations.append(target)

        # Apply all writes through the shared guarded pipeline.
        applied_rel: set[str] = set()
        rejected_rel: set[str] = set()
        recent_rel: set[str] = set()
        write_cids = set(updates_by_candidate) | set(companion_by_candidate)
        for cid in sorted(write_cids, key=row_order.__getitem__):
            # Companion topic pages land FIRST, in their own non-atomic call:
            # the primary body's [[link]] to them then resolves from disk,
            # and a failing companion can never roll back the durable primary
            # fact. A graph-mandatory companion keeps the journal row pending
            # for retry; an optional companion remains best-effort.
            companions = companion_by_candidate.get(cid)
            if companions:
                companion_result = await self._curator.apply_external_updates(
                    companions,
                    source_label=f"{label}:candidate:{cid}:companion",
                    verb="merge",
                    all_or_nothing=False,
                )
                applied_rel.update(
                    self._rel(p) for p in companion_result.applied
                )
                failed_companions = [
                    self._rel(p)
                    for p in (
                        *companion_result.blocked_pii,
                        *companion_result.failed_validation,
                        *companion_result.skipped_due_to_recent_edit,
                    )
                ]
                if failed_companions:
                    log.warning(
                        "Consolidator: companion topic page(s) %s for "
                        "candidate %d did not land; the primary write remains "
                        "durable and mandatory graph work stays pending",
                        ", ".join(failed_companions),
                        cid,
                    )
            updates = updates_by_candidate.get(cid)
            if not updates:
                continue
            # A contradiction can create/update one page and invalidate a
            # second.  Those writes are one candidate-level transaction: a
            # validation failure or edit lock on either page rolls back both.
            result = await self._curator.apply_external_updates(
                updates,
                source_label=f"{label}:candidate:{cid}",
                verb="merge",
                all_or_nothing=True,
            )
            applied_rel.update(self._rel(p) for p in result.applied)
            rejected_rel.update(
                self._rel(p)
                for p in (*result.blocked_pii, *result.failed_validation)
            )
            recent_rel.update(
                self._rel(p) for p in result.skipped_due_to_recent_edit
            )

        # Secondary invalidations are counted on landed writes only — never
        # before the writer's verdict (a blocked/skipped page must not
        # inflate the counter).
        for target in secondary_invalidations:
            if target in applied_rel:
                telemetry.inc("wiki_consolidator_invalidate")
        for target in secondary_adds:
            if target in applied_rel:
                telemetry.inc("wiki_consolidator_add")

        transient_ids: set[int] = set()
        # Close out every candidate unless it is intentionally deferred or hit
        # a transient human-edit lock. Those states remain pending and visible.
        for cid in by_id:
            if cid in deferred_ids:
                continue
            if cid in duplicate_target_ids:
                self._journal.mark([cid], status="skipped")
                log.warning(
                    "Consolidator: candidate %d proposed the same target twice; skipped",
                    cid,
                )
                continue
            if cid in noop_ids:
                self._journal.mark([cid], status="consolidated", decision="noop")
                telemetry.inc("wiki_consolidator_noop")
                continue
            plan = write_plan.get(cid)
            if plan is None:
                # Judge returned nothing usable for this candidate.
                self._journal.mark([cid], status="skipped")
                log.debug("Consolidator: candidate %d unjudged — skipped", cid)
                continue
            decision, target = plan
            if decision is None or target is None:
                self._journal.mark([cid], status="skipped")
                continue
            required = required_targets.get(cid, {target})
            if required and required.issubset(applied_rel):
                self._journal.mark(
                    [cid], status="consolidated",
                    decision=decision,  # type: ignore[arg-type]
                    target_path=target,
                )
                telemetry.inc(f"wiki_consolidator_{decision}")
            elif required & rejected_rel:
                self._journal.mark([cid], status="rejected", target_path=target)
                log.warning(
                    "Consolidator: write for candidate %d rejected "
                    "(secret guard / validation) — %s", cid, target,
                )
            elif required & recent_rel:
                transient_ids.add(cid)
                log.info(
                    "Consolidator: candidate %d remains pending after a recent edit",
                    cid,
                )
            else:
                # An unexpected partial/no-write outcome is observable as a
                # retryable pending row, not silently converted to a verdict.
                transient_ids.add(cid)
                log.warning(
                    "Consolidator: candidate %d had no complete writer outcome; "
                    "leaving it pending",
                    cid,
                )

        return len(deferred_ids), len(transient_ids)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _with_source_marker(body: str, row: JournalRow) -> str:
        """Attach deterministic transcript provenance to a proposed page body."""
        turn_id = str(row.evidence_turn_id or "").strip()
        session_id = str(row.session_id or "").strip()
        if not turn_id:
            return body
        if session_id:
            marker = (
                f"- Realtime transcript: session `{session_id}`, "
                f"turn `{turn_id}`."
            )
        else:
            marker = f"- Conversation transcript: turn `{turn_id}`."
        if marker in body:
            return body

        heading = "## Sources"
        heading_at = body.find(heading)
        if heading_at < 0:
            return body.rstrip() + f"\n\n{heading}\n\n{marker}\n"
        line_end = body.find("\n", heading_at + len(heading))
        if line_end < 0:
            return body.rstrip() + f"\n\n{marker}\n"
        insert_at = line_end + 1
        return body[:insert_at] + f"\n{marker}\n" + body[insert_at:]

    def _safe_target(self, raw: Any) -> str | None:
        """Normalise a judge-provided target to a vault-relative .md path."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        rel = raw.strip().replace("\\", "/").lstrip("/")
        if not rel.endswith(".md"):
            rel += ".md"
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] not in _TARGET_DIRS:
            return None
        slug = parts[1][:-3]
        if normalise_subjects((slug,)) != (slug,):
            return None
        return rel

    def _graph_target(self, row: JournalRow) -> str | None:
        """Return the canonical or existing topic page for ``row``."""
        directory = _GRAPH_TARGET_DIR_BY_KIND.get(str(row.kind or ""))
        if directory is None:
            return None
        user_slug = resolve_user_entity_slug(
            getattr(
                self._root_cfg.memory.wiki.session_rollup,
                "user_entity_slug",
                "",
            )
        )
        topic_slug = next(
            (
                subject
                for subject in row.subjects
                if subject not in {user_slug, "user"}
            ),
            "",
        )
        if not topic_slug:
            return None
        for candidate_dir in _TARGET_DIRS:
            candidate = self._vault_root / candidate_dir / f"{topic_slug}.md"
            if candidate.is_file():
                return f"{candidate_dir}/{topic_slug}.md"
        return f"{directory}/{topic_slug}.md"

    def _required_graph_target(self, row: JournalRow) -> str | None:
        """Return the missing page required to make ``row`` graph-visible."""
        target = self._graph_target(row)
        if target is None or (self._vault_root / target).is_file():
            return None
        return target

    def _required_place_write_targets(self, row: JournalRow) -> set[str]:
        """Return user/place pages that must change to establish both links."""
        if row.kind != "place":
            return set()
        graph_target = self._graph_target(row)
        if graph_target is None:
            return set()
        user_slug = resolve_user_entity_slug(
            getattr(
                self._root_cfg.memory.wiki.session_rollup,
                "user_entity_slug",
                "",
            )
        )
        profile_target = f"entities/{user_slug}.md"
        topic_slug = Path(graph_target).stem
        required: set[str] = set()
        for target, linked_slug in (
            (graph_target, user_slug),
            (profile_target, topic_slug),
        ):
            path = self._vault_root / target
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                required.add(target)
                continue
            if not self._body_links_to_slug(body, linked_slug):
                required.add(target)
        return required

    def _is_required_place_secondary_update(
        self,
        row: JournalRow,
        raw_target: Any,
    ) -> bool:
        """Allow only the missing half of a residence-link update pair."""
        target = self._safe_target(raw_target)
        return (
            target is not None
            and target in self._required_place_write_targets(row)
            and (self._vault_root / target).is_file()
        )

    @staticmethod
    def _body_links_to_slug(body: str, slug: str) -> bool:
        """Return whether ``body`` contains a wikilink to ``slug``."""
        for raw_link in extract_wikilinks(body):
            target = raw_link.split("|", 1)[0].strip().removesuffix(".md")
            if target.rsplit("/", 1)[-1] == slug:
                return True
        return False

    def _validate_decisions(
        self,
        parsed: list[Any],
        rows: Sequence[JournalRow],
        *,
        neighbours: dict[str, str],
    ) -> str | None:
        """Validate that a judge response is complete and safely writable.

        A transport-successful JSON array is not necessarily a usable answer.
        Reject the whole response (and let the provider chain try another
        family) unless every candidate has exactly one valid primary decision.
        Secondary actions are limited to distinct invalidations, companion
        "add" pages, and the exact second existing-page update required to
        establish a bidirectional residence link.
        """
        expected = {row.id for row in rows}
        by_id = {row.id: row for row in rows}
        primary_seen: set[int] = set()
        primary_decisions: dict[int, str] = {}
        targets_seen: dict[int, set[str]] = {}

        for item in parsed:
            if not isinstance(item, dict):
                return "decision array contains a non-object item"
            cid = item.get("candidate_id")
            if type(cid) is not int or cid not in expected:
                return "decision array contains an unknown candidate_id"
            decision = item.get("decision")
            if not isinstance(decision, str) or decision not in CURATOR_DECISIONS:
                return "decision array contains an invalid decision"

            secondary = cid in primary_seen
            if secondary:
                # Secondary actions: extra invalidations for a contradiction,
                # or a companion "add" that creates a missing topic page in
                # the same batch. A narrowly-scoped second update is permitted
                # only when an existing residence page/profile lacks its edge.
                required_place_update = (
                    decision == "update"
                    and self._is_required_place_secondary_update(
                        by_id[cid], item.get("target")
                    )
                )
                if primary_decisions[cid] == "noop" or (
                    decision not in ("invalidate", "add")
                    and not required_place_update
                ):
                    return (
                        f"candidate {cid} has more than one primary decision "
                        f"(primary={primary_decisions[cid]}, "
                        f"extra={decision} -> {item.get('target', '')!r})"
                    )
            else:
                primary_seen.add(cid)
                primary_decisions[cid] = decision

            if decision == "noop":
                if secondary or any(
                    item.get(field) for field in ("target", "new_body", "superseded_by")
                ):
                    return "noop decision contains write fields"
                row = by_id[cid]
                if self._has_explicit_persistence_request(row):
                    reason = str(item.get("reason", "") or "").strip()
                    exact_duplicate = self._fact_exists_unchanged(
                        row.fact,
                        neighbours.values(),
                    )
                    unsupported = bool(
                        _UNSUPPORTED_EVIDENCE_REASON_RE.search(reason)
                    )
                    if not exact_duplicate and not unsupported:
                        return (
                            "explicit wiki persistence request cannot be noop "
                            "without an exact duplicate or unsupported user evidence"
                        )
                continue

            target = self._safe_target(item.get("target"))
            if target is None:
                return "write decision contains an unsafe target"
            candidate_targets = targets_seen.setdefault(cid, set())
            if target in candidate_targets:
                return "candidate writes the same target more than once"
            candidate_targets.add(target)
            target_path = self._vault_root / target

            if decision in ("add", "update"):
                required_place_update = (
                    secondary
                    and decision == "update"
                    and self._is_required_place_secondary_update(
                        by_id[cid], target
                    )
                )
                if secondary and decision != "add" and not required_place_update:
                    return "secondary decision must be an add or an invalidation"
                body = item.get("new_body")
                if not isinstance(body, str) or not body.strip():
                    return "page decision is missing a full new_body"
                if decision == "add" and target_path.exists():
                    return "add decision targets an existing page"
                if decision == "update":
                    if not target_path.is_file():
                        return "update decision targets a missing page"
                    if not self._preserves_existing_page(target_path, body):
                        return "update decision removes existing page content"
                unsupported = self._unsupported_numeric_values(
                    body,
                    row=by_id[cid],
                    existing_path=target_path if target_path.is_file() else None,
                    neighbours=neighbours.values(),
                )
                if unsupported:
                    values = ", ".join(sorted(unsupported))
                    return f"page decision contains unsupported numeric values: {values}"
                continue

            # INVALIDATE bodies are built mechanically, never accepted from
            # model output.  The optional replacement reference must be a safe
            # page slug before it can enter YAML frontmatter.
            if not target_path.is_file():
                return "invalidate decision targets a missing page"
            if self._safe_superseded_slug(item.get("superseded_by", "")) is None:
                return "invalidate decision contains an unsafe superseded_by"

        if primary_seen != expected:
            return "decision array does not cover every candidate"

        for cid, row in by_id.items():
            if primary_decisions.get(cid) == "noop":
                # A noop primary may not carry secondary writes (enforced
                # above), so demanding a companion page or link repair here
                # would make every legitimate noop on a graph-visible
                # candidate structurally unanswerable — ALL providers fail
                # the same check and the row wedges the chain forever.
                continue
            graph_target = self._graph_target(row)
            if graph_target is None:
                continue
            graph_path = self._vault_root / graph_target
            graph_item = next(
                (
                    item
                    for item in parsed
                    if item.get("candidate_id") == cid
                    and item.get("decision") in ("add", "update")
                    and self._safe_target(item.get("target")) == graph_target
                ),
                None,
            )
            if not graph_path.is_file():
                if graph_item is None or graph_item.get("decision") != "add":
                    return (
                        "graph-visible fact is missing its required companion page: "
                        f"{graph_target}"
                    )

            # Non-place topics retain the missing-page invariant. Residence
            # additionally requires a durable edge in both directions even
            # when both pages already existed before this candidate.
            if row.kind != "place":
                continue
            user_slug = resolve_user_entity_slug(
                getattr(
                    self._root_cfg.memory.wiki.session_rollup,
                    "user_entity_slug",
                    "",
                )
            )
            if graph_item is not None:
                graph_body = str(graph_item.get("new_body", "") or "")
            else:
                try:
                    graph_body = graph_path.read_text(encoding="utf-8")
                except OSError:
                    graph_body = ""
            if not self._body_links_to_slug(graph_body, user_slug):
                return "place companion page does not link to the user profile"

            profile_target = f"entities/{user_slug}.md"
            profile_path = self._vault_root / profile_target
            profile_item = next(
                (
                    item
                    for item in parsed
                    if item.get("candidate_id") == cid
                    and item.get("decision") in ("add", "update")
                    and self._safe_target(item.get("target")) == profile_target
                ),
                None,
            )
            if profile_item is not None:
                profile_body = str(profile_item.get("new_body", "") or "")
            else:
                try:
                    profile_body = profile_path.read_text(encoding="utf-8")
                except OSError:
                    profile_body = ""
            topic_slug = Path(graph_target).stem
            if not self._body_links_to_slug(profile_body, topic_slug):
                return "user profile does not link to the place companion page"

            required_place_targets = self._required_place_write_targets(row)
            proposed_targets = {
                self._safe_target(item.get("target"))
                for item in parsed
                if item.get("candidate_id") == cid
                and item.get("decision") in ("add", "update")
            }
            missing_updates = required_place_targets - proposed_targets
            if missing_updates:
                return (
                    "residence link repair is missing required page update(s): "
                    + ", ".join(sorted(missing_updates))
                )
        return None

    @staticmethod
    def _has_explicit_persistence_request(row: JournalRow) -> bool:
        """Recognise Wiki writes or a narrow multilingual fact-clause request."""
        match = _FOCUS_EVIDENCE_RE.search(str(row.evidence_excerpt or ""))
        if match is None:
            return False
        focus_text = match.group(1)
        return (
            match_wiki_intent(focus_text) is not None
            or _EXPLICIT_PERSISTENCE_CLAUSE_RE.search(focus_text) is not None
        )

    def _fact_exists_unchanged(
        self,
        fact: str,
        page_bodies: Iterable[str],
    ) -> bool:
        """Return whether one existing page contains the exact fact as a line."""

        configured_user_slug = resolve_user_entity_slug(
            getattr(
                self._root_cfg.memory.wiki.session_rollup,
                "user_entity_slug",
                "",
            )
        ).replace("-", " ")
        user_prefixes = ("the user", configured_user_slug.casefold())

        def _normalise_line(value: str) -> str:
            cleaned = _MARKDOWN_FACT_PREFIX_RE.sub("", value)
            normalised = " ".join(cleaned.casefold().split()).rstrip(".!?")
            for prefix in user_prefixes:
                if prefix and normalised.startswith(f"{prefix} "):
                    return normalised[len(prefix) + 1 :]
            return normalised

        needle = _normalise_line(str(fact or ""))
        if not needle:
            return False
        return any(
            _normalise_line(line) == needle
            for body in page_bodies
            for line in str(body).splitlines()
        )

    @classmethod
    def _unsupported_numeric_values(
        cls,
        proposed: str,
        *,
        row: JournalRow,
        existing_path: Path | None,
        neighbours: Iterable[str] = (),
    ) -> set[str]:
        """Return model-added numeric values with no grounded source.

        Candidate facts, their exact user-evidence excerpt, safe subject slugs,
        the current target page, and the neighbour bodies the judge was shown
        are authoritative — a number copied from a page in the judge's own
        input is a cross-reference, not an invention, and rejecting it would
        burn every provider on a correct answer.  ISO dates in the
        schema's date frontmatter fields are bookkeeping rather than factual
        prose and are allowed; the normal create/update path supplies today's
        date there.  Markdown ordered-list indices are formatting, not claims.
        """
        grounded_text = "\n".join(
            (row.fact, row.evidence_excerpt, *row.subjects, *neighbours)
        )
        grounded = cls._numeric_values(grounded_text)
        today = _dt.date.today()
        # The Stage-2 prompt explicitly supplies this temporal context.  The
        # judge may safely render it as either an ISO date or a prose qualifier
        # such as "as of July 2026" without requiring the user to repeat it.
        grounded.update((today.isoformat(), str(today.year)))
        if existing_path is not None:
            try:
                grounded.update(
                    cls._numeric_values(existing_path.read_text(encoding="utf-8"))
                )
            except OSError:
                # The independent existence/preservation checks reject an
                # unreadable update.  Do not weaken this guard in the meantime.
                pass
        return {
            value
            for value in cls._numeric_values(proposed, ignore_schema_dates=True)
            if not cls._numeric_value_grounded(value, grounded)
        }

    @staticmethod
    def _numeric_value_grounded(value: str, grounded: set[str]) -> bool:
        """Exact match, decimal-comma/dot equivalence, or a grounded range.

        Two renderings carry no new numeric claim and must not be rejected
        (live 2026-07-17: "5 to 6 million euros" evidence, "5-6" proposal —
        the whole provider chain burned on a correct answer):

        * locale decimal separators — "1,80" and "1.80" are the same value;
        * hyphen ranges whose every endpoint is individually grounded —
          "5-6" from grounded "5" and "6".

        Everything else (new digits, new precision such as "5.6" from
        grounded 5 and 6) stays rejected.
        """
        if value in grounded:
            return True
        canonical_grounded = {v.replace(",", ".") for v in grounded}
        if value.replace(",", ".") in canonical_grounded:
            return True
        endpoints = value.rstrip("%").split("-")
        return len(endpoints) > 1 and all(
            part and part.replace(",", ".") in canonical_grounded
            for part in endpoints
        )

    @staticmethod
    def _numeric_values(
        text: str,
        *,
        ignore_schema_dates: bool = False,
    ) -> set[str]:
        """Extract exact numeric values while excluding non-claim syntax."""
        values: set[str] = set()
        in_frontmatter = False
        for index, raw_line in enumerate(text.splitlines()):
            stripped = raw_line.strip()
            if stripped == "---":
                if index == 0:
                    in_frontmatter = True
                elif in_frontmatter:
                    in_frontmatter = False
                continue
            if ignore_schema_dates and in_frontmatter and ":" in raw_line:
                key, raw_value = raw_line.split(":", 1)
                date_value = raw_value.strip().strip('"\'')
                if (
                    key.strip() in _SCHEMA_DATE_FIELDS
                    and _SCHEMA_DATE_RE.fullmatch(date_value)
                ):
                    continue
            claim_text = _ORDERED_LIST_PREFIX_RE.sub("", raw_line)
            claim_text = _INLINE_CODE_SPAN_RE.sub(" ", claim_text)
            values.update(
                match.group(0).replace(" ", "")
                for match in _NUMERIC_VALUE_RE.finditer(claim_text)
            )
        return values

    @staticmethod
    def _preserves_existing_page(path: Path, proposed: str) -> bool:
        """Require update bodies to retain every meaningful existing line.

        Stage 2 is an append/merge path; contradictions use INVALIDATE.  A
        schema-valid full-page replacement may therefore change ``updated:``
        metadata and add content, but it may not silently delete identity
        metadata, headings, prose, facts, links, or source lines.
        """
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return False

        def _required_lines(raw: str) -> set[str]:
            lines = raw.splitlines()
            closing = -1
            if lines and lines[0].strip() == "---":
                for index, line in enumerate(lines[1:], start=1):
                    if line.strip() == "---":
                        closing = index
                        break
            required: set[str] = set()
            for index, line in enumerate(lines):
                normalised = " ".join(line.split())
                if not normalised or normalised == "---":
                    continue
                if 0 < index < closing:
                    if normalised.startswith(
                        ("type:", "entity_kind:", "slug:", "created:")
                    ):
                        required.add(normalised)
                    continue
                if closing >= 0 and index <= closing:
                    continue
                required.add(normalised)
            return required

        proposed_lines = {" ".join(line.split()) for line in proposed.splitlines()}
        return _required_lines(current).issubset(proposed_lines)

    @staticmethod
    def _safe_superseded_slug(raw: Any) -> str | None:
        """Return a frontmatter-safe replacement slug, ``""``, or ``None``."""
        if raw is None or raw == "":
            return ""
        if not isinstance(raw, str):
            return None
        value = raw.strip().replace("\\", "/")
        parts = value.split("/")
        if len(parts) == 1:
            slug = parts[0]
        elif len(parts) == 2 and parts[0] in _TARGET_DIRS:
            slug = parts[1]
        else:
            return None
        slug = slug.removesuffix(".md")
        return slug if normalise_subjects((slug,)) == (slug,) else None

    def _build_invalidation(
        self, target_rel: str, superseded_by: str,
    ) -> PageUpdate | None:
        """Mechanically mark ``target_rel`` superseded (never via the LLM).

        Sets/overwrites frontmatter ``valid_until: <today>`` and
        ``superseded-by: "[[<slug>]]"`` on the existing page; the body is
        byte-preserved. Returns ``None`` when the page does not exist.
        """
        abs_path = self._vault_root / target_rel
        try:
            raw = abs_path.read_text(encoding="utf-8")
        except OSError:
            log.warning(
                "Consolidator: invalidate target missing on disk: %s", target_rel,
            )
            return None

        today = _dt.date.today().isoformat()
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            log.warning(
                "Consolidator: invalidate target has no frontmatter: %s", target_rel,
            )
            return None
        try:
            closing = next(
                i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---"
            )
        except StopIteration:
            return None

        # Drop any previous valid_until/superseded-by lines, then re-insert.
        fm = [
            ln for ln in lines[1:closing]
            if not ln.startswith(("valid_until:", "superseded-by:"))
        ]
        fm.append(f"valid_until: {today}")
        safe_slug = self._safe_superseded_slug(superseded_by)
        if safe_slug is None:
            log.warning(
                "Consolidator: refused unsafe superseded_by for %s",
                target_rel,
            )
            return None
        slug = safe_slug
        if slug:
            fm.append(f'superseded-by: "[[{slug}]]"')
        new_raw = "\n".join(["---", *fm, *lines[closing:]])
        if raw.endswith("\n") and not new_raw.endswith("\n"):
            new_raw += "\n"

        return PageUpdate(
            target_path=Path(target_rel),
            operation="update",
            new_body=new_raw,
            reason=f"superseded by {slug or 'a newer page'}",
        )

    def _rel(self, abs_path: Path) -> str:
        try:
            return abs_path.resolve().relative_to(self._vault_root).as_posix()
        except ValueError:
            return abs_path.as_posix()

    def _ensure_brain(self) -> Any:
        if self._brain is not None:
            return self._brain
        provider, model = _resolve_provider_and_model(self._curator_cfg, self._root_cfg)
        try:
            # Thinking disabled for Gemini non-pro: the judge must spend its
            # token budget on page bodies, not on internal reasoning (see
            # instantiate_curator_brain).
            self._brain = instantiate_curator_brain(self._registry, provider, model)
            self._resolved_provider = provider
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Consolidator: provider %r unavailable (%s) — batch stays pending",
                provider, exc,
            )
            self._brain = None
        return self._brain

    def reset_brain(self) -> None:
        """Drop the cached brain (provider switch via the Wiki settings card)."""
        self._brain = None
        self._resolved_provider = None


__all__ = ["Consolidator"]
