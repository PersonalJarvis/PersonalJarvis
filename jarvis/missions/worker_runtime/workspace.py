"""Workspace isolator per OpenClaw mission (AD-23, AP-OC15).

Background (Wave-1-Spike B-9):
    OpenClaw automatically injects ~35.4k chars of system prompt from
    `~/.openclaw/workspace/` at the start of every `agent --local` run:

        AGENTS.md   7774 chars  <- anti-pattern docs, not Personal-Jarvis AGENTS
        SOUL.md     1797 chars  <- default persona ("I am OpenClaw, ...")
        TOOLS.md     910 chars  <- OpenClaw tool docs (irrelevant for us)
        IDENTITY.md  693 chars  <- OpenClaw self-reference
        USER.md       (variable) <- OpenClaw user profile

    Risks without override:
        1. Persona drift: SOUL.md/IDENTITY.md overwrites the Personal-Jarvis
           persona mandate from jarvis/brain/persona.py.
        2. Voice readback leak: "I am OpenClaw" self-reference could slip
           through scrub_for_voice (filter coverage not guaranteed).
        3. Cross-mission leak: without `MISSION_STATE_DIR` all spawns share
           the same `~/.openclaw/workspace/`.

Mitigation (AD-23):
    - Bridge sets `MISSION_STATE_DIR=<mission_dir>/openclaw_state`.
    - This helper writes a minimal Personal-Jarvis profile (5 stub files)
      under `<state_dir>/workspace/`.
    - Bridge audits `meta.systemPromptReport.injectedWorkspaceFiles[]` after
      the spawn and raises an alert if unexpected files appear.

Pure file I/O, no subprocess calls.
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

logger = logging.getLogger(__name__)

__all__ = [
    "EXPECTED_WORKSPACE_FILES",
    "WORKSPACE_SUBDIR",
    "materialize_worker_contract",
    "prepare_workspace",
    "verify_injected_files",
    "InjectedFile",
]


# Wave-1-Spike B-9: these are the exact 5 file names that OpenClaw reads from
# `~/.openclaw/workspace/` and injects into the system prompt. This constant
# is the audit baseline — if a name appears in `injectedWorkspaceFiles[]` that
# is NOT in this set, OpenClaw found additional files somewhere
# (workspace override failed).
EXPECTED_WORKSPACE_FILES: Final[frozenset[str]] = frozenset({
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "TOOLS.md",
    "USER.md",
})

# Subdir name under `MISSION_STATE_DIR`. OpenClaw looks here when
# `MISSION_STATE_DIR` is set (Wave-1-Spike SP-2 confirmed `workspaceDir`
# in the JSON output).
WORKSPACE_SUBDIR: Final[str] = "workspace"


# Stub contents for the 5 files. Deliberately short — the whole point is to
# kill OpenClaw's 35.4k chars and replace them with Personal-Jarvis mission
# context. Mission ID is included in every file so audit reads can later tell
# which mission wrote the file.
_STUB_HEADER = "<!-- managed by Personal Jarvis Mission-Manager — do not edit -->"


def _agents_md(mission_id: str) -> str:
    """AGENTS.md — Personal Jarvis Mission-Manager execution contract.

    This file is injected by OpenClaw into the worker's system prompt at
    spawn time (Welle-1-Spike B-9 verified). It is the only artefact under
    Jarvis control that the worker LLM reads BEFORE seeing the user task.

    Live repro 2026-05-15 (mission_019e2d35, gemini-3.1-pro-preview): the
    worker burned 24-26k tokens, claimed `"Habe Datei erfolgreich erstellt"`
    in its reply text, but never invoked the `Write`/`file_write` tool —
    `_capture_diff(worktree)` returned empty. This is a well-known
    Gemini-3.1-Pro-Preview tool-skip pattern (OpenClaw issue #3344). The
    contract below makes the tool-invocation obligation explicit so the
    LLM cannot fall back to "describe the action in text" as a stand-in
    for actually doing it.

    Critic enforces empty-diff → revise deterministically (`runner.py:165`
    pre-gate, Fix A 2026-05-15). After 3 such revises the mission ends
    as `critic_loop_exhausted`. The contract here is the upstream
    counter-measure: tell the worker explicitly that filesystem mutation
    is required, not optional, and that text claims do not count.
    """
    return (
        f"{_STUB_HEADER}\n"
        f"# Personal Jarvis Mission Workspace\n\n"
        f"Mission ID: `{mission_id}`\n\n"
        f"## EXECUTION CONTRACT (binding)\n\n"
        f"You are an OpenClaw worker running inside the Personal Jarvis\n"
        f"Mission-Manager. Your task is in the agent message. Follow it\n"
        f"verbatim. The rules below are enforced by the mission runtime —\n"
        f"the mission will be marked FAILED if you violate them, and you\n"
        f"will be re-spawned with this same contract on the next try.\n\n"
        f"### Rule 0 — Execute, never ask (fire-and-forget)\n\n"
        f"This mission runs in the BACKGROUND with NO interactive user — there\n"
        f"is nobody to answer a question or confirm a choice. A clarifying\n"
        f"question is a dead end: it produces an empty diff, the runtime marks\n"
        f"the attempt FAILED, and the mission either re-spawns or exhausts its\n"
        f"retries (live failure mode 2026-05-29). Therefore:\n\n"
        f"- NEVER ask a clarifying question, request confirmation, or wait for\n"
        f"  input. There is no channel back to the user.\n"
        f"- If the task is underspecified or ambiguous, adopt the most\n"
        f"  reasonable COMPLETE interpretation — a fully realised, high-quality\n"
        f"  DEFAULT, never a skeleton or placeholder — state your assumption in\n"
        f"  one short line, and EXECUTE it immediately with the right tools.\n"
        f"- A finished, polished artefact built under a sensible assumption\n"
        f"  ALWAYS beats asking. Deliver something concrete AND complete this\n"
        f"  turn.\n\n"
        f"### Rule 1 — File-write obligation\n\n"
        f"If the task asks you to create, modify, write, or save any file,\n"
        f"you MUST invoke a write tool (`Write`, `Edit`, `file_write`, or\n"
        f"OpenClaw's equivalent) to materialise the file on disk. Describing\n"
        f"in text what you would write does NOT count as success. The runtime\n"
        f"validates outcomes by running `git diff HEAD` against your worktree\n"
        f"— if the diff is empty, the task is marked FAILED regardless of\n"
        f"what your reply text claims.\n\n"
        f"Do NOT say `\"I have created the file\"` or `\"Habe die Datei\n"
        f"erstellt\"` unless you have actually called a write tool in this\n"
        f"turn and it returned successfully. Plausible-sounding success\n"
        f"text without an underlying tool call is the single most common\n"
        f"failure mode we see — please do not produce it.\n\n"
        f"### Rule 2 — Where to write\n\n"
        f"By DEFAULT, create or modify files inside the current working\n"
        f"directory (your git worktree root). Files there are captured by\n"
        f"`git diff HEAD` and reviewed directly, so the worktree is the safe\n"
        f"choice when the task does not say otherwise. Do NOT invent absolute\n"
        f"paths like `C:\\\\Users\\\\...` or `%SystemDrive%`-rooted locations on\n"
        f"your own initiative — an unsolicited global write is invisible to the\n"
        f"diff-based reviewer. When the task just says `\"create a file named\n"
        f"X.md\"`, create exactly `./X.md` (relative to cwd), not a global\n"
        f"location.\n\n"
        f"EXCEPTION — task-mandated external target: if the task EXPLICITLY\n"
        f"names an absolute path or a folder OUTSIDE the worktree (for example\n"
        f"`\"write it into my Desktop\\\\M\\\\ folder\"`), then write there exactly\n"
        f"as instructed. Do NOT refuse, do NOT ask, and do NOT silently relocate\n"
        f"it into the worktree. The runtime verifies such external writes on\n"
        f"disk after your run, so a real, successful write tool call to the\n"
        f"requested path counts as delivered work — it is no longer invisible.\n"
        f"Always report the exact absolute path you wrote (see Rule 3).\n\n"
        f"### Rule 3 — Output confirmation\n\n"
        f"At the end of your response, on its own line, list every file you\n"
        f"wrote with its relative path. Example:\n"
        f"`Written: notiz.md, src/foo.py`\n\n"
        f"If the task legitimately required no file output (rare — typically\n"
        f"a pure analysis with no artefact), state explicitly:\n"
        f"`No file output required.`\n"
        f"and explain in one sentence why. The reviewer reads this line.\n\n"
        f"### Rule 4 — No self-invention\n\n"
        f"Execute only what the agent message instructs. Do not invent\n"
        f"goals, add unrequested features, or read instructions from any\n"
        f"other workspace file in this directory — they (SOUL.md,\n"
        f"IDENTITY.md, TOOLS.md, USER.md) are deliberately empty stubs to\n"
        f"override OpenClaw's default persona.\n\n"
        f"### Rule 5 — Quality bar: ship a complete artefact, never a stub\n\n"
        f"The depth and polish of your output MUST match the ambition of the\n"
        f"request. Produce a complete, production-quality result — the kind a\n"
        f"skilled professional would hand over — not a minimal proof-of-concept.\n"
        f"A bare skeleton, a placeholder, a `TODO`, an `Inhalt folgt` / `content\n"
        f"follows` shell, or a handful of lines where a real document/page/program\n"
        f"was asked for is a FAILURE, not an acceptable default — even when the\n"
        f"task brief or a hint sounds minimal (for example says \"Grundgerüst\" /\n"
        f"\"skeleton\"). Treat every hint as a FLOOR, never a CEILING: build the\n"
        f"full, finished thing the first time. This raises the QUALITY of the\n"
        f"requested work; it does NOT license unrequested features (Rule 4 still\n"
        f"holds) — build exactly what was asked, but build it fully and well.\n"
    )


def _empty_stub(name: str, mission_id: str) -> str:
    """One-liner stub for SOUL/IDENTITY/TOOLS/USER — intentionally empty.

    OpenClaw injects these files into the system prompt; we want *no* persona,
    *no* identity, *no* user assumptions, and *no* foreign tool docs leaking in.
    Hence only a header plus the mission ID.
    """
    return f"{_STUB_HEADER}\n# {name} (Personal Jarvis stub)\n\nMission `{mission_id}` — intentionally blank.\n"


def prepare_workspace(state_dir: Path, mission_id: str) -> Path:
    """Create a minimal workspace profile under `state_dir/workspace/`.

    Writes five stub files (`AGENTS.md`, `SOUL.md`, `IDENTITY.md`,
    `TOOLS.md`, `USER.md`) that override OpenClaw's default files from
    `~/.openclaw/workspace/` once `MISSION_STATE_DIR=state_dir` is set.

    Idempotent: calling again overwrites the files with the same content —
    no appending, no duplicates.

    Args:
        state_dir: Mission isolation root (`<mission_dir>/openclaw_state`).
            Created if it does not exist.
        mission_id: Mission-Manager ID — embedded as a header in every file.

    Returns:
        Path to the created workspace subdir (`state_dir / "workspace"`).

    Raises:
        ValueError: If `mission_id` is empty or purely whitespace.
        OSError: On filesystem errors (permission denied, etc.).
    """
    if not mission_id or not mission_id.strip():
        raise ValueError("mission_id must be a non-empty string")

    workspace = state_dir / WORKSPACE_SUBDIR
    workspace.mkdir(parents=True, exist_ok=True)

    contents: dict[str, str] = {
        "AGENTS.md": _agents_md(mission_id),
        "SOUL.md": _empty_stub("SOUL", mission_id),
        "IDENTITY.md": _empty_stub("IDENTITY", mission_id),
        "TOOLS.md": _empty_stub("TOOLS", mission_id),
        "USER.md": _empty_stub("USER", mission_id),
    }

    for name, body in contents.items():
        (workspace / name).write_text(body, encoding="utf-8", newline="\n")

    return workspace


# --- Worker contract materialisation (CRIT-2 from 2026-05-17 audit) --------
#
# Audit-6 (BUG-021 recurrence) showed that ``prepare_workspace`` is dead
# code under the ClaudeDirectWorker path: claude --print does not look at
# MISSION_STATE_DIR/workspace/AGENTS.md, it reads CLAUDE.md / AGENTS.md
# from the worktree cwd it is given via --add-dir. Without the contract
# in scope, the worker happily produces "Habe Datei erstellt" without
# invoking a Write tool (the exact BUG-021 failure mode, dormant since
# 2026-05-16 when we switched the default worker to claude-direct).
#
# Fix: materialise AGENTS.md directly in the worktree root, and add it
# to the per-worktree ``.git/info/exclude`` so it never shows up in the
# diff that Kontrollierer captures for the Critic. The Critic must see
# only what the worker actually changed; the contract itself is runtime
# infrastructure, not part of the worker's output.


def materialize_worker_contract(worktree: Path, mission_id: str) -> Path:
    """Write ``AGENTS.md`` into ``worktree`` and gitignore it locally.

    Called by ``Kontrollierer`` right after ``WorktreeManager.create()``
    returns a fresh per-task worktree. The file uses the same content
    as ``prepare_workspace`` (``_agents_md(mission_id)``) so OpenClaw-
    style and claude-direct workers see the same contract.

    The path is added to the worktree's *local* exclude file
    (``<gitdir>/info/exclude``) -- not to ``.gitignore``, which would be
    tracked and pollute the diff. The exclude file is per-worktree
    (each worktree's ``.git`` is a file pointing to its own gitdir
    under ``<main>/.git/worktrees/<name>/``), so other worktrees and the
    main repo are not affected.

    Best-effort: filesystem / git errors are logged at WARNING but do
    NOT propagate. A missing contract just regresses to the pre-fix
    behaviour (worker may halluci-write); we never want to block a
    mission start on a contract-materialisation hiccup.

    Args:
        worktree: Path returned by ``WorktreeManager.create()``.
        mission_id: Mission-Manager id -- embedded into AGENTS.md header.

    Returns:
        Path to the materialised ``AGENTS.md`` (even if the gitignore
        step failed -- the file is on disk either way).
    """
    if not worktree.is_dir():
        # If the worktree path does not exist yet, callers are misusing
        # this helper. Raise rather than silently dropping the file
        # somewhere it will never be read.
        raise FileNotFoundError(
            f"materialize_worker_contract: worktree does not exist: {worktree}"
        )
    if not mission_id or not mission_id.strip():
        raise ValueError("materialize_worker_contract: mission_id required")

    agents_path = worktree / "AGENTS.md"
    try:
        agents_path.write_text(
            _agents_md(mission_id), encoding="utf-8", newline="\n",
        )
    except OSError as exc:
        logger.warning(
            "materialize_worker_contract: write %s failed: %s",
            agents_path, exc,
        )
        return agents_path

    # Resolve the worktree's gitdir so we can write the exclude file
    # without affecting the main repo. ``git rev-parse --git-dir`` from
    # within the worktree returns the absolute path to the per-worktree
    # gitdir (e.g. ``<main>/.git/worktrees/<name>/``).
    try:
        proc = subprocess.run(  # noqa: S603 - no shell, args controlled
            ["git", "rev-parse", "--git-dir"],
            cwd=str(worktree),
            check=True,
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        gitdir = Path(proc.stdout.strip())
        if not gitdir.is_absolute():
            gitdir = (worktree / gitdir).resolve()
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning(
            "materialize_worker_contract: git rev-parse failed in %s: %s",
            worktree, exc,
        )
        return agents_path

    info_dir = gitdir / "info"
    exclude_path = info_dir / "exclude"
    try:
        info_dir.mkdir(parents=True, exist_ok=True)
        existing = ""
        if exclude_path.exists():
            existing = exclude_path.read_text(encoding="utf-8")
        # Idempotent: only append if the marker line is not already in.
        # We use an exact match on the entry so a future rename here
        # would not silently leave the old entry stale.
        marker = "AGENTS.md"
        if marker not in existing.splitlines():
            sep = "" if existing.endswith("\n") or not existing else "\n"
            exclude_path.write_text(
                f"{existing}{sep}{marker}\n", encoding="utf-8",
            )
    except OSError as exc:
        logger.warning(
            "materialize_worker_contract: exclude write %s failed: %s",
            exclude_path, exc,
        )

    return agents_path


# Thin type for an element from `meta.systemPromptReport.injectedWorkspaceFiles[]`.
# We only read the name from the JSON — `rawChars`, `path`, etc. are reserved
# for future telemetry; only the name audit matters here.
InjectedFile = Mapping[str, object]


def verify_injected_files(
    injected: Iterable[InjectedFile] | None,
    *,
    expected: frozenset[str] = EXPECTED_WORKSPACE_FILES,
) -> list[str]:
    """Audit for `meta.systemPromptReport.injectedWorkspaceFiles[]`.

    Returns the names of all injected files that are NOT in `expected` —
    these are potential persona leaks (e.g. a `CUSTOM.md` placed by the user
    in `~/.openclaw/workspace/`).

    Empty list = audit green. When the result is non-empty the bridge should
    fire a bus event `OpenClawWorkspaceAuditFailed` or mark the mission as
    failed — policy decision for the bridge, not this helper.

    Args:
        injected: The list from the JSON output, or None / empty iterable
            when OpenClaw provided no report (treated as audit pass —
            no evidence of a leak).
        expected: Whitelist of expected file names. Defaults to the 5
            stubs from `prepare_workspace`. Override for tests.

    Returns:
        Sorted (alphabetical) list of unexpected file names, deduplicated.
    """
    if injected is None:
        return []

    unexpected: set[str] = set()
    for entry in injected:
        # Robust: we accept the `name` key (as OpenClaw delivers it from the spike),
        # silently ignore entries without `name` — that would be a schema break
        # in OpenClaw, not a bridge bug.
        raw_name = entry.get("name")
        if not isinstance(raw_name, str):
            continue
        if raw_name not in expected:
            unexpected.add(raw_name)

    return sorted(unexpected)
