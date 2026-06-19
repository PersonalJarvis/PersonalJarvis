"""Stage-1 conversation fact extractor (Wave 2, ADD-only).

One cheap LLM call per eligible conversation turn extracts 0..N atomic
candidate facts and appends them to the :class:`CandidateJournal`. This
stage NEVER touches the vault — a failing or over-eager extractor cannot
corrupt existing pages; the expensive judgment (ADD/UPDATE/NOOP/INVALIDATE
against real page bodies) happens later in the batched Stage-2 consolidator.

Provider/model resolve through the SAME hook as the curator
(``curator_llm._resolve_provider_and_model`` over ``[memory.wiki.curator]``),
so the Wiki settings card drives both stages — cheap router-tier model by
default, explicit override wins.

Stage-1 selection contract (spec §4.2, D1 "completeness with cleanliness"):
recall-biased — when unsure whether something matters long-term, surface a
candidate; smalltalk and contentless turns yield ``[]``. The journal +
Stage-2 NOOP de-duplication contain the over-capture.

AP-9: callers invoke :meth:`extract_and_journal` only from fire-and-forget
background tasks; this module never runs on the voice critical path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.brain.streaming import aggregate, is_length_truncated
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.memory.wiki.curator_llm import (
    _extract_json_array,
    _resolve_provider_and_model,
    instantiate_curator_brain,
)
from jarvis.memory.wiki.journal import CandidateFact
from jarvis.memory.wiki.telemetry import telemetry

if TYPE_CHECKING:
    from jarvis.core.config import JarvisConfig
    from jarvis.memory.wiki.journal import CandidateJournal

log = logging.getLogger(__name__)

# Hard cap on candidates accepted from one turn — a runaway extractor must
# not flood the journal (mirrors curator_llm._MAX_UPDATES_PER_INGEST).
_MAX_FACTS_PER_TURN = 10

# Assistant reply is context only; keep the prompt tiny (cheap-model tier).
_MAX_ASSISTANT_CONTEXT_CHARS = 500

# Valid candidate kinds. Anything else degrades to "other" (soft vocab —
# kinds are retrieval hints for Stage 2, not a wire-format contract).
_KNOWN_KINDS = frozenset(
    {"identity", "preference", "person", "project", "decision", "event", "other"}
)

def _log_trigger_outcome(task: "asyncio.Task[Any]") -> None:
    """Done-callback for the fire-and-forget journal-pressure trigger.

    Retrieves the exception (silences the never-retrieved warning) but —
    unlike a bare ``t.exception()`` lambda — actually logs real failures.
    A lost trigger is retried on the next append, so WARNING suffices.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning(
            "ConversationFactExtractor: journal-pressure trigger failed: %s", exc,
        )


_SYSTEM_PROMPT = """\
You extract durable personal-memory facts from ONE conversation turn between
a user and their assistant.

Return ONLY a JSON array. Each element: {"fact": "<one self-contained
sentence>", "kind": "<identity|preference|person|project|decision|event|other>",
"subjects": ["<lowercase-kebab-slug>", ...]}.

Rules:
- A fact must still be useful weeks later (identity, preferences, people,
  projects, decisions, plans, biographical events). Rephrase it so it stands
  alone without the conversation.
- Recall-biased: when unsure whether something matters long-term, include it.
- Return [] for smalltalk, pure questions, commands without durable content,
  or turns that only reference the immediate task at hand.
- "subjects" names who/what the fact is about (e.g. ["lena"], ["ruben"],
  ["personal-jarvis"]). Use ["ruben"]-style user slugs for facts about the
  speaker.
- Never include credentials, API keys, passwords, or tokens in a fact.
- No prose outside the JSON array.
"""


