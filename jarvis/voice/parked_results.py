"""Parked results — a heavy turn's answer that outlives the conversation (ADR-0034).

When the tool model, a router round trip or a background agent takes longer
than the user's attention, the conversation moves on and the answer arrives
later. Every engine used to handle that moment on its own — the realtime
session with a 30 s "late result" queue, the pipeline with a deferred
announcement — and each dropped or mis-timed the answer in its own way. This
module is the ONE vocabulary both voice engines and the text surfaces share:

* :class:`ParkedResult` — the answer, the request it belongs to, and how long
  it has waited.
* :class:`ParkedResultLedger` — a per-session queue with **no time-based
  expiry**. A parked result leaves the ledger only for a named reason: it was
  delivered, the user cancelled the order, the same order was re-issued
  (superseded) or the session ended — and the caller logs the request it
  abandons by name.
* :func:`classify_wait_query` — the closed multilingual vocabulary for the
  two things a user says INTO a wait: "how far are you?" (progress) and "what
  came out of it?" (result). Regex only, every locale equal (CLAUDE.md §1),
  a miss stays native.
* :func:`reanchor` — the short spoken/written prefix that ties a result
  delivered after other exchanges back to the request it answers.

Deliberately stdlib-only and engine-neutral: no bus, no audio, no model
call. The engines decide WHEN a parked result may be delivered (their own
"at rest" gate) and HOW (live model, TTS, chat message); this module keeps
the queue honest.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final
from uuid import uuid4

#: A wait query the vocabulary does not recognise.
WAIT_QUERY_NONE: Final[str] = ""
#: "How far are you?" — the user asks about progress; answer with ONE grounded
#: progress line while the work still runs.
WAIT_QUERY_PROGRESS: Final[str] = "progress"
#: "What came out of it?" — the user asks for the outcome; deliver a ready
#: parked result now, or the progress line if it is still running.
WAIT_QUERY_RESULT: Final[str] = "result"

#: Surfaces a parked result can belong to (observability only, free-form).
SURFACE_REALTIME: Final[str] = "realtime"
SURFACE_PIPELINE: Final[str] = "pipeline"
SURFACE_CHAT: Final[str] = "chat"
SURFACE_CHANNEL: Final[str] = "channel"

_SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("de", "en", "es")
_DEFAULT_LANGUAGE: Final[str] = "en"
_MAX_WAIT_QUERY_WORDS: Final[int] = 9
_TOPIC_MAX_WORDS: Final[int] = 8
_RECENT_PREFIX_DEPTH: Final[int] = 2


def _language_key(language: str) -> str:
    key = str(language or "").strip().lower()[:2]
    return key if key in _SUPPORTED_LANGUAGES else _DEFAULT_LANGUAGE


def _fold(text: str) -> str:
    # Apostrophes are dropped, not spaced: "how's" folds to "hows" so one
    # pattern covers the contracted and the spelled-out form.
    lowered = re.sub(r"['’]", "", str(text or "").casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", lowered).split())


@dataclass(slots=True)
class ParkedResult:
    """One heavy answer waiting for room in the conversation."""

    text: str
    language: str
    request_text: str
    success: bool = True
    request_turn_id: str = ""
    delivery_id: str = ""
    surface: str = ""
    #: ``time.monotonic()`` when the result was parked (set by the ledger).
    queued_at: float = 0.0
    #: Turns the conversation completed while this result waited. Zero means
    #: the result follows its own acknowledgment directly and needs no
    #: re-anchoring prefix.
    intervening_turns: int = 0
    #: Free-form technical detail for the transcript (never spoken).
    detail: str = ""

    def waited_s(self, now: float | None = None) -> float:
        stamp = time.monotonic() if now is None else now
        return max(0.0, stamp - self.queued_at)


class ParkedResultLedger:
    """Per-session queue of parked results. FIFO, no expiry, named exits.

    ``clock`` is injectable for tests; every method is synchronous and never
    raises on an empty ledger.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._items: list[ParkedResult] = []

    # -- state ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self) -> Iterable[ParkedResult]:  # pragma: no cover - trivial
        return iter(tuple(self._items))

    def peek(self) -> ParkedResult | None:
        """The oldest parked result, or ``None``."""
        return self._items[0] if self._items else None

    def has(self, delivery_id: str) -> bool:
        return any(item.delivery_id == delivery_id for item in self._items)

    # -- transitions ------------------------------------------------------

    def park(self, result: ParkedResult) -> ParkedResult:
        """Queue ``result``. Idempotent per ``delivery_id``; stamps the clock."""
        if not result.delivery_id:
            result.delivery_id = f"parked:{uuid4()}"
        for existing in self._items:
            if existing.delivery_id == result.delivery_id:
                return existing
        if not result.queued_at:
            result.queued_at = float(self._clock())
        self._items.append(result)
        return result

    def pop(self, delivery_id: str) -> ParkedResult | None:
        """Remove and return the item with ``delivery_id`` (delivered)."""
        for index, item in enumerate(self._items):
            if item.delivery_id == delivery_id:
                return self._items.pop(index)
        return None

    def note_turn_completed(self) -> None:
        """A conversation turn finished while results were parked."""
        for item in self._items:
            item.intervening_turns += 1

    def cancel_all(self) -> list[ParkedResult]:
        """The user withdrew the running orders: drop everything, return it."""
        dropped, self._items = self._items, []
        return dropped

    def supersede(self, request_text: str) -> list[ParkedResult]:
        """The same order was issued again: drop older results for it."""
        key = _fold(request_text)
        if not key:
            return []
        kept: list[ParkedResult] = []
        dropped: list[ParkedResult] = []
        for item in self._items:
            (dropped if _fold(item.request_text) == key else kept).append(item)
        self._items = kept
        return dropped

    def drain(self) -> list[ParkedResult]:
        """Session end: hand back everything still parked so it can be logged."""
        return self.cancel_all()

    def retrieve_for(self, query: str) -> ParkedResult | None:
        """The parked result the user is asking for, if the query names one.

        With exactly one parked result any result request means it. With
        several, the query has to share a content word with the request text
        of one of them; otherwise the oldest is returned — the user asked for
        "the result", and the oldest debt is the most likely one.
        """
        if not self._items:
            return None
        if len(self._items) == 1:
            return self._items[0]
        query_words = _content_words(query)
        for item in self._items:
            if query_words & _content_words(item.request_text):
                return item
        return self._items[0]


