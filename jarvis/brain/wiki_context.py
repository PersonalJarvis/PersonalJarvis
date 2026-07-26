"""Wiki context injector for the brain system prompt.

Performs a fast, latency-bounded vault search before each brain turn and
prepends the surviving snippets to the system prompt as a framed personal-memory
section.  If the relevance gate declines the turn, if the search exceeds the
latency budget, if no useful keywords can be extracted from the user text, or if
nothing retrieved is relevant enough, the original system prompt is returned
unchanged.

Relevance contract (``jarvis.brain.wiki_relevance``):
    Retrieval has no null element — a keyword search always returns a ranked
    list, and "best match" never means "good match".  Injecting whatever came
    back is what welds unrelated personal facts onto general-knowledge answers.
    Three gates, in order:

    1. BEFORE searching, ``should_consult_memory`` skips turns that are not
       questions about the user's own life.  A general-knowledge question never
       touches the vault at all — no latency, no context, no non-sequitur.
    2. AFTER searching, ``relevant_hits`` drops hits that merely share a common
       word with the question.
    3. AT injection, ``frame_context_block`` states that the notes may be
       irrelevant and that the model must ignore them if so.

    When the gate declines a turn the knowledge is not lost: the router brain
    holds the ``wiki-recall`` tool and can look something up deliberately.

UltraWiki mode (decision D-5, either-or):
    While UltraWiki mode is ON and its store is open, the candidates come from
    the UltraWiki store instead of the vault — never from both. Only the
    RETRIEVAL changes; the three gates above still decide what may be injected,
    because this surface is unsolicited and the Bugatti mandate is retrieval-
    agnostic: silence beats a personal fact nobody asked for.

    Two gates are mapped honestly onto UltraWiki's different score semantics:

    * The within-call relative floor is REPLACED by a keyword-leg requirement.
      An UltraWiki ``score`` is an RRF fusion rank — ordinal by construction, so
      a relative floor over it certifies nothing (a fusion over garbage still
      produces a confident-looking number one). A candidate that only the vector
      leg produced has no lexical evidence at all, so the deterministic
      substitute is: the keyword leg must have seen it too.
    * Coverage of the question's content terms is applied unchanged — it never
      depended on scores in the first place.

    The absolute measure UltraWiki does have (the rerank grade, which
    ``enforce_floor`` gates on) is deliberately NOT used here: grading costs a
    model call, which is exactly what may not happen on this path (AP-9). Design
    doc 03 anticipates that case — an ungraded candidate stays the caller's
    deterministic gate's responsibility, which is this module plus
    ``wiki_relevance``.

Latency contract:
    The whole ``maybe_inject`` coroutine must complete in <= ``latency_budget_ms``
    milliseconds (``ultra_latency_budget_ms`` for the UltraWiki path).  It uses
    ``asyncio.wait_for`` to enforce this.  A slow vault (cold filesystem, network
    FS, etc.) therefore cannot block the voice path.  The relevance gate itself
    is regex-only and IO-free (AP-9/AP-11).

    The UltraWiki path gets its own, larger budget because it is a database
    query rather than a grep, and because its vector leg embeds the query text
    when an embedding provider is configured — cheap on a local endpoint,
    a network round-trip on a cloud one. The rerank stage (a second model call)
    is switched off outright. Whatever exceeds the budget is dropped: the turn
    proceeds with NO context rather than waiting.

Fallback contract:
    When ``search`` is ``None`` (Agent B not yet merged) and no UltraWiki
    service is available, the injector silently does nothing.  Pass
    ``search=None`` from the factory; every ``maybe_inject`` call returns the
    prompt unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.memory.wiki.search import VaultSearch

from jarvis.brain.wiki_relevance import (
    DEFAULT_MIN_COVERAGE,
    DEFAULT_MIN_RELATIVE_SCORE,
    frame_context_block,
    relevant_hits,
    should_consult_memory,
)
from jarvis.memory.wiki.telemetry import telemetry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stopword set (German + English, inlined — no nltk dependency)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset({
    # German
    "aber", "alle", "allem", "allen", "aller", "alles", "also", "ander",
    "andere", "anderem", "anderen", "anderer", "anderes", "anderm", "andern",
    "anderr", "anders", "auch", "auf", "aus", "bald", "beime", "beim",  # i18n-allow: German stopword list, matched against user text for wiki relevance scoring
    "bereits", "bin", "bist", "bitte", "bzw", "dabei", "dadurch", "damit",
    "dann", "dass", "dein", "deine", "deinem", "deinen", "deiner", "deines",
    "denen", "denn", "derer", "dessen", "dies", "diese", "diesem", "diesen",
    "dieser", "dieses", "doch", "durch", "ein", "eine", "einem", "einen",  # i18n-allow: same German stopword list
    "einer", "eines", "einig", "einige", "einigem", "einigen", "einiger",
    "einiges", "einmal", "erst", "etwa", "euch", "euer", "eure", "eurem",
    "euren", "eurer", "eures", "falls", "fast", "fuer", "ganz", "gemacht",  # i18n-allow: same German stopword list
    "gibt", "hatte", "haben", "habe", "habt", "hier", "hinter", "ihnen",  # i18n-allow: same German stopword list
    "ihrer", "ihrem", "ihres", "ihren", "indem", "irgend", "ist", "jede",
    "jedem", "jeden", "jeder", "jedes", "jetzt", "kein", "keine", "keinem",  # i18n-allow: same German stopword list
    "keinen", "keiner", "keines", "kann", "kannst", "konnte", "koennen",  # i18n-allow: same German stopword list
    "macht", "manche", "manchem", "manchen", "mancher", "manches", "mein",
    "meine", "meinem", "meinen", "meiner", "meines", "mehr", "mich", "muss",  # i18n-allow: same German stopword list
    "nach", "nicht", "noch", "oder", "ohne", "sehr", "sein", "seine",  # i18n-allow: same German stopword list
    "seinem", "seinen", "seiner", "seines", "seit", "selbst", "sich", "sie",
    "sind", "soll", "sollen", "sollte", "sondern", "sonst", "ueber", "und",  # i18n-allow: same German stopword list
    "unser", "unsere", "unserem", "unseren", "unserer", "unseres", "unter",
    "viel", "viele", "vielem", "vielen", "vieler", "vieles", "vom", "von",  # i18n-allow: same German stopword list
    "vor", "wann", "ward", "warum", "was", "weg", "weil", "welche", "welchem",
    "welchen", "welcher", "welches", "wenn", "wer", "werden", "wie", "wieder",  # i18n-allow: same German stopword list
    "will", "wird", "wirst", "wohl", "worden", "wurden", "wurde", "wird",  # i18n-allow: same German stopword list
    "zwar", "zwischen",
    # English
    "about", "above", "after", "again", "against", "among", "any",
    "are", "because", "been", "before", "being", "between", "both", "but",
    "came", "can", "come", "could", "did", "does", "doing", "done", "down",
    "during", "each", "few", "for", "from", "further", "gave", "get", "give",
    "goes", "going", "gone", "got", "had", "has", "have", "having", "here",
    "him", "his", "how", "into", "its", "just", "know", "like", "long",
    "look", "make", "many", "more", "most", "much", "must", "need", "new",
    "next", "not", "now", "old", "once", "only", "other", "our", "out",
    "over", "same", "say", "should", "since", "some", "still", "such",
    "tell", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "though", "through", "time", "told", "too",
    "under", "until", "upon", "use", "used", "using", "very", "want", "well", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "would", "you", "your",
    # Short German articles and pronouns
    "das", "dem", "den", "der", "des", "die", "dir", "du", "ein",  # i18n-allow: same German stopword list
    "hat", "ich", "ihm", "ihn", "ihr", "ins", "man", "mir", "mit",
    "nun", "nur", "pro", "sei", "uns", "war", "wir", "wen",
    "zum", "zur",  # i18n-allow: same German stopword list
})

# Tokenize on whitespace and common punctuation
_TOKEN_RE = re.compile(r"[^\w\s]|\s+", re.UNICODE)

#: Time budget for the UltraWiki retrieval leg. Its own knob because it is a
#: store query (plus a possible query embedding) rather than the vault's grep;
#: design doc 03 allots <= 150 ms to the fan-out inside a <= 900 ms
#: to-first-spoken-token budget, and this stays in that order of magnitude.
DEFAULT_ULTRA_LATENCY_BUDGET_MS = 250

#: How many UltraWiki candidates the gates get to judge. Small on purpose: the
#: block is capped at ``max_chars`` anyway and every extra candidate is one more
#: chance for an off-topic personal fact to slip past.
DEFAULT_ULTRA_K = 5

#: The retrieval leg that must have seen an UltraWiki candidate before it may
#: be injected — the deterministic stand-in for an absolute score (see the
#: module docstring).
_KEYWORD_LEG = "keyword"


def _extract_keywords(
    text: str,
    *,
    min_length: int = 4,
    max_keywords: int = 3,
) -> list[str]:
    """Extract 1-3 meaningful keywords from a user utterance.

    Strategy:
    1. Tokenize on whitespace + punctuation.
    2. Drop tokens shorter than ``min_length``.
    3. Drop tokens that are in the stopword list (case-insensitive).
    4. Prefer tokens that are capitalized mid-sentence (likely proper nouns),
       but include lowercase tokens too if there are not enough proper nouns.
    5. Return up to ``max_keywords`` tokens, proper nouns first.
    """
    raw_tokens = _TOKEN_RE.sub(" ", text).split()

    # Filter by length and stopwords
    candidates: list[str] = []
    for i, tok in enumerate(raw_tokens):
        if len(tok) < min_length:
            continue
        if tok.lower() in _STOPWORDS:
            continue
        # Skip leading token of the sentence (likely a greeting/verb, not a noun)
        # unless the whole utterance is very short (fewer than 4 tokens total)
        candidates.append(tok)

    if not candidates:
        return []

    # Prefer proper nouns (capitalized, not first word of utterance)
    first_word = raw_tokens[0] if raw_tokens else ""
    proper_nouns = [
        t for t in candidates
        if t[0].isupper() and t != first_word
    ]
    others = [t for t in candidates if t not in proper_nouns]

    # Merge: proper nouns first, then others; deduplicate (preserve order)
    seen: set[str] = set()
    ordered: list[str] = []
    for t in proper_nouns + others:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(t)

    return ordered[:max_keywords]


class WikiContextInjector:
    """Latency-bounded wiki-snippet injector for the system prompt.

    Construction is cheap; one instance is reused for the lifetime of the
    BrainManager.  The injector is a no-op when ``search`` is ``None`` (used
    when Agent B's work is not yet merged — fallback path).

    Usage::

        injector = WikiContextInjector(search=vault_search)
        augmented = await injector.maybe_inject(
            user_text="When was Harald born?",
            system_prompt=base_prompt,
        )

    Log line (INFO, exactly one per call)::

        WikiContextInjector injected=True hits=2 latency_ms=14
    """

    def __init__(
        self,
        *,
        search: VaultSearch | None,
        max_chars: int = 1500,
        latency_budget_ms: int = 80,
        min_keyword_length: int = 4,
        relevance_gate: bool = True,
        min_coverage: float = DEFAULT_MIN_COVERAGE,
        min_relative_score: float = DEFAULT_MIN_RELATIVE_SCORE,
        ultra_latency_budget_ms: int = DEFAULT_ULTRA_LATENCY_BUDGET_MS,
        ultra_k: int = DEFAULT_ULTRA_K,
    ) -> None:
        self._search = search
        self._max_chars = max_chars
        self._latency_budget_ms = latency_budget_ms
        self._min_keyword_length = min_keyword_length
        self._ultra_latency_budget_ms = ultra_latency_budget_ms
        self._ultra_k = ultra_k
        # Escape hatch: ``relevance_gate=False`` restores the pre-gate
        # behaviour (search every turn, inject every hit). Kept so a user who
        # believes the gate is too strict can prove it from config rather than
        # by patching code — the defaults stay on.
        self._relevance_gate = relevance_gate
        self._min_coverage = min_coverage
        self._min_relative_score = min_relative_score

    def _miss(self, t0: float, reason: str) -> None:
        """Record one skipped injection: telemetry + the single INFO line."""
        telemetry.inc("wiki_context_misses")
        log.info(
            "WikiContextInjector injected=False hits=0 latency_ms=%d reason=%s",
            int((time.monotonic() - t0) * 1000),
            reason,
        )

    async def maybe_inject(
        self,
        *,
        user_text: str,
        system_prompt: str,
    ) -> str:
        """Return system_prompt unchanged on any of:

        * no memory at all (``search is None`` and no UltraWiki service)
        * no extractable keywords from ``user_text``
        * the retrieval exceeds its latency budget
        * retrieval returns zero hits
        * nothing survives the relevance gates

        Otherwise returns::

            system_prompt + "\\n\\n## Wiki context\\n" + merged_snippets

        with up to ``max_chars`` of merged snippets, each prefixed by its
        page title.

        Logs exactly one line per call at INFO::

            WikiContextInjector injected=<bool> hits=<n> latency_ms=<int> …
        """
        t0 = time.monotonic()

        # Which memory answers this turn — UltraWiki when the mode is on and
        # its store is open, the vault otherwise. Never both (D-5).
        ultra_service = _active_ultrawiki_service()

        # Fast-path: no memory available at all (Agent B not merged yet, and
        # no UltraWiki service in this runtime)
        if ultra_service is None and self._search is None:
            self._miss(t0, "no_search")
            return system_prompt

        # Gate 1 (pre-retrieval): is this a question about the user's own life
        # at all? A general-knowledge question must never reach the vault —
        # that is what produces personalized non-sequiturs. Regex-only, so the
        # skip path costs microseconds and SAVES the search latency.
        if self._relevance_gate:
            verdict = should_consult_memory(user_text)
            if not verdict.consult:
                self._miss(t0, verdict.reason)
                return system_prompt

        # Extract keywords
        keywords = _extract_keywords(
            user_text,
            min_length=self._min_keyword_length,
        )
        if not keywords:
            self._miss(t0, "no_keywords")
            return system_prompt

        query = " ".join(keywords)

        # Run search with a strict latency budget
        ultra = ultra_service is not None
        budget_ms = self._ultra_latency_budget_ms if ultra else self._latency_budget_ms
        stage_timings: dict[str, float] = {}
        try:
            hits = await asyncio.wait_for(
                _run_ultra_search(
                    ultra_service,
                    query,
                    self._ultra_k,
                    budget_s=budget_ms / 1000.0,
                    timings=stage_timings,
                )
                if ultra
                else _run_search(self._search, query),
                timeout=budget_ms / 1000.0,
            )
        except TimeoutError:
            log.warning(
                "WikiContextInjector timed out after %dms (budget=%dms, "
                "source=%s, stages=%s) — skipping wiki context for this turn",
                int((time.monotonic() - t0) * 1000),
                budget_ms,
                "ultrawiki" if ultra else "vault",
                _stage_summary(stage_timings),
            )
            self._miss(t0, "ultra_timeout" if ultra else "timeout")
            return system_prompt
        except Exception:  # noqa: BLE001
            log.warning(
                "WikiContextInjector search raised unexpectedly — "
                "skipping wiki context",
                exc_info=True,
            )
            self._miss(t0, "ultra_search_error" if ultra else "search_error")
            return system_prompt

        if not hits:
            self._miss(t0, "no_hits")
            return system_prompt

        # Gate 2 (post-retrieval): the index matches on ANY query term, so a
        # page sharing one common word arrives looking just like one that is
        # on topic. Coverage + a within-call relative floor separate them.
        if self._relevance_gate:
            if ultra:
                # An RRF score is ordinal, so the relative floor is neutralised
                # (0.0) and the keyword leg carries that half of the gate
                # instead — see the module docstring.
                hits = [hit for hit in hits if _has_keyword_leg(hit)]
                if not hits:
                    self._miss(t0, "no_keyword_leg")
                    return system_prompt
            hits = relevant_hits(
                hits,
                query,
                min_coverage=self._min_coverage,
                min_relative_score=0.0 if ultra else self._min_relative_score,
            )
            if not hits:
                self._miss(t0, "no_relevant_hits")
                return system_prompt

        latency_ms = int((time.monotonic() - t0) * 1000)

        # Build the context block, capped at max_chars
        context_parts: list[str] = []
        chars_used = 0
        hits_included = 0
        for hit in hits:
            # A page found through a frontmatter alias (the language bridge)
            # has no body snippet by contract — fall back to its leading text
            # so the entry carries content instead of just a bare title.
            text = hit.snippet or getattr(hit, "preview", "") or ""
            if ultra:
                # An UltraWiki snippet can span lines; the block is one entry
                # per line. An ingested item may also carry no title at all,
                # in which case its source is the only honest label.
                text = " ".join(text.split())
            title = hit.title or (getattr(hit, "source_id", "") if ultra else "")
            entry = f"**{title}**: {text}"
            if chars_used + len(entry) + 1 > self._max_chars:
                # Try trimming to fit the remaining budget
                remaining = self._max_chars - chars_used - len(f"**{title}**: ") - 1
                if remaining >= 40:  # only worth including if enough chars remain
                    entry = f"**{title}**: {text[:remaining]}…"
                else:
                    break
            context_parts.append(entry)
            chars_used += len(entry) + 1  # +1 for the newline
            hits_included += 1

        if not context_parts:
            self._miss(t0, "empty_block")
            return system_prompt

        # Gate 3 (at injection): the block carries its own usage contract, so
        # the model knows the notes are a guess and is explicitly allowed to
        # ignore them. A bare context block reads as an instruction to use it.
        augmented = system_prompt + "\n\n" + frame_context_block(context_parts)

        telemetry.inc("wiki_context_hits")
        log.info(
            "WikiContextInjector injected=True hits=%d latency_ms=%d source=%s%s",
            hits_included,
            latency_ms,
            "ultrawiki" if ultra else "vault",
            f" stages={_stage_summary(stage_timings)}" if stage_timings else "",
        )
        return augmented


def _stage_summary(timings: dict[str, float]) -> str:
    """Compact ``keyword=12 vector=88`` attribution for the one log line —
    durations only, never query text."""
    if not timings:
        return "none"
    return " ".join(
        f"{name[: -len('_ms')]}={value:.0f}" for name, value in timings.items()
    )


async def _run_search(search: VaultSearch, query: str) -> list:
    """Thin async wrapper around VaultSearch.search.

    VaultSearch.search is a synchronous method (file-walking + grep).
    We run it in the default executor to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, search.search, query)


