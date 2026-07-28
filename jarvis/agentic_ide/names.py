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

#: Coding products a pane may never be named after, whether or not this build
#: can run them. Saying "Claude" to address a pane called Claude is a coin flip,
#: and the same holds for a product the user merely has installed — so the list
#: is deliberately WIDER than the registry: a name retired from the registry
#: tomorrow is still a name people say out loud today.
_KNOWN_PRODUCTS: frozenset[str] = frozenset(
    {"jarvis", "claude", "codex", "gemini", "copilot", "cursor", "aider"}
)


def _reserved() -> frozenset[str]:
    """Every name a call-sign must stay clear of, registry included.

    Built at call time rather than frozen at import so a CLI registered later
    cannot end up sharing its name with a pane — which is the one collision this
    whole module is about, and the one nobody would notice until an instruction
    went to the wrong place.
    """
    try:
        from jarvis.workspace import agents as workspace_agents

        return _KNOWN_PRODUCTS | workspace_agents.reserved_call_signs()
    except Exception:  # noqa: BLE001 - naming must never fail on this
        return _KNOWN_PRODUCTS


#: The fixed part of that list, kept as a module constant for existing readers.
#: The user's OWN wake word is configurable and cannot be checked here — the
#: session layer keeps a call-sign from shadowing it at assignment time.
RESERVED_NAMES: frozenset[str] = _reserved()

# Below this similarity a spoken word is NOT treated as a terminal name. Tuned
# so that a garbled "Mika" ("Micah", "Meeka", "Mikka") still lands while an
# unrelated word ("Wiki", "Marker") does not.
_MATCH_FLOOR = 0.72

# The NEAR-MISS band: close enough that a human would ask "did you mean Ellis?",
# too far to act on. Its whole reason to exist is the live 2026-07-27 failure —
# a pane called "Ellis" came back from speech recognition as "Ilies" (0.667,
# just under the floor), so the addressed-terminal path stood down in silence
# and the user was told an agent was working when none was.
#
# The floor of the band is NOT what makes acting on it safe: ordinary words of
# the spoken language reach well into it ("wieso" scores 0.500 against "Finn",
# "kannst" 0.600 against "Casey"), and no threshold separates those from a
# garbled call-sign. Safety comes from the CONTEXT gate in ``clarify.py`` — a
# near miss may only ever produce a QUESTION, and only in a turn that is
# addressing the workspace at all. The band merely keeps that question rare.
#
# Set low enough to reach the maintainer's own example (2026-07-27): a pane
# called "Maggie" heard as "Max" scores 0.571, and asking is exactly what
# should happen there.
_NEAR_MISS_FLOOR = 0.55


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


def similarity(spoken: str, name: str) -> float:
    """How close ONE spoken word is to ONE call-sign, on the resolver's scale.

    The exact score ``resolve`` ranks by, exposed so the near-miss path cannot
    drift into scoring differently from the path that acts.
    """
    word = normalize(spoken)
    key = normalize(name)
    if not word or not key:
        return 0.0
    return max(
        SequenceMatcher(None, word, key).ratio(),
        SequenceMatcher(None, phonetic_key(word), phonetic_key(key)).ratio(),
    )


def near_miss(
    spoken: str, candidates: list[str], *, limit: int = 3
) -> tuple[tuple[str, float], ...]:
    """Call-signs ``spoken`` ALMOST names, best first, or ``()``.

    The line is drawn at CERTAINTY, not at a score: a word that is the name or
    folds to the same sound ("Elis" → "Ellis") is certain and returns ``()`` —
    the acting path owns it. Everything else that still scores close is
    uncertain and belongs in a question, however high it scores.

    That boundary matters more than it looks. A merely-similar word used to sit
    in a blind spot between the two paths: "Dena" scores 0.75 against "Dana", so
    the resolver called it a match and the near-miss check stood down — while
    the addressing templates, which need the exact spelling, found nothing at
    all. The turn produced neither an action nor a question. Anchoring both
    sides on the same notion of certainty is what closes that gap.

    Several candidates are returned on purpose. A transcript that sits between
    two panes ("Mags" between "Max" and "Maggie") is exactly the case where
    picking the best score silently is how the wrong agent gets the work; the
    caller asks instead, and it can only ask if it knows both.
    """
    if not spoken or not candidates:
        return ()
    if resolve(spoken, candidates, fuzzy=False) is not None:
        return ()
    scored = sorted(
        (
            (name, score)
            for name in candidates
            if (score := similarity(spoken, name)) >= _NEAR_MISS_FLOOR
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return tuple(scored[:limit])


__all__ = [
    "NAME_POOL",
    "RESERVED_NAMES",
    "default_names",
    "near_miss",
    "normalize",
    "phonetic_key",
    "resolve",
    "similarity",
]
