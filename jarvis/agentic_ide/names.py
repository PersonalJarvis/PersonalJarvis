"""Speakable call-signs for Agentic-IDE terminals.

A pane's call-sign is its POSITION: the first terminal is ``T1``, the second
``T2``, and so on — "T" for terminal, the number for where it sits in the grid,
read left to right and top to bottom. Nothing has to be learned or remembered,
which is the whole point (maintainer directive 2026-07-28): the user looks at
the screen, sees which pane they mean, and says its number.

**Why this replaced a pool of given names.** Panes used to be called "Alex",
"Blake", "Casey" … — chosen because a phonetically distinct proper name
survives an imperfect transcript better than a spoken ordinal. That held, but
it bought robustness with two costs the position scheme simply does not have:

1. **the user had to LEARN the roster.** Addressing a pane meant first reading
   its header, then saying a name that means nothing — and getting it wrong
   sent work to a different agent;
2. **given names collide with ordinary speech.** Measured against the old pool,
   "allen" scores 0.750 against "Alex", "unten" reaches "Hunter", "keine"  <!-- i18n-allow: quoted transcript tokens as measurement data -->
   reaches "Kai" — all above or near the acting threshold. Half of ``clarify.py`` (stop-word tables,
   capitalization heuristics, surname detection) exists to tell a spoken pane
   name apart from the sentence around it. A call-sign of the shape ``T3``
   collides with nothing in German, English or Spanish.

The cost this scheme pays instead is that a bare number IS fragile in a
transcript — so a number alone never addresses a pane. What counts is a
number carrying a TERMINAL MARKER: the letter "T" glued to it (``T3``, "tee
three"), the noun ("terminal three", "pane 3"), or an ordinal in front of the
noun ("the third terminal"). ``spoken_positions`` owns that list, and it is
deliberately the only way a position enters the system. See AP-27 for the
neighbouring doctrine — the reasoning is the same shape, but the stakes are
lower here: a mismatch costs one clarifying question, not a deaf assistant.

Custom call-signs still work. ``POST /terminals`` accepts a ``name``, so a
pane can carry any label its owner gives it, and the fuzzy/phonetic matching
below is what keeps THOSE speakable. Positional call-signs never take part in
fuzzy matching: "T1" and "T11" score 0.80 against each other, which is above
the acting floor, so a fuzzy hit between two positions would be a coin flip
over which agent gets the work. Positions match exactly or not at all.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

#: The letter a positional call-sign starts with. One place, because the
#: assignment side, the resolver and the spoken forms all have to agree.
CALL_SIGN_PREFIX = "T"

#: A written positional call-sign, whole-string: "T1", "t12".
_POSITION_NAME_RE = re.compile(rf"^{CALL_SIGN_PREFIX}(\d{{1,3}})$", re.IGNORECASE)

#: Coding products a pane may never be named after, kept for callers that ask
#: what a CUSTOM name must stay clear of. Saying "Claude" to address a pane
#: called Claude is a coin flip, and the same holds for a product the user
#: merely has installed — so the list is deliberately WIDER than the registry.
#: Positional call-signs cannot collide with any of them.
_KNOWN_PRODUCTS: frozenset[str] = frozenset(
    {"jarvis", "claude", "codex", "gemini", "copilot", "cursor", "aider"}
)


def _reserved() -> frozenset[str]:
    """Every name a CUSTOM call-sign must stay clear of, registry included.

    Built at call time rather than frozen at import so a CLI registered later
    cannot end up sharing its name with a pane — which is the one collision
    this whole module is about, and the one nobody would notice until an
    instruction went to the wrong place.
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

# Below this similarity a spoken word is NOT treated as a CUSTOM call-sign.
# Tuned so that a garbled "Mika" ("Micah", "Meeka", "Mikka") still lands while
# an unrelated word ("Wiki", "Marker") does not. Positional call-signs never
# reach this path.
_MATCH_FLOOR = 0.72

# The NEAR-MISS band: close enough that a human would ask "did you mean Mika?",
# too far to act on. Its whole reason to exist is the live 2026-07-27 failure —
# a pane called "Ellis" came back from speech recognition as "Ilies" (0.667,
# just under the floor), so the addressed-terminal path stood down in silence
# and the user was told an agent was working when none was.
#
# The floor of the band is NOT what makes acting on it safe: ordinary words of
# the spoken language reach well into it, and no threshold separates those from
# a garbled call-sign. Safety comes from the CONTEXT gate in ``clarify.py`` — a
# near miss may only ever produce a QUESTION, and only in a turn that is
# addressing the workspace at all. The band merely keeps that question rare.
_NEAR_MISS_FLOOR = 0.55


