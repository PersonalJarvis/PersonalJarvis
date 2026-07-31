"""Offline routing eval — provider-free, importable, CI-runnable.

The point of this module is that it calls **exactly** the functions the runtime
calls: :func:`jarvis.skills.match_eval.evaluate_match` for the decision and
:func:`jarvis.skills.guards.evaluate_guards` for the vetoes. A harness that
re-implements the algorithm measures the harness, not the product — and the
number it prints would drift away from reality without anyone noticing.

It replaces nothing: ``scripts/skill_routing_eval.py`` still measures the
LLM-judged path against a live provider, which is a different question. This
one measures the deterministic layer, needs no key, no network and no
``BrainManager``, and therefore can block a pull request.

Why both precision and recall, always, in the same report: the wake-word
subsystem twice destroyed its own recall while tightening precision, and the
collapse was invisible because only precision was being watched (AP-27). A
report that shows one number is how that happens again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.skills.guards import evaluate_guards
from jarvis.skills.match_eval import BAND_NONE, band_at_least, evaluate_match

#: Repo-relative location of the golden set.
GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "skill_routing"
    / "golden.yaml"
)
BASELINE_PATH = GOLDEN_PATH.parent / "BASELINE.json"


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Positive:
    skill: str
    text: str
    lang: str = "auto"
    min_band: str = "narrow"
    source: str = "authored"
    #: Which layer MUST handle this utterance: ``trigger`` (the author's own
    #: regex) or ``relevance`` (the deterministic paraphrase channel).
    #:
    #: This field exists because the first run of this eval reported 100 %
    #: recall while 31 of 43 positives were merely echoing an unanchored
    #: plugin trigger — they would have passed in 2025 and proved nothing
    #: about the new layer. Declaring the channel makes that impossible to
    #: hide: a relevance positive silently falling back to a trigger match
    #: (or the reverse) now fails.
    channel: str = "relevance"
    #: A known, accepted gap. Reported prominently, never counted as a pass,
    #: never fails CI. Bounded by a test so it cannot become a dumping ground.
    known_gap: bool = False


@dataclass(frozen=True, slots=True)
class Negative:
    text: str
    reason: str = ""
    #: Skill this is a hard negative FOR; empty means "for every skill".
    skill: str = ""


@dataclass(frozen=True, slots=True)
class GoldenSet:
    positives: tuple[Positive, ...] = ()
    hard_negatives: tuple[Negative, ...] = ()
    global_negatives: tuple[Negative, ...] = ()

    @property
    def skills(self) -> tuple[str, ...]:
        return tuple(sorted({p.skill for p in self.positives}))


def load_golden(path: Path | None = None) -> GoldenSet:
    """Parse the golden YAML. Raises loudly — a broken fixture must fail CI."""
    import yaml

    raw = yaml.safe_load((path or GOLDEN_PATH).read_text(encoding="utf-8")) or {}
    positives: list[Positive] = []
    hard: list[Negative] = []
    for skill, entry in (raw.get("skills") or {}).items():
        for item in (entry or {}).get("positives") or ():
            positives.append(
                Positive(
                    skill=skill,
                    text=str(item["text"]),
                    lang=str(item.get("lang", "auto")),
                    min_band=str(item.get("min_band", "narrow")),
                    source=str(item.get("source", "authored")),
                    channel=str(item.get("channel", "relevance")),
                    known_gap=bool(item.get("known_gap", False)),
                )
            )
        for item in (entry or {}).get("hard_negatives") or ():
            hard.append(
                Negative(
                    text=str(item["text"]),
                    reason=str(item.get("reason", "")),
                    skill=skill,
                )
            )
    globals_ = [
        Negative(text=str(item["text"]), reason=str(item.get("reason", "")))
        for item in (raw.get("global_negatives") or ())
    ]
    return GoldenSet(
        positives=tuple(positives),
        hard_negatives=tuple(hard),
        global_negatives=tuple(globals_),
    )


# ---------------------------------------------------------------------------
# Registry under test — repo builtins ONLY
# ---------------------------------------------------------------------------


class _StaticRegistry:
    """Minimal registry over a fixed skill list (no watchdog, no disk watch)."""

    def __init__(self, skills: list[Any]) -> None:
        self._skills = list(skills)
        self.generation = 1

    def list(self) -> list[Any]:
        return list(self._skills)

    def list_active(self) -> list[Any]:
        from jarvis.skills.schema import SkillLifecycleState

        return [
            s
            for s in self._skills
            if s.state
            in (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)
        ]

    def by_trigger(self, kind: str) -> list[Any]:
        out = []
        for skill in self._skills:
            frontmatter = getattr(skill, "frontmatter", None)
            if frontmatter is None:
                continue
            if any(t.type == kind for t in frontmatter.triggers):
                out.append(skill)
        return out

    def get(self, name: str) -> Any:
        for skill in self._skills:
            if skill.name == name:
                return skill
        raise KeyError(name)


def builtin_registry() -> _StaticRegistry:
    """A registry over the SHIPPED builtin skills.

    Two deliberate exclusions, both about reproducibility:

    * Never ``user_skills_dir()`` — CI has no user directory, and the
      maintainer's own half-finished skills must not be able to turn a shared
      pipeline red.
    * Only names in ``BUILTIN_SKILL_NAMES``, not everything on disk. The
      working tree is shared with parallel sessions, so ``builtin/`` routinely
      contains untracked in-flight skills that do not exist in a fresh clone.
      Scoring against them would make the committed baseline unreproducible in
      CI and let another session's work-in-progress fail this gate.
    """
    from jarvis.skills.builtin import BUILTIN_SKILL_NAMES
    from jarvis.skills.loader import discover_skills

    root = Path(__file__).resolve().parent / "builtin"
    shipped = set(BUILTIN_SKILL_NAMES)
    return _StaticRegistry(
        [s for s in discover_skills(root) if s.name in shipped]
    )


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the matcher actually did for one utterance."""

    text: str
    expected: str
    winner: str
    band: str
    source: str
    score: float
    vetoed_by: str
    passed: bool
    detail: str = ""
    #: Channel that actually handled it, vs the one the fixture demanded.
    channel: str = ""
    expected_channel: str = ""
    known_gap: bool = False


