"""MissionDecomposer — User-Prompt -> MissionPlan (1-5 parallel tasks).

Heuristic (Plan §"Decision-MVP"):
- Mission < 200 characters OR only 1 external_system_marker -> 1-Step-Plan
  without an LLM call (deterministic, cheap, low-latency).
- Otherwise: BrainManager (Opus 4.7) with JSON-output prompt -> Pydantic post-parse.

Rationale for in-process execution via BrainManager (NOT subprocess like Worker/Critic):
- Decomposer is orchestrator-internal, not a worktree-isolated Worker.
- It produces plan metadata, not code diffs.
- Cost is tracked via the existing BrainManager cost hook (Phase 5).
- Latency: once per mission ~1-2s, acceptable.

Output: `MissionPlan` Pydantic model. After successful decomposition
the caller (Orchestrator) publishes the `MissionPlanReady` event.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..ids import uuid7_str

logger = logging.getLogger(__name__)


# Heuristic thresholds
SHORT_PROMPT_CHAR_LIMIT: Final[int] = 200
MAX_STEPS: Final[int] = 5

# External-system markers (from ADR-0011 §2 + BrainRoutingConfig)
EXTERNAL_SYSTEM_MARKERS: Final[tuple[str, ...]] = (
    "github", "gitlab", "git ", "PR ", "pull request", "issue",
    "repository", "repo ", "branch", "commit", "merge",
    "linear", "jira", "notion", "slack", "discord",
    "browser", "url", "http", "scrape", "crawl",
)


# Type for a brain caller (compatible with BrainManager.generate).
BrainCallerFn = Callable[[str], Awaitable[str]]


class Step(BaseModel):
    """A single worker task within a mission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(default_factory=uuid7_str)
    slug: str  # short kebab-case for worktree naming (e.g. "refactor-auth")
    prompt: str  # complete instruction for the worker
    worker_cli: Literal["claude", "codex"] = "claude"
    # Empty string = "use whatever the worker's primary provider configures".
    # ClaudeDirectWorker overrides this with primary.model from
    # [brain.providers.claude-api]; CodexDirectWorker omits --model when
    # empty (relies on ~/.codex/config.toml default). Previously hardcoded
    # to "sonnet", which is rejected by Codex on ChatGPT-OAuth accounts
    # (HTTP 400). See docs/openclaw-spawn-failure-analysis-2026-05-18.md.
    model: str = ""
    allowed_tools: str = "Read,Edit,Write,Bash,Grep,Glob"