# --------------------------------------------------------------------------- #
# Positional call-signs                                                       #
# --------------------------------------------------------------------------- #

def position_name(number: int) -> str:
    """The call-sign of the ``number``-th pane, counting from 1 ("T3")."""
    return f"{CALL_SIGN_PREFIX}{int(number)}"


def position_of(name: str) -> int | None:
    """The position a call-sign encodes, or ``None`` for a custom name.

    The one place that reads a call-sign back as a number. Callers use it to
    tell the two kinds of name apart, because they are matched by different
    rules: a position is exact-only, a custom name is fuzzy.
    """
    match = _POSITION_NAME_RE.match(str(name or "").strip())
    if match is None:
        return None
    number = int(match.group(1))
    return number if number >= 1 else None


def is_position_name(name: str) -> bool:
    """Whether ``name`` is a positional call-sign rather than a custom one."""
    return position_of(name) is not None


def default_names(count: int) -> list[str]:
    """The first ``count`` call-signs: ``["T1", "T2", …]``."""
    if count <= 0:
        return []
    return [position_name(index) for index in range(1, count + 1)]


def free_positions(taken: Iterable[str], count: int) -> list[str]:
    """The next ``count`` positional call-signs not in ``taken``, lowest first.

    Filling the LOWEST gap rather than counting on from the highest is what
    keeps the numbers matching the screen. Close the middle pane of three and
    the workspace shows T1 and T3; the next one opened becomes T2 again rather
    than T4, so the user never has to remember that a number was skipped.

    A number is never REASSIGNED while its pane is alive, though. Renumbering
    the survivors after a close would be the tidier grid and the worse
    contract: an instruction spoken one second before a pane closes would land
    in a different agent than the one the user was looking at when they said
    it. Names identify; positions are how they are handed out.
    """
    used = {position_of(name) for name in taken}
    used.discard(None)
    out: list[str] = []
    number = 1
    while len(out) < max(0, count):
        if number not in used:
            out.append(position_name(number))
            used.add(number)
        number += 1
    return out


# Number words a person actually speaks for a pane. Capped where speech stops
# being the way anybody addresses a pane: past twenty nobody says "terminal
# twenty-three" — they say the digits, which the digit form below covers up to
# MAX_TERMINALS. One table per language, so a locale can be extended without
# touching the others.
#
# The indefinite article is deliberately NOT a number word, in any locale:
# an utterance like "let the terminal run for a while" is ordinary speech, and
# reading its article as the digit would turn it into an order for pane one.
_NUMBER_WORDS: dict[str, int] = {
    # German
    "eins": 1, "zwei": 2, "zwo": 2, "drei": 3,  # i18n-allow: input vocabulary
    "vier": 4, "fuenf": 5, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,  # i18n-allow: input vocabulary
    "neun": 9, "zehn": 10, "elf": 11, "zwoelf": 12, "zwölf": 12,  # i18n-allow: input vocabulary
    "dreizehn": 13, "vierzehn": 14, "fuenfzehn": 15, "fünfzehn": 15,  # i18n-allow: input vocabulary
    "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,  # i18n-allow: input vocabulary
    "zwanzig": 20,  # i18n-allow: input vocabulary
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    # Spanish
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20,
}

# Ordinals, for "the third terminal". Spelled out in FULL rather than as stems
# with an optional ending, because several German ordinal stems are also the
# plain number (the stem of "eighth" is the word "eight"): a stem match read a
# request to OPEN eight panes as an instruction aimed at pane number eight. The
# ending is what tells the two apart, so it is mandatory wherever the language
# has one.
_ORDINAL_STEMS_DE: dict[str, int] = {
    "erst": 1, "zweit": 2, "dritt": 3, "viert": 4, "fuenft": 5, "fünft": 5,  # i18n-allow: input vocabulary
    "sechst": 6, "siebt": 7, "siebent": 7, "acht": 8, "neunt": 9, "zehnt": 10,  # i18n-allow: input vocabulary
    "elft": 11, "zwoelft": 12, "zwölft": 12,  # i18n-allow: input vocabulary
}
_ORDINAL_ENDINGS_DE: tuple[str, ...] = ("e", "en", "er", "es", "em")

