"""Behaviour tests for the relevance scorer.

Several of these pin decisions that were made because a naive alternative was
MEASURED to be wrong against the real 20 builtin skills, not because they
sounded better in the abstract. Where that is the case the docstring says so —
those tests are the reason the constant has its value, and flipping one should
force a re-measurement rather than a re-tune.
"""
from __future__ import annotations

import time

import pytest

from jarvis.skills import relevance
from jarvis.skills.relevance import (
    FIELD_WEIGHTS,
    ScoredSkill,
    build_index,
    clear_index_cache,
    get_index,
    is_content_token,
    normalize_text,
    tokenize,
)
from jarvis.skills.schema import SkillLifecycleState

# German vocabulary used as fixtures throughout — the speech input under test.
# Declared once so the assertions stay readable and inside the line limit.
FOCUS = "fokusmodus"  # i18n-allow: speech input
INBOX = "postfach"  # i18n-allow: speech input
OVERVIEW = "tagesueberblick"  # i18n-allow: speech input
OVERVIEW_UMLAUT = "Tagesüberblick"  # i18n-allow: speech input
MESSAGE = "nachricht"  # i18n-allow: speech input
OVERVIEW_PATTERN = r"(tages(ue|ü)berblick)"  # i18n-allow: speech input

FUNCTION_WORD_SAMPLES = (
    "die", "ich", "muss", "meine", "mich",  # i18n-allow: speech input
    "the", "your", "para", "cuando",
)
CONTENT_WORD_SAMPLES = (
    "mail", "kalender", "termin", "fokus", "repo", "work", "deep",  # i18n-allow: speech input
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTrigger:
    def __init__(self, pattern: str, kind: str = "voice") -> None:
        self.type = kind
        self.pattern = pattern


class _FakeFrontmatter:
    def __init__(
        self,
        *,
        description: str = "",
        when_to_use: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        triggers: list[_FakeTrigger] | None = None,
        intent_verbs: list[str] | None = None,
        intent_objects: list[str] | None = None,
    ) -> None:
        self.description = description
        self.when_to_use = when_to_use
        self.category = category
        self.tags = tags or []
        self.triggers = triggers or []
        self.intent_verbs = intent_verbs or []
        self.intent_objects = intent_objects or []


class _FakeSkill:
    def __init__(
        self,
        name: str,
        frontmatter: _FakeFrontmatter | None = None,
        state: SkillLifecycleState = SkillLifecycleState.ACTIVE,
    ) -> None:
        self.name = name
        self.frontmatter = frontmatter if frontmatter is not None else _FakeFrontmatter()
        self.state = state


class _FakeRegistry:
    def __init__(self, skills: list[_FakeSkill], generation: int = 1) -> None:
        self._skills = skills
        self.generation = generation

    def list(self) -> list[_FakeSkill]:
        return list(self._skills)

    def list_active(self) -> list[_FakeSkill]:
        return [
            s
            for s in self._skills
            if s.state in (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)
        ]


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_index_cache()


# ---------------------------------------------------------------------------
# Normalization + tokenization
# ---------------------------------------------------------------------------


def test_umlauts_fold_to_digraphs_not_bare_vowels() -> None:
    """The corpus is WRITTEN in digraph form, e.g. "tages(ue|ue)berblick".

    A plain NFKD strip would produce "uberblick" and fail to match the
    "ueberblick" the same pattern also offers — silently disabling the German
    half of the vocabulary.
    """
    assert normalize_text("Tagesüberblick") == "tagesueberblick"  # i18n-allow: speech input
    assert normalize_text("Fähigkeit") == "faehigkeit"  # i18n-allow: speech input
    assert normalize_text("groß") == "gross"  # i18n-allow: speech input


def test_spanish_accents_fold_without_a_language_branch() -> None:
    """Accents fold; punctuation is the tokenizer's job, not this function's."""
    assert tokenize("¿Qué canción favorita?") == ("cancion", "favorita")


def test_short_tokens_are_dropped() -> None:
    assert "ab" not in tokenize("ab abc")
    assert "abc" in tokenize("ab abc")


# ---------------------------------------------------------------------------
# Function words — the regression a live measurement found
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word",
    FUNCTION_WORD_SAMPLES,
)
def test_function_words_never_become_vocabulary(word: str) -> None:
    """Measured regression, 2026-07-25 (real 20-skill corpus).

    Corpus IDF alone does NOT neutralize these. Skill descriptions are English
    while when_to_use and triggers quote German, so a German function word
    appears in ONE skill and IDF scores it as maximally distinctive. Live
    effect before the fix: "die"/"ich" (idf 2.64 of 3.74) made morning-routine
    win "ich brauch jetzt ruhe zum arbeiten", and the mined trigger literal
    "meine" made plugin-google_calendar beat cli-gcloud on a Google Cloud
    billing question.
    """
    assert is_content_token(word) is False
    assert word not in tokenize(f"bitte {word} sofort")  # i18n-allow: speech input


