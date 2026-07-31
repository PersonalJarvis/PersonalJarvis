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

from . import prompt_history, recap_engine, resume_store
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
from .terminal_input import THEME_COLOURS, TerminalQueryResponder
from .transcript import ReplayBuffer, Transcript

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.agent_accounts import AgentAccount
    from jarvis.terminal.pty_manager import PtyManager

# The coding CLIs a pane can run, and the binary each one is. This table is what
# "an agent" means to the rest of this module: an account can be pinned to it, a
# conversation can be resumed in it, and a prompt can be typed into it.
#
# argv is built here rather than reused from jarvis.workspace.agents because the
# IDE runs the agent as the PTY's OWN process, not inside a persistent shell.
AGENT_BINARIES: dict[str, str] = {
    a.name: a.executable for a in workspace_agents.coding_agents()
}


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
# How long a pane's call-sign may be. Half the workspace tab's 80, and for a
# different reason: a workspace name is read, a call-sign is SAID — it is how a
# user addresses one agent among several out loud, and it also has to fit in a
# pane header that may be a quarter of a screen wide. Long enough for "Frontend
# rewrite", short enough that it stays a name rather than a description.
MAX_TERMINAL_NAME = 40
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
# How many workspaces may be open at once. Not a technical ceiling — a real one
# costs a folder's worth of running coding agents, so the cap exists to keep an
# accidental click from spawning them, and the number is what still fits in a
# row of tabs the user can read.
MAX_WORKSPACES = 6
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

# How long a deferred "carry on" waits after its pane's process appears.
#
# The process existing is not the same as its prompt box being on screen: a
# coding CLI spends a second or two loading, and text typed into that window is
# swallowed whole rather than queued (measured on a real Codex — see
# `_await_arrival`). Long enough to clear that boot burst, short enough that
# nobody watches a pane sit there.
CONTINUE_AFTER_START_S = 2.5

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


