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
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Final, Literal

from ...core.process_utils import NO_WINDOW_CREATIONFLAGS
from ..budget import BudgetExceeded, BudgetTracker
from ..critic.escalation import FRONTIER_MODEL
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
    MissionCancelled,
    MissionFailed,
    MissionPlanReady,
    WorkerCorrectionRequired,
    WorkerDraftReady,
    WorkerKilled,
    WorkerProgress,
    WorkerSpawned,
    now_ms,
)
from ..isolation.worktree import (
    SourceCheckoutUnavailableError,
    WorktreeManager,
    read_worktree_base_sha,
)
from ..manager import MissionManager
from ..safety import (
    extract_worker_authored_text,
    filter_diff_paths,
    has_high_severity,
)
from ..safety import (
    scan as injection_scan,
)
from ..state_machine import IllegalStateTransition, MissionState
from ..stream_evidence import (
    extract_verified_commands,
    extract_verified_desktop_actions,
    extract_write_targets,
    readonly_answer,
    summarize_answers,
)
from ..worker_runtime.workspace import materialize_worker_contract
from ..workers.base import WorkerProtocol
from .decomposer import MissionDecomposer, MissionPlan, Step
from .deliverable import (
    build_deliverable_summary,
    build_delivered_summary,
    deliver_to_user_folder,
    materialize_answer_document,
)
from .deliverable_paths import find_generator_scripts, is_deliverable_path
from .worker_prompt import compose_worker_prompt

logger = logging.getLogger(__name__)


MAX_WORKERS_PER_MISSION: Final[int] = 5
"""ADR-0009 + jarvis.toml [phase6.orchestrator]: max_workers_per_mission."""

_HEARTBEAT_INTERVAL_S: float = 20.0
"""How often (seconds) the orchestrator writes a liveness heartbeat to the
mission header while a worker is draining. Recovery uses
max(last_event_ts, last_heartbeat_ms) as freshness so a busy-but-silent
worker (Opus, long tool calls, Computer-Use) is never swept as orphaned."""

_WORKER_PROGRESS_MIN_INTERVAL_S: float = 1.5
"""Throttle for live WorkerProgress events emitted from the worker drain loop.
A streaming worker can emit many assistant/tool events per second; we surface a
human-readable progress note to the UI ReasoningPanel at most this often (the
first note always goes out) so the bus / event store / WebSocket fan-out are
not flooded on a long, busy mission. Pure transparency — read-only, never on the
voice critical path (AP-9). The dominant slow-mission symptom this addresses:
a long-but-healthy worker that emits no visible progress looks identical to a
hang, so the user restarts the app mid-run and finished work is discarded as
app_shutdown (forensic 2026-06-15, missions 019ecb35 / 019ec708)."""

_PROGRESS_NOTE_MAX_CHARS: int = 160
"""Cap on a single WorkerProgress note — enough for a tool name + a command /
path / text snippet, short enough to keep the event small."""

# Mission time-budget shape (user mandate 2026-06-10: "a task should take
# 5-15 minutes on average and never run much past 20 — nobody waits longer,
# but the output must stay remarkable"). Supersedes the 2026-06-09 "more
# time" mandate that allowed 3 x 20-minute iterations (38-49-minute missions,
# live 019eb27f/019eb288 — users gave up and restarted the app mid-run).
#
# The shape: iteration 0 (the main build) gets the large budget; correction
# iterations get the short one — they refine an EXISTING workspace guided by
# the critic's correction_instruction, they do not rebuild from scratch. A
# worker that overruns its slice is killed-with-work-preserved and its
# partial diff is still GRADED (never discarded), so quality control stays
# fully active. MAX_CRITIC_LOOPS is untouched (ADR-0009); the time guard
# below is an additional bound, not a loop-count change.
_ITER0_WORKER_TIMEOUT_S: float = 720.0
_CORRECTION_WORKER_TIMEOUT_S: float = 360.0
# Soft per-task budget consulted BEFORE starting a correction iteration: when
# elapsed + (correction slice + one critic call) would overshoot, the loop
# ends with the existing exhausted semantics instead of overrunning the
# 20-minute target. 1380 s = 12-min iter0 + a fast critic + one full 6-min
# correction still fits; a second correction almost never does (by design).
_TASK_TIME_BUDGET_S: float = 1380.0
# One critic subprocess call (mirrors critic.runner.DEFAULT_TIMEOUT_SECONDS).
_CRITIC_TIME_RESERVE_S: float = 240.0


def _worker_error_is_transient(err: str) -> bool:
    """True when a worker's terminal error is a passing condition, not a fault.

    Transient errors qualify delivered work for critic grading (non-empty
    diff) or a fresh-spawn retry (empty diff) instead of an opaque
    ``task_error``. Two families:

    - Throttling/availability: rate limits, 429, overloaded, 5xx.
    - Subscription-window limits (live mission 019eb2fd, 2026-06-10 21:23):
      Claude Max says "You've hit your session limit · resets 11:10pm",
      ChatGPT/codex say "hit your usage limit" / "out of credits". The
      worker died AFTER writing the complete deliverable and the old
      matcher (rate-limit phrasings only) discarded it as task_error.
      A window that resets is transient by definition.
    """
    if not err:
        return False
    err_lower = err.lower()
    return any(
        m in err_lower
        for m in (
            "rate limit", "rate_limit", "ratelimit",
            "too many requests", "429", "overloaded",
            "503", "service unavailable", "please try again",
            "session limit", "usage limit", "out of credits",
            "out_of_credits",
        )
    )


def _worker_error_is_auth(err: str) -> bool:
    """True when a worker's terminal error means its provider AUTH is dead.

    Provider-agnostic by design (AP-21/AP-22): every worker kind surfaces its
    credential failure through this one classifier — the claude CLI's
    "Failed to authenticate. API Error: 401 Invalid authentication credentials"
    (2026-07-06, expired Claude Max OAuth token), codex's "Failed to refresh
    token. Please log in again." (2026-06-08), an API worker's
    "invalid_api_key" / 401, a CLI's "Not logged in".

    Why it matters: dead auth used to fall through to the fatal task_error
    branch, killing the mission terminally even though the worker factory is
    re-consulted on every iteration and — with the dead provider flagged by
    the worker (claude_auth_dead / codex_needs_reauth) — would pick a HEALTHY
    family on the retry. Classifying auth as retryable turns a one-provider
    brick into a cross-family recovery for EVERY worker kind.
    """
    if not err:
        return False
    err_lower = err.lower()
    return any(
        m in err_lower
        for m in (
            "failed to authenticate",
            "invalid authentication",
            "authentication failed",
            "authentication_error",
            "unauthorized",
            "401",
            "invalid api key",
            "invalid x-api-key",
            "invalid_api_key",
            "not logged in",
            "please run /login",
            "log in again",
            "login again",
            "token expired",
            "expired token",
            "oauth token has expired",
        )
    )


def _classify_worker_error(err: str, *, timed_out: bool = False) -> str | None:
    """Map a worker's terminal error onto MISSION_ERROR_CLASSES, or ``None``.

    Pure + offline; the single place the orchestrator derives the
    provider-failure class that flows to WorkerKilled/MissionFailed and from
    there to the Sub-Agents view and the voice announcer. Order matters:
    the structured timeout flag wins (it is the robust signal), then auth
    (the most specific text class), then quota/billing, then the generic
    transient bucket. Unclassifiable errors return ``None`` so consumers
    fall back to the mission-level ``reason``.
    """
    if timed_out:
        return "worker_timeout"
    if not err:
        return None
    low = err.lower()
    if _worker_error_is_auth(low):
        return "provider_auth"
    if any(
        m in low
        for m in (
            "balance", "billing", "credit",
            "session limit", "usage limit",
            "rate limit", "rate_limit", "ratelimit",
            "too many requests", "429",
            "out of credits", "out_of_credits",
        )
    ):
        return "provider_quota"
    if "timeout" in low:
        return "worker_timeout"
    if _worker_error_is_transient(low):
        return "provider_unreachable"
    return None


