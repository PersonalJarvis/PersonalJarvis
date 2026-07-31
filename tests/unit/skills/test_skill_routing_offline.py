"""The blocking routing gate — precision AND recall, no provider required.

This is the AP-27 tripwire. The wake-word subsystem twice destroyed its own
recall while tightening precision, and both times the collapse was invisible
because only precision was being watched: "fires when it shouldn't" and "never
fires" share one root, so a fix aimed at one silently eats the other.

Three assertions of DIFFERENT kinds, deliberately:

1. recall floor      — every golden positive still reaches its skill.
2. precision floor   — zero captures on negatives.
3. no-regression     — both numbers compared against a COMMITTED baseline.

(1) and (2) alone become a rubber stamp the moment someone tunes a threshold
until green. (3) alone lets both numbers drift down together. Together, a
precision fix that costs recall cannot land without literally writing the lower
number into BASELINE.json in the same change — which puts it in the diff and in
front of a reviewer.

Plus the anti-rubber-stamp tests, which are what decide whether any of this is
real. The first run of this eval reported 100 % recall while 31 of 43 positives
were merely echoing an unanchored plugin trigger — they would have passed in
2025 and proved nothing. That is why every positive now declares the channel
that must handle it.
"""
from __future__ import annotations

import json
import re

import pytest

from jarvis.skills import eval_harness
from jarvis.skills.eval_harness import (
    BASELINE_PATH,
    builtin_registry,
    format_report,
    load_golden,
    run_eval,
)
from jarvis.skills.match_eval import MATCH_BANDS, MATCH_SOURCES

#: Known gaps are reported but not scored. Bounded so the field cannot become
#: a place to bury failures — past this many, fix the skills instead.
MAX_KNOWN_GAPS = 6

#: Minimum sizes, so the set cannot be quietly hollowed out.
MIN_POSITIVES_PER_SKILL = 1
MIN_RELEVANCE_POSITIVES = 10
MIN_GLOBAL_NEGATIVES = 15


@pytest.fixture(scope="module")
def report() -> eval_harness.Report:
    return run_eval()


@pytest.fixture(scope="module")
def golden() -> eval_harness.GoldenSet:
    return load_golden()


# ---------------------------------------------------------------------------
# 1 + 2: the floors
# ---------------------------------------------------------------------------


def test_recall_floor_every_positive_reaches_its_skill(
    report: eval_harness.Report,
) -> None:
    misses = [o for o in report.scored_positives if not o.passed]
    assert not misses, "\n" + format_report(report)


def test_precision_floor_no_negative_is_ever_captured(
    report: eval_harness.Report,
) -> None:
    """A NARROW suggestion on a negative is fine — the model still decides.

    Only a capture (band FIRE, surviving the guards) counts as a failure,
    because only a capture rewrites the turn's instructions.
    """
    assert not report.false_fires, "\n" + format_report(report)


# ---------------------------------------------------------------------------
# 3: no regression against the committed baseline
# ---------------------------------------------------------------------------


def test_no_regression_vs_baseline(report: eval_harness.Report) -> None:
    """Recall may not drop without editing BASELINE.json in the same change.

    That is the whole mechanism: a threshold tweak that buys precision by
    quietly costing recall becomes a visible number in the diff.
    """
    assert BASELINE_PATH.exists(), (
        f"{BASELINE_PATH.name} is missing — regenerate it with "
        "`python scripts/skill_relevance_calibrate.py --write-baseline`"
    )
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert report.recall >= baseline["macro_recall"] - 1e-9, (
        f"overall recall dropped from {baseline['macro_recall']:.0%} to "
        f"{report.recall:.0%}\n" + format_report(report)
    )
    assert report.relevance_recall >= baseline["relevance_recall"] - 1e-9, (
        f"RELEVANCE-channel recall dropped from "
        f"{baseline['relevance_recall']:.0%} to {report.relevance_recall:.0%} — "
        "this is the number that says whether the paraphrase layer works\n"
        + format_report(report)
    )
    assert len(report.false_fires) <= baseline["false_fires"], (
        "false fires increased\n" + format_report(report)
    )

    regressed = {
        skill: value
        for skill, value in report.per_skill_recall().items()
        if value < baseline["per_skill"].get(skill, 0.0) - 1e-9
    }
    assert not regressed, f"per-skill recall regressed: {regressed}"


# ---------------------------------------------------------------------------
# Anti-rubber-stamp — the tests that test the tests
# ---------------------------------------------------------------------------


def test_positives_route_through_their_declared_channel(
    report: eval_harness.Report,
) -> None:
    """A relevance positive must not be rescued by an author's regex.

    Without this, the eval measures 2025's behaviour and calls it a win: on the
    first run 31 of 43 positives were unanchored plugin-trigger echoes.
    """
    assert not report.wrong_channel, "\n" + format_report(report)


