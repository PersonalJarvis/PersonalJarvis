"""Kontrollierer.Orchestrator — THE HEART of Phase 6.

Wires together:
- MissionDecomposer -> MissionPlan (1..5 Steps)
- WorkerProtocol-Factory -> spawn per step in the worktree
- CriticRunner -> verdict per worker iteration
- ReflectionMemory -> episodic memory between iterations
- BudgetTracker -> cost discipline
- MissionManager -> state-machine transitions

Concurrency:
- `asyncio.TaskGroup` (Python 3.11+) + `asyncio.Semaphore(MAX_WORKERS)` —
  each step coroutine wraps try/except so that a single failing task does not
  trigger a full TaskGroup cancellation.
- State-machine transitions are serialised via `_state_lock` per mission so
  that parallel tasks do not interfere with each other.

State-machine model (simplified for MVP):
- Mission state: PENDING -> RUNNING -> CRITIQUING -> APPROVED|FAILED.
- Per-task iteration is tracked internally (not via the state machine); the
  mission-level state only reflects the coarse lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable, Final, Literal

from ...core.process_utils import NO_WINDOW_CREATIONFLAGS
from ..budget import BudgetExceeded, BudgetTracker
from ..critic.reflections import ReflectionMemory
from ..critic.runner import MAX_CRITIC_LOOPS, CriticRunner
from ..critic.verdict import (
    CriticSchemaInvalid,
    CriticTimeout,
    CriticVerdict,
    CriticVerdictInconsistent,
    is_approval_valid,
)
from ..events import (
    CriticVerdictReady,
    EventEnvelope,
    MissionApproved,
    MissionFailed,
    MissionPlanReady,
    WorkerCorrectionRequired,
    WorkerDraftReady,
    WorkerKilled,
    WorkerSpawned,
    now_ms,
)
from ..isolation.worktree import WorktreeManager
from ..worker_runtime.workspace import materialize_worker_contract
from ..manager import MissionManager
from ..stream_evidence import (
    extract_write_targets,
    readonly_answer,
    summarize_answers,
)
from .deliverable import (
    build_deliverable_summary,
    build_delivered_summary,
    deliver_to_user_folder,
)
from ..safety import (
    filter_diff_paths,
    has_high_severity,
    scan as injection_scan,
)
from ..state_machine import IllegalStateTransition, MissionState
from ..workers.base import WorkerProtocol
from .decomposer import MissionDecomposer, MissionPlan, Step

logger = logging.getLogger(__name__)


MAX_WORKERS_PER_MISSION: Final[int] = 5
"""ADR-0009 + jarvis.toml [phase6.orchestrator]: max_workers_per_mission."""


# BUG-LIVE-05 (Recon-Agent 2, 2026-05-16): when persona files (AGENTS.md
# etc.) end up in the worktree diff, the Critic mistakes them for worker
# output and falsely APPROVES the mission even though the worker did
# zero file_write tool calls (hallucinated "Habe Datei erstellt"). Strip
# these well-known managed paths before the empty-diff check.
_MANAGED_PERSONA_FILES: Final[frozenset[str]] = frozenset({
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    ".openclaw/workspace-state.json",
})

# Directory names whose contents are never worker deliverables — git
# internals, the materialized OpenClaw state, and build/cache junk. The
# ``--ignored`` enumeration union in ``_archive_task_artifacts`` (added by
# the 2026-05-27 hardening audit to capture gitignored deliverables such as
# ``output.log``) would otherwise surface this noise into ``artifacts/files/``
# — the exact Outputs-UI garbage Wave 3 (2026-05-26) removed.
_JUNK_DIR_NAMES: Final[frozenset[str]] = frozenset({
    ".git",
    ".openclaw",
    "openclaw_state",
    "node_modules",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
})


def _is_deliverable_path(rel: str) -> bool:
    """True if a worktree-relative path is a genuine worker deliverable.

    False for managed worker-contract files (``AGENTS.md`` etc.) and for
    anything inside a git-internal / state / build-cache directory. Used to
    filter the untracked-file enumeration before copying into
    ``artifacts/files/`` so the Outputs UI shows only real deliverables.
    """
    norm = rel.replace("\\", "/").strip("/")
    if not norm:
        return False
    if norm in _MANAGED_PERSONA_FILES:
        return False
    return not any(seg in _JUNK_DIR_NAMES for seg in norm.split("/"))


def _real_diff_is_empty(diff_text: str) -> bool:
    """True iff the diff carries no foreign hunks after stripping
    managed persona files.

    Walks `diff --git a/<F> b/<F>` blocks and drops hunks whose b/<F> is
    in the managed allowlist. A diff that contains only managed-file
    additions plus an untracked-trailer that points at managed files is
    considered empty.
    """
    if not diff_text or not diff_text.strip():
        return True

    real_lines: list[str] = []
    in_managed_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            target = parts[1].strip() if len(parts) == 2 else None
            in_managed_hunk = (
                target in _MANAGED_PERSONA_FILES if target else False
            )
            if not in_managed_hunk:
                real_lines.append(line)
            continue
        if line.startswith("# untracked-not-in-diff:"):
            real_lines.append(line)
            continue
        if line.startswith("# - "):
            entry = line[4:].strip()
            if entry not in _MANAGED_PERSONA_FILES:
                real_lines.append(line)
            continue
        if not in_managed_hunk:
            real_lines.append(line)

    meaningful = [
        ln for ln in real_lines
        if ln.strip() and not ln.startswith("# untracked-not-in-diff:")
    ]
    return not meaningful


# Cap on the content embedded for a verified external file. Large enough for a
# typical document deliverable, small enough not to blow the Critic's prompt.
_EXTERNAL_VERIFY_MAX_CHARS: Final[int] = 8000


def _path_is_within(child: Path, parent: Path) -> bool:
    """True if ``child`` is ``parent`` or lives somewhere beneath it.

    Used to tell an out-of-worktree deliverable from an in-worktree one
    (the latter is already captured by ``git diff`` and must not be
    double-reported). Defensive: cross-drive / malformed comparisons on
    Windows return False rather than raising.
    """
    try:
        return child == parent or child.is_relative_to(parent)
    except (ValueError, AttributeError, OSError):
        return False


def _format_external_write_block(path: Path, raw_bytes: bytes) -> str:
    """Render a verified external deliverable as a diff block for the Critic.

    The block is deliberately NOT a ``diff --git`` hunk: the archive's
    new-file regex keys on ``diff --git``, so a distinct ``diff --external-target``
    header keeps the external file out of the worktree-relative archive copy
    loop while still being *meaningful* to :func:`_real_diff_is_empty` (it does
    not start with ``# untracked-not-in-diff:``). Content lines are ``+``-prefixed
    so any embedded ``diff --git`` / ``# untracked`` text inside the file cannot
    be mis-parsed as diff control lines.
    """
    n = len(raw_bytes)
    p = str(path)
    header = (
        f"diff --external-target b/{p}\n"
        f"# verified-external-write: {p}\n"
        f"# ground-truth: file exists on disk ({n} bytes); a non-errored "
        f"Write/Edit tool_use targeting this exact path is present in this "
        f"iteration's worker stream. This is on-disk-verified output, NOT a "
        f"log claim — treat it as real delivered content.\n"
    )
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return header + f"# (binary file, {n} bytes — content not shown)\n"
    truncated = text[:_EXTERNAL_VERIFY_MAX_CHARS]
    note = (
        ""
        if len(text) <= _EXTERNAL_VERIFY_MAX_CHARS
        else f"# (content truncated to {_EXTERNAL_VERIFY_MAX_CHARS} of "
        f"{len(text)} chars)\n"
    )
    # A 0-byte deliverable (touch-only task) is legitimate — label it so the
    # Critic does not read the absent `+` content as a parse glitch.
    body = (
        "# (empty file)"
        if not truncated
        else "\n".join("+" + ln for ln in truncated.splitlines())
    )
    return header + f"--- /dev/null\n+++ b/{p}\n" + note + body + "\n"


def _strip_managed_persona_hunks(diff_text: str) -> str:
    """Return ``diff_text`` with managed worker-contract hunks removed.

    ``git add -A`` stages the materialized contract files (AGENTS.md etc.)
    because ``.git/info/exclude`` does NOT gate an explicit ``add`` — it only
    hides files from *untracked* listings. Those static contract files are not
    worker output and must never reach the Critic: a ~2 KB AGENTS.md hunk in
    the reviewed diff re-opens the BUG-LIVE-05 false-APPROVE vector (the Critic
    mistakes the contract prose for delivered work). Stripping here keeps every
    downstream consumer clean — the Critic prompt, the ``WorkerDraftReady``
    event, and the archived ``diff.patch`` — so they see only real changes.

    Mirrors the hunk-walk in :func:`_real_diff_is_empty`: a ``diff --git
    a/<F> b/<F>`` line opens a hunk; lines belong to it until the next header.
    """
    if not diff_text or not diff_text.strip():
        return diff_text

    kept: list[str] = []
    in_managed = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            target = parts[1].strip() if len(parts) == 2 else None
            in_managed = (
                target in _MANAGED_PERSONA_FILES if target else False
            )
            if not in_managed:
                kept.append(line)
            continue
        if line.startswith("# - "):
            # untracked-trailer entry: keep only non-managed paths
            entry = line[4:].strip()
            if entry not in _MANAGED_PERSONA_FILES:
                kept.append(line)
            continue
        if not in_managed:
            kept.append(line)

    return "\n".join(kept)


_NEW_FILE_DIFF_HEADER_RE: re.Pattern[str] = re.compile(
    r"^diff --git (\"?)a/(?P<path>[^\"\n]+?)\1 (\"?)b/(?P=path)\3\nnew file mode",
    re.MULTILINE,
)


_GIT_C_SIMPLE_ESCAPES: Final[dict[str, int]] = {
    "a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13,
    '"': 34, "\\": 92,
}


def _decode_git_quoted_path(raw: str) -> str:
    """Decode git's C-style quoted path back to the real on-disk name.

    With ``core.quotepath=true`` (git's default) non-ASCII bytes in a path
    are octal-escaped (``ä`` → ``\\303\\244``) and a handful of control
    characters are backslash-escaped (``\\t``, ``\\n``, ``\\\\``, ``\\"``).
    A bilingual/German assistant routinely produces umlaut deliverable
    names (``Werbungä.html``, ``Lebenslauf-Müller.pdf``); without decoding,
    the archive copy loop builds ``worktree / 'Werbung\\303\\244.html'``,
    ``is_file()`` is False, and the deliverable is silently dropped from
    ``artifacts/files/`` (HIGH finding 2026-05-27 hardening audit).

    No-op for paths that carry no backslash escape (the common ASCII case).
    Best-effort: undecodable byte sequences fall back to U+FFFD rather than
    raising, so a malformed path never aborts the archive step.
    """
    if "\\" not in raw:
        return raw
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            # 3-digit octal byte escape (\303). First digit is 0-3, but
            # accept any octal triple defensively.
            if (
                nxt in "01234567"
                and i + 4 <= n
                and all(c in "01234567" for c in raw[i + 1 : i + 4])
            ):
                out.append(int(raw[i + 1 : i + 4], 8))
                i += 4
                continue
            if nxt in _GIT_C_SIMPLE_ESCAPES:
                out.append(_GIT_C_SIMPLE_ESCAPES[nxt])
                i += 2
                continue
            # Unknown escape — keep the backslash literally and move on.
            out.append(0x5C)
            i += 1
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def _extract_new_file_paths_from_diff(diff: str) -> list[str]:
    """Recover paths of newly created files from a unified diff.

    Live regression 2026-05-27 (mission_019e6858-ab9a): worker wrote
    test.html correctly, diff.patch carried the content, mission ended
    SUCCESS — but ``artifacts/files/`` was empty so the user saw only
    diff.patch + stream.jsonl as "deliverables" (which is garbage). Root
    cause: ``_capture_diff`` runs ``git add -A`` per Critic iteration,
    moving the new file into the index; by the time
    ``_archive_task_artifacts`` later calls ``git ls-files --others``,
    the file is no longer untracked and the ``shutil.copy2`` loop sees
    an empty list. This helper parses paths from the diff text itself
    (index-state independent). Pure stdlib, deterministic.

    Paths quoted/octal-escaped by ``core.quotepath`` are decoded back to
    the real UTF-8 name via :func:`_decode_git_quoted_path` so non-ASCII
    deliverable names round-trip (HIGH finding 2026-05-27).
    """
    if not diff:
        return []
    paths: list[str] = []
    for match in _NEW_FILE_DIFF_HEADER_RE.finditer(diff):
        raw = match.group("path")
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        paths.append(_decode_git_quoted_path(raw))
    return paths


# Type aliases
WorkerFactoryFn = Callable[[Step], WorkerProtocol]
EnvBuilderFn = Callable[[Path], dict[str, str]]
JobFactoryFn = Callable[[], Any]  # () -> WindowsJobObject (async context manager)


class TaskOutcome:
    """Internal — result of a single task-loop iteration."""

    APPROVED = "approved"
    EXHAUSTED = "exhausted"
    REJECTED = "rejected"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"
    # Live forensic 2026-05-16 (mission_019e3288): iter0 produced a real
    # 1237-byte diff, but the Critic spawn returned non-zero rc twice in
    # a row (EPERM symlink on `plugin-skills/browser-automation`, then
    # `Unknown agent id "critic"`). The runner mapped both into the
    # `revise`/`empty_diff` deterministic fast-path, and the loop ate
    # iter0's real work over iter1+iter2's no-op overwrites. Distinct
    # outcome here so the failure-reason mapper can surface
    # `critic_unavailable` instead of the misleading
    # `critic_loop_exhausted`.
    CRITIC_UNAVAILABLE = "critic_unavailable"
    # 2026-05-27 hardening finding #8: a worktree-create failure (200-char
    # path cap ValueError or `git worktree add` index-lock CalledProcessError)
    # used to return the generic ERROR, which aggregated to the `task_error`
    # readback — indistinguishable from a real worker subprocess crash. This
    # distinct outcome lets the failure-reason mapper surface
    # `worktree_setup_failed` ("Konnte keinen Arbeitsbereich anlegen.") so the
    # user hears an actionable cause instead of "Der Worker ist abgebrochen."
    SETUP_FAILED = "setup_failed"


class Kontrollierer:
    """Orchestrator class that drives a mission end-to-end."""

    def __init__(
        self,
        *,
        manager: MissionManager,
        decomposer: MissionDecomposer,
        critic_runner: CriticRunner,
        worktree_mgr: WorktreeManager,
        env_builder: EnvBuilderFn,
        budget: BudgetTracker,
        worker_factory: WorkerFactoryFn,
        job_factory: JobFactoryFn,
        isolation_root: Path,
        max_workers: int = MAX_WORKERS_PER_MISSION,
        # Cross-mission concurrency cap (2026-05-24): the worker AND the critic
        # both shell out to `claude` over the same Claude Max OAuth. When the
        # user fires several missions in a burst (conversation mode), N parallel
        # claude-direct critics overload the account and return prose instead of
        # JSON -> `critic_unavailable` even though the workers wrote real files
        # (live repro 2026-05-24: 5 missions in 2 min, 3 failed on the critic).
        # A global semaphore serialises the heavy claude phase to N missions at
        # a time; the rest queue. The spawn ACK is returned before run_mission
        # (fire-and-forget), so queuing never blocks the voice response.
        # 2026-05-28: default lowered 2 -> 1. Live forensics showed even TWO
        # concurrent claude-direct missions (each running a worker AND a critic
        # over the same Claude Max OAuth) saturate the subscription: the CLI
        # hangs at 0-byte output until the 630s cap (task_error) or the critic
        # throttles (critic_unavailable). 82% of task_error rows overlapped
        # another mission within 90s. Serialising to 1 removes the contention;
        # missions queue behind the in-flight one (throughput is irrelevant for
        # a personal assistant, correctness is not). Override per deployment via
        # [phase6.orchestrator].max_concurrent_missions.
        max_concurrent_missions: int = 1,
        # Phase-5 safety hooks (all optional — None = no-op):
        safety_enabled: bool = True,
        extra_blocked_globs: tuple[str, ...] = (),
    ) -> None:
        self._manager = manager
        self._decomposer = decomposer
        self._runner = critic_runner
        self._worktrees = worktree_mgr
        self._env_builder = env_builder
        self._budget = budget
        self._worker_factory = worker_factory
        self._job_factory = job_factory
        self._isolation_root = isolation_root
        self._max_workers = max(1, min(max_workers, MAX_WORKERS_PER_MISSION))
        # Global cap on concurrent heavy (claude worker+critic) mission phases.
        self._mission_sem = asyncio.Semaphore(max(1, max_concurrent_missions))
        self._state_locks: dict[str, asyncio.Lock] = {}
        self._safety_enabled = safety_enabled
        self._extra_blocked_globs = tuple(extra_blocked_globs)
        # Per-task per-iteration diff capture. Keyed by `step.task_id`,
        # value is a list of (iteration_index, diff_text). Populated by
        # `_run_iterations` after every `_capture_diff` call so the
        # archive step can preserve the *best* iteration's output even
        # when later iterations overwrite the worktree with a no-op.
        # See live forensic 2026-05-16 mission_019e3288.
        self._task_iter_diffs: dict[str, list[tuple[int, str]]] = {}
        # Per-mission worker answers for read-only/informational tasks (empty
        # diff + tool evidence). Surfaced as MissionApproved.summary_de so the
        # voice readback speaks the actual answer instead of "Mission
        # abgeschlossen." See jarvis.missions.stream_evidence.readonly_answer.
        self._task_answers: dict[str, list[str]] = {}

    async def run_mission(self, mission_id: str) -> MissionState:
        """Runs a mission end-to-end and returns the final state.

        Returns: APPROVED | FAILED | CANCELLED | TIMED_OUT.
        """
        view = await self._manager.mission(mission_id)
        if view is None:
            raise KeyError(f"Mission nicht gefunden: {mission_id}")

        # Idempotency guard: a SUCCESSFULLY-completed (APPROVED) or explicitly
        # CANCELLED mission must never be re-run. Both the REST path
        # (missions_routes background_task) and the voice path (spawn_openclaw)
        # call run_mission with no such check, so a stale re-dispatch re-emitted
        # a second created->plan->approved lifecycle with no worker run (live
        # 2026-05-29 on mission_019e70d0 — a duplicate Outputs card + re-spend).
        # Error states (FAILED / TIMED_OUT / ESCALATED / ORCHESTRATOR_CRASH)
        # stay re-runnable ON PURPOSE: a crash_recovery'd mission must still be
        # retryable to completion (test_recovery_then_rerun_is_idempotent).
        if view.state in (MissionState.APPROVED, MissionState.CANCELLED):
            logger.info(
                "run_mission: %s already %s — skipping re-run",
                mission_id, view.state.value,
            )
            return view.state

        # PENDING -> RUNNING
        await self._safe_transition(mission_id, MissionState.RUNNING, "kontrollierer-start")

        # Decomposer: determine the plan.
        try:
            plan = await self._decomposer.decompose(view.prompt)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Kontrollierer: decompose failed")
            await self._fail_mission(mission_id, f"decompose_failed: {exc}")
            return MissionState.FAILED

        # MissionPlanReady on bus + DB
        await self._publish_plan_ready(mission_id, plan)

        # Mission directory for reflections + logs.
        # 2026-05-17 (BUG-LIVE-10): UUID7 IDs share the first 8 hex chars
        # when dispatched within the same millisecond — five sequential
        # missions in a burst test all collapsed into a single
        # `mission_019e3600` directory and overwrote each other's
        # artifacts. 13 chars (`019e3600-a84e`) include the random
        # block and stay collision-free under any realistic dispatch
        # cadence while keeping paths readable.
        mission_dir = self._isolation_root / f"mission_{mission_id[:13]}"
        mission_dir.mkdir(parents=True, exist_ok=True)
        reflections = ReflectionMemory(mission_dir)

        # Parallel task execution with semaphore limit
        sem = asyncio.Semaphore(min(plan.n_workers, self._max_workers))
        task_outcomes: list[str] = []

        async def _run(step: Step) -> None:
            outcome = await self._run_task_with_critic_loop(
                mission_id=mission_id,
                mission_prompt=view.prompt,
                step=step,
                mission_dir=mission_dir,
                reflections=reflections,
                sem=sem,
            )
            task_outcomes.append(outcome)

        # Cross-mission concurrency cap: serialise the heavy claude
        # (worker + critic) phase so a burst of missions cannot overload the
        # Claude Max OAuth and crash the critics (critic_unavailable). The
        # within-mission `sem` above still bounds per-step parallelism.
        async with self._mission_sem:
            async with asyncio.TaskGroup() as tg:
                for step in plan.steps:
                    tg.create_task(_run(step), name=f"task-{step.task_id[:13]}")

        # Aggregate
        if all(o == TaskOutcome.APPROVED for o in task_outcomes):
            await self._approve_mission(mission_id, plan)
            return MissionState.APPROVED

        # Which task failed determines the failure reason.
        # Collect per-iter diff paths once so we can attach them to every
        # failure mode (not just CRITIC_UNAVAILABLE) — even a budget-cap
        # or critic-reject is more recoverable when the user can see the
        # work the worker actually produced.
        partial = self._collect_partial_artifacts(mission_id, plan)
        # CRITIC_UNAVAILABLE has priority over EXHAUSTED: when the
        # Critic spawn crashed before iter0's verdict could be rendered,
        # subsequent iterations only saw the deterministic empty-diff
        # fast-path and contributed nothing. Surfacing the real root
        # cause to the voice layer is the whole point of this branch.
        if TaskOutcome.BUDGET_EXCEEDED in task_outcomes:
            await self._fail_mission(
                mission_id, "budget_exceeded", partial_artifacts=partial
            )
        elif TaskOutcome.CRITIC_UNAVAILABLE in task_outcomes:
            await self._fail_mission(
                mission_id, "critic_unavailable", partial_artifacts=partial
            )
        elif TaskOutcome.REJECTED in task_outcomes:
            await self._fail_mission(
                mission_id, "critic_rejected", partial_artifacts=partial
            )
        elif TaskOutcome.EXHAUSTED in task_outcomes:
            await self._fail_mission(
                mission_id, "critic_loop_exhausted", partial_artifacts=partial
            )
        elif TaskOutcome.SETUP_FAILED in task_outcomes:
            # Worktree-create failure (path cap / git index lock) — surface an
            # actionable cause instead of the generic "worker aborted" (#8).
            await self._fail_mission(
                mission_id, "worktree_setup_failed", partial_artifacts=partial
            )
        else:
            await self._fail_mission(
                mission_id, "task_error", partial_artifacts=partial
            )
        return MissionState.FAILED

    # --- Per-Task-Loop -----------------------------------------------------

    async def _run_task_with_critic_loop(
        self,
        *,
        mission_id: str,
        mission_prompt: str,
        step: Step,
        mission_dir: Path,
        reflections: ReflectionMemory,
        sem: asyncio.Semaphore,
    ) -> str:
        """Runs a step through the Worker+Critic loop (max MAX_CRITIC_LOOPS)."""
        async with sem:
            try:
                # Pre-spawn budget check — do not start a worker if already over.
                self._budget.assert_under_limit(mission_id)
            except BudgetExceeded as exc:
                logger.warning("Task %s: pre-spawn budget exceeded: %s", step.task_id, exc)
                return TaskOutcome.BUDGET_EXCEEDED

            try:
                worktree = self._worktrees.create(
                    mission_slug=_short_slug(mission_prompt),
                    task_id=step.task_id,
                )
            except (subprocess.CalledProcessError, ValueError) as exc:
                logger.exception("Task %s: worktree-create failed: %s", step.task_id, exc)
                return TaskOutcome.SETUP_FAILED

            # BUG-021 fix: materialise AGENTS.md contract into the worktree so
            # the worker has Read/Write/Edit tool grants in scope. claude
            # --print reads AGENTS.md from cwd; without it the worker boots
            # in chat-only mode and hallucinates "I created the file"
            # without invoking a Write tool. Best-effort: a failure here
            # only regresses to the pre-fix behaviour and must not block
            # the mission.
            try:
                materialize_worker_contract(worktree, mission_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "materialize_worker_contract failed for %s",
                    worktree, exc_info=True,
                )

            try:
                return await self._run_iterations(
                    mission_id=mission_id,
                    mission_prompt=mission_prompt,
                    step=step,
                    mission_dir=mission_dir,
                    worktree=worktree,
                    reflections=reflections,
                )
            finally:
                # Persist worker artifacts BEFORE the worktree teardown.
                # Without this step the worktree (and everything the agent
                # wrote into it) is gone the moment the task finishes,
                # leaving the "outputs folder" the user expects in the
                # sub-agents-outputs sidebar permanently empty even on a
                # successful mission.
                try:
                    self._archive_task_artifacts(
                        worktree=worktree,
                        mission_dir=mission_dir,
                        task_id=step.task_id,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "artifact archive failed for %s", worktree, exc_info=True
                    )
                # Worktree cleanup MUST run for every return path so we don't
                # leak per-task git worktrees on disk (Phase-6 hard rule #4).
                try:
                    self._worktrees.remove(worktree, force=True)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "worktree cleanup failed for %s", worktree, exc_info=True
                    )

    async def _run_iterations(
        self,
        *,
        mission_id: str,
        mission_prompt: str,
        step: Step,
        mission_dir: Path,
        worktree: Path,
        reflections: ReflectionMemory,
    ) -> str:
        """Inner critic-loop body. Extracted so the worktree-finally in the
        caller wraps every return path."""
        session_id: str | None = None
        # Track per-iteration critic outcomes so the failure-reason mapper
        # can distinguish "Critic was broken all the way through" from
        # "Critic worked but rejected the worker every time".
        #
        # Audit-2 finding (2026-05-18): when all 3 iterations end with
        # `CriticTimeout`/`CriticSchemaInvalid` (i.e. the Critic itself
        # was unusable), the loop used to exit with EXHAUSTED and the
        # voice announcer told the user "Three attempts were not
        # enough" — which falsely implies the worker was tried and
        # failed three times. The semantically correct outcome is
        # CRITIC_UNAVAILABLE: the Critic could not be reached, the
        # worker was never judged on its merits.
        #
        # Rule: if ALL critic calls in this task raised an exception
        # AND we reach loop-exhaustion, return CRITIC_UNAVAILABLE.
        # If at least one iteration produced a valid revise verdict,
        # return EXHAUSTED (the worker was given real feedback and
        # still didn't fix things).
        critic_ok_count = 0

        for iteration in range(MAX_CRITIC_LOOPS):
            # Per-iteration: render reflections, spawn worker, capture diff+log
            prior_block = reflections.render_for_worker_prompt(n=3)
            worker_prompt = (
                prior_block + "\n\n" + step.prompt if prior_block else step.prompt
            )

            # State-Machine drive-thru:
            #   iter 0: PENDING/RUNNING -> CRITIQUING (single jump).
            #   iter 1+: CRITIQUING -> LOOPING -> RUNNING -> CRITIQUING so the
            #   transition back into CRITIQUING below is legal (CRITIQUING ->
            #   CRITIQUING would be illegal and silently swallowed).
            if iteration > 0:
                await self._safe_transition(
                    mission_id, MissionState.LOOPING, f"iter-{iteration}-revise"
                )
                await self._safe_transition(
                    mission_id, MissionState.RUNNING, f"iter-{iteration}-worker-start"
                )
                # Per-iteration budget pre-check: catch overruns recorded by
                # the bus subscription in the previous iteration before we
                # spawn another worker.
                try:
                    self._budget.assert_under_limit(mission_id)
                except BudgetExceeded as exc:
                    logger.warning(
                        "Task %s: iter-%d pre-check budget exceeded: %s",
                        step.task_id, iteration, exc,
                    )
                    return TaskOutcome.BUDGET_EXCEEDED

            await self._safe_transition(
                mission_id, MissionState.CRITIQUING, f"iter-{iteration}-start"
            )

            # Worker spawn (real or fake depending on the factory)
            worker = self._worker_factory(step)
            log_dir = mission_dir / "tasks" / step.task_id[:13] / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            try:
                # BUG-LIVE-03 (2026-05-14): never reuse OpenClaw session-id
                # across critic iterations. Live repro mission_019e2605
                # showed that OpenClaw 2026.5.7 prefers the failover chain
                # persisted in the session-state file over the explicit
                # `--model` CLI flag on resume — so iter1 silently retried
                # `openai/gpt-5.5` instead of `xai/grok-4.3` and died with
                # `chain_exhausted`. The Critic's correction context is
                # already injected into the worker prompt via
                # `prior_block` (see line 250), so a fresh session loses
                # nothing.
                spawn_result = await self._spawn_worker_collect(
                    worker=worker,
                    worker_prompt=worker_prompt,
                    worktree=worktree,
                    mission_dir=mission_dir,
                    log_dir=log_dir,
                    mission_id=mission_id,
                    step=step,
                    iteration=iteration,
                    resume_session_id=None,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Task %s iter %d: worker spawn failed", step.task_id, iteration)
                if iteration == MAX_CRITIC_LOOPS - 1:
                    return TaskOutcome.ERROR
                continue

            diff_text = self._capture_diff(worktree)
            log_text = self._read_stream_log(log_dir)
            # Out-of-worktree deliverables (live mission_019e7abd, 2026-05-30):
            # a task may legitimately target an absolute path outside the
            # worktree (e.g. the user's Desktop). `_capture_diff` is
            # worktree-scoped and returns empty for those, which the Critic's
            # GROUND-TRUTH-RULE fails deterministically. Credit external files
            # the worker actually wrote (real tool_use + verified on disk) so
            # the Critic reviews real content instead of failing 3× on a blind
            # spot. Must run before the iter-diff record, the WorkerDraftReady
            # publish, the safety scan, and the Critic call below — all consume
            # `diff_text`.
            diff_text = self._augment_diff_with_external_writes(
                diff_text, log_text, worktree
            )
            # Record this iteration's diff verbatim. Later iterations may
            # overwrite the worktree with a no-op Edit (live repro
            # mission_019e3288: iter0=1237B real diff, iter1+iter2=0B), so
            # capturing the worktree snapshot in `_archive_task_artifacts`
            # alone would lose iter0's real work. We persist every
            # iteration's bytes here and let the archive step pick the
            # largest non-empty one as the canonical `diff.patch`.
            self._task_iter_diffs.setdefault(step.task_id, []).append(
                (iteration, diff_text)
            )
            if spawn_result.session_id:
                session_id = spawn_result.session_id

            # Fail-fast on terminal worker errors (billing, auth, etc.). The
            # Critic cannot review work that never happened; iterating 3
            # times burns credits and minutes only to fail with the same
            # underlying cause hidden under "critic_loop_exhausted".
            #
            # WorkerKilled.reason is a closed literal — we map the upstream
            # text to the closest applicable label (billing/balance →
            # "budget", everything else → "user") and log the verbatim
            # message at ERROR level so it shows up in jarvis_desktop.log
            # immediately above the failure announcement.
            if spawn_result.worker_error:
                err_lower = spawn_result.worker_error.lower()
                is_timeout = "timeout" in err_lower
                kill_reason: Literal[
                    "timeout", "user", "budget", "parent_cancelled",
                    "injection_detected", "path_guard",
                ] = (
                    "budget"
                    if ("balance" in err_lower or "billing" in err_lower
                        or "credit" in err_lower)
                    else "timeout" if is_timeout
                    else "user"
                )
                logger.error(
                    "Task %s iter %d: worker terminal error (%s) — %s",
                    step.task_id, iteration, kill_reason,
                    spawn_result.worker_error,
                )
                # A worker timeout is transient (Claude Max OAuth contention:
                # claude produced zero output and was killed by the worker's
                # first-output gate). Retry on a fresh spawn instead of failing
                # the whole mission — the heavy-phase semaphore (_mission_sem,
                # default 1) serialises the retry so it no longer competes with
                # the spawn that just timed out. Budget/auth errors stay fatal.
                if is_timeout and iteration < MAX_CRITIC_LOOPS - 1:
                    logger.warning(
                        "Task %s iter %d: worker timed out with no output — "
                        "retrying on a fresh spawn",
                        step.task_id, iteration,
                    )
                    continue
                await self._publish_worker_killed(
                    mission_id=mission_id,
                    worker_id=spawn_result.worker_id,
                    reason=kill_reason,
                )
                return TaskOutcome.ERROR

            # WorkerDraftReady event — BudgetTracker.bind_to_event_bus
            # auto-records cost_usd via the bus subscription (init.py:119).
            # We DO NOT call self._budget.record() explicitly here to avoid
            # double-counting. Hard-abort on overrun is detected by the
            # pre-spawn assert_under_limit() check at the top of each
            # iteration of this loop.
            draft_env = await self._publish_worker_draft(
                mission_id=mission_id,
                worker_id=spawn_result.worker_id,
                diff=diff_text,
                cost_usd=spawn_result.cost_usd,
                tokens_used=spawn_result.tokens_used,
                session_id=session_id or "",
            )
            # Detect budget overrun caused by this iteration's draft so we
            # can publish WorkerKilled and abort fast (instead of waiting
            # for the next pre-spawn check).
            try:
                self._budget.assert_under_limit(mission_id)
            except BudgetExceeded:
                logger.warning(
                    "Task %s: budget exceeded after iter %d", step.task_id, iteration
                )
                await self._publish_worker_killed(
                    mission_id=mission_id,
                    worker_id=spawn_result.worker_id,
                    reason="budget",
                )
                return TaskOutcome.BUDGET_EXCEEDED

            # Phase-5 Safety: PostToolUse-Scanner gegen Injection + Path-Guard.
            if self._safety_enabled:
                safety_kill_reason = await self._safety_scan(
                    mission_id=mission_id,
                    worker_id=spawn_result.worker_id,
                    diff_text=diff_text,
                    log_text=log_text,
                )
                if safety_kill_reason is not None:
                    return TaskOutcome.ERROR

            # Critic-Call
            env = self._env_builder(mission_dir)
            try:
                verdict = await self._runner.run(
                    mission_prompt=mission_prompt,
                    worker_diff=diff_text,
                    worker_log=log_text,
                    prior_reflections=prior_block,
                    iteration=iteration,
                    worktree=worktree,
                    env=env,
                    security_tag=_detect_security_tag(step.prompt),
                )
            except CriticTimeout as exc:
                # A critic timeout is TRANSIENT (the critic also shells out to
                # `claude` over the same Claude Max OAuth; under concurrent load
                # it throttles). Unlike a malformed verdict, retrying CAN help —
                # so we re-run the critic on a fresh iteration instead of failing
                # the mission. This is the dominant cause of the 22
                # `critic_unavailable` failures, all clustered on high-load days.
                logger.warning(
                    "Task %s iter %d: critic timed out (transient, likely OAuth "
                    "contention): %s", step.task_id, iteration, exc,
                )
                if iteration == MAX_CRITIC_LOOPS - 1:
                    # No iterations left to retry the critic.
                    return (
                        TaskOutcome.CRITIC_UNAVAILABLE
                        if critic_ok_count == 0
                        else TaskOutcome.EXHAUSTED
                    )
                # The iter diff is already persisted in self._task_iter_diffs, so
                # re-spawning the worker loses no work; tell it its output is
                # already applied so it only addresses gaps (idempotency guard).
                reflections.append(
                    iteration,
                    "Critic timed out (infrastructure contention, not a defect in "
                    "your work). Your previous output is preserved in the worktree "
                    "— do NOT redo work that is already applied; only address any "
                    "remaining gaps.",
                    [],
                )
                continue
            except (CriticSchemaInvalid, CriticVerdictInconsistent) as exc:
                logger.warning("Task %s iter %d: critic failed: %s", step.task_id, iteration, exc)
                # Live forensic 2026-05-16 (mission_019e3288): a Critic
                # subprocess crash on iter0 (EPERM symlink + Unknown
                # agent id) currently degrades to `continue` and the
                # worker's real iter0 work is silently overwritten by
                # iter1+iter2 no-ops. When the worker DID produce real
                # output (non-empty diff after stripping managed persona
                # files), don't iterate — surface `critic_unavailable`
                # immediately so the user knows their work survived in
                # the worktree and the failure was infrastructure, not
                # the worker. Conservative scope: only short-circuit on
                # iter0; later iterations may carry partial corrections
                # that the next loop should still try to refine. A malformed
                # verdict (schema/consistency) will NOT improve on retry, so —
                # unlike CriticTimeout above — we keep the fast short-circuit.
                if iteration == 0 and not _real_diff_is_empty(diff_text):
                    logger.error(
                        "Task %s: critic crashed on iter0 with non-empty diff "
                        "(%d bytes) — short-circuiting to critic_unavailable "
                        "to preserve the worker's real work",
                        step.task_id, len(diff_text),
                    )
                    return TaskOutcome.CRITIC_UNAVAILABLE
                if iteration == MAX_CRITIC_LOOPS - 1:
                    # Audit-2 (2026-05-18): differentiate between
                    # "Critic was broken throughout" (CRITIC_UNAVAILABLE)
                    # and "Critic gave real feedback but the worker
                    # never reached approve" (EXHAUSTED). If no
                    # iteration ever produced a valid Critic verdict,
                    # the loop terminated because the Critic itself
                    # was unusable — the worker was never actually
                    # judged on its merits.
                    if critic_ok_count == 0:
                        logger.error(
                            "Task %s: all %d critic iterations failed with "
                            "exceptions — surfacing critic_unavailable",
                            step.task_id, MAX_CRITIC_LOOPS,
                        )
                        return TaskOutcome.CRITIC_UNAVAILABLE
                    return TaskOutcome.EXHAUSTED
                # Behandle wie revise — neue Iteration
                reflections.append(iteration, f"Critic exception: {type(exc).__name__}", [])
                continue

            # Got a parsed verdict — count it as a successful Critic
            # round-trip regardless of approve/revise/reject. Used by
            # the loop-exhaustion branch above to distinguish
            # `critic_unavailable` from `critic_loop_exhausted`.
            critic_ok_count += 1

            # Publish verdict event
            await self._publish_critic_verdict(
                mission_id=mission_id,
                worker_id=spawn_result.worker_id,
                verdict=verdict,
                iteration=iteration,
            )

            # Verdict evaluation
            if is_approval_valid(verdict):
                # Read-only / informational task (empty diff + real tool
                # evidence + a substantive answer): capture the worker's answer
                # so the mission speaks it back instead of "Mission
                # abgeschlossen." Code tasks (non-empty diff) yield None here
                # and keep the generic summary.
                answer = readonly_answer(diff_text, log_text)
                if answer:
                    self._task_answers.setdefault(mission_id, []).append(answer)
                return TaskOutcome.APPROVED

            if verdict.verdict == "reject":
                return TaskOutcome.REJECTED

            # revise — persist reflection + next iteration
            reflections.append(
                iteration,
                verdict.summary or "no summary",
                [i.evidence_ref for i in verdict.issues],
            )

            # Publish WorkerCorrectionRequired
            await self._publish_correction(
                mission_id=mission_id,
                worker_id=spawn_result.worker_id,
                instruction=verdict.correction_instruction or "address critic feedback",
                iteration=iteration,
                next_model=("opus" if iteration + 1 >= 2 else "sonnet"),
            )

        # MAX_CRITIC_LOOPS exhausted without approval
        return TaskOutcome.EXHAUSTED

    # --- Worker-Spawn-Adapter ---------------------------------------------

    class _SpawnResult:
        """Internal — aggregate of worker stream events."""

        def __init__(
            self,
            *,
            worker_id: str,
            cost_usd: float,
            tokens_used: int,
            session_id: str | None,
            worker_error: str | None = None,
        ) -> None:
            self.worker_id = worker_id
            self.cost_usd = cost_usd
            self.tokens_used = tokens_used
            self.session_id = session_id
            # Non-None when the worker subprocess returned a terminal
            # `result` event with is_error=True. Carries the upstream
            # error message verbatim (e.g. "Credit balance is too low"
            # from a 400 billing_error, "Not logged in" when the CLI has
            # no credentials). Used by the calling loop to fail-fast
            # instead of grinding through MAX_CRITIC_LOOPS retries.
            self.worker_error = worker_error

    async def _spawn_worker_collect(
        self,
        *,
        worker: WorkerProtocol,
        worker_prompt: str,
        worktree: Path,
        mission_dir: Path,
        log_dir: Path,
        mission_id: str,
        step: Step,
        iteration: int,
        resume_session_id: str | None,
    ) -> "Kontrollierer._SpawnResult":
        """Spawns the worker, drains the stream, and aggregates cost/tokens/session.

        ``mission_dir`` is the mission root (parent of the per-task ``log_dir``).
        ``env_builder(mission_dir)`` derives ``CODEX_HOME`` from it; passing the
        deep ``log_dir`` would place ``.codex`` inside a logs subfolder which is
        wrong (FIX-4: env_builder mission_dir consistency).
        """
        worker_id = f"{mission_id[:13]}::{step.task_id[:13]}::iter{iteration}"
        job = self._job_factory()
        cost = 0.0
        tokens = 0
        session_id: str | None = resume_session_id
        spawned_emitted = False
        worker_error: str | None = None

        async with job:
            kwargs: dict[str, Any] = {
                "model": step.model,
                "allowed_tools": step.allowed_tools,
            }
            if resume_session_id:
                kwargs["resume_session_id"] = resume_session_id

            async for ev in worker.spawn(
                worker_prompt,
                worktree=worktree,
                env=self._env_builder(mission_dir),
                job=job,
                worker_id=worker_id,
                log_dir=log_dir,
                **kwargs,
            ):
                # Publish WorkerSpawned on the first event
                if not spawned_emitted:
                    pid = getattr(worker, "last_pid", 0) or 0
                    sid = getattr(ev, "session_id", None) or session_id
                    await self._publish_worker_spawned(
                        mission_id=mission_id,
                        worker_id=worker_id,
                        pid=int(pid) if pid else 0,
                        cli=worker.cli,
                        model=step.model,
                        worktree=str(worktree),
                        session_id=sid,
                        step=step,
                    )
                    spawned_emitted = True

                # Capture session ID from the init event
                ev_session_id = getattr(ev, "session_id", None)
                if ev_session_id and not session_id:
                    session_id = ev_session_id

                # Cost+Tokens aus result-Event aggregieren.
                ev_cost = getattr(ev, "cost_usd", None)
                if ev_cost is not None:
                    cost = float(ev_cost)
                # Claude stream-json result events carry num_turns (not the
                # older total_tokens used by some Codex flows). Read whichever
                # is present so the field doesn't silently stay at 0 and
                # mislead the budget tracker / Sub-Agents UI.
                ev_tokens = (
                    getattr(ev, "tokens_used", None)
                    or getattr(ev, "total_tokens", None)
                    or getattr(ev, "num_turns", None)
                )
                if ev_tokens is not None:
                    tokens = int(ev_tokens)
                # Fail-fast signal: claude emits a terminal result with
                # is_error=True for billing errors ("Credit balance is too
                # low"), authentication failures ("Not logged in"), and
                # error_max_turns. Without this hook the loop above keeps
                # retrying for MAX_CRITIC_LOOPS (3) iterations, each one
                # roundtripping the Critic and burning credits + minutes,
                # before failing with the misleading reason
                # "critic_loop_exhausted". Capture the real cause once so
                # the caller can short-circuit.
                if getattr(ev, "is_error", False):
                    upstream = (
                        getattr(ev, "result", None)
                        or getattr(ev, "subtype", None)
                        or "worker reported is_error=True"
                    )
                    worker_error = str(upstream)[:300]

        return Kontrollierer._SpawnResult(
            worker_id=worker_id,
            cost_usd=cost,
            tokens_used=tokens,
            session_id=session_id,
            worker_error=worker_error,
        )

    # --- Phase-5 Safety -----------------------------------------------------

    async def _safety_scan(
        self,
        *,
        mission_id: str,
        worker_id: str,
        diff_text: str,
        log_text: str,
    ) -> str | None:
        """Scant Worker-Output gegen Injection + Path-Guard.

        Returns:
            None wenn alles clean. Sonst: kill-reason (z.B. "injection_detected"
            oder "path_guard:.env"). In dem Fall publiziert die Methode auch
            ein WorkerKilled-Event.
        """
        # 1) Injection-Scanner — high/critical blocks, med/low logged
        detections = injection_scan(diff_text, where="diff")
        detections += injection_scan(log_text, where="log")
        if has_high_severity(detections):
            top = next(
                (d for d in detections if d.severity in ("critical", "high")),
                detections[0],
            )
            logger.warning(
                "Mission %s: injection detected in %s — pattern=%s severity=%s",
                mission_id, top.where, top.pattern_id, top.severity,
            )
            await self._publish_worker_killed(
                mission_id=mission_id,
                worker_id=worker_id,
                reason="injection_detected",
            )
            return "injection_detected"

        # 2) Path-Guard auf diff-File-Pfade
        blocked_paths = filter_diff_paths(
            diff_text, extra_globs=self._extra_blocked_globs
        )
        if blocked_paths:
            reason = f"path_guard:{blocked_paths[0]}"
            logger.warning(
                "Mission %s: path-guard blocked — paths=%s", mission_id, blocked_paths
            )
            await self._publish_worker_killed(
                mission_id=mission_id, worker_id=worker_id, reason=reason,
            )
            return reason

        return None

    async def _publish_worker_killed(
        self,
        *,
        mission_id: str,
        worker_id: str,
        reason: str,
    ) -> None:
        """Emittiert WorkerKilled-Event auf Bus + Store."""
        # Reason wird auf das Literal-Set in events.WorkerKilled gemappt.
        # path_guard:* wird auf "path_guard" reduziert (Voice-Listener-Routing
        # unterscheidet path_guard vs injection_detected fuer praezisere TTS).
        if reason.startswith("path_guard"):
            mapped: str = "path_guard"
        elif reason in ("budget", "timeout", "user", "parent_cancelled", "injection_detected"):
            mapped = reason
        else:
            mapped = "injection_detected"
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerKilled(worker_id=worker_id, reason=mapped),  # type: ignore[arg-type]
        )
        await self._manager.store.append_and_publish(env)

    # --- Helpers ----------------------------------------------------------

    def _capture_diff(self, worktree: Path) -> str:
        """Returns the worker's changes as a unified diff.

        Plain `git diff HEAD` only shows MODIFIED tracked files — it misses
        the worker's freshly created files (e.g. a new hello.py), which is
        exactly the most common task shape. We stage everything with
        `git add -A .` (real, full-content blobs) so new files appear as
        empty→content adds in `git diff HEAD`. The staging is confined to
        the throwaway per-task worktree (no commit, reset at cleanup).

        BUG-DIFF-EMPTY (2026-05-24): the previous `git add -N .`
        (intent-to-add) was a soft marker that registered new files with an
        EMPTY index blob AND removed them from `git ls-files --others`.
        On Windows git, `git diff HEAD` did not render the intent-to-add
        content, so a freshly-written file fell through BOTH detection
        paths → empty diff → the Critic deterministically returned
        "revise" → critic_loop_exhausted even though the worker had
        written the file correctly (live repro mission_019e5a0f, 3 empty
        iterations despite a real Write tool-call). Switching to `add -A`
        stages full blobs so `git diff HEAD` shows new files reliably.

        As a belt-and-braces guard we still enumerate `git ls-files
        --others --exclude-standard` and append any paths missed by the
        diff as comment lines (`# untracked-not-in-diff: …`). The Critic
        prompt reads the diff as text, so comment-prefixed lines are
        informational and don't require a parser change.

        All git calls are best-effort with a 10s cap; failure returns ""
        and logs at WARNING so the upstream Critic still gets a
        (truthful) empty diff rather than a stale one.
        """
        try:
            subprocess.run(  # noqa: S603
                ["git", "add", "-A", "."],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            r_diff = subprocess.run(  # noqa: S603
                # `git add -A` stages full-content blobs, then `git diff
                # --cached HEAD` (index vs HEAD) renders newly-created files
                # reliably — including on Windows git, where plain `git diff
                # HEAD` (working-tree vs HEAD) intermittently returned EMPTY
                # for freshly-staged new files (live repro 2026-05-24). Verified
                # green: mission_019e5a52 captured a 2728-byte diff for a
                # worker-created file via this exact sequence.
                # `-c core.quotepath=false` keeps non-ASCII paths raw UTF-8
                # instead of octal-escaping them (HIGH finding 2026-05-27).
                ["git", "-c", "core.quotepath=false", "diff", "--cached", "HEAD"],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            r_unt = subprocess.run(  # noqa: S603
                ["git", "-c", "core.quotepath=false",
                 "ls-files", "--others", "--exclude-standard"],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            diff = r_diff.stdout or ""
            untracked_lines = [
                ln.strip()
                for ln in (r_unt.stdout or "").splitlines()
                if ln.strip()
            ]
            # Only flag untracked entries that don't already appear in the
            # diff so we don't double-report a file `add -N` already
            # surfaced as an empty→content add.
            missed = [
                p for p in untracked_lines
                if f"b/{p}" not in diff and f"a/{p}" not in diff
            ]
            if missed:
                trailer = "\n# untracked-not-in-diff:\n" + "\n".join(
                    f"# - {p}" for p in missed
                )
                diff = (diff + trailer) if diff else trailer.lstrip("\n")
            # Strip the materialized worker-contract files (AGENTS.md etc.):
            # `git add -A` stages them despite .git/info/exclude, and they must
            # not reach the Critic (BUG-LIVE-05 false-APPROVE vector).
            return _strip_managed_persona_hunks(diff)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("git diff failed in %s: %s", worktree, exc)
            return ""

    def _augment_diff_with_external_writes(
        self, diff_text: str, stream_text: str, worktree: Path
    ) -> str:
        """Append on-disk-verified external deliverables to the captured diff.

        ``_capture_diff`` only sees the worktree. A task that legitimately
        targets an absolute path OUTSIDE the worktree (e.g. the user's
        ``Desktop\\M\\``) therefore yields an empty diff, which the Critic's
        GROUND-TRUTH-RULE fails deterministically — even when the file was
        created correctly (live mission_019e7abd, 2026-05-30: 3 empty
        iterations → ``critic_loop_exhausted`` while the 282-byte file sat on
        the Desktop). This helper restores ground truth: for every path the
        worker wrote with a real, non-errored ``Write``/``Edit`` tool_use
        (parsed from the stream) that (a) is absolute, (b) lies outside the
        worktree, and (c) actually exists on disk, it appends a
        ``diff --external-target`` block carrying the file's real on-disk bytes.

        Anti-hallucination is preserved: a write with no tool call, or with no
        file on disk, is never credited, so a hallucinated "I created the file"
        still falls through to the empty-diff veto.

        Best-effort: any per-file error is logged and skipped; never raises.
        """
        targets = extract_write_targets(stream_text)
        if not targets:
            return diff_text
        try:
            wt_resolved = worktree.resolve()
        except OSError:
            # `absolute()` anchors the drive/root without a filesystem stat, so
            # the containment check still works if `resolve()` (which stats and
            # follows symlinks) fails on an exotic / offline path.
            wt_resolved = worktree.absolute()

        blocks: list[str] = []
        seen: set[str] = set()
        for raw in targets:
            try:
                p = Path(raw)
            except (ValueError, OSError):
                continue
            if not p.is_absolute():
                # Relative paths resolve into the worktree → git diff covers them.
                continue
            try:
                pr = p.resolve()
            except OSError:
                pr = p
            if _path_is_within(pr, wt_resolved):
                continue  # in-worktree write — already in the captured diff
            key = str(pr)
            if key in seen:
                continue
            if not pr.is_file():
                continue  # hallucinated success / file vanished → do not credit
            try:
                raw_bytes = pr.read_bytes()
            except OSError as exc:
                logger.warning(
                    "external-write verify: read %s failed: %s", pr, exc
                )
                continue
            seen.add(key)
            blocks.append(_format_external_write_block(pr, raw_bytes))
            logger.info(
                "external-write verify: credited out-of-worktree deliverable "
                "%s (%d bytes)", pr, len(raw_bytes),
            )

        if not blocks:
            return diff_text
        trailer = "\n".join(blocks)
        if diff_text and diff_text.strip():
            return diff_text.rstrip("\n") + "\n" + trailer
        return trailer

    def _archive_task_artifacts(
        self,
        *,
        worktree: Path,
        mission_dir: Path,
        task_id: str,
    ) -> Path | None:
        """Persist the worker's outputs out of the worktree so they survive
        the per-task cleanup.

        Writes (under ``<mission_dir>/tasks/<task_id[:13]>/artifacts/``):
        - ``diff.iter<N>.patch`` — one file per critic-loop iteration that
          actually ran, capturing the worktree state right after that
          iteration's worker finished. Preserves real work from earlier
          iterations even when later iterations land a no-op edit that
          reverts the diff back to empty (live repro mission_019e3288,
          2026-05-16: iter0=1237B, iter1+iter2=0B because the Critic
          crashed on iter0's review and Sonnet then re-applied an
          already-applied Edit).
        - ``diff.patch`` — copy of the *largest non-empty* ``diff.iter<N>``,
          for backward compatibility with consumers that resolve by name.
          Falls back to a fresh `git diff HEAD` of the worktree when no
          per-iter captures exist (rare: only when this helper is called
          out-of-band from a test fixture).
        - ``files/<rel>`` — verbatim copies of any *new* untracked files.
          The diff records only their paths, not their content, so a
          "create hello.txt"-style task would leave nothing recoverable
          here without an explicit copy step.

        All git operations are best-effort with a 10s cap. Returns the
        artifacts directory on success, ``None`` on irrecoverable failure
        (the upstream finally still runs the worktree cleanup either way).
        """
        try:
            artifacts = mission_dir / "tasks" / task_id[:13] / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)

            # Drain the per-iteration captures collected during the
            # critic loop. The dict-pop guarantees a single mission can't
            # accidentally double-archive the same task and that the
            # per-mission map doesn't grow unbounded across the
            # orchestrator's lifetime. `getattr` keeps test fixtures
            # happy that instantiate via `object.__new__` and bypass
            # the constructor — the helper still produces a valid
            # archive in that case, just without per-iter snapshots.
            iter_map = getattr(self, "_task_iter_diffs", None)
            if iter_map is None:
                per_iter: list[tuple[int, str]] = []
            else:
                per_iter = iter_map.pop(task_id, [])
            for it_idx, diff_text in per_iter:
                (artifacts / f"diff.iter{it_idx}.patch").write_text(
                    diff_text or "", encoding="utf-8"
                )
            # Choose the largest non-empty captured diff as the canonical
            # `diff.patch`. Ties broken by *earliest* iteration: iter0
            # typically reflects the worker's first honest attempt before
            # the loop machinery overwrites it.
            best_diff: str | None = None
            best_iter: int | None = None
            for it_idx, diff_text in per_iter:
                if not diff_text or not diff_text.strip():
                    continue
                if best_diff is None or len(diff_text) > len(best_diff):
                    best_diff = diff_text
                    best_iter = it_idx
                elif (
                    len(diff_text) == len(best_diff)
                    and best_iter is not None
                    and it_idx < best_iter
                ):
                    best_diff = diff_text
                    best_iter = it_idx

            # Order matters: enumerate untracked files BEFORE `git add -N`,
            # because `add -N` enters them into the index and `ls-files
            # --others` then stops listing them. We still need the list
            # later to copy file contents verbatim — diff format only
            # records paths for new files, not their bytes.
            #
            # TWO enumerations, unioned (2026-05-27 hardening audit):
            #   1. `--others --exclude-standard` → untracked, NOT ignored.
            #   2. `--others --ignored --exclude-standard` → untracked AND
            #      matched by a .gitignore pattern. A worker deliverable named
            #      with an ignored pattern (e.g. `output.log` under the repo's
            #      `/*.log`, or anything under `dist/`) is invisible to (1) and
            #      to the staged diff, so without (2) it is silently lost when
            #      the worktree is removed (MEDIUM finding
            #      `archive-untracked-copy-relies-on-git-enumeration`).
            # `-c core.quotepath=false` keeps non-ASCII names raw UTF-8.
            r_unt = subprocess.run(  # noqa: S603
                ["git", "-c", "core.quotepath=false",
                 "ls-files", "--others", "--exclude-standard"],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            r_unt_ign = subprocess.run(  # noqa: S603
                ["git", "-c", "core.quotepath=false",
                 "ls-files", "--others", "--ignored", "--exclude-standard"],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            untracked: list[str] = []
            for _src in (r_unt.stdout or "", r_unt_ign.stdout or ""):
                for ln in _src.splitlines():
                    rel = ln.strip()
                    if rel and rel not in untracked:
                        untracked.append(rel)

            # `git add -A .` stages full-content blobs so new files show
            # up in `git diff HEAD` with their bytes (2026-05-24: was
            # `add -N`, whose empty intent-to-add blob made new files
            # invisible to `git diff HEAD` on Windows git). `ls-files
            # --others` above already captured the untracked names for the
            # verbatim copies below, so the copy path is unaffected.
            subprocess.run(  # noqa: S603
                ["git", "add", "-A", "."],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            r_diff = subprocess.run(  # noqa: S603
                # `--cached HEAD` (index vs HEAD) after `git add -A` shows
                # newly-created files reliably; plain `git diff HEAD`
                # (working-tree vs HEAD) returned EMPTY for freshly-staged
                # new files on Windows git, even though the blob was real
                # (live proof mission_019e5a16: `git diff --cached` showed
                # "+opus direct works" while `git diff HEAD` was empty).
                # `-c core.quotepath=false` keeps non-ASCII paths raw UTF-8
                # instead of octal-escaping them (HIGH finding 2026-05-27).
                ["git", "-c", "core.quotepath=false", "diff", "--cached", "HEAD"],
                cwd=str(worktree),
                check=False,
                capture_output=True,
                text=True,
                # Force UTF-8: Windows subprocess defaults to cp1252, which
                # mojibakes git's UTF-8 path/content output and breaks
                # non-ASCII deliverable round-trip (HIGH finding 2026-05-27).
                encoding="utf-8",
                errors="replace",
                timeout=10.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            final_diff = (
                best_diff
                if best_diff is not None
                else (r_diff.stdout or "")
            )
            # best_diff is already stripped (it comes from _capture_diff); the
            # r_diff fallback is not — strip managed contract files either way
            # so the archived diff.patch never shows AGENTS.md etc.
            final_diff = _strip_managed_persona_hunks(final_diff)
            (artifacts / "diff.patch").write_text(
                final_diff, encoding="utf-8"
            )
            # Recover new-file paths the ``git ls-files --others`` call
            # missed because earlier ``_capture_diff`` invocations had
            # already run ``git add -A`` (live 2026-05-27 regression
            # mission_019e6858-ab9a: SUCCESS but artifacts/files/ empty).
            for _np in _extract_new_file_paths_from_diff(final_diff):
                if _np not in untracked:
                    untracked.append(_np)
            # Drop managed worker-contract files (AGENTS.md etc.) and
            # build/state/junk dirs — the `--ignored` union widens what we
            # enumerate, so this filter keeps artifacts/files/ to genuine
            # deliverables (no Outputs-UI garbage, the Wave-3 invariant).
            untracked = [rel for rel in untracked if _is_deliverable_path(rel)]
            if untracked:
                files_root = artifacts / "files"
                for rel in untracked:
                    src = worktree / rel
                    if not src.is_file():
                        # Skip directories, broken symlinks etc. — only
                        # regular files round-trip cleanly via copy2.
                        continue
                    dst = files_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src, dst)
                    except OSError as exc:
                        logger.warning(
                            "artifact copy failed for %s: %s", src, exc
                        )
            return artifacts
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning(
                "artifact archive failed in %s: %s", worktree, exc
            )
            return None

    def _read_stream_log(self, log_dir: Path) -> str:
        """Liest stream.jsonl als Text fuer den Critic-Log-Summarizer."""
        stream_path = log_dir / "stream.jsonl"
        if not stream_path.exists():
            return ""
        try:
            return stream_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Read stream.jsonl failed: %s", exc)
            return ""

    async def _safe_transition(
        self, mission_id: str, to_state: MissionState, reason: str
    ) -> None:
        """State-Machine-Transition mit Lock + Idempotenz-Tolerance."""
        lock = self._state_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            try:
                await self._manager.transition_state(
                    mission_id, to_state, reason=reason, source_actor="kontrollierer"
                )
            except IllegalStateTransition:
                # bereits im Zielzustand oder weiter — kein Crash
                logger.debug(
                    "Mission %s: skip transition -> %s (already past)",
                    mission_id,
                    to_state.value,
                )

    async def _approve_mission(self, mission_id: str, plan: MissionPlan) -> None:
        await self._safe_transition(mission_id, MissionState.APPROVED, "all_tasks_approved")
        # Point `result_uri` at the real mission directory so the Outputs
        # view and any voice-readback consumer can resolve it to actual
        # files (diff.patch + untracked file copies persisted by
        # `_archive_task_artifacts`). Falls back to the virtual
        # `mission://<id>` form if the dir is missing — defensive: under
        # normal flow it always exists since `run_mission` mkdir's it.
        mission_dir = self._isolation_root / f"mission_{mission_id[:13]}"
        if mission_dir.exists():
            result_uri = mission_dir.resolve().as_uri()
        else:
            result_uri = f"mission://{mission_id}"
        # For read-only/informational missions the worker's actual answer is
        # the payload the user asked for — speak it back instead of the generic
        # completion phrase. For CODE tasks (those that produced files) the
        # answer_summary is empty, so we fall through to a deliverable-aware
        # summary that NAMES the archived file(s) — without this the user
        # hears the canned "Mission abgeschlossen" for a working code task
        # and assumes nothing was produced (live regression 2026-05-26: two
        # real HTML deliverables existed on disk that day and the user heard
        # about neither). Only when even that yields nothing (Edit-only on
        # tracked files, no archived basenames) do we fall back to the
        # generic phrase.
        answers = self._task_answers.pop(mission_id, [])
        answer_summary = summarize_answers(answers)
        # Mirror the archived deliverables into a user-visible folder
        # (~/Downloads/Jarvis-Outputs on Windows) so a non-coder can actually
        # find the file the worker created — instead of it living six levels
        # deep under sub-agents-outputs/. Best-effort: a delivery failure must
        # never flip an APPROVED mission to FAILED, so it is wrapped and the
        # summary falls back to the in-archive basename naming.
        delivered: list[Path] = []
        try:
            delivered = deliver_to_user_folder(
                mission_dir, mission_short_id=mission_id[:13]
            )
        except Exception:  # noqa: BLE001 — delivery is never mission-fatal
            logger.warning(
                "deliver_to_user_folder failed for %s", mission_id, exc_info=True
            )
        delivered_summary = build_delivered_summary(delivered)
        # Prefer, in order: a read-only task's spoken answer, the delivered-file
        # summary (names file + folder), the in-archive basename summary, then
        # the generic phrase.
        deliverable_summary = build_deliverable_summary(mission_dir)
        summary_de = (
            answer_summary
            or delivered_summary
            or deliverable_summary
            or "Mission abgeschlossen."
        )
        summary_en = answer_summary or deliverable_summary or "Mission completed."
        env = EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri=result_uri,
                tokens_used=0,
                cost_usd=self._budget.mission_cost(mission_id),
                wall_ms=0,
                summary_de=summary_de,
                summary_en=summary_en,
            ),
        )
        await self._manager.store.append_and_publish(env)

    async def _fail_mission(
        self,
        mission_id: str,
        reason: str,
        *,
        partial_artifacts: list[str] | None = None,
    ) -> None:
        self._task_answers.pop(mission_id, None)  # hygiene: drop captured answers
        # Nur transitionieren wenn noch nicht terminal
        view = await self._manager.mission(mission_id)
        if view is None or view.state in (
            MissionState.APPROVED,
            MissionState.FAILED,
            MissionState.CANCELLED,
            MissionState.TIMED_OUT,
        ):
            return
        await self._safe_transition(mission_id, MissionState.FAILED, reason)
        env = EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason=reason,
                last_state=view.state.value,
                partial_artifacts=partial_artifacts or [],
            ),
        )
        await self._manager.store.append_and_publish(env)

    def _collect_partial_artifacts(
        self, mission_id: str, plan: MissionPlan | None
    ) -> list[str]:
        """Enumerate per-iter diff files we kept for this mission.

        Reads from ``<mission_dir>/tasks/<task_id[:13]>/artifacts/`` which
        ``_archive_task_artifacts`` populates as the per-task `finally`
        runs (so by the time `_fail_mission` calls this, every captured
        iteration is already on disk). Returns absolute paths as strings
        so the failure event carries actionable references the user (or
        a replay tool) can `git apply` against the original worktree.

        Returns an empty list when the mission directory isn't laid out
        yet (decompose-failed path) or no per-iter captures exist.
        """
        mission_dir = self._isolation_root / f"mission_{mission_id[:13]}"
        if not mission_dir.exists():
            return []

        out: list[str] = []
        task_ids: list[str] = []
        if plan is not None:
            task_ids = [s.task_id for s in plan.steps]
        else:
            # Defensive: best-effort enumerate from the on-disk layout.
            tasks_root = mission_dir / "tasks"
            if tasks_root.is_dir():
                task_ids = [p.name for p in tasks_root.iterdir() if p.is_dir()]

        for task_id in task_ids:
            artifacts = mission_dir / "tasks" / task_id[:13] / "artifacts"
            if not artifacts.is_dir():
                continue
            for patch in sorted(artifacts.glob("diff.iter*.patch")):
                try:
                    if patch.stat().st_size > 0:
                        out.append(str(patch.resolve()))
                except OSError:
                    continue
            # Fall through and also surface the canonical `diff.patch`
            # when no per-iter files made it (e.g. the helper hit an
            # OSError before populating them).
            canonical = artifacts / "diff.patch"
            try:
                if (
                    not any(p.startswith(str(artifacts)) for p in out)
                    and canonical.is_file()
                    and canonical.stat().st_size > 0
                ):
                    out.append(str(canonical.resolve()))
            except OSError:
                continue

        return out

    async def _publish_plan_ready(self, mission_id: str, plan: MissionPlan) -> None:
        env = EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionPlanReady(
                plan=[s.model_dump() for s in plan.steps],
                n_workers=plan.n_workers,
                expected_output=plan.expected_output,
            ),
        )
        await self._manager.store.append_and_publish(env)

    async def _publish_worker_spawned(
        self,
        *,
        mission_id: str,
        worker_id: str,
        pid: int,
        cli: str,
        model: str,
        worktree: str,
        session_id: str | None,
        step: Step,
    ) -> None:
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerSpawned(
                worker_id=worker_id,
                step=step.model_dump(),
                pid=pid,
                cli=cli,  # type: ignore[arg-type]
                model=model,
                worktree=worktree,
                session_id=session_id,
            ),
        )
        await self._manager.store.append_and_publish(env)

    async def _publish_worker_draft(
        self,
        *,
        mission_id: str,
        worker_id: str,
        diff: str,
        cost_usd: float,
        tokens_used: int,
        session_id: str,
    ) -> EventEnvelope:
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="worker",
            ts_ms=now_ms(),
            payload=WorkerDraftReady(
                worker_id=worker_id,
                artifact_uri=f"diff://{worker_id}",
                diff=diff[:8000],  # Cap zum Schutz vor Riesen-Diffs im Event-Store
                tokens_used=tokens_used,
                cost_usd=cost_usd,
                session_id=session_id,
            ),
        )
        await self._manager.store.append_and_publish(env)
        return env

    async def _publish_critic_verdict(
        self,
        *,
        mission_id: str,
        worker_id: str,
        verdict: CriticVerdict,
        iteration: int,
    ) -> None:
        axes_dict: dict[str, dict[str, Any]] = {
            ax_name: ax.model_dump() for ax_name, ax in verdict.axes.items()
        }
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="critic",
            ts_ms=now_ms(),
            payload=CriticVerdictReady(
                worker_id=worker_id,
                verdict=verdict.verdict,  # type: ignore[arg-type]
                summary=verdict.summary,
                confidence=verdict.confidence,
                axes=axes_dict,
                iteration=iteration,
            ),
        )
        await self._manager.store.append_and_publish(env)

    async def _publish_correction(
        self,
        *,
        mission_id: str,
        worker_id: str,
        instruction: str,
        iteration: int,
        next_model: str,
    ) -> None:
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="critic",
            ts_ms=now_ms(),
            payload=WorkerCorrectionRequired(
                worker_id=worker_id,
                correction_instruction=instruction,
                iteration=iteration,
                next_model=next_model,
            ),
        )
        await self._manager.store.append_and_publish(env)


# --- Module-level helpers ---


def _short_slug(text: str, *, max_len: int = 30) -> str:
    """Kurzer kebab-slug fuer Mission-Naming."""
    import re

    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (out[:max_len] or "mission").rstrip("-")


_SECURITY_KEYWORDS = (
    "auth", "oauth", "password", "secret", "token",
    "crypto", "encrypt", "decrypt", "hash", "salt",
    "database", "sql ", "injection", "xss", "csrf",
    "permission", "privilege", "sudo", "admin",
)


def _detect_security_tag(prompt: str) -> bool:
    """True wenn der Step-Prompt sicherheitsrelevante Keywords enthaelt.

    Triggert Critic-Tier-Eskalation Sonnet -> Opus auch in iter 0.
    """
    lower = prompt.lower()
    return any(kw in lower for kw in _SECURITY_KEYWORDS)


__all__ = [
    "MAX_WORKERS_PER_MISSION",
    "EnvBuilderFn",
    "JobFactoryFn",
    "Kontrollierer",
    "TaskOutcome",
    "WorkerFactoryFn",
]