def _active_ultrawiki_service() -> Any | None:
    """The live UltraWiki service while it can answer, else ``None``.

    Lazy import (AP-26): with the mode off this is one ``sys.modules`` lookup
    per turn and nothing else — no config read, no disk, no network.
    """
    try:
        from jarvis.ultrawiki.service import active_search_service

        return active_search_service()
    except Exception:  # noqa: BLE001 — an unavailable mode is not an error here
        log.debug("WikiContextInjector: UltraWiki lookup failed", exc_info=True)
        return None


async def _run_ultra_search(
    service: Any,
    query: str,
    k: int,
    *,
    budget_s: float,
    timings: dict[str, float] | None = None,
) -> list:
    """Candidate retrieval from the UltraWiki store — cheap legs only.

    ``rerank=False`` keeps the grading model call off this path (AP-9);
    ``expand_context=False`` skips the neighbour lookups, which are evidence
    for an answer the user asked for, not for a prompt hint. ``enforce_floor``
    would be a no-op without grades and is left off deliberately: the gates in
    :meth:`WikiContextInjector.maybe_inject` are what stands in for it.

    ``vector_timeout_s`` caps the vector leg at HALF the caller's overall
    budget: embedding the query is a network round trip (and the live log
    shows providers answering 429), and without this cap the outer
    ``wait_for`` cancels the WHOLE search — the keyword hits die with the
    slow leg and the injector can structurally never fire. The injection
    gate only admits keyword-confirmed hits anyway (`_has_keyword_leg`), so
    losing a slow vector leg costs ordering consensus, never a candidate.
    """
    return await service.search(
        query=query,
        k=k,
        rerank=False,
        enforce_floor=False,
        expand_context=False,
        vector_timeout_s=max(0.05, budget_s * 0.5),
        timings=timings,
    )


def _has_keyword_leg(hit: Any) -> bool:
    """True when the keyword leg produced this candidate.

    A vector-only candidate matched on embedding proximity alone. Nothing in
    an RRF score can tell "close in meaning" from "closest of a bad lot", so
    an unsolicited surface must not inject it. Hits from a store that reports
    no legs at all are treated as keyword hits — the keyword leg is the one
    that always runs (search.py), so an absent label is a missing report, not
    evidence of a vector-only match.
    """
    legs = getattr(hit, "matched_by", None)
    if not legs:
        return True
    return _KEYWORD_LEG in tuple(legs)