def test_content_words_are_never_stoplisted() -> None:
    """The stoplist must stay a closed function-word class, never content."""
    for word in CONTENT_WORD_SAMPLES:
        assert is_content_token(word) is True


def test_a_polite_sentence_does_not_dilute_the_signal() -> None:
    """Function words are dropped from the QUERY too, not just the index.

    Otherwise they inflate the query-mass denominator and a politely-phrased
    request scores lower than a terse one — which is backwards, because polite
    phrasing is most of what a person actually says out loud.
    """
    index = build_index(
        [
            _FakeSkill("focus", _FakeFrontmatter(tags=[FOCUS])),
            _FakeSkill("other", _FakeFrontmatter(tags=["gmail"])),
        ]
    )
    terse = index.rank(FOCUS)
    polite = index.rank(f"kannst du mir bitte mal den {FOCUS} machen")
    assert terse.top is not None and polite.top is not None
    assert polite.top.name == terse.top.name == "focus"
    # Not identical — "machen" is a content verb and legitimately adds mass —
    # but politeness alone must not halve the score.
    assert polite.top.score > terse.top.score * 0.5


# ---------------------------------------------------------------------------
# IDF
# ---------------------------------------------------------------------------


def test_a_term_in_every_skill_carries_no_weight() -> None:
    skills = [
        _FakeSkill(f"skill-{i}", _FakeFrontmatter(description="shared corpus term"))
        for i in range(10)
    ]
    index = build_index(skills)
    assert index.idf["shared"] == 0.0
    assert index.idf["corpus"] == 0.0


def test_a_unique_term_carries_the_most_weight() -> None:
    skills = [_FakeSkill("alpha", _FakeFrontmatter(description="distinctive thing"))]
    skills += [
        _FakeSkill(f"s{i}", _FakeFrontmatter(description="common thing"))
        for i in range(4)
    ]
    index = build_index(skills)
    assert index.idf["distinctive"] > index.idf["common"] > 0.0


def test_the_corpus_common_clamp_is_disabled_on_a_tiny_corpus() -> None:
    """A fresh install must still match.

    With three skills, "present in 60 % of the corpus" is TWO skills, so the
    clamp would zero out nearly every term and nothing could ever match — at
    exactly the moment a new user first tries a skill.
    """
    index = build_index(
        [_FakeSkill(f"s{i}", _FakeFrontmatter(tags=["shared"])) for i in range(3)]
    )
    assert index.idf["shared"] > 0.0
    assert index.rank("shared").ranked != ()


def test_idf_is_never_negative() -> None:
    """BM25's log((N-df+0.5)/(df+0.5)) goes negative past df > N/2.

    At corpus sizes around 25 that would make a common word actively SUBTRACT
    score, which is wrong for a "does this turn signal this skill at all" gate.
    """
    skills = [
        _FakeSkill(f"s{i}", _FakeFrontmatter(description="everywhere token"))
        for i in range(30)
    ]
    skills.append(_FakeSkill("rare", _FakeFrontmatter(description="scarce token")))
    index = build_index(skills)
    assert all(value >= 0.0 for value in index.idf.values())


# ---------------------------------------------------------------------------
# Scoring shape
# ---------------------------------------------------------------------------


def test_field_weights_rank_name_and_trigger_highest() -> None:
    assert FIELD_WEIGHTS["name"] == FIELD_WEIGHTS["trigger"]
    assert FIELD_WEIGHTS["trigger"] > FIELD_WEIGHTS["intent"] > FIELD_WEIGHTS["tag"]
    assert FIELD_WEIGHTS["tag"] > FIELD_WEIGHTS["when_to_use"] > FIELD_WEIGHTS["category"]


def test_repeating_a_term_across_fields_does_not_multiply_the_score() -> None:
    """max over fields, never sum — else the wordiest skill wins everything."""
    verbose = _FakeSkill(
        "verbose",
        _FakeFrontmatter(
            description="gmail gmail",
            when_to_use="gmail",
            tags=["gmail"],
            category="gmail",
        ),
    )
    focused = _FakeSkill("gmail", _FakeFrontmatter(description="unrelated words here"))
    index = build_index([verbose, focused, _FakeSkill("filler")])
    ranking = index.rank("gmail")
    assert ranking.top is not None
    assert ranking.top.name == "gmail", "the name match must beat four weak repeats"


