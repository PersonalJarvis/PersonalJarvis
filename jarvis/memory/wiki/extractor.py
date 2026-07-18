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
import hashlib
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.brain.streaming import aggregate, is_length_truncated
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.core.redact import safe_preview
from jarvis.memory.wiki.curator_llm import (
    _extract_json_array,
    _resolve_provider_and_model,
    instantiate_curator_brain,
)
from jarvis.memory.wiki.grounding import is_unsupported_user_interest_claim
from jarvis.memory.wiki.journal import CandidateFact, normalise_subjects
from jarvis.memory.wiki.secret_guard import contains_secret
from jarvis.memory.wiki.telemetry import telemetry

if TYPE_CHECKING:
    from jarvis.core.config import JarvisConfig
    from jarvis.memory.wiki.journal import CandidateJournal

log = logging.getLogger(__name__)

# Hard cap on candidates accepted from one turn — a runaway extractor must
# not flood the journal (mirrors curator_llm._MAX_UPDATES_PER_INGEST).
_MAX_FACTS_PER_TURN = 10
_MAX_FACTS_PER_SESSION_CHUNK = 16

# Assistant reply is context only; keep the prompt tiny (cheap-model tier).
_MAX_ASSISTANT_CONTEXT_CHARS = 500
_MAX_CONTEXT_TURNS = 5
_MAX_CONTEXT_CHARS_PER_TURN = 1_000
_MAX_SESSION_CHUNK_CHARS = 9_000
_MAX_SESSION_CHUNK_TURNS = 16
_SESSION_CONTEXT_OVERLAP_TURNS = 2
_MIN_SESSION_OUTPUT_TOKENS = 2_400
_MAX_EVIDENCE_EXCERPT_CHARS = 1_200
_MAX_FOCUS_EVIDENCE_CHARS = 800
_MAX_PRIOR_EVIDENCE_CHARS = 150
_MAX_EVIDENCE_TURN_ID_CHARS = 80

# A valid empty array from one cheap provider gets a second opinion only when
# the user turn has a durable first-person/relationship/project signal. The
# binding Stage-2 judge still decides whether anything reaches the vault.
_DURABLE_CUE_RE = re.compile(
    r"(?i)\b(?:my|our|i\s+(?:am|own|prefer|love|hate|work|live|decided|plan)"
    r"|mein(?:e|er|en|em|es)?|unser(?:e|er|en|em|es)?"
    r"|ich\s+(?:bin|habe|besitze|mag|liebe|hasse|arbeite|wohne|plane)"
    r"|mi|mis|nuestro|nuestra|soy|tengo|prefiero|trabajo|vivo"
    r"|friend|partner|wife|husband|boss|mother|father"
    r"|freund|partnerin|ehefrau|ehemann|chef|mutter|vater"
    r"|amigo|amiga|pareja|esposa|esposo|jefe|madre|padre"
    r"|project|projekt|proyecto)\b"
)

# Valid candidate kinds. Anything else degrades to "other" (soft vocab —
# kinds are retrieval hints for Stage 2, not a wire-format contract).
_KNOWN_KINDS = frozenset(
    {
        "identity",
        "preference",
        "person",
        "project",
        "decision",
        "event",
        "asset",
        "place",
        "organization",
        "relationship",
        "other",
    }
)


