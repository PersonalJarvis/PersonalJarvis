"""Prompt templates for worker and reviewer spawns (Phase 8.3).

Plan reference: §6.3 (spawn prompts). These templates are the contact
point between LLM output (worker) and LLM input (reviewer).
All content flows through path references (AD-9 filesystem IPC), not
through string round-trips.

Default rubrics are read in Phase 8.4 from `[review.rubrics.*]` in
`jarvis.toml`; in Phase 8.3 they are hardcoded here.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.core.review.state import IterationRecord, RunState

# Plan-§6.4: Rubric items per task class. Phase 8.3 hardcoded; Phase 8.4
# reads from `[review.rubrics.<id>]` in jarvis.toml.
_DEFAULT_RUBRICS: dict[str, list[str]] = {
    "default": [
        "task_completion",
        "tool_output_fidelity",
        "completeness",
        "voice_friendliness",
        "tool_use_efficiency",
    ],
    "code_generation": [
        "task_completion",
        "no_stub_code",
        "tests_pass_locally",
        "no_secret_leakage",
        "voice_friendliness",
    ],
    "skill_authoring": [
        "frontmatter_valid",
        "trigger_keywords_unique",
        "instructions_actionable",
        "no_malicious_bash",
    ],
}


def get_rubric_items(rubric_id: str) -> list[str]:
    """Returns the rubric items for a rubric ID, falling back to `default`."""
    return list(_DEFAULT_RUBRICS.get(rubric_id, _DEFAULT_RUBRICS["default"]))


# ----------------------------------------------------------------------
# Feedback block (for worker re-spawns starting at iteration 2)
# ----------------------------------------------------------------------


def build_feedback_block(record: IterationRecord) -> str:
    """Formats the issues from a previous iteration as a worker hard-requirement.

    Plan-§6.3 template for iteration N>1: contains `summary` + each issue with
    severity, location, description, fix_hint.
    """
    if record.verdict is None:
        return ""
    verdict = record.verdict
    lines: list[str] = [
        f"## Reviewer feedback from iteration {record.iteration}",
        "",
        verdict.summary,
        "",
        "Issues to fix:",
    ]
    if not verdict.issues:
        lines.append("- (no issues listed; revise anyway based on summary)")
    else:
        for issue in verdict.issues:
            location = issue.location or "n/a"
            fix_hint = issue.fix_hint or "(no fix hint provided)"
            lines.extend(
                [
                    f"- [{issue.severity}] {location}: {issue.description}",
                    f"  Fix-Hint: {fix_hint}",
                ]
            )
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Worker-Prompt
# ----------------------------------------------------------------------


def build_worker_prompt(
    state: RunState,
    iteration: int,
    *,
    worker_output_path: Path,
) -> str:
    """Builds the full worker prompt for iteration `iteration`.

    At iteration 1: only the original task plus the path hint. From iteration 2
    onward: plus the feedback block from the previous iteration with issues.
    Plan-§6.3 worker template:

        ## Original task
        <task>

        ## Reviewer feedback from iteration {N-1}
        ...

        Write the corrected artifact to {worker_output_path}.
    """
    parts: list[str] = ["## Original task", "", state.task]

    if iteration > 1:
        # The last complete iteration with a verdict provides the feedback.
        previous = next(
            (
                r
                for r in reversed(state.iterations)
                if r.verdict is not None
            ),
            None,
        )
        if previous is not None:
            parts.extend(["", build_feedback_block(previous)])

    parts.extend(
        [
            "",
            f"Write the corrected artifact to {worker_output_path}.",
            "Output ONLY a brief 1-2 sentence summary on stdout — the full "
            "artifact lives at the path above.",
        ]
    )
    return "\n".join(parts)


# ----------------------------------------------------------------------
# Reviewer-Prompt
# ----------------------------------------------------------------------


def build_reviewer_prompt(
    state: RunState,
    iteration: int,
    *,
    worker_output_path: Path,
    verdict_schema_path: Path,
    rubric_items: list[str] | None = None,
) -> str:
    """Builds the full reviewer prompt for iteration `iteration`.

    Plan-§6.3 reviewer template:

        ## Task being evaluated
        <task>

        ## Worker output to review
        The worker wrote its artifact to: {worker_output_path}
        Use the Read tool to read it before evaluating.

        ## Rubric for this task
        {rubric_items}

        ## Required output schema
        {verdict_schema_inline}

        Now evaluate. Output JSON only.

    `rubric_items` is optional — None → `_DEFAULT_RUBRICS[state.rubric_id]`.
    """
    items = rubric_items if rubric_items is not None else get_rubric_items(
        state.rubric_id
    )
    rubric_block = "\n".join(f"- {item}" for item in items)

    return "\n".join(
        [
            "## Task being evaluated",
            "",
            state.task,
            "",
            "## Worker output to review",
            f"The worker wrote its artifact to: {worker_output_path}",
            "Use the Read tool to read it before evaluating.",
            "",
            "## Rubric for this task",
            rubric_block,
            "",
            "## Required output schema",
            f"See {verdict_schema_path} (also enforced by --json-schema).",
            "",
            "Now evaluate. Output JSON only.",
        ]
    )
