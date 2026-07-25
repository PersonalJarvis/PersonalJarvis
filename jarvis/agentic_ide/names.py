"""Speakable call-signs for Agentic-IDE terminals.

Why terminals get names instead of numbers: the whole point of the Agentic IDE
is that the user can ASK about a running agent by voice — "what is Mika doing?"
— and a spoken ordinal ("terminal three") is both awkward to say and easy for
speech recognition to mangle into a different number. A short, phonetically
distinct proper name survives an imperfect transcript far better, and a wrong
match is recoverable (the user hears the name back in the answer).

Selection criteria for the pool:
- two syllables or fewer, no consonant clusters that ASR routinely drops,
- pairwise phonetically distinct (no "Mika"/"Mica", no "Leo"/"Theo"),
- neutral across the supported locales (de / en / es) — every name is
  pronounceable and gender-neutral-ish in all three,
- no collision with the wake word or with provider names.

Name resolution is deliberately fuzzy (``resolve``): the caller is matching a
name a human just spoke through an imperfect transcript. This is NOT the wake
word — AP-27 does not apply, because a mismatch here costs one clarifying
question, not a deaf assistant. The floor is still high enough that room noise
does not silently address a random terminal.
"""
from __future__ import annotations

from difflib import SequenceMatcher

# Ordered: the Nth terminal of a session gets the Nth name, so the mapping is
# reproducible across sessions and the user builds a habit ("Mika is always the
# first pane").
NAME_POOL: tuple[str, ...] = (
    "Mika",
    "Nova",
    "Aria",
    "Kai",
    "Luna",
    "Theo",
    "Iris",
    "Bruno",
    "Vega",
    "Juno",
    "Milo",
    "Zara",
)

# Below this similarity a spoken word is NOT treated as a terminal name. Tuned
# so that a garbled "Mika" ("Micah", "Meeka", "Mikka") still lands while an
# unrelated word ("Wiki", "Marker") does not.
_MATCH_FLOOR = 0.72


def default_names(count: int) -> list[str]:
    """First ``count`` call-signs, extended with numbered fallbacks if needed."""
    if count <= 0:
        return []
    names = list(NAME_POOL[:count])
    # More terminals than pool entries: keep going deterministically rather than
    # refusing a large grid.
    index = 2
    while len(names) < count:
        for base in NAME_POOL:
            if len(names) >= count:
                break
            names.append(f"{base}-{index}")
        index += 1
    return names


def normalize(name: str) -> str:
    """Lowercase, whitespace-stripped comparison key for a call-sign."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


# Spelling variants that sound identical but wreck a character-level comparison.
# Measured need: "Mika" comes back from speech recognition as "Micah", "Meeka",
# "Mikka" — all of which score BELOW a plain similarity floor tight enough to
# reject unrelated words. Folding the spelling first fixes both ends at once:
# real garble lands, unrelated words still do not.
_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("sch", "s"),
    ("ph", "f"),
    ("ck", "k"),
    ("th", "t"),
    ("qu", "k"),
    ("ai", "ei"),
    ("ay", "ei"),
    ("ey", "ei"),
)
_LETTERS = str.maketrans({"c": "k", "z": "s", "y": "i", "v": "f", "w": "f", "j": "i"})


def phonetic_key(name: str) -> str:
    """Spelling-insensitive key: same sound in, same string out.

    Not a full Soundex — that collapses too far for four-letter call-signs
    ("Kai" and "Kia" must still differ). This only folds the substitutions that
    actually show up in speech transcripts, then squeezes doubled letters and
    silent h.
    """
    key = normalize(name)
    for src, dst in _DIGRAPHS:
        key = key.replace(src, dst)
    key = key.translate(_LETTERS)
    # Silent h anywhere but the first position ("Micah" -> "mika").
    key = key[:1] + key[1:].replace("h", "")
    squeezed: list[str] = []
    for ch in key:
        if not squeezed or squeezed[-1] != ch:
            squeezed.append(ch)
    return "".join(squeezed)


def resolve(spoken: str, candidates: list[str]) -> str | None:
    """Best-matching call-sign for ``spoken``, or ``None`` below the floor.

    ``spoken`` may be a whole utterance ("what is mika up to?") — every word is
    tried, longest first, so a name embedded in a sentence is found without the
    caller having to pre-extract it.
    """
    if not spoken or not candidates:
        return None

    keys = {normalize(c): c for c in candidates}

    # Exact hit on the full string first (the API path case: /terminals/mika).
    full = normalize(spoken)
    if full in keys:
        return keys[full]

    words = [w for w in (normalize(w) for w in spoken.split()) if w]
    best: tuple[float, str] | None = None
    for word in words:
        if word in keys:
            return keys[word]
        folded = phonetic_key(word)
        for key, original in keys.items():
            if folded and folded == phonetic_key(key):
                return original
            # Score the raw spelling AND the folded one; the better of the two
            # decides, so an odd transcript has two chances to be recognised.
            score = max(
                SequenceMatcher(None, word, key).ratio(),
                SequenceMatcher(None, folded, phonetic_key(key)).ratio(),
            )
            if score >= _MATCH_FLOOR and (best is None or score > best[0]):
                best = (score, original)
    return best[1] if best else None


__all__ = ["NAME_POOL", "default_names", "normalize", "phonetic_key", "resolve"]