_ORDINAL_PLAIN: dict[str, int] = {
    # i18n-allow: speech-recognition input vocabulary, not prose
    # English
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12,
    # Spanish, in every gendered and apocopated form people actually say
    "primer": 1, "primero": 1, "primera": 1,
    "segundo": 2, "segunda": 2,
    "tercer": 3, "tercero": 3, "tercera": 3,
    "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5,
    "sexto": 6, "sexta": 6, "septimo": 7, "séptimo": 7, "septima": 7,
    "séptima": 7, "octavo": 8, "octava": 8, "noveno": 9, "novena": 9,
    "decimo": 10, "décimo": 10, "decima": 10, "décima": 10,
}

_ORDINAL_WORDS: dict[str, int] = {
    **_ORDINAL_PLAIN,
    **{
        f"{stem}{ending}": value
        for stem, value in _ORDINAL_STEMS_DE.items()
        for ending in _ORDINAL_ENDINGS_DE
    },
}

#: Words meaning "the one at the end", resolved against the live pane count.
_LAST_WORDS: frozenset[str] = frozenset(
    # i18n-allow: speech-recognition input vocabulary, not prose
    {"letzte", "letzten", "letztes", "letzter", "last", "ultimo", "último",
     "ultima", "última"}
)

#: How a person says the noun. Matches the German compound too ("Terminalfenster")
#: without needing a second pattern.
_PANE_NOUN = r"(?:terminals?|terminales|panes?|tabs?|fenster)"

#: An article in front of the noun or the ordinal, all three locales.
_ARTICLE = r"(?:der|die|das|den|dem|des|the|el|la|los|las)"  # i18n-allow: input vocabulary

_NUMBER_WORD_ALT = "|".join(
    sorted((re.escape(w) for w in _NUMBER_WORDS), key=len, reverse=True)
)
_ORDINAL_ALT = "|".join(
    sorted((re.escape(w) for w in _ORDINAL_WORDS), key=len, reverse=True)
)
_LAST_ALT = "|".join(sorted((re.escape(w) for w in _LAST_WORDS), key=len, reverse=True))

#: Every shape that names a pane BY POSITION. Each one carries a terminal
#: marker — the letter, the noun, or an ordinal bound to the noun — because a
#: bare number is ordinary speech ("fix the two failing tests") and must never
#: address an agent.
#:
#: Order matters: the longest, most explicit shapes are tried first, so
#: "terminal number two" is read once as position 2 rather than twice.
_POSITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "terminal 2" / "Terminal Nummer zwei" / "pane number two" / "tab dos".
    # ONE t-led consonant cluster may sit between the noun and the number: it
    # is what is left of the spoken call-sign letter when speech recognition
    # mangles it ("Terminal T2" arrived as "terminal tft zwei" live on
    # 2026-08-12, and with the letter lost the pane's NAME was re-read one
    # layer up as a fleet size — two fresh panes opened for a brief aimed at
    # T2). Consonants only, so a real word ("Typ", "ten", "the") that happens
    # to start with t never smuggles a number into a position.
    #
    # The cluster may also sit GLUED to the number, which is the same garble
    # one space tighter: "Terminal T1" came back as "Terminal TR1" live on
    # 2026-08-13, the repair above needed the space it did not have, and the
    # turn fell through to the spawn path — a fresh Codex pane opened and was
    # briefed while T1 sat idle. Requiring the space was the whole bug; the
    # consonant class is what keeps the branch honest either way, and a
    # number word behind the cluster still wins by backtracking ("terminal
    # two" is two, never a "tw" cluster in front of a number that is not there).
    re.compile(
        rf"\b{_PANE_NOUN}\s*"
        # i18n-allow: speech-recognition input vocabulary, not prose
        rf"(?:(?:nummer|nr\.?|number|n[uú]mero)\s*)?"
        rf"(?:t[bcdfghjklmnpqrstvwxz]{{0,3}}\s*)?"
        rf"(?P<value>\d{{1,3}}|{_NUMBER_WORD_ALT})\b",
        re.IGNORECASE,
    ),
    # "das dritte Terminal" / "the third pane" / "el tercer terminal".
    # The ordinal is matched as a WHOLE word: allowing it to run on ("\w*")
    # let "erstelle Terminal 3" — create a terminal — parse as the ordinal
    # "erste" plus a suffix, and address pane one instead.
    re.compile(
        rf"\b(?:{_ARTICLE}\s+)?(?P<ordinal>{_ORDINAL_ALT})\s+{_PANE_NOUN}\b",
        re.IGNORECASE,
    ),
    # "das letzte Terminal" / "the last one"
    re.compile(
        rf"\b(?:{_ARTICLE}\s+)?(?P<last>{_LAST_ALT})\s+{_PANE_NOUN}\b",
        re.IGNORECASE,
    ),
    # "T3", "t 3", "T-3", "T.3" — the written form, and what speech recognition
    # produces most of the time when the letter is said crisply.
    re.compile(
        rf"\b{CALL_SIGN_PREFIX}\s*[-.–]?\s*(?P<value>\d{{1,3}})\b",
        re.IGNORECASE,
    ),
    # "tee three" / "te eins" / "ti dos" — the letter transcribed as a word,
    # which is what happens when it is said together with the number. The
    # spellings are the ones speech recognition actually produces; each is a
    # non-word in all three locales, so a number word behind one is never
    # ordinary speech. The BARE letter is deliberately absent: "t zwei" is
    # also how a dictated variable or time index sounds ("t eins kleiner als
    # t zwei"), and with no pane noun nearby nothing vouches for the pane
    # reading. Behind the noun the same debris IS admitted — that is the
    # cluster branch of the first pattern.
    re.compile(
        # i18n-allow: speech-recognition input vocabulary, not prose
        rf"\b(?:tee|te|ti|tea|tey)\s*[-.]?\s*(?P<value>\d{{1,3}}|{_NUMBER_WORD_ALT})\b",
        re.IGNORECASE,
    ),
)


