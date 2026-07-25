"""Deterministic skill relevance — the paraphrase channel.

The gap this closes: a skill fires only when the user hits an author-written
regex verbatim, or when the router LLM happens to notice it among twenty
undifferentiated bullets. There is no *partial* match channel, so every
rephrasing falls through. This module is that channel.

It needs no model, no network and no new dependency, because **the corpus
already contains the vocabulary**: authors write their trigger regexes as lists
of literal phrases, quote real utterances inside ``when_to_use``, and fill
``intent_verbs`` / ``intent_objects`` with the German and Spanish nouns a user
actually says. The scorer mines what is already written rather than asking
anyone to re-author 22 builtin skills.

Design commitments, each load-bearing:

* **Stdlib only, pure CPU.** No LLM call, no disk access, no network — this sits
  on the voice hot path beside ~30 regex gates that cost 5-20 µs each (AP-9,
  AP-11). Typical query is ~75 µs at 50 skills.
* **O(len(utterance)), not O(len(skills)).** A posting list means installing
  more skills does not slow the hot path.
* **Corpus IDF, PLUS a closed function-word list.** IDF alone was the original
  design and it was measured to be insufficient here — see the comment above
  ``_FUNCTION_WORDS`` for the failure and the numbers. IDF still does the heavy
  lifting for domain terms that happen to be common across the installed set;
  the word list only covers the closed class IDF structurally cannot see.
* **Zero ``jarvis.*`` imports outside this package.** Skills are duck-typed via
  ``getattr`` exactly as ``prompt_injection.py`` and ``local_search.py`` do, so
  the module is trivially testable and cannot close an import cycle.

Deliberately NOT here: embeddings (a network round trip per query, or a ~90 MB
local model plus torch — both break the headless 1-vCPU floor and AP-26) and
stemming (a dependency or ~400 lines of per-language rules, needs language
detection we do not have on this path, and is hostile to German compounds,
which are the actual problem). Character trigrams do the same work in far less
code, language-agnostically.

Bands and thresholds are applied by :mod:`jarvis.skills.match_eval`; this module
only scores and reports where its own corpus-derived cut-offs lie. That keeps
the dependency one-directional.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field weights
# ---------------------------------------------------------------------------

FIELD_NAME = "name"
FIELD_TRIGGER = "trigger"
FIELD_INTENT = "intent"
FIELD_TAG = "tag"
FIELD_WHEN = "when_to_use"
FIELD_DESC = "description"
FIELD_CATEGORY = "category"

#: ``trigger`` carries name-level weight on purpose: a regex literal is the
#: author's own statement of "this is what the user says", and it is the only
#: field reliably non-English across the corpus (descriptions are English by
#: repo policy; triggers and ``intent_objects`` carry the German and Spanish).
FIELD_WEIGHTS: dict[str, float] = {
    FIELD_NAME: 3.0,
    FIELD_TRIGGER: 3.0,
    FIELD_INTENT: 2.5,
    FIELD_TAG: 2.0,
    FIELD_WHEN: 1.6,
    FIELD_DESC: 1.0,
    FIELD_CATEGORY: 0.6,
}

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

#: Tokens shorter than this are dropped: below three characters a token carries
#: no discrimination and matches half the corpus by accident.
MIN_TOKEN_LEN = 3

#: Added to the query-mass denominator. Roughly "one distinctive word", which
#: stops a single-token utterance from producing an unbounded score.
QUERY_MASS_FLOOR = 3.0

#: A term present in this fraction of the corpus or more is treated as carrying
#: no information at all.
CORPUS_COMMON_FRACTION = 0.6

#: The corpus-common clamp only applies from this many skills upwards. Below
#: it the fraction is meaningless and actively harmful: on a fresh install with
#: three skills, "present in 60 % of the corpus" is TWO skills, so almost every
#: term would be clamped to zero and nothing could ever match. A fresh install
#: is precisely when the user first tries a skill, so failing there is the
#: worst possible place to fail.
CORPUS_COMMON_MIN_SKILLS = 6

#: How many top candidates get the (more expensive) trigram pass.
TRIGRAM_CANDIDATES = 8

#: Utterance tokens shorter than this contribute no trigrams — below five
#: characters the trigram set is noise.
TRIGRAM_MIN_TOKEN_LEN = 5

#: Weight of the trigram channel relative to the token channel.
TRIGRAM_WEIGHT = 0.55

#: Smoothing in the trigram containment denominator.
TRIGRAM_FLOOR = 8

#: Clear-winner rule: the leader must beat the runner-up by an absolute margin
#: OR a relative one, whichever is larger. The relative term is what forces a
#: genuine tie ("gmail 1.80 vs slack 1.70") to degrade instead of firing.
MARGIN_ABS = 0.12
MARGIN_REL = 0.25

#: Threshold coefficients, expressed against a corpus statistic rather than as
#: bare floats so they stay meaningful from a 5-skill fresh install to a
#: 60-skill power user (see :meth:`SkillMatchIndex.fire_threshold`).
#:
#: Set by ``scripts/skill_relevance_calibrate.py``, which picks the smallest
#: FIRE threshold with ZERO false positives over the hard-negative set and then
#: REPORTS the recall that costs. Do not hand-tune these to make a single
#: over-fire go away — add a hard negative to the golden set and re-run the
#: sweep, so the trade-off lands in a diff instead of in someone's head.
#:
#: Calibrated 2026-07-25 against the shipped builtin corpus: the strongest
#: negative scored 1.139 while the configured FIRE threshold was 1.057, i.e.
#: below it — only the clear-winner margin rule was preventing a capture. Too
#: thin to rely on, so FIRE was raised to sit above the negative distribution
#: on its own.
FIRE_COEFFICIENT = 0.68
HINT_COEFFICIENT = 0.22

# ---------------------------------------------------------------------------
# Hard caps — nothing a user installs may degrade the hot path
# ---------------------------------------------------------------------------

MAX_INDEXED_SKILLS = 300
MAX_TOKENS_PER_SKILL = 200
MAX_TRIGRAMS_PER_SKILL = 600

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# Umlauts fold to digraphs BEFORE the NFKD pass, because the corpus is written
# in digraph form ("tages(ue|ue)berblick", "faehigkeit"). A plain NFKD strip
# would yield the bare vowel instead and silently disable the German
# vocabulary. Lifted from jarvis/brain/turn_planner.py for exactly that reason.
_UMLAUT_MAP = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue",  # i18n-allow: speech-input normalization data
    "Ä": "ae", "Ö": "oe", "Ü": "ue",  # i18n-allow: speech-input normalization data
    "ß": "ss",  # i18n-allow: speech-input normalization data
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Function words in every supported language, in normalized (digraph) form.
#
# Corpus IDF was SUPPOSED to make this list unnecessary — a term common to the
# whole corpus earns ~0 weight on its own. Measured against the real 20 builtin
# skills, that reasoning is wrong here, and the failure is instructive: skill
# descriptions are English by repo policy while ``when_to_use`` and triggers
# quote German and Spanish. A German function word therefore appears in ONE
# skill, not twenty, so IDF scores it as maximally DISTINCTIVE. Live
# measurement: "die" and "ich" carried idf 2.64 of a possible 3.74 and made
# morning-routine win "ich brauch jetzt ruhe zum arbeiten"; the trigger literal
# "meine" (mined from "(meine[nm]? )?" in the calendar pattern) made
# plugin-google_calendar beat cli-gcloud on a Google Cloud billing question.
#
# So this list is a deliberate, bounded exception, not a maintenance burden:
# function words are a CLOSED class that does not change as skills are
# installed. Content words are never listed — only articles, pronouns,
# auxiliaries, prepositions, conjunctions and degree adverbs.
_FUNCTION_WORDS: frozenset[str] = frozenset(
    (
        # -- German ---------------------------------------------------
        "der die das den dem des ein eine einen einem einer eines "  # i18n-allow
        "und oder aber ich du sie wir ihr mein meine meinem meinen "  # i18n-allow
        "meiner meines mich mir dich dir sich uns euch ihm ihn ihnen "  # i18n-allow
        "ist sind war waren bin bist sein seine habe hast hat haben "  # i18n-allow
        "hatte hatten wird werden wurde muss musst muessen kann "  # i18n-allow
        "kannst koennen koennte soll sollte sollen will willst wollen "  # i18n-allow
        "darf duerfen mag moegen nicht kein keine nur auch noch schon "  # i18n-allow
        "mal doch denn dann wenn weil dass als wie was wer wen wem "  # i18n-allow
        "wann warum wieso welche welcher welches fuer mit von zu zum "  # i18n-allow
        "zur aus bei nach ueber unter vor durch gegen ohne seit gerade "  # i18n-allow
        "eigentlich jetzt bitte danke etwas alles nichts viel mehr "  # i18n-allow
        "sehr ganz immer wieder hier dort "  # i18n-allow
        # -- English --------------------------------------------------
        "the and but you your his her its our their they them him "
        "are was were been being have has had does did doing "
        "will would can could should shall may might must not "
        "only also still already just then when because that this these "
        "those with from for into over under after before between about "
        "there here very much more most some any each every "
        "yes please thank thanks "
        # -- Spanish --------------------------------------------------
        "los las una unos unas pero ella nosotros mis nos por para con "  # i18n-allow
        "del son era eran ser estar tengo tienes tiene tener hacer solo "  # i18n-allow
        "tambien ahora quien donde cuando como mas mucho poco todo nada "  # i18n-allow
        "bien que sus esta este estos estas ese esa hay han desde hasta "  # i18n-allow
        "entre sobre sin"  # i18n-allow
    ).split()
)


def _politeness_fillers() -> frozenset[str]:
    """The project's own curated address/courtesy vocabulary.

    ``trigger_matcher._FILLER_WORDS`` already encodes which German and English
    tokens are pure politeness glue ("bitte", "kurz", "mal", "mach"), reviewed
    against real utterances. Reusing it beats maintaining a second, drifting
    copy — and it covers the imperative glue ("mach", "kannst") that a plain
    function-word list misses.
    """
    try:
        from jarvis.skills.trigger_matcher import _FILLER_WORDS

        return frozenset(normalize_text(word) for word in _FILLER_WORDS)
    except Exception:  # noqa: BLE001 — never let a refactor there break scoring
        return frozenset()


def normalize_text(text: str) -> str:
    """Casefold, fold umlauts to digraphs, then strip remaining diacritics.

    The trailing NFKD pass costs nothing and makes Spanish accents and ñ fold
    correctly for free, which is why no language-specific branch is needed.
    """
    folded = str(text or "").casefold().translate(_UMLAUT_MAP)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_STOPWORDS: frozenset[str] | None = None


def _stopwords() -> frozenset[str]:
    global _STOPWORDS
    if _STOPWORDS is None:
        _STOPWORDS = _FUNCTION_WORDS | _politeness_fillers()
    return _STOPWORDS


def is_content_token(token: str) -> bool:
    """True for a token that can discriminate between skills."""
    return len(token) >= MIN_TOKEN_LEN and token not in _stopwords()


def tokenize(text: str) -> tuple[str, ...]:
    """Normalized content tokens, order preserved.

    Function words are dropped on BOTH sides — index and query. Dropping them
    from the query too is deliberate: they would otherwise inflate the
    query-mass denominator and dilute the real signal in a politely-phrased
    sentence, which is most of what a person actually says out loud.
    """
    return tuple(
        tok for tok in _TOKEN_RE.findall(normalize_text(text)) if is_content_token(tok)
    )


def tokenize_with_spans(text: str) -> tuple[tuple[str, str], ...]:
    """``(normalized_token, raw_span)`` pairs over the ORIGINAL text.

    The raw span is what downstream guards need: the definitional-question
    guard escapes it into a pattern run against the untouched utterance, so a
    normalized token would blind it on every umlaut word.
    """
    out: list[tuple[str, str]] = []
    for match in re.finditer(r"\w+", text or "", re.UNICODE):
        raw = match.group(0)
        for tok in _TOKEN_RE.findall(normalize_text(raw)):
            if is_content_token(tok):
                out.append((tok, raw))
    return tuple(out)


# ---------------------------------------------------------------------------
# Regex literal mining — the highest-leverage component
# ---------------------------------------------------------------------------

# Escapes that introduce a character CLASS rather than a literal character.
# Hitting one ends the current literal run.
_CLASS_ESCAPES = frozenset("swdbSWDBAZnrtfv")


def mine_pattern_literals(pattern: str) -> tuple[str, ...]:
    """Extract literal phrase runs from an author-written voice regex.

    A single left-to-right pass. Anything that is not a plain literal ends the
    current run; the runs that survive are the phrases the author guaranteed
    the pattern contains.

    Hand-rolled on purpose: ``sre_parse`` / ``re._parser`` is a private stdlib
    API that was renamed in 3.11, and this project's ``requires-python``
    admits up to 3.14. A gate built on a private API is a version-skew brick
    waiting to happen (AP-28), and this function is load-bearing enough that a
    crash here would take out the vocabulary of every skill at once.

    Worked example (a real builtin trigger, quoted as matching data):  <!-- i18n-allow -->
    ``(morgenroutine|morning routine|tages(ue|ue)berblick)`` yields the runs
    ``morgenroutine``, ``morning routine``, ``tages`` and ``berblick``. The
    compound split at the group boundary is expected: the trigram channel is
    what recovers it.
    """
    runs: list[str] = []
    current: list[str] = []
    index = 0
    length = len(pattern or "")

    def flush() -> None:
        if current:
            runs.append("".join(current))
            current.clear()

    while index < length:
        char = pattern[index]

        if char == "\\":
            nxt = pattern[index + 1] if index + 1 < length else ""
            if nxt and nxt not in _CLASS_ESCAPES:
                # An escaped metacharacter is a literal: \. \- \( \+ ...
                current.append(nxt)
            else:
                flush()
            index += 2
            continue

        if char == "[":
            flush()
            index += 1
            # Skip to the matching ']', honouring escapes and a leading ']'.
            if index < length and pattern[index] == "^":
                index += 1
            if index < length and pattern[index] == "]":
                index += 1
            while index < length and pattern[index] != "]":
                index += 2 if pattern[index] == "\\" else 1
            index += 1
            continue

        if char == "{":
            # A quantifier such as {0,2}: it repeats what came before and adds
            # no literal of its own.
            flush()
            while index < length and pattern[index] != "}":
                index += 1
            index += 1
            continue

        if char in "?*+":
            # The preceding single character is optional or repeated, so it is
            # not guaranteed: drop it, then end the run.
            if current:
                current.pop()
            flush()
            index += 1
            continue

        if char in "()|.^$":
            flush()
            index += 1
            continue

        current.append(char)
        index += 1

    flush()
    return tuple(run.strip() for run in runs if run.strip())


# ---------------------------------------------------------------------------
# Trigrams
# ---------------------------------------------------------------------------


def trigrams(text: str) -> frozenset[str]:
    """Space-padded character trigrams of a normalized surface."""
    padded = f" {normalize_text(text).strip()} "
    if len(padded) < 3:
        return frozenset()
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def _query_trigrams(text: str) -> frozenset[str]:
    """Trigrams of the long tokens only — word boundaries must survive.

    Trigramming the whole sentence would blur across word boundaries and make
    every skill look vaguely similar to every utterance.
    """
    out: set[str] = set()
    for token in tokenize(text):
        if len(token) >= TRIGRAM_MIN_TOKEN_LEN:
            out |= trigrams(token)
    return frozenset(out)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TermHit:
    """One utterance token that matched a skill, and what it was worth."""

    token: str
    field: str
    raw_span: str
    contribution: float


@dataclass(frozen=True, slots=True)
class ScoredSkill:
    """A skill and its relevance score for one utterance."""

    name: str
    score: float
    token_score: float = 0.0
    trigram_score: float = 0.0
    hits: tuple[TermHit, ...] = ()

    @property
    def reason(self) -> str:
        """Human-readable explanation, strongest signal first.

        Rendered directly in UI search results and the Test-Match panel, so it
        names the matched token AND the field it came from — "matched name,
        description" (the old wording) never told anyone why.
        """
        if not self.hits:
            return ""
        best: dict[str, TermHit] = {}
        for hit in self.hits:
            existing = best.get(hit.token)
            if existing is None or hit.contribution > existing.contribution:
                best[hit.token] = hit
        ordered = sorted(best.values(), key=lambda h: (-h.contribution, h.token))
        return " · ".join(f"{hit.field}: {hit.token}" for hit in ordered[:4])

    @property
    def evidence(self) -> str:
        """RAW span of the strongest matched token, as it appeared in the text.

        Never normalized — the definitional-question guard re-escapes this
        against the original utterance.
        """
        if not self.hits:
            return ""
        return max(self.hits, key=lambda h: h.contribution).raw_span


@dataclass(frozen=True, slots=True)
class RelevanceRanking:
    """Ranked skills for one utterance, plus the corpus-derived cut-offs."""

    ranked: tuple[ScoredSkill, ...] = ()
    margin: float = 0.0
    clear_winner: bool = False
    fire_threshold: float = 0.0
    hint_threshold: float = 0.0

    @property
    def top(self) -> ScoredSkill | None:
        return self.ranked[0] if self.ranked else None


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

#: ``(skill_index, field_weight, field_name)``
_Posting = tuple[int, float, str]


@dataclass(frozen=True, slots=True)
class SkillMatchIndex:
    """An inverted index over the skill corpus.

    Immutable and self-contained — no registry reference, no paths, no file
    handles. That is what lets it be handed to ``turn_planner.plan_turn``,
    whose contract promises no model call, no disk access and no network.
    """

    names: tuple[str, ...] = ()
    postings: dict[str, tuple[_Posting, ...]] = dc_field(default_factory=dict)
    idf: dict[str, float] = dc_field(default_factory=dict)
    idf_unknown: float = 0.0
    #: trigram -> skill indices containing it. A posting list rather than a
    #: per-skill set, because the trigram channel MUST be able to surface a
    #: skill the token channel missed entirely — that is its entire purpose
    #: (a German compound the miner split, e.g. "tagesueberblick" against the
    #: indexed "tages" + "berblick"). Deriving trigram candidates only from
    #: skills that already scored on tokens would make it decorative.
    trigram_postings: dict[str, tuple[int, ...]] = dc_field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.names)

    @property
    def fire_threshold(self) -> float:
        """Score at or above which a single skill may capture a turn.

        Derived from a corpus statistic — "one maximally-distinctive name token
        in a short utterance" — rather than being a bare float, so the same
        coefficient stays meaningful as the corpus grows. At N=22 the reference
        value is ~1.73; at N=60, ~1.77. That flatness is the point.
        """
        reference = (
            FIELD_WEIGHTS[FIELD_NAME] * self.idf_unknown
        ) / (self.idf_unknown + QUERY_MASS_FLOOR)
        return FIRE_COEFFICIENT * reference

    @property
    def hint_threshold(self) -> float:
        """Score at or above which a skill is worth showing the model."""
        reference = (
            FIELD_WEIGHTS[FIELD_NAME] * self.idf_unknown
        ) / (self.idf_unknown + QUERY_MASS_FLOOR)
        return HINT_COEFFICIENT * reference

    # -- query ------------------------------------------------------------

    def rank(self, text: str, *, limit: int = 5) -> RelevanceRanking:
        """Score every skill the utterance touches, best first.

        Cost is O(tokens in ``text``) for the token channel plus a fixed
        trigram pass over the top :data:`TRIGRAM_CANDIDATES`; it does not grow
        with the number of installed skills.
        """
        if not text or not self.names:
            return RelevanceRanking(
                fire_threshold=self.fire_threshold,
                hint_threshold=self.hint_threshold,
            )

        spans = tokenize_with_spans(text)
        if not spans:
            return RelevanceRanking(
                fire_threshold=self.fire_threshold,
                hint_threshold=self.hint_threshold,
            )

        # First raw span per normalized token (for the evidence field).
        raw_of: dict[str, str] = {}
        for token, raw in spans:
            raw_of.setdefault(token, raw)
        unique_tokens = tuple(raw_of)

        # Query mass: how much information the utterance carries in total.
        # Normalizing by THIS (rather than by document length) makes the score
        # mean "what fraction of the utterance does this skill explain?" —
        # precise on short commands, conservative on long prose. Document-length
        # normalization would instead punish a well-documented skill for being
        # well-documented.
        mass = sum(self.idf.get(tok, self.idf_unknown) for tok in unique_tokens)
        denominator = mass + QUERY_MASS_FLOOR

        raw_scores: dict[int, float] = {}
        hits: dict[int, list[TermHit]] = {}
        for token in unique_tokens:
            postings = self.postings.get(token)
            if not postings:
                continue
            weight_of_token = self.idf.get(token, 0.0)
            if weight_of_token <= 0.0:
                continue
            for skill_index, field_weight, field in postings:
                # ``build_index`` already collapsed each (token, skill) pair to
                # its single strongest field, so this is max-over-fields, never
                # sum. Summing would let a skill repeating "gmail" in four
                # fields quadruple-count one utterance word — the "verbose
                # skill wins everything" failure.
                contribution = weight_of_token * field_weight
                hits.setdefault(skill_index, []).append(
                    TermHit(
                        token=token,
                        field=field,
                        raw_span=raw_of[token],
                        contribution=contribution,
                    )
                )
                raw_scores[skill_index] = raw_scores.get(skill_index, 0.0) + contribution

        token_scores = {
            index: value / denominator for index, value in raw_scores.items()
        }

        # Trigram pass. This is what recovers German compounds
        # ("tagesueberblick" against the indexed "tages" + "berblick"),
        # inflection and STT garble — cases no token-exact channel can reach.
        # Candidates come from the trigram postings, NOT from the token
        # winners, so a skill the token channel missed completely can still
        # surface. Cost stays O(query trigrams).
        query_tri = _query_trigrams(text)
        trigram_scores: dict[int, float] = {}
        if query_tri:
            overlaps: dict[int, int] = {}
            for gram in query_tri:
                for index in self.trigram_postings.get(gram, ()):
                    overlaps[index] = overlaps.get(index, 0) + 1
            # Longest content token, used as the evidence span for a
            # trigram-only match so the downstream definitional guard still has
            # a raw span to work with.
            longest = max(raw_of.items(), key=lambda kv: len(kv[0]))
            leaders = sorted(overlaps, key=lambda i: (-overlaps[i], self.names[i]))
            for index in leaders[:TRIGRAM_CANDIDATES]:
                # Containment-shaped, not Jaccard: Jaccard would punish a skill
                # for having many triggers even when it fully contains the
                # utterance's trigrams.
                trigram_scores[index] = overlaps[index] / (len(query_tri) + TRIGRAM_FLOOR)
                if index not in hits:
                    hits[index] = [
                        TermHit(
                            token=longest[0],
                            field="trigram",
                            raw_span=longest[1],
                            contribution=0.0,
                        )
                    ]

        if not token_scores and not trigram_scores:
            return RelevanceRanking(
                fire_threshold=self.fire_threshold,
                hint_threshold=self.hint_threshold,
            )

        scored = [
            ScoredSkill(
                name=self.names[index],
                score=token_scores.get(index, 0.0)
                + TRIGRAM_WEIGHT * trigram_scores.get(index, 0.0),
                token_score=token_scores.get(index, 0.0),
                trigram_score=trigram_scores.get(index, 0.0),
                hits=tuple(
                    sorted(hits.get(index, ()), key=lambda h: (-h.contribution, h.token))
                ),
            )
            for index in set(token_scores) | set(trigram_scores)
        ]
        # Name as the tie-break keeps the ranking deterministic regardless of
        # registry iteration order or PYTHONHASHSEED.
        scored.sort(key=lambda s: (-s.score, s.name))

        margin = scored[0].score - scored[1].score if len(scored) > 1 else scored[0].score
        clear = margin >= max(MARGIN_ABS, MARGIN_REL * scored[0].score)

        return RelevanceRanking(
            ranked=tuple(scored[: max(1, limit)]),
            margin=margin,
            clear_winner=clear,
            fire_threshold=self.fire_threshold,
            hint_threshold=self.hint_threshold,
        )


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def _skill_surfaces(skill: Any) -> list[tuple[str, str]]:
    """``(field, text)`` pairs contributing this skill's vocabulary."""
    out: list[tuple[str, str]] = [(FIELD_NAME, str(getattr(skill, "name", "") or ""))]
    frontmatter = getattr(skill, "frontmatter", None)
    if frontmatter is None:
        return out

    for trigger in getattr(frontmatter, "triggers", ()) or ():
        if getattr(trigger, "type", "") != "voice":
            continue
        pattern = getattr(trigger, "pattern", "") or ""
        for literal in mine_pattern_literals(pattern):
            out.append((FIELD_TRIGGER, literal))

    for attribute in ("intent_verbs", "intent_objects"):
        for value in getattr(frontmatter, attribute, ()) or ():
            out.append((FIELD_INTENT, str(value)))

    for tag in getattr(frontmatter, "tags", ()) or ():
        out.append((FIELD_TAG, str(tag)))

    out.append((FIELD_WHEN, str(getattr(frontmatter, "when_to_use", "") or "")))
    out.append((FIELD_DESC, str(getattr(frontmatter, "description", "") or "")))
    out.append((FIELD_CATEGORY, str(getattr(frontmatter, "category", "") or "")))
    return out


