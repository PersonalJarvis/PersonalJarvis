"""Calibrate the skill-relevance thresholds — offline, no provider, no network.

Answers one question with a number instead of a hunch: where do FIRE and NARROW
belong?

The operating point is chosen deliberately and stated out loud:

    T_FIRE = the SMALLEST threshold at which false positives over the hard
             negatives is EXACTLY ZERO — and then the resulting recall is
             REPORTED, never hidden.

Zero, not "low", because a wrong capture rewrites the turn's instructions and
removes the model's ability to decline; the maintainer notices that immediately,
while a missed skill is silent. Reporting the recall that this costs is the
other half — it is precisely the number the wake-word subsystem stopped
watching twice, and both times recall collapsed unnoticed (AP-27).

**The standing rule, and it matters more than the numbers:** if the zero-FP
threshold lands above the 60th percentile of the positive score distribution,
the ALGORITHM is not good enough. Fix the field weights or the skills'
vocabulary. Do NOT loosen the threshold, and do not hand-tune a coefficient to
make one over-fire go away — add a hard negative to the golden set and re-run
this.

Usage:
    python scripts/skill_relevance_calibrate.py                 # report
    python scripts/skill_relevance_calibrate.py --write-baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

if sys.platform == "win32":  # UTF-8 stdout; cp1252 is the Windows default
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _guard_veto(registry: object, skill_name: str, text: str, evidence: str) -> str:
    """The veto the runtime's own guards would return, or "" if none."""
    from jarvis.skills.guards import evaluate_guards

    skill = registry.get(skill_name)  # type: ignore[attr-defined]
    return evaluate_guards(skill, user_text=text, evidence=evidence).vetoed_by


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="write tests/fixtures/skill_routing/BASELINE.json from this run",
    )
    args = parser.parse_args()

    from jarvis.skills.eval_harness import (
        BASELINE_PATH,
        builtin_registry,
        format_report,
        load_golden,
        run_eval,
    )
    from jarvis.skills.relevance import get_index

    golden = load_golden()
    registry = builtin_registry()
    index = get_index(registry)
    report = run_eval(golden, registry)

    print(format_report(report))
    print()
    print("Corpus")
    print("=" * 62)
    print(f"  skills indexed     : {index.size}")
    print(f"  distinct tokens    : {len(index.postings)}")
    print(f"  idf(unknown term)  : {index.idf_unknown:.3f}")
    print(f"  T_FIRE (in effect) : {index.fire_threshold:.3f}")
    print(f"  T_HINT (in effect) : {index.hint_threshold:.3f}")

    # --- score distributions ------------------------------------------------
    positive_scores: list[float] = []
    for positive in golden.positives:
        if positive.channel != "relevance" or positive.known_gap:
            continue
        ranking = index.rank(positive.text)
        hit = next((s for s in ranking.ranked if s.name == positive.skill), None)
        if hit is not None:
            positive_scores.append(hit.score)

    # Only negatives that SURVIVE the guards belong in the threshold
    # calculation. This distinction is load-bearing, and getting it wrong is the
    # AP-27 trap in miniature: the five strongest-scoring negatives are all
    # definitional questions ("what is <product>, actually?"), which score high
    # for the correct reason — the utterance really is about that skill's topic —
    # and are suppressed by the definitional-question guard, not by a threshold.
    # Counting them here would recommend raising FIRE from ~1.16 to ~1.59 and
    # cut relevance recall from 100 % to 29 % while the report still claimed
    # "zero false positives". Scoring is content-agnostic; the guards are where
    # semantics belong. Keep them in separate columns.
    negative_scores: list[float] = []
    guarded_away: list[tuple[float, str, str]] = []
    for negative in (*golden.global_negatives, *golden.hard_negatives):
        ranking = index.rank(negative.text)
        if ranking.top is None:
            continue
        winner = ranking.top.name
        veto = ""
        try:
            veto = _guard_veto(registry, winner, negative.text, ranking.top.evidence)
        except Exception:  # noqa: BLE001
            veto = ""
        if veto:
            guarded_away.append((ranking.top.score, winner, veto))
            continue
        negative_scores.append(ranking.top.score)

    print()
    print("Score distributions (relevance channel only)")
    print("=" * 62)
    if positive_scores:
        print(
            f"  positives  n={len(positive_scores):3}  "
            f"min={min(positive_scores):.3f}  p25={_percentile(positive_scores, 0.25):.3f}  "
            f"p60={_percentile(positive_scores, 0.60):.3f}  max={max(positive_scores):.3f}"
        )
    if negative_scores:
        print(
            f"  negatives  n={len(negative_scores):3}  "
            f"min={min(negative_scores):.3f}  p75={_percentile(negative_scores, 0.75):.3f}  "
            f"max={max(negative_scores):.3f}   (guard survivors only)"
        )
    if guarded_away:
        print()
        print(
            f"  {len(guarded_away)} negative(s) scored high but were VETOED by a "
            "guard, not by a threshold —"
        )
        print(
            "  excluded from the calculation on purpose. Folding them in would "
            "recommend a"
        )
        print(
            "  much higher FIRE cut-off and quietly destroy recall (AP-27). "
            "Strongest:"
        )
        for score, winner, veto in sorted(guarded_away, reverse=True)[:5]:
            print(f"     {score:.3f}  {winner:26} vetoed_by={veto}")

    # --- the operating point ------------------------------------------------
    print()
    print("Operating point")
    print("=" * 62)
    zero_fp = (max(negative_scores) if negative_scores else 0.0) + 1e-6
    p60 = _percentile(positive_scores, 0.60)
    print(f"  smallest zero-false-positive T_FIRE : {zero_fp:.3f}")
    print(f"  60th percentile of positives        : {p60:.3f}")
    kept = sum(1 for s in positive_scores if s >= zero_fp)
    total = len(positive_scores) or 1
    print(f"  recall AT that threshold            : {kept}/{total} ({kept / total:.0%})")

    if positive_scores and zero_fp > p60:
        print()
        print("  VERDICT: the zero-FP threshold sits ABOVE the 60th percentile of")
        print("  positives. Per the standing rule this is an ALGORITHM problem, not")
        print("  a threshold problem. Fix field weights or the skills' vocabulary —")
        print("  do NOT loosen the threshold to make this line go away.")
    else:
        print()
        print("  VERDICT: negatives separate cleanly from positives; the configured")
        print("  thresholds have headroom on both sides.")

    if args.write_baseline:
        payload = {
            "note": (
                "Committed baseline for tests/unit/skills/"
                "test_skill_routing_offline.py. Lowering any number here is a "
                "RECALL REGRESSION and must be justified in the same change."
            ),
            "macro_recall": round(report.recall, 6),
            "relevance_recall": round(report.relevance_recall, 6),
            "false_fires": len(report.false_fires),
            "known_gaps": len(report.known_gaps),
            "positives_scored": len(report.scored_positives),
            "negatives": len(report.negatives),
            "per_skill": {
                skill: round(value, 6)
                for skill, value in report.per_skill_recall().items()
            },
        }
        BASELINE_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print()
        print(f"  wrote {BASELINE_PATH.relative_to(REPO)}")

    return 0 if (report.precision_ok and not report.wrong_channel) else 1


if __name__ == "__main__":
    sys.exit(main())
