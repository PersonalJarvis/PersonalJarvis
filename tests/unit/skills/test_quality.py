"""Can this skill ever be FOUND? — the check nothing performed before.

``validate_skill`` asks whether a SKILL.md parses and is safe. These tests cover
the different question: whether a skill carries enough distinctive vocabulary to
be reachable at all. A skill can be perfectly valid, enabled, and invisible.

Every ERROR-level rule here was written against something real on disk, not
imagined: a skill with an empty description and a one-heading body, and a skill
whose vocabulary is English-only while the user speaks German (the routing eval
records that one as a known gap independently).
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.skills import quality


class _FakeRiskPolicy:
    def __init__(self, tier: str = "monitor") -> None:
        self.default_tier = tier


class _FakeTrigger:
    def __init__(self, pattern: str) -> None:
        self.type = "voice"
        self.pattern = pattern


class _FakeFrontmatter:
    def __init__(
        self,
        *,
        description: str = "Runs a focused work sprint with a timer.",
        when_to_use: str = "Use when the user wants to concentrate.",
        category: str = "productivity",
        tags: list[str] | None = None,
        execution: str = "inline",
        triggers: list[Any] | None = None,
        intent_objects: list[str] | None = None,
    ) -> None:
        self.description = description
        self.when_to_use = when_to_use
        self.category = category
        self.tags = tags if tags is not None else ["focus", "timer", "sprint"]
        self.execution = execution
        self.risk_policy = _FakeRiskPolicy()
        self.triggers = triggers or []
        self.intent_verbs: list[str] = []
        self.intent_objects = intent_objects or []


class _FakeSkill:
    def __init__(
        self,
        name: str = "deep-work-mode",
        frontmatter: _FakeFrontmatter | None = None,
        body: str = "# Deep work\n\nSilence notifications and start a timer.\n",
    ) -> None:
        self.name = name
        self.frontmatter = frontmatter if frontmatter is not None else _FakeFrontmatter()
        self.body = body


def _codes(report: quality.QualityReport) -> set[str]:
    return {finding.code for finding in report.findings}


# ---------------------------------------------------------------------------
# A healthy skill
# ---------------------------------------------------------------------------


def test_a_well_formed_skill_reports_nothing() -> None:
    report = quality.lint_skill(_FakeSkill())
    assert report.ok
    assert report.findings == ()


def test_a_broken_frontmatter_is_left_to_the_validator() -> None:
    """Nothing to judge here — a parse failure is validator territory."""
    skill = _FakeSkill()
    skill.frontmatter = None
    assert quality.lint_skill(skill).findings == ()


# ---------------------------------------------------------------------------
# The errors — each one written against something real on disk
# ---------------------------------------------------------------------------


def test_an_empty_description_is_an_error() -> None:
    """The description IS the listing entry the model reads."""
    report = quality.lint_skill(
        _FakeSkill(frontmatter=_FakeFrontmatter(description=""))
    )
    assert quality.Q_EMPTY_DESCRIPTION in _codes(report)
    assert not report.ok


def test_a_body_with_only_a_heading_is_an_error() -> None:
    """A skill that does nothing when it fires is worse than one that misses."""
    report = quality.lint_skill(_FakeSkill(body="## Browser Tabs\n"))
    assert quality.Q_EMPTY_BODY in _codes(report)
    assert not report.ok


def test_too_few_distinctive_terms_is_an_error() -> None:
    """Unreachable by construction — no channel has anything to match on."""
    report = quality.lint_skill(
        _FakeSkill(
            name="x1",
            frontmatter=_FakeFrontmatter(
                description="the and but", when_to_use="", tags=[], category=""
            ),
            body="# x1\n\nthe and but\n",
        )
    )
    assert quality.Q_TOO_FEW_TERMS in _codes(report)


def test_function_words_do_not_count_as_vocabulary() -> None:
    """Uses the shared scorer's own stopword handling, so "matchable" here means
    exactly what the matcher can match."""
    terms = quality.distinctive_terms(
        _FakeSkill(
            name="s",
            frontmatter=_FakeFrontmatter(
                description="der die das und oder aber",  # i18n-allow: speech input
                when_to_use="",
                tags=[],
                category="",
            ),
        )
    )
    assert len(terms) < quality.MIN_DISTINCTIVE_TERMS


def test_a_mission_skill_without_when_to_use_is_unreachable() -> None:
    """It can never auto-fire (it starts a process), so the model must be able
    to CHOOSE it — and it chooses from when_to_use."""
    report = quality.lint_skill(
        _FakeSkill(
            name="cloud-debug",
            frontmatter=_FakeFrontmatter(execution="mission", when_to_use=""),
        )
    )
    assert quality.Q_MISSION_WITHOUT_WHEN in _codes(report)
    assert not report.ok


# ---------------------------------------------------------------------------
# The warnings
# ---------------------------------------------------------------------------


def test_a_display_string_name_is_flagged() -> None:
    """Functional, not stylistic: run-skill looks skills up BY NAME, so a skill
    called "Browser Tabs" cannot be invoked."""
    report = quality.lint_skill(_FakeSkill(name="Browser Tabs"))
    assert quality.Q_NAME_NOT_A_SLUG in _codes(report)


@pytest.mark.parametrize("name", ["deep-work-mode", "plugin-google_calendar", "cli-gcloud"])
def test_real_slugs_are_accepted(name: str) -> None:
    assert quality.Q_NAME_NOT_A_SLUG not in _codes(quality.lint_skill(_FakeSkill(name=name)))


def test_a_missing_when_to_use_is_a_warning_not_an_error() -> None:
    """control-api's actual defect — and it must not block a save."""
    report = quality.lint_skill(_FakeSkill(frontmatter=_FakeFrontmatter(when_to_use="")))
    assert quality.Q_NO_WHEN_TO_USE in _codes(report)
    assert report.ok, "a warning must never block"


