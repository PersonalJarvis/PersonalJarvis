"""Speakable call-signs for Agentic-IDE terminals.

Why terminals get names instead of numbers: the whole point of the Agentic IDE
is that the user can ASK about a running agent by voice — "what is Mika doing?"
— and a spoken ordinal ("terminal three") is both awkward to say and easy for
speech recognition to mangle into a different number. A short, phonetically
distinct proper name survives an imperfect transcript far better, and a wrong
match is recoverable (the user hears the name back in the answer).

Selection criteria for the pool:
- **plain English given names** (maintainer directive 2026-07-26), so anyone
  can say them — the pool is what an international user base reads and speaks
  aloud, and a name that is obvious in one language and a guess in the next
  costs a mis-addressed agent,
- two syllables or fewer, no consonant clusters that ASR routinely drops,
- pairwise phonetically distinct — enforced, not eyeballed: at 70+ names no
  human spots every confusable pair, so
  ``tests/unit/agentic_ide/test_names.py`` runs the SHIPPING resolver over
  every pair and fails on any that could steal each other's instructions,
- no collision with the wake word or with the coding agents' own names.

The pool is long on purpose. A workspace may hold as many panes as the machine
can stand, and running out of names would fall back to "Alex-2" — a call-sign
nobody can say naturally, which defeats the entire point of naming panes.

Name resolution is deliberately fuzzy (``resolve``): the caller is matching a
name a human just spoke through an imperfect transcript. This is NOT the wake
word — AP-27 does not apply, because a mismatch here costs one clarifying
question, not a deaf assistant. The floor is still high enough that room noise
does not silently address a random terminal.
"""
from __future__ import annotations

from difflib import SequenceMatcher

# Ordered: the Nth terminal of a session gets the Nth name, so the mapping is
# reproducible across sessions and the user builds a habit ("Alex is always the
# first pane"). The waves are a readability grouping only — the clearest,
# most everyday names sit first because those are the panes people see most.
NAME_POOL: tuple[str, ...] = (
    # Wave 1 — the panes almost everyone will actually have.
    "Alex", "Blake", "Casey", "Dana", "Ellis", "Finn", "Grace", "Hunter",
    "Ivy", "Jasper", "Kate", "Logan", "Maya", "Noah", "Oscar", "Quinn",
    "Ruby", "Sage", "Tessa", "Vera", "Wyatt", "Zoe",
    # Wave 2 — still short, still plain English.
    "Aaron", "Bailey", "Clara", "Drew", "Hazel", "Isaac", "Jade", "Jordan",
    "Keira", "Lucas", "Mason", "Nina", "Olive", "Parker", "Reese", "Tyler",
    "Violet", "Brooke", "Cole", "Grant", "Heidi", "Jules",
    # Wave 3 — the long tail, for workspaces nobody will realistically fill.
    "Aubrey", "Cody", "Emery", "Felix", "Gemma", "Harvey", "Ivan",
    "Lila", "Marlow", "Owen", "Preston", "Rowan", "Sawyer", "Tobias",
    "Vaughn", "Willow", "Yara", "Leah", "Naomi", "Otis", "Pearl",
    "Simone", "Trent", "Hugo", "Nadia", "Rosa", "Theo", "Colby", "Marcus",
)

#: Names a pane may never carry: the fixed wake word and the coding agents
#: themselves. Saying "Claude" to address a pane called Claude is a coin flip.
#: The user's OWN wake word is configurable and cannot be checked here — the
#: session layer keeps a call-sign from shadowing it at assignment time.
RESERVED_NAMES: frozenset[str] = frozenset(
    {"jarvis", "claude", "codex", "gemini", "copilot", "cursor"}
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


def resolve(
    spoken: str, candidates: list[str], *, fuzzy: bool = True
) -> str | None:
    """Best-matching call-sign for ``spoken``, or ``None`` below the floor.

    ``spoken`` may be a whole utterance ("what is mika up to?") — every word is
    tried, longest first, so a name embedded in a sentence is found without the
    caller having to pre-extract it.

    Two strengths of match, and callers pick by what a wrong answer costs them:

    * **exact** — the word IS the name, or folds to the same sound ("Micah" →
      "Mika"). Certain enough to act on with no other evidence.
    * **fuzzy** (``fuzzy=True``, the default) — the word merely SCORES close
      enough. That is what rescues a transcript the phonetic folding does not
      cover, and it is also how ordinary speech collides with the pool: measured
      against the shipping names, "unten" reaches "Hunter" and "dann" reaches
      "Dana"; the live 2026-07-26 session had "keine" reaching "Kai"  # i18n-allow: quoted transcript tokens
      (everyday words of the spoken language, quoted as measurement data).
      Below the floor those are indistinguishable from a garbled call-sign, so
      a caller that would ACT on the answer alone passes ``fuzzy=False`` and
      accepts an exact match only.
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
            if not fuzzy:
                continue
            # Score the raw spelling AND the folded one; the better of the two
            # decides, so an odd transcript has two chances to be recognised.
            score = max(
                SequenceMatcher(None, word, key).ratio(),
                SequenceMatcher(None, folded, phonetic_key(key)).ratio(),
            )
            if score >= _MATCH_FLOOR and (best is None or score > best[0]):
                best = (score, original)
    return best[1] if best else None


__all__ = [
    "NAME_POOL",
    "RESERVED_NAMES",
    "default_names",
    "normalize",
    "phonetic_key",
    "resolve",
]