class ConversationFactExtractor:
    """Cheap-model Stage-1 extraction into the candidate journal."""

    def __init__(
        self,
        *,
        config: "JarvisConfig",
        journal: "CandidateJournal",
        registry: BrainProviderRegistry | None = None,
    ) -> None:
        self._root_cfg = config
        self._cfg = config.memory.wiki.extractor
        self._curator_cfg = config.memory.wiki.curator
        self._journal = journal
        self._registry = registry if registry is not None else BrainProviderRegistry()
        self._brain: Any = None
        self._resolved_provider: str | None = None
        self._resolved_model: str | None = None
        # Wave-2 journal pressure: when attached, an append that pushes the
        # backlog past the threshold fires a background JOURNAL trigger so
        # the Stage-2 consolidator drains a batch (cooldown/lock gated there).
        self._scheduler: Any = None
        self._consolidate_after: int = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def extract_and_journal(
        self,
        user_text: str,
        assistant_text: str,
        *,
        source_label: str,
        turn_hash: str,
    ) -> int:
        """One LLM call -> 0..N facts -> journal. Returns the appended count.

        Never raises: every failure (brain unavailable, timeout, malformed
        JSON, truncation) degrades to 0 with a logged warning — the
        conversation must never notice the memory pipeline.
        """
        text = (user_text or "").strip()
        if not self._cfg.enabled:
            return 0
        if len(text) < int(self._cfg.min_user_chars):
            return 0

        facts = await self._extract(text, (assistant_text or "").strip())
        if not facts:
            return 0

        appended = self._journal.append(
            facts, source_label=source_label, turn_hash=turn_hash,
        )
        if appended:
            telemetry.inc("wiki_candidates_extracted", appended)
            log.info(
                "ConversationFactExtractor: %d candidate fact(s) journaled (%s)",
                appended, source_label,
            )
            self._maybe_trigger_consolidation()
        return appended

    def attach_scheduler(self, scheduler: Any, *, consolidate_after: int) -> None:
        """Enable the journal-pressure trigger (Wave-2 B4).

        ``scheduler`` is duck-typed: needs ``trigger(TriggerSource.JOURNAL)``.
        """
        self._scheduler = scheduler
        self._consolidate_after = max(1, int(consolidate_after))

    def _maybe_trigger_consolidation(self) -> None:
        """Fire a background JOURNAL trigger when the backlog is heavy.

        Fire-and-forget (AP-9): the conversation turn never waits for the
        consolidator; the scheduler applies its own cooldown + vault lock.
        """
        if self._scheduler is None:
            return
        try:
            if self._journal.backlog_count() < self._consolidate_after:
                return
            from jarvis.memory.wiki.scheduler import TriggerSource

            task = asyncio.create_task(
                self._scheduler.trigger(TriggerSource.JOURNAL),
                name="wiki-journal-pressure-trigger",
            )
            task.add_done_callback(_log_trigger_outcome)
        except RuntimeError:
            # No running event loop (sync test context) — next append from
            # an async context will retry.
            log.debug("ConversationFactExtractor: no event loop for journal trigger")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ConversationFactExtractor: journal-pressure trigger failed: %s", exc,
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _extract(self, user_text: str, assistant_text: str) -> list[CandidateFact]:
        brain = self._ensure_brain()
        if brain is None:
            return []

        context = assistant_text[:_MAX_ASSISTANT_CONTEXT_CHARS]
        # Real-time anchor so relative phrases ("last month", "this spring")
        # resolve to the correct year downstream.
        today = time.strftime("%Y-%m-%d")
        user_prompt = (
            f"Today is {today}.\n\n"
            f"User said:\n{user_text}\n\n"
            f"Assistant replied (context only):\n{context or '(none)'}"
        )
        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user_prompt),),
            system=_SYSTEM_PROMPT,
            max_tokens=int(self._cfg.max_output_tokens),
            temperature=0.2,  # extraction, not creativity
            stream=True,
        )

        start_ns = time.time_ns()
        try:
            agg = await asyncio.wait_for(
                aggregate(brain.complete(request)),
                timeout=float(self._cfg.timeout_s),
            )
        except TimeoutError:
            log.warning(
                "ConversationFactExtractor: timeout after %.1fs (provider=%s)",
                self._cfg.timeout_s, self._resolved_provider,
            )
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ConversationFactExtractor: brain call failed (provider=%s): %s",
                self._resolved_provider, exc,
            )
            return []

        if is_length_truncated(agg.finish_reason, agg.text):
            log.warning(
                "ConversationFactExtractor: response hit the output-token cap "
                "(finish_reason=%r, %d chars) — discarding the turn's candidates",
                agg.finish_reason, len(agg.text or ""),
            )
            telemetry.inc("wiki_writes_blocked_truncated")
            return []

        try:
            parsed = _extract_json_array(agg.text)
        except ValueError as exc:
            log.warning(
                "ConversationFactExtractor: malformed JSON from %s (%s) — "
                "no candidates this turn",
                self._resolved_provider, exc,
            )
            return []

        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        facts = self._coerce_facts(parsed)
        log.debug(
            "ConversationFactExtractor: %d/%d item(s) accepted in %dms",
            len(facts), len(parsed), duration_ms,
        )
        return facts

    def _coerce_facts(self, parsed: list[Any]) -> list[CandidateFact]:
        facts: list[CandidateFact] = []
        for item in parsed[:_MAX_FACTS_PER_TURN]:
            if not isinstance(item, dict):
                continue
            fact = item.get("fact")
            if not isinstance(fact, str) or not fact.strip():
                continue
            kind = item.get("kind")
            if not isinstance(kind, str) or kind not in _KNOWN_KINDS:
                kind = "other"
            raw_subjects = item.get("subjects")
            subjects: tuple[str, ...] = ()
            if isinstance(raw_subjects, list):
                subjects = tuple(
                    s.strip().lower() for s in raw_subjects
                    if isinstance(s, str) and s.strip()
                )
            facts.append(CandidateFact(fact=fact.strip(), kind=kind, subjects=subjects))
        return facts

    def _ensure_brain(self) -> Any:
        """Lazily instantiate the cheap brain; ``None`` when unavailable."""
        if self._brain is not None:
            return self._brain
        provider, model = _resolve_provider_and_model(self._curator_cfg, self._root_cfg)
        try:
            # Thinking disabled for Gemini non-pro: extraction is small,
            # deterministic JSON output (see instantiate_curator_brain).
            self._brain = instantiate_curator_brain(self._registry, provider, model)
            self._resolved_provider = provider
            self._resolved_model = model
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ConversationFactExtractor: provider %r unavailable (%s) — "
                "extraction disabled until next attempt",
                provider, exc,
            )
            self._brain = None
        return self._brain

    def seen_turn(self, turn_hash: str) -> bool:
        """Durable dedupe: True when this turn hash is already journaled."""
        try:
            return self._journal.seen_turn(turn_hash)
        except Exception:  # noqa: BLE001
            return False

    def reset_brain(self) -> None:
        """Drop the cached brain so the next turn re-resolves provider/model.

        Mirrors the live-apply contract of the Wiki settings route, which
        clears the curator's cached brain on a provider switch.
        """
        self._brain = None
        self._resolved_provider = None
        self._resolved_model = None


__all__ = ["ConversationFactExtractor"]
