"""Tests fuer MissionDecomposer (Heuristik + LLM-Pfad)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from jarvis.missions.kontrollierer.decomposer import (
    EXTERNAL_SYSTEM_MARKERS,
    MAX_STEPS,
    SHORT_PROMPT_CHAR_LIMIT,
    MissionDecomposer,
    MissionPlan,
    Step,
)


# --- Pydantic-Schema ---


def test_step_requires_slug_and_prompt() -> None:
    with pytest.raises(ValidationError):
        Step(slug="x")  # type: ignore[call-arg]


def test_step_defaults_worker_cli_claude() -> None:
    s = Step(slug="x", prompt="do x")
    assert s.worker_cli == "claude"
    # Default model is "" since 9769f7b — the worker resolves its own model
    # (ClaudeDirectWorker uses primary.model; CodexDirectWorker omits --model
    # when empty, since ChatGPT-OAuth rejects "sonnet" with HTTP 400). The
    # stale "sonnet" assertion predated that change.
    assert s.model == ""


def test_step_task_id_auto_generated() -> None:
    s = Step(slug="x", prompt="do x")
    assert isinstance(s.task_id, str) and len(s.task_id) > 8


def test_mission_plan_min_one_step() -> None:
    with pytest.raises(ValidationError):
        MissionPlan(steps=[], n_workers=0)


def test_mission_plan_max_steps_enforced() -> None:
    too_many = [Step(slug=f"s{i}", prompt=f"p{i}") for i in range(MAX_STEPS + 1)]
    with pytest.raises(ValidationError):
        MissionPlan(steps=too_many, n_workers=MAX_STEPS + 1)


# --- Heuristik: short prompt ---


@pytest.mark.asyncio
async def test_short_prompt_returns_single_step_no_brain() -> None:
    """Kein BrainCaller noetig wenn Prompt < 200 chars."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Build palindrome function")
    assert len(plan.steps) == 1
    assert plan.n_workers == 1
    assert "single-step" in plan.expected_output.lower() or "short_prompt" in plan.expected_output


@pytest.mark.asyncio
async def test_short_prompt_skips_brain_even_if_provided() -> None:
    """Heuristik trumpft LLM-Call (Latenz-Sparen)."""
    called: list[str] = []

    async def brain(p: str) -> str:
        called.append(p)
        return "{}"

    d = MissionDecomposer(brain=brain)
    await d.decompose("Build X")
    assert called == []


# --- Heuristik: external markers ---


@pytest.mark.asyncio
async def test_long_prompt_with_one_marker_single_step() -> None:
    """Lang aber nur 1 marker -> single step (kein parallel-split sinnvoll)."""
    long_prompt = "Bitte oeffne mir das github repo openhands-cli und " * 5
    long_prompt += "x" * 100
    assert len(long_prompt) > SHORT_PROMPT_CHAR_LIMIT
    d = MissionDecomposer(brain=None)
    plan = await d.decompose(long_prompt)
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_empty_prompt_raises() -> None:
    d = MissionDecomposer(brain=None)
    with pytest.raises(ValueError, match="leer"):
        await d.decompose("")


@pytest.mark.asyncio
async def test_whitespace_prompt_raises() -> None:
    d = MissionDecomposer(brain=None)
    with pytest.raises(ValueError):
        await d.decompose("   \n\t  ")


# --- LLM-Pfad ---


def _valid_plan_json() -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "slug": "refactor-auth",
                    "prompt": "Refactor jarvis/auth/oauth.py",
                    "worker_cli": "claude",
                    "model": "sonnet",
                    "allowed_tools": "Read,Edit,Write,Bash,Grep,Glob",
                },
                {
                    "slug": "add-tests",
                    "prompt": "Add tests for new auth module",
                    "worker_cli": "claude",
                    "model": "sonnet",
                    "allowed_tools": "Read,Edit,Write,Bash,Grep,Glob",
                },
            ],
            "n_workers": 2,
            "expected_output": "Refactored auth + tests",
        }
    )


@pytest.mark.asyncio
async def test_llm_path_returns_multi_step_plan() -> None:
    captured: list[str] = []

    async def brain(p: str) -> str:
        captured.append(p)
        return _valid_plan_json()

    # Long prompt mit 3+ markers -> LLM-Pfad
    prompt = (
        "Bitte oeffne das github repository openhands-cli, lies das issue #42, "
        "schreibe einen pull request fuer den branch fix/auth, und committe die "
        "Aenderung mit einer message die das jira ticket referenziert. Achte auf "
        "den slack-channel #engineering fuer Feedback."
    )
    assert len(prompt) >= SHORT_PROMPT_CHAR_LIMIT
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert len(captured) == 1
    assert "<<<" + prompt + ">>>" in captured[0]
    assert len(plan.steps) == 2
    assert plan.n_workers == 2


@pytest.mark.asyncio
async def test_llm_path_handles_json_in_prose() -> None:
    """LLM gibt JSON manchmal mit Pre-/Post-Prosa zurueck — Decomposer muss extrahieren."""

    async def brain(p: str) -> str:
        return f"Sure, here's the plan:\n```json\n{_valid_plan_json()}\n```\nDone."

    prompt = "x" * 300 + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5])
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 2


@pytest.mark.asyncio
async def test_llm_path_invalid_json_falls_back_single() -> None:
    async def brain(p: str) -> str:
        return "This is not JSON at all"

    prompt = "x" * 300 + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5])
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_llm_path_brain_crash_falls_back_single() -> None:
    async def brain(p: str) -> str:
        raise RuntimeError("BrainManager kaputt")

    prompt = "x" * 300 + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5])
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_llm_path_fills_missing_n_workers() -> None:
    """Wenn LLM `n_workers` vergisst, Decomposer leitet aus len(steps) ab."""

    async def brain(p: str) -> str:
        return json.dumps(
            {
                "steps": [
                    {"slug": "a", "prompt": "do a"},
                    {"slug": "b", "prompt": "do b"},
                ],
                "expected_output": "x",
            }
        )

    prompt = "x" * 300 + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5])
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert plan.n_workers == 2