def _behind_win_shim(
    spec: workspace_agents.WorkspaceAgent, shim: str
) -> tuple[str, ...] | None:
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
    # Where the pane sits in the grid, on TWO axes: the workspace is a
    # left-to-right list of columns, and each column is a top-to-bottom stack.
    # "Split right" opens a new column beside the anchor; "split down" adds a
    # pane to the anchor's OWN column and leaves every other column alone.
    #
    # The second axis is load-bearing. With only a row number, "split down"
    # could only mean "open a new row", and a row is window-wide by definition —
    # so splitting one pane squashed every other pane to half height. A full
    # split TREE (arbitrary nesting, draggable separators) is still deliberately
    # NOT modelled: two axes express both buttons the UI offers and stay
    # readable.
    column: int = 0
    slot: int = 0
    # Which subscription of `agent` this pane runs on (see jarvis.agent_accounts).
    # Resolved to a concrete id when the pane is CREATED, never read live at
    # spawn time: flipping the global default must not silently re-point a pane
    # that is already on screen — least of all one mid-conversation, which would
    # hand a resumed transcript to an account that has never seen it.
    account: str | None = None
    status: Status = "pending"
    pty_id: str | None = None
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
    prompts_sent: int = 0
    last_prompt: str = ""
    # The current process's records are kept as a fallback if the local history
    # file cannot be written. The full durable history is loaded only when its
    # UI is opened, never in the workspace-state hot path.
    prompt_records: list[prompt_history.PromptHistoryEntry] = field(
        default_factory=list, repr=False, compare=False
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
    # `attach` the moment that pane's agent exists.
    continue_when_ready: bool = False
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
    # EVERY viewer currently attached to this pane, as ``(output, exit)`` pairs,
    # newest last. ``viewer_output`` above is the newest of them — the OWNER,
    # which is a different question from who gets to see the screen.
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
    watchers: list[tuple[Any, Any]] = field(default_factory=list, repr=False, compare=False)
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
        return {
            "key": self.key,
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
    # Focus mode: while on, Jarvis answers inside this workspace's context. The
    # flag lives here (not in jarvis.toml) on purpose — it is a mode of the
    # current session, and a restart should land the user back in normal mode
    # rather than silently keeping a narrowed assistant.
    focus_mode: bool = False
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "folder": self.folder,
            "name": self.name,
            "project": self.profile.to_dict(),
            "created_at": self.created_at,
            "focus_mode": self.focus_mode,
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

    ``skipped`` carries a reason per workspace that could not come back (folder
    deleted, workspace limit reached). Reported rather than swallowed: a resume
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


def _watch(term: Terminal, on_output: Any, on_exit: Any) -> None:
    """Attach a viewer to ``term`` and make it the owner.

    Newest last, and never twice: a socket that re-attaches (a resize, a resume
    retry) replaces its own entry rather than being fed the same bytes twice.
    """
    term.watchers = [w for w in term.watchers if not _same_viewer(w[0], on_output)]
    term.watchers.append((on_output, on_exit))
    if len(term.watchers) > MAX_WATCHERS:
        del term.watchers[0 : len(term.watchers) - MAX_WATCHERS]
    term.viewer_output = on_output
    term.viewer_exit = on_exit


def _viewers(term: Terminal) -> list[Any]:
    """Every output callback this pane should write to, newest last.

    Falls back to the owner slot alone when nothing registered — a test (or any
    caller) that sets ``viewer_output`` by hand still gets its output.
    """
    if term.watchers:
        return [out for out, _ in term.watchers]
    return [term.viewer_output] if term.viewer_output is not None else []


def _exit_viewers(term: Terminal) -> list[Any]:
    """The same, for the one-shot "the agent stopped" callback."""
    if term.watchers:
        return [done for _, done in term.watchers if done is not None]
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
        # Admits a few agent cold starts at a time (see COLD_START_LIMIT).
        # Created on first use rather than here: a semaphore belongs to the loop
        # it is first awaited on, and the registry is also built in tests that
        # run each case on a loop of its own.
        self._cold_start: asyncio.Semaphore | None = None

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

            if len(self._sessions) >= MAX_WORKSPACES:
                raise SessionError(
                    f"{MAX_WORKSPACES} workspaces are already open — close one "
                    "before opening another."
                )

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
                terminals.append(
                    Terminal(
                        key=normalize(name) or f"t{index}",
                        name=name,
                        agent=agent,
                        display_name=agent_display(agent),
                        index=index,
                        # A wizard-opened workspace is one row of columns; the
                        # user's own "split down" is what creates a stack.
                        column=index,
                        account=resolve_account(agent, _requested_account(entry)),
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
        folder is reported, and the workspace limit stops the rest with a
        reason. What could not be restored comes back in ``skipped`` so the
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

        if len(self._sessions) >= MAX_WORKSPACES:
            raise SessionError(
                f"{MAX_WORKSPACES} workspaces are already open — close one before reopening this."
            )

        terminals = [
            Terminal(
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
                # The remembered account, re-validated: a pane must come back
                # on the subscription whose history holds its conversation,
                # and an account deleted in the meantime falls back to the
                # active one rather than failing the reopen.
                account=resolve_account(entry.agent, entry.account),
            )
            for index, entry in enumerate(space.terminals)
        ]
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
        # Pack the grid: a snapshot can carry gaps if it was written between a
        # close and its renumbering, and a gap renders as a blank stripe.
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
        """Turn the focused coding mode on/off. Returns the resulting state."""
        session = self.session
        if session is None:
            if enabled:
                raise SessionError("No Agentic-IDE session is running — open one first.")
            return False
        session.focus_mode = bool(enabled)
        logger.info("Agentic IDE focus mode {}", "on" if enabled else "off")
        return session.focus_mode

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
                asyncio.get_running_loop().call_later(
                    COLD_START_SETTLE_S, gate.release
                )
            else:
                gate.release()

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
        if appearance in THEME_COLOURS:
            term.queries.appearance = appearance

        manager = self._manager()
        if term.pty_id and manager.has(term.pty_id):
            # The agent never stopped. Take over the viewer slot: the previous
            # viewer is either gone (a socket that closed) or is being replaced
            # by this one, so the newest viewer always wins. The one it replaces
            # may still be TIDYING UP — see ``detach``, which is what stops that
            # tidy-up from clearing the slot this line just filled.
            #
            # Winning the slot is about OWNERSHIP (the size, the handover), not
            # about who may look: a viewer that was here first keeps receiving
            # this pane's output until its own socket goes away.
            _watch(term, on_output, on_exit)
            term.reattached = True
            term.stopping = False
            geometry_changed = (term.transcript.cols, term.transcript.rows) != (
                cols,
                rows,
            )
            if geometry_changed:
                geometry_changed = manager.resize(term.pty_id, cols, rows)
                if geometry_changed:
                    term.transcript.resize(cols, rows)
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
        home = account_home(term.agent, term.account)
        continuing = resume_argv(term.agent, term.resume)
        if continuing is not None and not has_conversation(term.agent, term.resume, home):
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
        term.continuation_pending = term.resumed and term.resume_continuation_needed
        if not term.resumed:
            term.resume_continuation_needed = False

        term.transcript.resize(cols, rows)
        # A fresh process draws a fresh screen: anything the previous one left
        # in the replay buffer belongs to a terminal that no longer exists, and
        # replaying it to the next viewer would show output from a dead agent.
        term.replay.clear()
        _watch(term, on_output, on_exit)
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

        # One of a few starts at a time (see COLD_START_LIMIT). The wait covers
        # the account preparation too: that is filesystem work on the same
        # directory every pane of an account shares, so letting eight of them
        # queue on its lock while eight CLIs boot is the same pile-up one step
        # earlier.
        async with self._cold_start_slot():
            # Off the event loop: getting the pane's account ready is filesystem
            # work (a few stat calls once it is in place — see `_prepare_spawn`).
            env = await asyncio.to_thread(self._prepare_spawn, term, session.folder)
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

        term.pty_id = pty_session.terminal_id
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
        if term.resume is None and can_resume(term.agent):
            # A CLI that cannot be told its session id (Codex): find out which
            # one it just created, shortly from now.
            self._schedule_lookup(session, term, session.folder, term.started_at)
        if term.continue_when_ready:
            # Somebody pressed "Continue" while this pane was still waiting for
            # a cold-start slot. The wish outlives the wait — see
            # `continue_when_ready` — and is spent HERE, once the agent exists
            # and can be typed into. As a task, because the pane's socket is
            # waiting on this call: the nudge itself watches the pane's screen
            # for a few seconds, and holding the attach open for that would
            # leave the terminal blank while it ran.
            term.continue_when_ready = False
            self._schedule_continue(session, term)
        await self._persist()
        return term

    def _schedule_continue(self, session: Session, term: Terminal) -> None:
        """Send the deferred "carry on" to a pane that has just come up.

        Kept on the session's own task set, like the conversation-id lookups, so
        closing that workspace cancels it rather than leaving a nudge in flight
        for a pane that no longer exists.
        """
        from .interrupted import CONTINUE_PROMPT

        async def _nudge() -> None:
            try:
                # The agent's CLI needs a moment after its process exists before
                # its prompt box is on screen; typing into the boot sequence is
                # how a paste gets swallowed whole (see `_await_arrival`, which
                # then waits for text that never appears).
                await asyncio.sleep(CONTINUE_AFTER_START_S)
                await self.send_prompt(term.name, CONTINUE_PROMPT)
            except SessionError as exc:
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
        # Somebody is typing in here. Recorded on EVERY keystroke (unlike the
        # submit handling below), because the activity detector needs to tell
        # the agent's own output apart from the echo of a person at the
        # keyboard — see `activity._printing_now`.
        term.last_input_at = time.time()
        # Gated on a SUBMIT rather than on any keystroke: scrolling, arrow keys
        # and a half-typed line are not an instruction.
        if "\r" in data or "\n" in data:
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
            term.last_submit_at = term.last_input_at
            # And the pane's conversation may have just begun, which for most
            # coding CLIs is the first moment its id exists on disk at all. A
            # pane driven only by hand never goes through `send_prompt`, so
            # without this hook it would keep the gap that cost every non-Claude
            # pane its resume handle.
            self._lookup_after_conversation(owner, term)
        return self._manager().write(term.pty_id, data)

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
            # Two is the floor a TUI can still lay out; below it some redraw
            # into a single row and never recover the frame.
            manager.resize(pty_id, cols, max(rows - 1, 2))
            await asyncio.sleep(REPAINT_NUDGE_S)
            manager.resize(pty_id, cols, rows)
        except Exception as exc:  # noqa: BLE001 - a stale screen beats a failed reconnect
            logger.debug("Agentic IDE: could not nudge {} into a repaint: {}", term.name, exc)

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
        if found is None:
            return False
        term = found[1]
        if not term.pty_id:
            return False
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
            term.watchers = [w for w in term.watchers if not _same_viewer(w[0], viewer)]
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
            term.viewer_output, term.viewer_exit = term.watchers[-1]
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

        ``account`` names which subscription of that agent to run on. Without
        one, a pane split off a NAMED anchor inherits that anchor's account —
        splitting a pane that runs the second plan should stay on the second
        plan, or a "split" would quietly move the work onto a different bill —
        while every other new pane opens on the workspace's active account
        (``set_active_account``).
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

            chosen = agent or (base.agent if base else "claude")
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

            if base is None:
                column, slot = 0, 0
            elif direction == "right":
                # A new column of its own, immediately right of the anchor's;
                # everything further right shifts one column over.
                column, slot = base.column + 1, 0
                for other in session.terminals:
                    if other.column >= column:
                        other.column += 1
            else:
                # Inside the anchor's column, directly beneath it. No other
                # column is touched — that is what makes this a real split
                # rather than a new window-wide row.
                column, slot = base.column, base.slot + 1
                for other in session.terminals:
                    if other.column == column and other.slot >= slot:
                        other.slot += 1

            # Which subscription the new pane opens on, when the caller named
            # none. Two different questions, so two different answers:
            #
            # * **A named anchor is a split** — "another one of these". It
            #   inherits, and only when the CLI matches (a Claude account id
            #   means nothing to Codex), so splitting a pane that runs the
            #   second plan cannot quietly move the work onto a different bill.
            # * **Everything else is a NEW terminal** — the batch behind "open
            #   five more", the empty grid's button, the CLI. Those follow the
            #   workspace's active account, which is exactly the promise the
            #   account switcher makes: switching applies to new terminals.
            #
            # Anchor-less used to inherit from whatever pane happened to be last,
            # which made the switch reach nothing a user could predict: flipping
            # to the second seat and opening a terminal still billed the first.
            if anchor and base is not None and base.agent == chosen:
                inherited = base.account
            else:
                inherited = self.active_account_id(chosen)
            term = Terminal(
                key=normalize(final) or f"t{len(session.terminals)}",
                name=final,
                agent=chosen,
                display_name=agent_display(chosen),
                index=len(session.terminals),
                column=column,
                slot=slot,
                account=resolve_account(chosen, account or inherited),
            )
            session.terminals.append(term)
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

        Returns the panes that were created and whether the workspace cap
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

    async def move_terminal(
        self, wanted: str, *, target: str, position: str = "swap"
    ) -> Terminal:
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

            if position == "swap":
                moved.column, anchor.column = anchor.column, moved.column
                moved.slot, anchor.slot = anchor.slot, moved.slot
            elif position in ("left", "right"):
                # A column of its own beside the target. The moved pane is left
                # out of the shift — it is being placed, not pushed — and the
                # column it vacates is closed by `_renumber` below, so a pane
                # that was already on that side lands exactly where it started.
                column = anchor.column if position == "left" else anchor.column + 1
                for other in session.terminals:
                    if other is not moved and other.column >= column:
                        other.column += 1
                moved.column, moved.slot = column, 0
            else:
                # Into the target's own stack. Every other column stays put —
                # the same property that makes "split down" a real split.
                column = anchor.column
                slot = anchor.slot if position == "above" else anchor.slot + 1
                for other in session.terminals:
                    if other is not moved and other.column == column and other.slot >= slot:
                        other.slot += 1
                moved.column, moved.slot = column, slot

            self._renumber(session)
            await self._persist()
            logger.info(
                "Agentic IDE: moved terminal {} {} {}",
                moved.name,
                position,
                anchor.name,
            )
            return moved

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
            raise SessionError(
                f"Terminal names can be at most {MAX_TERMINAL_NAME} characters."
            )
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
        """Re-pack the grid after an insert or a removal.

        Three things at once, all of them about not leaking holes into the UI:
        the terminal list is sorted back into reading order (left to right, top
        to bottom), column numbers are packed so emptying a column does not
        render as a blank stripe, and each column's slots are packed so closing
        the middle of a stack does not leave a gap in it.
        """
        session.terminals.sort(key=lambda t: (t.column, t.slot))

        columns = sorted({t.column for t in session.terminals})
        remap = {old: new for new, old in enumerate(columns)}
        next_slot: dict[int, int] = {}
        for position, term in enumerate(session.terminals):
            term.index = position
            term.column = remap.get(term.column, 0)
            term.slot = next_slot.get(term.column, 0)
            next_slot[term.column] = term.slot + 1

    # --------------------------------------------------------------- prompt
    async def send_prompt(self, wanted: str, text: str) -> Terminal:
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

        Raises ``SessionError`` when the terminal is unknown, not running, or the
        prompt sanitizes down to nothing. A prompt that was typed but refused to
        submit is NOT an error — the text is in the box and the caller is told.
        """
        found = self.find_terminal(wanted)
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
        # way. Set even for a hard False: the text is in the pane's input box,
        # and whatever it does next was still asked of it.
        term.last_submit_at = term.last_prompt_at
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
        if not manager.write(term.pty_id or "", typed):
            raise SessionError(f"Could not write to {term.name}.")

        arrived = await self._await_arrival(term, payload)
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
        return free_positions(
            [name for name in used if position_of(name) is not None], 1
        )[0]
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