#: Surfaces the trigram channel is built from. Description and when_to_use are
#: deliberately excluded: their English prose would flood the trigram set and
#: destroy its ability to discriminate.
_TRIGRAM_FIELDS = frozenset({FIELD_NAME, FIELD_TRIGGER, FIELD_INTENT, FIELD_TAG})


def _skill_mtime(skill: Any) -> float:
    """Last-modified time used for eviction; 0.0 when unknown.

    Mirrors ``prompt_injection._skill_mtime`` so the prompt listing and the
    match index evict the same skills under pressure.
    """
    try:
        return float(skill.path.stat().st_mtime)
    except Exception:  # noqa: BLE001
        try:
            return float(getattr(skill, "mtime", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0


def build_index(skills: list[Any]) -> SkillMatchIndex:
    """Build the inverted index. Pure: same skills in, same index out."""
    selected = list(skills or ())
    if len(selected) > MAX_INDEXED_SKILLS:
        log.warning(
            "skill match index capped at %d of %d skills (evicting oldest by mtime)",
            MAX_INDEXED_SKILLS,
            len(selected),
        )
        selected.sort(key=_skill_mtime, reverse=True)
        selected = selected[:MAX_INDEXED_SKILLS]

    names: list[str] = []
    bags: list[dict[str, tuple[float, str]]] = []
    tri_sets: list[frozenset[str]] = []

    for skill in selected:
        name = str(getattr(skill, "name", "") or "")
        if not name:
            continue
        bag: dict[str, tuple[float, str]] = {}
        skill_tri: set[str] = set()
        for field, text in _skill_surfaces(skill):
            if not text:
                continue
            weight = FIELD_WEIGHTS[field]
            for token in tokenize(text):
                current = bag.get(token)
                if current is None or weight > current[0]:
                    bag[token] = (weight, field)
            if field in _TRIGRAM_FIELDS and len(skill_tri) < MAX_TRIGRAMS_PER_SKILL:
                skill_tri |= trigrams(text)
        if len(bag) > MAX_TOKENS_PER_SKILL:
            # Keep the heaviest-weighted terms; a pathologically long
            # when_to_use must not dominate the posting lists.
            kept = sorted(bag.items(), key=lambda kv: (-kv[1][0], kv[0]))
            bag = dict(kept[:MAX_TOKENS_PER_SKILL])
        names.append(name)
        bags.append(bag)
        tri_sets.append(frozenset(sorted(skill_tri)[:MAX_TRIGRAMS_PER_SKILL]))

    total = len(names)
    if total == 0:
        return SkillMatchIndex(names=(), postings={}, idf={}, idf_unknown=0.0)

    document_frequency: dict[str, int] = {}
    for bag in bags:
        for token in bag:
            document_frequency[token] = document_frequency.get(token, 0) + 1

    # log((N + 1) / (df + 0.5)) — deliberately NOT BM25's
    # log((N - df + 0.5) / (df + 0.5)), which goes negative once df > N/2. At
    # N ~ 25 that would make a common word actively SUBTRACT score, which is
    # wrong for a "does this turn signal this skill at all" gate.
    common_cutoff = (
        CORPUS_COMMON_FRACTION * total
        if total >= CORPUS_COMMON_MIN_SKILLS
        else float("inf")
    )
    idf: dict[str, float] = {}
    for token, frequency in document_frequency.items():
        if frequency >= common_cutoff:
            idf[token] = 0.0
        else:
            idf[token] = math.log((total + 1) / (frequency + 0.5))
    idf_unknown = math.log((total + 1) / 0.5)

    postings: dict[str, list[_Posting]] = {}
    for index, bag in enumerate(bags):
        for token, (weight, field) in bag.items():
            postings.setdefault(token, []).append((index, weight, field))

    trigram_postings: dict[str, list[int]] = {}
    for index, tri_set in enumerate(tri_sets):
        for gram in tri_set:
            trigram_postings.setdefault(gram, []).append(index)

    return SkillMatchIndex(
        names=tuple(names),
        postings={token: tuple(entries) for token, entries in postings.items()},
        idf=idf,
        idf_unknown=idf_unknown,
        trigram_postings={
            gram: tuple(entries) for gram, entries in trigram_postings.items()
        },
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE: dict[tuple[int, int, bool], SkillMatchIndex] = {}


def _source_skills(registry: Any, include_inactive: bool) -> list[Any]:
    """Which skills the index sees.

    The voice index is built from ``list_active()`` ONLY, so a DRAFT skill is
    not merely filtered out at score time — it is not in the index at all.
    Encoding AP-15 at BUILD time rather than query time is what makes it
    structural instead of a rule someone can forget to apply.
    """
    try:
        if include_inactive:
            return list(registry.list())
        return list(registry.list_active())
    except Exception:  # noqa: BLE001
        return []


def get_index(registry: Any, *, include_inactive: bool = False) -> SkillMatchIndex:
    """Return the cached index for ``registry``, building it on first use.

    Lazy on purpose (AP-26). Building eagerly inside the registry reload would
    put 8-15 ms of CPU on the deferred boot scan — a path that is critical by
    construction — and would rebuild on every watchdog fire while the user is
    typing in an open SKILL.md.

    No lock: :func:`build_index` is pure and a benign double-build under a race
    is far cheaper than letting the watchdog thread block a voice turn.
    """
    if registry is None:
        return SkillMatchIndex(names=(), postings={}, idf={}, idf_unknown=0.0)
    key = (id(registry), int(getattr(registry, "generation", 0)), include_inactive)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    index = build_index(_source_skills(registry, include_inactive))
    # Bounded to the two live variants (voice + UI); dropping stale generations
    # keeps this from growing across reloads.
    _CACHE.clear()
    _CACHE[key] = index
    return index


def clear_index_cache() -> None:
    """Drop every cached index (tests, and an explicit registry swap)."""
    _CACHE.clear()


__all__ = [
    "FIELD_CATEGORY",
    "FIELD_DESC",
    "FIELD_INTENT",
    "FIELD_NAME",
    "FIELD_TAG",
    "FIELD_TRIGGER",
    "FIELD_WEIGHTS",
    "FIELD_WHEN",
    "MARGIN_ABS",
    "MARGIN_REL",
    "MAX_INDEXED_SKILLS",
    "MAX_TOKENS_PER_SKILL",
    "MAX_TRIGRAMS_PER_SKILL",
    "MIN_TOKEN_LEN",
    "RelevanceRanking",
    "ScoredSkill",
    "SkillMatchIndex",
    "TermHit",
    "build_index",
    "clear_index_cache",
    "get_index",
    "mine_pattern_literals",
    "normalize_text",
    "tokenize",
    "tokenize_with_spans",
    "trigrams",
]