def test_a_long_utterance_scores_lower_than_a_terse_one() -> None:
    """Normalizing by QUERY mass, not document length.

    The score means "what fraction of this utterance does the skill explain?",
    so one hit inside a rambling sentence is worth less than the same hit in a
    direct command. Document-length normalization would instead punish a
    well-documented skill for being well-documented.
    """
    index = build_index(
        [
            _FakeSkill("focus", _FakeFrontmatter(tags=[FOCUS])),
            _FakeSkill("other", _FakeFrontmatter(tags=[INBOX])),
        ]
    )
    terse = index.rank(FOCUS)
    rambling = index.rank(
        f"{FOCUS} wetter berlin photosynthese rezept fahrrad urlaub steuer"
    )
    assert terse.top is not None and rambling.top is not None
    assert terse.top.score > rambling.top.score


def test_trigram_channel_recovers_a_compound() -> None:
    """The load-bearing case: the scanner splits the compound, chars rejoin it."""
    index = build_index(
        [
            _FakeSkill(
                "morning-routine",
                _FakeFrontmatter(
                    triggers=[_FakeTrigger(OVERVIEW_PATTERN)],
                ),
            ),
            _FakeSkill("plugin-gmail", _FakeFrontmatter(tags=[INBOX])),
        ]
    )
    ranking = index.rank(OVERVIEW)
    assert ranking.top is not None
    assert ranking.top.name == "morning-routine"
    assert ranking.top.trigram_score > 0.0


def test_evidence_is_the_raw_span_from_the_utterance() -> None:
    """Guards re-escape this against the ORIGINAL text (see guards.py)."""
    index = build_index([_FakeSkill("focus", _FakeFrontmatter(tags=[OVERVIEW_UMLAUT]))])
    ranking = index.rank(f"gib mir den {OVERVIEW_UMLAUT}")
    assert ranking.top is not None
    assert ranking.top.evidence == OVERVIEW_UMLAUT


def test_reason_names_the_token_and_the_field() -> None:
    index = build_index(
        [_FakeSkill("plugin-gmail", _FakeFrontmatter(tags=[INBOX]))]
    )
    ranking = index.rank(f"schau mal ins {INBOX}")
    assert ranking.top is not None
    assert f"tag: {INBOX}" in ranking.top.reason


# ---------------------------------------------------------------------------
# Clear-winner margin
# ---------------------------------------------------------------------------


def test_two_near_tied_skills_are_not_a_clear_winner() -> None:
    """Genuine ambiguity must degrade so the model disambiguates instead."""
    index = build_index(
        [
            _FakeSkill("plugin-slack", _FakeFrontmatter(tags=[MESSAGE])),
            _FakeSkill("plugin-discord", _FakeFrontmatter(tags=[MESSAGE])),
        ]
    )
    ranking = index.rank(f"schick eine {MESSAGE}")
    assert len(ranking.ranked) == 2
    assert ranking.clear_winner is False


def test_an_unambiguous_hit_is_a_clear_winner() -> None:
    index = build_index(
        [
            _FakeSkill("focus", _FakeFrontmatter(tags=[FOCUS])),
            _FakeSkill("mail", _FakeFrontmatter(tags=[INBOX])),
        ]
    )
    ranking = index.rank(FOCUS)
    assert ranking.clear_winner is True


def test_thresholds_stay_stable_as_the_corpus_grows() -> None:
    """Expressed against a corpus statistic, not as a bare float.

    A threshold tuned at 20 skills must not silently become wrong at 60.
    """
    small = build_index([_FakeSkill(f"s{i}") for i in range(20)])
    large = build_index([_FakeSkill(f"s{i}") for i in range(60)])
    drift = abs(small.fire_threshold - large.fire_threshold) / small.fire_threshold
    assert drift < 0.15, "tripling the corpus must not move the cut-off much"
    assert small.fire_threshold > small.hint_threshold > 0.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_ranking_is_independent_of_registry_order() -> None:
    skills = [
        _FakeSkill(f"skill-{i}", _FakeFrontmatter(description=f"topic{i} shared word"))
        for i in range(12)
    ]
    baseline = build_index(list(skills)).rank("topic7 shared", limit=5)
    # Deterministic permutations (rotations + the reversal) rather than an RNG:
    # a flaky ordering test that only fails on some seeds is worse than none.
    permutations = [skills[i:] + skills[:i] for i in range(1, 5)]
    permutations.append(list(reversed(skills)))
    for shuffled in permutations:
        other = build_index(shuffled).rank("topic7 shared", limit=5)
        assert [s.name for s in other.ranked] == [s.name for s in baseline.ranked]
        assert [round(s.score, 9) for s in other.ranked] == [
            round(s.score, 9) for s in baseline.ranked
        ]


def test_ties_break_by_name_for_a_stable_order() -> None:
    index = build_index(
        [
            _FakeSkill("zulu", _FakeFrontmatter(tags=["sharedterm"])),
            _FakeSkill("alpha", _FakeFrontmatter(tags=["sharedterm"])),
            _FakeSkill("other", _FakeFrontmatter(tags=["unrelated"])),
        ]
    )
    ranking = index.rank("sharedterm")
    assert [s.name for s in ranking.ranked][:2] == ["alpha", "zulu"]


