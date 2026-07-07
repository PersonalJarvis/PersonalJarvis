"""Worker and reviewer spawners for real pipeline integration (Phase 8.3).

Plan reference: §6.3 (spawn CLI), §AD-9 (filesystem IPC), §AD-10 (`--bare`),
§AD-13 (`--max-turns` surrogate).

Both spawners use the existing `HarnessManager` for subprocess lifecycle
(stream drain, timeout, cancellation). The spawners are the only place
where `OpenClaw` fields of `HarnessTask` are set — pipeline and tests
remain harness-agnostic.

`WorkerSpawner.spawn()` and `ReviewerSpawner.spawn()` match the signatures
of the `WorkerSpawn` and `ReviewerSpawn` type aliases from `pipeline.py`,
so they can be passed directly as callables.
"""
from __future__ import annotations

import json
import logging
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from jarvis.core.protocols import HarnessTask
from jarvis.core.review.errors import (
    HarnessUnavailable,
    ReviewerUnavailable,
    VerdictParseError,
    WorkerSpawnError,
)
from jarvis.core.review.io import RunDirectory
from jarvis.core.review.prompts import build_reviewer_prompt, build_worker_prompt
from jarvis.core.review.state import RunState
from jarvis.core.review.verdict import ReviewVerdict
from jarvis.harness.manager import HarnessManager

_LOG = logging.getLogger(__name__)

# Default path to the verdict JSON schema (relative to the package). Passed as
# `--json-schema @<path>` to claude.
_DEFAULT_VERDICT_SCHEMA_PATH = Path(
    str(files("jarvis.core.review").joinpath("verdict_schema.json"))
)

# Plan-§AD-13: worker tools complete, reviewer tools EXACTLY Read/Grep/Glob.
DEFAULT_WORKER_TOOLS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Grep",
    "Glob",
)
DEFAULT_REVIEWER_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")

# Plan-§AD-13 surrogate: cost cap as a stand-in for `--max-turns`.
DEFAULT_WORKER_BUDGET_USD = 0.30
DEFAULT_REVIEWER_BUDGET_USD = 0.05

# ----------------------------------------------------------------------
# Harness resolution (AP-23 wave-2 finding 5)
# ----------------------------------------------------------------------
#
# Candidate names for the Jarvis-Agent worker harness, in preference order.
# ``jarvis_agent`` is the canonical name (CLAUDE.md §4 rename); ``openclaw``
# is the pre-rename back-compat alias, mirroring the
# ``AliasChoices("jarvis_agent", "openclaw")`` pattern already used for the
# ``[harness.jarvis_agent]`` config block (jarvis/core/config.py). Neither is
# ever ASSUMED present (AP-21: gate on registration, not a hardcoded name) —
# both spawners check ``HarnessManager.available()`` at spawn time. As of
# 2026-07-07 NEITHER name is a registered ``jarvis.harness`` entry point on
# any install (Welle-4 removed the old subprocess bridge and no replacement
# has shipped yet), so this resolves to ``None`` and the spawners raise
# ``HarnessUnavailable`` — never the raw ``KeyError`` that
# ``HarnessManager.get()`` would otherwise throw for an unregistered name.
# The day a real worker harness registers under either name, this starts
# resolving it with no further code change required.
_WORKER_HARNESS_CANDIDATES: tuple[str, ...] = ("jarvis_agent", "openclaw")


def _harness_is_registered(manager: HarnessManager, name: str) -> bool:
    """True if `name` is a live `jarvis.harness` entry point on `manager`.

    Fails closed: a broken/minimal manager (e.g. a test double without
    `available()`) is treated as "not registered", never crashes the caller.
    """
    try:
        return name in set(manager.available())
    except Exception:  # noqa: BLE001 — resolution must never crash the spawn
        return False


def _resolve_worker_harness_name(manager: HarnessManager) -> str | None:
    """Returns the first registered candidate harness name, or `None`."""
    for candidate in _WORKER_HARNESS_CANDIDATES:
        if _harness_is_registered(manager, candidate):
            return candidate
    return None


# ----------------------------------------------------------------------
# Helper: Stream-Drain
# ----------------------------------------------------------------------