@dataclass
class Report:
    positives: list[Outcome] = field(default_factory=list)
    negatives: list[Outcome] = field(default_factory=list)

    @property
    def scored_positives(self) -> list[Outcome]:
        """Positives that count — known gaps are reported, never scored."""
        return [o for o in self.positives if not o.known_gap]

    @property
    def known_gaps(self) -> list[Outcome]:
        return [o for o in self.positives if o.known_gap]

    @property
    def recall(self) -> float:
        scored = self.scored_positives
        if not scored:
            return 0.0
        return sum(o.passed for o in scored) / len(scored)

    @property
    def relevance_recall(self) -> float:
        """Recall over ONLY the positives the new layer is responsible for.

        This is the number that says whether the paraphrase channel works.
        Overall recall is inflated by the author-written triggers, which
        already worked before any of this existed.
        """
        scored = [o for o in self.scored_positives if o.expected_channel == "relevance"]
        if not scored:
            return 0.0
        return sum(o.passed for o in scored) / len(scored)

    @property
    def wrong_channel(self) -> list[Outcome]:
        return [
            o
            for o in self.scored_positives
            if o.winner and o.channel and o.channel != o.expected_channel
        ]

    @property
    def false_fires(self) -> list[Outcome]:
        return [o for o in self.negatives if not o.passed]

    @property
    def precision_ok(self) -> bool:
        return not self.false_fires

    def per_skill_recall(self) -> dict[str, float]:
        buckets: dict[str, list[bool]] = {}
        for outcome in self.positives:
            buckets.setdefault(outcome.expected, []).append(outcome.passed)
        return {
            skill: sum(values) / len(values) for skill, values in sorted(buckets.items())
        }


def _decide(registry: Any, text: str, lang: str = "auto") -> tuple[Any, str]:
    """Run the real matcher + the real guards. Returns (decision, veto_reason)."""
    decision = evaluate_match(registry, text, lang=lang)
    if decision.top is None:
        return decision, ""
    try:
        skill = registry.get(decision.top.skill_name)
    except Exception:  # noqa: BLE001
        return decision, ""
    ladder = evaluate_guards(
        skill,
        user_text=text,
        evidence=decision.top.evidence,
    )
    return decision, ladder.vetoed_by


