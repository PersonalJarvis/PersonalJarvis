"""Permission modes per agent-chat runner.

Every runner has its own idea of "how much may the agent do without asking":
Claude Code has ``--permission-mode`` (default / acceptEdits / plan /
bypassPermissions / dontAsk), the Codex CLI pairs a sandbox with an approval
policy (the TUI's Read-only / Auto / Full-access presets, plus headless
``--approve-for-me`` where its own reviewer model answers the prompts),
Antigravity's ``agy`` knows accept-edits, plan and a skip-permissions
switch, and the in-process API runner gates its own tools. The composer
shows exactly the ladder of the runner in use — the same words the vendor's
own CLI uses, so a person who knows Claude Code's "Auto-accept edits" finds
it here — and
:func:`normalize_permission` folds a mode onto the nearest one another runner
offers when a session moves between providers.

``plan`` is special: every ladder that has it maps it onto "read and plan,
change nothing" (Claude Code's plan mode; a read-only sandbox on Codex; the
read-only tool set on the API runner). The composer draws it as the
Build | Plan switch next to the permission pill.

Single source of truth: the frontend receives the ladders through
``GET /api/agent-chat/catalog`` and never mirrors them (AP-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PermissionMode:
    id: str
    label: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "description": self.description}


# ----------------------------------------------------------------- ladders

_CLAUDE: Final[tuple[PermissionMode, ...]] = (
    PermissionMode(
        "default",
        "Ask before acting",
        "Claude Code's default: reads run freely; edits and commands show an "
        "approval card here in the chat before they run.",
    ),
    PermissionMode(
        "acceptEdits",
        "Auto-accept edits",
        "Edits and file writes in the working folder run without asking; "
        "shell commands still ask (an approval card here in the chat).",
    ),
    PermissionMode(
        "plan",
        "Plan",
        "Read and plan only — nothing is changed; the finished plan asks before building starts.",
    ),
    PermissionMode(
        "auto",
        "Auto (classifier)",
        "Claude Code's own classifier approves routine actions and asks only "
        "for the risky ones (approval card here in the chat).",
    ),
    PermissionMode(
        "dontAsk",
        "Don't ask",
        "Never prompt: anything that would need a prompt is denied outright.",
    ),
    PermissionMode(
        "bypassPermissions",
        "Bypass permissions",
        "Everything runs without asking — edits, commands, the lot. Use on a "
        "folder you can afford to lose.",
    ),
)

_CODEX: Final[tuple[PermissionMode, ...]] = (
    PermissionMode(
        "read-only",
        "Read only",
        "The sandbox lets the agent read; edits and commands that write are "
        "blocked (Codex `--sandbox read-only`).",
    ),
    PermissionMode(
        "approve-for-me",
        "Ask (auto-review)",
        "Codex's own reviewer model approves or declines what would have "
        "asked you — the one ask-stance a headless run has "
        "(`--approve-for-me`); the sandbox stays workspace-write.",
    ),
    PermissionMode(
        "auto",
        "Auto",
        "Edits and commands inside the working folder run on their own; "
        "anything outside it or on the network is blocked "
        "(`--sandbox workspace-write`).",
    ),
    PermissionMode(
        "plan",
        "Plan",
        "Read and plan only — a read-only sandbox; nothing is changed.",
    ),
    PermissionMode(
        "full-access",
        "Full access",
        "No sandbox, no approvals — the agent may touch anything on this "
        "machine (`--dangerously-bypass-approvals-and-sandbox`).",
    ),
)

_AGY: Final[tuple[PermissionMode, ...]] = (
    PermissionMode(
        "accept-edits",
        "Auto-accept edits",
        "Edits run on their own; commands that need a permission are declined "
        "and reported to the model.",
    ),
    PermissionMode(
        "plan",
        "Plan",
        "Read and plan only — nothing is changed.",
    ),
    PermissionMode(
        "skip-permissions",
        "Skip permissions",
        "Everything runs without asking (`--dangerously-skip-permissions`).",
    ),
)

_GROK: Final[tuple[PermissionMode, ...]] = (
    PermissionMode(
        "default",
        "Ask before acting",
        "Grok Build's default: edits and commands would ask; in the chat the "
        "CLI cannot ask back, so they are declined and reported.",
    ),
    PermissionMode(
        "acceptEdits",
        "Auto-accept edits",
        "Edits in the working folder run without asking.",
    ),
    PermissionMode(
        "plan",
        "Plan",
        "Read and plan only — nothing is changed.",
    ),
    PermissionMode(
        "bypassPermissions",
        "Bypass permissions",
        "Everything runs without asking.",
    ),
)

_API: Final[tuple[PermissionMode, ...]] = (
    PermissionMode(
        "ask",
        "Ask before acting",
        "Reads run freely; every edit, write and shell command shows an "
        "approval card here in the chat before it runs.",
    ),
    PermissionMode(
        "accept-edits",
        "Auto-accept edits",
        "Edits and writes in the working folder run on their own; shell commands still ask.",
    ),
    PermissionMode(
        "plan",
        "Plan",
        "Read and plan only — the agent gets no editing or shell tools.",
    ),
    PermissionMode(
        "auto",
        "Full access",
        "Everything runs without asking.",
    ),
)

# runner -> (ladder, default)
#
# ``brain`` has NO ladder, and that is the honest answer rather than a missing
# one: Jarvis' own risk tiers decide what a tool may do (safe / monitor / ask /
# block, ``ToolExecutor``), a consequential action asks back inside the answer,
# and none of that is a per-chat dial. A picker offering vendor permission
# modes there would be a control wired to nothing (AP-31). The composer hides
# the pick — and the Build | Plan switch with it — when the ladder is empty.
_LADDERS: Final[dict[str, tuple[tuple[PermissionMode, ...], str]]] = {
    "brain": ((), ""),
    "claude-cli": (_CLAUDE, "acceptEdits"),
    "codex-cli": (_CODEX, "auto"),
    "agy-cli": (_AGY, "accept-edits"),
    "grok-cli": (_GROK, "acceptEdits"),
    "api": (_API, "ask"),
}

# The universal stance ordering: every ladder is a sub-sequence of this once
# its vendor words are translated, so a mode can be folded across runners.
_STANCE: Final[dict[str, int]] = {
    # 0 = reads only, 1 = ask first, 2 = edits yes / commands ask, 3 = all.
    "plan": 0,
    "read-only": 0,
    "ask": 1,
    "default": 1,
    "dontAsk": 1,
    "approve-for-me": 1,
    "accept-edits": 2,
    "acceptEdits": 2,
    "auto_codex": 2,
    "auto_claude": 2,
    "auto": 3,
    "full-access": 3,
    "bypassPermissions": 3,
    "skip-permissions": 3,
}


def _stance(runner: str, mode: str) -> int:
    # Codex's "auto" is the workspace-write sandbox (edits yes, not everything);
    # Claude Code's "auto" is its classifier (routine yes, risky asks).
    if runner == "codex-cli" and mode == "auto":
        return _STANCE["auto_codex"]
    if runner == "claude-cli" and mode == "auto":
        return _STANCE["auto_claude"]
    return _STANCE.get(mode, 1)


def permission_modes(runner: str) -> tuple[PermissionMode, ...]:
    """The ladder the composer offers for ``runner``."""
    return _LADDERS.get(runner, _LADDERS["api"])[0]


def default_permission(runner: str) -> str:
    return _LADDERS.get(runner, _LADDERS["api"])[1]


def permission_ids(runner: str) -> tuple[str, ...]:
    return tuple(m.id for m in permission_modes(runner))


def is_permission_mode(runner: str, mode: str) -> bool:
    return mode in permission_ids(runner)


def normalize_permission(runner: str, mode: str | None) -> str:
    """Fold ``mode`` onto the closest mode ``runner`` accepts.

    A mode the runner offers passes through. ``plan`` passes through wherever
    a ladder has it. Anything else (a mode from another runner's ladder, or
    the legacy ``ask``/``auto`` pair) snaps to the mode of the same stance,
    else the nearest LOWER stance — moving to a runner that cannot express
    "edits yes, commands ask" must not silently grant everything.
    """
    picked = (mode or "").strip()
    ids = permission_ids(runner)
    if not picked:
        return default_permission(runner)
    if picked in ids:
        return picked
    # Legacy ask/auto from before the ladders (store rows written by the
    # first draft of this package).
    want = _stance(runner, picked) if picked in _STANCE else 1
    if picked == "auto":
        want = 3
    # Plan / read-only are only ever the target when that is what was asked
    # for: folding "ask" onto a read-only sandbox would silently take the
    # hands away.
    pool = [m for m in ids if want == 0 or _stance(runner, m) > 0]
    candidates = sorted(
        (abs(_stance(runner, m) - want), _stance(runner, m) > want, i, m)
        for i, m in enumerate(pool)
    )
    return candidates[0][3] if candidates else default_permission(runner)


__all__ = [
    "PermissionMode",
    "default_permission",
    "is_permission_mode",
    "normalize_permission",
    "permission_ids",
    "permission_modes",
]
