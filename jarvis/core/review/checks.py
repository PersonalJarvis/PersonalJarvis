"""Deterministic pre/post-checks without LLM calls (Phase 8.1).

Plan reference: §6.1, §AD-5.

Pre-checks run before the worker spawn and decide whether a worker is
started at all (task plausible, prompt not empty). Post-checks run
between the worker spawn and the reviewer spawn and consume no LLM budget
— they eliminate the cheap 80 % of defects without invoking the reviewer.

Pattern (Composite): each check is a stateless callable
`(input) -> CheckResult`. Pre- and post-checks share the same structural
signature — the runner is agnostic and calls every callable with the raw
input string. On the first `ok=False` the runner short-circuits: later
checks are not called (plan §AD-5: save reviewer spawns).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

# ----------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """Result of a single check call."""

    ok: bool
    name: str
    message: str = ""


@dataclass(frozen=True)
class RunnerResult:
    """Aggregate result of a pre- or post-check runner.

    `executed` contains all checks that were actually called
    (no downstream check runs after a failure — short-circuit guarantee).
    """

    ok: bool
    failed: CheckResult | None
    executed: tuple[CheckResult, ...]


# A check is a callable that maps a raw string (task or worker output)
# to a CheckResult. Pre- and post-checks share the same signature so that
# the runner can iterate over both lists agnostically.
Check = Callable[[str], CheckResult]


# ----------------------------------------------------------------------
# Runner (composite pattern)
# ----------------------------------------------------------------------


class _CheckRunner:
    """Shared runner logic. Subclasses are pure markers."""

    def __init__(self, checks: list[Check]) -> None:
        self._checks: list[Check] = list(checks)

    @property
    def checks(self) -> tuple[Check, ...]:
        return tuple(self._checks)

    def run(self, payload: str) -> RunnerResult:
        executed: list[CheckResult] = []
        for check in self._checks:
            result = check(payload)
            executed.append(result)
            if not result.ok:
                return RunnerResult(
                    ok=False, failed=result, executed=tuple(executed)
                )
        return RunnerResult(ok=True, failed=None, executed=tuple(executed))


class PreCheckRunner(_CheckRunner):
    """Runs pre-checks sequentially, short-circuiting on the first failure.

    Input is the raw task string. Returns a `RunnerResult`; on failure
    `failed` holds the aborting check and `executed` contains all
    preceding checks plus the failing one — later checks were not called.
    """


class PostCheckRunner(_CheckRunner):
    """Runs post-checks sequentially, short-circuiting on the first failure.

    Input is the raw worker output string. Semantics identical to
    `PreCheckRunner`.
    """


# ----------------------------------------------------------------------
# Built-in pre-checks
# ----------------------------------------------------------------------


def task_not_empty(task: str) -> CheckResult:
    """Pre: task must have more than 10 characters after `strip()`.

    Plan §6.1: `len(task) > 10`. The threshold is intentionally not 0 —
    single-word tasks ("help", "status") are handled on the direct
    main-Jarvis path, not through the review pipeline.
    """
    stripped = task.strip()
    if len(stripped) > 10:
        return CheckResult(ok=True, name="task_not_empty")
    return CheckResult(
        ok=False,
        name="task_not_empty",
        message=f"task too short: {len(stripped)} chars (need >10)",
    )


# ----------------------------------------------------------------------
# Built-in post-checks
# ----------------------------------------------------------------------


def output_not_empty(output: str) -> CheckResult:
    """Post: worker output must not be empty after `strip()`."""
    if len(output.strip()) > 0:
        return CheckResult(ok=True, name="output_not_empty")
    return CheckResult(
        ok=False,
        name="output_not_empty",
        message="worker output is empty",
    )


def make_output_budget_check(max_output_chars: int) -> Check:
    """Factory: check that verifies `len(output) < max_output_chars`.

    The threshold is configurable (plan §6.4 `[review]` section), hence
    the closure pattern instead of a global constant.
    """
    if max_output_chars <= 0:
        raise ValueError("max_output_chars must be > 0")

    def output_within_budget(output: str) -> CheckResult:
        if len(output) < max_output_chars:
            return CheckResult(ok=True, name="output_within_budget")
        return CheckResult(
            ok=False,
            name="output_within_budget",
            message=(
                f"output exceeds budget: {len(output)} chars "
                f"(limit {max_output_chars})"
            ),
        )

    return output_within_budget


# Detects stub markers as standalone lines (not inline comments like
# `x = 1  # TODO: rename`). MULTILINE so that `^`/`$` apply per line.
_STUB_LINE_RE = re.compile(r"^\s*(TODO|FIXME|XXX|pass)\s*$", re.MULTILINE)


def no_stub_code(output: str) -> CheckResult:
    """Post: output must not contain stub markers as a whole line.

    Pattern matches `TODO`, `FIXME`, `XXX`, `pass` as a standalone line
    (with arbitrary leading/trailing whitespace). Inline comments like
    `# TODO: foo` are NOT flagged — they are often legitimate.
    """
    match = _STUB_LINE_RE.search(output)
    if match is None:
        return CheckResult(ok=True, name="no_stub_code")
    return CheckResult(
        ok=False,
        name="no_stub_code",
        message=f"stub marker present: {match.group(1)!r}",
    )


def valid_json(output: str) -> CheckResult:
    """Post (optional): output must be valid JSON.

    Plan §6.1 marks this as optional ("when `expect_json=true`") —
    the pipeline caller adds this check to the runner only when the
    reviewer expects JSON (e.g. `--json-schema`-enforced outputs).
    """
    try:
        json.loads(output)
    except json.JSONDecodeError as exc:
        return CheckResult(
            ok=False,
            name="valid_json",
            message=f"output not valid JSON: {exc.msg} at line {exc.lineno}",
        )
    return CheckResult(ok=True, name="valid_json")
