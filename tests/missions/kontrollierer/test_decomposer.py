"""Tests for MissionDecomposer (heuristic + LLM path)."""
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


# --- Heuristic: short prompt ---


@pytest.mark.asyncio
async def test_short_prompt_returns_single_step_no_brain() -> None:
    """No BrainCaller needed when prompt < 200 chars."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Build palindrome function")
    assert len(plan.steps) == 1
    assert plan.n_workers == 1
    assert "single-step" in plan.expected_output.lower() or "short_prompt" in plan.expected_output


@pytest.mark.asyncio
async def test_short_prompt_skips_brain_even_if_provided() -> None:
    """Heuristic beats an LLM call (saves latency)."""
    called: list[str] = []

    async def brain(p: str) -> str:
        called.append(p)
        return "{}"

    d = MissionDecomposer(brain=brain)
    await d.decompose("Build X")
    assert called == []


# --- Heuristic: external markers ---


@pytest.mark.asyncio
async def test_long_prompt_with_one_marker_single_step() -> None:
    """Long but only 1 marker -> single step (no parallel split makes sense)."""
    long_prompt = "Please open the github repo openhands-cli for me and " * 5
    long_prompt += "x" * 100
    assert len(long_prompt) > SHORT_PROMPT_CHAR_LIMIT
    d = MissionDecomposer(brain=None)
    plan = await d.decompose(long_prompt)
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_empty_prompt_raises() -> None:
    d = MissionDecomposer(brain=None)
    with pytest.raises(ValueError, match="empty"):
        await d.decompose("")


@pytest.mark.asyncio
async def test_whitespace_prompt_raises() -> None:
    d = MissionDecomposer(brain=None)
    with pytest.raises(ValueError):
        await d.decompose("   \n\t  ")


# --- LLM path ---


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

    # Long prompt with 3+ markers -> LLM path
    prompt = (
        "Please open the github repository openhands-cli, read issue #42, "
        "write a pull request for the branch fix/auth, and commit the "
        "change with a message that references the jira ticket. Watch "
        "the slack-channel #engineering for feedback."
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
    """The decomposer extracts JSON wrapped in model-generated prose."""

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
        raise RuntimeError("BrainManager failed")

    prompt = "x" * 300 + " " + " ".join(EXTERNAL_SYSTEM_MARKERS[:5])
    d = MissionDecomposer(brain=brain)
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_llm_path_fills_missing_n_workers() -> None:
    """When the LLM omits `n_workers`, the decomposer derives it from len(steps)."""

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


# --- Decomposition prompt contains anchor token ---


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
    """German external-artifact prompt → lean workspace (no repo markers)."""
    d = MissionDecomposer(brain=None)
    prompt = "Erstelle eine HTML-Datei von den aktuellen Tagesnews"  # i18n-allow: DE input
    plan = await d.decompose(prompt)
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
        "Please open the github repository openhands-cli, read issue #42, "
        "write a pull request for the branch fix/auth, and commit the "
        "change with a message that references the jira ticket. Watch "
        "the slack-channel #engineering for feedback."
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


@pytest.mark.asyncio
async def test_remote_github_issue_summary_is_lean() -> None:
    """A connected GitHub read is not evidence that local source is needed."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose(
        "Summarize the open issues in the GitHub repository for me"
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_remote_pull_request_review_is_lean() -> None:
    """Reviewing remote metadata through a connected service needs no clone."""
    plan = await MissionDecomposer(brain=None).decompose(
        "Review the open GitHub pull request and summarize its status"
    )
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_remote_github_test_failure_review_is_lean() -> None:
    """Generic code nouns do not turn a connected-service read into local work."""
    plan = await MissionDecomposer(brain=None).decompose(
        "Review the failing tests in the GitHub repository and summarize them"
    )
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_ambiguous_task_fails_closed_to_source_workspace() -> None:
    """Absence of a source keyword is not enough to select an empty repo."""
    plan = await MissionDecomposer(brain=None).decompose(
        "Improve performance without changing existing behavior"
    )
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_spanish_standalone_report_is_lean() -> None:
    """Workspace classification is locale-neutral across supported input."""
    plan = await MissionDecomposer(brain=None).decompose(
        "Crea un informe independiente con una tabla comparativa"
    )
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_explicit_local_repository_analysis_keeps_full_worktree() -> None:
    """An explicit reference to the local project remains fail-closed."""
    d = MissionDecomposer(brain=None)
    plan = await d.decompose("Analyze this repository's architecture")
    assert plan.steps[0].needs_repo is True


