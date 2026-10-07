"""Resolve a spoken or transcribed name to one of a known set of names.

Speech recognition rarely returns a name the way it is spelled: "Jarvis Code"
comes back as "Jarvis Kot", "Jarwis Code", "Davis Code" or "Jarvis Coder",
and "Jarvis Scout" as "Java Scout" (live 2026-10-02 — the hand-off silently
failed because every lookup compared strings exactly). This module is the one
place that turns such a reference into a ranked, scored decision:

1. **Normalization** — case, umlauts, accents, hyphens and punctuation are
   folded, and filler words a person wraps around a name ("the", "agent",
   "unseren") are dropped.
2. **Exact and alias** — the folded reference equals a name, an id, or one of
   the candidate's aliases.
3. **Fuzzy** — Jaro-Winkler similarity, word by word, so one garbled word does
   not sink a two-word name.
4. **Phonetic** — Cologne phonetics (Koelner Phonetik, built for German
   pronunciation) plus a spelling fold for the substitutions English and
   German transcripts produce (c/k, v/w/f, j/i ...): "Kot" and "Code" sound
   alike, so they match.
5. **Context** — when the surrounding request is clearly about programming, a
   candidate whose role is coding gets a small bonus. Context can tip a close
   call; it can never create a match on its own.

The result is a decision, not just a best guess: ``act`` (one clear winner),
``ask`` (close, or several close candidates — the caller asks "did you mean
...?"), or ``none`` (nothing close — the caller lists what exists). Every
resolution is logged with the raw text, winner, score and method so a future
miss can be traced from ``jarvis_desktop.log`` alone.

Pure Python, no dependencies, no I/O: safe on the voice path and on every OS.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

log = logging.getLogger(__name__)

Decision = Literal["act", "ask", "none"]

#: At or above this score, with a clear margin, the caller acts on the match.
ACT_SCORE: Final[float] = 0.90
#: At or above this score the caller asks "did you mean ...?" instead of giving up.
ASK_SCORE: Final[float] = 0.68
#: The winner must lead the runner-up by this much to be acted on; two
#: candidates closer than this are a question, never a coin flip.
ACT_MARGIN: Final[float] = 0.06
#: What a request that is clearly about programming adds to a coding candidate.
CONTEXT_BONUS: Final[float] = 0.04
#: Score of a word pair whose Cologne codes are equal but whose spelling is not.
_PHONETIC_EQUAL: Final[float] = 0.92
#: Weight of a word that names the product rather than one agent ("Jarvis").
_WEAK_WEIGHT: Final[float] = 0.35
#: A name found INSIDE a longer reference ("Java Scout and Quick Meshes")
#: scores this fraction of a reference that is only the name.
_WINDOW_PENALTY: Final[float] = 0.92
#: How many "did you mean" candidates a resolution carries.
_MAX_CANDIDATES: Final[int] = 3

#: Words people wrap around a name when they say it. They never identify one.
_FILLER: Final[frozenset[str]] = frozenset(
    # i18n-allow: speech-recognition input vocabulary, not prose
    {
        "agent", "agents", "agenten", "agentin", "bot", "bots",
        "the", "our", "my", "your", "a", "an", "to", "please",
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",  # i18n-allow
        "unser", "unsere", "unseren", "unserem", "unserer", "unseres",  # i18n-allow
        "mein", "meine", "meinen", "meinem", "meiner", "dein", "deine", "deinen",  # i18n-allow
        "bitte", "am",  # i18n-allow
        "el", "la", "los", "las", "mi", "mis", "nuestro", "nuestra", "por", "favor",
    }
)  # fmt: skip

#: Words that name the product, not an agent: "Jarvis Code" and "Jarvis Scout"
#: share "Jarvis", so it must not be what decides between them.
_WEAK: Final[frozenset[str]] = frozenset({"jarvis", "personal"})

#: Product names a user says on purpose. Speech recognition does not garble
#: an agent name INTO one of them, so a spoken product name only ever matches
#: itself: "Codex" must never land on an agent called "Jarvis Code".
_PRODUCTS: Final[frozenset[str]] = frozenset(
    {
        "claude", "codex", "gemini", "cursor", "copilot", "aider", "grok", "deepseek",
        "antigravity", "chatgpt", "openai", "gpt", "ollama",
    }
)  # fmt: skip
_PRODUCT_CAP: Final[float] = 0.5

#: A request is "about programming" when it carries one of these stems.
_CODING_CONTEXT = re.compile(
    # i18n-allow: speech-recognition input vocabulary, not prose
    r"\b(?:cod(?:e|en|ing|er|ex)|programm\w*|entwickl\w*|implement\w*|refactor\w*|"
    r"bug\w*|fix\w*|commit\w*|pull request|repo\w*|codebase|debug\w*|"
    r"developer|programar|c[oó]digo)\b",
    re.IGNORECASE,
)

#: A candidate's role is coding when its name, title or description says so.
_CODING_ROLE = re.compile(
    # i18n-allow: speech-recognition input vocabulary, not prose
    r"\b(?:code|coder|coding|programm\w*|entwickl\w*|developer|dev|engineer\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NameCandidate:
    """One thing a spoken name can resolve to.

    ``key`` is the stable identity (an agent id), ``label`` what a person sees.
    ``names`` are spellings that count as the name itself, ``aliases`` further
    ways to say it. ``roles`` are tags such as ``"coding"`` the context gate
    reads.
    """

    key: str
    label: str
    names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    roles: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NameMatch:
    key: str
    label: str
    score: float
    method: str  # exact | alias | fuzzy | phonetic
    form: str  # the name or alias that matched

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 3),
            "method": self.method,
            "matched": self.form,
        }


@dataclass(frozen=True)
class NameResolution:
    """What a spoken reference resolved to, and what the caller should do."""

    raw: str
    decision: Decision
    best: NameMatch | None
    candidates: tuple[NameMatch, ...] = field(default_factory=tuple)
    context_used: bool = False

    @property
    def key(self) -> str | None:
        return self.best.key if self.best is not None and self.decision == "act" else None

    def as_dict(self) -> dict[str, object]:
        return {
            "heard": self.raw,
            "decision": self.decision,
            "best": self.best.as_dict() if self.best else None,
            "candidates": [c.as_dict() for c in self.candidates],
            "context_used": self.context_used,
        }


# --------------------------------------------------------------------------- #
# Normalization                                                               #
# --------------------------------------------------------------------------- #

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})  # i18n-allow: folding


def normalize(text: str) -> str:
    """Lowercase ASCII words separated by single spaces.

    "Jarvis-Code!" and "jarvis  code" both become "jarvis code"; umlauts fold
    to their two-letter spelling, other accents are dropped.
    """
    folded = str(text or "").casefold().translate(_UMLAUTS)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def name_tokens(text: str) -> tuple[str, ...]:
    """The words of ``text`` that can identify a name (fillers dropped).

    A reference made only of fillers ("the agent") keeps its words, so it still
    compares as what it is rather than as nothing.
    """
    words = normalize(text).split()
    kept = [w for w in words if w not in _FILLER]
    return tuple(kept or words)


# --------------------------------------------------------------------------- #
# Similarity                                                                  #
# --------------------------------------------------------------------------- #


def jaro_winkler(a: str, b: str, *, prefix_scale: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1]; 1.0 means identical."""
    if a == b:
        return 1.0 if a else 0.0
    if not a or not b:
        return 0.0
    window = max(0, max(len(a), len(b)) // 2 - 1)
    a_hits = [False] * len(a)
    b_hits = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hits[j] and b[j] == ch:
                a_hits[i] = b_hits[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    a_seq = [ch for ch, hit in zip(a, a_hits, strict=True) if hit]
    b_seq = [ch for ch, hit in zip(b, b_hits, strict=True) if hit]
    transpositions = sum(x != y for x, y in zip(a_seq, b_seq, strict=True)) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def koelner_phonetik(word: str) -> str:
    """Cologne phonetics code of one word ("Jarvis" -> "0738", "Kot" -> "42").

    The German counterpart of Soundex: letters that sound alike in German share
    a digit, so a transcript that spells a name the way it sounds lands on the
    same code as the real spelling.
    """
    letters = re.sub(r"[^a-z]", "", normalize(word).replace(" ", ""))
    if not letters:
        return ""
    digits: list[str] = []
    for i, ch in enumerate(letters):
        prev = letters[i - 1] if i else ""
        nxt = letters[i + 1] if i + 1 < len(letters) else ""
        if ch in "aeijouy":
            code = "0"
        elif ch == "h":
            code = ""
        elif ch == "b":
            code = "1"
        elif ch == "p":
            code = "3" if nxt == "h" else "1"
        elif ch in "dt":
            code = "8" if nxt in ("c", "s", "z") else "2"
        elif ch in "fvw":
            code = "3"
        elif ch in "gkq":
            code = "4"
        elif ch == "c":
            if i == 0:
                code = "4" if nxt in tuple("ahkloqrux") else "8"
            elif prev in ("s", "z"):
                code = "8"
            else:
                code = "4" if nxt in tuple("ahkoqux") else "8"
        elif ch == "x":
            code = "8" if prev in ("c", "k", "q") else "48"
        elif ch == "l":
            code = "5"
        elif ch in "mn":
            code = "6"
        elif ch == "r":
            code = "7"
        elif ch in "sz":
            code = "8"
        else:  # digits never reach here; anything else carries no sound
            code = ""
        digits.append(code)
    raw = "".join(digits)
    squeezed = "".join(ch for i, ch in enumerate(raw) if i == 0 or ch != raw[i - 1])
    return squeezed[:1] + squeezed[1:].replace("0", "")


_FOLD_DIGRAPHS: Final[tuple[tuple[str, str], ...]] = (
    ("sch", "s"),
    ("ph", "f"),
    ("ck", "k"),
    ("th", "t"),
    ("qu", "k"),
    ("ai", "ei"),
    ("ay", "ei"),
    ("ey", "ei"),
)
_FOLD_LETTERS = str.maketrans({"c": "k", "z": "s", "y": "i", "v": "f", "w": "f", "j": "i"})


def spelling_fold(word: str) -> str:
    """Fold spellings that sound the same ("Jarwis" and "Jarvis" -> "iarfis").

    The substitutions transcripts actually make in English and German; it
    complements the Cologne code, which is coarser.
    """
    key = normalize(word).replace(" ", "")
    for src, dst in _FOLD_DIGRAPHS:
        key = key.replace(src, dst)
    key = key.translate(_FOLD_LETTERS)
    key = key[:1] + key[1:].replace("h", "")
    return "".join(ch for i, ch in enumerate(key) if i == 0 or ch != key[i - 1])


def word_similarity(spoken: str, name: str) -> tuple[float, str]:
    """How close one spoken word is to one name word, and by which method."""
    if spoken == name:
        return 1.0, "exact"
    if spoken in _PRODUCTS or name in _PRODUCTS:
        return min(_PRODUCT_CAP, jaro_winkler(spoken, name)), "fuzzy"
    fold_a, fold_b = spelling_fold(spoken), spelling_fold(name)
    if fold_a and fold_a == fold_b:
        return 0.97, "phonetic"  # "Jarwis" / "Jarvis": spelled apart, said alike
    fuzzy = max(jaro_winkler(spoken, name), jaro_winkler(fold_a, fold_b))
    phonetic = 0.0
    if min(len(spoken), len(name)) >= 3:
        code_a, code_b = koelner_phonetik(spoken), koelner_phonetik(name)
        # A two-digit code is shared by many short words ("Guide", "Gut" and
        # "Code" are all 42), so it only counts when the first sound agrees
        # too ("Kot" / "Code").
        same_onset = fold_a[:1] == fold_b[:1]
        if code_a == code_b and (len(code_a) >= 3 or (len(code_a) == 2 and same_onset)):
            phonetic = _PHONETIC_EQUAL
    if phonetic > fuzzy:
        return phonetic, "phonetic"
    return fuzzy, "fuzzy"


def _weight(word: str, frequency: dict[str, int]) -> float:
    """Rare words decide; the product name and words shared by many names do not."""
    base = _WEAK_WEIGHT if word in _WEAK else 1.0
    return base / (1.0 + math.log(max(1, frequency.get(word, 1))))


def _form_score(
    spoken: tuple[str, ...], form: tuple[str, ...], frequency: dict[str, int]
) -> tuple[float, str]:
    """Soft word-aligned F-score of a spoken reference against one name form.

    Precision asks "is every spoken word part of the name?", recall "is every
    name word in what was said?" — both weighted, so the product name or a word
    every agent carries cannot carry a match on its own.
    """
    if spoken == form:
        return 1.0, "exact"
    # One-to-one word alignment, best pairs first: a spoken word can stand
    # for ONE name word only, so "Claude Code" cannot use "code" twice to
    # look like "Jarvis Code".
    pairs = sorted(
        (
            (*word_similarity(a, b), i, j)
            for i, a in enumerate(spoken)
            for j, b in enumerate(form)
        ),
        key=lambda item: -item[0],
    )
    spoken_best = [0.0] * len(spoken)
    form_best = [0.0] * len(form)
    used_spoken: set[int] = set()
    used_form: set[int] = set()
    methods: list[str] = []
    for score, how, i, j in pairs:
        if i in used_spoken or j in used_form:
            continue
        used_spoken.add(i)
        used_form.add(j)
        spoken_best[i] = form_best[j] = score
        if score >= 0.8:
            methods.append(how)

    def side(words: tuple[str, ...], best: list[float]) -> float:
        weights = [_weight(word, frequency) for word in words]
        total = sum(w * b for w, b in zip(weights, best, strict=True))
        return total / sum(weights) if sum(weights) else 0.0

    precision = side(spoken, spoken_best)
    recall = side(form, form_best)
    score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # A name said as one glued word ("Jarviscode"), or a one-word name said as two.
    if (len(spoken) == 1) != (len(form) == 1):
        glued = jaro_winkler("".join(spoken), "".join(form))
        if glued * 0.95 > score:
            score, methods = glued * 0.95, ["fuzzy"]
    method = "phonetic" if "phonetic" in methods else "fuzzy"
    return score, method


def _reference_score(
    spoken: tuple[str, ...], form: tuple[str, ...], frequency: dict[str, int]
) -> tuple[float, str]:
    """``_form_score``, also trying the name as a phrase inside a longer reference."""
    score, method = _form_score(spoken, form, frequency)
    if score >= 1.0 or len(spoken) <= len(form):
        return score, method
    size = len(form)
    for start in range(len(spoken) - size + 1):
        window, how = _form_score(spoken[start : start + size], form, frequency)
        if window * _WINDOW_PENALTY > score:
            score, method = window * _WINDOW_PENALTY, ("fuzzy" if how == "exact" else how)
    return score, method


# --------------------------------------------------------------------------- #
# Resolution                                                                  #
# --------------------------------------------------------------------------- #


def is_coding_context(text: str) -> bool:
    """Whether a request is clearly about programming."""
    return bool(_CODING_CONTEXT.search(str(text or "")))


def has_coding_role(*texts: str) -> bool:
    """Whether a name, title or description marks a coding role."""
    return any(_CODING_ROLE.search(normalize(t)) for t in texts if t)


def resolve_name(
    spoken: str,
    candidates: Sequence[NameCandidate],
    *,
    context: str = "",
    surface: str = "",
) -> NameResolution:
    """Score ``spoken`` against every candidate and decide what to do.

    ``context`` is the rest of the request (the task text); it only ever adds
    ``CONTEXT_BONUS`` to a coding candidate in a coding request. ``surface``
    names the caller in the log line.
    """
    raw = str(spoken or "").strip()
    spoken_tokens = name_tokens(raw)
    if not raw or not spoken_tokens or not candidates:
        resolution = NameResolution(raw=raw, decision="none", best=None)
        _log(resolution, surface)
        return resolution

    forms: list[tuple[NameCandidate, str, tuple[str, ...], bool]] = []
    for cand in candidates:
        for text in cand.names:
            if tokens := name_tokens(text):
                forms.append((cand, text, tokens, False))
        for text in cand.aliases:
            if tokens := name_tokens(text):
                forms.append((cand, text, tokens, True))
    # How many candidates carry each word: shared words decide nothing.
    frequency: dict[str, int] = {}
    for cand in candidates:
        words = {w for _c, _t, toks, _a in forms if _c is cand for w in toks}
        for word in words:
            frequency[word] = frequency.get(word, 0) + 1

    coding = is_coding_context(context)
    per_candidate: dict[str, NameMatch] = {}
    for cand, text, tokens, is_alias in forms:
        score, method = _reference_score(spoken_tokens, tokens, frequency)
        if method == "exact":
            method = "alias" if is_alias else "exact"
        elif is_alias:
            # A loose match on an alias is weaker evidence than on the name.
            score *= 0.97
        if coding and "coding" in cand.roles and ASK_SCORE <= score < 1.0:
            score = min(0.99, score + CONTEXT_BONUS)
        current = per_candidate.get(cand.key)
        if current is None or score > current.score:
            per_candidate[cand.key] = NameMatch(cand.key, cand.label, score, method, text)

    ranked = sorted(per_candidate.values(), key=lambda m: (-m.score, m.label))
    best = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    close = tuple(m for m in ranked if m.score >= ASK_SCORE)[:_MAX_CANDIDATES]
    if best.score >= ACT_SCORE and best.score - runner_up >= ACT_MARGIN:
        decision: Decision = "act"
    elif best.score >= 1.0 and runner_up < 1.0:
        decision = "act"  # an exact name beats any near miss, however close
    elif close:
        decision = "ask"
    else:
        decision = "none"
    resolution = NameResolution(
        raw=raw,
        decision=decision,
        best=best,
        candidates=close if decision != "act" else (best,),
        context_used=coding,
    )
    _log(resolution, surface, runner_up=ranked[1] if len(ranked) > 1 else None)
    return resolution


def _log(resolution: NameResolution, surface: str, *, runner_up: NameMatch | None = None) -> None:
    best = resolution.best
    log.info(
        "name resolution [%s]: heard=%r -> %s score=%.3f method=%s decision=%s%s%s",
        surface or "-",
        resolution.raw,
        f"{best.label} ({best.key})" if best else "nothing",
        best.score if best else 0.0,
        best.method if best else "-",
        resolution.decision,
        f" runner_up={runner_up.label}:{runner_up.score:.3f}" if runner_up else "",
        " context=coding" if resolution.context_used else "",
    )


def labels(matches: Iterable[NameMatch]) -> list[str]:
    return [m.label for m in matches]


__all__ = [
    "ACT_MARGIN",
    "ACT_SCORE",
    "ASK_SCORE",
    "CONTEXT_BONUS",
    "NameCandidate",
    "NameMatch",
    "NameResolution",
    "has_coding_role",
    "is_coding_context",
    "jaro_winkler",
    "koelner_phonetik",
    "labels",
    "name_tokens",
    "normalize",
    "resolve_name",
    "spelling_fold",
    "word_similarity",
]
