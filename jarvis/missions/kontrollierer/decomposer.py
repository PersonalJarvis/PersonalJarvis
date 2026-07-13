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
from collections.abc import Awaitable, Callable
from typing import Any, Final, Literal

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


# Repo-affinity markers — when ANY of these appear in a step prompt we
# keep the full git worktree (needs_repo=True) even though the step would
# otherwise qualify for a lean workspace. This is the conservative guard for
# the lean path: the moment a task smells like it touches THIS repo's code
# (a path, a file extension, a git/branch/commit mention, a refactor/bugfix
# verb) we must not strip its checkout. When in any doubt → full worktree.
#
# Deliberately a SUPERSET-aware companion to EXTERNAL_SYSTEM_MARKERS (which is
# about *whether* to split into multiple steps); this regex is about *whether*
# a step needs the codebase on disk. It never weakens multi-step
# classification and is the hard guard when an LLM labels source work as lean.
_REPO_AFFINITY_RE: Final = re.compile(
    r"""
    \b(?:
        git | commit | commits | merge | rebase
      | pyproject | requirements\.txt | codebase | code[ -]?base
      | source[ -]?checkout | source[ -]?tree | working[ -]?tree | worktree
      | refactor | refactors | refactoring
      | bugfix | bug[ -]?fix | hotfix
    )\b
    | \b(?:this|our|current|local|personal[ -]?jarvis)\s+
        (?:repo(?:sitory)?|code[ -]?base|source|project)\b
    | \b(?:modify|change|edit|fix|refactor|implement|add|remove|update|test)\b
        [^.]{0,80}\b(?:repo(?:sitory)?|code[ -]?base|source(?:[ -]?code)?|project[ -]?files?)\b
    | \bfix(?:es|ed|ing)?\b[^.]*\bbug\b   # "fix the bug", "fixing a nasty bug"
    | \bjarvis/                           # an in-repo path reference
    | \b[\w./-]+\.(?:py|ts|tsx|js|jsx|toml|cfg|ini|yaml|yml|sql|sh|ps1)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Positive evidence that a task can run without the Personal Jarvis checkout.
# Connected services are remote capabilities; standalone artifacts are lean
# only when an artifact action and artifact noun occur near each other. This
# positive-evidence rule is intentional: an ambiguous prompt such as "improve
# performance" stays source-dependent and fails early in a copied install
# instead of launching a worker into an empty repository.
_STANDALONE_SERVICE_RE: Final = re.compile(
    r"\b(?:"
    r"github|gitlab|slack|discord|notion|jira|linear|mcp|"
    r"browser|https?|url|website|web[ -]?page|scrape|crawl|"
    r"email|calendar|google[ -]?drive|google[ -]?docs|google[ -]?sheets"
    r")\b",
    re.IGNORECASE,
)
_STANDALONE_ACTION_RE: Final = re.compile(
    r"\b(?:"
    r"create|write|draft|generate|prepare|render|make|produce|"
    r"research|summari[sz]e|compare|list|find|search|"
    r"erstelle|erstellen|schreibe|"  # i18n-allow: German input vocabulary
    r"schreiben|entwirf|generiere|"  # i18n-allow: German input vocabulary
    r"bereite|rendere|recherchiere|"  # i18n-allow: German input vocabulary
    r"fasse|vergleiche|liste|finde|suche|"  # i18n-allow: German input vocabulary
    r"crea|crear|escribe|escribir|redacta|genera|prepara|"
    r"renderiza|investiga|resume|compara|lista|busca"
    r")\b",
    re.IGNORECASE,
)
_STANDALONE_ARTIFACT_RE: Final = re.compile(
    r"\b(?:"
    r"standalone|report|summary|document|file|artifact|"
    r"html|markdown|csv|json|spreadsheet|presentation|slides?|"
    r"image|video|audio|poster|email|table|checklist|news|weather|"
    r"datei|bericht|zusammenfassung|"  # i18n-allow: German input vocabulary
    r"dokument|tabelle|nachrichten|wetter|"  # i18n-allow: German input vocabulary
    r"archivo|informe|resumen|documento|tabla|noticias|clima"
    r")\b",
    re.IGNORECASE,
)


def _has_repo_affinity(prompt: str) -> bool:
    """True when the prompt points at THIS repo's code (keep full worktree)."""
    return _REPO_AFFINITY_RE.search(prompt) is not None


def _has_standalone_affinity(prompt: str) -> bool:
    """True when the prompt has positive evidence it needs no source tree."""
    if _STANDALONE_SERVICE_RE.search(prompt) is not None:
        return True
    actions = tuple(_STANDALONE_ACTION_RE.finditer(prompt))
    artifacts = tuple(_STANDALONE_ARTIFACT_RE.finditer(prompt))
    return any(
        abs(action.start() - artifact.start()) <= 120
        for action in actions
        for artifact in artifacts
    )


def _workspace_requirement(prompt: str) -> bool | None:
    """Return True for source, False for lean, or None when ambiguous.

    Source evidence wins over standalone evidence. This makes remote-service
    tasks portable while keeping ambiguous work fail-closed.
    """
    if _has_repo_affinity(prompt):
        return True
    if _has_standalone_affinity(prompt):
        return False
    return None


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
    # Whether this step needs a full git worktree of the Personal Jarvis repo
    # as its workspace. Default True keeps every existing path byte-compatible:
    # old persisted plan payloads (which never carried the field) deserialize
    # fine, and any step the decomposer does NOT explicitly classify as a lean
    # external-artifact task gets the full, isolated worktree it always had.
    #
    # When False the orchestrator hands the worker a LEAN workspace — a fresh
    # empty `git init` repo with one initial commit — instead of a checkout of
    # the whole codebase. This is reserved for steps that use connected services
    # or produce standalone deliverables ("create an HTML file with today's
    # news") and have no affinity to this repo's code. Cloning and exploring the
    # repo otherwise burned 10+ minutes and >1.3M input tokens before a trivial
    # write (live mission 019eb17d, 2026-06-10).
    needs_repo: bool = True


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
            raise ValueError("MissionDecomposer: empty prompt")

        # A single step qualifies for a LEAN workspace only with positive
        # standalone evidence and no source affinity. Ambiguous prompts keep
        # the full worktree (needs_repo=True) — conservative by design.
        lean_eligible = _workspace_requirement(mission_prompt) is False

        # Heuristic 1: short prompts are never multi-step
        if len(mission_prompt) < SHORT_PROMPT_CHAR_LIMIT:
            return self._single_step_plan(
                mission_prompt, reason="short_prompt", needs_repo=not lean_eligible
            )

        # Heuristic 2: exactly one external-system marker is sufficient for 1 step
        marker_count = self._count_external_markers(mission_prompt)
        if marker_count <= 1:
            return self._single_step_plan(
                mission_prompt,
                reason="single_external_target",
                needs_repo=not lean_eligible,
            )

        # LLM path — Brain not bound -> fallback single-step.
        if self._brain is None:
            logger.info(
                "MissionDecomposer: brain=None, falling back to single-step plan"
            )
            return self._single_step_plan(
                mission_prompt,
                reason="no_brain_available",
                needs_repo=not lean_eligible,
            )

        # LLM decomposition
        decomposition_prompt = self._build_decomposition_prompt(mission_prompt)
        try:
            raw = await self._brain(decomposition_prompt)
        except Exception:  # noqa: BLE001
            logger.warning(
                "MissionDecomposer: brain call raised — fallback single-step",
                exc_info=True,
            )
            return self._single_step_plan(
                mission_prompt,
                reason="brain_error",
                needs_repo=not lean_eligible,
            )

        plan = self._parse_plan(raw, mission_prompt)
        if plan is None:
            logger.info("MissionDecomposer: parse failed, falling back to single-step")
            return self._single_step_plan(
                mission_prompt,
                reason="parse_failed",
                needs_repo=not lean_eligible,
            )

        return plan

    # --- Internals ---

    def _single_step_plan(
        self, mission_prompt: str, *, reason: str, needs_repo: bool = True
    ) -> MissionPlan:
        """Fallback: entire mission as a single step.

        ``needs_repo`` defaults to True for callers that have not classified a
        prompt. All built-in paths pass the deterministic repo-affinity result,
        including no-brain/error/parse fallbacks. This matters in copied or
        frozen installations: a repository-independent fallback remains
        runnable in a lean workspace even though no source checkout exists.
        """
        slug = _slugify(mission_prompt)[:40] or "task"
        return MissionPlan(
            steps=[
                Step(
                    slug=slug,
                    prompt=mission_prompt,
                    worker_cli="claude",
                    model="",  # let the worker pick its configured primary
                    needs_repo=needs_repo,
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
            '"allowed_tools": "Read,Edit,Write,Bash,Grep,Glob", '
            '"needs_repo": true | false }\n'
            "  ],\n"
            '  "n_workers": <int 1..5>,\n'
            '  "expected_output": "<one-sentence description of the deliverable>"\n'
            "}\n\n"
            "Rules:\n"
            "- DO NOT split if tasks share files (race conditions).\n"
            "- DO NOT include task_id (auto-generated).\n"
            "- Set needs_repo=true only when the step must read or modify the "
            "Personal Jarvis source checkout.\n"
            "- Set needs_repo=false for research, connected-service actions, "
            "and standalone artifacts that do not use Personal Jarvis source.\n"
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

        # Older model outputs predate ``needs_repo`` and therefore omit it.
        # Backfill those outputs from deterministic source-affinity rather than
        # allowing Step's persistence-safe default (True) to disable every
        # standalone mission in a copied/container distribution. Conversely,
        # an explicit model-provided False may never override clear source
        # affinity in either the original mission or the individual step.
        mission_requirement = _workspace_requirement(mission_prompt)
        raw_steps = data.get("steps")
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict):
                    continue
                step_prompt = raw_step.get("prompt")
                step_requirement = (
                    _workspace_requirement(step_prompt)
                    if isinstance(step_prompt, str)
                    else None
                )
                inferred = (
                    step_requirement
                    if step_requirement is not None
                    else mission_requirement
                )
                if inferred is not None:
                    # Deterministic evidence owns the boundary: a model cannot
                    # downgrade source work or unnecessarily disable a clearly
                    # remote/standalone task in a source-less installation.
                    raw_step["needs_repo"] = inferred
                elif "needs_repo" not in raw_step:
                    # Neither the step nor mission is classifiable. Preserve
                    # the fail-closed persistence default.
                    raw_step["needs_repo"] = True

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