async def _drain_dispatch(
    manager: HarnessManager, harness_name: str, task: HarnessTask
) -> tuple[str, str, int, int, float]:
    """Accumulates a `HarnessManager.dispatch` stream into final tuples.

    Returns: `(stdout, stderr, exit_code, duration_ms, cost_usd)`.
    """
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    exit_code = -1
    duration_ms = 0
    cost_usd = 0.0
    async for r in manager.dispatch(harness_name, task):
        if r.stdout:
            stdout_buf.append(r.stdout)
        if r.stderr:
            stderr_buf.append(r.stderr)
        if r.is_final:
            exit_code = r.exit_code
            duration_ms = r.duration_ms
        if r.cost_usd:
            cost_usd += r.cost_usd
    return (
        "".join(stdout_buf),
        "".join(stderr_buf),
        exit_code,
        duration_ms,
        cost_usd,
    )


# ----------------------------------------------------------------------
# WorkerSpawner
# ----------------------------------------------------------------------


class WorkerSpawner:
    """Spawns the worker subagent via the registered Jarvis-Agent harness.

    Writes the worker stdout to `data/review/runs/<run_id>/iter-N/worker.out`
    and returns the content as a string. The pipeline uses the string for
    post-checks and best-of-pick (audit + display); the reviewer reads the
    file path directly via the `Read` tool — no string round-trip through the
    reviewer prompt (AD-9).
    """

    def __init__(
        self,
        *,
        harness_manager: HarnessManager,
        runs_root: Path,
        agent_name: str = "jarvis-worker",
        harness_name: str | None = None,
        allowed_tools: tuple[str, ...] = DEFAULT_WORKER_TOOLS,
        max_budget_usd: float = DEFAULT_WORKER_BUDGET_USD,
        timeout_s: int = 600,
    ) -> None:
        self._manager = harness_manager
        self._runs_root = runs_root
        self._agent_name = agent_name
        # `None` (the default) means "auto-resolve at spawn time via
        # `_resolve_worker_harness_name`" — never a hardcoded literal
        # (AP-21/AP-23 wave-2 finding 5). An explicit override is still
        # honored, but is re-validated against the registry at spawn time.
        self._harness_name = harness_name
        self._allowed_tools = allowed_tools
        self._max_budget_usd = max_budget_usd
        self._timeout_s = timeout_s

    async def spawn(self, state: RunState, iteration: int) -> str:
        """Executes the worker spawn, writes iter-N/worker.out, and
        returns the worker stdout as a string.
        """
        harness_name = self._harness_name
        if harness_name is None:
            harness_name = _resolve_worker_harness_name(self._manager)
        elif not _harness_is_registered(self._manager, harness_name):
            harness_name = None
        if harness_name is None:
            raise HarnessUnavailable(
                "no worker harness is registered on this install"
            )

        run_dir = RunDirectory(self._runs_root, state.run_id).ensure()
        worker_output_path = run_dir.worker_output_path(iteration)

        prompt = build_worker_prompt(
            state, iteration, worker_output_path=worker_output_path
        )
        task = HarnessTask(
            prompt=prompt,
            timeout_s=self._timeout_s,
        )

        stdout, stderr, exit_code, duration_ms, cost_usd = await _drain_dispatch(
            self._manager, harness_name, task
        )

        if exit_code != 0:
            raise WorkerSpawnError(
                f"Worker-Spawn ({harness_name}) exit_code={exit_code} "
                f"stderr={stderr[:300]!r}"
            )

        # If the worker has written the artifact itself it is already on disk;
        # if not (e.g. a worker that only writes to stdout), we persist the
        # stdout. Both cases produce a valid iter-N/worker.out — the reviewer
        # can always read it.
        if not worker_output_path.exists() or worker_output_path.stat().st_size == 0:
            run_dir.write_worker_output(iteration, stdout)

        _LOG.info(
            "review-pipeline %s iter=%d: worker spawn ok (%dms, $%.4f)",
            state.run_id,
            iteration,
            duration_ms,
            cost_usd,
        )

        return run_dir.read_worker_output(iteration)


# ----------------------------------------------------------------------
# ReviewerSpawner
# ----------------------------------------------------------------------