# ---------------------------------------------------------------------------
# What the user says into a wait
# ---------------------------------------------------------------------------

# Closed speech-recognition INPUT vocabulary (matching data, not prose), all
# supported languages equal. Anchored to the whole utterance so a longer
# request that merely contains "result" stays a real turn for the model.
_LEAD_IN: Final[str] = (
    r"(?:(?:ja|yes|s[ií]|und|and|y|also|so|okay|ok|hey|hallo|hello|hola|"
    r"jarvis|bitte|please|por\s+favor|sag\s+mal|tell\s+me|dime)\s+)*"
)
_TAIL: Final[str] = (
    r"(?:\s+(?:damit|dabei|schon|jetzt|denn|eigentlich|now|yet|already|ya|ahora|con\s+eso))*"
)

_PROGRESS_CORES: Final[tuple[str, ...]] = (
    # --- German ---
    r"wie\s+weit\s+bist\s+du",
    r"wie\s+l(?:ä|ae)uft(?:s|\s+es)?",
    r"bist\s+du\s+(?:schon\s+)?fertig",
    r"dauert(?:s|\s+es)?\s+noch(?:\s+lange)?",
    r"wie\s+lange\s+(?:dauert(?:s|\s+es)?|noch|brauchst\s+du(?:\s+noch)?)",
    r"(?:l(?:ä|ae)uft|geht)\s+(?:das|es)\s+noch",
    r"arbeitest\s+du\s+noch(?:\s+dran)?",
    r"bist\s+du\s+noch\s+dran",
    # --- English ---
    r"how\s+far\s+(?:are\s+you|along(?:\s+are\s+you)?)",
    r"how(?:s|\s+is)\s+it\s+going",
    r"are\s+you\s+(?:done|finished|ready|through)",
    r"(?:is\s+it|its)\s+(?:done|finished|ready)",
    r"how\s+(?:much\s+)?longer",
    r"how\s+long\s+(?:will\s+(?:it|that)\s+take|is\s+(?:it|that)\s+going\s+to\s+take)",
    r"(?:are\s+you\s+)?still\s+(?:working(?:\s+on\s+(?:it|that))?|on\s+it|at\s+it)",
    r"any\s+(?:progress|update)",
    # --- Spanish ---
    r"c(?:ó|o)mo\s+vas",
    r"c(?:ó|o)mo\s+va(?:\s+eso)?",
    r"(?:ya\s+)?(?:has\s+terminado|terminaste|est(?:á|a)s\s+listo)",
    r"cu(?:á|a)nto\s+(?:falta|tarda|queda)",
    r"sigues\s+(?:trabajando|en\s+ello)",
)