_SYSTEM_PROMPT = """\
You extract durable personal-memory facts from one FOCUS USER TURN. Earlier
turns and assistant replies are context for resolving references only.

Return ONLY a JSON array. Each element: {"fact": "<one self-contained
sentence>", "kind": "<identity|preference|person|project|decision|event|asset|
place|organization|relationship|other>", "subjects":
["<lowercase-kebab-slug>", ...], "evidence_turn_id": "<focus turn id>"}.

Rules:
- A fact must still be useful weeks later: identity, preferences, people,
  relationships, owned assets or vehicles, places, organizations, projects,
  decisions, plans, and biographical events. Rephrase it so it stands alone.
- Explicit ownership or naming is durable even when phrased briefly, for
  example "I own the yacht" or "My yacht is named Aurora".
- A topic mention, one-off question, or request for information is NOT evidence
  of the user's lasting interest, preference, habit, plan, intent, identity, or
  ownership. "What are the benefits of Vitamin D?" yields []; do not infer a
  supplement interest or plan. "Tell me about Monaco." yields []; do not infer
  interest in Monaco, travel plans, attendance, residence, or preference.
- Store a user relationship to a topic only when the user explicitly asserts
  or confirms it. "I own a yacht." and "I plan to attend Monaco." are durable
  self-disclosures. If a question also contains a self-disclosure, extract only
  the disclosed fact and never invent a reason for the question.
- Recall-biased applies only after the user has grounded a durable fact; it
  never permits turning topic choice into a personal-memory claim.
- Return [] for smalltalk, pure questions, commands without durable content,
  fleeting bodily/status chatter, or turns that only concern the immediate
  task. "I need the bathroom" is not durable memory.
- Extract facts asserted or confirmed by the USER in the focus turn only.
  Never turn an assistant statement, guess, suggestion, or question into a
  fact. Earlier turns may resolve a pronoun, but are not new evidence here.
- Every element must use the exact focus turn id as evidence_turn_id.
- "subjects" names who/what the fact is about (e.g. ["person-name"],
  ["personal-jarvis"]). The caller supplies the speaker's exact user slug.
- Never include credentials, API keys, passwords, or tokens in a fact.
- No prose outside the JSON array.
"""

_SESSION_SYSTEM_PROMPT = """\
You perform a completeness sweep over one finished realtime conversation.
Extract durable personal-memory facts that individual-turn review may have
missed because the meaning emerged across several turns.

Return ONLY a JSON array. Each element: {"fact": "<one self-contained
sentence>", "kind": "<identity|preference|person|project|decision|event|asset|
place|organization|relationship|other>", "subjects":
["<lowercase-kebab-slug>", ...], "evidence_turn_id": "<exact user turn id>"}.

Rules:
- Use only statements asserted or explicitly confirmed by the USER as facts.
  Assistant replies are context only and can never be evidence.
- Every fact must cite the exact user turn that supports it. If no user turn
  supports a claim, omit it.
- Preserve durable identity, people, relationships, ownership and names of
  assets or vehicles, places, organizations, projects, preferences, decisions,
  plans, and biographical events that remain useful weeks later.
- A topic mention, one-off question, or request for information is NOT evidence
  of the user's lasting interest, preference, habit, plan, intent, identity, or
  ownership. "What are the benefits of Vitamin D?" yields []; do not infer a
  supplement interest or plan. "Tell me about Monaco." yields []; do not infer
  interest in Monaco, travel plans, attendance, residence, or preference.
- Store a user relationship to a topic only when the user explicitly asserts
  or confirms it. "I own a yacht." and "I plan to attend Monaco." are durable
  self-disclosures. If a question also contains a self-disclosure, extract only
  the disclosed fact and never invent a reason for the question.
- Resolve pronouns and follow-up clarifications across turns. Prefer one
  complete fact over several fragments.
- Return [] for greetings, questions, commands without durable content,
  transient bodily/status chatter, weather talk, and immediate-task details.
- Never include credentials, API keys, passwords, or tokens.
- No prose outside the JSON array.
"""


@dataclass(frozen=True, slots=True)
class ConversationContextTurn:
    """One bounded, user-evidenced turn supplied as extraction context."""

    turn_id: str
    user_text: str
    assistant_text: str = ""


@dataclass(frozen=True, slots=True)
class _ExtractionResult:
    facts: tuple[CandidateFact, ...] = ()
    outcome: Literal["empty", "candidates", "failed"] = "empty"
    provider: str = ""
    duration_ms: int = 0
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class _SessionChunk:
    """One retryable focus partition with bounded user-reference overlap."""

    context: tuple[ConversationContextTurn, ...]
    focus: tuple[ConversationContextTurn, ...]


