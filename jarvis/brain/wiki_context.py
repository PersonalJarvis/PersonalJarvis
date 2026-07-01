"""Wiki context injector for the brain system prompt.

Performs a fast, latency-bounded vault search before each brain turn and
prepends the top matching snippets to the system prompt as a ``## Wiki context``
section.  If the search exceeds the latency budget, or if no useful keywords can
be extracted from the user text, or if the vault returns no hits, the original
system prompt is returned unchanged.

Latency contract:
    The whole ``maybe_inject`` coroutine must complete in <= ``latency_budget_ms``
    milliseconds.  It uses ``asyncio.wait_for`` to enforce this.  A slow vault
    (cold filesystem, network FS, etc.) therefore cannot block the voice path.

Fallback contract:
    When ``search`` is ``None`` (Agent B not yet merged), the injector silently
    does nothing.  Pass ``search=None`` from the factory; every ``maybe_inject``
    call returns the prompt unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.wiki.search import VaultSearch

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
    "about", "above", "after", "again", "against", "also", "among", "any",
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
    "under", "until", "upon", "use", "used", "using", "very", "want", "was",
    "well", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "would", "you", "your",
    # Short German articles and pronouns
    "das", "dem", "den", "der", "des", "die", "dir", "doch", "du", "ein",  # i18n-allow: same German stopword list
    "hat", "ich", "ihm", "ihn", "ihr", "ihm", "ins", "man", "mir", "mit",
    "nun", "nur", "pro", "sei", "sie", "uns", "war", "wir", "wer", "wen",
    "zum", "zur",  # i18n-allow: same German stopword list
})

# Tokenize on whitespace and common punctuation
_TOKEN_RE = re.compile(r"[^\w\s]|\s+", re.UNICODE)


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
        search: "VaultSearch | None",
        max_chars: int = 1500,
        latency_budget_ms: int = 80,
        min_keyword_length: int = 4,
    ) -> None:
        self._search = search
        self._max_chars = max_chars
        self._latency_budget_ms = latency_budget_ms
        self._min_keyword_length = min_keyword_length

    async def maybe_inject(
        self,
        *,
        user_text: str,
        system_prompt: str,
    ) -> str:
        """Return system_prompt unchanged on any of:

        * ``search is None``
        * no extractable keywords from ``user_text``
        * ``VaultSearch.search`` exceeds ``latency_budget_ms``
        * search returns zero hits

        Otherwise returns::

            system_prompt + "\\n\\n## Wiki context\\n" + merged_snippets

        with up to ``max_chars`` of merged snippets, each prefixed by its
        page title.

        Logs exactly one line per call at INFO::

            WikiContextInjector injected=<bool> hits=<n> latency_ms=<int>
        """
        t0 = time.monotonic()

        # Fast-path: no search engine available (Agent B not merged yet)
        if self._search is None:
            latency_ms = int((time.monotonic() - t0) * 1000)
            telemetry.inc("wiki_context_misses")
            log.info(
                "WikiContextInjector injected=False hits=0 latency_ms=%d",
                latency_ms,
            )
            return system_prompt

        # Extract keywords
        keywords = _extract_keywords(
            user_text,
            min_length=self._min_keyword_length,
        )
        if not keywords:
            latency_ms = int((time.monotonic() - t0) * 1000)
            telemetry.inc("wiki_context_misses")
            log.info(
                "WikiContextInjector injected=False hits=0 latency_ms=%d",
                latency_ms,
            )
            return system_prompt

        query = " ".join(keywords)

        # Run search with a strict latency budget
        try:
            hits = await asyncio.wait_for(
                _run_search(self._search, query),
                timeout=self._latency_budget_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            latency_ms = int((time.monotonic() - t0) * 1000)
            log.warning(
                "WikiContextInjector timed out after %dms (budget=%dms) — "
                "skipping wiki context for this turn",
                latency_ms,
                self._latency_budget_ms,
            )
            telemetry.inc("wiki_context_misses")
            log.info(
                "WikiContextInjector injected=False hits=0 latency_ms=%d",
                latency_ms,
            )
            return system_prompt
        except Exception:  # noqa: BLE001
            latency_ms = int((time.monotonic() - t0) * 1000)
            log.warning(
                "WikiContextInjector search raised unexpectedly — "
                "skipping wiki context",
                exc_info=True,
            )
            telemetry.inc("wiki_context_misses")
            log.info(
                "WikiContextInjector injected=False hits=0 latency_ms=%d",
                latency_ms,
            )
            return system_prompt

        latency_ms = int((time.monotonic() - t0) * 1000)

        if not hits:
            telemetry.inc("wiki_context_misses")
            log.info(
                "WikiContextInjector injected=False hits=0 latency_ms=%d",
                latency_ms,
            )
            return system_prompt

        # Build the context block, capped at max_chars
        context_parts: list[str] = []
        chars_used = 0
        hits_included = 0
        for hit in hits:
            entry = f"**{hit.title}**: {hit.snippet}"
            if chars_used + len(entry) + 1 > self._max_chars:
                # Try trimming to fit the remaining budget
                remaining = self._max_chars - chars_used - len(f"**{hit.title}**: ") - 1
                if remaining >= 40:  # only worth including if enough chars remain
                    entry = f"**{hit.title}**: {hit.snippet[:remaining]}…"
                else:
                    break
            context_parts.append(entry)
            chars_used += len(entry) + 1  # +1 for the newline
            hits_included += 1

        if not context_parts:
            telemetry.inc("wiki_context_misses")
            log.info(
                "WikiContextInjector injected=False hits=0 latency_ms=%d",
                latency_ms,
            )
            return system_prompt

        wiki_block = "## Wiki context\n" + "\n".join(context_parts)
        augmented = system_prompt + "\n\n" + wiki_block

        telemetry.inc("wiki_context_hits")
        log.info(
            "WikiContextInjector injected=True hits=%d latency_ms=%d",
            hits_included,
            latency_ms,
        )
        return augmented


async def _run_search(search: "VaultSearch", query: str) -> list:
    """Thin async wrapper around VaultSearch.search.

    VaultSearch.search is a synchronous method (file-walking + grep).
    We run it in the default executor to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, search.search, query)