_RESULT_CORES: Final[tuple[str, ...]] = (
    # --- German ---
    r"was\s+(?:kam|ist)\s+(?:dabei\s+|da\s+)?(?:raus|heraus|rausgekommen|herausgekommen)",
    r"was\s+hast\s+du\s+(?:rausgefunden|herausgefunden|gefunden)",
    r"hast\s+du\s+(?:schon\s+)?(?:ein|das|'?n)?\s*ergebnis",
    r"(?:gibt(?:s|\s+es)\s+)?(?:schon\s+)?(?:ein\s+)?ergebnis",
    r"und\s+(?:das\s+)?ergebnis",
    r"was\s+ist\s+(?:das\s+)?ergebnis",
    r"hast\s+du(?:s|\s+es|\s+das)(?:\s+schon)?",
    r"und\s+was\s+(?:kam|ist)\s+(?:dabei\s+)?(?:raus|rausgekommen)",
    # --- English ---
    r"what\s+(?:did\s+you\s+find|came\s+(?:out|of\s+it)|was\s+the\s+result|did\s+you\s+get)",
    r"(?:do\s+you\s+have|got)\s+(?:the|a|an|any)?\s*(?:result|answer|results)",
    r"any\s+(?:result|results|answer)",
    r"(?:and\s+)?the\s+result",
    r"what(?:s|\s+is)\s+the\s+result",
    r"did\s+you\s+(?:find|get)\s+(?:it|anything|something)",
    # --- Spanish ---
    r"qu(?:é|e)\s+(?:encontraste|sali(?:ó|o)|has\s+encontrado|resultado\s+hay)",
    r"(?:ya\s+)?tienes\s+(?:el|un|alg(?:ú|u)n)?\s*resultado",
    r"y\s+el\s+resultado",
    r"cu(?:á|a)l\s+es\s+el\s+resultado",
)


def _compile(cores: tuple[str, ...]) -> re.Pattern[str]:
    body = "|".join(f"(?:{core})" for core in cores)
    return re.compile(rf"^{_LEAD_IN}(?:{body}){_TAIL}$")


_PROGRESS_RE: Final[re.Pattern[str]] = _compile(_PROGRESS_CORES)
_RESULT_RE: Final[re.Pattern[str]] = _compile(_RESULT_CORES)


def classify_wait_query(text: str) -> str:
    """Return :data:`WAIT_QUERY_PROGRESS`, :data:`WAIT_QUERY_RESULT` or ``""``.

    Deliberately strict: whole-utterance anchored, at most
    ``_MAX_WAIT_QUERY_WORDS`` words. Anything longer is a real request the
    model must handle — "what did you find about the flight prices in the
    Wiki?" is not a probe.
    """
    normalized = _fold(text)
    if not normalized or len(normalized.split()) > _MAX_WAIT_QUERY_WORDS:
        return WAIT_QUERY_NONE
    if _RESULT_RE.fullmatch(normalized):
        return WAIT_QUERY_RESULT
    if _PROGRESS_RE.fullmatch(normalized):
        return WAIT_QUERY_PROGRESS
    return WAIT_QUERY_NONE


# ---------------------------------------------------------------------------
# Re-anchoring: "about your earlier request, …"
# ---------------------------------------------------------------------------

# Words that carry no topic when the request is shortened into a spoken
# reference. Kept small: the topic must stay recognisably the user's words.
_TOPIC_STOPWORDS: Final[dict[str, frozenset[str]]] = {
    "de": frozenset(
        "bitte kannst du kann ich mal mir mich mich mal doch eben schnell kurz "
        "hey hallo jarvis ok okay und dann also ja nein der die das ein eine einen "
        "einem einer den dem des".split()
    ),
    "en": frozenset(
        "please can could would you me my i hey jarvis ok okay and then just "
        "quickly the a an".split()
    ),
    "es": frozenset(
        "por favor puedes podrías me mi yo hey hola jarvis ok vale y luego "
        "el la los las un una unos unas".split()
    ),
}