def test_the_relevance_channel_carries_real_weight(
    report: eval_harness.Report, golden: eval_harness.GoldenSet
) -> None:
    """Enough positives must actually exercise the new layer."""
    relevance = [p for p in golden.positives if p.channel == "relevance"]
    assert len(relevance) >= MIN_RELEVANCE_POSITIVES
    assert report.relevance_recall > 0.0


def test_a_relevance_positive_does_not_match_its_own_trigger_regex(
    golden: eval_harness.GoldenSet,
) -> None:
    """Structural version of the anti-echo rule, independent of the matcher.

    Checked against the raw regex rather than the match result, so it still
    holds if the trigger matcher itself is changed.
    """
    registry = builtin_registry()
    echoes: list[str] = []
    for positive in golden.positives:
        if positive.channel != "relevance":
            continue
        try:
            skill = registry.get(positive.skill)
        except KeyError:
            continue
        frontmatter = getattr(skill, "frontmatter", None)
        if frontmatter is None:
            continue
        for trigger in frontmatter.triggers:
            if trigger.type != "voice" or not trigger.pattern:
                continue
            try:
                if re.search(trigger.pattern, positive.text, re.IGNORECASE):
                    echoes.append(f"{positive.skill}: {positive.text!r}")
            except re.error:
                continue
    assert not echoes, (
        "these 'relevance' positives are really trigger echoes and prove "
        "nothing about the new layer:\n  " + "\n  ".join(echoes)
    )


def test_the_eval_detects_a_broken_matcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """An eval that stays green against a crippled matcher is decoration.

    Cripple the scorer so it can never rank anything, and assert the suite
    NOTICES. This is the only test here that tests the test.
    """
    from jarvis.skills import relevance

    class _DeadIndex:
        names = ()

        def rank(self, text: str, *, limit: int = 5):  # noqa: ANN202, ARG002
            return relevance.RelevanceRanking()

    monkeypatch.setattr(relevance, "get_index", lambda *a, **k: _DeadIndex())
    crippled = run_eval()

    assert crippled.relevance_recall == 0.0, (
        "the eval did not notice a completely dead relevance scorer — "
        "it is measuring the trigger path only"
    )
    assert crippled.recall < 1.0


# ---------------------------------------------------------------------------
# Fixture hygiene
# ---------------------------------------------------------------------------


def test_known_gaps_are_bounded(report: eval_harness.Report) -> None:
    """`known_gap` must stay an honest note, not a dumping ground."""
    assert len(report.known_gaps) <= MAX_KNOWN_GAPS


def test_every_golden_skill_exists_in_the_registry(
    golden: eval_harness.GoldenSet,
) -> None:
    registry = builtin_registry()
    known = {s.name for s in registry.list()}
    unknown = [s for s in golden.skills if s not in known]
    assert not unknown, f"golden set names skills that no longer exist: {unknown}"


def test_every_builtin_with_a_trigger_has_a_golden_entry(
    golden: eval_harness.GoldenSet,
) -> None:
    """A new builtin must not land without routing coverage."""
    registry = builtin_registry()
    covered = set(golden.skills)
    missing: list[str] = []
    for skill in registry.list():
        frontmatter = getattr(skill, "frontmatter", None)
        if frontmatter is None or skill.name in covered:
            continue
        if getattr(frontmatter, "state", None) == "disabled":
            continue
        if any(t.type == "voice" for t in frontmatter.triggers):
            missing.append(skill.name)
    assert not missing, (
        "these builtin skills have a voice trigger but no golden entry: "
        f"{missing} — add positives to tests/fixtures/skill_routing/golden.yaml"
    )


def test_the_golden_set_is_big_enough(golden: eval_harness.GoldenSet) -> None:
    assert len(golden.global_negatives) >= MIN_GLOBAL_NEGATIVES
    per_skill: dict[str, int] = {}
    for positive in golden.positives:
        per_skill[positive.skill] = per_skill.get(positive.skill, 0) + 1
    thin = {k: v for k, v in per_skill.items() if v < MIN_POSITIVES_PER_SKILL}
    assert not thin, f"skills with too few positives: {thin}"


def test_fixture_values_are_in_the_closed_vocabularies(
    golden: eval_harness.GoldenSet,
) -> None:
    for positive in golden.positives:
        assert positive.min_band in MATCH_BANDS, positive.text
        assert positive.channel in MATCH_SOURCES, positive.text


def test_negatives_carry_a_reason(golden: eval_harness.GoldenSet) -> None:
    """A negative without a stated reason cannot be reviewed later."""
    missing = [n.text for n in golden.global_negatives if not n.reason.strip()]
    assert not missing, f"global negatives with no reason: {missing}"
