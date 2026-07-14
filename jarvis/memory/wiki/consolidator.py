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
import time
from pathlib import Path
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
from jarvis.memory.wiki.journal import JournalRow
from jarvis.memory.wiki.prompt import build_consolidator_prompt
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.telemetry import telemetry

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
        batch_limit: int = 20,
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

    async def run_once(self) -> str:
        """Drain one batch. Returns a short label for the scheduler log."""
        rows = self._journal.pending(limit=self._batch_limit)
        if not rows:
            return "journal-empty"

        neighbours = self._collect_neighbours(rows)
        decisions = await self._judge(rows, neighbours)
        if decisions is None:
            # Transient judge failure (timeout/unavailable): leave the rows
            # pending so the next trigger retries the batch.
            return "judge-unavailable"
        if decisions == "truncated":
            # Length-capped judge output: marking skipped prevents an
            # endless retry loop over the same over-long batch.
            self._journal.mark(
                [r.id for r in rows], status="skipped",
            )
            return "judge-truncated"

        label = f"journal-batch:{len(rows)}"
        await self._execute(rows, decisions, label)
        telemetry.inc("wiki_consolidator_runs")

        if self._on_run_complete is not None:
            try:
                maybe = self._on_run_complete()
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception as exc:  # noqa: BLE001
                log.warning("Consolidator: on_run_complete hook failed: %s", exc)
        return label

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
            if rel_path in found or len(found) >= self._k_nearest * len(rows):
                return
            abs_path = self._vault_root / rel_path
            try:
                if abs_path.is_file():
                    found[rel_path] = abs_path.read_text(encoding="utf-8")
            except OSError as exc:
                log.debug("Consolidator: cannot read neighbour %s: %s", rel_path, exc)

        for row in rows:
            for subject in row.subjects:
                slug = subject.strip().lower()
                if not slug:
                    continue
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
        """One batched LLM call. Returns decisions, "truncated", or None."""
        user_slug = str(
            getattr(
                self._root_cfg.memory.wiki.session_rollup, "user_entity_slug", "",
            )
            or ""
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
                _extract_json_array(agg.text)
            except ValueError as exc:
                reason = f"malformed JSON array: {exc}"
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
                    "after a truncated response; the batch will be skipped"
                )
                telemetry.inc("wiki_writes_blocked_truncated")
                return "truncated"
            return None
        agg, self._resolved_provider = result

        if is_length_truncated(agg.finish_reason, agg.text):
            telemetry.inc("wiki_writes_blocked_truncated")
            log.warning(
                "Consolidator: judge output hit the token cap "
                "(finish_reason=%r, %d chars) — batch will be skipped",
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
        return [item for item in parsed if isinstance(item, dict)]

    # ------------------------------------------------------------------
    # decision execution
    # ------------------------------------------------------------------

    async def _execute(
        self,
        rows: list[JournalRow],
        decisions: list[dict[str, Any]],
        label: str,
    ) -> None:
        by_id = {row.id: row for row in rows}
        judged_ids: set[int] = set()
        updates: list[PageUpdate] = []
        # candidate id -> (decision or None when unwritable, target or None)
        write_plan: dict[int, tuple[str | None, str | None]] = {}
        noop_ids: list[int] = []
        # Secondary invalidate targets (extra writes beyond a candidate's
        # primary decision); counted only after the write actually lands.
        secondary_invalidations: list[str] = []

        for item in decisions:
            cid = item.get("candidate_id")
            decision = item.get("decision")
            if not isinstance(cid, int) or cid not in by_id:
                continue
            if not isinstance(decision, str) or decision not in CURATOR_DECISIONS:
                continue
            # One PRIMARY decision per candidate; additional "invalidate"
            # items are allowed as secondary actions — a contradiction
            # typically ADDs/UPDATEs the corrected page AND invalidates the
            # superseded one in the same batch, for the same candidate.
            is_secondary = cid in judged_ids
            if is_secondary and decision != "invalidate":
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
                    write_plan[cid] = (None, None)
                    continue
                updates.append(
                    PageUpdate(
                        target_path=Path(target),
                        operation="create" if decision == "add" else "update",
                        new_body=new_body,
                        reason=str(item.get("reason", ""))[:200],
                    )
                )
                write_plan[cid] = (decision, target)
            else:  # invalidate
                superseded_by = str(item.get("superseded_by", "") or "").strip()
                invalidated = self._build_invalidation(target, superseded_by)
                if invalidated is None:
                    if not is_secondary:
                        write_plan[cid] = (None, None)
                    continue
                updates.append(invalidated)
                if not is_secondary:
                    write_plan[cid] = ("invalidate", target)
                else:
                    secondary_invalidations.append(target)

        # Apply all writes through the shared guarded pipeline.
        applied_rel: set[str] = set()
        rejected_rel: set[str] = set()
        if updates:
            result = await self._curator.apply_external_updates(
                updates, source_label=label, verb="merge",
            )
            applied_rel = {self._rel(p) for p in result.applied}
            rejected_rel = {
                self._rel(p)
                for p in (*result.blocked_pii, *result.failed_validation)
            }

        # Secondary invalidations are counted on landed writes only — never
        # before the writer's verdict (a blocked/skipped page must not
        # inflate the counter).
        for target in secondary_invalidations:
            if target in applied_rel:
                telemetry.inc("wiki_consolidator_invalidate")

        # Close out every candidate with an explicit status.
        for cid in by_id:
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
            if target in applied_rel:
                self._journal.mark(
                    [cid], status="consolidated",
                    decision=decision,  # type: ignore[arg-type]
                    target_path=target,
                )
                telemetry.inc(f"wiki_consolidator_{decision}")
            elif target in rejected_rel:
                self._journal.mark([cid], status="rejected", target_path=target)
                log.warning(
                    "Consolidator: write for candidate %d rejected "
                    "(secret guard / validation) — %s", cid, target,
                )
            else:
                # Recent-edit lock or another transient skip.
                self._journal.mark([cid], status="skipped", target_path=target)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

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
        if ".." in rel:
            return None
        return rel

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
        slug = superseded_by.split("/")[-1].removesuffix(".md").strip()
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