# --- Decomposition-Prompt enthaelt Anchor-Token ---


@pytest.mark.asyncio
async def test_decomposition_prompt_anchors_user_request() -> None:
    captured: list[str] = []

    async def brain(p: str) -> str:
        captured.append(p)
        return _valid_plan_json()

    user = "build a CLI tool for X with --help and tests"
    prompt = user + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5]) * 3
    prompt += "x" * (SHORT_PROMPT_CHAR_LIMIT + 10)
    d = MissionDecomposer(brain=brain)
    await d.decompose(prompt)
    assert f"<<<{prompt}>>>" in captured[0]


# --- Module-Level Constants ---


def test_short_prompt_char_limit_is_200() -> None:
    assert SHORT_PROMPT_CHAR_LIMIT == 200


def test_max_steps_is_5() -> None:
    """ADR-0009 + jarvis.toml [phase6.orchestrator]: max_workers_per_mission = 5."""
    assert MAX_STEPS == 5


def test_external_system_markers_includes_github_pr() -> None:
    assert "github" in EXTERNAL_SYSTEM_MARKERS
    assert "PR " in EXTERNAL_SYSTEM_MARKERS
    assert "branch" in EXTERNAL_SYSTEM_MARKERS


# --- needs_repo: lean workspace classification -------------------------------
#
# A single-step external-artefact task ("create an HTML file with today's
# news") does not need a full worktree of the repo. Cloning + exploring the
# whole repo cost a live mission 10+ minutes and >1.3M input tokens before it
# wrote a trivial file (mission 019eb17d, 2026-06-10). `needs_repo=False`
# routes such steps to a lean (empty) git workspace. The default stays True so
# every existing path keeps the full worktree and old persisted plans
# deserialize byte-compatibly.


def test_step_needs_repo_defaults_true() -> None:
    """Conservative default: a step always gets the full worktree unless the
    decomposer explicitly clears the flag. This also keeps already-persisted
    plan payloads (which never carried the field) deserializing fine."""
    s = Step(slug="x", prompt="do x")
    assert s.needs_repo is True


def test_step_needs_repo_is_frozen() -> None:
    """Step is frozen=True extra='forbid' — the new field must respect that."""
    s = Step(slug="x", prompt="do x", needs_repo=False)
    assert s.needs_repo is False
    with pytest.raises(ValidationError):
        s.needs_repo = True  # type: ignore[misc]


@pytest.mark.asyncio
async def test_external_artefact_task_is_lean() -> None:
    """A short single-step task that writes a standalone file → needs_repo
    False (no repo affinity)."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Create a file robot-haiku.txt with three haikus")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_german_html_news_task_is_lean() -> None:
    """German external-artefact prompt → lean workspace (no repo markers)."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Erstelle eine HTML-Datei von den aktuellen Tagesnews")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_repo_bugfix_task_keeps_full_worktree() -> None:
    """A repo-affinity marker (a jarvis/ path + 'bug') → needs_repo True even
    though it is a short single step."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Fix the bug in jarvis/brain/manager.py")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_git_branch_prompt_keeps_full_worktree() -> None:
    """An explicit git/branch/repo mention → needs_repo True (one external
    marker keeps it single-step, but the repo affinity blocks the lean path)."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Create a new git branch and tidy the README")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_repo_marker_word_keeps_full_worktree() -> None:
    """The word 'repository' alone is enough repo affinity to keep the full
    worktree."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Add a CONTRIBUTING section to the repository docs")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_python_file_extension_keeps_full_worktree() -> None:
    """A bare '.py' filename is a code-affinity marker → full worktree."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Write a small helper in utils.py for parsing dates")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_refactor_word_keeps_full_worktree() -> None:
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Refactor the date parsing into a cleaner shape")
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_long_single_marker_lean_when_no_repo_affinity() -> None:
    """The single_external_target path (long prompt, ≤1 external marker) is
    also eligible for lean when there is no repo affinity. 'browser' is the one
    external marker; nothing here points at this repo's code."""
    long_prompt = (
        "Please write a standalone landing page that summarises today's top "
        "stories with a nice headline and three sections of body copy and a "
        "footer, formatted as a single self-contained document for the "
        "browser. " * 2
    )
    assert len(long_prompt) > SHORT_PROMPT_CHAR_LIMIT
    d = MissionDecomposer(brain=None)
    plan = await d.decompose(long_prompt)
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_multi_step_plan_always_keeps_full_worktree() -> None:
    """Multi-step (LLM-decomposed) plans are NOT eligible for lean — they are
    the repo-heavy work the worktree isolation exists for. The lean override is
    only applied on the deterministic single-step heuristic paths."""

    async def brain(p: str) -> str:
        return _valid_plan_json()

    prompt = (
        "Bitte oeffne das github repository openhands-cli, lies das issue #42, "
        "schreibe einen pull request fuer den branch fix/auth, und committe die "
        "Aenderung mit einer message die das jira ticket referenziert. Achte auf "
        "den slack-channel #engineering fuer Feedback."
    )
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 2
    assert all(s.needs_repo is True for s in plan.steps)


@pytest.mark.asyncio
async def test_no_brain_fallback_keeps_full_worktree() -> None:
    """A long, multi-marker prompt that falls back to single-step because no
    brain is bound must stay conservative (needs_repo True) — we did not get to
    classify it, so we must not strip its worktree."""
    d = MissionDecomposer(brain=None)
    prompt = "x" * 300 + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5])
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is True
