"""Deterministic wiki-write intent matcher (spec A1).

Explicit "write this to the wiki" commands must never depend on the
router LLM choosing the ``wiki-ingest`` tool — the weak free default
model on fresh installs almost never does (forensics Bug 12/18). This
matcher runs on the final user transcript in the brain's fast-path
pre-pass (same philosophy as ``jarvis/brain/local_action_gate.py``) and
fires the ingest pipeline model-independently.

Pure regex — no LLM, no IO (AP-9/AP-11). The de/en/es tokens are
speech-recognition input vocabulary (closed-list category 3).
Precision over recall: a false positive writes noise to the vault, a
false negative falls back to the (possibly capable) LLM tool path — so
every pattern REQUIRES an explicit wiki object.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# German-umlaut transliteration table (input normalization).
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})  # i18n-allow

# Write verbs (normalized, de/en/es).  # i18n-allow: input vocabulary
_VERBS = (
    r"(?:schreib(?:e|st)?|notier(?:e)?|speicher(?:e)?|merk(?:e)?\s+dir|"
    r"trag(?:e)?\s+.{0,20}?ein|halte?\s+.{0,30}?fest|"
    r"write|save|note|add|store|put|record|"
    r"escribe|guarda|anota|apunta|agrega)"
)

# Explicit wiki object (normalized, de/en/es).  # i18n-allow: input vocabulary
_WIKI_OBJ = (
    r"(?:(?:ins|in\s+das|im|in\s+mein(?:em)?|zum)\s+wiki"  # i18n-allow: input vocabulary
    r"|(?:to|in|into)\s+(?:the\s+|my\s+)?wiki"
    r"|(?:en|al)\s+(?:la\s+|el\s+|mi\s+)?wiki)"
)

_PREFIX = r"^(?:hey\s+)?(?:jarvis[,\s]+)?"

# Question openers that mean recall/general questions, never a write command.
_QUESTION_RE = re.compile(
    r"^(?:was|wer|wie|wo|wann|warum|what|who|how|where|when|why|que|qu|quien|"  # i18n-allow
    r"como|donde|cuando)\b",  # i18n-allow
)

# Anaphoric objects: the command refers to prior conversation content.
_ANAPHORA = frozenset({
    "das", "es", "dies", "diese", "dieses", "den", "die",   # i18n-allow
    "that", "this", "it", "them",
    "eso", "esto", "lo", "la",                              # i18n-allow
})

# Standalone filler particles (single tokens from _FILLER_RE's alternation)
# that carry no content on their own — e.g. "schreib das BITTE ins wiki" is  # i18n-allow
# still anaphoric even though "bitte" trails the anaphor instead of leading  # i18n-allow
# the fragment, so the prefix-anchored _FILLER_RE strip alone misses it.
_FILLER_WORDS = frozenset({
    "bitte", "mal", "doch", "kurz", "please", "por", "favor", "que", "dass",  # i18n-allow
})

_COMMAND_RE = re.compile(
    _PREFIX
    + _VERBS
    + r"\s+(?P<pre>.*?)\s*"
    + _WIKI_OBJ
    + r"\s*[:,]?\s*(?P<post>.*?)\s*[?.!]*$",
    re.IGNORECASE,
)

_FILLER_RE = re.compile(
    r"^(?:bitte|mal|doch|kurz|please|por\s+favor|que|dass|,)+\s*"  # i18n-allow
)


@dataclass(frozen=True, slots=True)
class WikiIntentMatch:
    #: Inline content to ingest; ``None`` = anaphoric — the caller supplies
    #: the last conversation exchange as the source.
    content: str | None
    #: The full matched utterance (normalized) for logging.
    matched: str


def _normalize(text: str) -> str:
    return text.strip().lower().translate(_UMLAUTS)


def _strip_filler(fragment: str) -> str:
    prev = None
    frag = fragment.strip()
    while prev != frag:
        prev = frag
        frag = _FILLER_RE.sub("", frag).strip()
    return frag


def match_wiki_intent(user_text: str) -> WikiIntentMatch | None:
    """Return a match for an explicit wiki-WRITE command, else ``None``."""
    norm = _normalize(user_text)
    if not norm or len(norm) > 600:
        return None
    if _QUESTION_RE.match(norm) or norm.endswith("?"):
        return None
    m = _COMMAND_RE.match(norm)
    if m is None:
        return None
    # Precision gate: a legitimate command puts the write verb DIRECTLY on
    # the wiki-object, so ``pre`` (whatever sits between them) is either
    # empty (the content follows the object) or a bare anaphor (the
    # "write THAT to the wiki" shape). Any real word in ``pre`` means the
    # wiki reference sits in a subordinate/recall clause (a recall question
    # that merely opens with a write verb), not a write target — so defer to
    # the LLM tool path. A false negative is acceptable; a false positive
    # writes noise to the vault.
    pre = _strip_filler(m.group("pre") or "")
    pre_words = [w for w in re.split(r"\s+", pre) if w]
    if any(w not in _ANAPHORA and w not in _FILLER_WORDS for w in pre_words):
        return None
    # Anaphoric-vs-inline is decided on ``post``: real content there is an
    # inline write; nothing meaningful means an anaphoric command whose
    # source is the prior conversation exchange.
    post = _strip_filler(m.group("post") or "")
    post_words = [w for w in re.split(r"\s+", post) if w]
    if not any(w not in _ANAPHORA and w not in _FILLER_WORDS for w in post_words):
        return WikiIntentMatch(content=None, matched=norm)
    return WikiIntentMatch(content=post, matched=norm)