def _value_of(match: re.Match[str], *, count: int) -> int | None:
    """The position one ``_POSITION_PATTERNS`` hit stands for, or ``None``."""
    groups = match.groupdict()
    raw = groups.get("value")
    if raw:
        if raw.isdigit():
            return int(raw)
        return _NUMBER_WORDS.get(raw.casefold())
    ordinal = groups.get("ordinal")
    if ordinal:
        return _ORDINAL_WORDS.get(ordinal.casefold())
    if groups.get("last"):
        return count if count > 0 else None
    return None


def spoken_positions(text: str, *, count: int = 0) -> list[tuple[int, int, int]]:
    """Every pane position ``text`` names, as ``(start, end, number)`` in order.

    The ONE place that decides what counts as naming a pane by position. Every
    caller goes through it, so the resolver, the addressed-terminal detector
    and the clarify path cannot drift into three different opinions about
    whether "terminal two" addressed a pane.

    ``count`` is how many panes exist, needed only by "the last one". Zero
    means the caller does not know, and that shape is then skipped rather than
    guessed at.

    Overlapping hits are dropped, longest match first: "terminal number three"
    is one position, not a noun-hit and a stray digit.
    """
    body = str(text or "")
    if not body:
        return []
    found: list[tuple[int, int, int]] = []
    taken: list[tuple[int, int]] = []
    for pattern in _POSITION_PATTERNS:
        for match in pattern.finditer(body):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            number = _value_of(match, count=count)
            if number is None or number < 1:
                continue
            taken.append((start, end))
            found.append((start, end, number))
    found.sort(key=lambda item: item[0])
    return found


#: One WORD that could be a call-sign — the unit every caller scores against
#: the live roster. Letters, optionally followed by digits, so "T1" arrives
#: whole. A plain word tokenizer (``[^\W\d_]+``) cut it to "T", which scores
#: 0.667 against "t1" — below the floor — so a positional call-sign written
#: exactly the way the pane header spells it addressed nothing at all.
#:
#: Digits alone are deliberately NOT a token: a bare number is ordinary speech
#: ("fix the 3 failing tests") and only ever names a pane when it carries a
#: terminal marker, which is ``spoken_positions``' business, not this one's.
CALL_SIGN_WORD_RE = re.compile(r"[^\W\d_]+\d*", re.UNICODE)