class ReviewerSpawner:
    """Spawns the reviewer subagent via the registered Jarvis-Agent harness.

    The reviewer receives the **path** to the worker output file, not the
    content — filesystem IPC (AD-9). Returns a parsed `ReviewVerdict`.
    On JSON parse error: `VerdictParseError`. On subprocess crash / timeout /
    empty stdout: `ReviewerUnavailable`. Both are treated by the loop
    controller as "needs_revision with retry" (plan §7-table, AD-7).
    """

    def __init__(
        self,
        *,
        harness_manager: HarnessManager,
        runs_root: Path,
        verdict_schema_path: Path | None = None,
        agent_name: str = "jarvis-reviewer",
        harness_name: str | None = None,
        allowed_tools: tuple[str, ...] = DEFAULT_REVIEWER_TOOLS,
        max_budget_usd: float = DEFAULT_REVIEWER_BUDGET_USD,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "low",  # AD-13
        timeout_s: int = 120,
    ) -> None:
        self._manager = harness_manager
        self._runs_root = runs_root
        self._verdict_schema_path = (
            verdict_schema_path or _DEFAULT_VERDICT_SCHEMA_PATH
        )
        self._agent_name = agent_name
        # `None` (the default) means "auto-resolve at spawn time via
        # `_resolve_worker_harness_name`" — never a hardcoded literal
        # (AP-21/AP-23 wave-2 finding 5). An explicit override is still
        # honored, but is re-validated against the registry at spawn time.
        self._harness_name = harness_name
        self._allowed_tools = allowed_tools
        self._max_budget_usd = max_budget_usd
        self._effort = effort
        self._timeout_s = timeout_s

    async def spawn(
        self, state: RunState, worker_output: str, iteration: int
    ) -> ReviewVerdict:
        """`worker_output` is nominal — the reviewer reads the path itself.

        Plan-§AD-9: no string round-trip through the reviewer prompt;
        the reviewer already has the worker artifact via WorkerSpawner at
        `iter-N/worker.out` and references it by path.
        """
        del worker_output  # unused, siehe Docstring
        harness_name = self._harness_name
        if harness_name is None:
            harness_name = _resolve_worker_harness_name(self._manager)
        elif not _harness_is_registered(self._manager, harness_name):
            harness_name = None
        if harness_name is None:
            raise HarnessUnavailable(
                "no reviewer harness is registered on this install"
            )

        run_dir = RunDirectory(self._runs_root, state.run_id)
        worker_output_path = run_dir.worker_output_path(iteration)

        prompt = build_reviewer_prompt(
            state,
            iteration,
            worker_output_path=worker_output_path,
            verdict_schema_path=self._verdict_schema_path,
        )
        task = HarnessTask(
            prompt=prompt,
            timeout_s=self._timeout_s,
        )

        stdout, stderr, exit_code, duration_ms, _cost = await _drain_dispatch(
            self._manager, harness_name, task
        )

        if exit_code != 0:
            raise ReviewerUnavailable(
                f"Reviewer-Spawn exit_code={exit_code} stderr={stderr[:300]!r}"
            )

        verdict_payload = _extract_verdict_json(stdout)
        if verdict_payload is None:
            raise VerdictParseError(
                f"Reviewer stdout does not contain valid JSON; "
                f"head={stdout[:300]!r}"
            )

        try:
            verdict = ReviewVerdict.model_validate(verdict_payload)
        except Exception as exc:
            # Pydantic ValidationError and all other rejection paths land here.
            raise VerdictParseError(
                f"Reviewer-JSON parsed, aber Schema-Verletzung: {exc}"
            ) from exc

        # Persist verdict to disk (AD-11 — separate run artifact store).
        run_dir.write_verdict(iteration, verdict_payload)

        _LOG.info(
            "review-pipeline %s iter=%d: reviewer ok (%dms, status=%s, score=%.2f)",
            state.run_id,
            iteration,
            duration_ms,
            verdict.status.value,
            verdict.score,
        )

        return verdict


# ----------------------------------------------------------------------
# JSON extraction (robust against prose prefix)
# ----------------------------------------------------------------------


def _extract_verdict_json(stdout: str) -> dict[str, Any] | None:
    """Attempts to extract the verdict JSON from the reviewer stdout.

    The reviewer is instructed to output JSON only (`--output-format json` +
    subagent body "Output ONLY valid JSON"). If prose ends up around the JSON
    anyway, we search for the first `{` window that is parseable.
    """
    text = (stdout or "").strip()
    if not text:
        return None
    # Fast path: whole-string parse.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    # Fallback: search for the first `{...}` window that is parseable.
    start = text.find("{")
    while start != -1:
        end = text.rfind("}")
        while end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                end = text.rfind("}", start, end)
                continue
            break
        start = text.find("{", start + 1)
    return None