class ConversationFactExtractor:
    """Cheap-model Stage-1 extraction into the candidate journal."""

    def __init__(
        self,
        *,
        config: JarvisConfig,
        journal: CandidateJournal,
        registry: BrainProviderRegistry | None = None,
    ) -> None:
        self._root_cfg = config
        self._cfg = config.memory.wiki.extractor
        self._curator_cfg = config.memory.wiki.curator
        configured_slug = str(
            getattr(config.memory.wiki.session_rollup, "user_entity_slug", "")
            or "user"
        )
        safe_slug = normalise_subjects((configured_slug,))
        self._user_entity_slug = safe_slug[0] if safe_slug else "user"
        self._journal = journal
        self._registry = registry if registry is not None else BrainProviderRegistry()
        self._credential_filter = registry is None
        self._brain: Any = None
        self._resolved_provider: str | None = None
        self._resolved_model: str | None = None
        # Wave-2 journal pressure: when attached, an append that pushes the
        # backlog past the threshold fires a background JOURNAL trigger so
        # the Stage-2 consolidator drains a batch (cooldown/lock gated there).
        self._scheduler: Any = None
        self._consolidate_after: int = 0

    def _with_user_slug(self, prompt: str) -> str:
        """Bind self-facts to a portable configured slug, never a maintainer."""
        return (
            prompt
            + "\n- For facts about the speaker, include the exact subject slug "
            + f'["{self._user_entity_slug}"].\n'
        )

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
        review_key: str | None = None,
        session_id: str = "",
        turn_id: str = "",
        source_kind: str = "turn",
        context_turns: Sequence[ConversationContextTurn] = (),
    ) -> int:
        """One LLM call -> 0..N facts -> journal. Returns the appended count.

        Never raises: every failure (brain unavailable, timeout, malformed
        JSON, truncation) degrades to 0 with a logged warning — the
        conversation must never notice the memory pipeline.
        """
        text = (user_text or "").strip()
        focus_turn_id = (turn_id or turn_hash).strip()
        key = review_key or f"turn:v2:{turn_hash}"
        if not self._claim_review(
            review_key=key,
            source_label=source_label,
            source_kind=source_kind,
            text=text,
            session_id=session_id,
            turn_id=focus_turn_id,
        ):
            return 0

        if not self._cfg.enabled:
            self._finish_review(key, status="filtered", error_code="extractor-disabled")
            return 0
        if len(text) < int(self._cfg.min_user_chars):
            self._finish_review(key, status="filtered", error_code="below-min-chars")
            return 0

        prompt = self._build_turn_prompt(
            user_text=text,
            assistant_text=(assistant_text or "").strip(),
            focus_turn_id=focus_turn_id,
            context_turns=context_turns,
        )
        try:
            outcome = await self._extract(
                prompt,
                system_prompt=self._with_user_slug(_SYSTEM_PROMPT),
                allowed_evidence={focus_turn_id},
                fallback_evidence="",
                evidence_text_by_id={
                    focus_turn_id: self._user_evidence_excerpt(
                        ConversationContextTurn(
                            turn_id=focus_turn_id,
                            user_text=text,
                        ),
                        context_turns,
                    )
                },
                max_facts=_MAX_FACTS_PER_TURN,
                max_output_tokens=int(self._cfg.max_output_tokens),
                retry_empty=bool(_DURABLE_CUE_RE.search(text)),
            )
        except asyncio.CancelledError:
            self._finish_review(key, status="failed", error_code="cancelled")
            raise
        except Exception:  # noqa: BLE001 - the conversation must never notice
            log.exception("ConversationFactExtractor: unexpected extraction failure")
            self._finish_review(key, status="failed", error_code="unexpected")
            return 0

        return self._persist_outcome(
            outcome,
            review_key=key,
            source_label=source_label,
            turn_hash=turn_hash,
        )

    async def extract_session_and_journal(
        self,
        turns: Sequence[ConversationContextTurn],
        *,
        session_id: str,
        source_label: str,
        review_key: str | None = None,
    ) -> int:
        """Sweep every Realtime turn in stable, independently retryable chunks."""
        usable = tuple(t for t in turns if t.turn_id and t.user_text.strip())
        base_key = review_key or f"session:v3:{session_id}"
        chunks = self._session_chunks(usable)
        if not chunks:
            empty_key = f"{base_key}:empty"
            if self._claim_review(
                review_key=empty_key,
                source_label=source_label,
                source_kind="session-sweep",
                text="",
                session_id=session_id,
                turn_id="",
            ):
                self._finish_review(
                    empty_key,
                    status="filtered",
                    error_code="no-user-turns",
                )
            return 0

        keys = self.session_review_keys(
            usable,
            session_id=session_id,
            review_key=base_key,
        )
        total = 0
        seen_facts: set[str] = set()
        for index, (key, chunk) in enumerate(zip(keys, chunks, strict=True)):
            transcript = self._build_session_prompt(chunk)
            if not self._claim_review(
                review_key=key,
                source_label=f"{source_label}:chunk:{index}",
                source_kind="session-sweep",
                text=transcript,
                session_id=session_id,
                turn_id="",
            ):
                continue
            if not self._cfg.enabled:
                self._finish_review(
                    key,
                    status="filtered",
                    error_code="extractor-disabled",
                )
                continue
            try:
                outcome = await self._extract(
                    transcript,
                    system_prompt=self._with_user_slug(_SESSION_SYSTEM_PROMPT),
                    allowed_evidence={t.turn_id for t in chunk.focus},
                    fallback_evidence="",
                    evidence_text_by_id=self._chunk_evidence_map(chunk),
                    max_facts=_MAX_FACTS_PER_SESSION_CHUNK,
                    max_output_tokens=max(
                        _MIN_SESSION_OUTPUT_TOKENS,
                        int(self._cfg.max_output_tokens),
                    ),
                    retry_empty=True,
                )
            except asyncio.CancelledError:
                self._finish_review(key, status="failed", error_code="cancelled")
                raise
            except Exception:  # noqa: BLE001
                log.exception("ConversationFactExtractor: session chunk failed")
                self._finish_review(key, status="failed", error_code="unexpected")
                continue

            unique = tuple(
                fact
                for fact in outcome.facts
                if " ".join(fact.fact.casefold().split()) not in seen_facts
            )
            seen_facts.update(" ".join(f.fact.casefold().split()) for f in unique)
            if unique != outcome.facts:
                outcome = _ExtractionResult(
                    facts=unique,
                    outcome="candidates" if unique else "empty",
                    provider=outcome.provider,
                    duration_ms=outcome.duration_ms,
                    error_code=outcome.error_code,
                )
            total += self._persist_outcome(
                outcome,
                review_key=key,
                source_label=f"{source_label}:chunk:{index}",
                turn_hash=key,
            )
        return total

    def session_review_keys(
        self,
        turns: Sequence[ConversationContextTurn],
        *,
        session_id: str,
        review_key: str | None = None,
    ) -> tuple[str, ...]:
        """Return stable policy-v3 review keys for every complete-run chunk."""
        usable = tuple(t for t in turns if t.turn_id and t.user_text.strip())
        base = review_key or f"session:v3:{session_id}"
        keys: list[str] = []
        for index, chunk in enumerate(self._session_chunks(usable)):
            identity = "\0".join(
                f"{role}\0{turn.turn_id}\0"
                f"{hashlib.sha256(turn.user_text.encode('utf-8')).hexdigest()}"
                for role, group in (("context", chunk.context), ("focus", chunk.focus))
                for turn in group
            )
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            keys.append(f"{base}:chunk:{index:03d}:{digest}")
        return tuple(keys)

    @staticmethod
    def _session_chunks(
        turns: Sequence[ConversationContextTurn],
    ) -> tuple[_SessionChunk, ...]:
        """Partition every turn and preserve boundary context for references."""
        focus_chunks: list[tuple[ConversationContextTurn, ...]] = []
        current: list[ConversationContextTurn] = []
        current_chars = 0
        for turn in turns:
            block_chars = len(ConversationFactExtractor._session_turn_block(turn))
            if current and (
                len(current) >= _MAX_SESSION_CHUNK_TURNS
                or current_chars + block_chars > _MAX_SESSION_CHUNK_CHARS
            ):
                focus_chunks.append(tuple(current))
                current = []
                current_chars = 0
            current.append(turn)
            current_chars += block_chars
        if current:
            focus_chunks.append(tuple(current))

        chunks: list[_SessionChunk] = []
        consumed = 0
        all_turns = tuple(turns)
        for focus in focus_chunks:
            context = all_turns[
                max(0, consumed - _SESSION_CONTEXT_OVERLAP_TURNS):consumed
            ]
            # Keep the total prompt bounded. Drop the oldest overlap first;
            # every focus turn remains represented exactly once.
            while context and (
                sum(len(ConversationFactExtractor._session_turn_block(t)) for t in context)
                + sum(len(ConversationFactExtractor._session_turn_block(t)) for t in focus)
                > _MAX_SESSION_CHUNK_CHARS
            ):
                context = context[1:]
            chunks.append(_SessionChunk(context=tuple(context), focus=focus))
            consumed += len(focus)
        return tuple(chunks)

    @property
    def min_user_chars(self) -> int:
        """The one Stage-1 eligibility floor used by every bridge path."""
        return int(self._cfg.min_user_chars)

    def capture_seen(self, review_key: str) -> bool:
        """Return whether a transport-identity review already finished."""
        try:
            return self._journal.capture_seen(review_key)
        except Exception:  # noqa: BLE001
            return False

    def capture_status(self, review_key: str) -> str | None:
        """Return the durable audit state used to coordinate live/backfill work."""
        try:
            return self._journal.capture_status(review_key)
        except Exception:  # noqa: BLE001
            return None

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
            backlog = self._journal.backlog_count()
            try:
                from jarvis.memory.wiki.health import health

                health.record_backlog(backlog)
            except Exception:  # noqa: BLE001 — health recording must never break extraction
                log.debug(
                    "ConversationFactExtractor: health.record_backlog failed", exc_info=True,
                )
            if backlog < self._consolidate_after:
                return
            from jarvis.memory.wiki.scheduler import fire_journal_trigger

            fire_journal_trigger(
                self._scheduler,
                name="wiki-journal-pressure-trigger",
                log_context="journal-pressure trigger",
            )
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

    @staticmethod
    def _build_turn_prompt(
        *,
        user_text: str,
        assistant_text: str,
        focus_turn_id: str,
        context_turns: Sequence[ConversationContextTurn],
    ) -> str:
        parts = [f"Today is {time.strftime('%Y-%m-%d')}."]
        bounded = tuple(context_turns)[-_MAX_CONTEXT_TURNS:]
        if bounded:
            parts.append("RECENT CONTEXT (reference resolution only):")
            for turn in bounded:
                parts.append(
                    f"USER TURN [{turn.turn_id}]:\n"
                    f"{turn.user_text[:_MAX_CONTEXT_CHARS_PER_TURN]}\n"
                    f"ASSISTANT CONTEXT (never evidence):\n"
                    f"{turn.assistant_text[:_MAX_ASSISTANT_CONTEXT_CHARS] or '(none)'}"
                )
        parts.append(
            f"FOCUS USER TURN [{focus_turn_id}] (the only evidence source):\n"
            f"{user_text}\n\n"
            "ASSISTANT REPLY (context only; never evidence):\n"
            f"{assistant_text[:_MAX_ASSISTANT_CONTEXT_CHARS] or '(none)'}"
        )
        return "\n\n".join(parts)

    @staticmethod
    def _session_turn_block(turn: ConversationContextTurn) -> str:
        return (
            f"USER TURN [{turn.turn_id}]:\n"
            f"{turn.user_text[:_MAX_CONTEXT_CHARS_PER_TURN * 2]}\n"
            "ASSISTANT CONTEXT (never evidence):\n"
            f"{turn.assistant_text[:_MAX_ASSISTANT_CONTEXT_CHARS] or '(none)'}"
        )

    @staticmethod
    def _build_session_prompt(chunk: _SessionChunk) -> str:
        parts = [f"Today is {time.strftime('%Y-%m-%d')}."]
        if chunk.context:
            parts.append(
                "BOUNDARY USER CONTEXT (reference resolution only; these turn "
                "IDs are not eligible evidence):\n\n"
                + "\n\n".join(
                    ConversationFactExtractor._session_turn_block(turn)
                    for turn in chunk.context
                )
            )
        parts.append(
            "FOCUS SESSION TURNS (eligible user evidence):\n\n"
            + "\n\n".join(
                ConversationFactExtractor._session_turn_block(turn)
                for turn in chunk.focus
            )
        )
        return "\n\n".join(parts)

    @staticmethod
    def _user_evidence_excerpt(
        focus: ConversationContextTurn,
        prior_turns: Sequence[ConversationContextTurn],
    ) -> str:
        """Render user-only grounding, including two reference-resolution turns."""
        prior = [
            turn
            for turn in prior_turns
            if turn.turn_id != focus.turn_id and turn.user_text.strip()
        ][-_SESSION_CONTEXT_OVERLAP_TURNS:]
        focus_id = safe_preview(
            focus.turn_id,
            max_chars=_MAX_EVIDENCE_TURN_ID_CHARS,
        ).strip()
        focus_text = safe_preview(
            focus.user_text.strip(),
            max_chars=_MAX_FOCUS_EVIDENCE_CHARS,
        ).strip()
        # Focus comes first so the durable evidence cannot be truncated away by
        # an unusually long reference-resolution turn.
        lines = [f"Evidence user turn [{focus_id}]: {focus_text}"]
        for turn in prior:
            turn_id = safe_preview(
                turn.turn_id,
                max_chars=_MAX_EVIDENCE_TURN_ID_CHARS,
            ).strip()
            user_text = safe_preview(
                turn.user_text.strip(),
                max_chars=_MAX_PRIOR_EVIDENCE_CHARS,
            ).strip()
            lines.append(f"Prior user context [{turn_id}]: {user_text}")
        return safe_preview(
            "\n".join(lines),
            max_chars=_MAX_EVIDENCE_EXCERPT_CHARS,
        ).strip()

    @classmethod
    def _chunk_evidence_map(cls, chunk: _SessionChunk) -> dict[str, str]:
        history = list(chunk.context)
        evidence: dict[str, str] = {}
        for turn in chunk.focus:
            evidence[turn.turn_id] = cls._user_evidence_excerpt(turn, history)
            history.append(turn)
        return evidence

    def _claim_review(
        self,
        *,
        review_key: str,
        source_label: str,
        source_kind: str,
        text: str,
        session_id: str,
        turn_id: str,
    ) -> bool:
        try:
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return self._journal.claim_capture(
                review_key,
                source_label=source_label,
                source_kind=source_kind,
                text_hash=text_hash,
                session_id=session_id,
                turn_id=turn_id,
            )
        except Exception:  # noqa: BLE001 - audit must not disable memory
            log.warning(
                "ConversationFactExtractor: capture audit claim failed; continuing",
                exc_info=True,
            )
            return True

    def _finish_review(
        self,
        review_key: str,
        *,
        status: Literal["filtered", "empty", "candidates", "failed"],
        candidate_count: int = 0,
        provider: str = "",
        duration_ms: int = 0,
        error_code: str = "",
    ) -> None:
        try:
            self._journal.finish_capture(
                review_key,
                status=status,
                candidate_count=candidate_count,
                provider=provider,
                duration_ms=duration_ms,
                error_code=error_code,
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "ConversationFactExtractor: capture audit finish failed",
                exc_info=True,
            )

    def _persist_outcome(
        self,
        outcome: _ExtractionResult,
        *,
        review_key: str,
        source_label: str,
        turn_hash: str,
    ) -> int:
        if outcome.outcome == "failed":
            self._finish_review(
                review_key,
                status="failed",
                provider=outcome.provider,
                duration_ms=outcome.duration_ms,
                error_code=outcome.error_code,
            )
            return 0
        if not outcome.facts:
            self._finish_review(
                review_key,
                status="empty",
                provider=outcome.provider,
                duration_ms=outcome.duration_ms,
            )
            return 0

        try:
            appended = self._journal.commit_capture_candidates(
                outcome.facts,
                review_key=review_key,
                source_label=source_label,
                turn_hash=turn_hash,
                provider=outcome.provider,
                duration_ms=outcome.duration_ms,
            )
        except Exception:  # noqa: BLE001 - extraction must stay off the user path
            log.exception("ConversationFactExtractor: atomic journal commit failed")
            self._finish_review(
                review_key,
                status="failed",
                provider=outcome.provider,
                duration_ms=outcome.duration_ms,
                error_code="journal-write-failed",
            )
            return 0
        if not appended:
            return 0
        telemetry.inc("wiki_candidates_extracted", appended)
        log.info(
            "ConversationFactExtractor: %d candidate fact(s) journaled (%s)",
            appended,
            source_label,
        )
        self._maybe_trigger_consolidation()
        return appended

    async def _extract(
        self,
        user_prompt: str,
        *,
        system_prompt: str,
        allowed_evidence: set[str],
        fallback_evidence: str,
        evidence_text_by_id: dict[str, str],
        max_facts: int,
        max_output_tokens: int,
        retry_empty: bool,
    ) -> _ExtractionResult:
        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user_prompt),),
            system=system_prompt,
            max_tokens=max(1, int(max_output_tokens)),
            temperature=0.2,  # extraction, not creativity
            stream=True,
        )

        start_ns = time.time_ns()
        from jarvis.memory.wiki.provider_chain import (
            build_wiki_provider_chain,
            complete_with_fallback,
            credential_ready_wiki_providers,
        )

        # Key-aware fallback (AP-22/23): try the configured provider, then cross
        # to whatever family is reachable, instead of silently dropping the turn
        # when one provider is throttled / keyless (live 2026-06-30).
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
                parsed = _extract_json_array(agg.text)
            except ValueError as exc:
                reason = f"malformed JSON array: {exc}"
                rejection_reasons.append(reason)
                return reason
            if parsed and not self._has_usable_fact_item(
                parsed,
                allowed_evidence=allowed_evidence,
                fallback_evidence=fallback_evidence,
                evidence_text_by_id=evidence_text_by_id,
                max_facts=max_facts,
            ):
                reason = "candidate array has no grounded usable fact"
                rejection_reasons.append(reason)
                return reason
            if retry_empty and not parsed:
                reason = "empty-needs-second-opinion"
                rejection_reasons.append(reason)
                return reason
            return None

        result = await complete_with_fallback(
            registry=self._registry,
            chain=chain,
            request=request,
            timeout_s=float(self._cfg.timeout_s),
            label="ConversationFactExtractor",
            aggregate=aggregate,
            validate=_validate_response,
            # Both reasons are CONTENT verdicts ("this turn holds nothing
            # storable"), not provider damage. Two independent families
            # agreeing ends the chain quietly; without this, an ordinary
            # no-fact turn burned EVERY provider (incl. dead rungs and their
            # timeouts) and recorded a scary chain failure the Wiki tab
            # rendered as a red "not connected" state (live 2026-07-18).
            allow_last_rejection=lambda reason: reason in (
                "empty-needs-second-opinion",
                "candidate array has no grounded usable fact",
            ),
            # But a LONE ungrounded array (no second opinion available) stays
            # a retryable failure: one weak provider must never be terminal
            # proof that the transcript held no facts. A lone valid EMPTY
            # answer remains acceptable as before.
            allow_lone_rejection=lambda reason: (
                reason == "empty-needs-second-opinion"
            ),
        )
        if result is None:
            duration_ms = (time.time_ns() - start_ns) // 1_000_000
            error_code = "provider-chain-failed"
            if any(reason.startswith("truncated") for reason in rejection_reasons):
                log.warning(
                    "ConversationFactExtractor: every provider hit the output-token "
                    "cap or failed after a truncated response; no candidates were saved"
                )
                telemetry.inc("wiki_writes_blocked_truncated")
                error_code = "truncated"
            elif rejection_reasons:
                error_code = "invalid-structured-output"
            return _ExtractionResult(
                outcome="failed",
                duration_ms=duration_ms,
                error_code=error_code,
            )
        agg, self._resolved_provider = result

        if is_length_truncated(agg.finish_reason, agg.text):
            log.warning(
                "ConversationFactExtractor: response hit the output-token cap "
                "(finish_reason=%r, %d chars) — discarding the turn's candidates",
                agg.finish_reason, len(agg.text or ""),
            )
            telemetry.inc("wiki_writes_blocked_truncated")
            return _ExtractionResult(
                outcome="failed",
                provider=self._resolved_provider or "",
                duration_ms=(time.time_ns() - start_ns) // 1_000_000,
                error_code="truncated",
            )

        try:
            parsed = _extract_json_array(agg.text)
        except ValueError as exc:
            log.warning(
                "ConversationFactExtractor: malformed JSON from %s (%s) — "
                "no candidates this turn",
                self._resolved_provider, exc,
            )
            return _ExtractionResult(
                outcome="failed",
                provider=self._resolved_provider or "",
                duration_ms=(time.time_ns() - start_ns) // 1_000_000,
                error_code="invalid-structured-output",
            )

        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        facts = self._coerce_facts(
            parsed,
            allowed_evidence=allowed_evidence,
            fallback_evidence=fallback_evidence,
            evidence_text_by_id=evidence_text_by_id,
            max_facts=max_facts,
        )
        log.debug(
            "ConversationFactExtractor: %d/%d item(s) accepted in %dms",
            len(facts), len(parsed), duration_ms,
        )
        return _ExtractionResult(
            facts=tuple(facts),
            outcome="candidates" if facts else "empty",
            provider=self._resolved_provider or "",
            duration_ms=duration_ms,
        )

    def _has_usable_fact_item(
        self,
        parsed: list[Any],
        *,
        allowed_evidence: set[str],
        fallback_evidence: str,
        evidence_text_by_id: dict[str, str],
        max_facts: int,
    ) -> bool:
        """Return whether Stage 1 can persist at least one grounded item.

        JSON syntax alone is not success: objects without a fact, with only a
        secret-shaped fact, or with an assistant/context evidence id would be
        discarded by ``_coerce_facts``. Reject such a response early so the
        provider chain can ask a different family instead of recording a false
        terminal ``empty`` result.
        """
        secret_count = 0
        unsupported_interest_count = 0
        usable = False
        for item in parsed[:max_facts]:
            if not isinstance(item, dict):
                continue
            fact = item.get("fact")
            if not isinstance(fact, str) or not fact.strip():
                continue
            if contains_secret(fact):
                secret_count += 1
                continue
            raw_evidence = item.get("evidence_turn_id")
            evidence = raw_evidence.strip() if isinstance(raw_evidence, str) else ""
            if not evidence:
                evidence = fallback_evidence
            if evidence and evidence in allowed_evidence:
                raw_subjects = item.get("subjects")
                subjects = (
                    normalise_subjects(raw_subjects)
                    if isinstance(raw_subjects, list)
                    else ()
                )
                if is_unsupported_user_interest_claim(
                    fact=fact,
                    subjects=subjects,
                    evidence_excerpt=evidence_text_by_id.get(evidence, ""),
                    user_slug=self._user_entity_slug,
                ):
                    unsupported_interest_count += 1
                    continue
                usable = True
        if secret_count:
            telemetry.inc("wiki_candidates_blocked_secret", secret_count)
        if unsupported_interest_count:
            telemetry.inc(
                "wiki_candidates_blocked_unsupported_interest",
                unsupported_interest_count,
            )
        return usable

    def _coerce_facts(
        self,
        parsed: list[Any],
        *,
        allowed_evidence: set[str],
        fallback_evidence: str,
        evidence_text_by_id: dict[str, str],
        max_facts: int,
    ) -> list[CandidateFact]:
        facts: list[CandidateFact] = []
        for item in parsed[:max_facts]:
            if not isinstance(item, dict):
                continue
            fact = item.get("fact")
            if not isinstance(fact, str) or not fact.strip():
                continue
            if contains_secret(fact):
                telemetry.inc("wiki_candidates_blocked_secret")
                log.warning(
                    "ConversationFactExtractor: blocked secret-shaped candidate"
                )
                continue
            kind = item.get("kind")
            if not isinstance(kind, str) or kind not in _KNOWN_KINDS:
                kind = "other"
            raw_subjects = item.get("subjects")
            subjects: tuple[str, ...] = ()
            if isinstance(raw_subjects, list):
                subjects = normalise_subjects(raw_subjects)
            raw_evidence = item.get("evidence_turn_id")
            evidence = raw_evidence.strip() if isinstance(raw_evidence, str) else ""
            if not evidence and fallback_evidence:
                evidence = fallback_evidence
            if not evidence or evidence not in allowed_evidence:
                log.debug(
                    "ConversationFactExtractor: dropped fact without valid user evidence"
                )
                continue
            evidence_excerpt = safe_preview(
                evidence_text_by_id.get(evidence, ""),
                max_chars=_MAX_EVIDENCE_EXCERPT_CHARS,
            ).strip()
            if is_unsupported_user_interest_claim(
                fact=fact,
                subjects=subjects,
                evidence_excerpt=evidence_excerpt,
                user_slug=self._user_entity_slug,
            ):
                log.info(
                    "ConversationFactExtractor: dropped unsupported user-interest "
                    "candidate"
                )
                continue
            facts.append(
                CandidateFact(
                    fact=fact.strip(),
                    kind=kind,
                    subjects=subjects,
                    evidence_turn_id=evidence,
                    evidence_excerpt=evidence_excerpt,
                )
            )
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


__all__ = ["ConversationContextTurn", "ConversationFactExtractor"]