def canonical_positions(text: str, candidates: list[str]) -> str:
    """``text`` with every spoken position rewritten as its call-sign.

    "prompte Terminal eins" becomes "prompte T1", so the addressing templates —
    which are built from the exact call-sign — inherit every spoken form for
    free instead of each one having to enumerate "terminal one", "tee one",
    "the first terminal" for itself.

    Only positions the workspace ACTUALLY HAS are rewritten. A number nobody
    opened stays as the user said it, which is what lets the caller answer "no
    such terminal" honestly rather than silently addressing a neighbour.
    """
    body = str(text or "")
    if not body or not candidates:
        return body
    positions = {position_of(c): c for c in candidates if position_of(c) is not None}
    if not positions:
        return body
    hits = spoken_positions(body, count=len(candidates))
    if not hits:
        return body
    # Right to left, so an earlier hit's span is still valid after a later one
    # has been replaced with a shorter string.
    for start, end, number in reversed(hits):
        name = positions.get(number)
        if name is None:
            continue
        body = f"{body[:start]}{name}{body[end:]}"
    return body


def normalize(name: str) -> str:
    """Lowercase, whitespace-stripped comparison key for a call-sign."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


# Spelling variants that sound identical but wreck a character-level comparison
# of a CUSTOM call-sign. Measured need: "Mika" comes back from speech
# recognition as "Micah", "Meeka", "Mikka" — all of which score BELOW a plain
# similarity floor tight enough to reject unrelated words. Folding the spelling
# first fixes both ends at once: real garble lands, unrelated words still do not.
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
    """Best-matching call-sign for ``spoken``, or ``None`` when nothing fits.

    ``spoken`` may be a whole utterance ("what is terminal three up to?") — the
    positional forms are located inside the sentence and every word is tried
    against the custom names, so the caller does not have to pre-extract
    anything.

    Two kinds of call-sign, matched by two different rules:

    * **positional** ("T3") — EXACT only, whether written that way or spoken as
      one of the forms ``spoken_positions`` knows. Never fuzzy: "T1" and "T11"
      score 0.80 against each other, well above the acting floor, so a fuzzy
      hit between two positions would be a coin flip over which agent gets the
      work.
    * **custom** (a name the user gave the pane) — exact, or folded to the same
      sound ("Micah" → "Mika"), or merely CLOSE when ``fuzzy=True``. That last
      band is what rescues a transcript the phonetic folding does not cover,
      and it is also how ordinary speech collides with a name: measured against
      real call-signs, "unten" reaches "Hunter" and "dann" reaches "Dana". A
      caller that would ACT on the answer alone passes ``fuzzy=False``.
    """
    if not spoken or not candidates:
        return None

    keys = {normalize(c): c for c in candidates}

    # Exact hit on the full string first (the API path case: /terminals/t3).
    full = normalize(spoken)
    if full in keys:
        return keys[full]

    # A position named anywhere in the utterance, in any of its spoken shapes.
    positions = {position_of(c): c for c in candidates if position_of(c) is not None}
    if positions:
        for _start, _end, number in spoken_positions(spoken, count=len(candidates)):
            hit = positions.get(number)
            if hit is not None:
                return hit

    # Custom names: word by word, phonetically folded, fuzzy only if allowed.
    custom = {key: name for key, name in keys.items() if position_of(name) is None}
    if not custom:
        return None
    words = [w for w in (normalize(w) for w in spoken.split()) if w]
    best: tuple[float, str] | None = None
    for word in words:
        if word in custom:
            return custom[word]
        folded = phonetic_key(word)
        for key, original in custom.items():
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

    **Positional call-signs never produce a near miss.** "T2" is either said or
    not; the shapes that name it are explicit, and the ones that come close
    ("T1" vs "T11") are two real panes rather than one garbled word — asking
    "did you mean T11?" about a clean "T1" would invent doubt where there is
    none. A position the workspace does not have (``"T7"`` with four panes
    open) is a wrong number, not an unclear one, and the caller answers it with
    the honest "no such terminal" that names the ones that do exist.
    """
    if not spoken or not candidates:
        return ()
    if resolve(spoken, candidates, fuzzy=False) is not None:
        return ()
    scored = sorted(
        (
            (name, score)
            for name in candidates
            if position_of(name) is None
            and (score := similarity(spoken, name)) >= _NEAR_MISS_FLOOR
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return tuple(scored[:limit])


__all__ = [
    "CALL_SIGN_PREFIX",
    "CALL_SIGN_WORD_RE",
    "RESERVED_NAMES",
    "canonical_positions",
    "default_names",
    "free_positions",
    "is_position_name",
    "near_miss",
    "normalize",
    "phonetic_key",
    "position_name",
    "position_of",
    "resolve",
    "similarity",
    "spoken_positions",
]