def _classify_worktree_setup_failure(exc: BaseException) -> str:
    """Map a ``WorktreeManager.create()`` failure to an actionable reason code.

    AP-23 wave-2 audit finding 1: a fresh, ZIP-downloaded, or PATH-broken
    install used to make ``create()`` raise a raw ``FileNotFoundError`` (git
    binary missing) that escaped the task loop entirely — every mission,
    even a pure in-process API-worker task, crashed with no user-visible
    reason because every task is wrapped in a worktree first. Distinguishes:

    - ``git_missing``: no git executable on PATH at all
      (``FileNotFoundError``/``OSError`` from the subprocess spawn itself).
    - ``source_checkout_unavailable``: the installed application tree has no
      usable Git history. This is expected for copied/frozen/container
      distributions and blocks only source-dependent tasks.
    - ``git_not_a_repository``: backwards-compatible classification for an
      older ``git worktree add`` failure carrying "not a git repository".
    - ``worktree_setup_failed``: the pre-existing generic fallback for
      everything else (200-char path-length cap ``ValueError``, an
      index-lock ``CalledProcessError``, etc.) — unchanged behaviour.

    Pure + offline; never raises. Any exception type not explicitly handled
    above falls through to the generic fallback so this is always safe to
    call from an except clause.
    """
    if isinstance(exc, SourceCheckoutUnavailableError):
        return "source_checkout_unavailable"
    if isinstance(exc, FileNotFoundError):
        return "git_missing"
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        if exc.returncode == 128 and "not a git repository" in stderr.lower():
            return "git_not_a_repository"
    return "worktree_setup_failed"


# Mission-level wall-clock safety net. Bounds TOTAL execution time across all
# critic iterations + the critic subprocess + decomposition — the per-iteration
# worker cap does not. Measured AFTER the concurrency semaphore is acquired
# (queue wait is excluded). 25 minutes: the degressive per-iteration budgets
# above keep the normal worst case near 20 minutes; this catches a genuine
# runaway/hang, never slow-but-working code.
_MISSION_DEADLINE_S: float = 1500.0


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

def _is_deliverable_path(rel: str) -> bool:
    """True if a worktree-relative path is a genuine worker deliverable.

    Thin wrapper over the shared :func:`deliverable_paths.is_deliverable_path`
    — the single source of truth shared with the Outputs view + the user-folder
    mirror (anti-drift, BUG-008 class). False for managed worker-contract files
    (``AGENTS.md`` etc.), git-internal / state / dep-cache dirs, AND browser
    user-data / profile scratch (the 2026-06-21 chrome-profile leak,
    mission_019eeb34-bb67: a QA worker's 4 gitignored Chrome profiles were
    re-imported by the ``--ignored`` enumeration union below and buried the 2
    real deliverables in the Outputs view). Used to filter the untracked +
    ``--ignored`` union before copying into ``artifacts/files/``.
    """
    return is_deliverable_path(rel, managed_files=_MANAGED_PERSONA_FILES)