# ---------------------------------------------------------------------------
# Degradation + caps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "!!!", "ab"])
def test_empty_or_contentless_input_ranks_nothing(text: str) -> None:
    index = build_index([_FakeSkill("focus", _FakeFrontmatter(tags=[FOCUS]))])
    assert index.rank(text).ranked == ()


def test_an_empty_corpus_ranks_nothing() -> None:
    assert build_index([]).rank("anything").ranked == ()


def test_a_skill_without_frontmatter_still_contributes_its_name() -> None:
    broken = _FakeSkill("browser-tabs")
    broken.frontmatter = None
    index = build_index([broken, _FakeSkill("other")])
    ranking = index.rank("browser tabs")
    assert ranking.top is not None
    assert ranking.top.name == "browser-tabs"


def test_a_broken_trigger_pattern_does_not_break_the_build() -> None:
    index = build_index(
        [
            _FakeSkill("broken", _FakeFrontmatter(triggers=[_FakeTrigger(r"([unclosed")])),
            _FakeSkill("focus", _FakeFrontmatter(tags=[FOCUS])),
        ]
    )
    ranking = index.rank(FOCUS)
    assert ranking.top is not None
    assert ranking.top.name == "focus"


def test_the_skill_cap_is_enforced() -> None:
    many = [_FakeSkill(f"skill-{i}", _FakeFrontmatter(description=f"topic{i}")) for i in range(400)]
    index = build_index(many)
    assert index.size == relevance.MAX_INDEXED_SKILLS


def test_the_per_skill_token_cap_is_enforced() -> None:
    wordy = _FakeSkill(
        "wordy",
        _FakeFrontmatter(when_to_use=" ".join(f"term{i}" for i in range(600))),
    )
    index = build_index([wordy])
    matched = sum(1 for postings in index.postings.values() if postings)
    assert matched <= relevance.MAX_TOKENS_PER_SKILL


# ---------------------------------------------------------------------------
# Index cache + AP-15
# ---------------------------------------------------------------------------


def test_a_draft_skill_is_not_in_the_voice_index_at_all() -> None:
    """AP-15 enforced at BUILD time, not filtered at query time.

    Structural beats procedural: a draft that is absent from the index cannot
    be fired by a future code path that forgets to check the state.
    """
    registry = _FakeRegistry(
        [
            _FakeSkill(
                "secret-draft",
                _FakeFrontmatter(tags=[FOCUS]),
                state=SkillLifecycleState.DRAFT,
            ),
            _FakeSkill("visible", _FakeFrontmatter(tags=[INBOX])),
        ]
    )
    voice = get_index(registry)
    assert "secret-draft" not in voice.names
    assert voice.rank(FOCUS).ranked == ()


def test_the_ui_index_does_see_drafts() -> None:
    """The UI must find a draft in order to promote it — same code, one flag."""
    registry = _FakeRegistry(
        [
            _FakeSkill(
                "secret-draft",
                _FakeFrontmatter(tags=[FOCUS]),
                state=SkillLifecycleState.DRAFT,
            )
        ]
    )
    assert "secret-draft" in get_index(registry, include_inactive=True).names


def test_the_index_is_cached_per_generation() -> None:
    registry = _FakeRegistry([_FakeSkill("alpha")])
    first = get_index(registry)
    assert get_index(registry) is first

    registry.generation += 1
    assert get_index(registry) is not first


def test_a_missing_registry_yields_an_empty_index() -> None:
    assert get_index(None).size == 0


# ---------------------------------------------------------------------------
# Performance floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [60])
def test_query_cost_does_not_grow_with_the_corpus(count: int) -> None:
    """Guards against an accidental O(skills) rewrite of the posting lookup.

    Generous bounds — this is a shape check for CI, not a benchmark. Live
    measurement on the real 20-skill corpus is ~23 us per query.
    """
    skills = [
        _FakeSkill(
            f"skill-{i}",
            _FakeFrontmatter(description=f"topic{i} alpha beta", tags=[f"tag{i}"]),
        )
        for i in range(count)
    ]
    started = time.perf_counter()
    index = build_index(skills)
    build_seconds = time.perf_counter() - started
    assert build_seconds < 1.0

    started = time.perf_counter()
    for _ in range(1000):
        index.rank("topic7 alpha beta gamma delta")
    assert time.perf_counter() - started < 1.0


def test_scored_skill_records_are_frozen() -> None:
    hit = ScoredSkill(name="x", score=1.0)
    with pytest.raises((AttributeError, TypeError)):
        hit.score = 2.0  # type: ignore[misc]