_ANCHOR_POOLS: Final[dict[str, tuple[str, ...]]] = {
    "de": (
        "Zu deiner Anfrage von vorhin – {topic}: {result}",
        "Noch zu vorhin, {topic}: {result}",
        "Das Ergebnis zu {topic}: {result}",
    ),
    "en": (
        "About your earlier request – {topic}: {result}",
        "Back to {topic}: {result}",
        "Here's the result on {topic}: {result}",
    ),
    "es": (
        "Sobre tu petición de antes – {topic}: {result}",
        "Volviendo a {topic}: {result}",
        "El resultado sobre {topic}: {result}",
    ),
}
_ANCHOR_POOLS_NO_TOPIC: Final[dict[str, tuple[str, ...]]] = {
    "de": (
        "Zu deiner Anfrage von vorhin: {result}",
        "Noch zu vorhin: {result}",
    ),
    "en": (
        "About your earlier request: {result}",
        "Back to what you asked earlier: {result}",
    ),
    "es": (
        "Sobre tu petición de antes: {result}",
        "Volviendo a lo de antes: {result}",
    ),
}

_RECENT_ANCHORS: dict[str, deque[str]] = {}


def _content_words(text: str, language: str = "") -> set[str]:
    key = _language_key(language) if language else ""
    stop: set[str] = set()
    if key:
        stop = set(_TOPIC_STOPWORDS.get(key, frozenset()))
    else:
        for words in _TOPIC_STOPWORDS.values():
            stop |= set(words)
    return {word for word in _fold(text).split() if len(word) > 2 and word not in stop}


def topic_of(request_text: str, language: str, *, max_words: int = _TOPIC_MAX_WORDS) -> str:
    """A short spoken reference to ``request_text`` in the user's own words.

    Leading fillers and address words are dropped, the rest is cut to
    ``max_words`` words. Returns ``""`` when nothing topical is left, so the
    caller can fall back to a prefix without a topic.
    """
    key = _language_key(language)
    stop = _TOPIC_STOPWORDS.get(key, frozenset())
    words = str(request_text or "").strip().split()
    while words and _fold(words[0]) in stop:
        words.pop(0)
    if not words:
        return ""
    cleaned = [re.sub(r"[?!.,;:]+$", "", word) for word in words]
    cleaned = [word for word in cleaned if word]
    if not cleaned:
        return ""
    if len(cleaned) > max_words:
        return " ".join(cleaned[:max_words]) + " …"
    return " ".join(cleaned)


def anchor_pool(language: str, *, with_topic: bool = True) -> tuple[str, ...]:
    key = _language_key(language)
    pools = _ANCHOR_POOLS if with_topic else _ANCHOR_POOLS_NO_TOPIC
    return tuple(pools.get(key, pools[_DEFAULT_LANGUAGE]))


def reanchor(
    result_text: str,
    *,
    request_text: str,
    language: str,
    intervening_turns: int = 1,
    choose: Callable[[tuple[str, ...]], str] | None = None,
) -> str:
    """Prefix ``result_text`` with a reference to the request it answers.

    No prefix when nothing happened in between (``intervening_turns == 0``):
    the result then follows its own acknowledgment and a preface would be the
    double-tap ADR-0033 forbids. Otherwise one line from the closed pool,
    with the request's topic when one can be extracted. ``choose`` lets a
    caller (or a test) pick deterministically; the default avoids repeating
    the last two prefixes per language.
    """
    body = str(result_text or "").strip()
    if not body or intervening_turns <= 0:
        return body
    key = _language_key(language)
    topic = topic_of(request_text, key)
    pool = anchor_pool(key, with_topic=bool(topic))
    if choose is None:
        recent = _RECENT_ANCHORS.setdefault(key, deque(maxlen=_RECENT_PREFIX_DEPTH))
        candidates = [line for line in pool if line not in recent] or list(pool)
        template = candidates[0]
        recent.append(template)
    else:
        template = choose(pool)
    return template.format(topic=topic, result=body)


__all__ = [
    "SURFACE_CHANNEL",
    "SURFACE_CHAT",
    "SURFACE_PIPELINE",
    "SURFACE_REALTIME",
    "WAIT_QUERY_NONE",
    "WAIT_QUERY_PROGRESS",
    "WAIT_QUERY_RESULT",
    "ParkedResult",
    "ParkedResultLedger",
    "anchor_pool",
    "classify_wait_query",
    "reanchor",
    "topic_of",
]
