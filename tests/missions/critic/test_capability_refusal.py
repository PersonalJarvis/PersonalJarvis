"""Honest one-shot failure for impossible / capability-refusal tasks.

When the worker honestly reports it CANNOT do a task — no tools invoked, empty
diff, and a substantive refusal answer ("That's outside what I can do, I can't
access travel booking systems") — retrying it three times is pure waste: the
worker already decided it can't, and re-prompting won't grant it a capability it
lacks. Before this guard the empty-diff pre-gate auto-revised the refusal,
burning all three critic loops into ``critic_loop_exhausted`` and surfacing a
scary 3-attempt ERROR for a request that was simply impossible (live mission
019ec674, 2026-06-14: "book me a trip from Melbourne to Tokyo"). We now route a
genuine capability refusal to a one-shot ``reject`` so the mission fails honestly
as ``critic_rejected`` (one iteration) carrying the worker's own words, NOT
``critic_loop_exhausted`` after three.

The anti-hallucination contract (BUG-LIVE-02) is intact: this fires ONLY on a
refusal (the worker claiming it CANNOT) AND only when ZERO tools were invoked. A
"done!" success claim with no tools still hits the deterministic empty-diff veto,
and any task where the worker actually invoked tools defers to the Critic LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.missions.critic.runner import CriticRunner
from jarvis.missions.stream_evidence import capability_refusal_answer

_REFUSAL = (
    "That's outside what I can do. I can't access travel booking systems, "
    "so I'm unable to book a trip from Melbourne to Tokyo for you."
)
_SUCCESS = "I created the requested file and the task is now complete."


def _result_stream(text: str) -> str:
    return json.dumps({"type": "result", "result": text})


def _tool_stream(text: str) -> str:
    return "\n".join([
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {}},
            ]},
        }),
        json.dumps({"type": "result", "result": text}),
    ])


# --- the pure helper -------------------------------------------------------

def test_refusal_answer_detects_honest_capability_refusal() -> None:
    out = capability_refusal_answer(
        _result_stream(_REFUSAL), prompt="book me a trip from Melbourne to Tokyo"
    )
    assert out is not None
    assert "can't access" in out.lower() or "outside" in out.lower()


def test_refusal_answer_none_for_success_claim() -> None:
    # A bare "done" claim with no tools must NOT read as a refusal — it stays
    # vetoed by the empty-diff hallucination guard (BUG-LIVE-02).
    assert capability_refusal_answer(
        _result_stream(_SUCCESS), prompt="create a file foo.txt"
    ) is None


def test_refusal_answer_none_when_tools_were_invoked() -> None:
    # The worker actually attempted work -> defer to the Critic LLM, not a
    # one-shot reject (re-prompting may yet succeed).
    assert capability_refusal_answer(
        _tool_stream(_REFUSAL), prompt="book me a trip"
    ) is None


def test_refusal_answer_none_for_informational_request() -> None:
    # Informational questions are handled (approved) by readonly_answer upstream.
    assert capability_refusal_answer(
        _result_stream(_REFUSAL),
        prompt="which city would you recommend for a trip to Australia?",
    ) is None


# --- the critic gate -------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_rejects_impossible_task_in_one_shot(tmp_path: Path) -> None:
    verdict = await CriticRunner().run(
        mission_prompt="book me a trip from Melbourne to Tokyo",
        worker_diff="",
        worker_log=_result_stream(_REFUSAL),
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    # One-shot terminal reject -> orchestrator maps to critic_rejected, NOT a
    # 3-loop revise -> critic_loop_exhausted.
    assert verdict.verdict == "reject"
    assert verdict.suggested_next_action != "retry"
    combined = (verdict.summary + " " + verdict.summary_de).lower()
    assert "can't access" in combined or "outside" in combined


def test_refusal_answer_none_for_success_with_caveat() -> None:
    # A genuine completion that merely HEDGES ("I can't guarantee...") must not
    # be misread as a capability refusal — that would discard real work. Bare
    # "i can't"/"i cannot"/"no access to" substrings were removed for this.
    for caveat in (
        "I implemented the JSON parser and added tests. I can't guarantee every "
        "malformed input is handled, but the core works.",
        "Done. I can not only parse the file but also validate it.",
        "Setup complete. There is no access to worry about; it just works now.",
        "I cannot stress enough how thoroughly I tested the new module.",
    ):
        assert capability_refusal_answer(
            _result_stream(caveat), prompt="implement a JSON parser"
        ) is None, f"false-positive refusal on: {caveat!r}"


@pytest.mark.asyncio
async def test_runner_reject_does_not_overflow_verdict_summary(tmp_path: Path) -> None:
    # CriticVerdict.summary / summary_de are max_length=280. Refusals are wordy;
    # a long one must NOT raise pydantic ValidationError when the reject verdict
    # is built (the one-shot honest-failure path must never become a crash).
    long_refusal = (
        "That's outside what I can do. I can't access travel booking systems, "
        "flight APIs, payment rails, or any external reservation service, and I "
        "have no way to authenticate against an airline, a hotel chain, or a "
        "travel agency on your behalf, nor can I take payment, so I am unable to "
        "book this trip from Melbourne to Tokyo end to end for you here."
    )
    assert len(long_refusal) > 280, len(long_refusal)
    verdict = await CriticRunner().run(
        mission_prompt="book me a trip from Melbourne to Tokyo",
        worker_diff="",
        worker_log=_result_stream(long_refusal),
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "reject"
    assert len(verdict.summary) <= 280
    assert len(verdict.summary_de) <= 280


@pytest.mark.asyncio
async def test_runner_still_vetoes_hallucinated_success(tmp_path: Path) -> None:
    # Empty diff + "done" claim + no tools must STILL deterministic-revise — the
    # honest-refusal path must not open the hallucination backdoor (BUG-LIVE-02).
    verdict = await CriticRunner().run(
        mission_prompt="create a file foo.txt",
        worker_diff="",
        worker_log=_result_stream(_SUCCESS),
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "revise"