def test_colliding_vocabulary_is_flagged() -> None:
    """plugin-discord and plugin-slack really are ~76 % identical, which is why
    "send a message" is ambiguous between them."""
    first = _FakeSkill(
        name="plugin-slack",
        frontmatter=_FakeFrontmatter(
            description="Send and read messages in a workspace channel.",
            when_to_use="Use for channel messages.",
            tags=["messages", "channel"],
        ),
    )
    second = _FakeSkill(
        name="plugin-discord",
        frontmatter=_FakeFrontmatter(
            description="Send and read messages in a workspace channel.",
            when_to_use="Use for channel messages.",
            tags=["messages", "channel"],
        ),
    )
    report = quality.lint_skill(first, peers=[first, second])
    assert quality.Q_TERM_COLLISION in _codes(report)
    assert report.ok, "collision is advisory — both skills may be legitimate"


def test_distinct_skills_do_not_collide() -> None:
    first = _FakeSkill(name="deep-work-mode")
    second = _FakeSkill(
        name="plugin-gmail",
        frontmatter=_FakeFrontmatter(
            description="Read and send email from the inbox.",
            when_to_use="Use for mail.",
            tags=["mail", "inbox"],
        ),
    )
    report = quality.lint_skill(first, peers=[first, second])
    assert quality.Q_TERM_COLLISION not in _codes(report)


def test_a_very_short_bare_trigger_is_flagged() -> None:
    report = quality.lint_skill(
        _FakeSkill(frontmatter=_FakeFrontmatter(triggers=[_FakeTrigger("ab")]))
    )
    assert quality.Q_GREEDY_TRIGGER in _codes(report)


def test_a_real_alternation_trigger_is_fine() -> None:
    report = quality.lint_skill(
        _FakeSkill(
            frontmatter=_FakeFrontmatter(
                triggers=[_FakeTrigger("(fokusmodus|deep work mode)")]  # i18n-allow
            )
        )
    )
    assert quality.Q_GREEDY_TRIGGER not in _codes(report)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_only_errors_block() -> None:
    report = quality.lint_skill(_FakeSkill(frontmatter=_FakeFrontmatter(when_to_use="")))
    assert report.warnings
    assert not report.errors
    assert report.ok


def test_every_finding_code_is_in_the_closed_vocabulary() -> None:
    cases = [
        _FakeSkill(frontmatter=_FakeFrontmatter(description="")),
        _FakeSkill(body="# only a heading\n"),
        _FakeSkill(name="Display Name"),
        _FakeSkill(frontmatter=_FakeFrontmatter(execution="mission", when_to_use="")),
        _FakeSkill(frontmatter=_FakeFrontmatter(triggers=[_FakeTrigger("ab")])),
    ]
    for skill in cases:
        for finding in quality.lint_skill(skill).findings:
            assert finding.code in quality.QUALITY_CODES
            assert finding.severity in ("error", "warning")


def test_every_finding_carries_an_actionable_hint() -> None:
    """A report the maintainer cannot act on is noise."""
    report = quality.lint_skill(
        _FakeSkill(name="Browser Tabs", frontmatter=_FakeFrontmatter(description=""))
    )
    assert report.findings
    assert all(f.message.strip() for f in report.findings)
    assert all(f.hint.strip() for f in report.findings)


def test_the_report_serialises_for_the_rest_layer() -> None:
    payload = quality.lint_skill(_FakeSkill()).as_dict()
    assert payload["skill_name"] == "deep-work-mode"
    assert payload["ok"] is True
    assert payload["findings"] == []


def test_lint_registry_reports_each_skill_against_its_peers() -> None:
    skills = [_FakeSkill(name=f"skill-{i}") for i in range(3)]
    reports = quality.lint_registry(skills)
    assert len(reports) == 3
    assert [r.skill_name for r in reports] == ["skill-0", "skill-1", "skill-2"]


def test_linting_never_raises_on_a_degenerate_skill() -> None:
    class _Bare:
        pass

    assert isinstance(quality.lint_skill(_Bare()), quality.QualityReport)


def test_the_loader_is_not_involved() -> None:
    """Enforce at the WRITE boundary, report at the READ boundary, never at LOAD.

    A content rule applied at load time suppresses the skills nobody anticipated
    along with the bad ones — silently, at boot, with no signal (AP-27).
    """
    from pathlib import Path

    import jarvis.skills.loader as loader

    source = Path(loader.__file__).read_text(encoding="utf-8")
    assert "quality" not in source