class MissionPlan(BaseModel):
    """Complete mission split into 1..MAX_STEPS parallel tasks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)
    n_workers: int = Field(ge=1, le=MAX_STEPS)
    expected_output: str = ""


class MissionDecomposer:
    """Wrapper around BrainManager for mission decomposition.

    When `brain=None` the decomposer operates in heuristic-only mode — all
    missions are returned as a 1-step plan (useful for tests and for the
    bootstrap path before BrainManager is ready).
    """

    def __init__(self, *, brain: BrainCallerFn | None = None) -> None:
        self._brain = brain

    async def decompose(self, mission_prompt: str) -> MissionPlan:
        """User-Prompt -> MissionPlan.

        Heuristic path (no LLM):
        - prompt < SHORT_PROMPT_CHAR_LIMIT -> 1-Step.
        - exactly 1 external_system_marker -> 1-Step.

        LLM path: BrainManager.generate(decomposition_prompt) -> JSON ->
        Pydantic-Validate -> MissionPlan. On parse failure: fallback to
        1-Step-Plan, no crash.
        """
        if not mission_prompt or not mission_prompt.strip():
            raise ValueError("MissionDecomposer: leerer prompt")

        # Heuristic 1: short prompts are never multi-step
        if len(mission_prompt) < SHORT_PROMPT_CHAR_LIMIT:
            return self._single_step_plan(mission_prompt, reason="short_prompt")

        # Heuristic 2: exactly one external-system marker is sufficient for 1 step
        marker_count = self._count_external_markers(mission_prompt)
        if marker_count <= 1:
            return self._single_step_plan(mission_prompt, reason="single_external_target")

        # LLM-Pfad — Brain not bound -> Fallback Single-Step.
        if self._brain is None:
            logger.info(
                "MissionDecomposer: brain=None, falling back to single-step plan"
            )
            return self._single_step_plan(mission_prompt, reason="no_brain_available")

        # LLM decomposition
        decomposition_prompt = self._build_decomposition_prompt(mission_prompt)
        try:
            raw = await self._brain(decomposition_prompt)
        except Exception:  # noqa: BLE001
            logger.warning("MissionDecomposer: brain call raised — fallback single-step", exc_info=True)
            return self._single_step_plan(mission_prompt, reason="brain_error")

        plan = self._parse_plan(raw, mission_prompt)
        if plan is None:
            logger.info("MissionDecomposer: parse failed, falling back to single-step")
            return self._single_step_plan(mission_prompt, reason="parse_failed")

        return plan

    # --- Internals ---

    def _single_step_plan(self, mission_prompt: str, *, reason: str) -> MissionPlan:
        """Fallback: entire mission as a single step."""
        slug = _slugify(mission_prompt)[:40] or "task"
        return MissionPlan(
            steps=[
                Step(
                    slug=slug,
                    prompt=mission_prompt,
                    worker_cli="claude",
                    model="",  # let the worker pick its configured primary
                )
            ],
            n_workers=1,
            expected_output=f"Single-step plan ({reason})",
        )

    @staticmethod
    def _count_external_markers(prompt: str) -> int:
        """Counts unique external-system markers in the prompt."""
        lower = prompt.lower()
        return sum(1 for m in EXTERNAL_SYSTEM_MARKERS if m.lower() in lower)

    @staticmethod
    def _build_decomposition_prompt(mission_prompt: str) -> str:
        """Decomposer prompt for the LLM."""
        return (
            "You are a senior project manager. The user has issued the following "
            "mission for an autonomous engineering agent. Decompose it into "
            f"between 1 and {MAX_STEPS} parallel tasks if and only if the work "
            "naturally splits into independent files or subsystems. Otherwise, "
            "return a single step.\n\n"
            f"User mission:\n<<<{mission_prompt}>>>\n\n"
            "Output ONLY a JSON object matching this schema:\n"
            "{\n"
            '  "steps": [\n'
            '    { "slug": "kebab-case-short", '
            '"prompt": "<full instructions for one worker>", '
            '"worker_cli": "claude" | "codex", '
            '"model": "sonnet" | "opus" | "haiku", '
            '"allowed_tools": "Read,Edit,Write,Bash,Grep,Glob" }\n'
            "  ],\n"
            '  "n_workers": <int 1..5>,\n'
            '  "expected_output": "<one-sentence description of the deliverable>"\n'
            "}\n\n"
            "Rules:\n"
            "- DO NOT split if tasks share files (race conditions).\n"
            "- DO NOT include task_id (auto-generated).\n"
            "- Default worker_cli is 'claude'; only use 'codex' for OpenAI-specific work.\n"
            "- Default model is 'sonnet'; use 'opus' only for reasoning-heavy steps."
        )

    def _parse_plan(self, raw: str, mission_prompt: str) -> MissionPlan | None:
        """Attempts to validate the LLM output as a MissionPlan."""
        json_blob = _extract_json_object(raw)
        if json_blob is None:
            return None
        try:
            data: Any = json.loads(json_blob)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        # n_workers default = len(steps) when not provided
        if "n_workers" not in data and "steps" in data:
            data["n_workers"] = len(data["steps"])

        try:
            return MissionPlan.model_validate(data)
        except ValidationError as exc:
            logger.debug("MissionDecomposer: ValidationError: %s", exc)
            return None


# --- Module-level helpers ---


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """ASCII kebab slug from arbitrary text — for worktree naming."""
    lowered = text.lower().strip()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    return slug


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(raw: str) -> str | None:
    """Extracts the first {…} block from the LLM output (including nested)."""
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        return None
    return match.group(0)


__all__ = [
    "EXTERNAL_SYSTEM_MARKERS",
    "MAX_STEPS",
    "SHORT_PROMPT_CHAR_LIMIT",
    "BrainCallerFn",
    "MissionDecomposer",
    "MissionPlan",
    "Step",
]
