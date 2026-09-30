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
import hashlib
import os
import re
import shlex
import shutil
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from loguru import logger

from jarvis.workspace import agents as workspace_agents
from jarvis.workspace import launch_picks

from . import (
    fork,
    layout_tree,
    library,
    opening,
    pane_sessions,
    prompt_history,
    recap_engine,
    remote,
    resume_store,
)
from .activity import NO_READING, Reading, has_work_behind_it, observed
from .agent_sessions import (
    ResumeHandle,
    can_fork,
    can_resume,
    discover,
    fork_argv,
    has_conversation,
    launch_extra,
    reports_session_starts,
    resume_argv,
    resume_env,
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


#: The three picks a pane runs on, as the sentences about them name them.
_PICK_WORDS: dict[str, str] = {
    "model": "model",
    "effort": "reasoning effort",
    "permission_mode": "permission stance",
}


def accepts_prompts(agent: str) -> bool:
    """May Jarvis type into this pane from the outside (prompt bar, voice, CLI)?

    Only into an AGENT. A plain terminal is a live shell prompt, so an injected
    line would not be read by a coding agent — it would be EXECUTED, which turns
    the one keystroke channel this app exposes into arbitrary command execution
    by voice. That is precisely the boundary the module docstring's rule 1 draws,
    and it is why a plain terminal is typed into by hand or not at all.

    An agent may also decline the channel: one whose own interface is not the
    terminal (DeepSeek Harness boots a server in the pane and chats in the
    browser) has no reader for a typed line. Answering "yes" there would cost a
    silent no-op reported to the user as a delivered prompt, so the registry
    entry says so and this is the one place that reads it.
    """
    spec = workspace_agents.get_agent(agent)
    return spec is not None and spec.is_coding_agent and spec.accepts_typed_prompts


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


# How many coding sessions one workspace may hold. Every pane is a full CLI
# process with its own pseudo-terminal and socket, so this is a resource
# ceiling, not a layout rule. It matches the largest grid the workspace draws
# (MAX_GRID_COLUMNS x MAX_GRID_ROWS); past it, a second workspace tab is the
# better home. Voice call-signs cover it (see `names._NUMBER_WORDS`). Mirrored
# by the frontend's `workspaceDocking.ts`, which reads the count from the state.
MAX_GRID_COLUMNS = 4
MAX_GRID_ROWS = 4
MAX_TERMINALS = MAX_GRID_COLUMNS * MAX_GRID_ROWS


def balanced_columns(count: int) -> int:
    """Columns of the even grid ``count`` panes are dealt into, row by row.

    Two panes read best side by side; three to eight form two rows (six is
    3 x 2); beyond that the grid grows a row per four panes, never wider than
    MAX_GRID_COLUMNS. Mirrored by `balancedLayout` in ``workspaceDocking.ts``.
    """
    if count <= 2:
        return max(1, count)
    rows = 2 if count <= 2 * MAX_GRID_COLUMNS else -(-count // MAX_GRID_COLUMNS)
    return min(MAX_GRID_COLUMNS, -(-count // rows))


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
#
# That is the FLOOR. The slot is actually held until the pane shows its input
# line — the moment the CLI has finished loading — or until COLD_START_HOLD_MAX_S,
# whichever comes first. A fixed second was the whole gate on a machine that
# had booted a minute earlier: eight resumed Claude Code panes went from
# "spawned" to "loading" within two seconds of each other, each one reading a
# session transcript and starting its MCP servers off a cold disk, and the app
# stalled behind them (2026-08-27, BUG-189). The input line is the honest end
# of a cold start; the ceiling keeps a CLI stopped on a login or trust screen
# from holding everyone else's slot.
COLD_START_SETTLE_S = 1.2
COLD_START_HOLD_MAX_S = 15.0

# How long the nudged window size is held before it is put back (see
# ``_nudge_repaint``). A PTY carries one size, not a queue of them: set twice
# within the same event-loop tick, the agent may only ever observe the second
# value, see no change, and redraw nothing. Long enough that the two sizes are
# distinct events for a process that polls or debounces its resize handler,
# short enough that nobody sees a pane one row short.
REPAINT_NUDGE_S = 0.08

# A nudge is a request, and a busy agent may ignore it: measured against Claude
# Code 2.1.283 on Windows (2026-09-28), 10 of 20 nudges sent while it worked
# drew nothing, and a longer hold (0.3 s) or a width nudge did no better. A
# viewer that re-joined on a cut replay then keeps empty rectangles wherever
# the agent's screen does not change. So a full-screen agent's answer — the
# whole-screen erase its repaint opens with — is waited for, and the nudge
# repeated when none comes. Re-sending right away answered within two tries in
# every one of 12 measured runs; five bound the cost for an agent that never
# repaints this way.
REPAINT_CONFIRM_S = 0.5
REPAINT_NUDGE_ATTEMPTS = 5
REPAINT_POLL_S = 0.05

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


def remote_agent_argv(agent: str) -> tuple[str, ...] | None:
    """argv for ``agent`` on a connected computer (a POSIX server).

    Resolved THERE, by the server's own PATH (the pane starts in a login
    shell), so only the command name travels, never this machine's absolute
    path or a Windows shim. A plain terminal is the server's login shell.
    """
    spec = workspace_agents.get_agent(agent)
    if spec is None:
        return None
    if not spec.is_coding_agent:
        return ("bash", "-l")
    if spec.shell_launch:
        return ("sh", "-c", spec.launch_command or "")
    binary = spec.executable or spec.launch_command or spec.name
    name = binary.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".cmd", ".bat", ".exe", ".ps1"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return (name, *spec.launch_args)


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
    if agent == "cursor":
        from jarvis.workspace.cursor_cli import resolve_cursor_binary

        exe = resolve_cursor_binary()
    else:
        exe = shutil.which(binary)
    if exe is None and agent != "cursor":
        for alias in spec.binary_aliases:
            exe = shutil.which(alias)
            if exe is not None:
                break
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
    #: Called — synchronously, ``(cols, rows)`` — whenever the shared PTY takes
    #: a size this viewer did not ask for, so a screen that no longer holds the
    #: pane can follow the one that does (see ``Registry._announce_geometry``).
    #: ``None`` for a viewer that only consumes bytes.
    geometry: Any = None


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
    prompt_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Hidden from the chat-mode session list. The pane itself keeps running —
    # archive is a list filter, not a close. Survives a restart because the
    # resume snapshot carries it, so a cleaned-up sidebar stays cleaned up.
    archived: bool = False
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
    # What this pane was OPENED on: the model, the effort level and the
    # permission stance picked for it before it started
    # (:mod:`jarvis.workspace.launch_picks`). Empty means "whatever the CLI
    # itself defaults to", which is what every pane opened before these
    # existed still gets.
    #
    # Held for the pane's whole life rather than spent at spawn, because a
    # resume rebuilds the launch argv from scratch: without them a pane that
    # came back from a restart would quietly drop to the CLI's defaults while
    # its header still claimed the picks it was opened on. A running CLI owns
    # these three from the moment it starts — changing one here would be a
    # label the process never sees, so nothing writes them after the spawn
    # EXCEPT `Registry.apply_picks`, which writes one only after the CLI's own
    # command for it was typed into the pane and left the input line: then the
    # process has seen it, and the field says what the pane runs on now.
    model: str = ""
    effort: str = ""
    permission_mode: str = ""
    #: When `apply_picks` last changed each of the three (epoch seconds), so
    #: the timeline can tell a pick the chat just typed in from the older
    #: value the CLI's record still carries until its next reply.
    picked_at: dict[str, float] = field(default_factory=dict)
    # Where this pane's agent runs, when that is NOT the workspace folder: the
    # git worktree a fork was opened in (see `Registry.fork_terminal`). Empty
    # means the workspace folder, which is every pane that is not such a fork.
    # `branch` is that worktree's branch, shown in the pane header.
    folder: str = ""
    branch: str = ""
    # The conversation this pane is a COPY of, until its first process has
    # copied it. Spent by `attach` on the first spawn (the copy then has its
    # own handle in `resume`), so a restart afterwards resumes the copy rather
    # than forking the original a second time.
    fork_from: ResumeHandle | None = None
    # Where this pane's agent RUNS when that is a connected computer rather
    # than this machine (``jarvis.computers``): the computer's id, the folder
    # there, and the snapshot commit the code left here as. The agent then
    # lives in a tmux session on that computer and keeps working while this
    # app is closed; this pane is only its viewer. Empty = this machine.
    computer_id: str = ""
    remote_folder: str = ""
    offload_snapshot: str = ""
    # Set while the pane's folder is on its way to (or back from) its
    # computer: a viewer attaching then is told "not yet" instead of starting
    # the agent in the wrong place. Never persisted.
    placing: str = ""
    # The last placement's one-line report for the UI ("Not copied: .env").
    notice: str = ""
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
    # Restored from the snapshot: was this pane's agent running when the app
    # last saved? Read once, by ``_resume_after_reboot``.
    was_running: bool = False
    # Re-joined after an app restart while its last checkpoint saw it working:
    # whatever it finished in the meantime nobody was watching. The pane
    # watcher reads this once, on first sight, to report that finish instead
    # of treating it as history (``notifications.ActivityWatcher._step``).
    worked_while_detached: bool = False
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
    # Resized while its agent was still loading, before it had taken the whole
    # screen — so no repaint check could run for that size, and a CLI that was
    # not listening yet may still be drawing for the size it was born with.
    # Settled once the input line appears (see `_prompt_ready_then_settle`).
    resized_while_booting: bool = False
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
    # Is the job this pane is working on one the user gave THROUGH Jarvis (a
    # spoken order, the IDE prompt bar)? Then Jarvis owes the user a spoken
    # "here is what it did" when the pane stops (see `.voice_readback`). Set by
    # the prompt paths that ask for it, cleared by a job typed in by hand and by
    # the readback itself. Ephemeral: a restored pane owes nobody anything.
    voice_readback: bool = False
    # The user's own words for that job — what the readback is about.
    voice_readback_request: str = ""
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
    # Was this pane last observed actively working? Checkpointed into the
    # resume snapshot by the pane watcher. Evidence only — nothing is ever
    # typed on its strength: a re-joined agent that was working and has since
    # stopped is reported by the bell (``worked_while_detached``), and a
    # resumed Claude pane finishes an interrupted turn by itself
    # (``agent_sessions.resume_env``).
    resume_continuation_needed: bool = False
    # This pane's agent died because the PTY host went away under a running
    # app (not because it exited): it is resumed once a host is back.
    lost_with_host: bool = False
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
    # The process generation re-joined after an app restart while its agent
    # already had a job — a conversation on disk, prompts sent, or work seen at
    # the last checkpoint. The instruction behind that job was submitted in the
    # previous app's lifetime, so ``submit_generation`` cannot prove it; this
    # does, for exactly this process (a respawn moves the generation on).
    # Without it every re-joined agent read "done" while still working. The
    # same proof is stamped on a process RESUMED to finish a turn that was cut
    # off mid-work (``agent_sessions.resume_env``): it carries on by itself, so
    # no submit in this lifetime exists either.
    adopted_generation: int = -1
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
            # What this pane was OPENED on. Empty means "the CLI's own
            # default", which is every pane opened before these existed. Read
            # by the surfaces that SAY what a session runs on — otherwise the
            # picks would be visible only on the command line, and a list of
            # panes could not tell an Opus session from a Sonnet one.
            "model": self.model,
            "effort": self.effort,
            "permission_mode": self.permission_mode,
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
            # Whether a handle EXISTS, never the handle itself: it is an
            # internal pointer into the CLI's history and no client needs it.
            "has_resume": self.resume is not None,
            # Chat-mode session list only. The grid still draws every pane.
            "archived": self.archived,
            "account": self.account,
            "account_label": account_label(self.account),
            # Set only for a pane running in a git worktree of its own.
            "folder": self.folder,
            "branch": self.branch,
            # Set only for a pane running on a connected computer.
            "computer_id": self.computer_id,
            "remote_folder": self.remote_folder,
            # Can this pane be forked with its conversation? False for a CLI
            # without a fork of its own — the fork then starts a fresh chat.
            "can_fork": can_fork(self.agent),
        }

    def to_row(self) -> dict[str, Any]:
        """This pane as ONE LINE in a list of conversations.

        Deliberately not ``to_dict``: that payload walks the pane's whole
        scrollback twice (the line count and the recap) and carries the layout
        and prompt statistics with it. A session list polls every open
        workspace's panes on a clock, so a dozen scrollback walks per poll is
        the difference between a list and a stutter — and none of what it pays
        for is a line in that list.

        What survives is what the line actually shows: who this pane is, what
        it was last asked to do, and whether it is still doing it. The activity
        reading is the same one the grid's badge uses (:mod:`.activity`), so a
        pane cannot say "working" in one place and "done" in the other.
        """
        reading = self.reading()
        return {
            "key": self.key,
            # The pane's LIFETIME id, not its call-sign: T1 is handed on to a
            # replacement pane, and a list that keys on the call-sign would
            # quietly show the newcomer's row as the dead one's.
            "history_id": self.history_id,
            "name": self.name,
            "agent": self.agent,
            "display_name": self.display_name,
            "accepts_prompts": accepts_prompts(self.agent),
            "status": self.status,
            "exit_code": self.exit_code,
            "activity": reading.activity,
            "activity_since": reading.since,
            "worked": has_work_behind_it(self),
            "started_at": self.started_at,
            "last_output_at": self.last_output_at,
            # The opening of what was last asked of it — the closest thing a
            # terminal has to a chat's title. Capped like the full state's copy.
            "last_prompt": self.last_prompt[:200],
            "last_prompt_at": self.last_prompt_at,
            # The pane's title as its header last showed it — pinned, written by
            # the model, or the work-naming floor read off the screen on the
            # last header poll — else the last prompt sent, else the message
            # that opened the CLI's own conversation. From memory alone, never
            # computed here: this list must not walk a scrollback (see above).
            # Empty only for a pane that has been asked nothing anywhere; the
            # list then names the CLI.
            "recap": recap_engine.known_headline(self),
            # Whether a readable conversation could exist for this pane at all.
            # Whether one is actually on disk is the transcript endpoint's
            # answer; this only says the pane holds a handle to look up.
            "has_resume": self.resume is not None,
            "archived": self.archived,
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
            archived=self.archived,
            column=self.column,
            slot=self.slot,
            resume=self.resume,
            prompts_sent=self.prompts_sent,
            account=self.account,
            account_pinned=self.account_pinned,
            continuation_needed=self.resume_continuation_needed,
            model=self.model,
            effort=self.effort,
            permission_mode=self.permission_mode,
            folder=self.folder,
            branch=self.branch,
            fork_from=self.fork_from,
            computer_id=self.computer_id,
            remote_folder=self.remote_folder,
            offload_snapshot=self.offload_snapshot,
            running=self._counts_as_running(),
        )

    def _counts_as_running(self) -> bool:
        """Should a reboot bring this pane's agent back?

        A live agent, obviously. So is one that died with anything but a clean
        exit: a host that went away with the machine reports an unknown code,
        and a crash is exactly what recovery is for. An agent that exited 0
        ended by itself (``/exit``, a finished task) and stays ended, and a
        pane that never started keeps what the last snapshot said about it.
        """
        if self.status == "live":
            return True
        if self.status == "exited":
            return self.exit_code != 0
        if self.status == "pending":
            return self.was_running
        return False

    def cwd(self, workspace_folder: str) -> str:
        """The folder this pane's agent runs in: its own worktree, else the workspace's."""
        return self.folder or workspace_folder


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
    # Durable ownership in the project library, independent of the workspace ID.
    project_id: str = ""
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
    # The pane last selected on screen, kept when the section goes off screen
    # (unlike ``surface_prompt_target``) and saved with the workspace, so a
    # reopened app puts the focus back where it was (RUB-102).
    focused: str = ""
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
        if wanted.startswith("pane:"):
            return next((t for t in self.terminals if t.history_id == wanted[5:]), None)
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
            "project_id": self.project_id or library.project_id_for(self.folder),
            "folder": self.folder,
            "name": self.name,
            "project": self.profile.to_dict(),
            "created_at": self.created_at,
            "focus_mode": self.focus_mode,
            "focused": self.focused,
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
            "id": self.id,
            "project_id": self.project_id or library.project_id_for(self.folder),
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
            "project_id": self.project_id or library.project_id_for(self.folder),
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
    on_geometry: Any = None,
) -> bool:
    """Attach a viewer to ``term`` and optionally make it the owner.

    Newest last, and never twice: a socket that re-attaches (a resize, a resume
    retry) replaces its own entry rather than being fed the same bytes twice.
    A background viewer may watch without taking the one shared PTY geometry
    away from the window that is actually in front of the user.
    """
    term.watchers = [w for w in term.watchers if not _same_viewer(w.output, on_output)]
    term.watchers.append(PaneViewer(on_output, on_exit, cols, rows, on_geometry))
    if len(term.watchers) > MAX_WATCHERS:
        del term.watchers[0 : len(term.watchers) - MAX_WATCHERS]
    owner_is_attached = any(
        _same_viewer(watched.output, term.viewer_output) for watched in term.watchers
    )
    if claim_owner or term.viewer_output is None or not owner_is_attached:
        term.viewer_output = on_output
        term.viewer_exit = on_exit
    return _same_viewer(term.viewer_output, on_output)


def _tell_geometry(watched: PaneViewer, cols: int, rows: int) -> None:
    """Hand one viewer the size the PTY is really in — never fatally.

    One screen that cannot be told (a socket mid-close, a handler that raises)
    must not cost the resize that already happened, nor the other screens
    their notice.
    """
    if watched.geometry is None:
        return
    try:
        watched.geometry(cols, rows)
    except Exception as exc:  # noqa: BLE001 - one viewer's notice, not the resize
        logger.debug(
            "Agentic IDE: could not report a {}x{} geometry to a viewer: {}", cols, rows, exc
        )


def _announce_geometry(term: Terminal, cols: int, rows: int, *, except_viewer: Any = None) -> None:
    """Tell every attached viewer BUT ``except_viewer`` the PTY's new size.

    A pane open in two windows has two screens and one pseudo-terminal, and the
    screen that does not hold the pane has no way of its own to notice that
    the size moved under it: its tile never changed, so it never measures
    again, and it goes on holding a grid the agent is no longer drawing for.
    The agent's next repaint then lands in that grid as fragments — rows
    wrapped at the wrong width, an interface finished into the corner of a
    pane that is much larger (reported 2026-08-25, a desktop pane displaced
    by a browser tab a tool had opened).

    The viewer that ASKED for the size is left out: it reflowed itself before
    asking, and the socket route reports to it separately when — and only
    when — its request was not granted (``report_geometry``).
    """
    for watched in term.watchers:
        if except_viewer is not None and _same_viewer(watched.output, except_viewer):
            continue
        _tell_geometry(watched, cols, rows)


async def _connect_pty_host(*, start: bool) -> Any:
    """Attach to (or start) the PTY host; ``None`` keeps terminals in-process.

    A module function rather than an inline import so tests can replace it, and
    so the terminal stack stays off the import path until a pane needs it
    (AP-26).
    """
    from jarvis.terminal.pty_host_client import HostUnreachable, connect

    try:
        return await connect(start=start)
    except HostUnreachable as exc:
        # A host that is running holds agents; "not yet" is the only safe
        # answer — never a reason to start them a second time.
        logger.warning("Agentic IDE: {} — waiting for it", exc)
        raise SessionNotReady(
            "The terminal host is busy; the panes reconnect when it answers."
        ) from exc


#: The exit code ``RemotePtyManager`` reports for every agent when its host goes
#: away (``pty_manager.UNKNOWN_EXIT_CODE``); spelled out here so this module
#: does not import the terminal stack at load time (AP-26).
_HOST_LOST_CODE = -1


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


class WorkspaceFull(SessionError):
    """The refusal is the pane cap (``MAX_TERMINALS``), not anything else.

    Its own type because callers answer it differently from every other
    refusal — the voice path says "the workspace is full" in the turn's
    language instead of reading the English sentence out — and matching on
    the message's wording broke the moment a message was reworded.
    """


class SessionNotReady(SessionError):
    """The addressed workspace is not open — not "not here", but "not yet".

    Raised where the old code raised a plain ``SessionError`` with the same
    message, and the distinction is the whole point: a pane that connects while
    the backend is still coming up (a restart, a workspace not restored yet) is
    asking about a workspace that WILL exist, and a viewer told "no such pane"
    stops trying for good. Every caller that can wait must be able to tell the
    two apart — see the PTY socket's close codes.
    """


class PlacementError(SessionError):
    """A pane could not be set up on (or brought back from) a computer.

    Its own type so the HTTP layer can answer 502 — the server, the network or
    the copy failed — instead of blaming the request.
    """


class _Inherit:
    """ "Run the new pane where its neighbours run" — the default placement."""

    def __repr__(self) -> str:
        return "INHERIT_PLACEMENT"


#: ``add_terminal(computer_id=...)`` default: a split runs where its anchor
#: runs, any other new pane where the whole workspace runs.
INHERIT_PLACEMENT: Any = _Inherit()


def _remote_commands(terms: Sequence[Terminal]) -> dict[str, str]:
    """What must be on a computer's PATH before ``terms`` can start there."""
    needed = {"tmux": "tmux"}
    for term in terms:
        spec = workspace_agents.get_agent(term.agent)
        argv = remote_agent_argv(term.agent)
        if spec is not None and spec.is_coding_agent and not spec.shell_launch and argv:
            needed[term.display_name] = argv[0]
    return needed


def _copy_key(local: str) -> tuple[str, Path | None]:
    """Blocking half of ``Registry._copy_root``: one git call and a realpath."""
    top = remote.git_toplevel(Path(local))
    return os.path.normcase(os.path.realpath(str(top) if top else local)), top


def _changed_since(top: Path, snapshot: str) -> bool:
    """Whether repo ``top``'s working tree differs from offload ``snapshot``.

    Blocking (git). The same comparison ``remote.pull_code`` makes before it
    applies a copy's work; a snapshot git cannot read counts as unchanged,
    as joining did before the check existed.
    """
    try:
        offload_tree = remote._git(top, "rev-parse", f"{snapshot}^{{tree}}")
        return remote.working_tree_id(top) != offload_tree
    except (remote.MoveError, OSError) as exc:
        logger.info("Agentic IDE: offload snapshot {} unreadable: {}", snapshot[:12], exc)
        return False


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
        # One gate per (computer, local folder): copies of one folder to one
        # computer happen one at a time, so a second pane JOINS the first one's
        # copy — sending it again reset the server's folder under the agent
        # already working in it.
        self._copy_locks: dict[tuple[str, str], asyncio.Lock] = {}
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
        # The tasks holding a cold-start slot open until a pane's input line
        # appears (see ``_cold_start_slot``). asyncio keeps only weak
        # references to tasks; without this set a hold could be collected
        # mid-wait and its slot never given back.
        self._cold_start_holds: set[asyncio.Task[None]] = set()
        # The follow-ups checking that a repaint nudge was answered (see
        # ``_confirm_repaint``), held for the same weak-reference reason.
        self._repaint_checks: set[asyncio.Task[None]] = set()
        # The newest of those per pane, so a burst of resizes keeps one alive.
        self._repaint_check_by_pane: dict[str, object] = {}
        # Whether panes may live in the PTY host (``jarvis.terminal.pty_host``)
        # instead of this process. Off until the app turns it on through
        # ``boot_restore``: a registry built by a test, a script or the CLI
        # keeps its terminals in-process and never starts a background process.
        # On when the app's entry point switched it on before the UI could
        # reach the API (``host_mode``); ``enable_host`` / ``boot_restore``
        # turn it on later otherwise. Off for tests, scripts and the CLI.
        from . import host_mode

        self._host_enabled = host_mode.enabled()
        # What ``_manager`` hands a synchronous caller while the host is enabled
        # but not attached yet: an empty pool, never pinned (see ``_manager``).
        self._idle_pool: PtyManager | None = None
        # Set by ``set_surface_context`` when the focused pane changed, so the
        # route can save it without saving on every repeated report.
        self._focus_dirty = False
        self._host_lock = asyncio.Lock()
        self._boot_restored = False
        # The re-join retried in the background when the host did not answer
        # at startup, and the one recovery pass after a host died under us.
        self._rejoin_task: asyncio.Task[None] | None = None
        self._host_recovery: asyncio.Task[None] | None = None

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

    def panes(self) -> list[dict[str, Any]]:
        """Every pane of EVERY open workspace, as rows for a conversation list.

        ``state()`` answers with the front workspace alone, because that is the
        one being drawn. A list of "everything I have running" is the other
        question: a workspace in a background tab is not a workspace that
        stopped, and a list that omits its four agents tells the user they have
        none. So this walks all of them, in tab order, and marks which one is at
        the front rather than hiding the rest.

        Each row carries its workspace's identity — the folder is what the list
        groups by, and the id is what a click needs to bring that tab forward.
        """
        rows: list[dict[str, Any]] = []
        for session in self._sessions.values():
            for term in session.terminals:
                row = term.to_row()
                row["workspace_id"] = session.id
                row["workspace_name"] = session.name
                row["folder"] = session.folder
                row["workspace_active"] = session.id == self._active
                rows.append(row)
        return rows

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

    def _pool(self, term: Terminal) -> Any:
        """The pool that owns ``term``'s process: its computer's, else this machine's."""
        if term.computer_id:
            from jarvis.computers.remote_terminal import pool_for

            return pool_for(term.computer_id)
        return self._manager()

    def _manager(self) -> PtyManager:
        if self._pty is None:
            # Lazy: keeps the terminal stack off the import/boot path (AP-26).
            from jarvis.terminal.pty_manager import PtyManager

            if self._host_enabled:
                # Never PINNED here while the PTY host is in use. A synchronous
                # caller (a write, a resize, a status read) can arrive before
                # the async path has attached to the host; pinning an
                # in-process pool at that moment made every pane of a reopened
                # app start its agent again inside the app while the originals
                # kept running, unseen, in the host (RUB-102, 2026-09-28). An
                # empty pool answers those callers truthfully: nothing of
                # theirs runs in this process.
                if self._idle_pool is None:
                    self._idle_pool = PtyManager()
                return self._idle_pool
            self._pty = PtyManager()
        return self._pty

    def enable_host(self) -> None:
        """Put new and re-joined panes in the PTY host from now on.

        Synchronous and called by the app's entry points BEFORE the UI can
        reach the registry, so no pane ever starts in-process ahead of it.
        """
        from . import host_mode

        host_mode.enable()
        self._host_enabled = True

    # ---------------------------------------------------------- PTY host
    async def _live_manager(self) -> PtyManager:
        """The pool a NEW agent should start in — the PTY host when possible.

        Panes live in the host so that closing, quitting or restarting the app
        detaches from them instead of killing them (see
        ``jarvis.terminal.pty_host``). Everything degrades to the in-process
        pool: host disabled (tests, CLI), host unavailable on this install, or
        an in-process pool that already holds running agents — those would be
        orphaned by a switch, so the switch waits for the next app start.
        """
        if not self._host_enabled:
            return self._manager()
        async with self._host_lock:
            current = self._pty
            if current is not None and (
                # An in-process pool stays for the rest of this process once it
                # exists: it may hold running agents, and a host that failed to
                # start once is not worth a stall on every pane that connects.
                not getattr(current, "persistent", False) or getattr(current, "connected", False)
            ):
                return current
            # No pool yet, or the host went away (its agents went with it —
            # ``RemotePtyManager._lost`` already told their panes).
            remote = await _connect_pty_host(start=True)
            if remote is None:
                return self._fallback_manager()
            self._pty = cast("PtyManager", remote)
            return self._pty

    async def _attached_host(self) -> Any:
        """The PTY host's pool when one is running, WITHOUT starting one.

        Every re-join goes through here, whichever path gets there first — the
        boot pass, a workspace the UI restores, a pane that connects. None when
        the host is disabled, not running, or this process already keeps its
        agents in-process.
        """
        current = self._pty
        if current is not None and getattr(current, "persistent", False):
            if getattr(current, "connected", False):
                return current
        if not self._host_enabled:
            return None
        async with self._host_lock:
            current = self._pty
            if current is not None and not getattr(current, "persistent", False):
                return None
            if current is not None and getattr(current, "connected", False):
                return current
            remote = await _connect_pty_host(start=False)
            if remote is not None:
                self._pty = cast("PtyManager", remote)
            return remote

    @staticmethod
    def _hosted_for(manager: Any) -> dict[str, Any]:
        """The host's not-yet-adopted terminals by pane identity, newest wins."""
        found: dict[str, Any] = {}
        if manager is None or not hasattr(manager, "hosted"):
            return found
        for info in manager.hosted():
            history = str(info.meta.get("history_id") or "")
            if not history:
                continue
            older = found.get(history)
            if older is None or info.started_at > older.started_at:
                found[history] = info
        return found

    def _fallback_manager(self) -> PtyManager:
        """An in-process pool, replacing a host that is gone or cannot start.

        Pinned on purpose (``_manager`` would not pin one in host mode): a host
        that failed to start is not worth a retry on every pane that connects.
        """
        if self._pty is None or getattr(self._pty, "persistent", False):
            from jarvis.terminal.pty_manager import PtyManager

            self._pty = PtyManager()
        return self._pty

    async def boot_restore(self) -> None:
        """Bring back what was open when the app last ran — once per process.

        Called by the web server shortly after boot, off the critical path
        (AP-26). Three steps:

        1. Attach to the PTY host if one is still running from before. Its
           terminals are agents that never stopped: the app was closed, the
           machine was not.
        2. Reopen the workspaces that were open at the last save, in their
           layout. Each pane whose agent is still running in the host is
           re-joined to it (``_adopt_hosted``); the rest come back pending and
           continue their conversation through ``--resume`` when they connect,
           which is what a reboot leaves behind.
        3. End hosted terminals no reopened pane claimed. Nothing can reach
           them any more, and left alone they would keep the host alive forever.

        Deliberately closing every workspace before quitting is respected:
        ``resume_store.all_closed_at`` records it, and a snapshot older than
        that is not reopened unasked.
        """
        if self._boot_restored:
            return
        self._boot_restored = True
        self._host_enabled = True
        try:
            await self._attached_host()
            host_reachable = True
        except SessionNotReady:  # a live host that is silent: waited for below, not an error
            host_reachable = False
        try:
            snapshot = await asyncio.to_thread(resume_store.load)
            closed_at = await asyncio.to_thread(resume_store.all_closed_at)
        except Exception as exc:  # noqa: BLE001 - a broken file must not break boot
            logger.warning("Agentic IDE: restore point unreadable at startup: {}", exc)
            snapshot, closed_at = None, None
        newest = max((w.saved_at for w in snapshot.workspaces), default=0.0) if snapshot else 0.0
        if snapshot is not None and snapshot.workspaces and not self._sessions:
            if closed_at is not None and closed_at >= newest:
                logger.info(
                    "Agentic IDE: every workspace was closed before the app quit — "
                    "nothing reopened at startup"
                )
            else:
                try:
                    await self.restore(snapshot)
                except SessionError as exc:
                    logger.info("Agentic IDE: nothing reopened at startup: {}", exc)
        # Ended only when NO workspace could ever claim them — neither an open
        # one nor any the restore point remembers. A remembered workspace that
        # was not reopened now can still be reopened by hand, and its agents
        # must be there to re-join rather than killed on a guess.
        manager: Any = self._pty
        known = set(self._sessions) | {
            w.session_id for w in (snapshot.workspaces if snapshot is not None else [])
        }
        hosted = manager.hosted() if manager is not None and hasattr(manager, "hosted") else []
        for info in hosted:
            if str(info.meta.get("workspace_id") or "") in known:
                continue
            logger.info(
                "Agentic IDE: ending hosted terminal {} — no workspace claims it ({})",
                info.terminal_id,
                info.meta.get("name", "unnamed"),
            )
            manager.kill_hosted(info.terminal_id)
        if host_reachable:
            await self._resume_lost_agents("the machine restarted or the terminal host ended")
        else:
            # A host is running but did not answer: its agents are alive.
            # Resuming now would start a second copy of each one, so the panes
            # wait and re-join once it answers (``_rejoin_when_reachable``).
            self._rejoin_task = asyncio.create_task(
                self._rejoin_when_reachable(), name="ide-host-rejoin"
            )

    async def _rejoin_when_reachable(self) -> None:
        """Keep trying a live host that did not answer at startup, then re-join."""
        delay = 2.0
        for _attempt in range(30):
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 20.0)
            try:
                manager = await self._attached_host()
            except SessionNotReady:  # still silent; the next attempt follows after the delay
                continue
            for session in list(self._sessions.values()):
                await self._adopt_hosted(session)
            if manager is None:
                # It ended while we waited: its agents ended with it.
                await self._resume_lost_agents("the terminal host ended")
            return
        logger.warning("Agentic IDE: the terminal host never answered; panes stay waiting")

    async def _resume_lost_agents(self, why: str) -> None:
        """Bring back every agent whose process is gone, in every workspace.

        Herdr's native agent-session resume: the process died (with the machine,
        or with a host that crashed), so each agent is started again on ITS OWN
        conversation (the CLI's ``--resume <id>`` and equivalents, see
        ``agent_sessions``) — not a fresh CLI, and not only in the workspace
        somebody happens to open.

        Nothing is typed into any of them. A Claude Code pane is started with
        its CLI's own interrupted-turn resume (``agent_sessions.resume_env``), so
        a task the power cut interrupted runs on by itself; a finished one
        stays finished; other CLIs come back holding their conversation at
        their prompt.

        Which panes: every one still ``pending`` (a pane whose agent survived
        in the host was re-joined and is ``live``) whose agent was running at
        the last save (an agent that ended by itself stays ended) and whose
        CLI really holds the conversation its handle points at. The starts run
        without a viewer through the ordinary cold-start gate, so a dozen
        agents come back a few at a time.
        """
        pending = [
            (session, term)
            for session in self._sessions.values()
            for term in session.terminals
            if term.status == "pending"
            and term.was_running
            and term.resume is not None
            and accepts_prompts(term.agent)
            and not term.computer_id
        ]

        def _with_conversation() -> list[tuple[Session, Terminal]]:
            found: list[tuple[Session, Terminal]] = []
            for session, term in pending:
                try:
                    if has_conversation(
                        term.agent, term.resume, account_home(term.agent, term.account)
                    ):
                        found.append((session, term))
                except Exception as exc:  # noqa: BLE001 - one unreadable history skips one pane
                    logger.debug(
                        "Agentic IDE: could not check {}'s conversation: {}", term.name, exc
                    )
            return found

        starts = await asyncio.to_thread(_with_conversation)
        for session, term in starts:
            self._start_in_background(session, term)
        if starts:
            logger.info(
                "Agentic IDE: resuming {} agent(s) on their own conversations — {}",
                len(starts),
                why,
            )

    def _start_in_background(self, session: Session, term: Terminal) -> None:
        task = asyncio.create_task(
            self._start_unviewed(session, term), name=f"ide-resume-{term.key}"
        )
        self._cold_start_holds.add(task)
        task.add_done_callback(self._cold_start_holds.discard)

    def _host_went_away(self) -> bool:
        """Did the PTY host this process was attached to just drop away?"""
        current = self._pty
        return bool(
            current is not None
            and getattr(current, "persistent", False)
            and not getattr(current, "connected", True)
        )

    def _note_host_lost(self, term: Terminal) -> None:
        """A pane lost its agent with the host: resume it once a host is back."""
        term.lost_with_host = True
        term.was_running = True
        if self._host_recovery is None or self._host_recovery.done():
            self._host_recovery = asyncio.create_task(
                self._recover_from_host_loss(), name="ide-host-recovery"
            )

    async def _recover_from_host_loss(self) -> None:
        """Replace a host that died under a running app and resume its agents.

        Only what a crash of the host itself leaves behind: every agent in it
        died together, the conversations are on disk, and the app is still up.
        A fresh host is started (``_live_manager``) and each lost pane resumes
        its own conversation, exactly as after a reboot — never a fresh CLI and
        never a typed prompt.
        """
        await asyncio.sleep(1.0)  # let every exit of the lost host arrive first
        lost = [
            (session, term)
            for session in self._sessions.values()
            for term in session.terminals
            if term.lost_with_host and term.status == "exited"
        ]
        if not lost:
            return
        logger.warning("Agentic IDE: the terminal host went away — resuming {} agent(s)", len(lost))
        for session, term in lost:
            term.lost_with_host = False
            term.status = "pending"
            self._start_in_background(session, term)

    async def _start_unviewed(self, session: Session, term: Terminal) -> None:
        """Start a pane's agent with nobody watching (the registry records output)."""

        async def discard(_value: Any) -> None:
            return None

        identity = "pane:" + term.history_id
        try:
            await self.attach(
                identity,
                term.pty_cols or term.transcript.cols,
                term.pty_rows or term.transcript.rows,
                discard,
                discard,
                workspace_id=session.id,
                claim_owner=False,
            )
        except SessionError as exc:
            logger.warning("Agentic IDE: {} could not be started to continue: {}", term.name, exc)
        finally:
            self.detach(identity, workspace_id=session.id, viewer=discard)

    async def _adopt_hosted(self, session: Session) -> None:
        """Re-join each pane of ``session`` to its agent, if it is still running.

        A pane is recognised by its ``history_id`` — the stable identity that
        survives renames and the call-sign deduplication — which the spawn
        stored in the hosted terminal's ``meta``. A pane that finds its agent
        becomes ``live`` on the spot, with the host's copy of its screen in the
        replay buffer, so the viewer that connects next takes the ordinary
        "re-join a running agent" path in ``_attach_locked``.
        """
        # Attached here rather than trusted to be attached already: the UI can
        # restore a workspace before the boot pass has reached the host.
        manager: Any = await self._attached_host()
        if manager is None or not hasattr(manager, "adopt"):
            return
        by_history = self._hosted_for(manager)
        for term in session.terminals:
            info = by_history.pop(term.history_id, None)
            if info is not None and not term.computer_id:
                await self._adopt_one(manager, term, info)

    async def _adopt_one(self, manager: Any, term: Terminal, info: Any) -> bool:
        """Re-join ``term`` to the hosted terminal ``info``. True when it is live."""
        on_output, on_closed = self._adopted_callbacks(term)
        try:
            result = await manager.adopt(info.terminal_id, on_output, on_closed)
        except Exception as exc:  # noqa: BLE001 - the pane falls back to a resume
            logger.warning("Agentic IDE: {} could not be re-joined: {}", term.name, exc)
            return False
        if not result.alive:
            return False
        cols = result.cols or term.transcript.cols
        rows = result.rows or term.transcript.rows
        term.transcript.resize(cols, rows)
        term.transcript.feed(result.replay)
        term.replay.clear()
        term.replay.feed(result.replay)
        if result.truncated:
            term.replay.truncated = True
        term.pty_id = info.terminal_id
        term.pty_cols, term.pty_rows = cols, rows
        term.status = "live"
        term.error = ""
        term.exit_code = None
        term.resumed = False
        term.worked_while_detached = term.resume_continuation_needed
        term.resume_continuation_needed = False
        term.lost_with_host = False
        term.started_at = info.started_at or time.time()
        term.last_output_at = time.time() if result.replay else None
        if await self._adopted_with_work(term):
            term.adopted_generation = term.process_generation
        logger.info("Agentic IDE: {} re-joined its running agent after an app restart", term.name)
        return True

    @staticmethod
    async def _adopted_with_work(term: Terminal) -> bool:
        """Had this re-joined agent been given a job before the app restarted?

        Cheap proofs first; the conversation file is the one that survives a
        pane driven purely by hand (its submit stamps died with the old app).
        """
        if term.worked_while_detached or has_work_behind_it(term):
            return True
        if term.resume is None:
            return False
        try:
            return await asyncio.to_thread(
                has_conversation, term.agent, term.resume, account_home(term.agent, term.account)
            )
        except Exception as exc:  # noqa: BLE001 - an unreadable history only costs the word
            logger.debug("Agentic IDE: could not check {}'s conversation: {}", term.name, exc)
            return False

    def _adopted_callbacks(self, term: Terminal) -> tuple[Any, Any]:
        """Output/exit callbacks for an agent this process did not start.

        The same fan-out as the ones ``_attach_locked`` builds at spawn, minus
        the failed-resume recovery: an adopted agent was not just resumed, it
        has been running all along.
        """

        async def _output(_tid: str, text: str) -> None:
            term.transcript.feed(text)
            term.replay.feed(text)
            term.last_output_at = time.time()
            for viewer in _viewers(term):
                await viewer(text)

        async def _closed(_tid: str, code: int) -> None:
            term.pty_id = None
            term.status = "exited"
            term.exit_code = code
            if code == _HOST_LOST_CODE and self._host_went_away():
                self._note_host_lost(term)
            for viewer in _exit_viewers(term):
                await viewer(code)

        return _output, _closed

    # -------------------------------------------------------------- session
    async def start(
        self,
        folder: str,
        requested: list[dict[str, Any]],
        *,
        project_id: str | None = None,
        name: str | None = None,
        computer_id: str | None = None,
    ) -> Session:
        """Open ``folder`` as a NEW workspace with one terminal per request entry.

        ``requested`` entries look like ``{"agent": "claude", "name": "Mika"}``;
        the name is optional and filled from the call-sign pool.

        Opening ADDS a workspace and brings it to the front; whatever was open
        stays open with its agents running. The same folder may be opened more
        than once deliberately: each workspace is a separate set of panes and
        conversations, with a distinct tab name.

        ``computer_id`` runs every pane on that connected computer from the
        start: the server is checked (tmux, each CLI), the folder is copied
        there once, and only then may a pane start — its CLI never has to be
        installed on this machine. A failure closes the workspace again and
        raises :class:`PlacementError`.
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
                    raise SessionError(
                        f"There is no folder called {root} — choose the folder again."
                    )
            except OSError as exc:
                raise SessionError(f"Cannot open {root}: {exc}") from exc

            if project_id:
                # Local: git is needed only for this one check.
                from .git_ops import same_repository

                project = await asyncio.to_thread(library.get_project, project_id)
                if project is None and project_id != library.project_id_for(root):
                    raise SessionError("That project does not exist.")
                if (
                    project
                    and library.project_id_for(root) != library.project_id_for(project.path)
                    # A git worktree of the project's repository belongs to it
                    # too: that is how a workspace gets a checkout of its own.
                    and not await asyncio.to_thread(same_repository, root, project.path)
                ):
                    raise SessionError("The workspace folder must belong to its project.")

            unknown = {
                str(r.get("agent")) for r in requested if not is_runnable(str(r.get("agent")))
            }
            if unknown:
                raise SessionError(f"Unknown agent(s): {', '.join(sorted(unknown))}")

            # A workspace that runs on a computer needs its CLIs THERE, not
            # here — they are checked on the server before anything is copied.
            launcher = remote_agent_argv if computer_id else agent_argv
            missing = sorted(
                {str(r.get("agent")) for r in requested if launcher(str(r.get("agent"))) is None}
            )
            if missing:
                raise SessionError(" ".join(_unavailable(m) for m in missing))
            placing = self._placing_note(computer_id) if computer_id else ""

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
                terminal_name = _unique_name(wanted, used)
                used.add(normalize(terminal_name))
                requested_account = _requested_account(entry)
                resolved_account = resolve_account(agent, requested_account)
                terminals.append(
                    Terminal(
                        key=normalize(terminal_name) or f"t{index}",
                        name=terminal_name,
                        agent=agent,
                        display_name=agent_display(agent),
                        index=index,
                        # Legacy hints are normalized into row-major order
                        # when the workspace is opened below.
                        column=index // WIZARD_COLUMN_HEIGHT,
                        slot=index % WIZARD_COLUMN_HEIGHT,
                        account=resolved_account,
                        # A seat named in the wizard is a deliberate choice and
                        # travels through later splits; one that merely fell to
                        # the active default (or an id that no longer resolves)
                        # is not, and vouches for nothing.
                        account_pinned=requested_account is not None
                        and resolved_account == requested_account,
                        # Picked per pane in the wizard, checked against what
                        # this CLI offers (jarvis.workspace.launch_picks).
                        model=launch_picks.normalize_model(agent, entry.get("model")),
                        effort=launch_picks.normalize_effort(agent, entry.get("effort")),
                        permission_mode=launch_picks.normalize_permission(
                            agent, entry.get("permission_mode")
                        ),
                        computer_id=computer_id or "",
                        placing=placing,
                    )
                )

            session = await self._open_locked(
                root,
                terminals,
                name=name,
                project_id=project_id,
                grid=True,
            )
            logger.info(
                "Agentic IDE session started: {} terminals in {}",
                len(terminals),
                root,
            )
        if computer_id:
            try:
                await self._place_new(session, list(session.terminals), computer_id)
            except BaseException:
                # Also when the request itself was cancelled: a workspace whose
                # panes can never start is closed rather than left behind.
                await self.end(session.id)
                raise
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
        workspace_id: str | None = None,
        project_id: str | None = None,
        grid: bool = False,
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
            id=workspace_id or f"ide_{uuid4().hex[:12]}",
            folder=str(root),
            name=self._available_workspace_name(name or profile.name or root.name or str(root)),
            profile=profile,
            terminals=terminals,
            created_at=time.time(),
            project_id=project_id or library.project_id_for(root),
            # Both callers prepare panes with legacy (column, slot) positions
            # — the wizard's opening arithmetic, a snapshot's remembered grid
            # — and the columns-of-stacks shape those describe is exactly
            # representable as a tree. A restore that remembered a real tree
            # replaces this afterwards (`_restore_one_locked`).
            layout=layout_tree.from_grid((t.key, t.column, t.slot) for t in terminals),
        )
        if grid:
            self._row_major_grid(session)
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

    async def restore_workspace(self, workspace_id: str) -> Session:
        """Explicitly reopen one saved workspace, preserving every session ID."""
        async with self._lock:
            existing = self.get(workspace_id)
            if existing is not None:
                self._focus_locked(existing)
                return existing
            snapshot = await asyncio.to_thread(resume_store.load)
            saved = (
                next(
                    (space for space in snapshot.workspaces if space.session_id == workspace_id),
                    None,
                )
                if snapshot
                else None
            )
            if saved is None:
                raise SessionError("That saved workspace does not exist.")
            session = await self._restore_one_locked(saved)
            if session is None:
                raise SessionError("That saved workspace could not be reopened.")
            await self._persist()
            return session

    async def reorder_workspaces(self, workspace_ids: list[str]) -> list[Session]:
        """Persist tab order without starting, stopping or renaming anything."""
        async with self._lock:
            if len(workspace_ids) != len(self._sessions) or set(workspace_ids) != set(
                self._sessions
            ):
                raise SessionError(
                    "Workspace order must contain every open workspace exactly once."
                )
            self._sessions = {wid: self._sessions[wid] for wid in workspace_ids}
            await self._persist()
            return list(self._sessions.values())

    async def reorder_terminals(self, workspace_id: str, terminal_ids: list[str]) -> Session:
        """Persist row-major order without restarting or renaming an agent."""
        async with self._lock:
            session = self.get(workspace_id)
            if session is None:
                raise SessionError("That workspace is not open.")
            by_id = {term.history_id: term for term in session.terminals}
            if len(terminal_ids) != len(by_id) or set(terminal_ids) != set(by_id):
                raise SessionError(
                    "Terminal order must contain every workspace terminal exactly once."
                )
            session.terminals = [by_id[identity] for identity in terminal_ids]
            self._row_major_grid(session)
            await self._persist()
            return session

    @staticmethod
    def _row_major_grid(session: Session) -> None:
        """Keep legacy geometry consistent with the persistent terminal order."""
        # Rebuild the legacy split tree from the new order. Old geometry
        # must never sort the terminals back into their previous positions.
        columns = balanced_columns(len(session.terminals))
        rows = [
            layout_tree.normalize(
                layout_tree.Split(
                    direction="row",
                    children=[
                        layout_tree.Leaf(term.key)
                        for term in session.terminals[start : start + columns]
                    ],
                    weights=[1.0] * len(session.terminals[start : start + columns]),
                )
            )
            for start in range(0, len(session.terminals), columns or 1)
        ]
        session.layout = (
            layout_tree.normalize(
                layout_tree.Split(
                    direction="column",
                    children=rows,
                    weights=[1.0] * len(rows),
                )
            )
            if rows
            else None
        )
        Registry._renumber(session)

    async def _restore_one_locked(self, space: resume_store.SnapshotWorkspace) -> Session | None:
        """Reopen one remembered workspace. Caller holds the lock."""
        if len(space.terminals) > MAX_TERMINALS:
            raise SessionError(
                f"This saved workspace has {len(space.terminals)} terminals; "
                f"the workspace limit is {MAX_TERMINALS}. Its saved sessions were preserved."
            )
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
                archived=entry.archived,
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
                # The picks the pane was opened on, rebuilt into its argv by
                # the spawn — a restored pane runs on the model, effort and
                # permission stance it left on, not on the CLI's defaults.
                model=entry.model,
                effort=entry.effort,
                permission_mode=entry.permission_mode,
                # A worktree deleted in the meantime would leave the agent with
                # no folder to start in; the pane then falls back to the
                # workspace folder rather than failing the reopen.
                folder=entry.folder if entry.folder and Path(entry.folder).is_dir() else "",
                branch=entry.branch if entry.folder and Path(entry.folder).is_dir() else "",
                fork_from=entry.fork_from,
                # A pane saved while its folder was still being copied has no
                # folder on that computer to start in; it comes back here.
                computer_id=entry.computer_id if entry.remote_folder else "",
                remote_folder=entry.remote_folder,
                offload_snapshot=entry.offload_snapshot,
                was_running=entry.running,
            )

        terminals = [_restored(index, entry) for index, entry in enumerate(space.terminals)]
        # A snapshot remembers the call-signs each workspace had. Another one may
        # hold them now, and two panes answering to one name would make every
        # spoken instruction ambiguous — so a collision is renamed here. Only the
        # label moves; the resume handle underneath it continues the conversation.
        self._dedupe_names(terminals)
        # Restore the same logical workspace identity. Background controllers must
        # not lose their project binding when the persisted panes come back.
        session = await self._open_locked(
            root,
            terminals,
            name=space.name or None,
            workspace_id=space.session_id or None,
            project_id=space.project_id or library.project_id_for(space.folder),
        )
        # Which record this came back from, so a second restore of the same file
        # recognises it rather than opening a duplicate.
        session.restored_from = _restore_key(space)
        focused = session.find(space.focused) if space.focused else None
        session.focused = focused.name if focused is not None else ""
        # The remembered split tree, when the snapshot carries one and it
        # parses. `_open_locked` already built the coarse columns-of-stacks
        # equivalent from the legacy (column, slot) pairs, so a snapshot from
        # an older build — or a truncated tree — degrades to the shape those
        # hints describe instead of failing the reopen.
        if space.layout is not None:
            try:
                # Squared up on the way in: a workspace remembered as columns
                # side by side draws the same picture with one full-height
                # seam welding its bands together, and a reopen is the moment
                # to hand it back its per-band seams (see `rows_outermost`).
                session.layout = layout_tree.rows_outermost(layout_tree.from_dict(space.layout))
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
        # Panes whose agents never stopped (the app was closed, the PTY host
        # kept them) are re-joined now rather than resumed on connect.
        await self._adopt_hosted(session)
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

    async def remove_workspace(self, workspace_id: str) -> bool:
        """Remove a workspace for good: stop its agents and forget its record.

        What the sidebar's "Remove workspace" means, as opposed to ``end``:
        closing keeps the workspace as a closed, restorable row; removing makes
        the row go away. Works on an open workspace and on a remembered closed
        one alike. The folder on disk is never touched. False when the id is
        neither open nor remembered.
        """
        async with self._lock:
            closed = workspace_id in self._sessions
            if closed:
                await self._close_locked(workspace_id)
        # Under the persist lock so a save that read the old state cannot land
        # after the record is gone and write it straight back.
        async with self._persist_lock:
            forgotten = await asyncio.to_thread(resume_store.forget, session_ids={workspace_id})
        return closed or forgotten > 0

    async def close_all(self) -> int:
        """Close every open workspace. Returns how many were closed.

        The restore point survives — see ``end``.
        """
        async with self._lock:
            count = len(self._sessions)
            for workspace_id in list(self._sessions):
                await self._close_locked(workspace_id)
            return count

    def _sync_hooked_session(self, term: Terminal) -> None:
        """Adopt the conversation id the pane's session hook last reported.

        Runs off the loop (one small file read). A pane whose hook never fired
        keeps the id it was launched with.
        """
        latest = pane_sessions.latest_session(term.history_id)
        if latest is None:
            return
        session_id, at = latest
        if term.resume is not None and term.resume.id == session_id:
            return
        term.resume = ResumeHandle("claude_session", session_id, at or time.time())
        logger.info(
            "Agentic IDE: {} is now on conversation {} (reported by its session hook)",
            term.name,
            session_id[:8],
        )

    def _sync_hooked_sessions(self) -> None:
        for session in list(self._sessions.values()):
            for term in list(session.terminals):
                if not term.computer_id and reports_session_starts(term.agent):
                    self._sync_hooked_session(term)

    def runtime_status(self) -> dict[str, Any]:
        """Where the agents run right now, for the UI and ``jarvis api``.

        ``host`` — in the PTY host, surviving the app; ``in_process`` — in this
        process, ending with it; ``idle`` — no agent has been started yet.
        """
        manager = self._pty
        hosted = bool(getattr(manager, "persistent", False)) and bool(
            getattr(manager, "connected", False)
        )
        mode = "host" if hosted else ("in_process" if manager is not None else "idle")
        return {
            "mode": mode,
            "host_pid": int(getattr(manager, "host_pid", 0) or 0) if hosted else 0,
            "persistent_enabled": self._host_enabled,
            "workspaces": len(self._sessions),
            "live_agents": sum(
                1
                for session in self._sessions.values()
                for term in session.terminals
                if term.status == "live" and term.pty_id
            ),
        }

    async def stop_runtime(self) -> int:
        """The explicit "stop everything": end every agent in every workspace.

        Closing the app only detaches from the agents (``jarvis.terminal.pty_host``);
        this is the separate, deliberate action that ends them. It closes each
        workspace the ordinary way — so the restore point stays on offer and
        the next start does not reopen anything unasked — and the PTY host,
        left with no terminal, exits on its own shortly after.
        """
        count = await self.close_all()
        logger.info("Agentic IDE: runtime stopped by request — {} workspace(s) closed", count)
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
                    project_id=session.project_id or library.project_id_for(session.folder),
                    name=session.name,
                    terminals=[t.to_snapshot() for t in session.terminals],
                    layout=layout_tree.to_dict(session.layout) if session.layout else None,
                    focused=session.focused,
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
            await asyncio.to_thread(self._sync_hooked_sessions)
            snapshot = self.snapshot()
            if snapshot is None:
                return
            try:
                await asyncio.to_thread(resume_store.save, snapshot)
            except Exception as exc:  # noqa: BLE001 - the workspace comes first
                logger.warning("Agentic IDE: resume snapshot not written: {}", exc)

    async def persist_resume_activity(self) -> None:
        """Checkpoint the "was working" evidence the bell and the resume use.

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
        for term in session.terminals:
            owner = self._pool(term) if term.computer_id else manager
            if owner is not None:
                if term.pty_id:
                    try:
                        owner.close(term.pty_id)
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
        if not self._sessions:
            # The restore point stays on offer, but the app must not reopen it
            # by itself at the next start: the user shut everything down on
            # purpose (see ``boot_restore``).
            try:
                await asyncio.to_thread(resume_store.note_all_closed)
            except Exception as exc:  # noqa: BLE001 - closing must always succeed
                logger.warning("Agentic IDE: could not record that everything closed: {}", exc)
        logger.info("Agentic IDE session ended: {}", session.id)

    def set_focus_mode(self, enabled: bool) -> bool:
        """Turn the focused coding mode on/off. Returns the resulting state.

        The flag is per workspace and in-memory: it adds the workspace facts
        to the assistant's prompt while the workspace is at the front, and it
        does not survive a restart.
        """
        session = self.session
        if session is None:
            if enabled:
                raise SessionError("No Agentic-IDE session is running — open one first.")
            return False
        session.focus_mode = bool(enabled)
        logger.info("Agentic IDE focus mode {}", "on" if enabled else "off")
        return session.focus_mode

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
        if prompt is not None and prompt.name != session.focused:
            session.focused = prompt.name
            self._focus_dirty = True
        return True

    def take_focus_dirty(self) -> bool:
        """Did a focus change arrive since the last call? (Then it wants saving.)"""
        dirty, self._focus_dirty = self._focus_dirty, False
        return dirty

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
    async def _cold_start_slot(
        self, ready: Callable[[], Awaitable[bool]] | None = None
    ) -> AsyncIterator[None]:
        """Hold one of the few slots for starting an agent CLI.

        Waiting here is what turns "open a workspace" from a burst that pins
        every core into a rolling start (see :data:`COLD_START_LIMIT`). It
        gates STARTS only: a pane re-joining an agent that never stopped — the
        common case on every workspace switch — returns long before this, and
        an agent already running is never made to wait behind one that is
        booting.

        The slot is released AFTER the block, not at its end. What costs is
        the CLI loading itself and its servers, and by then ``spawn`` has
        returned; releasing immediately would let the whole grid through in
        the same instant and the limit would gate nothing. So the slot stays
        taken for at least ``COLD_START_SETTLE_S``, and then until ``ready``
        — the pane's input line, when the caller can name it — answers or
        ``COLD_START_HOLD_MAX_S`` runs out. A spawn that FAILED releases at
        once: nothing is loading, and a broken pane must not hold up the ones
        behind it.
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
                self._hold_cold_start_slot(gate, ready)
            else:
                gate.release()

    def _hold_cold_start_slot(
        self, gate: asyncio.Semaphore, ready: Callable[[], Awaitable[bool]] | None
    ) -> None:
        """Give ``gate`` back once the started pane has finished loading.

        Runs as a task of its own so the attach that started the pane returns
        at once — the viewer connects to a booting CLI exactly as before; only
        the NEXT cold start waits. Whatever happens in here, the slot comes
        back: a readiness check that raises or is cancelled releases in the
        ``finally``, and the ceiling bounds the wait for a CLI that never
        shows an input line (login, trust prompt, an unknown future screen).
        """

        async def _hold() -> None:
            try:
                await asyncio.sleep(COLD_START_SETTLE_S)
                remaining = COLD_START_HOLD_MAX_S - COLD_START_SETTLE_S
                if ready is not None and remaining > 0:
                    try:
                        await asyncio.wait_for(ready(), timeout=remaining)
                    except TimeoutError:
                        # The ceiling IS the release. The pane's own readiness
                        # wait reports a CLI that never showed its input line.
                        pass
                    except Exception:  # noqa: BLE001 — a probe that cannot read
                        # the screen must not keep a slot; log, release.
                        logger.debug(
                            "Agentic IDE: cold-start readiness check failed; releasing the slot",
                            exc_info=True,
                        )
            finally:
                gate.release()

        task = asyncio.get_running_loop().create_task(_hold(), name="cold-start-hold")
        self._cold_start_holds.add(task)
        task.add_done_callback(self._cold_start_holds.discard)

    async def _prompt_ready(self, session: Session, term: Terminal) -> bool:
        """Has this pane painted its input line yet? Waits, bounded.

        The input line, not ``ready_for_prompt``: Claude Code opts out of the
        typing wait because typing early is safe with it — but its composer
        still appears only once it has loaded, and loading is what the slot
        is for.
        """
        from . import fleet_actions

        ready = await fleet_actions.wait_for_input_line(
            session, [term.name], timeout_s=COLD_START_HOLD_MAX_S
        )
        return term.name in ready

    async def _prompt_ready_then_settle(self, session: Session, term: Terminal) -> bool:
        """Wait for the input line, then repaint a pane resized while it loaded.

        A fresh pane is spawned at the size its tile measured on mount, and the
        grid settles a moment later — so its real size reaches the PTY while the
        CLI is still booting. A CLI that is not listening for size changes yet
        keeps drawing for the size it was born with: an interface narrower or
        shorter than its pane, the input box floating mid-pane (reported
        2026-09-29, four panes opened together). The repaint check cannot catch
        this, because it only runs once the agent has taken the whole screen.
        One nudge after the input line appears — when the CLI certainly listens
        — makes it lay out for the size the pane really has.
        """
        generation = term.process_generation
        ready = await self._prompt_ready(session, term)
        if (
            ready
            and term.process_generation == generation
            and term.resized_while_booting
            and term.replay.holds_screen
            and term.pty_cols
            and term.pty_rows
        ):
            term.resized_while_booting = False
            # Shielded: the slot's ceiling may cancel this wait, and a nudge
            # cut between its two resizes leaves the PTY a row short.
            await asyncio.shield(self._nudge_repaint(term, term.pty_cols, term.pty_rows))
        return ready

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
        on_geometry: Any = None,
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

        ``on_geometry`` is told, synchronously, whenever the shared PTY takes a
        size this viewer did not ask for — see ``_announce_geometry``.
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
                on_geometry=on_geometry,
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
        on_geometry: Any = None,
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

        ``on_replay`` is called with ``repaint=True`` when the replay cannot
        stand on its own and a full-screen agent has been asked to repaint: the
        viewer then waits for that repaint's whole-screen erase before showing
        the pane.

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
        if term.placing:
            # Its folder is still travelling to the computer it will run on; a
            # viewer attaching now would start the agent in the wrong place.
            raise SessionNotReady(term.placing)
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
        # A pane on a connected computer is driven by that computer's SSH pool;
        # everything below (re-join, replay, resume) is the same path.
        manager = self._pool(term) if term.computer_id else await self._live_manager()
        if not term.computer_id and not (term.pty_id and manager.has(term.pty_id)):
            # Herdr's rule: attach to the running session first, start one only
            # when there is none. The host may still hold this pane's agent
            # from before the app restarted — whichever path restored the pane.
            hosted = self._hosted_for(manager).get(term.history_id)
            if hosted is not None:
                await self._adopt_one(manager, term, hosted)
        if appearance in THEME_COLOURS:
            term.queries.appearance = appearance
            if term.pty_id and hasattr(manager, "set_appearance"):
                # A hosted agent's emulator queries are answered in the host,
                # from its own copy of the appearance (``pty_host_client.spawn``).
                manager.set_appearance(term.pty_id, appearance)

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
                on_geometry=on_geometry,
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
                    # The screens this viewer just displaced hold the old
                    # grid; they follow the new one now, not at their next
                    # unrelated window resize.
                    _announce_geometry(term, cols, rows, except_viewer=on_output)
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
                if on_replay is not None and needs_repaint and term.replay.holds_screen:
                    # This replay alone cannot rebuild the screen, and the
                    # nudge below will be answered by a whole-screen erase —
                    # so the viewer is told to keep its curtain down until that
                    # erase arrives, instead of revealing the broken tail
                    # while a busy agent takes its time to repaint.
                    await on_replay(replay, repaint=True)
                else:
                    await (on_replay or on_output)(replay)
            if needs_repaint:
                # Either the tail lost its opening frame, or its cursor moves
                # belong to another geometry. Neither can rebuild this viewer.
                # Ask for a fresh paint instead of hoping one arrives.
                await self._nudge_repaint(term, cols, rows)
            logger.debug("Agentic IDE: {} re-joined a running agent", term.name)
            return term

        argv = remote_agent_argv(term.agent) if term.computer_id else agent_argv(term.agent)
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
        # the only one that ever felt it.
        if not term.computer_id and reports_session_starts(term.agent):
            # The conversation the pane is on NOW — after a ``/clear`` or a
            # ``/resume`` inside it that is not the id it was launched with.
            await asyncio.to_thread(self._sync_hooked_session, term)
        home = account_home(term.agent, term.account)
        continuing = resume_argv(term.agent, term.resume)
        # A remote pane's history lives on that computer; its handle was
        # carried there with it (``remote.push_conversation``), so trust it.
        if (
            continuing is not None
            and not term.computer_id
            and not await asyncio.to_thread(has_conversation, term.agent, term.resume, home)
        ):
            logger.info(
                "Agentic IDE: {} has no conversation to continue — starting fresh",
                term.name,
            )
            term.resume = None
            continuing = None
        # A forked pane's FIRST process starts as a copy of the conversation it
        # was forked from (see `Registry.fork_terminal`). Spent here, once: the
        # copy gets its own handle, and every later start resumes THAT rather
        # than forking the original again. A source without a conversation on
        # disk — never prompted, or pruned since — leaves nothing to copy, and
        # the pane starts fresh like any other.
        forking: tuple[tuple[str, ...], ResumeHandle | None] | None = None
        if continuing is None and term.fork_from is not None:
            source = term.fork_from
            term.fork_from = None
            if not term.computer_id and await asyncio.to_thread(
                has_conversation, term.agent, source, home
            ):
                forking = fork_argv(term.agent, source)
            if forking is None:
                logger.info(
                    "Agentic IDE: {} has no conversation to fork — starting fresh",
                    term.name,
                )
        # What the pane was OPENED on, put back on the command line. A resume
        # gets them too: the CLI reads a conversation back, never the model or
        # the permission stance it ran under, so a restored pane without these
        # would silently drop to the vendor's defaults. Every value is checked
        # against what this CLI actually offers before it becomes an argument
        # (:func:`jarvis.workspace.launch_picks.launch_argv`) — a pick it
        # cannot express costs nothing but itself.
        argv = (
            *argv,
            *launch_picks.launch_argv(
                term.agent,
                model=term.model,
                effort=term.effort,
                permission_mode=term.permission_mode,
            ),
        )
        if continuing is not None:
            argv = (*argv, *continuing)
            term.resumed = True
        elif forking is not None:
            extra, minted = forking
            argv = (*argv, *extra)
            # A copy of an existing conversation — the same "continued, not
            # empty" claim a resume makes, and the same early-exit recovery
            # (`_closed`) if the CLI refuses the fork: the pane restarts fresh.
            term.resumed = True
            term.resume = minted
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
        # A fresh start has no interrupted work behind it.
        if not term.resumed:
            term.resume_continuation_needed = False

        # Everything below belongs to a new process attempt. Reset this before
        # spawning so neither early output nor a concurrent notification sweep
        # can inherit the previous PTY's settled-screen evidence.
        term.process_generation += 1
        term.idle_seen = False
        term.resized_while_booting = False
        term.transcript.resize(cols, rows)
        # Readiness belongs to this process. Keeping the dead process's screen
        # here leaves old prompt sigils visible to the readiness probe and makes
        # a freshly spawned CLI look writable before it has emitted one byte.
        term.transcript.clear()
        # A fresh process draws a fresh screen: anything the previous one left
        # in the replay buffer belongs to a terminal that no longer exists, and
        # replaying it to the next viewer would show output from a dead agent.
        term.replay.clear()
        _watch(term, on_output, on_exit, cols, rows, on_geometry=on_geometry)
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
            if code == _HOST_LOST_CODE and self._host_went_away():
                # Not this agent failing: the host holding it went away. It is
                # resumed on its own conversation once a host is back.
                term.status = "exited"
                term.exit_code = code
                self._note_host_lost(term)
                for viewer in _exit_viewers(term):
                    await viewer(code)
                return
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
            if term.computer_id:
                # The CLI's account, config and env are the SERVER's own;
                # nothing of this machine's setup applies there.
                env: dict[str, str] | None = {}
            elif redirected_home is None:
                env = await asyncio.to_thread(self._prepare_spawn, term, term.cwd(session.folder))
            else:
                account_key = os.path.normcase(str(redirected_home))
                account_gate = self._account_prepare_locks.setdefault(account_key, asyncio.Lock())
                async with account_gate:
                    env = await asyncio.to_thread(
                        self._prepare_spawn, term, term.cwd(session.folder)
                    )
        except SessionError as exc:
            term.status = "error"
            term.error = str(exc)
            raise

        if term.resumed and not term.computer_id:
            # The CLI's own "finish the turn that was cut off" (see
            # ``agent_sessions.resume_env``) — how a resumed agent carries on
            # without Jarvis ever typing into it.
            native = resume_env(term.agent)
            if native:
                base = env if env is not None else _without_parent_agent_session(dict(os.environ))
                env = {**(base if base is not None else os.environ), **native}
                if term.resume_continuation_needed:
                    # The turn it re-runs is the job the user handed over before
                    # the process died. Nothing is submitted in THIS lifetime, so
                    # without this proof the agent worked on while every list
                    # filed it under "done" (maintainer report 2026-09-29).
                    term.adopted_generation = term.process_generation
        if not term.computer_id and reports_session_starts(term.agent):
            # Herdr's rule: the pane reports every conversation it starts, so a
            # reboot resumes the one it was really on (``pane_sessions``).
            wiring_argv, wiring_env = await asyncio.to_thread(
                pane_sessions.launch_wiring, term.history_id
            )
            # Right behind the binary, so resume arguments stay last.
            argv = (argv[0], *wiring_argv, *argv[1:])
            base = env if env is not None else _without_parent_agent_session(dict(os.environ))
            env = {**(base if base is not None else os.environ), **wiring_env}

        # The provider/account gate is acquired BEFORE the machine-wide gate.
        # A Codex pane waiting on shared state must never occupy a CPU slot that
        # an unrelated Claude/OpenCode pane could use immediately.
        # The one line that makes a resume complaint answerable.
        #
        # A pane that came back without its conversation and a pane that came
        # back with one look IDENTICAL in the log — and identical again in the
        # snapshot, which records what we intended rather than what the CLI
        # did. Reconstructing "which binary, with which arguments, on whose
        # behalf" afterwards meant reading five files and guessing. It is one
        # INFO line per process start, so it costs nothing on a workspace of a
        # dozen panes and it is there the next time somebody says "it did not
        # resume". The full argv, because the difference that matters lives in
        # the arguments (`--resume <id>`), not in the binary.
        logger.info(
            "Agentic IDE: {} ({}) starting {} — {}",
            term.name,
            term.agent,
            "RESUMED" if term.resumed else "fresh",
            " ".join(argv),
        )
        agent_start_gate = await self._acquire_agent_cold_start(term)
        spawn_succeeded = False
        try:
            # One of a few starts at a time (see COLD_START_LIMIT), and the
            # slot stays taken until this pane's input line appears.
            async with self._cold_start_slot(
                ready=lambda: self._prompt_ready_then_settle(session, term)
            ):
                try:
                    identity = "pane:" + term.history_id
                    if term.stopping or self._locate(identity, session.id) != (session, term):
                        raise SessionError("The selected terminal closed before startup.")
                    pty_session = await manager.spawn(
                        shell_argv=argv,
                        shell_id=f"agentic-ide:{term.key}",
                        cwd=(
                            term.remote_folder or term.cwd(session.folder)
                            if term.computer_id
                            else term.cwd(session.folder)
                        ),
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
                        # Only the PTY host takes (and needs) this: it is how a
                        # still-running agent is matched back to its pane after
                        # an app restart (``_adopt_hosted``).
                        **(
                            {
                                "meta": {
                                    "history_id": term.history_id,
                                    "name": term.name,
                                    "workspace_id": session.id,
                                    "agent": term.agent,
                                }
                            }
                            if getattr(manager, "persistent", False) or term.computer_id
                            else {}
                        ),
                    )
                    if term.stopping or self._locate(identity, session.id) != (session, term):
                        manager.close(pty_session.terminal_id)
                        raise SessionError("The selected terminal closed during startup.")
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
        if term.resume is None and can_resume(term.agent) and not term.computer_id:
            # A CLI that cannot be told its session id (Codex): find out which
            # one it just created, shortly from now. (Not on a remote pane:
            # its history is on that computer, not in this machine's folders.)
            self._schedule_lookup(session, term, term.cwd(session.folder), term.started_at)
        await self._persist()
        return term

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
        self._schedule_lookup(
            owner, term, term.cwd(owner.folder), term.started_at, CONVERSATION_DELAYS_S
        )

    def write(self, key: str, data: str, workspace_id: str | None = None) -> bool:
        """Raw keystrokes from the pane's own xterm (not the injection path)."""
        found = self._locate(key, workspace_id)
        if found is None:
            return False
        owner, term = found
        if not term.pty_id:
            return False
        manager = self._pool(term)
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
            # The user submitted something in the pane themselves: whatever
            # was interrupted before is superseded by this instruction.
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
                if term.reading().activity != "asking":
                    # A new job typed by hand is the user's own, and nobody
                    # asked Jarvis to report on it. Answering the pane's
                    # question keeps the Jarvis job (and its readback) alive.
                    term.voice_readback = False
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

        A full-screen agent may let a nudge pass without redrawing (see
        ``REPAINT_CONFIRM_S``), so for one the answer is checked in the
        background and the nudge repeated — off the caller's path, because
        the caller holds the registry lock every other pane's attach waits on.
        """
        clears_before = term.replay.clears
        if not await self._resize_there_and_back(term, cols, rows):
            return
        self._watch_repaint(term, clears_before)

    def _watch_repaint(self, term: Terminal, clears_before: int) -> None:
        """Check in the background that a size change was answered by a repaint.

        Only for a full-screen agent: a line-mode CLI or a shell answers with no
        whole-screen erase at all, and waiting for one would only nudge it four
        more times. Needs a running loop; a synchronous caller without one (a
        test, a script) simply goes unchecked.
        """
        if not term.replay.holds_screen:
            # Either a line-mode CLI, or a full-screen one still loading. The
            # second cannot be checked yet, so it is settled after boot.
            term.resized_while_booting = True
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (shutdown or sync caller): skip the repaint check.
            return
        # One check per pane: a dragged seam resizes many times a second, and
        # only the newest size's repaint is worth waiting for. An older check is
        # retired by the token below rather than cancelled — cancelling one in
        # the middle of its own nudge would leave the PTY a row short.
        token = object()
        self._repaint_check_by_pane[term.key] = token
        task = loop.create_task(self._confirm_repaint(term, clears_before, token))
        self._repaint_checks.add(task)
        task.add_done_callback(self._repaint_checks.discard)

        def _forget(_done: asyncio.Task[None], key: str = term.key) -> None:
            if self._repaint_check_by_pane.get(key) is token:
                del self._repaint_check_by_pane[key]

        task.add_done_callback(_forget)

    async def _confirm_repaint(
        self, term: Terminal, clears_before: int, token: object | None = None
    ) -> None:
        """Repeat the nudge until the agent's screen has really been redrawn.

        Each retry uses the size the pane has NOW: a viewer may have resized it
        meanwhile, and nudging back to the size captured earlier would undo
        that. A new process in the pane ends the check — its first paint is
        whole anyway — and so does a newer check for the same pane (``token``).
        """
        pty_id = term.pty_id
        generation = term.process_generation
        loop = asyncio.get_running_loop()
        for attempt in range(1, REPAINT_NUDGE_ATTEMPTS + 1):
            deadline = loop.time() + REPAINT_CONFIRM_S
            while loop.time() < deadline:
                if term.replay.clears != clears_before:
                    return
                await asyncio.sleep(REPAINT_POLL_S)
            if term.replay.clears != clears_before:
                return
            if term.pty_id != pty_id or term.process_generation != generation:
                return
            if not term.pty_cols or not term.pty_rows:
                return
            if token is not None and self._repaint_check_by_pane.get(term.key) is not token:
                return
            if attempt == REPAINT_NUDGE_ATTEMPTS:
                break
            clears_before = term.replay.clears
            if not await self._resize_there_and_back(term, term.pty_cols, term.pty_rows):
                return
        logger.info(
            "Agentic IDE: {} did not redraw after {} nudges; its pane may show gaps "
            "until the agent next repaints",
            term.name,
            REPAINT_NUDGE_ATTEMPTS,
        )

    async def _resize_there_and_back(self, term: Terminal, cols: int, rows: int) -> bool:
        """One nudge: the height one row short, then back. False if it failed.

        "Back" means the size the pane holds WHEN the nudge ends, not the one
        passed in. The sleep between the two resizes yields the loop, and a
        viewer's resize landing inside it (a dragged seam, the IDE view shown
        again after a tab switch) has already moved the PTY to the new size.
        Restoring the captured size then put the agent back on the OLD grid
        while ``pty_cols``, the transcript and every viewer's xterm said the
        new one — and nothing ever corrected it, because each side believed
        the sizes agreed. The agent kept formatting for a width nobody showed:
        rows drawn over rows, word tails left behind (reported 2026-09-29).
        """
        pty_id = term.pty_id
        if not pty_id:
            return False
        # The same holds for the start: a nudge only ever varies the height of
        # the size the agent already has. A re-joining viewer that does not own
        # the pane passes ITS size, and nudging to that width would reflow the
        # agent for a window that was never granted the pane.
        cols = term.pty_cols or cols
        rows = term.pty_rows or rows
        manager = self._pool(term)
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
            if term.pty_id != pty_id:
                # A new process owns the pane now and was sized on its own.
                return False
            manager.resize(pty_id, term.pty_cols or cols, term.pty_rows or rows)
            term.last_resize_at = time.time()
        except Exception as exc:  # noqa: BLE001 - a stale screen beats a failed reconnect
            logger.debug("Agentic IDE: could not nudge {} into a repaint: {}", term.name, exc)
            return False
        return True

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
        clears_before = term.replay.clears
        if not self._pool(term).resize(term.pty_id, cols, rows):
            return False
        term.pty_cols, term.pty_rows = cols, rows
        # A viewer's resize is a repaint request like any nudge, and a busy
        # full-screen agent may let it pass (see ``REPAINT_CONFIRM_S``). The
        # viewer has already reflowed its grid, so an unanswered one leaves the
        # old frame shredded across the new rows until something else makes the
        # agent paint (seen after restoring a minimized window, 2026-09-28).
        # Checked, and re-nudged, like a re-join.
        self._watch_repaint(term, clears_before)
        # The TUI answers the new size with a full redraw — shadow it so a
        # finished pane does not read as "working" every time the grid
        # re-lays itself out (chat view toggle, maximize, a dragged seam).
        term.last_resize_at = time.time()
        # Every OTHER screen on this pane is now showing a grid the agent is
        # no longer drawing for; tell them, so they can follow the owner.
        _announce_geometry(term, cols, rows, except_viewer=viewer)
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
            # The survivor spent its time as a watcher following the departed
            # owner's grid, and the restore above leaves it out on purpose (it
            # is the viewer that "asked"). It is told outright instead: whether
            # the PTY moved or not, the size it is in now is the one to show,
            # and a screen holding the old owner's strip would otherwise keep
            # it until something in that window happened to resize.
            if term.pty_cols and term.pty_rows:
                _tell_geometry(survivor, term.pty_cols, term.pty_rows)
            return
        term.viewer_output = None
        term.viewer_exit = None
        term.watchers = []

    # ------------------------------------------------------------- panes
    async def add_terminal(
        self,
        *,
        workspace_id: str | None = None,
        agent: str | None = None,
        name: str | None = None,
        anchor: str | None = None,
        direction: str = "right",
        account: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
        computer_id: Any = INHERIT_PLACEMENT,
        folder: str = "",
        fork_from: ResumeHandle | None = None,
        branch: str = "",
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

        ``model``, ``effort`` and ``permission_mode`` are what the pane RUNS
        ON — the picks a chat surface makes before the first message. Each is
        checked against what the chosen CLI actually offers
        (:mod:`jarvis.workspace.launch_picks`) and one it cannot express is
        dropped, so a stale pick costs the CLI's own default rather than a
        pane that will not start. Unlike the account they are never inherited
        from an anchor: splitting a pane means "another one of these CLI", not
        "another one of these settings", and a model quietly carried onto a
        pane somebody opened to try something else is the confusing kind of
        helpful.

        ``computer_id`` says where the pane RUNS: a connected computer's id,
        ``None`` for this machine, or (the default) where its neighbours run —
        a split beside its anchor, any other pane with the workspace when all
        of it runs on one computer. A pane for a computer is set up there
        before it can start (:meth:`_place_new`) and removed again if that
        fails. ``folder`` opens it in another folder than the workspace's (a
        worktree fork); ``fork_from`` and ``branch`` make the pane a fork
        (:meth:`fork_terminal`) from the moment it exists, so no viewer can
        spawn it as a fresh chat first.
        """
        selected_id = workspace_id or self.active_id
        async with self._lock:
            session = self.get(selected_id) if selected_id else None
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            if len(session.terminals) >= MAX_TERMINALS:
                raise WorkspaceFull(
                    f"This workspace already has the maximum of {MAX_TERMINALS} terminals."
                )
            if direction not in ("right", "down", "left", "up", "above", "below"):
                raise SessionError("Direction must be 'right', 'down', 'left', or 'up'.")

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
            target = self._new_pane_computer(session, base if anchor else None, computer_id)
            launcher = remote_agent_argv if target else agent_argv
            if launcher(chosen) is None:
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
                model=launch_picks.normalize_model(chosen, model),
                effort=launch_picks.normalize_effort(chosen, effort),
                permission_mode=launch_picks.normalize_permission(chosen, permission_mode),
                folder=folder,
                branch=branch,
                fork_from=fork_from,
                computer_id=target,
                placing=self._placing_note(target) if target else "",
            )
            session.terminals.append(term)
            # Where it goes is the tree's business, and the distinction is the
            # whole feature: a NAMED anchor is a split — the new pane carves
            # the clicked pane's own rectangle and no other pane changes its
            # PLACE — while an anchor-less add ("open five more", the empty
            # grid's button) joins the workspace edge as a full-height column,
            # because no pane was chosen to sit beside.
            if anchor and base is not None:
                session.layout = layout_tree.split_pane(
                    session.layout,
                    base.key,
                    term.key,
                    direction,
                )
            else:
                session.layout = layout_tree.append_pane(session.layout, term.key)
            # A split or an appended column may not leave the largest grid the
            # workspace draws; past it the panes are dealt into the even grid
            # instead (voice and the CLI have no preview to stop them first).
            columns, rows = layout_tree.grid_span(session.layout)
            if columns > MAX_GRID_COLUMNS or rows > MAX_GRID_ROWS:
                self._row_major_grid(session)
            # Then every terminal back to an equal share — the same act as the
            # grid's "even out" button, run for the user on every open — EXCEPT
            # inside a container whose boundaries were dragged by hand
            # (`Split.pinned`): a size chosen on purpose survives the next
            # open, everything not chosen comes out even. The split decided
            # the SHAPE; its halved weights are not kept where nobody chose
            # them, because a pane split off one that had been dragged small
            # arrived as a sliver (maintainer request, 2026-08-22). Doing it
            # here rather than in the grid covers every way a terminal opens —
            # split button, batch, voice, CLI — and the persisted layout agrees
            # with the screen.
            session.layout = layout_tree.evened(session.layout)
            self._renumber(session)
            await self._persist()
            logger.info(
                "Agentic IDE: added terminal {} ({}) {} of {}",
                term.name,
                term.agent,
                direction,
                base.name if base else "the grid",
            )
        if term.computer_id:
            try:
                await self._place_new(session, [term], term.computer_id)
            except BaseException:
                # Nothing ever started (the pane was gated); take it away rather
                # than leave a pane that can never start — also when the request
                # itself was cancelled.
                await self.close_terminals([term.name], workspace_id=session.id)
                raise
        return term

    def fork_suggestion(self, wanted: str, workspace_id: str | None = None) -> dict[str, Any]:
        """What the fork dialog offers for pane ``wanted``, before anything is made.

        Blocking (a few ``git`` calls) — callers run it in a worker thread.
        """
        found = self._locate(wanted, workspace_id)
        if found is None:
            raise self._unknown_terminal(wanted)
        session, term = found
        base = term.cwd(session.folder)
        return {
            "name": fork.suggest_name(base, term.name, recap_engine.known_headline(term)),
            "in_repo": fork.repo_root(base) is not None,
            "can_fork": can_fork(term.agent),
            "has_conversation": term.resume is not None,
        }

    # ------------------------------------------------------------ placement
    async def place_terminal(
        self, key: str, *, workspace_id: str | None, computer_id: str | None
    ) -> dict[str, Any]:
        """Run pane ``key`` on ``computer_id`` from now on (``None`` = this machine).

        To a computer ("offload"): the pane's folder travels as it is, uncommitted
        edits included, its conversation is copied so the agent continues with
        ``--resume``, the local process ends and the agent starts again THERE,
        inside tmux, where it keeps running while this app is closed.

        Back ("bring back"): the agent on the server ends, the server's work and
        the conversation come home (``remote.pull_code`` never overwrites local
        changes made meanwhile), and the pane runs here again.

        A pane that is not running just changes place; it starts there on its
        next attach.
        """
        found = self._locate(key, workspace_id)
        if found is None:
            raise SessionError(f"Unknown terminal: {key}")
        session, term = found
        target = computer_id or ""
        if term.computer_id == target:
            return {"moved": False, "message": "The pane already runs there."}
        if term.placing:
            raise SessionError(f"{term.name} is being moved already.")
        if term.computer_id and target:
            # Straight from one computer to another would copy this machine's
            # stale folder and end the agent there with its work unreturned.
            raise SessionError(
                f"{term.name} runs on {self._computer_label(term.computer_id)}. "
                "Bring it back to this computer first."
            )
        if not target:
            back, failures, messages = await self._bring_back_group([(session, term)])
            for failed, error in failures:
                if failed is term:
                    raise PlacementError(error)
            for other in back:
                if other is not term:
                    messages.append(
                        f"{other.name} came back with it: they worked in one copy there."
                    )
            return {"moved": True, "message": " ".join(messages), "terminal": term.to_dict()}
        async with term.attach_lock:
            was_live = bool(term.pty_id)
            message = await self._offload_locked(session, term, target, {})
            if was_live:
                await self._restart_in_place(session, term)
        await self._persist()
        return {"moved": True, "message": message, "terminal": term.to_dict()}

    async def place_workspace(
        self, workspace_id: str, *, computer_id: str | None
    ) -> dict[str, Any]:
        """Move every pane of a workspace, one folder transfer per folder.

        To a computer: a pane already on ANOTHER computer stays there (the
        messages say so) — moving it from there would lose its work. Back:
        see :meth:`_bring_workspace_back`. Whatever moved before a failure is
        remembered.
        """
        session = self.get(workspace_id)
        if session is None:
            raise SessionError("Unknown workspace.")
        target = computer_id or ""
        if not target:
            return await self._bring_workspace_back(session)
        placements: dict[str, tuple[remote.Placement, str]] = {}
        moved: list[str] = []
        messages: list[str] = []
        try:
            for term in list(session.terminals):
                if term.computer_id == target or term.placing:
                    continue
                if term.computer_id:
                    messages.append(
                        f"{term.name} stays on {self._computer_label(term.computer_id)}; "
                        "bring it back first to move it."
                    )
                    continue
                async with term.attach_lock:
                    was_live = bool(term.pty_id)
                    message = await self._offload_locked(session, term, target, placements)
                    if was_live:
                        await self._restart_in_place(session, term)
                moved.append(term.key)
                if message and message not in messages:
                    messages.append(message)
        finally:
            await self._persist()
        return {"moved": moved, "messages": messages}

    async def _bring_workspace_back(self, session: Session) -> dict[str, Any]:
        """Bring every remote pane of ``session`` home (see :meth:`_bring_back_group`).

        Fails only when no pane came back; otherwise the messages name each
        pane that stayed on its computer and why.
        """
        away = [(session, t) for t in session.terminals if t.computer_id and not t.placing]
        back, failures, messages = await self._bring_back_group(away)
        if failures and not back:
            raise PlacementError(" ".join(messages))
        return {"moved": [t.key for t in back], "messages": messages}

    async def _bring_back_group(
        self, panes: list[tuple[Session, Terminal]]
    ) -> tuple[list[Terminal], list[tuple[Terminal, str]], list[str]]:
        """Bring ``panes`` home together, one folder transfer per shared copy.

        Returns ``(back, failures, messages)``. Every pane that works in the
        same copy as one of ``panes`` comes along, from any workspace: a copy
        is one folder, and returning it for one pane applied a sibling's
        half-done work here while the sibling kept working there (#253).

        Every agent stops BEFORE any folder is packed — a sibling still writing
        while the first pane's copy came home lost its last edits — and the
        panes are gated meanwhile so no viewer restarts one there. Each
        (computer, folder) comes back once however many panes shared it: one
        return per pane used to make a second branch, collide on its name and
        leave the workspace half moved. A pane whose return fails stays on its
        computer and runs there again if it was running; one failure used to
        abort the loop and strand every later pane stopped on the server (#252).
        """
        panes = await self._with_copy_sharers(panes)
        back: list[Terminal] = []
        failures: list[tuple[Terminal, str]] = []
        messages: list[str] = []
        live: set[int] = set()
        returns: dict[tuple[str, str], remote.Return] = {}
        for _session, term in panes:
            term.placing = "Bringing the work back to this computer…"
        try:
            for _session, term in panes:
                async with term.attach_lock:
                    if term.pty_id:
                        live.add(id(term))
                        await self._stop_for_move(term, self._pool(term))
            for session, term in panes:
                async with term.attach_lock:
                    where = self._computer_label(term.computer_id)
                    try:
                        message = await self._bring_back_locked(session, term, returns)
                    except Exception as exc:  # noqa: BLE001 - reported per pane, the rest go on
                        logger.warning("Agentic IDE: {} stays on {}: {}", term.name, where, exc)
                        message = f"{term.name} stays on {where}: {exc}"
                        failures.append((term, str(exc)))
                    else:
                        back.append(term)
                    term.placing = ""
                    if id(term) in live:
                        await self._restart_in_place(session, term)
                if message and message not in messages:
                    messages.append(message)
        finally:
            for _session, term in panes:
                term.placing = ""
            await self._persist()
        return back, failures, messages

    async def _with_copy_sharers(
        self, panes: list[tuple[Session, Terminal]]
    ) -> list[tuple[Session, Terminal]]:
        """``panes`` plus every other remote pane that works in one of their copies."""
        copies: set[tuple[str, str]] = set()
        for session, term in panes:
            key, _top = await self._copy_root(term.cwd(session.folder))
            copies.add((term.computer_id, key))
        chosen = {id(term) for _session, term in panes}
        result = list(panes)
        for session in list(self._sessions.values()):
            for other in list(session.terminals):
                if id(other) in chosen or not other.remote_folder or other.placing:
                    continue
                key, _top = await self._copy_root(other.cwd(session.folder))
                if (other.computer_id, key) in copies:
                    result.append((session, other))
                    chosen.add(id(other))
        return result

    async def _stop_for_move(self, term: Terminal, pool: Any) -> None:
        """End the pane's current process and wait until its exit is recorded."""
        if not term.pty_id or pool is None:
            return
        term.stopping = True
        try:
            pool.close(term.pty_id)
        except Exception as exc:  # noqa: BLE001 - the move proceeds; the process is gone or going
            logger.info("Agentic IDE: stopping {} for a move: {}", term.name, exc)
        # A local PTY reports its exit through `_closed`, which clears
        # `pty_id` and must land BEFORE the new process is recorded. A remote
        # pool's close is final at once and reports nothing.
        if not getattr(pool, "computer_id", None):
            for _ in range(50):
                if term.pty_id is None:
                    break
                await asyncio.sleep(0.1)
        term.pty_id = None

    async def _offload_locked(
        self,
        session: Session,
        term: Terminal,
        computer_id: str,
        placements: dict[str, tuple[remote.Placement, str]],
    ) -> str:
        from jarvis.computers.remote_terminal import pool_for
        from jarvis.computers.service import ComputerError

        pool = pool_for(computer_id)
        local = term.cwd(session.folder)
        where = self._computer_label(computer_id)
        key, top = await self._copy_root(local)
        async with self._copy_lock(computer_id, key):
            try:
                await remote.preflight(pool, _remote_commands([term]), where)
                placement, joined = await self._join_or_copy(
                    pool, computer_id, local, key, top, term, placements
                )
                carried = await remote.push_conversation(
                    pool,
                    term.agent,
                    term.resume.id if term.resume else None,
                    placement.remote_folder,
                    account_home(term.agent, term.account),
                )
            except remote.MoveError as exc:
                raise PlacementError(str(exc)) from exc
            except ComputerError as exc:
                raise PlacementError(exc.message) from exc
            except Exception as exc:  # noqa: BLE001 - SSH/SFTP failures become one sentence
                logger.warning("Agentic IDE: moving {} failed: {}", term.name, exc)
                raise PlacementError(f"The move failed: {exc}") from exc
            await self._stop_for_move(term, self._pty)
            if not carried:
                # Nothing to continue from on the server: start clean there
                # rather than asking the CLI for a conversation it does not have.
                term.resume = None
            term.computer_id = computer_id
            term.remote_folder = placement.remote_folder
            term.offload_snapshot = placement.offload_snapshot or ""
        moved = (
            "Moved with its conversation." if carried else "Moved; the agent starts fresh there."
        )
        joined_note = f" It works in the copy already on {where}." if joined else ""
        term.notice = (
            f"{moved}{joined_note} {remote.left_behind_note(placement.left_behind)}".strip()
        )
        return term.notice

    async def _place_new(self, session: Session, terms: list[Terminal], computer_id: str) -> None:
        """Set up panes created for ``computer_id``: check the server, copy each repo once.

        The panes exist already, gated by ``placing`` so no viewer can start
        them anywhere yet. The server is asked first whether tmux and every
        pane's CLI are there — a missing CLI used to show up only as a dead
        pane after a long upload. A copy another pane already works in on
        that computer is JOINED, not sent again.
        """
        from jarvis.computers.remote_terminal import pool_for
        from jarvis.computers.service import ComputerError

        pool = pool_for(computer_id)
        where = self._computer_label(computer_id)
        placements: dict[str, tuple[remote.Placement, str]] = {}
        try:
            await remote.preflight(pool, _remote_commands(terms), where)
            for term in terms:
                local = term.cwd(session.folder)
                key, top = await self._copy_root(local)
                async with self._copy_lock(computer_id, key):
                    placement, joined = await self._join_or_copy(
                        pool, computer_id, local, key, top, term, placements
                    )
                    term.remote_folder = placement.remote_folder
                    term.offload_snapshot = placement.offload_snapshot or ""
                    term.notice = (
                        f"Works in the copy already on {where}."
                        if joined
                        else f"Copied to {where}. {remote.left_behind_note(placement.left_behind)}"
                    ).strip()
                    term.placing = ""
        except remote.MoveError as exc:
            raise PlacementError(str(exc)) from exc
        except ComputerError as exc:
            raise PlacementError(exc.message) from exc
        except Exception as exc:  # noqa: BLE001 - SSH/SFTP failures become one sentence
            logger.warning("Agentic IDE: setting up panes on {} failed: {}", where, exc)
            raise PlacementError(f"Copying the folder to {where} failed: {exc}") from exc
        await self._persist()

    async def _copy_root(self, local: str) -> tuple[str, Path | None]:
        """What one copy on a computer covers: the git repo ``local`` is in, else the folder.

        Returns ``(key, repo top)``. Panes in different subfolders of one repo
        share ONE copy there (the whole repo is sent), so they must share its
        lock and join each other: keyed on their own folders, a subfolder pane
        sent the repo again and reset it under the agent already working in it.
        """
        return await asyncio.to_thread(_copy_key, local)

    async def _join_or_copy(
        self,
        pool: Any,
        computer_id: str,
        local: str,
        key: str,
        top: Path | None,
        exclude: Terminal,
        placements: dict[str, tuple[remote.Placement, str]],
    ) -> tuple[remote.Placement, bool]:
        """The copy ``local`` works in on ``computer_id``, and whether it joined one.

        Caller holds the copy lock for ``key``. The same repo seen from another
        subfolder maps to the matching subfolder of the copy.

        A copy another pane made earlier is joined only while this folder is
        still what that copy started from: after local edits it would run on
        old code without anyone saying so, and a refresh would rewrite files
        under the agent working there (#253).
        """
        known = placements.get(key)
        if known is None:
            known = await self._sibling_placement(computer_id, key, exclude)
            snapshot = known[0].offload_snapshot if known is not None else None
            if top is not None and snapshot and await asyncio.to_thread(
                _changed_since, top, snapshot
            ):
                raise remote.MoveError(
                    f"This folder changed since the copy on {self._computer_label(computer_id)} "
                    "was made, and another pane still works in that copy. Bring that pane "
                    "back first, then move this one."
                )
        if known is None:
            placement = await remote.push_code(pool, Path(local))
            placements[key] = (placement, local)
            return placement, False
        placements[key] = known
        base, base_local = known
        if top is None or os.path.normcase(base_local) == os.path.normcase(local):
            return remote.Placement(base.remote_folder, base.offload_snapshot), True
        folder = await asyncio.to_thread(
            remote.joined_folder, base.remote_folder, Path(base_local), Path(local), top
        )
        return remote.Placement(folder, base.offload_snapshot), True

    async def _sibling_placement(
        self, computer_id: str, key: str, exclude: Terminal
    ) -> tuple[remote.Placement, str] | None:
        """The copy another pane already works in on ``computer_id``, and that pane's folder."""
        for session in list(self._sessions.values()):
            for other in list(session.terminals):
                if (
                    other is exclude
                    or other.computer_id != computer_id
                    or not other.remote_folder
                    or other.placing
                ):
                    continue
                other_local = other.cwd(session.folder)
                other_key, _top = await self._copy_root(other_local)
                if other_key == key:
                    placement = remote.Placement(
                        other.remote_folder, other.offload_snapshot or None
                    )
                    return placement, other_local
        return None

    def _copy_lock(self, computer_id: str, key: str) -> asyncio.Lock:
        return self._copy_locks.setdefault((computer_id, key), asyncio.Lock())

    @staticmethod
    def _new_pane_computer(session: Session, anchor: Terminal | None, wanted: Any) -> str:
        """Where a new pane runs: named, else beside its anchor, else with the workspace."""
        if wanted is not INHERIT_PLACEMENT:
            return str(wanted or "")
        if anchor is not None:
            return anchor.computer_id
        places = {t.computer_id for t in session.terminals}
        return places.pop() if len(places) == 1 else ""

    @staticmethod
    def _computer_label(computer_id: str) -> str:
        """The computer's name for messages."""
        from jarvis.computers.service import get_service

        try:
            return get_service().get(computer_id).name
        except Exception:  # noqa: BLE001 - a removed or unreadable record still needs a word
            return "the other computer"

    def _placing_note(self, computer_id: str) -> str:
        return f"Copying the folder to {self._computer_label(computer_id)}…"

    async def _bring_back_locked(
        self,
        session: Session,
        term: Terminal,
        returns: dict[tuple[str, str], remote.Return] | None = None,
    ) -> str:
        """Bring one pane home; ``returns`` shares one return per copy across calls.

        The pane is gated meanwhile and the copy's lock is held, so no new pane
        joins a copy that is being packed up.
        """
        from jarvis.computers.remote_terminal import pool_for, tmux_session_name

        pool = pool_for(term.computer_id)
        term.placing = term.placing or "Bringing the work back to this computer…"
        try:
            if term.pty_id:
                await self._stop_for_move(term, pool)
            else:
                await pool.run(
                    f"tmux kill-session -t {shlex.quote(tmux_session_name(term.history_id))}"
                    " 2>/dev/null; true",
                    timeout_s=20,
                )
            local_folder = Path(term.cwd(session.folder))
            name = self._computer_label(term.computer_id)
            copy_key, _top = await self._copy_root(str(local_folder))
            key = (term.computer_id, copy_key)
            async with self._copy_lock(term.computer_id, copy_key):
                try:
                    outcome = returns.get(key) if returns is not None else None
                    if outcome is None:
                        outcome = await remote.pull_code(
                            pool,
                            local_folder,
                            term.remote_folder,
                            term.offload_snapshot or None,
                            name,
                        )
                        if returns is not None:
                            returns[key] = outcome
                    carried = await remote.pull_conversation(
                        pool,
                        term.agent,
                        term.resume.id if term.resume else None,
                        local_folder,
                        account_home(term.agent, term.account),
                    )
                except remote.MoveError as exc:
                    raise PlacementError(str(exc)) from exc
                except Exception as exc:  # noqa: BLE001 - SSH/SFTP failures become one sentence
                    logger.warning("Agentic IDE: bringing {} back failed: {}", term.name, exc)
                    raise PlacementError(f"Bringing the pane back failed: {exc}") from exc
                if not carried:
                    term.resume = None
                term.computer_id = ""
                term.remote_folder = ""
                term.offload_snapshot = ""
        finally:
            term.placing = ""
        return outcome.message

    async def _restart_in_place(self, session: Session, term: Terminal) -> None:
        """Start the pane's agent in its new place for whoever is watching it."""

        async def _discard(_data: Any) -> None:
            return None

        term.status = "pending"
        term.stopping = False
        try:
            await self._attach_locked(
                term.key,
                term.pty_cols or term.transcript.cols,
                term.pty_rows or term.transcript.rows,
                term.viewer_output or _discard,
                term.viewer_exit or _discard,
                workspace_id=session.id,
            )
        except SessionError as exc:
            logger.warning("Agentic IDE: {} did not start after its move: {}", term.name, exc)

    async def fork_terminal(
        self,
        wanted: str,
        *,
        workspace_id: str | None = None,
        worktree: bool = False,
        name: str | None = None,
        direction: str = "right",
    ) -> Terminal:
        """Open a new pane that starts from a copy of pane ``wanted``'s chat.

        The new pane runs the same CLI on the same account (its conversation
        lives in that account's history) with the same picks, beside the
        original. Its first process copies the conversation through the CLI's
        own fork (:func:`.agent_sessions.fork_argv`); a CLI without one, or a
        pane with nothing said yet, gives a fresh chat instead.

        ``worktree=True`` first creates a git worktree on a new branch called
        ``name`` (:func:`.fork.create_worktree`) and runs the new pane there,
        so the two agents can change files without touching each other's work.
        """
        found = self._locate(wanted, workspace_id)
        if found is None:
            raise self._unknown_terminal(wanted)
        session, source = found
        if not accepts_prompts(source.agent):
            raise SessionError(f"{source.name} is a plain terminal — it has no chat to fork.")
        # Checked before a worktree is created, so a full workspace does not
        # leave an orphaned branch behind.
        if len(session.terminals) >= MAX_TERMINALS:
            raise WorkspaceFull(
                f"This workspace already has the maximum of {MAX_TERMINALS} terminals."
            )
        folder = ""
        branch = ""
        if worktree:
            base = source.cwd(session.folder)
            wanted_name = (name or "").strip() or await asyncio.to_thread(
                fork.suggest_name, base, source.name, recap_engine.known_headline(source)
            )
            try:
                created = await asyncio.to_thread(fork.create_worktree, base, wanted_name)
            except fork.ForkError as exc:
                raise SessionError(str(exc)) from exc
            folder, branch = str(created.folder), created.branch
        term = await self.add_terminal(
            workspace_id=session.id,
            agent=source.agent,
            anchor=source.name,
            direction=direction,
            account=source.account,
            model=source.model,
            effort=source.effort,
            permission_mode=source.permission_mode,
            folder=folder,
            # Born a fork: a pane for a computer opens its ``placing`` gate
            # and persists before add_terminal returns, so a viewer can spawn
            # it inside that await (#254).
            fork_from=source.resume,
            branch=branch,
        )
        logger.info(
            "Agentic IDE: forked {} into {}{}",
            source.name,
            term.name,
            f" on worktree branch {branch}" if branch else "",
        )
        return term

    async def add_terminals(
        self,
        count: int,
        *,
        agent: str | None = None,
        account: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[list[Terminal], bool]:
        """Open a batch in one pinned workspace, rejecting oversized requests.

        Returns the created panes and a flag for partial operational failure.
        Capacity is checked before any pane is created. A concurrent addition
        or a disappearing agent binary can still stop a batch partway through,
        which the flag reports honestly.

        Deliberately a loop over ``add_terminal`` rather than a second placement
        implementation: the anchor, the call-sign pool, and the grid position are
        already decided there, and a batch that placed panes its own way would
        drift from what the split buttons do. No anchor is named, so without an
        explicit ``account`` every pane opens on the workspace's active one.

        A failure before creating any pane is raised to the caller.
        """
        selected = self.get(workspace_id)
        if selected is None:
            raise SessionError("No Agentic-IDE session is running.")
        wanted = max(1, int(count))
        if len(selected.terminals) + wanted > MAX_TERMINALS:
            raise WorkspaceFull(f"A workspace can contain at most {MAX_TERMINALS} terminals.")
        created: list[Terminal] = []
        for _ in range(wanted):
            try:
                created.append(
                    await self.add_terminal(
                        agent=agent,
                        account=account,
                        workspace_id=selected.id,
                    )
                )
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

            if session.layout is None:
                # Legacy/injected workspaces can still carry only grid hints.
                # Build their tree before moving; moving None followed by
                # renumbering would silently leave every pane in its old order.
                session.layout = layout_tree.from_grid(
                    (term.key, term.column, term.slot) for term in session.terminals
                )

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

    async def set_terminal_archived(
        self, wanted: str, archived: bool, *, workspace_id: str | None = None
    ) -> tuple[Session, Terminal]:
        """Hide or restore a pane in the chat-mode session list.

        The pane keeps running. Archive is a list filter: a workspace with two
        dozen agents is unusable as a conversation list, and closing them would
        throw the conversations away. The flag survives a restart because the
        resume snapshot carries it.

        ``workspace_id`` pins the search to one tab. Every workspace numbers
        its panes from T1, so without it the front workspace would archive
        the wrong T1 whenever two tabs are open.
        """
        async with self._lock:
            found = self.find_terminal(wanted, workspace_id)
            if found is None:
                raise self._unknown_terminal(wanted)
            session, term = found
            term.archived = bool(archived)
            await self._persist()
            logger.info(
                "Agentic IDE: {} terminal {} in workspace {}",
                "archived" if term.archived else "restored",
                term.name,
                session.id,
            )
            return session, term

    async def close_terminal(self, wanted: str, *, workspace_id: str | None = None) -> Terminal:
        """Stop one terminal's agent and remove its pane from the workspace."""
        closed, failed = await self.close_terminals([wanted], workspace_id=workspace_id)
        if failed:
            raise SessionError(failed[0]["detail"])
        return closed[0]

    async def close_terminals(
        self, wanted: list[str], *, workspace_id: str | None = None
    ) -> tuple[list[Terminal], list[dict[str, str]]]:
        """Stop several panes under one registry lock and persist once.

        Unknown and duplicate names are reported individually while every valid
        terminal is closed. Resolving the complete selection before teardown
        keeps concurrent callers from changing which pane a name refers to
        halfway through the batch.

        ``workspace_id`` pins the batch to one tab. The default is the workspace
        on screen, which is what the grid's own close is asking about.
        """
        async with self._lock:
            session = self.get(workspace_id) if workspace_id is not None else self.session
            if session is None:
                raise SessionError(
                    "That workspace is not open."
                    if workspace_id is not None
                    else "No Agentic-IDE session is running."
                )
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
                pool = self._pool(term) if term.computer_id else self._pty
                if term.pty_id and pool is not None:
                    try:
                        pool.close(term.pty_id)
                    except Exception:  # noqa: BLE001, S110 - best-effort teardown
                        pass
                elif term.computer_id and term.remote_folder and hasattr(pool, "end_session"):
                    # Nobody is watching it, but its agent may still be working
                    # in tmux on the computer: closing the pane ends it there too.
                    pool.end_session(term.history_id)
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
                recap_engine.forget(recap_engine.pane_id(term))
                opening.forget(term.key)
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
        self,
        wanted: str,
        text: str,
        *,
        workspace_id: str | None = None,
        typed: str = "",
        attachments: Sequence[Any] = (),
        require_idle: bool = False,
        expected_input: str = "",
        allow_question: bool = False,
        readback: bool = False,
    ) -> Terminal:
        """Serialize deliveries and pin the pane before the first await.

        ``readback`` marks the job as one the user gave through Jarvis, so its
        end is reported by voice (see :mod:`.voice_readback`). Callers that
        supervise the pane themselves (a society agent) leave it off.
        """
        found = self.find_terminal(wanted, workspace_id)
        if found is None:
            raise self._unknown_terminal(wanted)
        owner, term = found
        identity = "pane:" + term.history_id
        async with term.prompt_lock:
            if self.find_terminal(identity, owner.id) != (owner, term):
                raise SessionError("The selected terminal was closed; nothing was sent.")
            if expected_input and (
                term.reading().activity
                not in (("asking", "waiting") if allow_question else ("waiting",))
                or self.input_token(term) != expected_input
            ):
                raise SessionError("The input request changed; nothing was sent.")
            if require_idle:
                activity = term.reading().activity
                has_submission = (
                    term.last_submit_at is not None
                    and term.submit_generation == term.process_generation
                )
                if activity in ("working", "asking", "failed", "exited") or (
                    has_submission and activity != "waiting"
                ):
                    raise SessionError("The selected coding agent is busy; nothing was sent.")
            return await self._send_prompt_locked(
                identity,
                text,
                workspace_id=owner.id,
                typed=typed,
                attachments=attachments,
                expected_input=expected_input,
                allow_question=allow_question,
                readback=readback,
            )

    @staticmethod
    def input_token(term: Terminal) -> str:
        """Bind a textual reply to the current process and visible input request."""
        data = f"{term.history_id}|{term.process_generation}|{term.pty_id}|{term.last_submit_at}|"
        return hashlib.sha256((data + "\n".join(term.transcript.tail(20))).encode()).hexdigest()

    async def _send_prompt_locked(
        self,
        wanted: str,
        text: str,
        *,
        workspace_id: str | None = None,
        typed: str = "",
        attachments: Sequence[Any] = (),
        expected_input: str = "",
        allow_question: bool = False,
        readback: bool = False,
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

        ``typed`` and ``attachments`` are the receipt: what the person actually
        said and the files that went with it, when ``text`` is a brief written
        around them. They are recorded beside the prompt so the chat stage can
        show the person their own sentence and their pictures, where the
        CLI's transcript holds only the brief (``prompt_receipts``).

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

        process_id = term.pty_id
        generation = term.process_generation
        ready = await fleet_actions.wait_for_prompt_ready(
            owner,
            [wanted],
            timeout_s=fleet_actions.READY_TIMEOUT_S,
        )
        if wanted not in ready:
            if term.status != "live" or not term.pty_id:
                raise SessionError(
                    f"{term.name} stopped while it was starting (status: {term.status}) — "
                    "nothing was sent."
                )
            raise SessionError(
                f"{term.name} is still starting — its input line never appeared, "
                "so nothing was sent."
            )

        if (
            self.find_terminal("pane:" + term.history_id, owner.id) != (owner, term)
            or term.pty_id != process_id
            or term.process_generation != generation
            or term.status != "live"
        ):
            raise SessionError("The selected terminal changed while waiting; nothing was sent.")
        if expected_input and (
            self.input_token(term) != expected_input
            or term.reading().activity
            not in (("asking", "waiting") if allow_question else ("waiting",))
        ):
            raise SessionError("The input request changed while waiting; nothing was sent.")
        manager = self._pool(term)
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
        # Whoever sent THIS job decides whether its end is reported by voice; a
        # supervising agent's follow-up replaces a Jarvis job and its readback.
        term.voice_readback = readback
        term.voice_readback_request = (typed or payload).strip() if readback else ""
        from .prompt_receipts import receipts_for

        history_entry = prompt_history.PromptHistoryEntry(
            id=uuid4().hex,
            sequence=term.prompts_sent,
            text=payload,
            at=term.last_prompt_at,
            submitted=submitted,
            # Only when it differs: a verbatim prompt IS what the person typed.
            typed=typed.strip() if typed.strip() != payload.strip() else "",
            attachments=receipts_for(attachments),
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
        # Somebody is driving this pane again: an earlier interrupted turn is
        # superseded by this instruction.
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

    async def apply_picks(
        self,
        wanted: str,
        *,
        workspace_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Change what a RUNNING pane runs on, through the CLI's own commands.

        The chat stage's composer shows a pane's model, effort and permission
        pills, and a pick there used to do nothing but toast (maintainer
        report, 2026-08-27: "I switched effort to Max, it snapped back to
        Extra high, and a little error appeared"). A running process cannot be
        re-flagged, but every CLI that has a typed command for a pick takes it
        that way — Claude Code's ``/effort max`` — so this types exactly that
        command and presses Enter, the same keystroke path a prompt takes
        (``_write_and_confirm``), minus the receipt: a command is not a
        message and must not appear in the conversation as one.

        Returns, per pick asked for::

            {"applied": {"effort": "max"}, "declined": {"permission_mode": "..."}}

        A pick is applied once its command has left the input line (or was
        never seen to sit there — the pane took it or dropped it, and the
        field follows the optimistic reading the prompt path uses too). A
        pick the CLI has no command for, a value off its ladder, or a pane
        that would not submit the line is declined with the sentence that
        says why, so the composer can show it instead of guessing.

        Raises ``SessionError`` when the pane is unknown, not an agent, or not
        running — the same reasons a prompt cannot be typed.
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
            raise SessionError(
                f"{term.name} is a {agent_display(term.agent).lower()}, not a coding agent — "
                "there is no model, effort or permission stance to change."
            )
        if term.status != "live" or not term.pty_id:
            raise SessionError(
                f"{term.name} is not running right now (status: {term.status}) — "
                "nothing was changed."
            )
        asked = {
            "model": model,
            "effort": effort,
            "permission_mode": permission_mode,
        }
        display = agent_display(term.agent)
        applied: dict[str, str] = {}
        declined: dict[str, str] = {}
        offers = launch_picks.runtime_picks_for(term.agent).offers()
        for pick, value in asked.items():
            if value is None:
                continue
            if not offers.get(pick):
                declined[pick] = (
                    f"{display} takes its {_PICK_WORDS[pick]} only when it starts — "
                    "a new chat is where it changes."
                )
                continue
            line = launch_picks.runtime_command(term.agent, pick, value)
            if not line:
                declined[pick] = f"{display} does not offer {value!r} as a {_PICK_WORDS[pick]}."
                continue
            from . import fleet_actions

            ready = await fleet_actions.wait_for_prompt_ready(
                owner, [term.name], timeout_s=fleet_actions.READY_TIMEOUT_S
            )
            if term.name not in ready:
                declined[pick] = f"{term.name} is still starting — its input line never appeared."
                continue
            submitted = await self._write_and_confirm(term, line, self._pool(term), False)
            if submitted is False:
                declined[pick] = f"{term.name} kept `{line}` in its input box instead of taking it."
                continue
            checked = line.rsplit(" ", 1)[-1]
            setattr(term, pick, checked)
            term.picked_at[pick] = time.time()
            applied[pick] = checked
            logger.info(
                "Agentic IDE picks -> {} ({}): {} = {} ({})",
                term.name,
                term.agent,
                pick,
                checked,
                "submitted" if submitted else "unconfirmed",
            )
        return {"terminal": term.name, "applied": applied, "declined": declined}

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
    def find_terminal(
        self, wanted: str, workspace_id: str | None = None
    ) -> tuple[Session, Terminal] | None:
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

        ``workspace_id`` turns that off and asks ONE workspace. A caller holding
        a list of panes already knows which tab each row came from, and letting
        the front workspace answer for it would hand back the wrong pane's
        conversation whenever two tabs both have a T1 — which every pair of
        workspaces does, since each numbers its panes from one.
        """
        if workspace_id is not None:
            owner = self._sessions.get(workspace_id)
            if owner is None:
                return None
            term = owner.find(wanted)
            return None if term is None else (owner, term)
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
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> Registry:
    """The process-wide Agentic-IDE registry (created on first use)."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = Registry()
        return _REGISTRY


def schedule_boot_restore() -> asyncio.Task[None] | None:
    """Run :meth:`Registry.boot_restore` in the background, once.

    Called by the two real app entry points (the desktop shell and the web
    launcher) right after the server is up — never by ``WebServer.start``,
    which tests boot against the real user data directory and must not attach
    to the user's PTY host or reopen their workspaces.
    """
    # Before anything can reach the registry — see ``Registry.enable_host``.
    get_registry().enable_host()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Agentic IDE: startup restore not scheduled — no running loop")
        return None

    async def _run() -> None:
        try:
            await get_registry().boot_restore()
        except Exception as exc:  # noqa: BLE001 - startup must not fail on this
            logger.opt(exception=exc).warning("Agentic IDE: startup restore failed")

    return loop.create_task(_run(), name="agentic-ide-boot-restore")


def reset_registry() -> None:
    """Drop the registry — tests only."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None


__all__ = [
    "AGENT_BINARIES",
    "AGENT_DISPLAY",
    "MAX_PROMPT_CHARS",
    "MAX_GRID_COLUMNS",
    "MAX_GRID_ROWS",
    "MAX_TERMINALS",
    "MAX_WORKSPACES",
    "INHERIT_PLACEMENT",
    "PLAIN_TERMINAL",
    "PlacementError",
    "Registry",
    "Session",
    "SessionError",
    "SessionNotReady",
    "Terminal",
    "WorkspaceFull",
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