def _safe_read_text(path: Path) -> str:
    """Read a worktree file as UTF-8 text, never raising.

    Used by the generator-script filter (:func:`find_generator_scripts`) to
    inspect a candidate script's body. Only script-typed files reach here, so
    this never tries to slurp a large binary. Returns "" on any read error.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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


# Cap on the command output embedded per verified command — enough for a
# push/PR confirmation line, small enough not to blow the Critic prompt.
_COMMAND_EVIDENCE_MAX_CHARS: Final[int] = 1200


def _format_command_evidence_block(commands: list[tuple[str, str]]) -> str:
    """Render verified mutating git/GitHub commands as a Critic diff block.

    Like :func:`_format_external_write_block`, the block is deliberately NOT a
    ``diff --git`` hunk (so the artifact archive's new-file regex ignores it)
    yet IS meaningful to :func:`_real_diff_is_empty` (it does not start with
    ``# untracked-not-in-diff:``). Each command's real subprocess output is
    ``+``-prefixed so any embedded ``diff --git`` text in the output cannot be
    mis-parsed as a diff control line.
    """
    header = (
        "diff --command-evidence b/<git-github-operations>\n"
        "# verified-command-execution\n"
        "# ground-truth: the following state-changing git/GitHub commands ran "
        "with a NON-ERRORED result this iteration. The output below was written "
        "by the real subprocess (git/gh), NOT asserted by the worker — treat it "
        "as on-execution-verified evidence. A 'commit and push' / 'open a PR' "
        "task legitimately leaves an empty worktree diff; this block IS the "
        "deliverable, so do not veto under the empty-diff rule.\n"
    )
    lines: list[str] = []
    for command, output in commands:
        lines.append(f"$ {command}")
        excerpt = (output or "").strip()
        if len(excerpt) > _COMMAND_EVIDENCE_MAX_CHARS:
            excerpt = excerpt[: _COMMAND_EVIDENCE_MAX_CHARS - 1].rstrip() + "…"
        if excerpt:
            lines.extend("+" + ln for ln in excerpt.splitlines())
        else:
            lines.append("+ (command succeeded; no output captured)")
    return header + "\n".join(lines) + "\n"


def _format_desktop_action_evidence_block(actions: list[tuple[str, str]]) -> str:
    """Render verified desktop-launch commands as a Critic diff block.

    Like :func:`_format_command_evidence_block`, the block is deliberately NOT
    a ``diff --git`` hunk (so the artifact archive's new-file regex ignores it)
    yet IS meaningful to :func:`_real_diff_is_empty`. Each command's real
    subprocess output (or the sentinel string for a silent detached spawn) is
    ``+``-prefixed so any embedded ``diff --git`` text cannot be mis-parsed.
    """
    header = (
        "diff --desktop-action-evidence b/<desktop-launch-operations>\n"
        "# verified-desktop-launch\n"
        "# ground-truth: the following desktop/process-launch commands ran "
        "with a NON-ERRORED result this iteration. A task like 'open Explorer' "
        "/ 'launch Chrome' / 'start the calculator' leaves NO worktree file "
        "change — the deliverable is a running process, not a file. The output "
        "below was captured from a real, non-errored Bash/shell tool_use; "
        "'(command succeeded; no output captured)' means the spawn was silent "
        "(detached process) — treat that as a successful launch, not as missing "
        "evidence. Do NOT veto this diff under the empty-diff rule.\n"
    )
    lines: list[str] = []
    for command, output in actions:
        lines.append(f"$ {command}")
        excerpt = (output or "").strip()
        if len(excerpt) > _COMMAND_EVIDENCE_MAX_CHARS:
            excerpt = excerpt[: _COMMAND_EVIDENCE_MAX_CHARS - 1].rstrip() + "…"
        if excerpt:
            lines.extend("+" + ln for ln in excerpt.splitlines())
        else:
            lines.append("+ (command succeeded; no output captured)")
    return header + "\n".join(lines) + "\n"


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
    are octal-escaped (``ä`` → ``\\303\\244``) and a handful of control  # i18n-allow
    characters are backslash-escaped (``\\t``, ``\\n``, ``\\\\``, ``\\"``).
    A bilingual/German assistant routinely produces umlaut deliverable
    names (``Werbungä.html``, ``Lebenslauf-Müller.pdf``); without decoding,  # i18n-allow
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


def _worker_progress_note(ev: Any) -> str | None:
    """Build a short, human-readable progress note from a worker stream event.

    Returns a note for an ``assistant`` message that carried a tool_use (an
    action — preferred) or visible text; ``None`` for events not worth surfacing
    as live progress (the terminal result, token deltas, tool_result echoes).

    Both ClaudeDirectWorker and CodexDirectWorker translate their native frames
    into these Claude-shaped assistant events (codex maps file_change -> a
    synthetic ``Write`` tool_use, command_execution -> ``Bash``), so this single
    extractor lights up progress for both backends.
    """
    if getattr(ev, "type", None) != "assistant":
        return None
    message = getattr(ev, "message", None)
    if not isinstance(message, dict):
        return None
    text_bits: list[str] = []
    for blk in message.get("content") or []:
        if not isinstance(blk, dict):
            continue
        if blk.get("type") == "tool_use":
            name = str(blk.get("name") or "tool")
            tool_input = blk.get("input")
            detail = ""
            if isinstance(tool_input, dict):
                detail = str(
                    tool_input.get("command")
                    or tool_input.get("file_path")
                    or tool_input.get("path")
                    or tool_input.get("pattern")
                    or ""
                ).strip()
            note = f"{name}: {detail}" if detail else name
            return note[:_PROGRESS_NOTE_MAX_CHARS]
        if blk.get("type") == "text":
            t = str(blk.get("text") or "").strip()
            if t:
                text_bits.append(t)
    if text_bits:
        return " ".join(text_bits)[:_PROGRESS_NOTE_MAX_CHARS]
    return None


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
    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"
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
    # `worktree_setup_failed` ("Could not create a workspace.") so the
    # user hears an actionable cause instead of "The worker was aborted."
    # AP-23 wave-2 finding 1 (2026-07-07): the same outcome now also covers
    # a missing git binary and a ZIP/no-.git install — see
    # `_classify_worktree_setup_failure` and `_setup_failure_reason`, which
    # refine the surfaced reason to `git_missing` / `git_not_a_repository`.
    SETUP_FAILED = "setup_failed"
    # Live deep-dive 2026-06-07 (mission 019ea1da): a Computer-Use mission whose
    # final iteration hit the 630s wall-clock cap returned the generic ERROR,
    # which aggregated to `task_error` -- i.e. the "worker aborted" voice phrase.
    # The user heard a worker-abort phrase for what was really a TIMEOUT, on a
    # mission they never consciously spawned. This distinct outcome lets the
    # failure-reason mapper surface `attempts_timed_out` (the "time limit
    # exceeded" phrase) so a run that ran out of time is honestly labelled a
    # timeout, not an abort. A non-timeout worker error (auth/billing/crash)
    # stays the generic ERROR -> task_error. (Voice strings live in
    # readback.FAILURE_REASON_PHRASES; see the deep-dive README.)
    TIMED_OUT = "timed_out"


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
        # Mission-level wall-clock deadline (Task 2.2). Measured AFTER the
        # concurrency semaphore is acquired so queued missions are not
        # penalised for waiting. Deliberately GENEROUS — the worst legitimate
        # mission observed was ~18 min. Injected by tests via a tiny value.
        mission_deadline_s: float = _MISSION_DEADLINE_S,
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
        self._mission_deadline_s = mission_deadline_s
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
        # Last classified worker-failure per mission (error_class,
        # error_detail, failed_provider) — written by the worker_error branch
        # in the critic loop, consumed once by _fail_mission so the terminal
        # MissionFailed event can name the real cause (2026-07-06 incident).
        # Popped on BOTH terminal paths (fail + approve) so a retried-then-
        # approved mission never leaks a stale context into a later run.
        self._mission_failure_context: dict[str, dict[str, str | None]] = {}
        # Per-mission classified worktree-setup-failure reason (AP-23 wave-2
        # finding 1) — written by the except clause in
        # `_run_task_with_critic_loop` when `WorktreeManager.create()` raises,
        # consumed once (popped) by the `SETUP_FAILED` aggregation branch in
        # `run_mission` so the terminal reason names the real cause
        # ("git_missing" / "source_checkout_unavailable") instead of the
        # generic "worktree_setup_failed" fallback. Last write wins, same
        # convention as `_mission_failure_context`.
        self._setup_failure_reason: dict[str, str] = {}
        # Per-mission worker answers for read-only/informational tasks (empty
        # diff + tool evidence). Surfaced as MissionApproved.summary_de so the
        # voice readback speaks the actual answer instead of "Mission
        # abgeschlossen." See jarvis.missions.stream_evidence.readonly_answer.
        self._task_answers: dict[str, list[str]] = {}
        # In-flight run_mission tasks by mission_id — lets an external
        # cancel (UI hold-to-abort) abort a running mission mid-flight.
        self._running_missions: dict[str, asyncio.Task[Any]] = {}

    async def run_mission(self, mission_id: str) -> MissionState:
        """Runs a mission end-to-end and returns the final state.

        Returns: APPROVED | FAILED | CANCELLED | TIMED_OUT.

        The in-flight asyncio task is tracked in ``_running_missions`` so an
        external cancel (REST ``POST /api/missions/{id}/cancel``) can abort
        the run mid-flight via :meth:`cancel_running_mission`.
        """
        task = asyncio.current_task()
        if task is not None:
            self._running_missions[mission_id] = task
        try:
            return await self._run_mission_inner(mission_id)
        finally:
            self._running_missions.pop(mission_id, None)

    def cancel_running_mission(self, mission_id: str) -> bool:
        """Cancel the in-flight ``run_mission`` task for this mission.

        Returns ``True`` iff a live task was found and received the cancel
        request. The cancellation propagates through the per-step TaskGroup;
        every worker's Job-Object context manager closes on exit and kills
        the worker subprocess tree — the same teardown path the wall-clock
        mission timeout uses. Callers must flip the mission state to
        CANCELLED *before* calling this so a late terminal transition from
        the dying task cannot race the user's decision (``_safe_transition``
        and ``_fail_mission`` both tolerate the already-terminal state).
        """
        task = self._running_missions.get(mission_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def running_mission_ids(self) -> list[str]:
        """Ids of missions whose ``run_mission`` task is currently in flight.

        Public, read-only accessor over the private tracking map so the restart
        guard (POST /api/settings/restart-app) can refuse to silently kill live
        missions. A done/cancelled task is excluded — the same liveness test
        ``cancel_running_mission``/``cancel_all_running`` use — so a finished
        mission never spuriously blocks a restart.
        """
        return [
            mission_id
            for mission_id, task in self._running_missions.items()
            if task is not None and not task.done()
        ]

    async def cancel_all_running(self, *, reason: str = "app_shutdown") -> list[str]:
        """Finalize + kill every in-flight mission (shutdown/restart path).

        Live incident 2026-06-10 19:24:12 (missions 019eb27f + 019eb288):
        the app's self-restart killed the process with two missions in
        flight; nothing finalized them, so they lingered non-terminal until
        the recovery re-sweep buried them 30 minutes later as opaque
        crash_recovery / ERROR cards with zero artifacts. On shutdown each
        tracked mission is flipped to CANCELLED FIRST (terminal wall — the
        same protocol as the REST cancel route), then its run task is
        cancelled and awaited briefly so the dying tasks finish their
        teardown before the mission store closes.

        Returns the mission ids that were finalized. Never raises — a
        failing state flip is logged and the task is cancelled anyway.
        """
        finalized: list[str] = []
        tasks: list[asyncio.Task[Any]] = []
        for mission_id in list(self._running_missions.keys()):
            task = self._running_missions.get(mission_id)
            if task is None or task.done():
                continue
            transitioned = False
            try:
                await self._manager.transition_state(
                    mission_id,
                    MissionState.CANCELLED,
                    reason=reason,
                    source_actor="kontrollierer",
                )
                transitioned = True
            except IllegalStateTransition:
                # Already terminal — still cancel the zombie task below.
                pass
            except Exception:  # noqa: BLE001 — shutdown must never crash here
                logger.exception(
                    "cancel_all_running: state flip failed for %s", mission_id
                )
            if transitioned:
                try:
                    await self._manager.store.append_and_publish(
                        EventEnvelope(
                            mission_id=mission_id,
                            source_actor="kontrollierer",
                            ts_ms=now_ms(),
                            payload=MissionCancelled(
                                cascade=True,
                                reason=reason,
                            ),
                        )
                    )
                except Exception:  # noqa: BLE001 — shutdown still kills the worker
                    logger.exception(
                        "cancel_all_running: terminal event failed for %s",
                        mission_id,
                    )
            task.cancel()
            tasks.append(task)
            finalized.append(mission_id)
        if tasks:
            with suppress(Exception):
                await asyncio.wait(tasks, timeout=5.0)
            logger.info(
                "cancel_all_running: finalized %d in-flight mission(s) as "
                "CANCELLED (%s): %s",
                len(finalized), reason, finalized,
            )
        return finalized

    async def _run_mission_inner(self, mission_id: str) -> MissionState:
        """Mission body — the tracking wrapper lives in :meth:`run_mission`."""
        view = await self._manager.mission(mission_id)
        if view is None:
            raise KeyError(f"Mission not found: {mission_id}")

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
        logger.info(
            "swarm team: coordinator -> scouts -> builders mission=%s "
            "steps=%d n_workers=%d slugs=%s expected_output=%r",
            mission_id,
            len(plan.steps),
            plan.n_workers,
            ",".join(step.slug for step in plan.steps),
            plan.expected_output,
        )

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
            try:
                async with asyncio.timeout(self._mission_deadline_s):
                    async with asyncio.TaskGroup() as tg:
                        for step in plan.steps:
                            tg.create_task(_run(step), name=f"task-{step.task_id[:13]}")
            except TimeoutError:
                # The mission ran past its wall-clock deadline. TaskGroup
                # cancellation has already propagated to the worker(s) — their
                # Job Objects close on context exit and kill the subprocesses.
                # Fail HONESTLY as a timeout (not the generic "worker aborted").
                logger.warning(
                    "run_mission: mission %s exceeded the %.0fs wall-clock "
                    "deadline — failing as attempts_timed_out",
                    mission_id, self._mission_deadline_s,
                )
                partial = self._collect_partial_artifacts(mission_id, plan)
                await self._fail_mission(
                    mission_id, "attempts_timed_out", partial_artifacts=partial
                )
                return MissionState.FAILED

        # Aggregate
        # A plan that produced no task outcomes at all must never be approved
        # (all(...) over an empty list is vacuously True). Treat zero work as a
        # task error, not a silent success.
        if not task_outcomes:
            logger.warning(
                "run_mission: %s produced no task outcomes — failing", mission_id
            )
            await self._fail_mission(mission_id, "task_error")
            return MissionState.FAILED
        if all(o == TaskOutcome.APPROVED for o in task_outcomes):
            await self._approve_mission(mission_id, plan, prompt=view.prompt)
            return MissionState.APPROVED

        # Which task failed determines the failure reason.
        # Collect per-iter diff paths once so we can attach them to every
        # failure mode (not just CRITIC_UNAVAILABLE) — even a budget-cap
        # or critic-reject is more recoverable when the user can see the
        # work the worker actually produced.
        partial = self._collect_partial_artifacts(mission_id, plan)
        # Execution/setup failures outrank review outcomes in a multi-task
        # mission. Otherwise one review time guard can mask a real worker crash
        # in another step and show the user the wrong terminal cause.
        if TaskOutcome.BUDGET_EXCEEDED in task_outcomes:
            await self._fail_mission(
                mission_id, "budget_exceeded", partial_artifacts=partial
            )
        elif TaskOutcome.SETUP_FAILED in task_outcomes:
            # Worktree-create failure (path cap / git index lock / missing git
            # binary / no .git repo) — surface an actionable cause instead of
            # the generic "worker aborted" (#8). AP-23 wave-2 finding 1: the
            # classified reason (git_missing / source_checkout_unavailable)
            # set by `_run_task_with_critic_loop`'s except clause takes
            # priority over the pre-existing generic fallback.
            setup_reason = self._setup_failure_reason.pop(
                mission_id, "worktree_setup_failed"
            )
            await self._fail_mission(
                mission_id, setup_reason, partial_artifacts=partial
            )
        elif TaskOutcome.TIMED_OUT in task_outcomes:
            # Final-attempt wall-clock timeout — honest "timeout" reason instead
            # of the generic "worker aborted" (deep-dive 2026-06-07,
            # mission 019ea1da). The WorkerKilled event already carries
            # reason="timeout"; surface the same truth at the mission level so
            # the voice layer never speaks the "worker aborted" phrase for a run
            # that simply ran out of time.
            # A worker timeout outranks review outcomes in another step because
            # it is an execution failure and must remain visible. Budget remains
            # the only higher-priority aggregate terminal cause.
            await self._fail_mission(
                mission_id, "attempts_timed_out", partial_artifacts=partial
            )
        elif TaskOutcome.ERROR in task_outcomes:
            await self._fail_mission(
                mission_id, "task_error", partial_artifacts=partial
            )
        # CRITIC_UNAVAILABLE has priority over the other review outcomes: when
        # the critic crashed before iter0's verdict, later empty-diff rounds do
        # not turn that infrastructure failure into ordinary exhaustion.
        elif TaskOutcome.CRITIC_UNAVAILABLE in task_outcomes:
            await self._fail_mission(
                mission_id, "critic_unavailable", partial_artifacts=partial
            )
        elif TaskOutcome.REJECTED in task_outcomes:
            await self._fail_mission(
                mission_id, "critic_rejected", partial_artifacts=partial
            )
        elif TaskOutcome.TIME_BUDGET_EXHAUSTED in task_outcomes:
            await self._fail_mission(
                mission_id,
                "review_time_budget_exhausted",
                partial_artifacts=partial,
            )
        elif TaskOutcome.EXHAUSTED in task_outcomes:
            await self._fail_mission(
                mission_id, "critic_loop_exhausted", partial_artifacts=partial
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
                # `step.needs_repo` (default True) decides the workspace shape:
                # True → full registered worktree of the repo (isolation for
                # repo tasks); False → lean empty git repo for standalone
                # external-artefact tasks so the worker isn't forced to explore
                # the whole codebase first. The AGENTS.md contract is
                # materialised into BOTH shapes identically (below), and
                # `_capture_diff` works against both because each has a HEAD.
                worktree = self._worktrees.create(
                    mission_slug=_short_slug(mission_prompt),
                    task_id=step.task_id,
                    needs_repo=step.needs_repo,
                )
            except (
                SourceCheckoutUnavailableError,
                subprocess.CalledProcessError,
                ValueError,
                OSError,
            ) as exc:
                # OSError added (AP-23 wave-2 finding 1): a missing git binary
                # raises FileNotFoundError (an OSError subclass), which used
                # to escape uncaught here and crash every mission — even a
                # pure in-process API-worker task, since every task is
                # wrapped in a worktree first. Classify the cause so the
                # aggregation branch in ``run_mission`` can surface an
                # actionable reason instead of the generic
                # "worktree_setup_failed" for every facet.
                logger.exception("Task %s: worktree-create failed: %s", step.task_id, exc)
                self._setup_failure_reason[mission_id] = (
                    _classify_worktree_setup_failure(exc)
                )
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
        # Anchor for the per-task time budget (queue wait excluded — the
        # caller already holds the concurrency semaphore, mirroring
        # _MISSION_DEADLINE_S semantics).
        task_t0 = time.monotonic()
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
            # Lead every worker prompt with the artifact-language directive so
            # generated code defaults to English regardless of the request
            # language (the German-request -> German-code leak). This is the one
            # chokepoint every worker prompt passes through — all decomposition
            # paths, all worker CLIs, every critic iteration.
            worker_prompt = compose_worker_prompt(prior_block, step.prompt)

            # State-Machine drive-thru:
            #   iter 0: PENDING/RUNNING -> CRITIQUING (single jump).
            #   iter 1+: CRITIQUING -> LOOPING -> RUNNING -> CRITIQUING so the
            #   transition back into CRITIQUING below is legal (CRITIQUING ->
            #   CRITIQUING would be illegal and silently swallowed).
            if iteration > 0:
                # Time-budget guard (2026-06-10 mandate): a correction
                # iteration only starts when it can FINISH inside the task
                # budget (its worker slice + one critic call). Otherwise the
                # loop ends with an explicit time-budget outcome — a late,
                # rushed correction would overshoot the 20-minute target
                # without improving the deliverable. Do not claim that all
                # three critic attempts ran when this guard prevented one.
                elapsed_s = time.monotonic() - task_t0
                needed_s = _CORRECTION_WORKER_TIMEOUT_S + _CRITIC_TIME_RESERVE_S
                if elapsed_s + needed_s > _TASK_TIME_BUDGET_S:
                    logger.warning(
                        "Task %s: time budget exhausted before iter-%d "
                        "(elapsed=%.0fs + needed=%.0fs > budget=%.0fs) — "
                        "ending the critic loop with the current state",
                        step.task_id, iteration, elapsed_s, needed_s,
                        _TASK_TIME_BUDGET_S,
                    )
                    return TaskOutcome.TIME_BUDGET_EXHAUSTED
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
                # BUG-LIVE-03 (2026-05-14): never reuse the `openclaw` session-id
                # across critic iterations. Live repro mission_019e2605
                # showed that `openclaw` 2026.5.7 prefers the failover chain
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
            except Exception:  # noqa: BLE001
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
            # Side-effecting git/GitHub work (commit, push, open a PR) leaves no
            # worktree diff — the change is a commit or a remote ref update done
            # via the shell. Credit those verified, non-errored commands so a
            # "commit and push" / "open PRs" task is reviewed on its real
            # subprocess output instead of failing 3× on a blind empty diff
            # (the dominant Git/GitHub critic_loop_exhausted false-negative).
            diff_text = self._augment_diff_with_command_evidence(
                diff_text, log_text
            )
            # Desktop/process-launch work (open Explorer, launch Chrome, etc.)
            # also produces NO worktree diff — the deliverable is a running
            # process. Credit verified launch commands so a diff-less
            # "open Explorer" / "start Calculator" task can be approved instead
            # of failing 3× with critic_loop_exhausted. Mirrors the git/gh
            # command-evidence path above.
            diff_text = self._augment_diff_with_desktop_action_evidence(
                diff_text, log_text
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
            if spawn_result.supervisor_tool_failed:
                error_detail = (
                    spawn_result.supervisor_tool_error
                    or "Supervisor tool execution did not complete successfully."
                )[:300]
                self._mission_failure_context[mission_id] = {
                    "error_class": "supervisor_tool_failed",
                    "error_detail": error_detail,
                    "failed_provider": (
                        getattr(worker, "provider", None)
                        or getattr(worker, "cli", None)
                    ),
                }
                logger.error(
                    "Task %s iter %d: refusing critic review after an unclean "
                    "supervisor tool grant: %s",
                    step.task_id,
                    iteration,
                    error_detail,
                )
                await self._publish_worker_killed(
                    mission_id=mission_id,
                    worker_id=spawn_result.worker_id,
                    reason="worker_error",
                    error_class="supervisor_tool_failed",
                    error_detail=error_detail,
                )
                return TaskOutcome.ERROR

            if spawn_result.worker_error:
                err_lower = spawn_result.worker_error.lower()
                # Structured flag first (robust), result-text "timeout" as a
                # belt-and-suspenders fallback. The flag is why a codex/gemini
                # timeout that left a real diff now reaches the grade-partial
                # branch below instead of being discarded as task_error.
                is_timeout = spawn_result.worker_timed_out or "timeout" in err_lower
                # A transient provider error (rate-limit / overloaded / 429 /
                # 503) is NOT a real failure — retry on a fresh, serialised spawn
                # like a timeout instead of killing the mission as task_error.
                # Keeps every provider reliable when the shared Claude Max OAuth
                # subscription throttles under load (2026-06-09 codex verify:
                # task_error rounds were throttle, not a code fault).
                is_transient = _worker_error_is_transient(err_lower)
                # Dead provider AUTH (401 / not logged in / expired token) is
                # retryable too — not because the credential heals, but because
                # the worker factory is re-consulted on every iteration and the
                # failing worker flagged its provider dead (claude_auth_dead /
                # codex_needs_reauth), so the retry runs on a DIFFERENT family
                # (2026-07-06: expired Claude OAuth token killed every mission
                # terminally while codex + OpenRouter were healthy — AP-22).
                is_auth = _worker_error_is_auth(err_lower)
                # Classify once, reuse below for both the recorded context and
                # the WorkerKilled payload — avoids re-deriving error_detail
                # from spawn_result.worker_error twice and re-fetching
                # error_class back out of the dict we just wrote.
                error_class = _classify_worker_error(
                    spawn_result.worker_error,
                    timed_out=spawn_result.worker_timed_out,
                )
                error_detail = spawn_result.worker_error[:300]
                # Record the classified failure for the terminal MissionFailed
                # event. Last write wins: the final iteration's cause is the
                # one the mission actually died of.
                self._mission_failure_context[mission_id] = {
                    "error_class": error_class,
                    "error_detail": error_detail,
                    "failed_provider": (
                        getattr(worker, "provider", None)
                        or getattr(worker, "cli", None)
                    ),
                }
                kill_reason: Literal[
                    "timeout", "user", "budget", "parent_cancelled",
                    "injection_detected", "path_guard", "worker_error",
                ] = (
                    "budget"
                    if ("balance" in err_lower or "billing" in err_lower
                        or "credit" in err_lower)
                    else "timeout" if is_timeout
                    # Honest: a non-timeout/non-billing worker error is NOT a
                    # user cancellation. "worker_error" replaces the old "user"
                    # mislabel (five-layer: events.py / TS / voice / parity).
                    else "worker_error"
                )
                logger.error(
                    "Task %s iter %d: worker terminal error (%s) — %s",
                    step.task_id, iteration, kill_reason,
                    spawn_result.worker_error,
                )
                # A timeout-killed worker that STILL left real files on disk
                # (non-empty diff after the external-write augmentation above)
                # is GRADED by the critic, not discarded. Live false-negative
                # E1: a long Git/build task ("open PRs", "commit and push")
                # completes its writes, then the worker's 630s wall-clock cap
                # fires on the trailing network/IO and the process is killed.
                # The old loop returned TaskOutcome.ERROR → task_error and threw
                # the real on-disk work away. The diff is the ground truth; the
                # critic is the judge — fall through to the draft + critic call
                # below so the user keeps work that actually happened. An empty
                # diff means nothing was produced (a genuine zero-output hang,
                # e.g. Claude Max OAuth contention), so keep the transient-retry
                # / hard-fail behaviour for that case.
                if (
                    is_timeout or is_transient or is_auth
                ) and not _real_diff_is_empty(diff_text):
                    logger.warning(
                        "Task %s iter %d: worker hit a timeout/transient/auth "
                        "error but left a non-empty diff (%d bytes) — grading "
                        "the partial work with the critic instead of failing "
                        "as task_error",
                        step.task_id, iteration, len(diff_text),
                    )
                    # The critic is now the judge: if it rejects, the honest
                    # cause is the critic verdict, not this (survived) worker
                    # error — drop the recorded context so a later
                    # _fail_mission cannot misattribute the failure (stale
                    # cross-outcome context, AP-19 class).
                    self._mission_failure_context.pop(mission_id, None)
                    # Deliberately fall through (no continue / no return): the
                    # WorkerDraftReady publish + critic call below grade the
                    # partial deliverable. We intentionally do NOT emit
                    # WorkerKilled here: the worker's output is being judged, not
                    # discarded — emitting a kill event would contradict a
                    # subsequent MissionApproved. If the critic instead rejects
                    # the partial work, the honest failure cause is the critic
                    # verdict (critic_loop_exhausted / rejected), not "timeout";
                    # the timeout itself is recorded in the logger.error above.
                # A worker timeout with no output is transient (Claude Max OAuth
                # contention: claude produced zero output and was killed by the
                # first-output gate). Retry on a fresh spawn instead of failing
                # the whole mission — the heavy-phase semaphore (_mission_sem,
                # default 1) serialises the retry so it no longer competes with
                # the spawn that just timed out. Budget/auth errors stay fatal.
                elif (
                    is_timeout or is_transient or is_auth
                ) and iteration < MAX_CRITIC_LOOPS - 1:
                    logger.warning(
                        "Task %s iter %d: worker %s with no usable output — "
                        "retrying on a fresh spawn%s",
                        step.task_id, iteration,
                        "timed out" if is_timeout
                        else "hit a transient/rate-limit error" if is_transient
                        else "hit a dead-auth error (401/not logged in)",
                        "" if not is_auth
                        else "; the worker factory will pick a different "
                        "provider family (the failing provider is flagged "
                        "auth-dead)",
                    )
                    continue
                else:
                    await self._publish_worker_killed(
                        mission_id=mission_id,
                        worker_id=spawn_result.worker_id,
                        reason=kill_reason,
                        error_class=error_class,
                        error_detail=error_detail,
                    )
                    # A worker that ran out of time (wall-clock cap) on its
                    # final attempt is a TIMEOUT, not a crash. Surface the
                    # honest `attempts_timed_out` reason (deep-dive 2026-06-07,
                    # mission 019ea1da) so the voice layer speaks the "time limit
                    # exceeded" phrase instead of the alarming "worker aborted"
                    # phrase that a real crash produces. Any other worker error
                    # (auth/billing/non-timeout crash) stays ERROR.
                    if is_timeout:
                        return TaskOutcome.TIMED_OUT
                    return TaskOutcome.ERROR

            # WorkerDraftReady event — BudgetTracker.bind_to_event_bus
            # auto-records cost_usd via the bus subscription (init.py:119).
            # We DO NOT call self._budget.record() explicitly here to avoid
            # double-counting. Hard-abort on overrun is detected by the
            # pre-spawn assert_under_limit() check at the top of each
            # iteration of this loop.
            # Persist the current draft BEFORE announcing it. The final archive
            # in the worktree-cleanup block is still authoritative, but it never
            # runs after a hard process crash. A WorkerDraftReady event must not
            # point at a deliverable that exists only in a disposable worktree.
            try:
                snapshot = await asyncio.to_thread(
                    self._archive_task_artifacts,
                    worktree=worktree,
                    mission_dir=mission_dir,
                    task_id=step.task_id,
                    drain_iteration_diffs=False,
                )
                if snapshot is None:
                    logger.warning(
                        "draft artifact snapshot returned no archive for %s",
                        worktree,
                    )
            except Exception:  # noqa: BLE001 — snapshot is best-effort
                logger.warning(
                    "draft artifact snapshot failed for %s",
                    worktree,
                    exc_info=True,
                )
            await self._publish_worker_draft(
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
                # Read-only / informational task: capture the worker's answer
                # so the mission speaks it back instead of "Mission
                # abgeschlossen." Two shapes qualify — (a) empty diff + real
                # tool evidence + answer, and (b) a pure question (no tools, no
                # diff) whose answer IS the deliverable (mission_prompt is
                # informational; live mission 019ec638, 2026-06-14). Code tasks
                # (non-empty diff) yield None here and keep the generic summary.
                answer = readonly_answer(diff_text, log_text, prompt=mission_prompt)
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
                next_model=(FRONTIER_MODEL if iteration + 1 >= 2 else "sonnet"),
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
            worker_timed_out: bool = False,
            supervisor_tool_failed: bool = False,
            supervisor_tool_error: str | None = None,
        ) -> None:
            self.worker_id = worker_id
            self.cost_usd = cost_usd
            self.tokens_used = tokens_used
            self.session_id = session_id
            # True when the worker's terminal result carried the structured
            # `timed_out` flag (a wall-clock / first-output timeout). The
            # orchestrator keys is_timeout off THIS, not a "timeout" substring
            # in worker_error — so a codex/gemini timeout that left a real diff
            # is graded, not discarded (mission 019eacb8).
            self.worker_timed_out = worker_timed_out
            # Non-None when the worker subprocess returned a terminal
            # `result` event with is_error=True. Carries the upstream
            # error message verbatim (e.g. "Credit balance is too low"
            # from a 400 billing_error, "Not logged in" when the CLI has
            # no credentials). Used by the calling loop to fail-fast
            # instead of grinding through MAX_CRITIC_LOOPS retries.
            self.worker_error = worker_error
            self.supervisor_tool_failed = supervisor_tool_failed
            self.supervisor_tool_error = supervisor_tool_error

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
    ) -> Kontrollierer._SpawnResult:
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
        worker_timed_out = False
        last_progress_at: float | None = None
        worker_timeout_s = (
            _ITER0_WORKER_TIMEOUT_S
            if iteration == 0
            else _CORRECTION_WORKER_TIMEOUT_S
        )
        from jarvis.missions.workers.worker_tool_broker import (
            EmptyWorkerToolBrokerBinding,
        )

        broker_binding: Any = EmptyWorkerToolBrokerBinding()
        inventory = getattr(worker, "capability_inventory", None)
        bind_broker = getattr(inventory, "bind_broker", None)
        if callable(bind_broker):
            try:
                issued_binding = bind_broker(
                    ttl_s=worker_timeout_s + 60.0,
                    mission_id=mission_id,
                    worker_id=worker_id,
                )
                if issued_binding is not None:
                    broker_binding = issued_binding
            except Exception:  # noqa: BLE001 - unavailable tools degrade honestly
                logger.exception(
                    "Mission %s worker %s: supervisor tool grant unavailable",
                    mission_id,
                    worker_id,
                )

        async with job:
            hb_stop = asyncio.Event()

            async def _heartbeat() -> None:
                """Write a liveness heartbeat every _HEARTBEAT_INTERVAL_S seconds.

                Runs concurrently with the worker drain. Any exception is
                swallowed so a transient DB hiccup never kills the worker path.
                """
                while not hb_stop.is_set():
                    try:
                        await self._manager.store.touch_heartbeat(mission_id, now_ms())
                    except Exception as hb_exc:  # noqa: BLE001 - heartbeat must never kill the worker
                        logger.debug("Heartbeat write failed (non-fatal): %s", hb_exc)
                    try:
                        await asyncio.wait_for(
                            hb_stop.wait(), timeout=_HEARTBEAT_INTERVAL_S
                        )
                    except TimeoutError:
                        pass

            hb_task = asyncio.create_task(_heartbeat())
            try:
                kwargs: dict[str, Any] = {
                    "model": step.model,
                    "allowed_tools": step.allowed_tools,
                    "mission_id": mission_id,
                    "_broker_binding": broker_binding,
                    # Degressive per-iteration budget (2026-06-10 mandate):
                    # the main build gets the large slice, corrections the
                    # short one — they refine an existing workspace. Workers
                    # preserve + grade partial work on timeout, so an overrun
                    # costs the cap, never the deliverable.
                    "timeout_s": worker_timeout_s,
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
                    # Structured timeout signal — read independently of is_error
                    # so the orchestrator recognises a timeout without matching
                    # the result-text wording (codex/gemini used to omit
                    # "timeout" → real work discarded).
                    if getattr(ev, "timed_out", False):
                        worker_timed_out = True

                    # Live progress: translate streamed worker activity into a
                    # WorkerProgress event so the UI ReasoningPanel shows what a
                    # long-but-healthy mission is doing (instead of an opaque
                    # spinner the user restarts mid-run). Throttled so a fast
                    # stream can't flood the bus/store; the first note always
                    # goes out. Read-only, off the voice critical path (AP-9);
                    # a failure here must never disturb the worker drain.
                    note = _worker_progress_note(ev)
                    if note:
                        now_mono = time.monotonic()
                        if (
                            last_progress_at is None
                            or now_mono - last_progress_at
                            >= _WORKER_PROGRESS_MIN_INTERVAL_S
                        ):
                            last_progress_at = now_mono
                            try:
                                await self._publish_worker_progress(
                                    mission_id=mission_id,
                                    worker_id=worker_id,
                                    note=note,
                                    tokens_so_far=tokens,
                                    cost_so_far=cost,
                                )
                            except Exception:  # noqa: BLE001 — progress is best-effort
                                logger.debug(
                                    "WorkerProgress publish failed (non-fatal)",
                                    exc_info=True,
                                )
            finally:
                hb_stop.set()
                await hb_task
                await broker_binding.aclose()

        broker_summary = broker_binding.execution_summary

        return Kontrollierer._SpawnResult(
            worker_id=worker_id,
            cost_usd=cost,
            tokens_used=tokens,
            session_id=session_id,
            worker_error=worker_error,
            worker_timed_out=worker_timed_out,
            supervisor_tool_failed=not broker_summary.clean,
            supervisor_tool_error=broker_summary.failure_summary,
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
        """Scans worker output against injection + path-guard.

        Returns:
            None if everything is clean. Otherwise: kill-reason (e.g.
            "injection_detected" or "path_guard:.env"). In that case the
            method also publishes a WorkerKilled event.
        """
        # 1) Injection-Scanner — high/critical blocks, med/low logged.
        # The stream log is reduced to worker-AUTHORED text first: the raw
        # stream.jsonl carries the output of the worker's read commands
        # (rg / Get-Content / tool_result blocks), and any worker reading
        # this repo's own safety blacklist, security docs or frontend
        # secret-panel code was killed AFTER delivering a clean diff
        # (live mission 019eadaf-272d, 2026-06-09 — 20 min of work
        # discarded via WorkerKilled(injection_detected) on rm -rf / from
        # jarvis.toml.example). Authored channels (assistant prose,
        # commands, tool_use inputs) stay fully scanned.
        detections = injection_scan(diff_text, where="diff")
        detections += injection_scan(
            extract_worker_authored_text(log_text), where="log"
        )
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

    async def _publish_worker_progress(
        self,
        *,
        mission_id: str,
        worker_id: str,
        note: str,
        tokens_so_far: int,
        cost_so_far: float,
    ) -> None:
        """Emit a lightweight WorkerProgress event for the UI live-progress panel.

        Transparency only — the payload carries a human-readable ``note`` plus
        the running token/cost totals. The WS fan-out + frontend ReasoningPanel
        already render this event type; the producer was the only missing link.
        """
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="worker",
            ts_ms=now_ms(),
            payload=WorkerProgress(
                worker_id=worker_id,
                note=note,
                tokens_so_far=tokens_so_far,
                cost_so_far=cost_so_far,
            ),
        )
        await self._manager.store.append_and_publish(env)

    async def _publish_worker_killed(
        self,
        *,
        mission_id: str,
        worker_id: str,
        reason: str,
        error_class: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Emits a WorkerKilled event on the bus + store."""
        # Reason is mapped onto the literal set in events.WorkerKilled.
        # path_guard:* is reduced to "path_guard" (voice-listener routing
        # distinguishes path_guard vs injection_detected for more precise TTS).
        if reason.startswith("path_guard"):
            mapped: str = "path_guard"
        elif reason in (
            "budget", "timeout", "user", "parent_cancelled",
            "injection_detected", "worker_error",
        ):
            mapped = reason
        else:
            mapped = "injection_detected"
        env = EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerKilled(
                worker_id=worker_id,
                reason=mapped,  # type: ignore[arg-type]
                error_class=error_class,
                error_detail=error_detail,
            ),
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

        Committed-deliverable capture (2026-07-03, mission 019f26d0-bb07): when
        the worktree records a BASE commit SHA (``read_worktree_base_sha``), we
        diff the index against that BASE rather than the live ``HEAD``. A worker
        that ``git commit``s its file advances ``HEAD`` past the deliverable, so
        ``git diff --cached HEAD`` renders EMPTY for it and the file is lost;
        diffing against the fork-point base surfaces committed AND uncommitted
        changes alike. Falls back to ``HEAD`` when no base was recorded (older /
        externally-created worktrees), so this is purely additive.

        All git calls are best-effort with a 10s cap; failure returns ""
        and logs at WARNING so the upstream Critic still gets a
        (truthful) empty diff rather than a stale one.
        """
        diff_base = read_worktree_base_sha(worktree) or "HEAD"
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
                # `diff_base` is the worktree's fork-point (or HEAD when none was
                # recorded) so a worker's own commits are still captured.
                ["git", "-c", "core.quotepath=false", "diff", "--cached", diff_base],
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

    def _augment_diff_with_command_evidence(
        self, diff_text: str, stream_text: str
    ) -> str:
        """Append verified mutating git/GitHub commands to the captured diff.

        ``_capture_diff`` is worktree-scoped and a "commit and push" / "open a
        PR" task produces NO worktree file change — the work is a commit or a
        remote ref update, executed via the shell. The Critic's GROUND-TRUTH-RULE
        then fails the empty diff 3× → ``critic_loop_exhausted`` even though the
        push/PR succeeded (the dominant Git/GitHub false-negative bucket). This
        helper restores ground truth: for every recognised state-changing
        git/``gh`` command the worker ran with a real, NON-ERRORED result
        (parsed from the stream by :func:`extract_verified_commands`), it appends
        a ``diff --command-evidence`` block carrying the command + its real
        subprocess output.

        Anti-hallucination is preserved: read-only commands never match, and a
        command with no correlated/errored result is never credited — so a bare
        "I pushed it" claim still falls through to the empty-diff veto. The
        Critic remains the final judge of whether the command satisfied the goal.

        Best-effort: never raises.
        """
        try:
            commands = extract_verified_commands(stream_text)
        except Exception as exc:  # noqa: BLE001 — evidence parse must not crash the loop
            logger.warning("command-evidence parse failed: %s", exc)
            return diff_text
        if not commands:
            return diff_text
        block = _format_command_evidence_block(list(commands))
        logger.info(
            "command-evidence: credited %d verified git/GitHub command(s): %s",
            len(commands), [c[0][:60] for c in commands],
        )
        if diff_text and diff_text.strip():
            return diff_text.rstrip("\n") + "\n" + block
        return block

    def _augment_diff_with_desktop_action_evidence(
        self, diff_text: str, stream_text: str
    ) -> str:
        """Append verified desktop/process-launch commands to the captured diff.

        ``_capture_diff`` is worktree-scoped and a desktop-launch task
        ("open Explorer", "launch Chrome", "start the calculator") produces NO
        worktree file change — the deliverable is a running process. The
        Critic's GROUND-TRUTH-RULE then fails the empty diff 3×
        → ``critic_loop_exhausted`` even though the launch succeeded (a
        false-negative for app-open missions). This helper mirrors
        :meth:`_augment_diff_with_command_evidence` for the desktop-launch
        case: for every recognised launch command the worker ran with a real,
        NON-ERRORED result (parsed by :func:`extract_verified_desktop_actions`),
        it appends a ``diff --desktop-action-evidence`` block carrying the
        command + its real subprocess output (or the silent-spawn sentinel).

        Anti-hallucination is preserved: read-only commands never match, and a
        command with no correlated/errored result is never credited — so a bare
        "I opened Explorer" claim still falls through to the empty-diff veto.
        The Critic remains the final judge of whether the launch satisfied the
        goal.

        Best-effort: never raises.
        """
        try:
            actions = extract_verified_desktop_actions(stream_text)
        except Exception as exc:  # noqa: BLE001 — evidence parse must not crash the loop
            logger.warning("desktop-action-evidence parse failed: %s", exc)
            return diff_text
        if not actions:
            return diff_text
        block = _format_desktop_action_evidence_block(list(actions))
        logger.info(
            "desktop-action-evidence: credited %d verified launch command(s): %s",
            len(actions), [a[0][:60] for a in actions],
        )
        if diff_text and diff_text.strip():
            return diff_text.rstrip("\n") + "\n" + block
        return block

    def _archive_task_artifacts(
        self,
        *,
        worktree: Path,
        mission_dir: Path,
        task_id: str,
        drain_iteration_diffs: bool = True,
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
        - ``files/<rel>`` — a synchronized snapshot of the latest deliverable
          files. Earlier iteration content remains recoverable from the
          ``diff.iter<N>.patch`` history, but a deleted or renamed draft must
          never survive here as if it were the final output.

        ``drain_iteration_diffs=False`` creates a crash-safe draft snapshot
        while retaining the in-memory per-iteration history for later critic
        rounds. The final cleanup call uses the default ``True`` and releases
        that history. All git operations are best-effort with a 10s cap.
        Returns the artifacts directory on success, ``None`` on irrecoverable
        failure (the upstream finally still runs the worktree cleanup either
        way).
        """
        try:
            artifacts = mission_dir / "tasks" / task_id[:13] / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)

            # Read or drain per-iteration captures. Draft-time snapshots retain
            # the list so later rounds and the final cleanup can still choose
            # the best diff; final archival pops it to prevent process-lifetime
            # growth. `getattr` keeps bare unit-test fixtures functional.
            iter_map = getattr(self, "_task_iter_diffs", None)
            if iter_map is None:
                per_iter: list[tuple[int, str]] = []
            elif drain_iteration_diffs:
                per_iter = iter_map.pop(task_id, [])
            else:
                per_iter = list(iter_map.get(task_id, []))
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

            # THIRD enumeration — files the worker COMMITTED (2026-07-03, mission
            # 019f26d0-bb07). A committed deliverable is tracked, so neither
            # `ls-files --others` above nor the staged `git diff HEAD` sees it,
            # and it is lost when the worktree is pruned. When a base commit was
            # recorded (`read_worktree_base_sha`), enumerate every file
            # added/copied/modified/renamed between the base and the current HEAD
            # and union them in — they exist on disk in the worktree (a commit
            # does not remove the working-tree file) and are copied verbatim
            # below like any other deliverable. `--diff-filter=ACMR` excludes
            # deletions (D) so a removed file is never resurrected as an empty
            # copy. Best-effort: no base / a git hiccup just falls back to the
            # untracked-only behaviour.
            archive_base = read_worktree_base_sha(worktree)
            if archive_base:
                r_committed = subprocess.run(  # noqa: S603
                    ["git", "-c", "core.quotepath=false", "diff",
                     "--name-only", "--diff-filter=ACMR", archive_base, "HEAD"],
                    cwd=str(worktree),
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10.0,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                )
                for ln in (r_committed.stdout or "").splitlines():
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
                # Diff against the recorded base (fork-point) when present so the
                # fallback diff.patch includes files the worker committed; else
                # HEAD (unchanged pre-fix behaviour).
                ["git", "-c", "core.quotepath=false", "diff", "--cached",
                 archive_base or "HEAD"],
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
            current_diff = _strip_managed_persona_hunks(r_diff.stdout or "")
            final_diff = (
                best_diff
                if best_diff is not None
                else current_diff
            )
            # best_diff is already stripped (it comes from _capture_diff); the
            # r_diff fallback is not — strip managed contract files either way
            # so the archived diff.patch never shows AGENTS.md etc.
            final_diff = _strip_managed_persona_hunks(final_diff)
            (artifacts / "diff.patch").write_text(
                final_diff, encoding="utf-8"
            )
            # Recover current new-file paths the ``git ls-files --others`` call
            # missed because earlier ``_capture_diff`` invocations had
            # already run ``git add -A`` (live 2026-05-27 regression
            # mission_019e6858-ab9a: SUCCESS but artifacts/files/ empty).
            # Use the fresh worktree diff, not the largest historical diff:
            # the latter may name a first draft that a later worker deleted or
            # renamed. Per-iteration patches preserve that history separately.
            for _np in _extract_new_file_paths_from_diff(current_diff):
                if _np not in untracked:
                    untracked.append(_np)
            # Drop managed worker-contract files (AGENTS.md etc.) and
            # build/state/junk dirs — the `--ignored` union widens what we
            # enumerate, so this filter keeps artifacts/files/ to genuine
            # deliverables (no Outputs-UI garbage, the Wave-3 invariant).
            untracked = [rel for rel in untracked if _is_deliverable_path(rel)]
            # Drop generator/build scripts whose only purpose is to emit a
            # sibling DOCUMENT deliverable that survives in the set (e.g. a
            # generate_guide.py that writes city_guide.html as an embedded
            # literal). The script is process scratch the user did not ask for —
            # live forensic 2026-06-22 (mission_019ef099): a "make me one HTML
            # file" mission shipped the HTML PLUS its Python generator, which the
            # user opened and saw "only code". Safe by construction: the emitted
            # document is never script-typed, so it always survives.
            if untracked:
                _generators = find_generator_scripts(
                    untracked, lambda rel: _safe_read_text(worktree / rel)
                )
                if _generators:
                    untracked = [r for r in untracked if r not in _generators]
            files_root = artifacts / "files"
            # ``files`` is the canonical latest snapshot. Rebuild it on every
            # draft/final archive so deleted and renamed first drafts cannot be
            # mistaken for the current deliverable. Historical bytes remain in
            # the per-iteration patches written above. Build the entire new
            # tree beside the canonical one before promotion: a copy failure
            # must leave the last durable snapshot untouched.
            staged_root = Path(
                tempfile.mkdtemp(prefix=".files-next-", dir=str(artifacts))
            )
            previous_root = staged_root.with_name(
                staged_root.name.replace(".files-next-", ".files-previous-", 1)
            )
            try:
                for rel in untracked:
                    src = worktree / rel
                    if not src.is_file():
                        # Skip directories, broken symlinks etc. — only
                        # regular files round-trip cleanly via copy2.
                        continue
                    dst = staged_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

                # Path.replace is a same-volume rename because both trees are
                # siblings. Keep the prior directory under a unique backup
                # name until the new tree is canonical; if promotion fails,
                # roll it back. This portable two-phase swap works on Windows,
                # macOS, and Linux without relying on platform-specific rename
                # exchange flags.
                moved_previous = False
                if files_root.exists():
                    files_root.replace(previous_root)
                    moved_previous = True
                try:
                    staged_root.replace(files_root)
                except OSError:
                    if (
                        moved_previous
                        and previous_root.exists()
                        and not files_root.exists()
                    ):
                        previous_root.replace(files_root)
                    raise
                if moved_previous:
                    shutil.rmtree(previous_root, ignore_errors=True)
            finally:
                # No-op after successful promotion. On a copy/promotion error,
                # remove only the unpublished staging tree; never the prior
                # canonical snapshot (or its rollback copy).
                if staged_root.exists():
                    shutil.rmtree(staged_root, ignore_errors=True)
            return artifacts
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning(
                "artifact archive failed in %s: %s", worktree, exc
            )
            return None

    def _read_stream_log(self, log_dir: Path) -> str:
        """Reads stream.jsonl as text for the critic log summarizer."""
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
    ) -> bool:
        """Transition under the mission lock and report whether it committed."""
        lock = self._state_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            try:
                await self._manager.transition_state(
                    mission_id, to_state, reason=reason, source_actor="kontrollierer"
                )
                return True
            except IllegalStateTransition:
                # already at the target state or further — not a crash
                logger.debug(
                    "Mission %s: skip transition -> %s (already past)",
                    mission_id,
                    to_state.value,
                )
                return False

    async def _approve_mission(
        self, mission_id: str, plan: MissionPlan, *, prompt: str = ""
    ) -> None:
        # Hygiene: a retried-then-approved mission must not leak a stale
        # worker-failure context into a later run (mirror of _fail_mission).
        self._mission_failure_context.pop(mission_id, None)
        transitioned = await self._safe_transition(
            mission_id,
            MissionState.APPROVED,
            "all_tasks_approved",
        )
        if not transitioned:
            return
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
        # "Always a document": a code/file task already wrote its artifact, but a
        # pure research/Q&A task delivers its answer as TEXT and would leave the
        # Outputs view empty (live forensic 2026-06-19: the same question yielded
        # a report once and an empty card the next time, depending on whether the
        # worker happened to write a file). When no genuine file deliverable
        # exists, materialise the worker's answer as a Markdown report in the
        # canonical deliverable subtree so EVERY successful mission shows a
        # document — and so the mirror + summary below pick it up like any other
        # deliverable. Best-effort: a write hiccup must never fail an APPROVED
        # mission, so it is wrapped (the function itself also never raises).
        try:
            materialize_answer_document(
                mission_dir,
                answers=answers,
                prompt=prompt,
                expected_output=plan.expected_output,
            )
        except Exception:  # noqa: BLE001 — report materialisation is never fatal
            # Defense-in-depth: the function already swallows its own OSError and
            # returns None, so this outer catch is unreachable today — it guards
            # against a future change to the function's error handling ever
            # flipping an APPROVED mission to FAILED.
            logger.warning(
                "materialize_answer_document failed for %s", mission_id,
                exc_info=True,
            )
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
        # Build BOTH language variants so MissionApproved carries a genuinely
        # English summary_en — the announcer selects the field by the mission's
        # DISPATCH language, so a German-only summary_en made an English-dispatched
        # mission read its completion confirmation back in German (forensic
        # 2026-06-24: the deliverable summary was the deterministic German leak;
        # answer_summary already mirrors the request language via the worker).
        # Prefer, in order: a read-only task's spoken answer (already in the
        # request language), the delivered-file summary (names file + folder),
        # the in-archive basename summary, then the generic phrase.
        summary_de = (
            answer_summary
            or build_delivered_summary(delivered, language="de")
            or build_deliverable_summary(mission_dir, language="de")
            or "Mission abgeschlossen."
        )
        summary_en = (
            answer_summary
            or build_delivered_summary(delivered, language="en")
            or build_deliverable_summary(mission_dir, language="en")
            or "Mission completed."
        )
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
        # Consume the classified worker-failure context (if any) so the
        # terminal MissionFailed event names the real cause instead of the
        # bare mission-level reason (2026-07-06 incident: error_class was
        # always None and the UI/voice could not name a dead credential).
        #
        # Attach the recorded worker-failure context ONLY when the terminal
        # reason is actually worker-caused. Any other reason (critic_*,
        # budget_exceeded, worktree_setup_failed, ...) has its own honest
        # cause — inheriting a leftover worker-error context would
        # misattribute the failure (review finding, 2026-07-07). The pop is
        # unconditional either way: terminal means the context is dead.
        failure_ctx = self._mission_failure_context.pop(mission_id, {})
        if reason not in ("task_error", "attempts_timed_out"):
            failure_ctx = {}
        # Only transition when not already terminal
        view = await self._manager.mission(mission_id)
        if view is None or view.state in (
            MissionState.APPROVED,
            MissionState.FAILED,
            MissionState.CANCELLED,
            MissionState.TIMED_OUT,
        ):
            return
        if not await self._safe_transition(mission_id, MissionState.FAILED, reason):
            return
        env = EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason=reason,
                error_class=failure_ctx.get("error_class"),
                error_detail=failure_ctx.get("error_detail"),
                failed_provider=failure_ctx.get("failed_provider"),
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
                diff=diff[:8000],  # Cap protects the event store from huge diffs.
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
        try:
            await self._manager.store.advance_mission_iteration(
                mission_id,
                iteration=iteration,
                ts_ms=now_ms(),
            )
        except Exception:  # noqa: BLE001 — event remains authoritative
            logger.warning(
                "critic verdict header update failed for %s iter-%d",
                mission_id,
                iteration,
                exc_info=True,
            )

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
    """Short kebab-slug for mission naming."""
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
    """True when the step prompt contains security-relevant keywords.

    Triggers critic-tier escalation Sonnet -> Opus even in iter 0.
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
