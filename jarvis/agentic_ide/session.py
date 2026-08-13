"""In-process state of the Agentic-IDE workspaces.

One registry holds several *sessions*, one of which is *active*. A session is a
chosen folder plus N named terminals, each running a coding-agent CLI (Claude
Code / Codex) in a real pseudo-terminal rooted in that folder. The registry is
what makes the feature more than an embedded terminal grid — it is the thing
Jarvis reads from and writes to:

* **reads** — every terminal keeps a sanitized transcript, so "what is Mika
  doing?" is answered from what Mika actually printed, not from a guess,
* **writes** — a prompt can be injected into a terminal from the outside
  (voice, chat, CLI), which is how you talk to an agent without touching the
  keyboard.

Only one workspace is on screen at a time, and ``session`` — the property every
other layer reads — is always that one. Voice, the brain's context, the prompt
composer and the CLI therefore keep asking exactly one question ("the workspace
I am looking at") and never had to learn that there are others.

**A workspace lives until it is closed.** Looking away is not closing: the panes
of a workspace you switched off stay attached to their running agents, which is
the entire point of having more than one. Only ``end`` (and app shutdown) stops
an agent, and every open workspace is visible in the UI's workspace bar — so
nothing runs unwatched in a way the user cannot see. Coming back re-binds the
running PTY instead of restarting it, and replays the pane's raw output so the
screen is the one you left (see ``attach`` and ``ReplayBuffer``).

Security posture of the write path (this is a keystroke channel into a running
process, so it is bounded deliberately):

1. The PTY runs the AGENT, never a persistent shell. When the agent exits the
   PTY dies with it, the terminal flips to ``exited``, and injection is refused
   — so an injected prompt can never fall through into a live shell prompt and
   be executed as a command.
2. Injected text is stripped of every C0 control character. Voice can therefore
   not send Ctrl-C, ESC, or EOF: it cannot kill the agent, break out of its TUI,
   or drive its keyboard shortcuts — only type a prompt and press Enter.
3. Length is capped, and Enter is sent as a separate write a beat later,
   because agent TUIs treat an instant text+newline burst as a paste and insert
   a literal line break instead of submitting.

**A pane is the user's own CLI, not a stripped copy of it.** Whatever the user
gets by typing ``claude`` or ``codex`` in a terminal — their skills, subagents,
slash commands, plugins and connectors, hooks, output styles, global
instructions, default mode — a pane gets too. That is free while the CLI keeps
its own configuration directory, and it is NOT free for a pane running on an
added subscription, because switching accounts works by redirecting exactly that
directory (see ``_spawn_env`` and :mod:`jarvis.agent_config_parity`). Anything
this module opens must close that gap rather than ship a second, quieter version
of the CLI the user installed.

Platform notes: the PTY layer itself is already cross-platform behind
``jarvis.terminal.backend`` (ConPTY on Windows, ptyprocess on POSIX, a clearly
messaged no-op where no PTY exists). What this module adds per platform is
resolving the agent binary: npm installs it as a ``.cmd``/``.ps1`` shim on
Windows. Codex is launched through absolute ``node.exe`` + ``codex.js`` paths
there: ``cmd.exe`` drops inherited environment variables longer than 8,191
characters, so an npm batch shim cannot find Node when the app has a large
PATH. Other batch shims use a one-shot ``cmd /c`` (so rule 1 above still holds).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

from jarvis.workspace import agents as workspace_agents

from . import layout_tree, prompt_history, recap_engine, resume_store
from .activity import NO_READING, Reading, has_work_behind_it, observed
from .agent_sessions import (
    ResumeHandle,
    can_resume,
    discover,
    has_conversation,
    launch_extra,
    resume_argv,
)
from .folders import ProjectProfile, probe_project
from .names import free_positions, normalize, position_of, resolve
from .terminal_input import (
    THEME_COLOURS,
    TerminalQueryResponder,
    classify_terminal_input,
    is_pointer_noise_only,
)
from .transcript import ReplayBuffer, Transcript
from .workspace_view import (
    VIEW_CHAT,
    VIEW_GRID,
    coerce_view,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.agent_accounts import AgentAccount
    from jarvis.terminal.pty_manager import PtyManager

# The coding CLIs a pane can run, and the binary each one is. This table is what
# "an agent" means to the rest of this module: an account can be pinned to it, a
# conversation can be resumed in it, and a prompt can be typed into it.
#
# argv is built here rather than reused from jarvis.workspace.agents because the
# IDE runs the agent as the PTY's OWN process, not inside a persistent shell.
AGENT_BINARIES: dict[str, str] = {a.name: a.executable for a in workspace_agents.coding_agents()}


def is_coding_agent(agent: str) -> bool:
    """Does ``agent`` run a coding CLI (as opposed to a bare shell)?

    Asks the registry rather than the snapshot above, so a CLI registered after
    this module was imported is not invisible to the one test that decides
    whether a pane may be typed into at all.
    """
    spec = workspace_agents.get_agent(agent)
    return spec is not None and spec.is_coding_agent


def has_accounts(agent: str) -> bool:
    """Can this CLI hold several subscriptions the app can switch between?

    A DIFFERENT question from :func:`is_coding_agent`, and conflating the two is
    what the single membership test used to do. Every coding CLI can be typed
    into; only some publish a variable that moves their whole identity, and one
    that does not must never be offered an account switcher that would silently
    keep spending the same login.
    """
    from jarvis import agent_accounts

    return agent in agent_accounts.platforms()


# A pane that runs the machine's own shell and nothing else — see `agent_argv`.
# It is NOT in AGENT_BINARIES on purpose: it has no account, no conversation to
# resume, and (deliberately) no prompt injection, and every one of those falls
# out of that single membership test instead of needing a special case.
PLAIN_TERMINAL: str = workspace_agents.PLAIN_TERMINAL

# What each runnable is called on screen. Read from the workspace registry so a
# newly registered CLI is offerable in the IDE without a second table to keep in
# step (jarvis.workspace.agents.register_agent).
AGENT_DISPLAY: dict[str, str] = {a.name: a.display_name for a in workspace_agents.list_agents()}


def agent_display(agent: str) -> str:
    """What ``agent`` is called on screen — the name itself if nothing knows it.

    Asks the registry rather than only the snapshot above, so an entry
    registered after import still gets its proper label.
    """
    spec = workspace_agents.get_agent(agent)
    if spec is not None:
        return spec.display_name
    return AGENT_DISPLAY.get(agent, agent)


def is_runnable(agent: str) -> bool:
    """May a pane run this? Every registered entry, plain terminal included."""
    return workspace_agents.get_agent(agent) is not None


def accepts_prompts(agent: str) -> bool:
    """May Jarvis type into this pane from the outside (prompt bar, voice, CLI)?

    Only into an AGENT. A plain terminal is a live shell prompt, so an injected
    line would not be read by a coding agent — it would be EXECUTED, which turns
    the one keystroke channel this app exposes into arbitrary command execution
    by voice. That is precisely the boundary the module docstring's rule 1 draws,
    and it is why a plain terminal is typed into by hand or not at all.
    """
    return is_coding_agent(agent)


def _unavailable(agent: str) -> str:
    """Why this pane cannot open, said in the terms of what it would have run.

    A missing coding CLI is installable and the message says where; a host with
    no shell at all is not something the user can fix from the CLIs page, and
    pointing them there would send them looking for a product that does not
    exist.
    """
    pretty = agent_display(agent)
    if accepts_prompts(agent):
        return (
            f"{pretty} is not installed or not on this machine's PATH. "
            "Install it from the CLIs page, then try again."
        )
    return f"{pretty} cannot open: this machine has no shell Jarvis can start."


# How many panes one workspace may hold.
#
# Raised from 12 on maintainer directive (2026-07-26): "you can open as many as
# you want". 12 was a product opinion dressed up as a limit, and it was wrong —
# how many agents are useful is the user's call, not this module's.
#
# A number remains, and it is deliberately far above any real use: this is a
# RUNAWAY GUARD, not a product ceiling. Every pane is a real coding-agent
# process with its own memory, CPU and API spend, so a mistyped "500" in the
# count field must not take the machine down before anyone can click away.
# Nobody reaches 100 deliberately; anyone who mistypes their way past it gets a
# sentence instead of a frozen desktop.
MAX_TERMINALS = 100
# How deep a wizard-opened column is filled before the next one is started.
#
# The workspace is exactly one screenful, so its columns share the window's
# width: one row of columns — what this used to be — spends the whole window on
# a single line, and the sixth terminal then left every pane about 410 px wide
# on the maintainer's own display. A pane narrower than its agent's minimum
# grid (see MIN_VIEWER_COLS) is clipped at the tile edge, which is how six panes
# each came to show two thirds of themselves and read as overlapping one
# another (reported 2026-08-11).
#
# Two deep halves the column count and so doubles every pane's width, which is
# the axis the clipping is on. Only the OPENING shape: the user's own splits,
# drags and closes rearrange the workspace freely afterwards, and no count is
# refused or quietly reshaped — thirty columns is theirs to build.
#
# Mirrored by `WIZARD_COLUMN_HEIGHT` in the frontend's layout module, which
# draws the preview. The two must agree or the workspace that opens is not the
# one the wizard showed.
WIZARD_COLUMN_HEIGHT = 2
# How long a pane's call-sign may be. Half the workspace tab's 80, and for a
# different reason: a workspace name is read, a call-sign is SAID — it is how a
# user addresses one agent among several out loud, and it also has to fit in a
# pane header that may be a quarter of a screen wide. Long enough for "Frontend
# rewrite", short enough that it stays a name rather than a description.
MAX_TERMINAL_NAME = 40
# The narrowest geometry the shared PTY may be asked to work in — a CRASH
# GUARD, not an opinion about what a coding CLI deserves.
#
# The rule it serves is the viewer's (see MIN_REAL_COLS in
# ``AgenticTerminal.tsx``): a terminal is exactly as wide as the tile showing
# it, and every character in that tile is visible. The agent is therefore told
# what the tile MEASURES, and this only refuses a measurement that cannot be
# real — a tile mid-layout reports 0, a hidden one reports nothing, and a PTY
# resized to zero columns permanently wrecks the agent's drawing.
#
# It used to be 60x15, and it used to mean something else: the width below
# which both installed CLIs stop rendering a usable frame. Enforcing that here
# did keep the agents alive — measured on 2026-08-09, thirteen panes, where a
# squeezed workspace left one pane printing ONE CHARACTER PER LINE and six
# others silently stuck — but it paid for it by drawing every narrow pane wider
# than the window showing it, which the maintainer read as terminals shoved
# behind one another (2026-08-11). The comfort question moved to where it can
# be answered honestly: the launcher warns from twenty terminals up and opens
# as many as the user confirms.
#
# Below the floor a size is REFUSED rather than clamped — the PTY keeps its
# last real geometry rather than being handed one no window is showing.
MIN_VIEWER_COLS = 10
MIN_VIEWER_ROWS = 4
# Where a pane may land when it is dragged onto another one, in the same two
# axes the grid is built from (columns of stacked panes). "swap" is listed first
# because it is the one a user reaches for most: two panes are the wrong way
# round and nothing else about the arrangement should change. The four sides are
# the same placements the split buttons already express — the difference is that
# these move a pane that exists instead of opening one.
MOVE_POSITIONS = ("swap", "left", "right", "above", "below")
# Transport ceiling for one injected prompt. Raised from 4000 once composed
# prompts became structured briefs that describe the code they point at: at
# 4000 the cap, not the writer, was deciding where a brief ended. Bracketed
# paste delivers the whole block in one write, so length costs nothing here —
# the real limit is the pane's readability, not the channel.
MAX_PROMPT_CHARS = 6000
# There is deliberately no hard limit on open workspaces. Each one carries real
# processes once its panes attach, so the practical ceiling is the machine's
# capacity and remains the user's decision. ``None`` keeps the public state
# field backward-compatible with clients that used to receive an integer cap.
MAX_WORKSPACES: int | None = None
# How long to wait for the pane to SHOW the prompt before pressing Enter, and
# how finely to look. This replaced a fixed 120 ms delay: the wait is not really
# about debouncing, it is about the pane having taken the text at all. A pane
# that is still booting swallows a paste outright (measured on a real Codex
# while its MCP servers were loading), and pressing Enter into that types into
# nothing. Polling returns the moment the text is visible, so a healthy pane is
# faster than the old fixed delay, and a busy one gets the time it needs.
_ARRIVAL_POLL_S = 0.2
_ARRIVAL_WINDOW_S = 3.0

Status = str  # "pending" | "live" | "exited" | "error"

# Verification budget. Measured against a real Claude Code: a plain prompt clears
# the input line within ~0.3 s, but one carrying an @file reference takes over a
# second (the agent reads the file before redrawing). A 1.4 s window reported a
# prompt as failed that had in fact gone through — a false alarm is as bad as a
# silent drop — so the window is generous, polled finely, and returns the moment
# the line is clear (the normal case still costs ~0.3 s).
_SUBMIT_POLL_S = 0.25
_SUBMIT_WINDOW_S = 2.5
# One extra Enter, and only while the text is DEMONSTRABLY still in the box.
# Pressing blindly into an agent that already started is how you accidentally
# confirm one of ITS prompts.
_SUBMIT_RETRY_AFTER_S = 1.0

# Glyphs an agent TUI draws in front of its input line.
_INPUT_MARKERS = ("❯", ">", "›")

# How quickly an agent has to die after a RESUME for the resume itself to be the
# suspect. A healthy agent the user quits normally exits with code 0 and is
# never second-guessed, and a deliberate kill is flagged as such — so this only
# has to be longer than a failing agent takes to fail. That is not instant: a
# coding CLI loads its plugins and hooks BEFORE reporting a missing
# conversation, and running SessionEnd hooks on the way out adds more. The
# first version used 8 s and watched twelve real panes die just past it.
RESUME_FAILED_WINDOW_S = 45.0

# When to look for the session id of a CLI that cannot be told one (Codex,
# OpenCode, Kimi). It writes its session record a beat after launching, so
# asking immediately finds nothing; two attempts cover a slow machine without
# turning into polling.
DISCOVERY_DELAYS_S = (4.0, 12.0)

# When to look AGAIN, counted from the moment the pane's conversation actually
# received its first message.
#
# **The bug this exists for.** Launching one of those CLIs does not create a
# session on disk — the record appears when the conversation first has something
# to record. Measured on this machine: a Codex pane launched at 15:17:44 wrote
# its rollout file at 15:19:32, the instant its first brief was submitted, 106
# seconds after the schedule above had given up for good. Across 338 real Codex
# TUI sessions, 40 % of the files appeared after that window (p90: 402 s), while
# `codex exec` runs — which carry their prompt at launch — landed inside it 98 %
# of the time. So the window was never the problem; measuring it from the wrong
# EVENT was. A pane that lost this race kept `resume = None` for the rest of its
# life, the snapshot stored a pane with no conversation, and the restore brought
# back an empty agent without a word about it. Claude Code never showed it: its
# id is minted at launch (`--session-id`), so it is in the snapshot before the
# CLI has done anything at all.
#
# Hence a second schedule hung off the event that MAKES the session findable —
# a prompt from Jarvis, or a line the user submitted in the pane themselves.
# Short, because by then the CLI is writing; three attempts, because "is writing"
# is not "has flushed".
CONVERSATION_DELAYS_S = (1.5, 5.0, 15.0)

# How long one pane must wait between lookup ROUNDS. Every submit into a pane
# with no handle is a reason to look, and somebody pressing Enter ten times is
# not ten reasons — each round opens up to _MAX_CANDIDATES session files.
LOOKUP_COOLDOWN_S = 15.0

# ---------------------------------------------------------------------------
# How many agent CLIs may be COLD-STARTING at the same moment.
#
# Opening a workspace mounts every pane at once, each pane connects at once, and
# each connection starts a coding CLI — so the grid used to launch all of them
# in the same instant. A coding CLI's start is not cheap: it loads its plugins
# and hooks, and then starts one process per MCP server the user has configured,
# most of them through ``npx``, which resolves a package before it runs one.
# Measured on this install: eleven user-scope servers, roughly two and a half
# processes each. Eight panes therefore meant well over two hundred process
# starts inside a second or two — every core pinned, the machine unresponsive,
# and the app itself too starved to draw the panes it was starting.
#
# The work is the same either way; only its SHAPE changes. Panes past the limit
# wait for a slot, so the same workspace opens as a rolling start that leaves
# the machine usable, and the pane the user is looking at is up immediately
# rather than last-of-eight in a freeze.
#
# A quarter of the cores, at least two: enough parallelism that a small
# workspace (which is most of them) is never held back at all, and a floor that
# keeps a dual-core VPS from serializing completely.
COLD_START_LIMIT = max(2, (os.cpu_count() or 4) // 4)

# How long a started pane keeps its slot. The expensive part happens AFTER the
# process exists — the CLI is loading while ``spawn`` has long returned — so
# releasing the slot on spawn would let the whole grid pile into the same second
# regardless of the limit. Roughly the length of a CLI's own boot burst; long
# enough to stagger, short enough that nobody watches a spinner for it.
COLD_START_SETTLE_S = 1.2

# How long the nudged window size is held before it is put back (see
# ``_nudge_repaint``). A PTY carries one size, not a queue of them: set twice
# within the same event-loop tick, the agent may only ever observe the second
# value, see no change, and redraw nothing. Long enough that the two sizes are
# distinct events for a process that polls or debounces its resize handler,
# short enough that nobody sees a pane one row short.
REPAINT_NUDGE_S = 0.08

# Bracketed paste. A TUI that has enabled it receives everything between these
# markers as ONE pasted block rather than as keystrokes, which is the only way
# a structured prompt survives the trip: a bare "\n" written to a PTY IS the
# Enter key, so an unwrapped markdown prompt would submit after its first line.
# This is a terminal-level convention, not an OS API — the same bytes go down
# the same PTY on Windows, macOS and Linux.
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"

# What an agent TUI draws instead of the text when it collapses a paste into a
# placeholder. The wording is per-TUI and changes between releases — Claude Code
# draws "[Pasted text #1 +12 lines]", Codex "[Pasted Content 2497 chars]" — so
# this matches the SHAPE (a bracketed summary that mentions pasting) rather than
# one vendor's phrasing. Keying on Claude Code's wording alone is what let a
# prompt sit visibly in a Codex box while the user was told it had been sent.
_PASTE_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\bpaste\w*\b[^\]]*\]", re.IGNORECASE)


def _opens_completion(payload: str) -> bool:
    """True when the prompt's last token would leave a completion popup open.

    ``@path`` opens the file picker and ``/name`` the command picker; with either
    still open, Enter selects from the list instead of submitting.
    """
    last = payload.rsplit(" ", 1)[-1]
    return last.startswith(("@", "/")) and len(last) > 1


def _submit_needle(payload: str) -> str:
    """The fragment used to recognise the prompt inside the input line.

    The beginning, not the end: the input box wraps long prompts, so only the
    first line is reliably intact — and it is the part that never changes when a
    completion popup rewrites the tail.

    A composed prompt is markdown, so the needle stops at the first line break
    too: a needle spanning a line break could never be found on one screen row.
    """
    first_line = payload.split("\n", 1)[0]
    return " ".join(first_line.split())[:28].strip().lower()


def _input_line_holds(tail: list[str], needle: str) -> bool:
    """True when the terminal's input line still shows ``needle`` being typed.

    Only the LAST prompt-marked line counts. An agent echoes a submitted prompt
    back into its history behind the same ``>`` glyph, so "any line starting with
    > contains the text" reports every successful submit as a failure — measured,
    it did exactly that. The live input line is always the bottom-most one, and
    after a submit it is empty.
    """
    if not needle:
        return False
    current: str | None = None
    for line in tail:
        stripped = line.strip()
        if not stripped:
            continue
        for marker in _INPUT_MARKERS:
            if stripped.startswith(marker):
                current = stripped[len(marker) :].strip()
                break
    if not current:
        return False
    if _PASTE_PLACEHOLDER_RE.search(current):
        # The TUI collapsed our paste into a placeholder, so the text itself is
        # not on screen to compare against. It is still sitting in the box —
        # calling that "submitted" would hide a real failure behind an
        # optimistic check, and the caller would tell the user it went out.
        return True
    return current.lower().startswith(needle[: max(8, len(needle) // 2)])


def sanitize_prompt(text: str, *, keep_newlines: bool = False) -> str:
    """Injectable form of ``text``: printable characters only, length-capped.

    Escape sequences are removed whole (so ``ESC [ A`` does not leave a stray
    ``[A`` in the prompt) and every remaining C0 control is dropped — the caller
    cannot smuggle Ctrl-C, ESC, or EOF into a running agent.

    With ``keep_newlines`` the line structure of a composed markdown prompt
    survives, which is what makes a structured brief possible at all. ``\\r``
    and ``\\t`` still do not survive: a lone carriage return IS the submit
    keystroke, and a tab is a completion key. Runs of blank lines collapse to
    one, so a stray gap cannot push the prompt out of the visible pane.
    """
    from .transcript import strip_ansi

    kept: list[str] = []
    for ch in strip_ansi(text):
        if keep_newlines and ch == "\n":
            kept.append(ch)
        elif ch in "\r\n\t":
            kept.append(" ")
        elif ch >= " ":
            kept.append(ch)
        # everything else is a C0 control and is dropped outright
    cleaned = "".join(kept)

    if not keep_newlines:
        return " ".join(cleaned.split())[:MAX_PROMPT_CHARS]

    lines: list[str] = []
    for raw in cleaned.split("\n"):
        line = " ".join(raw.split())
        if not line and lines and not lines[-1]:
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:MAX_PROMPT_CHARS]


def resolve_account(agent: str, requested: str | None) -> str | None:
    """Pin a pane to a concrete account id at CREATION time.

    ``None`` in, active account out — but the answer is stored, not re-read
    later. That is the whole point: a pane must keep running on the subscription
    it was opened with even after the user switches the default, because the
    alternative is an agent whose conversation history moves out from under it.

    A requested id that does not resolve (or belongs to another CLI) falls back
    to the active account rather than failing the pane: an unopenable pane is a
    worse answer than an honest default.
    """
    if not has_accounts(agent):
        return None
    from jarvis import agent_accounts

    if requested:
        account = agent_accounts.resolve(requested)
        if account is not None and account.platform == agent:
            return account.id
        logger.info(
            "Agentic IDE: account {!r} is unknown — using the active one instead",
            requested,
        )
    return agent_accounts.active_account(agent).id  # type: ignore[arg-type]


def account_label(account_id: str | None) -> str | None:
    """The display name of a pane's account, or ``None`` when it has none."""
    if not account_id:
        return None
    from jarvis import agent_accounts

    account = agent_accounts.resolve(account_id)
    return account.label if account is not None else None


def _requested_account(entry: dict[str, Any]) -> str | None:
    """The account id a wizard/API request asked for, if it named one."""
    value = entry.get("account")
    return str(value).strip() or None if isinstance(value, str) else None


def _restore_key(space: resume_store.SnapshotWorkspace) -> str:
    """Stable identity of ONE remembered workspace, for "did I already reopen it?".

    Folder alone is not it: two workspaces may share a folder on purpose, and
    collapsing them would silently drop one. The id alone is not it either —
    older snapshots carry none. Together they identify the record, and the
    folder is compared in the store's own normalized form so a symlinked or
    differently-cased path cannot read as a second folder.
    """
    return f"{space.session_id}|{resume_store.folder_key(space.folder)}"


def _redirected_home(term: Terminal) -> Path | None:
    """The config dir this pane's CLI will really run from, when it is not the
    machine's own.

    ``None`` for every pane that inherits the machine's configuration untouched
    — a plain terminal, a CLI that has no accounts, and the built-in login — and
    that is the case where a pane is already identical to an ordinary terminal.
    A path means the CLI has been redirected, which is what everything below has
    to compensate for.
    """
    if not term.account or not has_accounts(term.agent):
        return None
    from jarvis import agent_accounts

    if not agent_accounts.env_overrides(term.agent, term.account):  # type: ignore[arg-type]
        return None
    return agent_accounts.config_dir_for(term.agent, term.account)  # type: ignore[arg-type]


#: Environment markers left behind by the coding-agent session that STARTED this
#: app, which a pane must never inherit.
#:
#: The app is regularly launched from inside a coding CLI — a contributor running
#: ``run.bat`` from an agent's terminal, the in-app restart (which hands the new
#: process its predecessor's environment, so one such launch survives every
#: restart afterwards). A CLI that finds these variables believes it is a NESTED
#: run of itself, and Claude Code answers that by switching its transcript off:
#: "Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION". A pane whose
#: conversation is never written to disk cannot be continued afterwards, so every
#: pane came back with an empty history while the restore point looked healthy —
#: it held a session id for a conversation that was never recorded (found
#: 2026-07-28: not one transcript on disk for a whole morning's work).
#:
#: Deliberately an explicit list rather than a ``CLAUDE_*`` prefix sweep: the same
#: namespace carries credentials (``CLAUDE_CODE_OAUTH_TOKEN``), the account
#: redirection this module sets itself (``CLAUDE_CONFIG_DIR``) and settings a user
#: legitimately exports for every terminal they open. Only markers that identify
#: a RUNNING session belong here — add new ones as CLIs introduce them.
PARENT_AGENT_SESSION_VARS: frozenset[str] = frozenset(
    {
        # Claude Code (and every launch profile that borrows its binary).
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
        "CLAUDE_CODE_NO_FLICKER",
        "CLAUDE_CODE_USE_POWERSHELL_TOOL",
        "CLAUDE_EFFORT",
        "CLAUDE_PID",
        "CLAUDE_PLUGIN_DATA",
        # Codex: a pane inside a parent's sandbox refuses work it may do.
        "CODEX_SANDBOX",
        "CODEX_SANDBOX_NETWORK_DISABLED",
    }
)

#: Said once per process, not once per pane: a full grid would otherwise repeat
#: the same line a dozen times for one cause.
_parent_session_reported = False


def _without_parent_agent_session(env: dict[str, str] | None) -> dict[str, str] | None:
    """``env`` with the parent session's markers removed.

    ``None`` in and nothing to strip means ``None`` out — plain inheritance, the
    spawn this app produced before any of this existed. Stripping is what makes a
    pane a TOP-LEVEL session of its CLI, which is the only kind that records a
    conversation and can therefore be resumed.
    """
    global _parent_session_reported

    source = os.environ if env is None else env
    present = sorted(name for name in PARENT_AGENT_SESSION_VARS if name in source)
    if not present:
        return env
    cleaned = dict(source)
    for name in present:
        cleaned.pop(name, None)
    if not _parent_session_reported:
        _parent_session_reported = True
        logger.info(
            "Agentic IDE: this app was started from a coding-agent session; "
            "dropping {} from every pane so its CLI runs as its own session "
            "and can be resumed later",
            ", ".join(present),
        )
    return cleaned


def _spawn_env(term: Terminal) -> dict[str, str] | None:
    """The child environment that puts this pane on its own subscription.

    ``None`` — plain inheritance — whenever the pane's account needs nothing
    changed, which is every pane on the built-in account. So a user who never
    opens the switcher gets a spawn byte-for-byte identical to the one this app
    produced before the feature existed.

    Redirecting the CLI's config directory moves the CLI's whole USER LEVEL along
    with its login: its skills, subagents, slash commands, plugins and
    connectors, hooks, output styles, user memory file and settings all live in
    that directory. Left alone, a pane on an added account therefore ran a
    stripped version of the CLI the user has installed — no skills, no plugins,
    no global instructions, and the built-in fallback operating mode — while the
    same CLI in an ordinary terminal had all of it. A pane is supposed to BE that
    terminal, so the user's own setup is shared into the account's directory
    before the spawn (:mod:`jarvis.agent_config_parity`), and only what a shared
    settings file cannot carry falls back to the narrow per-key mode mirror.

    On top of the account, the pane carries whatever the registry entry declares
    for EVERY pane of that CLI: a fixed environment (switching off an updater
    that would otherwise swap the binary mid-conversation) and, for an entry
    whose environment depends on user configuration, a factory resolved fresh
    here. A factory that answers ``None`` means "not configured" and raises,
    because the alternative is the quiet disaster: the one entry that needs this
    is a launch profile pointing a borrowed binary at a different vendor's
    endpoint, the binary reads that endpoint once at start-up and never mentions
    which one it got, so a pane launched without it answers perfectly well from
    the wrong vendor and bills the wrong account.

    Filesystem work, so callers run it off the event loop.
    """
    from jarvis import agent_accounts, agent_config_parity

    env: dict[str, str] | None = None
    if _redirected_home(term) is not None:
        report = agent_config_parity.ensure_parity(term.agent, term.account)  # type: ignore[arg-type]
        mode_file = agent_accounts.mode_file_name(term.agent)  # type: ignore[arg-type]
        # Only when the account's settings file IS the user's file does sharing
        # it carry the mode too. A file the account has partly written itself was
        # merely filled in with the keys it lacked, and the mode may well be one
        # of the keys it already had — so the narrow per-key mirror still has
        # work to do there.
        if report.shared.get(str(mode_file)) not in {"mirrored", "current"}:
            agent_accounts.inherit_default_mode(term.agent, term.account)  # type: ignore[arg-type]
        env = agent_accounts.spawn_env(term.agent, term.account)  # type: ignore[arg-type]

    overlay = agent_spawn_overlay(term.agent)
    if not overlay:
        return _without_parent_agent_session(env)
    env = dict(os.environ if env is None else env)
    for key, value in overlay.items():
        # An empty value means "remove this variable from the child". A GLM pane
        # needs it: this host may well carry an ANTHROPIC_API_KEY for unrelated
        # reasons, it outranks the token being passed, and the result is the
        # silent wrong-vendor pane above.
        if value:
            env[key] = value
        else:
            env.pop(key, None)
    return _without_parent_agent_session(env)


def agent_spawn_overlay(agent: str) -> dict[str, str]:
    """Per-CLI environment every pane of ``agent`` gets, resolved now.

    Raises :class:`SessionError` when the entry declares a factory and the
    factory reports the CLI is not configured. Refusing to open the pane is the
    point — see :func:`_spawn_env`.
    """
    spec = workspace_agents.get_agent(agent)
    if spec is None:
        return {}
    overlay = dict(spec.spawn_env)
    if spec.spawn_env_factory is None:
        return overlay
    resolved = spec.spawn_env_factory()
    if resolved is None:
        raise SessionError(
            f"{spec.display_name} is not configured yet — add its API key on "
            "the API Keys page, then open the pane again."
        )
    overlay.update(resolved)
    return overlay


def account_home(agent: str, account_id: str | None) -> Path | None:
    """The config dir a pane's conversation history lives in.

    ``None`` for a pane with no account (or an agent that has none), which keeps
    every existing lookup on its old path.
    """
    if not account_id or not has_accounts(agent):
        return None
    from jarvis import agent_accounts

    return agent_accounts.config_dir_for(agent, account_id)  # type: ignore[arg-type]


def agent_argv(agent: str) -> tuple[str, ...] | None:
    """argv that runs ``agent`` as the PTY's own process, or None if missing.

    A plain terminal resolves to this machine's own interactive shell
    (``discover_shells()`` order: pwsh > Windows PowerShell > cmd > Git Bash, or
    ``$SHELL`` first on macOS/Linux) — no agent wrapped around it, and None on a
    host that has no shell at all, which reads the same as a missing binary.
    """
    spec = workspace_agents.get_agent(agent)
    if spec is None:
        return None
    if not spec.is_coding_agent:
        return workspace_agents.plain_terminal_argv()
    binary = spec.executable or spec.launch_command or spec.name
    try:
        from jarvis.core.path_augment import ensure_cli_paths

        ensure_cli_paths()
    except Exception:  # noqa: BLE001, S110 - PATH augmentation is best-effort
        pass
    exe = shutil.which(binary)
    if exe is None:
        return None
    if spec.shell_launch:
        # A user-added entry whose command is shell SOURCE — a pipeline, a
        # variable assignment, two commands chained. There is no argv to exec,
        # so it runs through a shell that EXITS with it (never `-NoExit`/`/k`:
        # a surviving prompt would look like a live agent to every readiness
        # check). The PATH lookup above still had to succeed, so an entry whose
        # first word is not installed is reported missing rather than opening a
        # pane that says "command not found".
        return workspace_agents.shell_run_argv(spec.launch_command or "")
    if sys.platform == "win32":
        lowered = exe.lower()
        if lowered.endswith((".cmd", ".bat")):
            if (direct := _behind_win_shim(spec, exe)) is not None:
                return (*direct, *spec.launch_args)
            # ConPTY cannot exec a batch shim. `cmd /c` (never /k) exits with
            # the agent, so no shell survives it.
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            return (comspec, "/c", exe, *spec.launch_args)
        if lowered.endswith(".ps1"):
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if shell is None:
                return None
            return (shell, "-NoLogo", "-NoProfile", "-File", exe, *spec.launch_args)
    return (exe, *spec.launch_args)


def _behind_win_shim(spec: workspace_agents.WorkspaceAgent, shim: str) -> tuple[str, ...] | None:
    """What the Windows ``.cmd`` shim would have launched, launched directly.

    ``cmd /c <shim>`` works and stays the fallback, but it wedges a second
    process between the pane and the agent, which costs clean signal delivery
    and a clean exit. When the entry declares where the real thing sits inside
    the installed package we skip the shim entirely.

    Two shapes exist and the entry says which: a Node script that needs
    ``node.exe`` in front of it, and a native executable that is simply run.
    ``None`` whenever the declared path is not actually there — an install
    laid out differently than expected must fall back, never fail.
    """
    if spec.win_shim is None:
        return None
    target = Path(shim).resolve().parent.joinpath(*spec.win_shim.relative_path)
    if not target.is_file():
        return None
    if spec.win_shim.kind == "exe":
        return (str(target),)
    from jarvis.core.path_augment import resolve_node_executable

    node = resolve_node_executable()
    return (node, str(target)) if node else None


@dataclass(slots=True)
class PaneViewer:
    """One attached screen and the geometry it most recently reported."""

    output: Any
    exit: Any
    cols: int
    rows: int


@dataclass(frozen=True, slots=True)
class PendingPromptAttachmentBatch:
    """One explicitly targeted drop waiting for a spoken pane prompt."""

    batch_id: str
    attachments: tuple[Any, ...]
    files: tuple[str, ...]


@dataclass(slots=True)
class Terminal:
    """One named pane: a call-sign, an agent, and its live PTY (if attached)."""

    # The url-safe key ("t1"), and the call-sign as it is written and spoken
    # ("T1"). The name is the pane's IDENTITY, not a live read of where it
    # sits: it is handed out from the grid position the pane is opened at and
    # then stays put, so an instruction cannot land in a different agent
    # because a neighbouring pane closed between hearing it and sending it.
    key: str
    name: str
    agent: str  # "claude" | "codex"
    display_name: str  # "Claude Code"
    index: int
    # Stable for THIS pane's lifetime and deliberately unrelated to its visible
    # call-sign. A closed T1 and a new T1 are different panes; a renamed T1 is
    # still the same pane. Prompt-history files use this id to preserve exactly
    # that boundary across app restarts.
    history_id: str = field(default_factory=lambda: uuid4().hex)
    # Coarse "where does this pane roughly sit" HINTS, derived from the
    # workspace's layout tree by `_renumber` after every structural change —
    # never authoritative. The tree (``Session.layout``) is the geometry now:
    # the flat two-axis grid these fields came from could not say "beside the
    # top pane only" ("split right" was a full-height column by construction,
    # so splitting the top pane of a stack restructured the whole workspace —
    # reported with a drawing on 2026-08-12, fixed by the tree). The two
    # integers survive because consumers that only SPEAK about the grid
    # ("the top-left terminal", the resume offer's dots) still think in
    # columns, and because older builds reading a new resume snapshot can
    # still place every pane somewhere sensible.
    column: int = 0
    slot: int = 0
    # Which subscription of `agent` this pane runs on (see jarvis.agent_accounts).
    # Resolved to a concrete id when the pane is CREATED, never read live at
    # spawn time: flipping the global default must not silently re-point a pane
    # that is already on screen — least of all one mid-conversation, which would
    # hand a resumed transcript to an account that has never seen it.
    account: str | None = None
    # True only when `account` was DELIBERATELY chosen — named in the wizard's
    # per-pane picker, passed explicitly to the API, or carried over by
    # splitting such a pane. False for a pane that simply followed the
    # workspace's active account at creation. Splits consult this: only a
    # deliberate seat is worth propagating. Without the distinction every pane
    # inherited its anchor's account, so in a workspace whose panes all shared
    # one seat the subscription switcher could never reach a single new pane —
    # the 2026-08-12 report: "I changed my subscriptions twice and it doesn't
    # change", with every split resurrecting the seat the user had just left.
    account_pinned: bool = False
    status: Status = "pending"
    pty_id: str | None = None
    # The geometry the PTY ACTUALLY holds, as last handed to `setwinsize`.
    #
    # Not derivable from anything else that was already here, which is why it
    # exists. `transcript.cols` looks like the same number and is not: it is the
    # DISPLAY mirror, and it drifts from the PTY in both directions. `resize`
    # floors a request before recording it there, so the transcript can hold a
    # size the child was never given; and `Transcript.resize` stores whatever it
    # is handed while its own `ScreenBuffer` clamps to `screen.MIN_COLS` (20), so
    # `transcript.cols` can equally hold a size the replayed grid is not in.
    # Two things were reading it as the real geometry — the reattach fallback and
    # the below-the-floor rescue — and only the PTY's own numbers can answer the
    # question both are really asking: what size is the AGENT drawing in?
    #
    # Zero until the first spawn, meaning "no process has been sized yet".
    pty_cols: int = 0
    pty_rows: int = 0
    # Set just before this pane's agent is killed on purpose (viewer gone, pane
    # closed, workspace closed). A killed process reports a failure exit exactly
    # like a crashed one, so without this the resume self-healing in `attach`
    # would helpfully restart an agent somebody had just stopped — and it would
    # then run on unwatched, which is the whole thing the kill prevents.
    stopping: bool = False
    exit_code: int | None = None
    error: str = ""
    started_at: float | None = None
    last_output_at: float | None = None
    # When anything was last typed INTO this pane — every keystroke, not only a
    # submitted line. It exists to keep the activity detector honest: a terminal
    # echoes what a person types, so "this pane is producing output" means the
    # agent is working only when nobody is at the keyboard. Without it, pausing
    # mid-sentence in a pane reads as an agent that just finished.
    last_input_at: float | None = None
    # When this pane's PTY was last RESIZED — a re-join with a new geometry, a
    # grid re-layout, the repaint nudge. A full-screen TUI answers a size change
    # by redrawing its whole frame, and that redraw is output plus a changed
    # screen: exactly the two signals the activity detector reads as "working".
    # Movement in the shadow of this stamp is the pane being redrawn, not the
    # agent working — see `activity._resize_shadowed`.
    last_resize_at: float | None = None
    prompts_sent: int = 0
    last_prompt: str = ""
    # The current process's records are kept as a fallback if the local history
    # file cannot be written. The full durable history is loaded only when its
    # UI is opened, never in the workspace-state hot path.
    prompt_records: list[prompt_history.PromptHistoryEntry] = field(
        default_factory=list, repr=False, compare=False
    )
    # Explicitly targeted drops waiting for this pane's next spoken prompt.
    # A batch is reserved by identity before composition and removed only after
    # a successful PTY write. The lock protects short state transitions; model
    # and PTY awaits never run while it is held. Ephemeral by design: this is a
    # pending gesture, not workspace history worth restoring after a restart.
    pending_prompt_attachment_batches: list[PendingPromptAttachmentBatch] = field(
        default_factory=list, repr=False, compare=False
    )
    pending_prompt_attachment_reservations: set[str] = field(
        default_factory=set, repr=False, compare=False
    )
    pending_prompt_attachment_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, repr=False, compare=False
    )
    # When the last prompt was handed to this pane, as a wall-clock timestamp.
    #
    # The receipt the user is shown is built from THIS rather than from the
    # terminal stream, and that is the whole point. A pane proves a prompt
    # arrived by echoing it, which requires a chain of things to have gone
    # right at one particular moment: the pane on screen, its output un-parked,
    # its socket up, the emulator painted. Every link in that chain has failed
    # in production at least once, and each failure looks identical from the
    # user's chair — Jarvis says it sent the brief and the pane shows nothing,
    # so the honest conclusion is that Jarvis lied. A timestamp in the state
    # cannot be missed: it is read at mount, at every reconnect and at every
    # poll, so the receipt is still there when somebody looks ten minutes later.
    last_prompt_at: float | None = None
    # When this pane was last GIVEN something to do — by Jarvis or by a person
    # pressing Enter in it. Distinct from `last_prompt_at`, which only knows
    # about the injection path, and from `last_input_at`, which counts every
    # arrow key.
    #
    # It exists because the activity detector reads MOVEMENT, and a coding CLI
    # moves plenty on its own: starting up, it paints a banner, a model line and
    # whatever warnings it has, then stands still. That is indistinguishable
    # from an agent finishing a job, so a freshly opened workspace rang its bell
    # once per pane — sometimes twice, when the startup drawing came in two
    # bursts — for work nobody had asked for. A pane nobody has given an
    # instruction cannot have finished one, and this is how that is known.
    last_submit_at: float | None = None
    # Did the last prompt actually leave the input line? None = none sent yet.
    submitted: bool | None = None
    # A hand-pressed Enter on an injected prompt is being checked against the
    # screen. Kept explicit so another Enter stays on the verified path rather
    # than being mistaken for a brand-new manual instruction.
    manual_submit_pending: bool = False
    manual_submit_token: int = 0
    bracketed_paste_active: bool = False
    # Did it arrive with its line structure intact? False means the pane
    # rejected the pasted block and the single-line fallback carried it — worth
    # seeing in the log, because it silently costs prompt readability.
    sent_multiline: bool = False
    # Where this pane's conversation lives inside the coding CLI's own history.
    # The pane is the window; this is what the window looks at, and it is the
    # only reason a closed browser is survivable (see .agent_sessions).
    resume: ResumeHandle | None = None
    # Is a conversation-id lookup in flight for this pane, and when did the last
    # ROUND begin (monotonic — a wall clock can jump)? Both exist because the
    # lookup now has more than one trigger: the pane starting, and the pane's
    # conversation actually beginning. Without them a busy pane would stack a
    # round on top of every keystroke that submits, and two rounds racing each
    # other could hand one conversation to two panes. Never persisted: they
    # describe a running pane, not the workspace on disk.
    lookup_running: bool = False
    lookup_at: float = 0.0
    # Did the CURRENT agent process continue that conversation, or start empty?
    # Reported honestly rather than assumed: a resume can fail, and a user who
    # is told "resumed" and gets an amnesiac agent has been lied to.
    resumed: bool = False
    # Did the last viewer re-join an agent that never stopped (rather than
    # starting one)? A different claim from `resumed`, and both are worth
    # telling apart on screen: "continued its conversation" means a NEW process
    # picked up an old transcript, "still running" means the same process has
    # been working the whole time you were looking somewhere else.
    reattached: bool = False
    # Was this pane last observed actively working before its process went
    # away? Persisted in the resume snapshot and kept separate from `resumed`:
    # an existing conversation may already be finished or waiting for input,
    # neither of which should receive a blind "continue".
    resume_continuation_needed: bool = False
    # This pane picked its old conversation back up, and NOBODY has told it what
    # to do since. That is the state a restart leaves behind: the agent is alive
    # and holds the whole transcript, but it was killed mid-task and a resumed
    # CLI sits at its prompt waiting rather than carrying on by itself — so the
    # work simply stops, silently, and looks exactly like a pane that finished.
    #
    # Raised where a restore establishes that this pane's conversation really
    # exists, and again where a process is SPAWNED onto one (see `attach`, which
    # also clears it when a resume failed and the pane came back empty). Cleared
    # by anything that counts as "somebody is driving this pane again": a prompt
    # from Jarvis, or a line the user typed into the pane themselves. Never
    # persisted — it describes the pane on screen, not the workspace on disk.
    continuation_pending: bool = False
    # "Continue this one as soon as it can be typed into."
    #
    # Cold starts are staggered (COLD_START_LIMIT), so in a workspace of a dozen
    # panes most are still waiting for a slot when the user presses Continue.
    # Sending only to the ones that happen to be up already is what made the
    # button look like it skipped terminals; refusing them would be the same
    # answer worn differently. So the wish is REMEMBERED here and spent by
    # `attach` once that pane's agent exposes a writable input line.
    continue_when_ready: bool = False
    # The exact nudge paired with ``continue_when_ready``. Usually the one-word
    # default, but the REST contract accepts custom wording and a queued pane
    # must not silently replace it while waiting for its cold-start slot.
    continue_prompt: str = ""
    # Has this pane's screen been observed STANDING STILL since its current
    # process started?
    #
    # This says only that restore/startup repainting has settled. It never proves
    # work: that requires a submission stamped with this process generation.
    # Keeping the claims separate prevents both startup replay and later MCP or
    # status redraws from retracting a valid Continue offer. Raised by the
    # notification sweep on an observed still screen (two looks, never one),
    # cleared on every spawn, and never persisted.
    idle_seen: bool = False
    # What this pane is DOING, as the activity sweep last observed it: working,
    # waiting, asking, starting, exited, failed (see `.activity`). Empty until
    # the first sweep has looked at this pane, and for a plain terminal, which
    # runs no agent and therefore has no job to be in the middle of.
    #
    # Stamped here rather than kept inside the sweep because whether a screen is
    # MOVING can only be seen across two looks, and everything else that wants
    # the answer — the workspace state, the pane list's poll — is a request
    # handler with exactly one look. `activity_at` is when the observation was
    # taken (so a reader can tell a live reading from one left behind by a sweep
    # that has since died), `activity_since` when the pane entered this state
    # (so "waiting" can be shown with how long it has been waiting).
    activity: str = ""
    activity_at: float = 0.0
    activity_since: float = 0.0
    # Monotonic identity for the process currently occupying this pane. The
    # notification watcher outlives PTYs, so it uses this to discard the old
    # process's screen fingerprint before interpreting a replacement process.
    process_generation: int = 0
    # The process generation that most recently received a real instruction.
    # A startup repaint has no such stamp, even if the pane resumes an old
    # conversation whose historical prompt count is non-zero.
    submit_generation: int = -1
    transcript: Transcript = field(default_factory=Transcript)
    # The RAW output stream, kept so the next viewer can be handed the screen
    # this pane is actually showing. Cleared on a fresh spawn, so what a viewer
    # replays always belongs to the process it is now watching.
    replay: ReplayBuffer = field(default_factory=ReplayBuffer)
    # Answers the emulator queries the agent's CLI asks on startup. It lives on
    # the TERMINAL rather than on the viewer's socket for two reasons: the PTY
    # outlives its viewers, and the replay handed to a re-joining viewer carries
    # the original queries — answering those a second time would write the reply
    # into a prompt the agent has long since opened, which is the corruption
    # this exists to prevent. Only live output reaches it.
    queries: TerminalQueryResponder = field(default_factory=TerminalQueryResponder)
    # Where this pane's output currently goes, or None while nobody is looking.
    #
    # A mutable slot rather than a closure captured at spawn time, and that is
    # what makes switching workspaces survivable: the agent keeps running with
    # no viewer, and a new viewer takes the slot without the PTY ever noticing.
    # Bound at spawn, cleared on detach, replaced on re-attach.
    viewer_output: Any = None
    viewer_exit: Any = None
    # EVERY viewer currently attached to this pane, newest last. Each entry
    # keeps its callbacks plus its most recently reported geometry, so promoting
    # an older viewer restores the one shared PTY to the screen now watching it.
    # ``viewer_output`` above is the newest entry — the OWNER — which is a
    # different question from who gets to see the screen.
    #
    # One slot was enough only while a pane could be open in one place. It can
    # be open in several: the desktop app and a browser tab, two windows, a
    # contributor's dev server beside the app. Every one of them attaches to the
    # same pane, and with a single slot the last to connect took the output and
    # every other viewer went silent for good — an agent typing away behind a
    # screen that never moved again, indistinguishable from a dead terminal, and
    # only a reload brought it back (reported 2026-07-28, where a leftover tab
    # from an earlier session quietly held the output of the panes the user was
    # watching).
    #
    # Output is therefore fanned out to all of them, while the OWNER keeps the
    # decisions that must have exactly one answer: the pseudo-terminal's size,
    # and who is allowed to hand the slot back (see ``resize`` and ``detach``).
    watchers: list[PaneViewer] = field(default_factory=list, repr=False, compare=False)
    # Viewers that want to be TOLD when this pane is handed a prompt, rather
    # than having to notice it in the output stream.
    #
    # Separate from ``watchers`` because it answers a different question. That
    # list carries the agent's screen, and a screen is exactly what fails to
    # prove a delivery: the pane may be parked, its emulator unpainted, its
    # socket reconnecting, or the CLI may simply redraw its input box without
    # the text ever scrolling into view. Every one of those has happened, and
    # each time the user was told the brief was sent and saw nothing.
    #
    # So delivery is announced on its own channel, and the state carries it too
    # (``last_prompt_at``) for the viewer that was not connected at that
    # instant. Neither is a substitute for the other: this one is immediate and
    # lossy, the state is durable and up to one poll late.
    prompt_viewers: list[Any] = field(default_factory=list, repr=False, compare=False)
    # Serializes THIS pane's attach path — see `SessionRegistry.attach`.
    #
    # A pane is routinely connected to more than once in the same instant: the
    # panes of a restored workspace reconnect in a burst while the workspace is
    # still opening, are answered "not yet", and retry — and a retry that
    # overlaps the attempt it replaces is two sockets asking for one pane. The
    # spawn path awaits three times between asking "is a process already
    # running?" and recording the one it starts — a cold-start slot, the
    # account's filesystem work, the spawn itself — so a second attempt walked
    # straight through that gap and started a SECOND agent for one call-sign.
    #
    # Measured 2026-07-28: two `claude --resume <the same id>` processes for one
    # pane, a grid of black panes whose transcripts were filling normally, and
    # orphaned CLIs burning a subscription with nothing left holding their ids.
    # The newer spawn takes the viewer slot and clears the replay buffer, which
    # is exactly what leaves the viewer that IS on screen attached to nothing —
    # and an agent's TUI paints itself once, so nothing arrives to correct it.
    #
    # Per pane rather than one registry-wide lock: attaches to DIFFERENT panes
    # must stay concurrent, or opening a workspace of a dozen agents would queue
    # every cold start behind the slowest one.
    attach_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        # Read the replayed screen ONCE. `lines()` walks the whole scrollback,
        # and both the line count below and the recap want it — asking twice per
        # pane per poll is a cost with nothing to show for it.
        lines = self.transcript.lines()
        # The model-written recap when one has been produced for this pane, the
        # deterministic one until then. Reading only — the refresh is scheduled
        # by the /recaps poll, which is the caller that knows a human is
        # actually looking at this workspace.
        summary = recap_engine.recap_for(self, lines=lines)
        reading = self.reading()
        return {
            "key": self.key,
            # The call-sign key is reusable after a pane closes. The chat rail
            # needs the pane lifetime to keep arrival order honest across a
            # workspace remount and to put a replacement T1 at the bottom.
            "history_id": self.history_id,
            "name": self.name,
            "agent": self.agent,
            "display_name": self.display_name,
            # Can Jarvis type into this pane at all? False for a plain terminal,
            # which is a shell prompt rather than an agent — the prompt bar and
            # the voice path both have to know, or they would offer a target
            # that refuses every instruction sent to it.
            "accepts_prompts": accepts_prompts(self.agent),
            "index": self.index,
            "column": self.column,
            "slot": self.slot,
            "status": self.status,
            "exit_code": self.exit_code,
            "error": self.error,
            "started_at": self.started_at,
            "last_output_at": self.last_output_at,
            "idle_seconds": (
                None if self.last_output_at is None else round(time.time() - self.last_output_at, 1)
            ),
            "prompts_sent": self.prompts_sent,
            # A composed brief runs to MAX_PROMPT_CHARS (6 000); nothing reads
            # more than the opening line back (the UI never renders the field
            # at all), while the full text used to ride along in every /state
            # poll AND every model-facing status payload — per pane. 200 chars
            # matches the focus block's per-pane budget.
            "last_prompt": self.last_prompt[:200],
            # How long the delivered text really is, so a client can say "1 of
            # 2 400 characters" instead of presenting the 200-char excerpt as
            # if it were everything that was sent.
            "last_prompt_chars": len(self.last_prompt),
            # WHEN it was handed over. Cheap enough for every poll (one float),
            # and it is what turns "this pane has a last prompt" into "this pane
            # was given a prompt at 15:42:07" — a claim the user can check
            # against what they just heard Jarvis say. See the field's own
            # comment for why the receipt may not be built from the terminal
            # stream instead.
            "last_prompt_at": self.last_prompt_at,
            "submitted": self.submitted,
            "lines_captured": len(lines),
            # What this pane is doing, in the two lengths the header needs: one
            # clause for the label (which the pane's width will clip) and one or
            # two sentences for the tooltip behind it. Derived, never stored —
            # see .recap for why it is computed on read.
            "recap": summary.headline,
            "recap_detail": summary.detail,
            # Is this pane's agent still on the job, or has it stopped? See
            # `.reading` — the one question the pane list could not answer, and
            # the reason it used to say "live" at a terminal that had been
            # finished for twenty minutes. Empty for a plain shell.
            "activity": reading.activity,
            "activity_since": reading.since,
            # Whether a still screen means "finished" or "never asked for
            # anything" — the same picture, and not the same news.
            "worked": has_work_behind_it(self),
            "resumed": self.resumed,
            # Continued its old conversation and has had no instruction since —
            # the pane a restart left standing still. Carried in the ordinary
            # state so a client can mark it without a second request; the list
            # of them, with the reason each one can or cannot be nudged, is
            # `GET /interrupted`.
            "continuation_pending": self.continuation_pending,
            # Whether a handle EXISTS, never the handle itself: it is an
            # internal pointer into the CLI's history and no client needs it.
            "has_resume": self.resume is not None,
            "account": self.account,
            "account_label": account_label(self.account),
        }

    def reading(self) -> Reading:
        """Is this pane's agent working, or has it stopped — and since when?

        One place, because two clients ask: the workspace state (what a pane
        opens with) and the pane-list poll (what it says from then on), and a
        pane described as working by one and finished by the other is worse than
        either answer alone.

        A plain terminal reads as nothing at all. It is a shell prompt, not an
        agent: it stands still for its whole life, so every word this vocabulary
        has would be a claim about a job it was never given.
        """
        if not accepts_prompts(self.agent):
            return NO_READING
        return observed(self)

    def to_snapshot(self) -> resume_store.SnapshotTerminal:
        """This pane as the resume store remembers it."""
        return resume_store.SnapshotTerminal(
            key=self.key,
            name=self.name,
            agent=self.agent,
            history_id=self.history_id,
            column=self.column,
            slot=self.slot,
            resume=self.resume,
            prompts_sent=self.prompts_sent,
            account=self.account,
            account_pinned=self.account_pinned,
            continuation_needed=self.resume_continuation_needed,
        )


@dataclass(slots=True)
class Session:
    """A chosen folder plus its named terminals."""

    id: str
    folder: str
    # The tab label is workspace identity, not project identity. Several
    # workspaces may intentionally point at the same folder, so the folder's
    # basename alone cannot distinguish them.
    name: str
    profile: ProjectProfile
    terminals: list[Terminal]
    created_at: float
    # WHERE every pane sits and how much room it has — the split tree, the one
    # authority on workspace geometry (see ``layout_tree``). Every structural
    # change (split, close, move, refold, restore) rewrites it and then lets
    # `_renumber` project reading order and the coarse per-pane hints from it.
    # ``None`` only for a workspace with no panes.
    layout: layout_tree.LayoutNode | None = None
    # Focus mode: while on, Jarvis answers inside this workspace's context. The
    # flag lives here (not in jarvis.toml) on purpose — it is a mode of the
    # current session, and a restart should land the user back in normal mode
    # rather than silently keeping a narrowed assistant.
    focus_mode: bool = False
    # Ephemeral UI context for deictic voice/chat references. In chat view one
    # pane fills the stage, so "this terminal" has a concrete, visible meaning;
    # in the grid every pane is visible and no default is honest. This state is
    # reported by the mounted frontend and deliberately excluded from resume
    # snapshots: after a restart the UI reports what it actually shows again.
    #
    # Which reading mode is on screen — see ``agentic_ide.workspace_view``. It
    # travels as a name rather than the ``chat_view`` boolean it replaced, so a
    # further mode reads correctly here without re-deriving what "not chat" was
    # supposed to mean.
    surface_view: str = VIEW_GRID
    surface_terminal: str = ""
    # The written prompt bar and the voice orb share one explicit pane target.
    # Unlike ``surface_terminal``, this remains meaningful in grid view: every
    # pane may be visible, but the selected prompt chip says exactly where the
    # next dropped file or instruction belongs.
    surface_on_screen: bool = False
    surface_prompt_target: str = ""
    # When this workspace was last brought to the front. Orders the "most
    # recently used" answer the resume snapshot and the UI both want, which is
    # NOT the order the workspaces were opened in.
    last_active_at: float = 0.0
    # Background session-id lookups belonging to THIS workspace. Held so the
    # loop cannot garbage-collect one mid-flight, and per session rather than
    # per registry so closing one workspace cannot cancel another's.
    lookups: set[asyncio.Task[None]] = field(default_factory=set)
    # Which remembered workspace this one came back from, empty when it was
    # opened rather than restored. It is what makes restoring idempotent: a
    # second "Resume all sessions" (a stale offer card in another window, a
    # double-submit) recognises what is already on screen instead of opening a
    # second copy of it with every call-sign renamed around the collision.
    restored_from: str = ""

    def find(self, wanted: str) -> Terminal | None:
        """Terminal by call-sign, key, or a spoken phrase containing one.

        Call-signs are tried across EVERY pane before any key is, and that
        order is load-bearing once panes can be renamed. A pane keeps the key
        it was opened with (it is what the running pseudo-terminal is filed
        under), so renaming T1 to "Frontend" leaves a pane whose key is still
        ``t1`` — and the next pane opened is free to take the call-sign T1.
        Asking about keys first would then hand "T1" to the pane the user
        renamed precisely so it would stop being T1.
        """
        if not wanted:
            return None
        key = normalize(wanted)
        for term in self.terminals:
            if normalize(term.name) == key:
                return term
        for term in self.terminals:
            if normalize(term.key) == key:
                return term
        matched = resolve(wanted, [t.name for t in self.terminals])
        if matched is None:
            return None
        return next((t for t in self.terminals if t.name == matched), None)

    def contextual_terminal(self) -> Terminal | None:
        """The one pane the visible surface puts in front of the user.

        Chat view stages exactly one pane and can answer. The grid never
        does — a dozen panes are visible there, and picking one of them would
        be a guess dressed as a fact.
        """
        if self.surface_view != VIEW_CHAT:
            return None
        if not self.surface_terminal:
            return None
        return self.find(self.surface_terminal)

    def stages_one_pane(self) -> bool:
        """Does the visible view show a single pane, rather than the wall?"""
        return self.surface_view == VIEW_CHAT

    def prompt_target_terminal(self) -> Terminal | None:
        """The pane selected by the visible prompt bar and voice orb."""
        if not self.surface_on_screen or not self.surface_prompt_target:
            return None
        selected = self.find(self.surface_prompt_target)
        if selected is None or not accepts_prompts(selected.agent):
            return None
        return selected

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "folder": self.folder,
            "name": self.name,
            "project": self.profile.to_dict(),
            "created_at": self.created_at,
            "focus_mode": self.focus_mode,
            # The split tree the grid draws from. The per-terminal column/slot
            # fields riding along below are coarse hints for consumers that
            # only talk ABOUT the layout; a client that renders it needs this.
            "layout": layout_tree.to_dict(self.layout) if self.layout else None,
            "terminals": [t.to_dict() for t in self.terminals],
        }

    def to_brief(self) -> dict[str, Any]:
        """This workspace as a language model needs it to steer the panes.

        Deliberately not ``to_dict``: the full state runs to ~25 000
        characters (profiles, prompts, transcript statistics), which a
        tool-result cap then slices mid-JSON — the model pays thousands of
        input tokens per loop iteration for a broken fragment. Steering
        needs exactly: which pane, which agent, alive or not, busy or idle,
        and one recap line of what it is doing.
        """
        terminals = []
        for term in self.terminals:
            lines = term.transcript.lines()
            summary = recap_engine.recap_for(term, lines=lines)
            terminals.append(
                {
                    "name": term.name,
                    "agent": term.agent,
                    "status": term.status,
                    "accepts_prompts": accepts_prompts(term.agent),
                    "idle_seconds": (
                        None
                        if term.last_output_at is None
                        else round(time.time() - term.last_output_at, 1)
                    ),
                    "recap": summary.headline,
                }
            )
        return {
            "folder": self.folder,
            "name": self.name,
            "focus_mode": self.focus_mode,
            "terminals": terminals,
        }

    def to_card(self, *, active: bool) -> dict[str, Any]:
        """This workspace as one tab in the workspace bar.

        Deliberately not ``to_dict``: a bar of six tabs would otherwise carry
        six full project profiles and every pane's transcript statistics on
        every poll, to render a name and a number.
        """
        live = sum(1 for t in self.terminals if t.status == "live")
        return {
            "id": self.id,
            "folder": self.folder,
            "name": self.name,
            "branch": self.profile.branch,
            "terminals": len(self.terminals),
            "live_terminals": live,
            "focus_mode": self.focus_mode,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "active": active,
        }