def run_eval(golden: GoldenSet | None = None, registry: Any | None = None) -> Report:
    """Score the golden set against the live deterministic matcher."""
    golden = golden or load_golden()
    registry = registry or builtin_registry()
    report = Report()

    for positive in golden.positives:
        decision, veto = _decide(registry, positive.text, positive.lang)
        winner = decision.top.skill_name if decision.top else ""
        effective_band = BAND_NONE if veto else decision.band
        passed = (
            not veto
            and winner == positive.skill
            and band_at_least(effective_band, positive.min_band)
            and decision.source == positive.channel
        )
        report.positives.append(
            Outcome(
                text=positive.text,
                expected=positive.skill,
                winner=winner,
                band=effective_band,
                source=decision.source,
                score=decision.top.score if decision.top else 0.0,
                vetoed_by=veto,
                passed=passed,
                channel=decision.source,
                expected_channel=positive.channel,
                known_gap=positive.known_gap,
            )
        )

    # A negative fails only when the matcher would CAPTURE the turn — a
    # narrow-band suggestion is free, because the model still decides.
    def _check_negative(text: str, reason: str, only_for: str = "") -> None:
        decision, veto = _decide(registry, text)
        winner = decision.top.skill_name if decision.top else ""
        fires = bool(decision.fired and not veto)
        if only_for:
            fires = fires and winner == only_for
        report.negatives.append(
            Outcome(
                text=text,
                expected=only_for or "(nothing)",
                winner=winner,
                band=BAND_NONE if veto else decision.band,
                source=decision.source,
                score=decision.top.score if decision.top else 0.0,
                vetoed_by=veto,
                passed=not fires,
                detail=reason,
            )
        )

    for negative in golden.global_negatives:
        _check_negative(negative.text, negative.reason)
    for negative in golden.hard_negatives:
        _check_negative(negative.text, negative.reason, only_for=negative.skill)

    # Cross-skill negatives, generated: every positive is a hard negative for
    # every OTHER skill. Free coverage, and the best defence against two skills
    # whose vocabulary collides.
    for positive in golden.positives:
        decision, veto = _decide(registry, positive.text, positive.lang)
        if not decision.fired or veto:
            continue
        winner = decision.top.skill_name if decision.top else ""
        if winner == positive.skill:
            continue
        report.negatives.append(
            Outcome(
                text=positive.text,
                expected=positive.skill,
                winner=winner,
                band=decision.band,
                source=decision.source,
                score=decision.top.score if decision.top else 0.0,
                vetoed_by=veto,
                passed=False,
                detail=f"cross-skill: fired {winner} instead of {positive.skill}",
            )
        )

    return report


def format_report(report: Report) -> str:
    """Human-readable summary — printed by CI so a red run names the skill."""
    lines: list[str] = []
    lines.append("Skill routing eval (deterministic layer, no provider)")
    lines.append("=" * 62)
    scored = report.scored_positives
    relevance = [o for o in scored if o.expected_channel == "relevance"]
    lines.append(
        f"  recall (all)      : {report.recall:.0%} "
        f"({sum(o.passed for o in scored)}/{len(scored)})"
    )
    lines.append(
        f"  recall (relevance): {report.relevance_recall:.0%} "
        f"({sum(o.passed for o in relevance)}/{len(relevance)})   <- the new layer"
    )
    lines.append(
        f"  false fires       : {len(report.false_fires)} "
        f"of {len(report.negatives)} negatives"
    )
    if report.known_gaps:
        lines.append(f"  known gaps        : {len(report.known_gaps)} (reported, not scored)")
    lines.append("")
    lines.append("  per-skill recall:")
    for skill, value in report.per_skill_recall().items():
        flag = "  " if value >= 0.5 else "!!"
        lines.append(f"   {flag} {skill:26} {value:.0%}")
    misses = [o for o in report.scored_positives if not o.passed]
    if misses:
        lines.append("")
        lines.append("  missed positives:")
        for outcome in misses:
            got = outcome.winner or "(nothing)"
            veto = f" vetoed={outcome.vetoed_by}" if outcome.vetoed_by else ""
            lines.append(
                f"     {outcome.expected:22} <- {outcome.text[:46]:46} "
                f"got {got} [{outcome.band}]{veto}"
            )
    if report.wrong_channel:
        lines.append("")
        lines.append("  WRONG CHANNEL (a fixture claim no longer holds):")
        for outcome in report.wrong_channel:
            lines.append(
                f"     {outcome.text[:44]:44} expected {outcome.expected_channel}, "
                f"got {outcome.channel}"
            )
    if report.known_gaps:
        lines.append("")
        lines.append("  known gaps (not scored — the next work item):")
        for outcome in report.known_gaps:
            got = outcome.winner or "(nothing)"
            lines.append(
                f"     {outcome.expected:22} <- {outcome.text[:44]:44} got {got}"
            )
    if report.false_fires:
        lines.append("")
        lines.append("  FALSE FIRES:")
        for outcome in report.false_fires:
            lines.append(
                f"     {outcome.text[:46]:46} -> {outcome.winner} ({outcome.detail})"
            )
    return "\n".join(lines)


__all__ = [
    "BASELINE_PATH",
    "GOLDEN_PATH",
    "GoldenSet",
    "Negative",
    "Outcome",
    "Positive",
    "Report",
    "builtin_registry",
    "format_report",
    "load_golden",
    "run_eval",
]