@pytest.mark.asyncio
async def test_no_brain_fallback_keeps_standalone_task_lean() -> None:
    """A source-less install can still run a long connected-service task when
    the decomposition brain is unavailable. Fallback must preserve the
    deterministic workspace classification instead of defaulting to a source
    checkout that the distribution does not contain."""
    d = MissionDecomposer(brain=None)
    prompt = (
        "Research the current product announcements in the browser, prepare "
        "a detailed standalone report with links and a comparison table, and "
        "post a concise summary to Slack for the project team. " * 3
    )
    assert len(prompt) > SHORT_PROMPT_CHAR_LIMIT
    plan = await d.decompose(prompt)
    assert len(plan.steps) == 1
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["brain_error", "parse_error"])
async def test_llm_failure_keeps_standalone_task_lean(failure_mode: str) -> None:
    """Brain and parse failures must not turn independent work into a source
    task merely because the fallback plan has one step."""

    async def brain(_prompt: str) -> str:
        if failure_mode == "brain_error":
            raise RuntimeError("provider unavailable")
        return "not valid JSON"

    prompt = (
        "Use the browser to research current accessibility guidance, then "
        "prepare a standalone report and send its short summary to Slack. " * 3
    )
    plan = await MissionDecomposer(brain=brain).decompose(prompt)
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_llm_plan_backfills_missing_workspace_requirement() -> None:
    """Legacy model output without ``needs_repo`` is classified per mission
    rather than inheriting Step's persistence-safe source default."""

    async def brain(_prompt: str) -> str:
        return json.dumps(
            {
                "steps": [
                    {
                        "slug": "research-release",
                        "prompt": "Research the release and write report.md",
                    }
                ],
                "n_workers": 1,
            }
        )

    prompt = (
        "Research the latest release in the browser and send a summary to "
        "Slack after creating a standalone report. " * 4
    )
    plan = await MissionDecomposer(brain=brain).decompose(prompt)
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_llm_cannot_disable_clear_standalone_task() -> None:
    """A model's over-conservative flag cannot brick a connected-service task
    on a copied/headless installation."""

    async def brain(_prompt: str) -> str:
        return json.dumps(
            {
                "steps": [
                    {
                        "slug": "summarize-issues",
                        "prompt": "Summarize the open GitHub issues",
                        "needs_repo": True,
                    }
                ],
                "n_workers": 1,
            }
        )

    prompt = (
        "Summarize open GitHub issues, compare them with the Jira backlog, "
        "and post a concise status update to Slack. " * 3
    )
    plan = await MissionDecomposer(brain=brain).decompose(prompt)
    assert plan.steps[0].needs_repo is False


@pytest.mark.asyncio
async def test_llm_cannot_mark_source_task_as_lean() -> None:
    """Deterministic source affinity overrides an unsafe model classification."""

    calls: list[str] = []

    async def brain(model_prompt: str) -> str:
        calls.append(model_prompt)
        return json.dumps(
            {
                "steps": [
                    {
                        "slug": "fix-router",
                        "prompt": "Fix the bug in jarvis/brain/manager.py",
                        "needs_repo": False,
                    }
                ],
                "n_workers": 1,
            }
        )

    prompt = (
        "Fix the bug in jarvis/brain/manager.py, commit it on a branch, open "
        "a GitHub pull request, and link the Jira issue with a Slack update. "
        "Preserve every existing behavior, add focused regression coverage, "
        "and explain the verification evidence in the pull request body."
    )
    assert len(prompt) > SHORT_PROMPT_CHAR_LIMIT
    plan = await MissionDecomposer(brain=brain).decompose(prompt)
    assert len(calls) == 1
    assert plan.steps[0].needs_repo is True