@dataclass(slots=True)
class RestoreResult:
    """What taking a restore point actually brought back.

    ``skipped`` carries a reason per workspace that could not come back (for
    example, its folder was deleted). Reported rather than swallowed: a resume
    that quietly returns three of five workspaces looks like a bug to the person
    who had five.
    """

    sessions: list[Session]
    skipped: list[tuple[str, str]]

    @property
    def terminal_count(self) -> int:
        return sum(len(s.terminals) for s in self.sessions)


#: How many viewers one pane may feed at once.
#:
#: Generous, because the legitimate number is small (an app window, a browser
#: tab, a second screen) and the point of the cap is not thrift — it is that a
#: client leaking sockets must not grow this list without end. The oldest is
#: dropped, which is also the one least likely to still have a human in front
#: of it.
MAX_WATCHERS = 8


def _same_viewer(left: Any, right: Any) -> bool:
    """Whether two viewer callbacks are the same one.

    By equality as well as identity: a bound method is a brand new object on
    every attribute access, so ``is`` alone answers "different" for two reads of
    one socket's callback.
    """
    return left is right or left == right


def _watch(
    term: Terminal,
    on_output: Any,
    on_exit: Any,
    cols: int,
    rows: int,
    *,
    claim_owner: bool = True,
) -> bool:
    """Attach a viewer to ``term`` and optionally make it the owner.

    Newest last, and never twice: a socket that re-attaches (a resize, a resume
    retry) replaces its own entry rather than being fed the same bytes twice.
    A background viewer may watch without taking the one shared PTY geometry
    away from the window that is actually in front of the user.
    """
    term.watchers = [w for w in term.watchers if not _same_viewer(w.output, on_output)]
    term.watchers.append(PaneViewer(on_output, on_exit, cols, rows))
    if len(term.watchers) > MAX_WATCHERS:
        del term.watchers[0 : len(term.watchers) - MAX_WATCHERS]
    owner_is_attached = any(
        _same_viewer(watched.output, term.viewer_output) for watched in term.watchers
    )
    if claim_owner or term.viewer_output is None or not owner_is_attached:
        term.viewer_output = on_output
        term.viewer_exit = on_exit
    return _same_viewer(term.viewer_output, on_output)


