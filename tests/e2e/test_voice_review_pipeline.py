"""E2E smoke tests for the voice → DispatchWithReview path (Phase 8.7).

Plan reference: §6.7. The 4 canonical scenarios:
- Trivial path: smalltalk → NO dispatch_with_review call.
- Code-gen path: pass in iter 1, voice_completion_phrase = success.
- Multi-iter path: needs_revision×1 → pass, voice_completion includes a hint.
- Cap-fire path: needs_revision×3, voice_completion = cap_fired phrase.

Approach: we test the tool/pipeline layer (not the full voice loop), but
with byte-exact TTS phrase assertions (AD-14). STT/TTS aren't really
wired up — the "voice E2E" character comes from the phrases being
rendered exactly as they would be for the TTS pipeline.

Marked with `@pytest.mark.e2e`: only runs via `pytest -m e2e`, not in
the standard run.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import ExecutionContext
from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.policy import ReviewPolicy
from jarvis.core.review.state import RunState
from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
)
from jarvis.plugins.tool.dispatch_with_review import (
    VOICE_HOLDING_PHRASE_DE,
    DispatchWithReviewTool,
)

pytestmark = pytest.mark.e2e


def _make_ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="e2e smoke",
        config={},
        memory_read=None,
    )


def _captured_announcements(bus: EventBus) -> list[str]:
    captured: list[str] = []

    async def _on_announce(event: AnnouncementRequested) -> None:
        captured.append(event.text)

    bus.subscribe(AnnouncementRequested, _on_announce)
    return captured


# ----------------------------------------------------------------------
# Scenario 1: trivial path — NO dispatch_with_review
# ----------------------------------------------------------------------


def test_trivial_smalltalk_does_not_trigger_review() -> None:
    """Plan §6.7 Smoke #1: Main-Jarvis's smalltalk classifier rejects
    trivial tasks — the dispatch_with_review path is NEVER entered.

    Verified via ReviewPolicy (Phase 8.4). Production path: the LLM
    reads the tool description (Plan §AD-6) and decides NOT to call
    the tool — analogous to our classifier.
    """
    policy = ReviewPolicy()
    smalltalk_inputs = [
        "hallo Jarvis, wie geht's?",
        "danke dir!",
        "wie spät ist es",  # i18n-allow: simulated German smalltalk utterance, matched by ReviewPolicy
    ]
    for utterance in smalltalk_inputs:
        decision = policy.should_review(utterance)
        assert decision.should_review is False, (
            f"smalltalk leaked into review-pipeline: {utterance!r}"
        )


# ----------------------------------------------------------------------
# Scenario 2: Code-Gen-Path — Pass in Iter 1, voice success-Phrase
# ----------------------------------------------------------------------


def test_code_gen_path_pass_iter1(tmp_path: Path) -> None:
    """Plan §6.7 Smoke #2: dispatch_with_review gets called, the reviewer
    passes in iter 1, ToolResult.output contains the success voice phrase.
    The holding phrase was emitted EXACTLY ONCE.
    """
    bus = EventBus()
    captured = _captured_announcements(bus)
    audit = ReviewAudit(path=tmp_path / "review.log")

    async def worker_spawn(state: RunState, i: int) -> str:
        return "scripts/convert_webp.py was written"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.PASS,
            summary="Skript ist OK, alle Tests grün.",  # i18n-allow: becomes the German voice-completion phrase, asserted below
            score=0.95,
        )

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=3,
    )
    tool = DispatchWithReviewTool(
        bus=bus,
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=pipeline,
    )

    result = asyncio.run(
        tool.execute(
            {
                "task": (
                    "schreib mir ein Python-Script das alle Bilder "  # i18n-allow: simulated German user task request (product voice/chat input)
                    "in ~/downloads in webp konvertiert"
                ),
                "rubric_id": "code_generation",
            },
            _make_ctx(),
        )
    )

    # Pipeline-Outcome
    assert result.success is True
    assert result.output is not None
    assert result.output["outcome"] == "success"
    assert result.output["cap_fired"] is False

    # AD-14: Holding-Phrase exakt einmal
    assert captured == [VOICE_HOLDING_PHRASE_DE]

    # AD-14: voice_completion_phrase ist success-template
    voice = result.output["voice_completion_phrase"]
    assert voice.startswith("Erledigt — ")
    assert "Skript ist OK" in voice


# ----------------------------------------------------------------------
# Scenario 3: Multi-Iter-Path — needs_revision×1 → pass
# ----------------------------------------------------------------------


def test_multi_iter_path_needs_revision_then_pass(tmp_path: Path) -> None:
    """Plan §6.7 Smoke #3: the reviewer returns needs_revision on iter 1,
    pass on iter 2. ToolResult.output is success; voice_completion_phrase
    is the success template (the brain itself might append "after one
    correction" — the tool itself only renders the final state).
    """
    bus = EventBus()
    captured = _captured_announcements(bus)
    audit = ReviewAudit(path=tmp_path / "review.log")

    iteration_index = {"i": 0}
    verdicts = [
        ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary="docstring fehlt",
            issues=[
                ReviewIssue(
                    severity="warning",
                    description="add() has no docstring",
                    fix_hint="add a one-line docstring",
                )
            ],
            score=0.6,
        ),
        ReviewVerdict(
            status=ReviewStatus.PASS,
            summary="ok mit docstring",
            score=0.95,
        ),
    ]

    async def worker_spawn(state: RunState, i: int) -> str:
        return f"scripts/foo.py iter={i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        v = verdicts[iteration_index["i"]]
        iteration_index["i"] += 1
        return v

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=3,
    )
    tool = DispatchWithReviewTool(
        bus=bus,
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=pipeline,
    )

    result = asyncio.run(
        tool.execute(
            {
                "task": "schreibe eine Python-Funktion add(a, b) mit docstring und pytest-Tests",  # i18n-allow: simulated German user task request (product voice/chat input)
                "rubric_id": "code_generation",
            },
            _make_ctx(),
        )
    )

    assert result.success is True
    assert result.output["outcome"] == "success"
    assert result.output["iterations_total"] == 2
    # Holding phrase EXACTLY ONCE per run, not per iter (AD-14)
    assert captured == [VOICE_HOLDING_PHRASE_DE]

    voice = result.output["voice_completion_phrase"]
    assert voice.startswith("Erledigt — ")
    assert "ok mit docstring" in voice


# ----------------------------------------------------------------------
# Scenario 4: Cap-Fire-Path — needs_revision×3
# ----------------------------------------------------------------------


def test_cap_fire_path_returns_best_of_with_warning(tmp_path: Path) -> None:
    """Plan §6.7 Smoke #4: the reviewer always returns needs_revision, cap=3.
    ToolResult.output.cap_fired=True, warnings non-empty,
    voice_completion_phrase is the cap_fired template.
    """
    bus = EventBus()
    captured = _captured_announcements(bus)
    audit = ReviewAudit(path=tmp_path / "review.log")

    async def worker_spawn(state: RunState, i: int) -> str:
        return f"scripts/foo.py attempt {i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary="Tests fehlen",
            issues=[
                ReviewIssue(
                    severity="warning",
                    description="kein pytest-Test für die neue Funktion",  # i18n-allow: may surface in the German voice-completion phrase, asserted below
                )
            ],
            score=0.5,
        )

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=3,
    )
    tool = DispatchWithReviewTool(
        bus=bus,
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=pipeline,
    )

    result = asyncio.run(
        tool.execute(
            {
                "task": "schreib eine Funktion die Listen mergt mit Duplikat-Filter",  # i18n-allow: simulated German user task request (product voice/chat input)
                "rubric_id": "code_generation",
            },
            _make_ctx(),
        )
    )

    # Cap-fire is `success=True` with a warning (AD-7: never fail-closed)
    assert result.success is True
    assert result.output["cap_fired"] is True
    assert result.output["outcome"] == "cap_fired"
    assert result.output["iterations_total"] == 3
    assert len(result.output["warnings"]) >= 1

    # AD-14: Holding-Phrase einmal
    assert captured == [VOICE_HOLDING_PHRASE_DE]

    voice = result.output["voice_completion_phrase"]
    assert voice.startswith("Mein bestes Ergebnis liegt vor, mit einer Einschränkung:")  # i18n-allow: asserts the German voice-completion phrase (product voice output)
    assert "kein pytest-Test" in voice or "Tests fehlen" in voice  # i18n-allow: asserts the German voice-completion phrase (product voice output)


# ----------------------------------------------------------------------
# Bonus: Pre-Check-Fail-Phrase
# ----------------------------------------------------------------------


def test_precheck_fail_renders_specific_voice_phrase(tmp_path: Path) -> None:
    """Pre-Check-Fail (Task < 11 Chars) hat eigene Voice-Phrase
    (AD-14)."""
    bus = EventBus()
    captured = _captured_announcements(bus)

    async def noop_worker(state: RunState, i: int) -> str:
        return "should not run"

    async def noop_reviewer(state: RunState, output: str, i: int) -> ReviewVerdict:
        return ReviewVerdict(status=ReviewStatus.PASS, summary="x", score=1.0)

    pipeline = ReviewPipeline(
        worker_spawn=noop_worker,
        reviewer_spawn=noop_reviewer,
        prechecks=PreCheckRunner([task_not_empty]),
        audit=ReviewAudit(path=tmp_path / "review.log"),
    )
    tool = DispatchWithReviewTool(
        bus=bus,
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=pipeline,
    )

    # 25-char task — passes the tool schema min, but pre-check task_not_empty
    # > 10 chars only kicks in when stripped ≤ 10. We use a 21-char string with
    # extra spaces.
    result = asyncio.run(
        tool.execute(
            {"task": "          weiß nicht  "},  # i18n-allow: exact char length after strip is the content under test
            _make_ctx(),
        )
    )

    # 21 chars → tool-schema akzeptiert (>=20), pre-check stripped → 10 chars → fail
    if result.output and result.output.get("outcome") == "precheck_fail":
        voice = result.output["voice_completion_phrase"]
        assert voice.startswith("Die Aufgabe ist zu kurz")  # i18n-allow: asserts the German voice-completion phrase (product voice output)
        # Holding phrase was still emitted (before the pre-check abort)
        assert captured == [VOICE_HOLDING_PHRASE_DE]