def _viewers(term: Terminal) -> list[Any]:
    """Every output callback this pane should write to, newest last.

    Falls back to the owner slot alone when nothing registered — a test (or any
    caller) that sets ``viewer_output`` by hand still gets its output.
    """
    if term.watchers:
        return [viewer.output for viewer in term.watchers]
    return [term.viewer_output] if term.viewer_output is not None else []


def _exit_viewers(term: Terminal) -> list[Any]:
    """The same, for the one-shot "the agent stopped" callback."""
    if term.watchers:
        return [viewer.exit for viewer in term.watchers if viewer.exit is not None]
    return [term.viewer_exit] if term.viewer_exit is not None else []


async def announce_prompt(term: Terminal) -> None:
    """Tell every attached viewer that this pane was just handed a prompt.

    Best-effort by construction, and deliberately so: a viewer that has gone
    away, a socket mid-close, a handler that raises — none of them may cost the
    delivery that already happened. The durable half of the receipt is the
    pane's own ``last_prompt_at``, which every later state read picks up, so a
    notice lost here degrades to "the receipt appears at the next poll" rather
    than to "the user is told nothing".

    A failure is logged rather than swallowed silently: a channel that never
    reaches anyone looks, from the outside, exactly like the bug this exists to
    fix.
    """
    if not term.prompt_viewers:
        return
    payload = {
        "name": term.name,
        "at": term.last_prompt_at,
        "chars": len(term.last_prompt),
        "preview": term.last_prompt[:200],
        "submitted": term.submitted,
        "prompts_sent": term.prompts_sent,
    }
    for notify in list(term.prompt_viewers):
        try:
            await notify(payload)
        except Exception:  # noqa: BLE001 - one dead viewer never sinks the others
            logger.debug("Agentic IDE: a prompt notice could not be delivered to a viewer")


class SessionError(RuntimeError):
    """A request the registry refuses, with a user-facing English message."""


class SessionNotReady(SessionError):
    """The addressed workspace is not open — not "not here", but "not yet".

    Raised where the old code raised a plain ``SessionError`` with the same
    message, and the distinction is the whole point: a pane that connects while
    the backend is still coming up (a restart, a workspace not restored yet) is
    asking about a workspace that WILL exist, and a viewer told "no such pane"
    stops trying for good. Every caller that can wait must be able to tell the
    two apart — see the PTY socket's close codes.
    """


class Registry:
    """Process-wide holder of the open Agentic-IDE workspaces.

    Several may be open; exactly one (or none) is *active*, and that is the one
    on screen. ``session`` is always the active one, so every layer that only
    ever cared about "the workspace" keeps working unchanged — the others are
    reachable through ``sessions`` and are only ever addressed by id.
    """

    def __init__(self, pty_manager: PtyManager | None = None) -> None:
        # Insertion-ordered: this is also the left-to-right order of the tabs,
        # so a workspace never jumps position because something about it
        # changed.
        self._sessions: dict[str, Session] = {}
        self._active: str | None = None
        # Which subscription NEW panes open on is deliberately NOT cached here.
        # An in-memory copy was a second source of truth: once the workspace
        # switcher had written it, a later switch on the app's own Subscriptions
        # page (which only writes the store) never reached this registry, and
        # new panes kept opening on the seat the user had just moved away from.
        # `active_account_id` reads the one persisted store instead.
        # Injectable so tests can drive the registry against a fake PTY pool
        # without a real pseudo-terminal (and without a coding agent installed).
        self._pty: PtyManager | None = pty_manager
        self._lock = asyncio.Lock()
        # Held across reading the state AND writing it — see `_persist` for the
        # interleaving that otherwise loses a freshly discovered conversation id.
        self._persist_lock = asyncio.Lock()
        # (folder, account config dir) pairs already pre-trusted in this process.
        # A workspace of eight panes on one account would otherwise parse and
        # rewrite the same config file eight times — and that file grows to tens
        # of kilobytes on a heavy user (see jarvis.workspace.trust).
        self._pre_trusted: set[tuple[str, str]] = set()
        # One async gate per redirected account. Waiting here consumes no
        # default-executor thread; only the task that owns the gate enters the
        # synchronous setup lock in ``_prepare_spawn``. This prevents a restore
        # burst for one account from starving unrelated ``asyncio.to_thread``
        # work (BUG-043).
        self._account_prepare_locks: dict[str, asyncio.Lock] = {}
        # Admits a few agent cold starts at a time (see COLD_START_LIMIT).
        # Created on first use rather than here: a semaphore belongs to the loop
        # it is first awaited on, and the registry is also built in tests that
        # run each case on a loop of its own.
        self._cold_start: asyncio.Semaphore | None = None
        # Some CLIs serialize badly on one account-scoped runtime store even
        # when the machine has ample CPU. Their registry entry supplies the
        # limit; separate accounts get separate gates, and CLIs with no limit
        # never touch this path.
        self._agent_cold_starts: dict[tuple[str, str], asyncio.Semaphore] = {}

    # ---------------------------------------------------------------- state
    @property
    def session(self) -> Session | None:
        """The workspace on screen, or None while the wizard is showing."""
        if self._active is None:
            return None
        return self._sessions.get(self._active)

    @property
    def sessions(self) -> list[Session]:
        """Every open workspace, in tab order."""
        return list(self._sessions.values())

    @property
    def active_id(self) -> str | None:
        return self._active

    def get(self, workspace_id: str | None) -> Session | None:
        """One workspace by id; without an id, the active one."""
        if workspace_id is None:
            return self.session
        return self._sessions.get(workspace_id)

    def workspaces(self) -> list[dict[str, Any]]:
        """Every open workspace as a tab card, in tab order."""
        return [s.to_card(active=s.id == self._active) for s in self._sessions.values()]

    def state(self) -> dict[str, Any]:
        session = self.session
        return {
            "active": session is not None,
            "session": session.to_dict() if session else None,
            "max_terminals": MAX_TERMINALS,
            "max_workspaces": MAX_WORKSPACES,
            "active_id": self._active,
            "workspaces": self.workspaces(),
            "accounts": self.active_accounts(),
        }

    def brief_state(self) -> dict[str, Any]:
        """The workspace as the voice/tool model reads it — see ``to_brief``."""
        session = self.session
        return {
            "active": session is not None,
            "workspace": session.to_brief() if session else None,
            "max_terminals": MAX_TERMINALS,
            "other_workspaces": [
                {"name": s.name, "terminals": len(s.terminals)}
                for s in self._sessions.values()
                if session is None or s.id != session.id
            ],
        }

    # ------------------------------------------------------------- accounts
    def active_account_id(self, agent: str) -> str | None:
        """Which subscription of ``agent`` the next new pane opens on.

        Always the ONE persisted default (`jarvis.agent_accounts`), never a
        registry-local copy — every surface that switches accounts writes that
        store, so reading anything else lets two surfaces disagree about which
        seat the next pane spends. An id that no longer resolves degrades to
        the built-in login rather than to nothing (``resolve_account`` owns
        that fallback). ``None`` only for something that is not a coding CLI
        with accounts.
        """
        if not has_accounts(agent):
            return None
        return resolve_account(agent, None)

    def active_accounts(self) -> list[dict[str, Any]]:
        """The active subscription of every coding CLI, as the UI shows it.

        Labels rather than ids, because an id is not something anybody can read
        back — "Work seat" is the answer to "which plan does the next terminal
        spend?". The count travels with it so a surface can stay quiet for
        everyone holding a single login and only appear for the few holding two.
        """
        from jarvis import agent_accounts

        rows: list[dict[str, Any]] = []
        for agent in agent_accounts.platforms():
            account_id = self.active_account_id(agent)
            rows.append(
                {
                    "agent": agent,
                    "display_name": AGENT_DISPLAY.get(agent, agent),
                    "active_account": account_id,
                    "active_label": account_label(account_id),
                    "account_count": len(agent_accounts.list_accounts(agent)),  # type: ignore[arg-type]
                }
            )
        return rows

    async def set_active_account(self, agent: str, account_id: str) -> AgentAccount:
        """Point NEW panes of ``agent`` at ``account_id``. This is the switch.

        Nothing that is already open moves. A pane carries the account it was
        created with (see ``resolve_account``), so switching here can never
        re-point a running agent onto a plan whose history has never seen its
        conversation — the same promise the settings surface makes out loud.

        The choice is written through to the stored default as well, so it
        survives a restart and the app's own account page cannot end up
        disagreeing with the workspace about which seat is in use.
        """
        if not has_accounts(agent):
            raise SessionError(f"{agent} has no switchable subscriptions.")
        from jarvis import agent_accounts

        account = await asyncio.to_thread(agent_accounts.resolve, account_id)
        if account is None or account.platform != agent:
            raise SessionError(
                f"{AGENT_DISPLAY.get(agent, agent)} has no account with id {account_id!r}."
            )
        # The store is the ONE place the choice lives (see active_account_id),
        # so a failure to write it means the switch did not happen — surfacing
        # that honestly beats a success answer new panes then contradict.
        try:
            await asyncio.to_thread(agent_accounts.set_active, agent, account.id)  # type: ignore[arg-type]
        except agent_accounts.AccountError as exc:
            raise SessionError(f"The account switch was not saved: {exc}") from exc
        logger.info("Agentic IDE: new {} terminals will use {!r}", agent, account.label)
        return account

    def _manager(self) -> PtyManager:
        if self._pty is None:
            # Lazy: keeps the terminal stack off the import/boot path (AP-26).
            from jarvis.terminal.pty_manager import PtyManager

            self._pty = PtyManager()
        return self._pty

    # -------------------------------------------------------------- session
    async def start(self, folder: str, requested: list[dict[str, Any]]) -> Session:
        """Open ``folder`` as a NEW workspace with one terminal per request entry.

        ``requested`` entries look like ``{"agent": "claude", "name": "Mika"}``;
        the name is optional and filled from the call-sign pool.

        Opening ADDS a workspace and brings it to the front; whatever was open
        stays open with its agents running. The same folder may be opened more
        than once deliberately: each workspace is a separate set of panes and
        conversations, with a distinct tab name.
        """
        async with self._lock:
            if not requested:
                raise SessionError("Pick at least one terminal.")
            if len(requested) > MAX_TERMINALS:
                raise SessionError(
                    f"At most {MAX_TERMINALS} terminals per session (got {len(requested)})."
                )

            # expanduser() is string/env work, not a filesystem call — the real
            # stat below runs in a worker thread.
            root = Path(folder).expanduser()  # noqa: ASYNC240
            try:
                # Off the event loop: on a network share or a spun-down drive a
                # stat can block for seconds, which would stall every other
                # request the server is serving.
                if not await asyncio.to_thread(root.is_dir):
                    raise SessionError(f"Not a folder: {root}")
            except OSError as exc:
                raise SessionError(f"Cannot open {root}: {exc}") from exc

            unknown = {
                str(r.get("agent")) for r in requested if not is_runnable(str(r.get("agent")))
            }
            if unknown:
                raise SessionError(f"Unknown agent(s): {', '.join(sorted(unknown))}")

            missing = sorted(
                {str(r.get("agent")) for r in requested if agent_argv(str(r.get("agent"))) is None}
            )
            if missing:
                raise SessionError(" ".join(_unavailable(m) for m in missing))

            # Call-signs count from T1 WITHIN this workspace, and every
            # workspace counts from T1 again. That is the whole promise of a
            # positional name: what the user sees on screen is what they say.
            # Numbering across tabs instead — a second workspace starting at T5
            # — would keep names globally unique at the price of the one thing
            # this scheme is for, and the ambiguity it would prevent is not
            # real: a spoken call-sign is resolved against the FRONT workspace
            # first, which is the only one the user is looking at.
            pool = free_positions([], len(requested))
            used: set[str] = set()
            terminals: list[Terminal] = []
            for index, entry in enumerate(requested):
                agent = str(entry.get("agent"))
                wanted = str(entry.get("name") or "").strip() or pool[index]
                name = _unique_name(wanted, used)
                used.add(normalize(name))
                requested_account = _requested_account(entry)
                resolved_account = resolve_account(agent, requested_account)
                terminals.append(
                    Terminal(
                        key=normalize(name) or f"t{index}",
                        name=name,
                        agent=agent,
                        display_name=agent_display(agent),
                        index=index,
                        # Columns of WIZARD_COLUMN_HEIGHT, filled top to bottom
                        # before the next one opens — the same arithmetic the
                        # preview draws with (frontend `layout.ts`), so the
                        # workspace that appears is the one that was shown.
                        column=index // WIZARD_COLUMN_HEIGHT,
                        slot=index % WIZARD_COLUMN_HEIGHT,
                        account=resolved_account,
                        # A seat named in the wizard is a deliberate choice and
                        # travels through later splits; one that merely fell to
                        # the active default (or an id that no longer resolves)
                        # is not, and vouches for nothing.
                        account_pinned=requested_account is not None
                        and resolved_account == requested_account,
                    )
                )

            session = await self._open_locked(root, terminals)
            logger.info(
                "Agentic IDE session started: {} terminals in {}",
                len(terminals),
                root,
            )
            return session

    # ------------------------------------------------------- workspace helpers
    def _find_by_folder(self, root: Path) -> Session | None:
        """An open workspace on ``root``, or None.

        Compared on the resolved path so ``~/code/app`` and ``/home/me/code/app``
        are recognised as one folder, and case-insensitively on the platforms
        where the filesystem itself is (Windows, and macOS by default) — asking
        the OS rather than assuming, so a case-sensitive mac volume still gets
        the right answer.
        """
        try:
            wanted = root.expanduser().resolve()
        except OSError:
            wanted = root
        for session in self._sessions.values():
            try:
                candidate = Path(session.folder).resolve()
            except OSError:
                candidate = Path(session.folder)
            if candidate == wanted:
                return session
            if os.path.normcase(str(candidate)) == os.path.normcase(str(wanted)):
                return session
        return None

    def _available_workspace_name(self, wanted: str) -> str:
        """Return a human-readable tab name that is unique in the bar."""
        base = wanted.strip() or "Workspace"
        used = {session.name.casefold() for session in self._sessions.values()}
        if base.casefold() not in used:
            return base
        suffix = 2
        while f"{base} {suffix}".casefold() in used:
            suffix += 1
        return f"{base} {suffix}"

    def _focus_locked(self, session: Session) -> None:
        """Bring ``session`` to the front. Caller holds the lock."""
        self._active = session.id
        session.last_active_at = time.time()

    async def _open_locked(
        self,
        root: Path,
        terminals: list[Terminal],
        *,
        name: str | None = None,
    ) -> Session:
        """Turn a prepared list of panes into a NEW open workspace, at the front.

        Shared by ``start`` and ``restore`` on purpose. Everything a workspace
        needs before its first pane connects lives here exactly once — the
        project probe, the trust pre-seed, the codebase index — so a resumed
        workspace can never quietly differ from a freshly opened one. Both
        callers hold ``self._lock``.
        """
        profile = await asyncio.to_thread(probe_project, root)

        # Pre-seed agent trust for this folder so no terminal stops on a
        # "do you trust this directory?" dialog the user cannot see coming.
        try:
            from jarvis.workspace.trust import ensure_trusted

            await asyncio.to_thread(ensure_trusted, root, sorted({t.agent for t in terminals}))
        except Exception as exc:  # noqa: BLE001 - trust is a convenience
            logger.warning("Agentic IDE: pre-trust failed: {}", exc)

        session = Session(
            id=f"ide_{uuid4().hex[:12]}",
            folder=str(root),
            name=self._available_workspace_name(name or profile.name or root.name or str(root)),
            profile=profile,
            terminals=terminals,
            created_at=time.time(),
            # Both callers prepare panes with legacy (column, slot) positions
            # — the wizard's opening arithmetic, a snapshot's remembered grid
            # — and the columns-of-stacks shape those describe is exactly
            # representable as a tree. A restore that remembered a real tree
            # replaces this afterwards (`_restore_one_locked`).
            layout=layout_tree.from_grid((t.key, t.column, t.slot) for t in terminals),
        )
        self._sessions[session.id] = session
        self._focus_locked(session)
        # Start indexing the codebase NOW, in a background thread, so the
        # first spoken instruction can already point the agent at real files
        # (@path). Deliberately fire-and-forget: nothing waits for it, and a
        # workspace whose walk is still running just gets a prompt without
        # file references (AP-26 — no heavy work on an interactive path).
        try:
            from . import file_index

            file_index.prime_index(str(root))
        except Exception as exc:  # noqa: BLE001 - the index is a convenience
            logger.warning("Agentic IDE: file index not primed: {}", exc)
        # Start watching the panes for the moment they stop working. Here rather
        # than at boot (AP-26): an install whose user never opens the IDE never
        # runs the sweep at all, and the sweep finishes by itself once the last
        # workspace closes.
        try:
            from . import notifications

            notifications.start(self)
        except Exception as exc:  # noqa: BLE001 - the bell is additive
            logger.warning("Agentic IDE: pane notifications not started: {}", exc)
        await self._persist()
        return session

    async def restore(self, snapshot: resume_store.Snapshot) -> RestoreResult:
        """Reopen the workspaces that were OPEN last, starting nothing.

        The panes come back with their call-signs, their coding CLIs, their grid
        coordinates and their resume handles — and in ``pending``, because
        spawning is not this method's job. The grid attaches its panes the way it
        always does, and ``attach`` spends the handles. That keeps ONE place
        where an agent is started; a second spawn path here would drift from it
        the first time either changed. It is also why restoring several
        workspaces is cheap: none of them launches anything until a pane
        connects, and only the workspace on screen has panes mounted.

        All of the last session rather than the front one, because "resume all
        sessions" is what was asked for and somebody with four folders open had
        four — but the LAST SESSION, not the whole file. The store deliberately
        remembers folders closed days ago so a new workspace cannot erase them
        (``resume_store._merged_with_stored``); reopening that archive wholesale
        is what made a restart come back with Tuesday's folders beside today's,
        every one of them carrying the same call-signs out of the same pool, so
        the deduplicator renamed the collisions into "Alex 2" / "Alex 3" and the
        result read as "it duplicated my terminals". ``last_session`` is the
        line between the two; the older folders stay on offer and are reopened
        from the picker, one deliberate click at a time.

        Restoring the same restore point TWICE is a no-op for whatever it
        already brought back. A workspace remembers which record it came from,
        so a stale offer card in a second window cannot open a duplicate of a
        workspace that is on screen right now.

        A workspace that cannot come back does not stop the others: a deleted
        folder is reported with a reason. What could not be restored comes back
        in ``skipped`` so the
        caller can say so out loud instead of quietly returning less than it
        promised. A folder that is already open in a workspace opened by hand is
        NOT one of those cases — two workspaces may share a folder deliberately,
        and the remembered one comes back beside the live one rather than
        replacing agents that are working.

        A pane whose CLI is no longer installed IS restored. It shows up as an
        error the moment it tries to connect, which is a far better outcome than
        silently dropping a terminal the user expects to see.
        """
        async with self._lock:
            if not snapshot.workspaces:
                raise SessionError("There is nothing in that restore point.")
            wanted = self._restore_set_locked(snapshot)
            if not wanted:
                raise SessionError("Everything in that restore point is already open.")

            restored: list[Session] = []
            skipped: list[tuple[str, str]] = []
            was_on_screen: Session | None = None
            for space in wanted:
                try:
                    session = await self._restore_one_locked(space)
                except SessionError as exc:
                    skipped.append((space.folder, str(exc)))
                    continue
                if session is None:
                    continue
                restored.append(session)
                if space.session_id and space.session_id == snapshot.active_session_id:
                    was_on_screen = session

            if not restored and skipped:
                # Nothing at all came back: that is a failure the caller must be
                # able to report as one, not a success with an empty list.
                raise SessionError(skipped[0][1])

            # Back to the tab that was being worked in, not simply the leftmost
            # one. Falls back to the first when the snapshot predates recording
            # it, or when that workspace was one of the ones that could not come
            # back.
            if restored:
                self._focus_locked(was_on_screen or restored[0])
            await self._persist()
            logger.info(
                "Agentic IDE resumed {} workspace(s), {} terminal(s); {} skipped",
                len(restored),
                sum(len(s.terminals) for s in restored),
                len(skipped),
            )
            return RestoreResult(sessions=restored, skipped=skipped)

    def _restore_set_locked(
        self, snapshot: resume_store.Snapshot
    ) -> list[resume_store.SnapshotWorkspace]:
        """Which remembered workspaces this restore should actually reopen.

        Three things are dropped here, and each of them showed up on screen as a
        duplicated terminal:

        1. **Folders that were merely remembered**, not open at the last save.
           See ``Snapshot.last_session`` for why the file holds both.
        2. **Records already restored in this process** — a second click on a
           stale offer card must recognise what is on screen, not open it again.
           Two checks, because restoring rewrites the file: right after a
           restore the record still names the id it was restored FROM, and once
           the workspace has saved itself it names the live workspace's own id.
        3. **The same record twice inside one file**, which a merge could leave
           behind. Two workspaces sharing a folder are legitimate and keep
           distinct ids, so only a genuinely identical record collapses.

        Caller holds ``self._lock``.
        """
        already = {s.restored_from for s in self._sessions.values() if s.restored_from}
        wanted: list[resume_store.SnapshotWorkspace] = []
        seen: set[str] = set()
        for space in snapshot.last_session():
            key = _restore_key(space)
            if key in already or space.session_id in self._sessions:
                logger.info(
                    "Agentic IDE: {} is already open from this restore point — not reopening it",
                    space.folder,
                )
                continue
            if key in seen:
                continue
            seen.add(key)
            wanted.append(space)
        earlier = len(snapshot.workspaces) - len(snapshot.last_session())
        if earlier:
            logger.info(
                "Agentic IDE: {} remembered workspace(s) predate the last session — "
                "left on offer instead of reopened",
                earlier,
            )
        return wanted

    async def _restore_one_locked(self, space: resume_store.SnapshotWorkspace) -> Session | None:
        """Reopen one remembered workspace. Caller holds the lock."""
        root = Path(space.folder).expanduser()  # noqa: ASYNC240
        try:
            if not await asyncio.to_thread(root.is_dir):
                raise SessionError(
                    f"{root} is no longer on this machine — that workspace cannot be reopened."
                )
        except OSError as exc:
            raise SessionError(f"Cannot open {root}: {exc}") from exc

        def _restored(index: int, entry: resume_store.SnapshotTerminal) -> Terminal:
            # The remembered account, re-validated: a pane must come back on
            # the subscription whose history holds its conversation, and an
            # account deleted in the meantime falls back to the active one
            # rather than failing the reopen.
            account = resolve_account(entry.agent, entry.account)
            return Terminal(
                key=entry.key or normalize(entry.name) or f"t{index}",
                name=entry.name,
                agent=entry.agent,
                display_name=agent_display(entry.agent),
                index=index,
                history_id=entry.history_id or uuid4().hex,
                column=entry.column,
                slot=entry.slot,
                resume=entry.resume,
                prompts_sent=entry.prompts_sent,
                resume_continuation_needed=entry.continuation_needed,
                account=account,
                # The pin survives the restart only while the seat it vouches
                # for does — a fallback onto the active account is not the
                # choice the snapshot remembered.
                account_pinned=entry.account_pinned and account == entry.account,
            )

        terminals = [_restored(index, entry) for index, entry in enumerate(space.terminals)]
        # Which of them will come back mid-task, decided HERE rather than when
        # each pane's agent happens to start.
        #
        # **The bug this fixes.** `continuation_pending` used to be raised in
        # `attach`, which is the moment a pane's process is spawned — and cold
        # starts are deliberately staggered (see COLD_START_LIMIT), so in a
        # workspace of a dozen panes most of them are still `pending` seconds
        # after the grid appears. Anybody pressing "Continue" in that window got
        # the handful that had started and silently no others, which is exactly
        # the "it skips terminals that should have carried on" report.
        #
        # A restored pane's answer does not depend on its process at all: it
        # depends on whether the coding CLI's history really holds the
        # conversation the handle points at. That is knowable now, so it is
        # answered now — one thread hop for the whole workspace, since each
        # check is a filename lookup. `attach` still corrects it either way when
        # the process really starts (a resume that fails clears it).
        await asyncio.to_thread(_mark_restored_continuations, terminals)
        # A snapshot remembers the call-signs each workspace had. Another one may
        # hold them now, and two panes answering to one name would make every
        # spoken instruction ambiguous — so a collision is renamed here. Only the
        # label moves; the resume handle underneath it continues the conversation.
        self._dedupe_names(terminals)
        session = await self._open_locked(root, terminals, name=space.name or None)
        # Which record this came back from, so a second restore of the same file
        # recognises it rather than opening a duplicate.
        session.restored_from = _restore_key(space)
        # The remembered split tree, when the snapshot carries one and it
        # parses. `_open_locked` already built the coarse columns-of-stacks
        # equivalent from the legacy (column, slot) pairs, so a snapshot from
        # an older build — or a truncated tree — degrades to the shape those
        # hints describe instead of failing the reopen.
        if space.layout is not None:
            try:
                session.layout = layout_tree.from_dict(space.layout)
            except ValueError as exc:
                logger.warning(
                    "Agentic IDE: the remembered layout of {} is unreadable, "
                    "restoring its panes on the coarse grid instead: {}",
                    space.folder,
                    exc,
                )
        # Pack the grid: a snapshot can carry gaps if it was written between a
        # close and its renumbering (or a remembered tree can disagree with the
        # panes that really came back), and `_renumber` settles both.
        self._renumber(session)
        return session

    @staticmethod
    def _dedupe_names(terminals: list[Terminal]) -> None:
        """Give any two panes of ONE workspace that share a call-sign one each.

        Scoped to the workspace being restored, because that is the scope a
        positional call-sign lives in: T1 in one tab and T1 in another are two
        different panes the user addresses by looking at one of them, and
        renaming across tabs would take numbers away from a workspace that has
        every right to them.

        A repeated POSITION is repaired with the lowest free number rather than
        a suffix: "T1 2" is neither speakable nor a position, so a snapshot
        that somehow carried two T1s would otherwise produce a pane nobody can
        address. A repeated CUSTOM name keeps the old suffix behaviour.
        """
        used: set[str] = set()
        for term in terminals:
            unique = _unique_name(term.name, used)
            if unique != term.name:
                term.name = unique
                term.key = normalize(unique) or term.key
            used.add(normalize(term.name))

    async def activate(self, workspace_id: str | None) -> Session | None:
        """Bring one workspace to the front, or clear the front entirely.

        ``None`` means "no workspace is on screen" — what the UI is in while the
        wizard is open for an ADDITIONAL workspace. It is a real state, not a
        close: every workspace stays open with its agents running, and the panes
        that go off screen simply let go of their viewers.

        Answering before the panes come down is what makes a switch safe: the
        panes of the outgoing workspace disconnect *after* this call, by which
        time it is no longer the front one, and nothing treats their departure
        as a reason to stop an agent.
        """
        async with self._lock:
            if workspace_id is None:
                self._active = None
                return None
            session = self._sessions.get(workspace_id)
            if session is None:
                raise SessionError("That workspace is not open any more.")
            self._focus_locked(session)
            # The front workspace is the one worth offering back after a
            # restart, so switching re-points the restore snapshot at it.
            await self._persist()
            logger.info("Agentic IDE: switched to {}", session.folder)
            return session

    async def rename(self, workspace_id: str, name: str) -> Session:
        """Rename one workspace tab without changing its folder or agents."""
        cleaned = " ".join(name.split()).strip()
        if not cleaned:
            raise SessionError("Give the workspace a name.")
        if len(cleaned) > 80:
            raise SessionError("Workspace names can be at most 80 characters.")
        async with self._lock:
            session = self._sessions.get(workspace_id)
            if session is None:
                raise SessionError("That workspace is not open any more.")
            if any(
                other.id != workspace_id and other.name.casefold() == cleaned.casefold()
                for other in self._sessions.values()
            ):
                raise SessionError("Another workspace already uses that name.")
            session.name = cleaned
            await self._persist()
            return session

    async def end(self, workspace_id: str | None = None) -> bool:
        """Close one workspace and stop every agent in it.

        Without an id this closes the workspace on screen, which is what the
        toolbar's Close button and the existing CLI/API callers mean. Returns
        False when there was nothing to close.

        Closing does NOT withdraw the restore point. Closing for the day and
        picking the same folders up tomorrow is the main thing resuming is FOR,
        so the snapshot written while these workspaces were open stays exactly
        as it is. Only the user asking to start fresh discards it.
        """
        async with self._lock:
            target = workspace_id if workspace_id is not None else self._active
            if target is None or target not in self._sessions:
                return False
            await self._close_locked(target)
            return True

    async def close_all(self) -> int:
        """Close every open workspace. Returns how many were closed.

        The restore point survives — see ``end``.
        """
        async with self._lock:
            count = len(self._sessions)
            for workspace_id in list(self._sessions):
                await self._close_locked(workspace_id)
            return count

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> resume_store.Snapshot | None:
        """EVERY open workspace, in the form the resume store keeps it.

        All of them, front one first. An earlier version remembered only the
        workspace on screen, on the reasoning that bringing back all of them
        would relaunch a folder's worth of coding agents per tab unasked. Both
        halves of that were wrong: the user asked for everything back, and
        restoring costs nothing — ``restore`` starts no agent, and only the
        workspace on screen has panes mounted. Five workspaces in the bar are
        five folders waiting, not five folders' worth of running agents.

        Returns None only when nothing is open at all, which leaves whatever was
        stored before untouched — closing the last workspace must not erase the
        thing the user wants back tomorrow.
        """
        if not self._sessions:
            return None
        # TAB ORDER, not front-first. The bar has to come back arranged the way
        # it was left, and the tab somebody was working in is not necessarily the
        # leftmost one — so which was on screen is recorded separately instead of
        # being implied by position.
        return resume_store.snapshot_now(
            [
                resume_store.SnapshotWorkspace(
                    session_id=session.id,
                    folder=session.folder,
                    name=session.name,
                    terminals=[t.to_snapshot() for t in session.terminals],
                    layout=layout_tree.to_dict(session.layout) if session.layout else None,
                )
                for session in self._sessions.values()
            ],
            active_session_id=self._active or "",
        )

    async def _persist(self) -> None:
        """Record the workspace so it can be offered back later.

        Best-effort and off the event loop. A resume point is a convenience;
        failing to write one must never break the workspace that is running
        perfectly well right now.

        **Reading the state and writing it are one indivisible step.** Without
        that they interleave, and the interleaving loses exactly the valuable
        part: a pane connecting collects the state and then hands it to a thread,
        and if the background lookup finds a Codex conversation id in that gap and
        writes it, the older collected state lands afterwards and erases it.
        Serialising build-and-write means a later save always reads a state newer
        than the one the previous save stored.
        """
        async with self._persist_lock:
            snapshot = self.snapshot()
            if snapshot is None:
                return
            try:
                await asyncio.to_thread(resume_store.save, snapshot)
            except Exception as exc:  # noqa: BLE001 - the workspace comes first
                logger.warning("Agentic IDE: resume snapshot not written: {}", exc)

    async def persist_resume_activity(self) -> None:
        """Checkpoint activity evidence used by the interrupted-work offer.

        The activity sweep calls this only when a pane crosses a meaningful
        boundary, never for each terminal repaint. Keeping it on the registry
        preserves the snapshot lock and last-writer ordering of `_persist`.
        """
        await self._persist()

    async def _forget(self) -> None:
        """Withdraw the resume offer, best-effort."""
        try:
            await asyncio.to_thread(resume_store.clear)
        except Exception as exc:  # noqa: BLE001 - closing must always succeed
            logger.warning("Agentic IDE: resume snapshot not cleared: {}", exc)

    async def _close_locked(self, workspace_id: str) -> None:
        """Tear ONE workspace down: stop its agents and drop it from the bar.

        This is the only place an agent is stopped on the user's behalf, and
        that is deliberate. Panes come and go constantly — a switch to another
        workspace, a browser reload, a closed tab — and none of those mean "stop
        working". Closing does.

        The restore point is re-pointed rather than withdrawn: whatever is still
        open moves to the front and writes its own snapshot, so the next restart
        offers a workspace that actually exists. Closing the LAST one withdraws
        it (in ``end``), because re-offering something deliberately shut down is
        the kind of prompt people learn to dismiss without reading.
        """
        session = self._sessions.pop(workspace_id, None)
        if session is None:
            return
        for task in list(session.lookups):
            task.cancel()
        session.lookups.clear()

        manager = self._pty
        for term in session.terminals:
            term.stopping = True  # deliberate kills, not crashed resumes
            term.viewer_output = None
            term.viewer_exit = None
            term.watchers.clear()
            term.prompt_viewers.clear()
        if manager is not None:
            for term in session.terminals:
                if term.pty_id:
                    try:
                        manager.close(term.pty_id)
                    except Exception:  # noqa: BLE001, S110 - best-effort teardown
                        pass
        # Its pane notifications go with it. Each one is a "jump to this pane"
        # button, and the panes have just been killed — an entry that quietly
        # does nothing when pressed is worse than one that is gone.
        try:
            from . import notifications

            notifications.center().forget_workspace(workspace_id)
            notifications.watcher().forget_workspace(workspace_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not fail on this
            logger.warning("Agentic IDE: could not clear notifications for a closed tab: {}", exc)

        # Drop THIS folder's codebase index. A blanket reset would take the
        # other open workspaces' indexes with it and silently cost them their
        # `@file` suggestions.
        # Workspaces may share a folder. Its index remains useful until the last
        # workspace using that folder closes.
        if self._find_by_folder(Path(session.folder)) is None:
            try:
                from . import file_index

                file_index.forget_index(session.folder)
            except Exception:  # noqa: BLE001, S110 - best-effort teardown
                pass

        if self._active == workspace_id:
            # Hand the front to the most recently used survivor rather than to
            # whatever happens to be first: closing the tab you were in should
            # land you on the one you were in before it, not at the far end.
            survivor = max(
                self._sessions.values(),
                key=lambda s: s.last_active_at,
                default=None,
            )
            self._active = survivor.id if survivor else None
            if survivor is not None:
                self._focus_locked(survivor)
        # Deliberately NOT re-written here. The restore point is refreshed by
        # activity — opening a workspace, adding a pane, connecting one — and
        # closing is not activity. Rewriting on close made the offer shrink one
        # workspace at a time: closing four of four left a restore point holding
        # one, which is the shape of "I closed everything for the day and got a
        # third of it back tomorrow". The cost of the other direction is a
        # workspace that lingers in the offer until something else happens, and
        # reopening one workspace too many is trivially undone.
        logger.info("Agentic IDE session ended: {}", session.id)

    def set_focus_mode(self, enabled: bool) -> bool:
        """Turn the focused coding mode on/off. Returns the resulting state.

        Also swaps the assistant's *character* to the built-in ``coding`` mode,
        because "focused coding mode" was only ever half a mode: it added the
        workspace facts to the prompt but left the tone alone. It is applied as
        a SECTION OVERRIDE, which is in-memory and never persisted — so this
        cannot overwrite the mode the user actually chose, and cannot survive a
        restart the way the old sticky focus flag did.
        """
        session = self.session
        if session is None:
            if enabled:
                raise SessionError("No Agentic-IDE session is running — open one first.")
            self._sync_coding_character(False)
            return False
        session.focus_mode = bool(enabled)
        logger.info("Agentic IDE focus mode {}", "on" if enabled else "off")
        self._sync_coding_character(session.focus_mode)
        return session.focus_mode

    @staticmethod
    def _sync_coding_character(enabled: bool) -> None:
        """Point the persona layer at the ``coding`` mode while focus mode is on.

        Best-effort by design: the workspace context block is the part users
        depend on, so a missing or renamed ``coding`` mode costs the tone and
        nothing else.
        """
        try:
            from jarvis.brain import modes

            modes.set_section_override(modes.MODE_CODING if enabled else None)
        except Exception as exc:  # noqa: BLE001 - never fail a toggle on the tone
            logger.debug("Coding-mode character not applied: {}", exc)

    def set_surface_context(
        self,
        *,
        workspace_id: str,
        view: str,
        on_screen: bool,
        terminal: str | None,
        prompt_target: str | None = None,
    ) -> bool:
        """Record which view the active UI shows, and the pane it stages.

        A stale grid can finish a request after the user changed workspace, so
        only the active workspace may write this context. Invalid or hidden
        selections clear the default rather than leaving a believable old one.

        An off-screen section reports the grid regardless of what it was last
        showing. That is not cosmetic: a section the user navigated away from
        must not keep answering "this terminal" with a pane that is behind
        whatever they are looking at now.
        """
        session = self.session
        if session is None or session.id != workspace_id:
            return False
        session.surface_on_screen = bool(on_screen)
        session.surface_view = coerce_view(view) if session.surface_on_screen else VIEW_GRID
        selected = session.find(terminal or "") if session.stages_one_pane() else None
        session.surface_terminal = selected.name if selected is not None else ""
        prompt = session.find(prompt_target or "") if session.surface_on_screen else None
        session.surface_prompt_target = (
            prompt.name if prompt is not None and accepts_prompts(prompt.agent) else ""
        )
        return True

    # ------------------------------------------------------------------ pty
    def _locate(self, key: str, workspace_id: str | None) -> tuple[Session, Terminal] | None:
        """One pane and the workspace holding it, by call-sign.

        ``workspace_id`` addresses a specific workspace — which every PTY-level
        caller passes, because its socket belongs to the workspace it opened in
        and not to whichever one happens to be at the front by the time a
        message arrives. Without one, the front workspace answers.
        """
        session = self.get(workspace_id)
        if session is None:
            return None
        term = session.find(key)
        return None if term is None else (session, term)

    @asynccontextmanager
    async def _cold_start_slot(self) -> AsyncIterator[None]:
        """Hold one of the few slots for starting an agent CLI.

        Waiting here is what turns "open a workspace" from a burst that pins
        every core into a rolling start (see :data:`COLD_START_LIMIT`). It
        gates STARTS only: a pane re-joining an agent that never stopped — the
        common case on every workspace switch — returns long before this, and
        an agent already running is never made to wait behind one that is
        booting.

        The slot is released a moment AFTER the block, not at its end. What
        costs is the CLI loading itself and its servers, and by then ``spawn``
        has returned; releasing immediately would let the whole grid through in
        the same instant and the limit would gate nothing. A spawn that FAILED
        releases at once — nothing is loading, and a broken pane must not hold
        up the ones behind it.
        """
        gate = self._cold_start
        if gate is None:
            # No await between the check and the assignment, so two panes
            # arriving together cannot end up with a semaphore each.
            gate = self._cold_start = asyncio.Semaphore(COLD_START_LIMIT)
        await gate.acquire()
        started = False
        try:
            yield
            started = True
        finally:
            if started:
                asyncio.get_running_loop().call_later(COLD_START_SETTLE_S, gate.release)
            else:
                gate.release()

    async def _acquire_agent_cold_start(self, term: Terminal) -> asyncio.Semaphore | None:
        """Take this CLI/account's boot slot when its registry entry needs one.

        The machine-wide gate protects CPU and process count. This narrower gate
        protects a vendor runtime store: Codex resumes that are fast alone can
        block one another for minutes when they initialize against the same
        account database concurrently. The caller releases the slot only after
        the pane's actual input line appears (or the readiness timeout expires).
        """
        spec = workspace_agents.get_agent(term.agent)
        if not term.resumed:
            return None
        limit = max(0, spec.resume_start_limit if spec is not None else 0)
        if limit == 0:
            return None
        key = (term.agent, term.account or "")
        gate = self._agent_cold_starts.get(key)
        if gate is None:
            # No await between lookup and assignment: concurrent mounts cannot
            # create two independent gates for one account.
            gate = self._agent_cold_starts[key] = asyncio.Semaphore(limit)
        await gate.acquire()
        return gate

    async def attach(
        self,
        key: str,
        cols: int,
        rows: int,
        on_output: Any,
        on_exit: Any,
        workspace_id: str | None = None,
        appearance: str | None = None,
        on_replay: Any = None,
        claim_owner: bool = True,
    ) -> Terminal:
        """Point a viewer at terminal ``key`` — one attach at a time per pane.

        The whole of :meth:`_attach_locked` runs under the pane's OWN lock:
        everything about a pane's agent that must be true exactly once is
        decided in there, across three awaits, and concurrent attaches are not a
        rare race but the ordinary case (a restored workspace reconnects every
        pane at once, and each retry while it is still opening is one more
        socket). See ``Terminal.attach_lock`` for what walking through that gap
        cost.

        Resolving the pane BEFORE taking the lock is deliberate: an unknown
        call-sign and a workspace that is not open yet are answers this can give
        immediately, and they are exactly what a burst of reconnecting panes
        asks for. ``_attach_locked`` resolves again under the lock, because the
        workspace may have closed while this attempt waited its turn.

        ``on_replay`` receives the re-joined screen (see :meth:`_attach_locked`)
        and exists so a viewer can tell it apart from live output. Omitted, the
        replay goes to ``on_output`` — correct for an internal caller that only
        wants the bytes, and wrong for a viewer that draws them, which is why
        the socket route passes one.
        """
        found = self._locate(key, workspace_id)
        if found is None:
            if self.get(workspace_id) is None:
                raise SessionNotReady("No Agentic-IDE session is running.")
            raise SessionError(f"Unknown terminal: {key}")
        _session, term = found
        async with term.attach_lock:
            return await self._attach_locked(
                key,
                cols,
                rows,
                on_output,
                on_exit,
                workspace_id=workspace_id,
                appearance=appearance,
                on_replay=on_replay,
                claim_owner=claim_owner,
            )

    async def _attach_locked(
        self,
        key: str,
        cols: int,
        rows: int,
        on_output: Any,
        on_exit: Any,
        workspace_id: str | None = None,
        appearance: str | None = None,
        on_replay: Any = None,
        claim_owner: bool = True,
    ) -> Terminal:
        """Point a viewer at terminal ``key``, starting its agent if needed.

        Caller holds ``term.attach_lock`` — see :meth:`attach`. Nothing here may
        run without it: the gap between "is a process already running?" below
        and ``term.pty_id`` being recorded at the end is what a second
        concurrent attach used to start a duplicate agent through.

        ``on_output(text)`` / ``on_exit(code)`` are awaited in this loop. The
        transcript is fed here, so it keeps filling even if the UI pane is
        closed and reconnects later.

        ``appearance`` is the light/dark ground the viewer draws this pane on.
        It is answered to the agent's CLI when it asks for the screen colours,
        so a CLI on a light pane picks a palette for paper rather than for
        slate. Omitted (an internal re-attach), whatever the last viewer said
        stands.

        **A running agent is re-joined, never restarted.** A pane whose PTY is
        still alive — the normal case after switching workspaces, reloading the
        browser, or coming back to the section — hands its output to the new
        viewer and replays what it has been printing meanwhile, so the screen
        comes back as it was. Restarting instead would throw away work in
        progress every time somebody looked away, which is precisely what having
        several workspaces would otherwise cost.

        A replay is valid only at the geometry that produced its cursor moves.
        When the viewer comes back at another size, the old drawing is replaced
        by its terminal-mode prologue and a live repaint. This is still the same
        process and conversation; only the stale pixels are discarded.

        **That replay goes out on ``on_replay``, not on ``on_output``, because a
        viewer has to CLEAR its screen before drawing it.** The two are the same
        bytes and completely different instructions: live output continues a
        screen, a replay REBUILDS one. A viewer that appended it instead drew
        the agent's interface a second time over the copy already there — and
        because an Ink TUI skips unchanged cells with cursor moves rather than
        overwriting them with spaces, the two copies did not stack tidily, they
        interleaved character by character ("plus everything new" came back as
        "plueverythingwnew"). Reported 2026-07-29 across three panes; every
        reconnect made it worse and nothing ever repaired it, because the agent
        only ever redraws its own visible rows and never the scrollback above
        them. Omitting ``on_replay`` keeps the old single-channel behaviour, for
        internal callers that consume bytes rather than paint them.

        **This is also where a conversation is continued rather than restarted.**
        A pane holding a resume handle launches its CLI with the arguments that
        reopen that conversation; a pane without one starts fresh and keeps
        whatever handle the launch minted. Putting it here rather than in a
        dedicated "resume" path is deliberate — every way a pane can come back
        (reopening the browser, restoring a snapshot, pressing restart on a dead
        pane) already goes through this one method, so all three continue the
        conversation and none of them can drift from the others.

        A resume can fail: the CLI may have pruned that conversation, or it may
        never have had a first message. The agent then prints an error and dies
        within a second, so an early non-zero exit after a resume drops the
        handle and starts the pane fresh — once. The pane comes back empty
        instead of dead, and ``resumed`` says which of the two happened.
        """
        found = self._locate(key, workspace_id)
        if found is None:
            if self.get(workspace_id) is None:
                # Not a refusal — a "not yet". A viewer may wait for this.
                raise SessionNotReady("No Agentic-IDE session is running.")
            raise SessionError(f"Unknown terminal: {key}")
        session, term = found
        if cols < MIN_VIEWER_COLS or rows < MIN_VIEWER_ROWS:
            # A handshake tile too narrow for the agent to draw in, which is how
            # a whole conversation ends up printed one character per line (the
            # resize path refuses the same sizes — see the floors' comment).
            # Fall back to the geometry the pane already has: for a live agent
            # that means "no geometry change", for a fresh spawn the
            # transcript's default.
            #
            # Floored, because that fallback is not always sound: a pane
            # squeezed by an earlier crowded grid carries a broken geometry of
            # its own, and handing it back here would reconnect the agent to
            # the very strip it stopped drawing in. Reattaching is the moment
            # such a pane can be put right.
            #
            # From the PTY's own geometry rather than the transcript's, which
            # only agrees with it above `screen.MIN_COLS` (see `pty_cols`). A
            # pane that has never spawned has no PTY geometry, and there the
            # transcript's default is exactly the right answer.
            cols = max(term.pty_cols or term.transcript.cols, MIN_VIEWER_COLS)
            rows = max(term.pty_rows or term.transcript.rows, MIN_VIEWER_ROWS)
        if appearance in THEME_COLOURS:
            term.queries.appearance = appearance

        manager = self._manager()
        if term.pty_id and manager.has(term.pty_id):
            # The agent never stopped. A foreground viewer takes over the owner
            # slot; a background viewer only joins the output fanout. A viewer
            # that is being replaced may still be TIDYING UP — see ``detach``,
            # which is what stops that tidy-up from clearing the live owner.
            #
            # Winning the slot is about OWNERSHIP (the size, the handover), not
            # about who may look: a viewer that was here first keeps receiving
            # this pane's output until its own socket goes away.
            owns_viewer = _watch(
                term,
                on_output,
                on_exit,
                cols,
                rows,
                claim_owner=claim_owner,
            )
            term.reattached = True
            term.stopping = False
            geometry_changed = owns_viewer and (
                (term.transcript.cols, term.transcript.rows) != (cols, rows)
            )
            if geometry_changed:
                geometry_changed = manager.resize(term.pty_id, cols, rows)
                if geometry_changed:
                    term.pty_cols, term.pty_rows = cols, rows
                    term.transcript.resize(cols, rows)
                    # The TUI is about to redraw itself for the new geometry;
                    # that redraw must not read as the agent working.
                    term.last_resize_at = time.time()
            needs_repaint = term.replay.truncated
            if geometry_changed and is_coding_agent(term.agent):
                # A cursor-addressed TUI stream is meaningful only at the size
                # that produced it. Replaying the old geometry after a grid
                # re-layout leaves status rows and command fragments behind
                # the new paint. Keep the terminal modes, drop those drawing
                # bytes, and let the live agent rebuild one clean screen below.
                replay = term.replay.rebase_for_resize()
                needs_repaint = True
            else:
                replay = term.replay.text()
            if replay:
                # Hand over either the stream that drew the current screen, or
                # (after a geometry change) the terminal-mode prologue that a
                # clean repaint must draw on. A coding agent's TUI is a painted
                # surface, not a log: the viewer needs one of those two rebuild
                # paths rather than an append to whatever it held before.
                #
                # On the replay channel when the viewer offered one — see the
                # docstring for what appending it to a screen that already had
                # a copy of it looked like.
                await (on_replay or on_output)(replay)
            if needs_repaint:
                # Either the tail lost its opening frame, or its cursor moves
                # belong to another geometry. Neither can rebuild this viewer.
                # Ask for a fresh paint instead of hoping one arrives.
                await self._nudge_repaint(term, cols, rows)
            logger.debug("Agentic IDE: {} re-joined a running agent", term.name)
            return term

        argv = agent_argv(term.agent)
        if argv is None:
            term.status = "error"
            term.error = f"{term.display_name} is not on PATH."
            raise SessionError(term.error)

        # A handle is a pointer, and it has to be dereferenced before it is
        # spent. Being handed an id at launch does not create a conversation:
        # a pane that was opened and never given an instruction leaves nothing
        # behind, and asking the CLI to resume that id makes it print "no
        # conversation found" and die. Measured on a real workspace — twelve
        # panes opened, none prompted, twelve dead panes on the way back.
        # Every history lookup below is scoped to the pane's OWN account: a pane
        # on the second subscription keeps its transcripts in that account's
        # directory, and asking the default one would report "no conversation"
        # for a conversation that is right there.
        # Off the event loop, because it is a directory walk and not a stat: the
        # check searches the CLI's history BY ID across every project folder it
        # has ever written (`agent_sessions._claude_conversation_exists`), which
        # on a long-lived install is hundreds of folders — measured here at
        # 9 ms per pane over 541 of them. Run inline it froze the whole server
        # for that long, once per restored pane, at the one moment the loop is
        # busiest: a restore mounts every pane in one commit, so a dozen panes
        # meant a dozen stalls interleaved with their own spawns. It only ever
        # runs on a pane that HAS a handle, which is why the restore path was
        # the only one that ever felt it. `_mark_restored_continuations` already
        # takes the same call to a thread for the same reason.
        home = account_home(term.agent, term.account)
        continuing = resume_argv(term.agent, term.resume)
        if continuing is not None and not await asyncio.to_thread(
            has_conversation, term.agent, term.resume, home
        ):
            logger.info(
                "Agentic IDE: {} has no conversation to continue — starting fresh",
                term.name,
            )
            term.resume = None
            continuing = None
        if continuing is not None:
            argv = (*argv, *continuing)
            term.resumed = True
        else:
            if term.resume is None and term.prompts_sent and can_resume(term.agent):
                # A pane that was WORKED IN and still has no conversation id is
                # the one failure this path used to swallow whole: the pane came
                # back looking right, empty, with nothing anywhere saying its
                # history had been lost. It means every lookup missed — see
                # `CONVERSATION_DELAYS_S` — so say so where the next person
                # debugging this will look.
                logger.info(
                    "Agentic IDE: {} was worked in but no conversation id was ever "
                    "recorded for it — starting fresh (the old thread is still in "
                    "{}'s own history, just not reachable from here)",
                    term.name,
                    term.display_name,
                )
            extra, minted = launch_extra(term.agent)
            argv = (*argv, *extra)
            term.resumed = False
            if minted is not None:
                term.resume = minted
        # A process that inherits a conversation inherits whatever it was in the
        # middle of, and then waits. That is the whole reason this flag exists —
        # see the field. A fresh start clears it, so a pane that failed its
        # resume and came back empty is not reported as waiting to be nudged.
        # A valid conversation may already be finished or waiting for input.
        # Offer a nudge only when the previous live pane was observed working.
        # A Continue claimed while this pane was still pending stays claimed:
        # raising the flag again during attach would let a second click enqueue
        # the same nudge while the first is waiting for the input line.
        term.continuation_pending = (
            term.resumed and term.resume_continuation_needed and not term.continue_when_ready
        )
        if not term.resumed:
            term.resume_continuation_needed = False

        # Everything below belongs to a new process attempt. Reset this before
        # spawning so neither early output nor a concurrent notification sweep
        # can inherit the previous PTY's settled-screen evidence.
        term.process_generation += 1
        term.idle_seen = False
        term.transcript.resize(cols, rows)
        # Readiness belongs to this process. Keeping the dead process's screen
        # here leaves old prompt sigils visible to the readiness probe and makes
        # a freshly spawned CLI look writable before it has emitted one byte.
        term.transcript.clear()
        # A fresh process draws a fresh screen: anything the previous one left
        # in the replay buffer belongs to a terminal that no longer exists, and
        # replaying it to the next viewer would show output from a dead agent.
        term.replay.clear()
        _watch(term, on_output, on_exit, cols, rows)
        term.reattached = False
        # This pane is wanted again, so the last deliberate kill is history.
        term.stopping = False
        # Monotonic: a wall clock can jump (NTP, a laptop waking up) and would
        # then mis-measure how long the agent survived.
        spawned_at = time.monotonic()
        recovered = False

        # Both callbacks go through the pane's viewer SLOT rather than through
        # the on_output/on_exit captured here. The PTY outlives its viewers —
        # that is what makes switching workspaces safe — so a closure pinned to
        # the viewer that happened to start the agent would keep writing into a
        # dead socket forever, and the viewer that came later would see nothing.
        async def _output(tid: str, text: str) -> None:
            term.transcript.feed(text)
            term.replay.feed(text)
            term.last_output_at = time.time()
            # To EVERY viewer, not only the newest one. A pane open in two
            # places has two screens and both are supposed to show the same
            # agent; sending to one of them is how a window ends up frozen
            # while the work behind it runs on.
            for viewer in _viewers(term):
                await viewer(text)

        async def _closed(_tid: str, code: int) -> None:
            nonlocal recovered
            term.pty_id = None
            died_young = time.monotonic() - spawned_at < RESUME_FAILED_WINDOW_S
            # Only a FAILED early exit is blamed on the resume. Quitting an
            # agent normally exits 0, and a pane we killed ourselves reports a
            # failure exit that looks identical to a crash — restarting either
            # of those would be its own bug.
            if term.resumed and not term.stopping and not recovered and died_young and code != 0:
                recovered = True
                logger.warning(
                    "Agentic IDE: {} could not continue its previous "
                    "conversation (exit {}) — starting it fresh instead",
                    term.name,
                    code,
                )
                term.resume = None
                term.resumed = False
                try:
                    await self.attach(
                        key,
                        cols,
                        rows,
                        term.viewer_output or on_output,
                        term.viewer_exit or on_exit,
                        workspace_id=session.id,
                    )
                except SessionError as exc:
                    logger.warning("Agentic IDE: {} could not be restarted: {}", term.name, exc)
                else:
                    # The pane is alive again; telling the viewer it exited
                    # would flash a dead pane for no reason.
                    return
            term.status = "exited"
            term.exit_code = code
            for viewer in _exit_viewers(term):
                await viewer(code)

        # BEFORE the slot, not inside it — and that ordering is the whole point.
        #
        # Off the event loop either way: getting the pane's account ready is
        # filesystem work (a few stat calls once it is in place — see
        # `_prepare_spawn`). But it also takes that account's setup lock, and
        # every pane of one account shares one config dir and therefore one
        # lock. Held inside the slot, a pane that is merely QUEUED on that lock
        # still occupies one of the few cold-start slots while doing nothing —
        # so a workspace of panes on the same account collapsed a gate meant to
        # admit COLD_START_LIMIT starts at once down to roughly one, and the
        # restore came back one terminal at a time.
        #
        # Serializing here is not the pile-up the gate exists to prevent. That
        # pile-up is coding CLIs BOOTING — plugins, hooks, an `npx` process tree
        # per MCP server. The account gate is async so waiters do not occupy the
        # shared executor while one thread owns the filesystem setup lock.
        redirected_home = _redirected_home(term)
        # Recorded ON THE PANE, not only raised. A refusal here is the SAME kind
        # of "this cannot start" as a missing binary or a spawn that threw, and
        # both of those mark the pane before they raise. Left unmarked, the pane
        # stayed `pending` — and `pending` has a headline of its own that reads
        # "waiting for terminal connection", which is a promise: it says the
        # terminal is about to connect. Nothing was ever going to connect. The
        # one refusal that reaches this path (an unconfigured launch profile —
        # see `agent_spawn_overlay`) therefore showed the user a pane that
        # claimed to be starting forever, with the actual reason living only in
        # a socket frame the pane painted over a moment later.
        try:
            if redirected_home is None:
                env = await asyncio.to_thread(self._prepare_spawn, term, session.folder)
            else:
                account_key = os.path.normcase(str(redirected_home))
                account_gate = self._account_prepare_locks.setdefault(account_key, asyncio.Lock())
                async with account_gate:
                    env = await asyncio.to_thread(self._prepare_spawn, term, session.folder)
        except SessionError as exc:
            term.status = "error"
            term.error = str(exc)
            raise

        # The provider/account gate is acquired BEFORE the machine-wide gate.
        # A Codex pane waiting on shared state must never occupy a CPU slot that
        # an unrelated Claude/OpenCode pane could use immediately.
        agent_start_gate = await self._acquire_agent_cold_start(term)
        spawn_succeeded = False
        try:
            # One of a few starts at a time (see COLD_START_LIMIT).
            async with self._cold_start_slot():
                try:
                    pty_session = await manager.spawn(
                        shell_argv=argv,
                        shell_id=f"agentic-ide:{term.key}",
                        cwd=session.folder,
                        cols=cols,
                        rows=rows,
                        on_output=_output,
                        on_closed=_closed,
                        env=env,
                        # In the READER THREAD, not here: a CLI asking its terminal
                        # for the device type or the screen colours reads the answer
                        # within milliseconds of asking, and this event loop is at
                        # its busiest while panes are starting — which is exactly
                        # when the question is asked. Answered from the pump, the
                        # reply was measured 203-234 ms late under a 300 ms stall
                        # and landed in the CLI's prompt as junk the user never
                        # typed. Off the loop it is immediate.
                        on_probe=term.queries.feed,
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced to the pane
                    term.status = "error"
                    term.error = str(exc)
                    raise SessionError(str(exc)) from exc
            spawn_succeeded = True
        finally:
            if not spawn_succeeded and agent_start_gate is not None:
                agent_start_gate.release()

        term.pty_id = pty_session.terminal_id
        # The size the child was actually born with — `spawn` was handed these
        # two numbers directly above, so this is the geometry, not a guess.
        term.pty_cols, term.pty_rows = cols, rows
        term.status = "live"
        term.error = ""
        term.exit_code = None
        term.started_at = time.time()
        # No output has arrived from THIS process yet, and saying otherwise is
        # not a harmless placeholder: `activity.read_activity` falls back to
        # "bytes arrived recently" when it has no previous screen to compare
        # against, so a start-stamp claims the pane is working the instant it
        # goes live. A resumed pane was then never reported as waiting to be
        # continued, because it looked busy from the moment it came back.
        # Cleared rather than left alone so a restarted pane cannot inherit the
        # previous process's last output either.
        term.last_output_at = None
        term.manual_submit_pending = False
        term.manual_submit_token += 1
        term.bracketed_paste_active = False
        # The previous process's activity stamp goes with it. `stamp` resets
        # `activity_since` only when the WORD changes, so a pane that was
        # "waiting", restarted, and settled back to "waiting" kept the old
        # since — and the tooltip claimed the fresh agent had been waiting for
        # however long the dead one had. Until the next sweep (≤2 s) readers
        # fall back to a one-look answer, which is honest about not knowing.
        term.activity = ""
        term.activity_at = 0.0
        term.activity_since = 0.0
        # And this process has not stood still yet, whatever the previous one
        # did. Everything it is about to draw is a CLI painting itself, not an
        # agent working — see the field.
        if agent_start_gate is not None:
            from . import fleet_actions

            try:
                ready = await fleet_actions.wait_for_prompt_ready(
                    session,
                    [term.name],
                    timeout_s=fleet_actions.READY_TIMEOUT_S,
                )
                if term.name not in ready:
                    # Never strand the remaining panes behind a CLI that stopped
                    # on login, trust, or an unknown future startup screen. Prompt
                    # delivery still waits independently and therefore stays safe.
                    logger.warning(
                        "Agentic IDE: {} did not expose an input line before its "
                        "cold-start slot expired; admitting the next {} pane",
                        term.name,
                        term.display_name,
                    )
            finally:
                agent_start_gate.release()
        if term.resume is None and can_resume(term.agent):
            # A CLI that cannot be told its session id (Codex): find out which
            # one it just created, shortly from now.
            self._schedule_lookup(session, term, session.folder, term.started_at)
        if term.continue_when_ready:
            # Somebody pressed "Continue" while this pane was still waiting for
            # a cold-start slot. The wish outlives the wait — see
            # `continue_when_ready` — and is spent HERE. As a task, because the
            # submit itself verifies the pane's screen for a few seconds and the
            # viewer should not wait for that receipt before attaching.
            self.defer_continue(session, term, term.continue_prompt)
        await self._persist()
        return term

    def defer_continue(self, session: Session, term: Terminal, prompt: str = "") -> None:
        """Remember a Continue nudge and schedule it once this pane is live.

        A pending pane carries the request into :meth:`attach`. A live pane may
        still be booting, so it enters the same background path immediately and
        :meth:`send_prompt` waits for the actual input line. There is one route
        for both states and therefore no fixed-delay race.
        """
        from .interrupted import CONTINUE_PROMPT

        term.continue_when_ready = True
        term.continue_prompt = (prompt or CONTINUE_PROMPT).strip() or CONTINUE_PROMPT
        if term.status != "live" or not term.pty_id:
            return
        queued_prompt = term.continue_prompt
        term.continue_when_ready = False
        term.continue_prompt = ""
        self._schedule_continue(session, term, queued_prompt)

    def _schedule_continue(self, session: Session, term: Terminal, prompt: str) -> None:
        """Send the deferred "carry on" to a pane that has just come up.

        Kept on the session's own task set, like the conversation-id lookups, so
        closing that workspace cancels it rather than leaving a nudge in flight
        for a pane that no longer exists.
        """

        async def _nudge() -> None:
            try:
                await self.send_prompt(
                    term.name,
                    prompt,
                    workspace_id=session.id,
                )
            except SessionError as exc:
                # No bytes were written when readiness timed out or the pane
                # stopped. Put the offer back instead of losing the user's click.
                term.continuation_pending = True
                logger.warning(
                    "Agentic IDE: {} came up but could not be continued: {}", term.name, exc
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a nudge must not kill the pane
                logger.warning("Agentic IDE: deferred continue for {} failed: {}", term.name, exc)

        task = asyncio.create_task(_nudge())
        session.lookups.add(task)
        task.add_done_callback(session.lookups.discard)

    def _prepare_spawn(self, term: Terminal, folder: str) -> dict[str, str] | None:
        """Everything this pane's agent needs on disk, then its environment.

        One thread hop for both, because both are filesystem work that has to
        finish before the process starts, and both exist for the same reason: a
        pane on an added subscription must open as the same session the user's
        own terminal opens (:func:`_spawn_env`), and it must not stop on a "do
        you trust this directory?" dialog on the way there.

        Both under ONE lock on the account's directory, because panes attach
        concurrently: a restored workspace re-attaches all of them at once, and
        the two steps below are read-modify-write cycles on the same file — the
        trust entry and the user's MCP servers both live in Claude Code's
        ``.claude.json``. Unserialized, the second write is built on a document
        read before the first one landed and drops it silently.
        """
        home = _redirected_home(term)
        if home is None:
            # Nothing was REDIRECTED — but that is not the same as "nothing to
            # do". A pane still carries whatever its CLI declares for every one
            # of its panes, and skipping the environment here is how a launch
            # profile silently becomes the CLI it borrows: a GLM pane would open
            # as plain Claude Code on the user's own Anthropic login, answer
            # perfectly, and bill the wrong vendor with nothing anywhere saying
            # so. Only the account work below needs a redirected directory.
            return _spawn_env(term)
        from jarvis import agent_config_parity

        with agent_config_parity.setup_lock(home):
            self._pre_trust(term, folder, home)
            return _spawn_env(term)

    def _pre_trust(self, term: Terminal, folder: str, home: Path) -> None:
        """Mark this folder trusted in the config dir THIS pane will run from.

        The workspace open already seeded the machine's own config, which covers
        every pane on the built-in login. A pane on an added account reads a
        different directory entirely, so without this it opens on the trust
        dialog — and a dialog nobody can answer from voice or the prompt bar is
        an agent that never starts. Once per folder and account per process.

        Never raises: an unseeded pane costs one click, a failed spawn costs the
        pane. Caller holds that directory's setup lock.
        """
        key = (os.path.normcase(folder), os.path.normcase(str(home)))
        if key in self._pre_trusted:
            return
        self._pre_trusted.add(key)
        try:
            from jarvis.workspace.trust import ensure_trusted

            ensure_trusted(Path(folder), [term.agent], config_dirs={term.agent: [home]})
        except Exception as exc:  # noqa: BLE001 - trust is a convenience
            logger.warning("Agentic IDE: pre-trust for {} failed: {}", term.name, exc)

    def _schedule_lookup(
        self,
        owner: Session,
        term: Terminal,
        folder: str,
        started_at: float,
        delays: tuple[float, ...] | None = None,
    ) -> None:
        """Find a pane's session id a moment after its CLI created it.

        Fire-and-forget, and deliberately not awaited by ``attach``: the pane is
        already usable, and making the user wait for a filesystem scan to learn
        something only needed after a restart would be the wrong trade.

        Bound to the workspace that owns the pane rather than to "the current
        one": with several open, the front workspace can change twice while this
        is sleeping, and a lookup that then read the front one would write a
        Codex conversation id onto a pane in a different folder.

        ``delays`` is the schedule to try on, because the same search answers two
        different questions: "has the CLI finished starting?" right after a spawn
        (``DISCOVERY_DELAYS_S``) and "has the CLI written the conversation that
        just began?" once the pane has been given something to do
        (``CONVERSATION_DELAYS_S``). ``started_at`` stays the pane's LAUNCH time
        in both cases — a session's recorded timestamp is when it opened, not
        when it was first spoken to, so anything later would rule out the very
        conversation being looked for.

        One round per pane at a time. Two rounds racing would ask the same
        question with the same ``taken`` set and could hand one conversation to
        two panes.
        """
        # Resolved here rather than as a default argument: a default is bound
        # when this module is imported, which silently pins the schedule to the
        # value it had then — including for anything that adjusts it later.
        schedule = DISCOVERY_DELAYS_S if delays is None else delays
        if term.lookup_running:
            return
        term.lookup_running = True
        term.lookup_at = time.monotonic()

        async def _look() -> None:
            try:
                for delay in schedule:
                    await asyncio.sleep(delay)
                    if term not in owner.terminals or owner.id not in self._sessions:
                        return  # the pane (or the workspace) is gone
                    session = owner
                    if term.resume is not None:
                        return
                    taken = {
                        other.resume.id for other in session.terminals if other.resume is not None
                    }
                    found = await asyncio.to_thread(
                        discover,
                        term.agent,
                        folder,
                        started_at,
                        taken,
                        account_home(term.agent, term.account),
                    )
                    if found is None:
                        continue
                    term.resume = found
                    logger.debug("Agentic IDE: {} is conversation {}", term.name, found.id)
                    await self._persist()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a convenience, never fatal
                logger.debug("Agentic IDE: session lookup for {} failed: {}", term.name, exc)
            finally:
                # Even a cancelled round has to let the next trigger through, or
                # a workspace that was closed and reopened would never look
                # again.
                term.lookup_running = False

        try:
            task = asyncio.ensure_future(_look())
        except RuntimeError:
            # No running loop — nothing to schedule onto. Only reachable from the
            # keystroke path, which a non-async caller could in principle drive.
            term.lookup_running = False
            logger.debug("Agentic IDE: no event loop to look up {}'s conversation on", term.name)
            return
        owner.lookups.add(task)
        task.add_done_callback(owner.lookups.discard)

    def _lookup_after_conversation(self, owner: Session, term: Terminal) -> None:
        """Look for this pane's conversation id now that it HAS a conversation.

        The trigger, not the timer, is the point — see ``CONVERSATION_DELAYS_S``.
        A CLI that cannot be told its id writes nothing until its conversation
        gets a first message, so the moment a prompt lands (from Jarvis or from
        the user's own keyboard) is the moment the id becomes findable, whether
        that is four seconds after the pane opened or four hours.

        Cheap to call on every submitted line: a pane that already has a handle,
        an agent that mints its own, and a round that just ran all return here
        without touching the disk.
        """
        if term.resume is not None or not term.started_at:
            return
        if not can_resume(term.agent):
            return
        if term.lookup_running:
            return
        if term.lookup_at and time.monotonic() - term.lookup_at < LOOKUP_COOLDOWN_S:
            return
        self._schedule_lookup(owner, term, owner.folder, term.started_at, CONVERSATION_DELAYS_S)

    def write(self, key: str, data: str, workspace_id: str | None = None) -> bool:
        """Raw keystrokes from the pane's own xterm (not the injection path)."""
        found = self._locate(key, workspace_id)
        if found is None:
            return False
        owner, term = found
        if not term.pty_id:
            return False
        manager = self._manager()
        if is_pointer_noise_only(data):
            # A wheel tick, a click, a focus flip: the terminal talking, not a
            # person typing. It echoes nothing, so it must not arm the typing
            # shadow — stamping it made a busy pane read "done" for STILL_S
            # whenever it was scrolled or merely clicked. The TUI still gets
            # the bytes; it asked for them.
            return manager.write(term.pty_id, data)
        is_submit, edits_prompt, paste_active = classify_terminal_input(
            data, term.bracketed_paste_active
        )
        confirm_pending_prompt = bool(
            is_submit
            and not edits_prompt
            and term.last_prompt
            and (term.submitted is False or term.manual_submit_pending)
        )
        # Do not mutate activity or receipt state for bytes the PTY refused.
        written = manager.write(term.pty_id, data)
        if not written:
            return False
        term.bracketed_paste_active = paste_active
        # Somebody is typing in here. Recorded on EVERY keystroke (unlike the
        # submit handling below), because the activity detector needs to tell
        # the agent's own output apart from the echo of a person at the
        # keyboard — see `activity._printing_now`.
        term.last_input_at = time.time()
        if term.manual_submit_pending and edits_prompt:
            # The screen observer is checking whether the PREVIOUS prompt
            # disappeared. Any later edit can make that happen without a
            # submission (Ctrl+U is the clearest example), so its verdict is
            # stale. Resolve the injected prompt conservatively as unsent.
            term.manual_submit_pending = False
            term.manual_submit_token += 1
            term.submitted = False
        # Gated on a SUBMIT rather than on any keystroke: scrolling, arrow keys
        # and a half-typed line are not an instruction.
        if is_submit:
            # The user submitted something in the pane themselves, so this one
            # is being driven again and is no longer waiting to be nudged.
            # Dropping the pane off that list for a mere keypress would hide a
            # stalled agent behind an accidental one.
            term.continuation_pending = False
            term.resume_continuation_needed = False
            # And this pane now has an instruction of its own, which is what
            # makes its next stop worth reporting — a pane driven only by hand
            # never goes through `send_prompt`, so without this hook the bell
            # would stay silent for everybody who types their own prompts.
            if confirm_pending_prompt:
                # Enter may accept a completion rather than submit. Return the
                # receipt to "unconfirmed" until the same screen check used by
                # the injection path sees the prompt leave the input box.
                term.submitted = None
                term.manual_submit_pending = True
                self._schedule_manual_submit_confirmation(owner, term)
            else:
                term.last_submit_at = term.last_input_at
                term.submit_generation = term.process_generation
            # And the pane's conversation may have just begun, which for most
            # coding CLIs is the first moment its id exists on disk at all. A
            # pane driven only by hand never goes through `send_prompt`, so
            # without this hook it would keep the gap that cost every non-Claude
            # pane its resume handle.
            if not term.manual_submit_pending:
                self._lookup_after_conversation(owner, term)
        return True

    def _schedule_manual_submit_confirmation(self, owner: Session, term: Terminal) -> None:
        """Verify a hand-pressed Enter before changing an unsent receipt."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # A synchronous embedding cannot observe the terminal over time.
            # Keep the receipt unconfirmed instead of making up an answer.
            logger.debug(
                "Agentic IDE: cannot verify manual Enter for {} without an event loop",
                term.name,
            )
            return

        payload = term.last_prompt
        generation = term.process_generation
        term.manual_submit_token += 1
        token = term.manual_submit_token

        async def _confirm() -> None:
            try:
                submitted = await self._observe_manual_submission(term, payload)
                if (
                    term.process_generation != generation
                    or term.last_prompt != payload
                    or term.manual_submit_token != token
                ):
                    return
                term.manual_submit_pending = False
                term.submitted = submitted
                if submitted:
                    term.last_submit_at = time.time()
                    term.submit_generation = term.process_generation
                    self._lookup_after_conversation(owner, term)
                await announce_prompt(term)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - confirmation must not kill input
                logger.warning(
                    "Agentic IDE: could not verify manual Enter for {}: {}", term.name, exc
                )

        task = loop.create_task(_confirm())
        owner.lookups.add(task)
        task.add_done_callback(owner.lookups.discard)

    async def _observe_manual_submission(self, term: Terminal, payload: str) -> bool:
        """Passively watch whether a hand-pressed Enter emptied the input box."""
        needle = _submit_needle(payload)
        checks = max(1, int(_SUBMIT_WINDOW_S / _SUBMIT_POLL_S)) if _SUBMIT_POLL_S else 1
        for _ in range(checks):
            await asyncio.sleep(_SUBMIT_POLL_S)
            if not _input_line_holds(term.transcript.tail(10), needle):
                return True
        return False

    async def _nudge_repaint(self, term: Terminal, cols: int, rows: int) -> None:
        """Ask the agent in ``term`` to draw its whole interface again.

        A terminal protocol has no "please repaint": the drawing side decides
        what to redraw and when. A WINDOW SIZE CHANGE is the one event every
        full-screen TUI answers by rebuilding its frame from scratch — and
        unlike sending Ctrl+L it is not input, so it cannot land in the agent's
        prompt, submit anything, or disturb the work in progress.

        Height only, by one row, and put back immediately. Changing the WIDTH
        would re-wrap the scrollback of an agent that has been running for an
        hour — a visible mess in exchange for nothing, since the redraw is
        triggered by the size CHANGING, not by which dimension changed.

        Never fatal: a pane whose PTY refuses to resize is one whose screen
        could not have been repaired anyway, and that must not cost the user
        the reconnect itself.
        """
        pty_id = term.pty_id
        if not pty_id:
            return
        manager = self._manager()
        try:
            # The whole point of the nudge is a full repaint — which must read
            # as the redraw it is, not as the agent suddenly working. Stamped
            # BEFORE the first resize as well as after the second: the sleep
            # between them yields the loop, so the shrunken frame's redraw can
            # arrive before this coroutine runs again.
            term.last_resize_at = time.time()
            # Two is the floor a TUI can still lay out; below it some redraw
            # into a single row and never recover the frame.
            manager.resize(pty_id, cols, max(rows - 1, 2))
            await asyncio.sleep(REPAINT_NUDGE_S)
            manager.resize(pty_id, cols, rows)
            term.last_resize_at = time.time()
        except Exception as exc:  # noqa: BLE001 - a stale screen beats a failed reconnect
            logger.debug("Agentic IDE: could not nudge {} into a repaint: {}", term.name, exc)

    def claim_viewer(
        self,
        key: str,
        cols: int,
        rows: int,
        workspace_id: str | None = None,
        viewer: Any = None,
    ) -> bool:
        """Give a foreground viewer ownership and restore its PTY geometry."""
        if viewer is None:
            return False
        found = self._locate(key, workspace_id)
        if found is None:
            return False
        term = found[1]
        if not term.pty_id:
            return False
        claimed = next(
            (watched for watched in term.watchers if _same_viewer(watched.output, viewer)),
            None,
        )
        if claimed is None:
            return False
        claimed.cols = cols
        claimed.rows = rows
        # Most recently foregrounded last: if this owner closes, ``detach`` can
        # promote the viewer the user interacted with most recently before it.
        term.watchers = [
            watched for watched in term.watchers if not _same_viewer(watched.output, viewer)
        ]
        term.watchers.append(claimed)
        term.viewer_output = claimed.output
        term.viewer_exit = claimed.exit
        return self.resize(
            term.key,
            cols,
            rows,
            workspace_id,
            viewer=claimed.output,
        )

    def pty_geometry(self, key: str, workspace_id: str | None = None) -> tuple[int, int] | None:
        """The size this pane's agent is really drawing in, or ``None``.

        The answer a viewer needs to keep its own grid honest. ``resize`` is
        allowed to refuse (a tile under the floor, a viewer that no longer holds
        the pane) and to clamp, and until this existed a viewer had no way to
        learn that its request had not been granted — it had already reflowed
        its own xterm and would then hold a grid the agent was never told about.
        A TUI addresses rows by RELATIVE moves, so at that point its repaints
        finish into rows holding something else, and the pane reads as corrupted
        rather than as merely narrow (reported 2026-08-11).

        ``None`` while no process has been sized: a pane that has not spawned has
        no geometry to disagree with.
        """
        found = self._locate(key, workspace_id)
        if found is None:
            return None
        term = found[1]
        if not term.pty_cols or not term.pty_rows:
            return None
        return term.pty_cols, term.pty_rows

    def resize(
        self,
        key: str,
        cols: int,
        rows: int,
        workspace_id: str | None = None,
        viewer: Any = None,
    ) -> bool:
        """Tell this pane's agent how big its screen is.

        ``viewer`` is the socket asking, and a pane accepts a size only from the
        viewer that is actually WATCHING it — the same identity check `detach`
        makes, for the same reason and one step further.

        **Why a size needs an owner.** A pseudo-terminal has exactly one size,
        while a pane may be open in more than one place: a second window, the
        browser UI beside the desktop app, a contributor's `--dev` tab. Those
        windows are different sizes, and this used to hand the agent whichever
        one wrote last. That alone would merely be untidy — what made it stick
        is the other half, in the pane itself: a viewer remembers the size it
        sent and stays quiet while its own measurement does not change (see
        `sentSize` in AgenticTerminal.tsx). So the moment a second viewer
        overwrote the size, the first one had no reason left to speak, and the
        agent kept formatting for a window nobody was looking at — a maximized
        pane drawing its interface into a narrow strip down the left-hand side
        (reported 2026-07-27), for as long as the pane stayed open.
        `viewer` settles it: the size comes from the viewer holding the slot,
        and a displaced one cannot move it any more than it can read from it.

        Passing nothing keeps the old unconditional behaviour, which is what an
        internal caller (a repaint nudge, a test) means by it.
        """
        found = self._locate(key, workspace_id)
        if cols < MIN_VIEWER_COLS or rows < MIN_VIEWER_ROWS:
            # A tile too narrow for the agent to draw in (see the floors). The
            # PTY keeps whatever working geometry it already has, and the tile
            # shows as much of that frame as fits.
            #
            # Unless the PTY is ALREADY under the floor, which is the state a
            # crowded grid used to leave behind and the one nothing else can
            # get a pane out of: every later measurement of the same small tile
            # is refused by this very branch, so a pane squeezed once stayed
            # squeezed for its whole life — agent silent, badge reading that
            # silence as "done". A pane below the floor is therefore lifted TO
            # the floor rather than left there. It is the one place a clamp is
            # right: no window is showing a workable frame anyway, so there is
            # no honest geometry left to preserve.
            # The PTY's own geometry, NOT the transcript's. The question here is
            # whether the AGENT is stuck in a terminal it cannot draw in, and only
            # the size last handed to `setwinsize` answers it; the transcript is a
            # display mirror that drifts from the PTY in both directions. See
            # `Terminal.pty_cols`.
            term_found = found[1] if found is not None else None
            current = (
                (term_found.pty_cols, term_found.pty_rows)
                if term_found is not None and term_found.pty_cols
                else None
            )
            if current is None or (current[0] >= MIN_VIEWER_COLS and current[1] >= MIN_VIEWER_ROWS):
                logger.debug(
                    "Agentic IDE: kept {}'s working geometry instead of a {}x{} tile",
                    key,
                    cols,
                    rows,
                )
                return False
            logger.info(
                "Agentic IDE: lifting {} off a {}x{} terminal its agent cannot draw in",
                key,
                current[0],
                current[1],
            )
            cols = max(cols, MIN_VIEWER_COLS)
            rows = max(rows, MIN_VIEWER_ROWS)
        if found is None:
            return False
        term = found[1]
        if not term.pty_id:
            return False
        # Remember what EVERY attached viewer currently needs, including one
        # that is temporarily displaced from the ownership slot. If the newer
        # owner closes while this message is in flight, ``detach`` promotes the
        # survivor and restores exactly this geometry. Without that memory, the
        # survivor believed it had already announced its size while the PTY was
        # left at the departing window's dimensions: a maximized pane with the
        # agent still drawing in a narrow strip (reported again 2026-07-31).
        if viewer is not None:
            for watched in term.watchers:
                if _same_viewer(watched.output, viewer):
                    watched.cols = cols
                    watched.rows = rows
                    break
        current = term.viewer_output
        # Equality, not only identity — a bound method is a fresh object on
        # every attribute access (see `detach`).
        if (
            viewer is not None
            and current is not None
            and current is not viewer
            and current != viewer
        ):
            logger.debug(
                "Agentic IDE: ignored a resize for {} from a viewer that no longer holds it",
                term.name,
            )
            return False
        # The replayed screen has to follow the real one; otherwise the
        # transcript keeps wrapping at the old width.
        if (term.transcript.cols, term.transcript.rows) == (cols, rows):
            return True
        if not self._manager().resize(term.pty_id, cols, rows):
            return False
        term.pty_cols, term.pty_rows = cols, rows
        # The TUI answers the new size with a full redraw — shadow it so a
        # finished pane does not read as "working" every time the grid
        # re-lays itself out (chat view toggle, maximize, a dragged seam).
        term.last_resize_at = time.time()
        if is_coding_agent(term.agent):
            # Future viewers must not replay cursor moves produced for the old
            # grid into the new one. The live viewer already has its screen;
            # this only starts a clean replay epoch for the next reconnect.
            term.replay.rebase_for_resize()
        term.transcript.resize(cols, rows)
        return True

    def detach(self, key: str, workspace_id: str | None = None, viewer: Any = None) -> None:
        """Let go of a pane's viewer. The agent behind it keeps running.

        Detaching used to kill the PTY, on the reasoning that an agent nobody
        watches burns tokens invisibly. With several workspaces that reasoning
        inverts: a viewer disappears every time you switch tab, reload the page
        or walk over to the chat view, and none of those mean "stop working" —
        killing there would throw away work in progress several times an hour.

        So the lifetime rule is the one a user can actually predict: **an agent
        runs until its workspace is closed.** Nothing is invisible about it —
        every open workspace is a tab with a live-pane count on it, and closing
        one stops its agents immediately (see ``_close_locked``).

        **``viewer`` is what stops a leaving viewer from blinding the one that
        replaced it** (BUG-113). Viewers overlap: reloading the page, restarting
        a pane or switching back to the section closes one socket and opens
        another for the SAME pane in the same breath, and which of the two the
        server finishes first is a matter of milliseconds. Clearing the slot
        unconditionally therefore wiped a viewer that had just been installed —
        the pane then sat there with an open socket, a live agent typing into a
        transcript, and a screen that never moved again. Passing the callback
        that was handed to ``attach`` makes this a no-op unless the slot is
        still that viewer's; a caller that genuinely means "nobody is watching
        this pane" (a test, a teardown) passes nothing and clears it outright.
        """
        found = self._locate(key, workspace_id)
        if found is None:
            return
        term = found[1]
        # This viewer stops receiving output either way — it is the one going
        # away. Done before the ownership check below, because a viewer that was
        # displaced from the slot but is still WATCHING must not keep being
        # written to after its socket closed.
        if viewer is not None:
            term.watchers = [w for w in term.watchers if not _same_viewer(w.output, viewer)]
        current = term.viewer_output
        # Compared by equality, not only by identity: a bound method is a brand
        # new object on every attribute access, so `is` would answer "you are
        # not the viewer" to the very callback sitting in the slot.
        if viewer is not None and current is not viewer and current != viewer:
            # Somebody else OWNS this pane now. Leaving quietly is the whole job
            # — the slot belongs to the newer viewer.
            logger.debug(
                "Agentic IDE: a departing viewer left {} to the one that replaced it",
                term.name,
            )
            return
        # The owner is leaving. Whoever else is still attached takes the slot —
        # the pane is not unwatched just because the newest window closed, and
        # handing ownership to a viewer that is still there is what keeps the
        # remaining screen able to set the agent's size.
        #
        # Naming no viewer keeps its original meaning: "nobody is watching this
        # pane", full stop. A teardown says that, and promoting a survivor there
        # would leave a torn-down pane holding callbacks.
        if viewer is not None and term.watchers:
            survivor = term.watchers[-1]
            term.viewer_output = survivor.output
            term.viewer_exit = survivor.exit
            # Ownership and geometry are one handover. The previous owner may
            # have resized the one shared PTY after this viewer last reported,
            # and the promoted viewer has no DOM change that would make it send
            # the same size again. Restore its remembered size now instead of
            # waiting for an unrelated window resize to repair the screen.
            if not self.resize(
                term.key,
                survivor.cols,
                survivor.rows,
                workspace_id,
                viewer=survivor.output,
            ):
                logger.debug(
                    "Agentic IDE: could not restore {} to its promoted viewer's geometry",
                    term.name,
                )
            return
        term.viewer_output = None
        term.viewer_exit = None
        term.watchers = []

    # ------------------------------------------------------------- panes
    async def add_terminal(
        self,
        *,
        agent: str | None = None,
        name: str | None = None,
        anchor: str | None = None,
        direction: str = "right",
        account: str | None = None,
    ) -> Terminal:
        """Open one more terminal in the running workspace.

        ``direction`` decides where it lands relative to ``anchor``:
        ``"right"`` opens a new column beside the anchor, ``"down"`` splits the
        anchor's own column and stacks the new pane under it — leaving every
        other column at full height. Without an anchor the new pane goes after
        the last one.

        The agent defaults to the anchor's, because splitting a Claude Code pane
        usually means "another one of these" — but a caller may name any
        installed agent, which is how the UI offers a choice of coding CLI.
        Without an anchor there is no "these" to copy, so the workspace's
        prevailing CLI decides instead (``_prevailing_agent``).

        ``account`` names which subscription of that agent to run on. Without
        one, every new pane opens on the workspace's active account
        (``set_active_account``) — with one exception: a pane split off an
        anchor whose seat was DELIBERATELY chosen (wizard picker, explicit
        ``account``, or itself split off such a pane) stays on that seat, so
        multiplying a second-plan pane cannot quietly move the work onto a
        different bill. An anchor that merely followed the default vouches for
        nothing, and its splits follow the switch like every other new pane.
        """
        async with self._lock:
            session = self.session
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            if len(session.terminals) >= MAX_TERMINALS:
                raise SessionError(
                    f"This workspace already has the maximum of {MAX_TERMINALS} terminals."
                )
            if direction not in ("right", "down"):
                raise SessionError("Direction must be 'right' or 'down'.")

            base = session.find(anchor) if anchor else None
            if anchor and base is None:
                raise SessionError(f"No terminal called {anchor!r}.")
            if base is None:
                base = session.terminals[-1] if session.terminals else None

            # A named CLI wins; a SPLIT inherits its anchor ("another one of
            # these"); everything else takes the workspace's prevailing CLI
            # rather than the last pane's — see ``_prevailing_agent``.
            if agent:
                chosen = agent
            elif anchor and base is not None:
                chosen = base.agent
            else:
                chosen = _prevailing_agent(session)
            if not is_runnable(chosen):
                raise SessionError(f"Unknown agent: {chosen}")
            if agent_argv(chosen) is None:
                raise SessionError(_unavailable(chosen))

            # Unused within THIS workspace — the scope a positional call-sign
            # is counted in. A split fills the lowest free number, so closing
            # the middle pane and opening another puts the grid back at T1..Tn
            # instead of drifting upward forever.
            used = {normalize(t.name) for t in session.terminals}
            wanted = (name or "").strip()
            if not wanted:
                wanted = free_positions([t.name for t in session.terminals], 1)[0]
            final = _unique_name(wanted, used)

            # Which subscription the new pane opens on, when the caller named
            # none. The rule, in priority order:
            #
            # * **An explicit ``account`` wins** and marks the pane as pinned —
            #   this seat was chosen on purpose, so splits of it may carry it on.
            # * **A split of a PINNED pane inherits that seat** (only when the
            #   CLI matches — a Claude account id means nothing to Codex), so a
            #   pane deliberately opened on the second plan can be multiplied
            #   without quietly moving the work onto a different bill.
            # * **Everything else follows the workspace's active account** —
            #   the batch behind "open five more", the empty grid's button, the
            #   CLI, and a split of a pane that itself only followed the default.
            #
            # That last clause is the 2026-08-12 fix. Splits used to inherit
            # their anchor's account unconditionally, and in a workspace whose
            # panes all shared one seat that made the subscription switcher
            # unreachable: the user switched twice, opened panes by splitting —
            # the dominant gesture — and every one resurrected the seat they had
            # just left ("I changed my subscriptions twice and it doesn't
            # change"). A pane that merely followed the default is not a
            # deliberate deviation, so it has no seat worth propagating; only a
            # chosen one does. (Anchor-less adds learned the same lesson
            # earlier: inheriting from whatever pane happened to be last made
            # the switch reach nothing a user could predict.)
            requested_account = (account or "").strip() or None
            if (
                requested_account is None
                and anchor
                and base is not None
                and base.agent == chosen
                and base.account_pinned
            ):
                requested_account = base.account
            resolved_account = resolve_account(chosen, requested_account)
            term = Terminal(
                key=normalize(final) or f"t{len(session.terminals)}",
                name=final,
                agent=chosen,
                display_name=agent_display(chosen),
                index=len(session.terminals),
                account=resolved_account,
                # An unknown requested id falls back to the active account
                # (see resolve_account) — a fallback is not a choice, so it
                # must not be pinned as one.
                account_pinned=requested_account is not None
                and resolved_account == requested_account,
            )
            session.terminals.append(term)
            # Where it goes is the tree's business, and the distinction is the
            # whole feature: a NAMED anchor is a split — the new pane carves
            # the clicked pane's own rectangle and nothing else moves — while
            # an anchor-less add ("open five more", the empty grid's button)
            # joins the workspace edge as a full-height column, because no
            # pane was chosen to give up half its room.
            if anchor and base is not None:
                session.layout = layout_tree.split_pane(
                    session.layout,
                    base.key,
                    term.key,
                    "right" if direction == "right" else "down",
                )
            else:
                session.layout = layout_tree.append_pane(session.layout, term.key)
            self._renumber(session)
            await self._persist()
            logger.info(
                "Agentic IDE: added terminal {} ({}) {} of {}",
                term.name,
                term.agent,
                direction,
                base.name if base else "the grid",
            )
            return term

    async def add_terminals(
        self, count: int, *, agent: str | None = None, account: str | None = None
    ) -> tuple[list[Terminal], bool]:
        """Open up to ``count`` more panes — the batch behind "open five more".

        Returns the panes that were created and whether the pane cap
        truncated the request, because those are two different answers the caller
        has to speak out loud: five requested with three opened is a success the
        user must hear ("room for three"), not a silent partial.

        Deliberately a loop over ``add_terminal`` rather than a second placement
        implementation: the anchor, the call-sign pool, and the grid position are
        already decided there, and a batch that placed panes its own way would
        drift from what the split buttons do. No anchor is named, so without an
        explicit ``account`` every pane opens on the workspace's active one.

        The cap is the expected stopping point, so hitting it is not an error.
        A failure with NOTHING opened is — an unknown agent or a vanished binary
        must not be reported as "nothing to do".
        """
        if self.session is None:
            raise SessionError("No Agentic-IDE session is running.")
        wanted = max(1, int(count))
        created: list[Terminal] = []
        for _ in range(wanted):
            try:
                created.append(await self.add_terminal(agent=agent, account=account))
            except SessionError as exc:
                if not created:
                    raise
                logger.info(
                    "Agentic IDE: batch stopped after {} of {} panes: {}",
                    len(created),
                    wanted,
                    exc,
                )
                break
        return created, len(created) < wanted

    async def move_terminal(self, wanted: str, *, target: str, position: str = "swap") -> Terminal:
        """Put an existing pane somewhere else in the grid.

        The rearranging half of the two-axis model ``add_terminal`` builds: no
        agent is started or stopped here, no PTY is touched, and no pane is
        remounted — only the two numbers that say where a pane is drawn change.
        That is precisely why rearranging is safe to offer at all. A workspace of
        a dozen agents is assembled one split at a time and ends up in an order
        nobody chose; without this the only way to fix it was to close a working
        agent and open it again somewhere else.

        ``position`` says what the drop meant, relative to ``target``:

        * ``"swap"`` — the two panes exchange places. The one move that keeps the
          grid's shape exactly as it was, which is what "these two are the wrong
          way round" asks for.
        * ``"left"`` / ``"right"`` — the pane becomes a column of its own on that
          side of the target; every column from there rightwards shifts over.
        * ``"above"`` / ``"below"`` — the pane joins the target's OWN column at
          that place, and only that column's stack moves.

        Dropping a pane on itself is a no-op rather than an error: it is what a
        user who changed their mind mid-drag does, and refusing it would turn a
        cancelled gesture into a red banner.
        """
        async with self._lock:
            session = self.session
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            if position not in MOVE_POSITIONS:
                allowed = ", ".join(f"'{item}'" for item in MOVE_POSITIONS)
                raise SessionError(f"Position must be one of {allowed}.")

            known = ", ".join(t.name for t in session.terminals) or "none"
            moved = session.find(wanted)
            if moved is None:
                raise SessionError(f"No terminal called {wanted!r}. Running: {known}.")
            anchor = session.find(target)
            if anchor is None:
                raise SessionError(f"No terminal called {target!r}. Running: {known}.")
            if anchor.key == moved.key:
                return moved

            # "swap" exchanges the two panes and keeps the tree's exact shape;
            # the four sides carve the TARGET's own rectangle — the same local
            # meaning the split buttons have, at any depth. The moved pane's
            # old room dissolves to its former siblings on the way out.
            session.layout = layout_tree.move_pane(session.layout, moved.key, anchor.key, position)
            self._renumber(session)
            await self._persist()
            logger.info(
                "Agentic IDE: moved terminal {} {} {}",
                moved.name,
                position,
                anchor.name,
            )
            return moved

    async def refold(self, depth: int) -> Session:
        """Re-deal every pane into columns ``depth`` deep, in reading order.

        The workspace is exactly one screenful and never scrolls, so a pane can
        only be given room that is taken from somewhere else. When the panes are
        too NARROW — the width a coding agent needs for its interface is a hard
        floor, ``MIN_REAL_COLS`` in the frontend's terminal — the only room left
        to spend is height, and folding the row is how it is spent: half as many
        columns are twice as wide. Six panes in a row at the maintainer's text
        size were ~410 px each where ~660 was needed, so every terminal drew a
        third of itself past its own tile edge (reported 2026-08-11, and read as
        the panes overlapping one another).

        Deliberately a whole-workspace operation rather than a run of
        ``move_terminal`` calls. Six moves are six persists and six pushes to
        every viewer, and each intermediate shape is a real arrangement the
        panes would refit to — the grid would visibly thrash through five wrong
        layouts on the way to the right one, and an interruption anywhere in
        that run leaves the workspace in one of them for good.

        Reading order is the order ``_renumber`` already keeps the list in (left
        to right, top to bottom), so folding preserves what the user sees as the
        sequence of their panes. Re-folding to the depth a workspace already has
        changes nothing and is not an error: it is what the grid asks for on
        every measurement once the shape is right, and answering it with the
        unchanged session is what lets the caller stop.

        No agent is started or stopped, no PTY is resized here and no pane is
        remounted — only the two numbers that say where a pane is drawn. That is
        what makes re-folding safe to do on the app's own initiative at all.
        """
        async with self._lock:
            session = self.session
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            if depth < 1:
                raise SessionError("Column depth must be at least 1.")
            # A depth past the pane count is the same shape as one column, and
            # accepting it rather than refusing keeps the caller from having to
            # clamp: the grid asks with a number it derived from a measurement.
            depth = min(depth, max(1, len(session.terminals)))

            # A fresh tree in the wizard's shape, weights reset: a re-fold is
            # a whole-workspace re-deal by definition, and carrying dragged
            # weights from an arrangement that no longer exists would re-fold
            # into something nobody has seen before.
            session.layout = layout_tree.wizard_tree([t.key for t in session.terminals], depth)
            self._renumber(session)
            await self._persist()
            logger.info(
                "Agentic IDE: re-folded {} panes into columns of {}",
                len(session.terminals),
                depth,
            )
            return session

    async def set_layout_weights(self, layout: dict[str, Any]) -> Session:
        """Adopt a client's dragged pane sizes; the STRUCTURE stays the server's.

        A seam drag changes exactly one thing about a workspace — how much
        room neighbours give each other — and that is all this accepts. The
        client sends back the whole tree it was looking at; if its shape still
        matches the live one, its weights are adopted, persisted, and pushed
        to every viewer like any other layout change.

        A mismatch is a RACE, not a fault: a voice-opened pane or a second
        client reshaped the workspace mid-drag. The drag is quietly declined —
        the response (and the next state poll) carries the authoritative tree,
        so the client snaps back to reality rather than painting a red banner
        over a background event the user never saw.
        """
        async with self._lock:
            session = self.session
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            try:
                proposed = layout_tree.from_dict(layout)
            except ValueError as exc:
                raise SessionError(f"Unreadable layout: {exc}") from exc
            if session.layout is not None and layout_tree.same_shape(session.layout, proposed):
                session.layout = layout_tree.adopt_weights(session.layout, proposed)
                await self._persist()
            else:
                logger.info(
                    "Agentic IDE: dragged sizes arrived for a reshaped workspace — "
                    "keeping the live arrangement"
                )
            return session

    async def rename_terminal(self, wanted: str, name: str) -> tuple[Session, Terminal]:
        """Give one pane a new call-sign, without touching what runs in it.

        The pane's own identity as far as its RUNNING agent is concerned is its
        key, not its call-sign: the pseudo-terminal is filed under the key, and
        the key is deliberately left alone here. So renaming is exactly what a
        user expects it to be — the label changes, the agent keeps working, its
        conversation and scrollback are untouched. The viewer reconnects (it
        addresses the pane by call-sign) and repaints from the transcript,
        which is the same path a workspace switch already takes.

        The new call-sign has to be usable as ONE, which is what the checks are
        about: a name nobody can say is a pane nobody can send work to.

        * It must contain something to compare — ``normalize`` keeps letters
          and digits only, so a name of pure punctuation would leave the pane
          addressable by nothing at all.
        * It must be free within THIS workspace, the scope a call-sign lives
          in. Two panes answering to one name make every spoken instruction a
          coin flip over which agent gets the work.

        Searching every open workspace rather than only the front one, because
        a custom call-sign is exactly what somebody gives a pane so they can
        address it from anywhere — including to rename it.
        """
        cleaned = " ".join(name.split()).strip()
        if not cleaned:
            raise SessionError("Give the terminal a name.")
        if len(cleaned) > MAX_TERMINAL_NAME:
            raise SessionError(f"Terminal names can be at most {MAX_TERMINAL_NAME} characters.")
        if not normalize(cleaned):
            raise SessionError("Give the terminal a name with letters or numbers in it.")
        async with self._lock:
            found = self.find_terminal(wanted)
            if found is None:
                raise self._unknown_terminal(wanted)
            session, term = found
            if term.name == cleaned:
                return session, term
            if any(
                other is not term and normalize(other.name) == normalize(cleaned)
                for other in session.terminals
            ):
                raise SessionError(
                    f"Another terminal in this workspace is already called {cleaned!r}."
                )
            previous = term.name
            term.name = cleaned
            await self._persist()
            logger.info("Agentic IDE: renamed terminal {} to {}", previous, cleaned)
            return session, term

    async def close_terminal(self, wanted: str) -> Terminal:
        """Stop one terminal's agent and remove its pane from the workspace."""
        closed, failed = await self.close_terminals([wanted])
        if failed:
            raise SessionError(failed[0]["detail"])
        return closed[0]

    async def close_terminals(
        self, wanted: list[str]
    ) -> tuple[list[Terminal], list[dict[str, str]]]:
        """Stop several panes under one registry lock and persist once.

        Unknown and duplicate names are reported individually while every valid
        terminal is closed. Resolving the complete selection before teardown
        keeps concurrent callers from changing which pane a name refers to
        halfway through the batch.
        """
        async with self._lock:
            session = self.session
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            known = ", ".join(t.name for t in session.terminals) or "none"
            resolved: list[Terminal] = []
            failed: list[dict[str, str]] = []
            seen: set[str] = set()
            for name in wanted:
                term = session.find(name)
                if term is None:
                    failed.append(
                        {
                            "name": name,
                            "detail": f"No terminal called {name!r}. Running: {known}.",
                        }
                    )
                    continue
                if term.key in seen:
                    failed.append(
                        {"name": name, "detail": "The terminal was selected more than once."}
                    )
                    continue
                seen.add(term.key)
                resolved.append(term)

            for term in resolved:
                term.stopping = True  # a deliberate kill, not a crashed resume
                if term.pty_id and self._pty is not None:
                    try:
                        self._pty.close(term.pty_id)
                    except Exception:  # noqa: BLE001, S110 - best-effort teardown
                        pass
                term.pty_id = None
                term.status = "exited"
                term.viewer_output = None
                term.viewer_exit = None
                term.watchers.clear()
                term.prompt_viewers.clear()
                session.terminals.remove(term)
                # The pane's rectangle folds away with it: its room goes to
                # its siblings and any container left holding one child
                # dissolves, so a workspace that was split apart closes back
                # to simple shapes.
                session.layout = layout_tree.remove_pane(session.layout, term.key)
                # The recap cache is keyed by pane, and pane keys are reused
                # (a new "Mika" in the same workspace). Dropping it here is what
                # stops a fresh pane opening under the last one's sentence.
                recap_engine.forget(term.key)
                # Its bell entries go the same way and for the same reason.
                # Each one is a "jump to this pane" button, and the pane has
                # just stopped existing — while its key has not, so waiting for
                # the sweep to notice would hand them to whoever takes the name
                # next.
                try:
                    from . import notifications

                    notifications.center().forget_pane(session.id, term.key)
                except Exception as exc:  # noqa: BLE001 - never fail a close on bookkeeping
                    logger.warning(
                        "Agentic IDE: could not clear notifications for a closed pane: {}", exc
                    )
            self._renumber(session)
            if resolved:
                await self._persist()
                logger.info(
                    "Agentic IDE: closed terminals {}",
                    ", ".join(term.name for term in resolved),
                )
            return resolved, failed

    @staticmethod
    def _renumber(session: Session) -> None:
        """Re-align the pane LIST with the layout tree after any change.

        The tree is the geometry; this keeps everything derived from it
        honest, defensively in both directions:

        * A pane the tree does not know (opened by a code path that predates
          the tree, or a snapshot written half-way through a close) is
          appended at the workspace edge rather than rendered nowhere.
        * A tree entry whose pane is gone is pruned rather than drawn as a
          blank rectangle.

        Then the list is sorted into the tree's reading order (left to right,
        top to bottom — the order the prompt-bar chips use), ``index`` is
        re-packed, and the coarse ``column``/``slot`` hints are re-projected
        for the consumers that only talk ABOUT the grid.
        """
        live = {t.key for t in session.terminals}
        for key in layout_tree.leaves(session.layout):
            if key not in live:
                session.layout = layout_tree.remove_pane(session.layout, key)
        placed = set(layout_tree.leaves(session.layout))
        for term in session.terminals:
            if term.key not in placed:
                session.layout = layout_tree.append_pane(session.layout, term.key)

        order = {key: at for at, key in enumerate(layout_tree.leaves(session.layout))}
        session.terminals.sort(key=lambda t: order.get(t.key, len(order)))
        hints = layout_tree.grid_hints(session.layout)
        for position, term in enumerate(session.terminals):
            term.index = position
            term.column, term.slot = hints.get(term.key, (0, 0))

    # --------------------------------------------------------------- prompt
    async def send_prompt(
        self, wanted: str, text: str, *, workspace_id: str | None = None
    ) -> Terminal:
        """Type ``text`` into a terminal, press Enter, and CONFIRM it was sent.

        Typing and hoping is not enough, which a live failure proved on
        2026-07-25: three prompts were typed into three agents and only one ran.
        The two that stalled both ended with an ``@file`` reference, and that is
        the whole mechanism — an ``@path`` (or a ``/command``) at the end of the
        line leaves the agent's completion popup OPEN, so the Enter that follows
        picks a suggestion instead of submitting. Measured on a real Claude Code:
        ending with ``@README.md`` never submits; the same prompt with one
        trailing space always does.

        So three defences, because a silent no-op is the worst outcome here:

        1. **Close any open completion** before Enter — a single space when the
           prompt ends in an ``@``/``/`` token. Harmless to the prompt text.
        2. **Verify and retry.** After Enter, the sent text must be GONE from the
           input line. While it is still sitting there, press Enter again (twice
           at most). Whether it finally went is reported back, so a caller can
           say "sent to Mika" or "Mika did not accept it" — never guess.
        3. **Fall back to one line.** A composed prompt is markdown and travels
           as a bracketed paste. Whether a given agent TUI honours that is not
           knowable from here, so a paste the pane did not accept is re-sent in
           the single-line form that has always worked. The worst case is
           therefore the old behaviour, never a lost instruction.

        ``workspace_id`` pins background work such as a deferred Continue to the
        pane it came from; without it, the front workspace keeps the established
        call-sign resolution rules.

        Raises ``SessionError`` when the terminal is unknown, not running, still
        booting after the readiness window, or the prompt sanitizes down to
        nothing. A prompt that was typed but refused to submit is NOT an error —
        the text is in the box and the caller is told.
        """
        found = (
            self._locate(wanted, workspace_id)
            if workspace_id is not None
            else self.find_terminal(wanted)
        )
        if found is None:
            raise self._unknown_terminal(wanted)
        owner, term = found
        if not accepts_prompts(term.agent):
            # A plain terminal is a live SHELL prompt, so an injected line would
            # not be read by an agent — it would run as a command. This is the
            # one place the module docstring's rule 1 has to be enforced rather
            # than merely implied, because such a pane exists on purpose now.
            raise SessionError(
                f"{term.name} is a {agent_display(term.agent).lower()}, not a coding agent — "
                "Jarvis does not type into a shell. Type it there yourself, or send it "
                "to an agent terminal."
            )
        if term.status != "live" or not term.pty_id:
            raise SessionError(
                f"{term.name} is not running right now (status: {term.status}) — nothing was sent."
            )
        payload = sanitize_prompt(text, keep_newlines=True)
        if not payload:
            raise SessionError("The prompt was empty after cleanup.")

        # A spawned PTY is not necessarily an interactive CLI yet. Codex in
        # particular can spend tens of seconds opening plugins and MCP servers,
        # and a paste written during that phase is swallowed rather than queued.
        # Waiting on the real input line is capability-gated, so the measured
        # stable fast path (Claude) remains immediate and new CLIs fail safe.
        from . import fleet_actions

        ready = await fleet_actions.wait_for_prompt_ready(
            owner,
            [term.name],
            timeout_s=fleet_actions.READY_TIMEOUT_S,
        )
        if term.name not in ready:
            if term.status != "live" or not term.pty_id:
                raise SessionError(
                    f"{term.name} stopped while it was starting (status: {term.status}) — "
                    "nothing was sent."
                )
            raise SessionError(
                f"{term.name} is still starting — its input line never appeared, "
                "so nothing was sent."
            )

        manager = self._manager()
        multiline = "\n" in payload

        submitted = await self._write_and_confirm(term, payload, manager, multiline)
        if submitted is False:
            # NOT a retry site. A hard False means the verification watched the
            # text SIT in the input box for the whole window, which is proof the
            # pane received it. Typing it again (the single-line fallback this
            # used to do) appends a second copy behind the first, and the next
            # Enter submits both — worse, a retry Enter landing mid-rewrite runs
            # the prompt twice for real. Extra Enters belong in the verification
            # loop, where each one is guarded by "the text is still there".
            #
            # Nothing is lost by stopping: the prompt sits in the pane in full,
            # visible to the user, and the caller is told plainly it never went.
            logger.warning(
                "Agentic IDE: {} kept the prompt in its input box — it was typed "
                "in full but never submitted",
                term.name,
            )

        term.prompts_sent += 1
        term.last_prompt = payload
        # Stamped before anything is announced, so the notice and the state can
        # never disagree about when this happened — and so a viewer that arrives
        # a second later reads the same instant the notice carried.
        term.last_prompt_at = time.time()
        # The same stamp under the name the activity watcher reads, so a pane
        # driven by Jarvis and one driven by hand prove the same thing the same
        # way. NOT set on a hard False: the verification watched the text SIT
        # in the input box, so no job was handed over — and stamping it anyway
        # turned the echo of an unsubmitted prompt into a "Finished and waiting
        # at its prompt" bell for work that never started. The moment the user
        # presses Enter on that box themselves, `write` stamps it for real.
        if submitted is not False:
            term.last_submit_at = term.last_prompt_at
            term.submit_generation = term.process_generation
        term.manual_submit_pending = False
        term.manual_submit_token += 1
        term.submitted = submitted
        term.sent_multiline = multiline and submitted is True
        history_entry = prompt_history.PromptHistoryEntry(
            id=uuid4().hex,
            sequence=term.prompts_sent,
            text=payload,
            at=term.last_prompt_at,
            submitted=submitted,
        )
        # Memory first: even a read-only or temporarily unavailable data folder
        # must not make a prompt disappear from the history while the pane is
        # still open. Disk is the persistence layer, not the only copy.
        term.prompt_records.append(history_entry)
        try:
            await asyncio.to_thread(prompt_history.append, term.history_id, history_entry)
        except OSError as exc:
            logger.warning(
                "Agentic IDE: could not persist the prompt history for {}: {}",
                term.name,
                exc,
            )
        # Somebody is driving this pane again, whatever the prompt said. Cleared
        # even when the pane did not submit the text: the instruction is sitting
        # in its input box in full, so offering to type "continue" behind it
        # would append a second line to a prompt the user still has to send.
        term.continuation_pending = False
        term.resume_continuation_needed = False
        if submitted is not False:
            # The conversation has (or may have) just begun, so for a CLI that
            # cannot be told its id this is the moment that id starts existing —
            # see `_lookup_after_conversation`. Skipped only for a hard False,
            # which means the text is provably still sitting in the input box:
            # nothing was recorded, and a round spent on that would burn the
            # cooldown the real submit needs.
            self._lookup_after_conversation(owner, term)
        logger.info(
            "Agentic IDE prompt -> {} ({}, {}): {}",
            term.name,
            "submitted"
            if submitted is True
            else "STILL IN THE INPUT BOX"
            if submitted is False
            else "UNCONFIRMED — never seen to arrive",
            "multi-line" if term.sent_multiline else "one line",
            payload[:120],
        )
        # The receipt goes out for every outcome, submitted or not. A prompt
        # sitting unsent in the input box is the case where seeing it matters
        # MOST — that pane looks identical to a working one, and the user is
        # the only one who can push it over the line.
        await announce_prompt(term)
        return term

    async def _write_and_confirm(
        self,
        term: Terminal,
        payload: str,
        manager: PtyManager,
        multiline: bool,
    ) -> bool | None:
        """Type ``payload``, press Enter, and report whether it was accepted.

        Three answers, because there genuinely are three: it went out, it is
        still sitting in the box, or the pane never visibly took it and no
        honest claim can be made either way (``None``).

        Enter is timed against the SCREEN, not against a stopwatch. A pane that
        is still booting swallows a paste whole — measured on a real Codex —
        and an input box that never received the text is indistinguishable from
        one that submitted it, so a blind "type, wait 120 ms, press Enter" both
        pressed into nothing and then reported success.
        """
        # The completion guard applies to the LAST line: that is the one the
        # cursor sits on when Enter arrives.
        last_line = payload.rsplit("\n", 1)[-1]
        typed = payload + (" " if _opens_completion(last_line) else "")
        if multiline:
            typed = f"{PASTE_START}{typed}{PASTE_END}"
        # Injected text echoes exactly like hand-typing, and the activity
        # detector must read it the same way: movement in the shadow of these
        # writes is the prompt being TYPED, never the agent already working —
        # and, for a prompt the pane refuses to submit, never the agent
        # "finishing" a job it was never given.
        term.last_input_at = time.time()
        if not manager.write(term.pty_id or "", typed):
            raise SessionError(f"Could not write to {term.name}.")

        arrived = await self._await_arrival(term, payload)
        term.last_input_at = time.time()
        manager.write(term.pty_id or "", "\r")
        left_the_box = await self._confirm_submitted(term, payload, manager)

        if not arrived and left_the_box:
            # The prompt was never SEEN in the box, and an empty box is exactly
            # what a successful submit looks like — so "it went out" and "the
            # pane swallowed it" are indistinguishable from here. Say so instead
            # of picking the flattering one: a booting Codex really does drop a
            # paste whole (measured 2026-07-26), and the old check called that
            # success. Writing it again is NOT the answer — if the text is in
            # fact sitting there unread, a second copy lands behind the first
            # and the pane runs a doubled instruction.
            logger.warning(
                "Agentic IDE: never saw the prompt reach {} — it may have been "
                "submitted or dropped; reporting it as unconfirmed",
                term.name,
            )
            return None
        return left_the_box

    async def _await_arrival(self, term: Terminal, payload: str) -> bool:
        """Wait until the pane visibly holds ``payload``, or give up.

        Returns as soon as the text (or the TUI's collapsed stand-in for it) is
        on the input line, which is also the moment Enter is worth pressing —
        so on a healthy pane this costs a fraction of the old fixed delay.
        """
        needle = _submit_needle(payload)
        deadline = max(1, int(_ARRIVAL_WINDOW_S / _ARRIVAL_POLL_S)) if _ARRIVAL_POLL_S else 1
        for _ in range(deadline):
            await asyncio.sleep(_ARRIVAL_POLL_S)
            if _input_line_holds(term.transcript.tail(10), needle):
                return True
        return False

    async def _confirm_submitted(self, term: Terminal, payload: str, manager: PtyManager) -> bool:
        """True once ``payload`` has left the terminal's input line.

        The input line lives at the bottom of the screen, just above the status
        bar; a submitted prompt scrolls up out of it. So the check is: does the
        BOTTOM of the replayed screen still show the beginning of what we typed?
        Content-based rather than timing-based, because "the agent produced some
        output" is not the same as "the prompt was accepted" (a completion popup
        redraws too).
        """
        needle = _submit_needle(payload)
        checks = max(1, int(_SUBMIT_WINDOW_S / _SUBMIT_POLL_S)) if _SUBMIT_POLL_S else 1
        retried = False
        for step in range(checks):
            await asyncio.sleep(_SUBMIT_POLL_S)
            if not _input_line_holds(term.transcript.tail(10), needle):
                return True
            elapsed = (step + 1) * _SUBMIT_POLL_S
            if not retried and elapsed >= _SUBMIT_RETRY_AFTER_S:
                retried = True
                logger.warning(
                    "Agentic IDE: {} still holds the prompt in its input box — "
                    "pressing Enter once more",
                    term.name,
                )
                manager.write(term.pty_id or "", "\r")
        return not _input_line_holds(term.transcript.tail(10), needle)

    def report(self, wanted: str, lines: int = 40) -> dict[str, Any]:
        """What one terminal has been up to — the answer to "what is X doing?"."""
        found = self.find_terminal(wanted)
        if found is None:
            raise self._unknown_terminal(wanted)
        session, term = found
        data = term.to_dict()
        data["folder"] = session.folder
        # Which workspace answered. With several open, "Kai is running the
        # tests" is only half an answer if Kai lives in a different folder than
        # the one on screen.
        data["workspace_id"] = session.id
        data["workspace"] = session.profile.name or Path(session.folder).name
        data["transcript"] = term.transcript.tail(max(1, min(lines, 300)))
        return data

    # -------------------------------------------------------- name resolution
    def find_terminal(self, wanted: str) -> tuple[Session, Terminal] | None:
        """A pane by call-sign, anywhere — the front workspace answering first.

        The FRONT workspace deciding first is what makes positional call-signs
        unambiguous: every workspace numbers its panes from T1, so "T2" means
        the second pane of the tab the user is looking at. Nothing else could
        be meant — the other tabs are not on screen.

        The search continues into the background workspaces only when the front
        one has no such pane. That is for CUSTOM call-signs, which a user gives
        a pane precisely so they can address it from anywhere: "tell Mika to
        run the tests" is an instruction to Mika, not a request to first go and
        find which tab Mika is in.
        """
        session = self.session
        if session is not None:
            term = session.find(wanted)
            if term is not None:
                return session, term
        for other in self._sessions.values():
            if session is not None and other.id == session.id:
                continue
            term = other.find(wanted)
            if term is not None:
                return other, term
        return None

    def _unknown_terminal(self, wanted: str) -> SessionError:
        """The 'no such pane' error, naming the panes that DO exist.

        The FRONT workspace's panes when there is one, because that is the
        answer to the question actually asked: somebody who says "T7" with four
        panes open needs to hear which numbers this grid has, not a list of
        every pane in every tab.
        """
        if not self._sessions:
            return SessionError("No Agentic-IDE session is running.")
        session = self.session
        panes = (
            session.terminals
            if session is not None
            else [term for s in self._sessions.values() for term in s.terminals]
        )
        known = ", ".join(term.name for term in panes)
        return SessionError(f"No terminal called {wanted!r}. Running: {known or 'none'}.")


def _prevailing_agent(session: Session) -> str:
    """The coding CLI an anchor-less new pane should run.

    An add with NO anchor is the batch behind "open five more", the voice spawn
    path, and the empty grid's button. None of them points at a pane, so none of
    them says which CLI is meant — and the answer has to come from the workspace
    itself.

    Copying the LAST pane was the old answer and the wrong one. ``_renumber``
    sorts the list into the grid's reading order, so "last" means the pane
    furthest bottom-right — whatever happened to be opened most recently, which
    is exactly the pane a user is least likely to mean. Live 2026-08-13: five
    Claude panes and ONE Codex pane opened minutes earlier for an unrelated
    errand, and a spoken order produced a sixth pane running Codex.

    The majority is what "another one of these" means for a workspace as a
    whole, and it is stable under the gesture that caused the surprise — one odd
    pane cannot flip it. A tie falls to the first pane in reading order, so the
    answer is deterministic rather than dependent on how the dict happened to
    iterate, and an empty grid falls back to the default CLI.

    A SPLIT is deliberately not routed through here: it names its anchor, and
    splitting a Claude pane really does mean "another one of these".
    """
    counts: dict[str, int] = {}
    for term in session.terminals:
        if term.agent:
            counts[term.agent] = counts.get(term.agent, 0) + 1
    if not counts:
        return "claude"
    most = max(counts.values())
    for term in session.terminals:
        if term.agent and counts[term.agent] == most:
            return term.agent
    return "claude"


def _unique_name(wanted: str, used: set[str]) -> str:
    """``wanted`` if it is free, otherwise the nearest name that is.

    The two kinds of call-sign need two different repairs, and using the wrong
    one costs a pane its voice:

    * a **position** that is taken moves to the next free NUMBER. Suffixing it
      would produce "T1 2" — neither a position nor anything a person can say
      out loud, so the pane would sit there unaddressable;
    * a **custom name** keeps the familiar numeric suffix ("Mika 2"), which is
      how a person distinguishes two of the same thing anyway.
    """
    if normalize(wanted) not in used:
        return wanted
    if position_of(wanted) is not None:
        return free_positions([name for name in used if position_of(name) is not None], 1)[0]
    suffix = 2
    while normalize(f"{wanted} {suffix}") in used:
        suffix += 1
    return f"{wanted} {suffix}"


def _mark_restored_continuations(terminals: list[Terminal]) -> None:
    """Flag the restored panes that will come back in the middle of a job.

    Runs off the event loop (each check stats the coding CLI's history) and
    never raises: a pane whose history cannot be read is left unflagged, which
    costs an offer to continue it and nothing else.

    Holding a handle is not the same as having a conversation — a pane that was
    opened and never used holds an id that points at nothing — so this asks the
    CLI's own history, exactly as the resume offer does.
    """
    for term in terminals:
        if term.resume is None or not accepts_prompts(term.agent):
            continue
        try:
            term.continuation_pending = term.resume_continuation_needed and has_conversation(
                term.agent, term.resume, account_home(term.agent, term.account)
            )
        except Exception as exc:  # noqa: BLE001 - a restore must never fail on this
            logger.debug(
                "Agentic IDE: could not tell whether {} has work to continue: {}",
                term.name,
                exc,
            )


def terminals_added_event(session: Session, created: list[Terminal], *, source_layer: str) -> Any:
    """The bus event announcing new panes to every connected client.

    A free function rather than a registry call, because the registry has no bus:
    it is a plain in-process holder, and reaching for a process-wide bus from
    inside it would be the lateral dependency the architecture forbids. The two
    callers that DO hold one (the REST route and the voice fast-path) build the
    event here so both send exactly the same payload.
    """
    from jarvis.core.events import AgenticIdeTerminalsAdded

    return AgenticIdeTerminalsAdded(
        session_id=session.id,
        names=tuple(t.name for t in created),
        agent=created[0].agent if created else "",
        folder=session.folder,
        source_layer=source_layer,
    )


def workspace_changed_event(
    session: Session | None,
    reason: str,
    *,
    source_layer: str,
    open_workspaces: int | None = None,
) -> Any:
    """The bus event announcing that a WORKSPACE appeared, moved or went away.

    Same shape and the same reasoning as :func:`terminals_added_event`: built
    here so every caller that holds a bus sends an identical payload, and read
    by clients as a trigger to re-fetch rather than as the state itself.

    ``session`` may be None — "closed" is a perfectly good thing to announce,
    and the client needs to hear it most of all.
    """
    from jarvis.core.events import AgenticIdeWorkspaceChanged

    if open_workspaces is None:
        try:
            open_workspaces = len(get_registry().workspaces())
        except Exception:  # noqa: BLE001 - a count must never cost the event
            open_workspaces = 0
    return AgenticIdeWorkspaceChanged(
        session_id=session.id if session is not None else "",
        reason=reason,
        folder=session.folder if session is not None else "",
        name=session.name if session is not None else "",
        open_workspaces=open_workspaces,
        source_layer=source_layer,
    )


def prompt_sent_event(session: Session | None, term: Terminal, *, source_layer: str) -> Any:
    """The bus event announcing that Jarvis typed a prompt into a pane.

    The preview is deliberately short. This exists so a client can SAY that
    something was sent — the prompt itself is already on screen in the pane it
    went to, and putting a full brief on the bus would put it in every event
    log as well.
    """
    from jarvis.core.events import AgenticIdePromptSent

    preview = " ".join((term.last_prompt or "").split())
    return AgenticIdePromptSent(
        session_id=session.id if session is not None else "",
        terminal=term.name,
        agent=term.agent,
        submitted=term.submitted,
        preview=preview[:160],
        source_layer=source_layer,
    )


def coding_mode_active() -> bool:
    """Is Jarvis an Agentic IDE right now?

    ONE answer to that question, for every layer that needs it. A workspace has
    to be open AND its focused coding mode has to be on — either half alone is
    not the mode: a workspace with the mode off is just terminals on a screen,
    and the flag without a workspace addresses nothing.

    It exists as a named predicate rather than as an inline
    ``session is not None and session.focus_mode`` in each caller because the
    two halves are exactly the kind of rule that drifts: the global indicator,
    the context block and (in future) the routing gates must agree, and three
    hand-written copies of a two-part condition are three chances to disagree
    about whether the user is in coding mode.

    Never raises — an optional surface must not be able to break a caller.
    """
    try:
        session = get_registry().session
    except Exception:  # noqa: BLE001 - optional surface, never fatal
        return False
    return session is not None and bool(session.focus_mode)


def running_call_signs() -> list[str]:
    """Call-signs of the open workspace, or ``[]`` when none is open.

    ONE answer for every layer that has to know which names are currently
    speakable — the turn planner, the realtime session instructions, and the
    addressed-terminal detector. They must agree: a layer that reads a
    different roster than the detector either routes a turn nobody can serve or
    withholds one the workspace owns.

    Deliberately NOT gated on ``coding_mode_active``. The panes carry their
    call-signs the moment they exist, and a user who says "what has Dana done"
    with the focus toggle off means the same terminal they would mean with it
    on. Callers that need the stricter mode ask ``coding_mode_active`` as well.

    Never raises — an optional surface must not be able to break a caller.
    """
    try:
        session = get_registry().session
    except Exception:  # noqa: BLE001 - optional surface, never fatal
        return []
    if session is None:
        return []
    return [term.name for term in session.terminals]


def coding_mode_event(session: Session | None, *, source_layer: str) -> Any:
    """The bus event announcing the EFFECTIVE coding mode to every client.

    Built here, next to the predicate it reports, so the payload can never claim
    a mode the predicate would deny. ``session`` is the workspace the switch
    happened in, or ``None`` when there is none left to be in coding mode.
    """
    from jarvis.core.events import AgenticIdeCodingModeChanged

    enabled = session is not None and bool(session.focus_mode)
    return AgenticIdeCodingModeChanged(
        session_id=session.id if session is not None else "",
        enabled=enabled,
        folder=session.folder if (session is not None and enabled) else "",
        workspace=session.name if (session is not None and enabled) else "",
        source_layer=source_layer,
    )


_REGISTRY: Registry | None = None


def get_registry() -> Registry:
    """The process-wide Agentic-IDE registry (created on first use)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry()
    return _REGISTRY


def reset_registry() -> None:
    """Drop the registry — tests only."""
    global _REGISTRY
    _REGISTRY = None


__all__ = [
    "AGENT_BINARIES",
    "AGENT_DISPLAY",
    "MAX_PROMPT_CHARS",
    "MAX_TERMINALS",
    "MAX_WORKSPACES",
    "PLAIN_TERMINAL",
    "Registry",
    "Session",
    "SessionError",
    "SessionNotReady",
    "Terminal",
    "accepts_prompts",
    "agent_argv",
    "agent_display",
    "coding_mode_active",
    "coding_mode_event",
    "get_registry",
    "is_runnable",
    "prompt_sent_event",
    "reset_registry",
    "running_call_signs",
    "sanitize_prompt",
    "terminals_added_event",
    "workspace_changed_event",
]
