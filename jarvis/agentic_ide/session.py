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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger


from . import recap, resume_store
from .agent_sessions import (
    ResumeHandle,
    can_resume,
    discover,
    has_conversation,
    launch_extra,
    resume_argv,
)
from .folders import ProjectProfile, probe_project
from .names import default_names, normalize, resolve
from .terminal_input import THEME_COLOURS, TerminalQueryResponder
from .transcript import ReplayBuffer, Transcript

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.terminal.pty_manager import PtyManager

# Agents the IDE can run. Kept parallel to jarvis.workspace.agents (same two
# CLIs, same display names) but with argv built for a one-shot agent process
# rather than a persistent shell.
AGENT_BINARIES: dict[str, str] = {"claude": "claude", "codex": "codex"}
AGENT_DISPLAY: dict[str, str] = {"claude": "Claude Code", "codex": "Codex"}

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

# When to look for the session id of a CLI that cannot be told one (Codex). It
# writes its rollout file a beat after launching, so asking immediately finds
# nothing; two attempts cover a slow machine without turning into polling.
DISCOVERY_DELAYS_S = (4.0, 12.0)

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
    if agent not in AGENT_BINARIES:
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
    if not term.account or term.agent not in AGENT_BINARIES:
        return None
    from jarvis import agent_accounts

    if not agent_accounts.env_overrides(term.agent, term.account):  # type: ignore[arg-type]
        return None
    return agent_accounts.config_dir_for(term.agent, term.account)  # type: ignore[arg-type]


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

    Filesystem work, so callers run it off the event loop.
    """
    if _redirected_home(term) is None:
        return None
    from jarvis import agent_accounts, agent_config_parity

    report = agent_config_parity.ensure_parity(term.agent, term.account)  # type: ignore[arg-type]
    mode_file = agent_accounts.mode_file_name(term.agent)  # type: ignore[arg-type]
    # Only when the account's settings file IS the user's file does sharing it
    # carry the mode too. A file the account has partly written itself was merely
    # filled in with the keys it lacked, and the mode may well be one of the keys
    # it already had — so the narrow per-key mirror still has work to do there.
    if report.shared.get(str(mode_file)) not in {"mirrored", "current"}:
        agent_accounts.inherit_default_mode(term.agent, term.account)  # type: ignore[arg-type]
    return agent_accounts.spawn_env(term.agent, term.account)  # type: ignore[arg-type]


def account_home(agent: str, account_id: str | None) -> Path | None:
    """The config dir a pane's conversation history lives in.

    ``None`` for a pane with no account (or an agent that has none), which keeps
    every existing lookup on its old path.
    """
    if not account_id or agent not in AGENT_BINARIES:
        return None
    from jarvis import agent_accounts

    return agent_accounts.config_dir_for(agent, account_id)  # type: ignore[arg-type]


def agent_argv(agent: str) -> tuple[str, ...] | None:
    """argv that runs ``agent`` as the PTY's own process, or None if missing."""
    binary = AGENT_BINARIES.get(agent)
    if binary is None:
        return None
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
            if agent == "codex":
                from jarvis.core.path_augment import resolve_node_executable

                node = resolve_node_executable()
                codex_js = (
                    Path(exe).resolve().parent
                    / "node_modules"
                    / "@openai"
                    / "codex"
                    / "bin"
                    / "codex.js"
                )
                if node and codex_js.is_file():
                    return (node, str(codex_js))
            # ConPTY cannot exec a batch shim. `cmd /c` (never /k) exits with
            # the agent, so no shell survives it.
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            return (comspec, "/c", exe)
        if lowered.endswith(".ps1"):
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if shell is None:
                return None
            return (shell, "-NoLogo", "-NoProfile", "-File", exe)
    return (exe,)


@dataclass(slots=True)
class Terminal:
    """One named pane: a call-sign, an agent, and its live PTY (if attached)."""

    key: str  # url-safe key, e.g. "mika"
    name: str  # spoken call-sign, e.g. "Mika"
    agent: str  # "claude" | "codex"
    display_name: str  # "Claude Code"
    index: int
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
    prompts_sent: int = 0
    last_prompt: str = ""
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

    def to_dict(self) -> dict[str, Any]:
        # Read the replayed screen ONCE. `lines()` walks the whole scrollback,
        # and both the line count below and the recap want it — asking twice per
        # pane per poll is a cost with nothing to show for it.
        lines = self.transcript.lines()
        summary = recap.summarize(self, tail=lines[-recap.TAIL_LINES :])
        return {
            "key": self.key,
            "name": self.name,
            "agent": self.agent,
            "display_name": self.display_name,
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
            "last_prompt": self.last_prompt,
            "submitted": self.submitted,
            "lines_captured": len(lines),
            # What this pane is doing, in the two lengths the header needs: one
            # clause for the label (which the pane's width will clip) and one or
            # two sentences for the tooltip behind it. Derived, never stored —
            # see .recap for why it is computed on read.
            "recap": summary.headline,
            "recap_detail": summary.detail,
            "resumed": self.resumed,
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
            column=self.column,
            slot=self.slot,
            resume=self.resume,
            prompts_sent=self.prompts_sent,
            account=self.account,
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
        """Terminal by key, call-sign, or a spoken phrase containing one."""
        if not wanted:
            return None
        key = normalize(wanted)
        for term in self.terminals:
            if normalize(term.key) == key or normalize(term.name) == key:
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
        }

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

            unknown = {str(r.get("agent")) for r in requested} - set(AGENT_BINARIES)
            if unknown:
                raise SessionError(f"Unknown agent(s): {', '.join(sorted(unknown))}")

            missing = sorted(
                {str(r.get("agent")) for r in requested if agent_argv(str(r.get("agent"))) is None}
            )
            if missing:
                pretty = ", ".join(AGENT_DISPLAY.get(m, m) for m in missing)
                raise SessionError(
                    f"{pretty} is not installed or not on this machine's PATH. "
                    "Install it from the CLIs page, then try again."
                )

            # Call-signs are unique across ALL open workspaces, not just within
            # this one. A name is how the user addresses a pane out loud, and a
            # second "Mika" two tabs over would make "tell Mika to run the
            # tests" a question rather than an instruction. So the pool skips
            # every name already spoken for, and this workspace simply starts
            # further down it.
            taken = self._reserved_names()
            offered = default_names(len(requested) + len(taken))
            pool = [n for n in offered if normalize(n) not in taken]
            used: set[str] = set(taken)
            terminals: list[Terminal] = []
            for index, entry in enumerate(requested):
                agent = str(entry.get("agent"))
                fallback = pool[index] if index < len(pool) else f"T{index + 1}"
                wanted = str(entry.get("name") or "").strip() or fallback
                name = wanted
                suffix = 2
                while normalize(name) in used:
                    name = f"{wanted} {suffix}"
                    suffix += 1
                used.add(normalize(name))
                terminals.append(
                    Terminal(
                        key=normalize(name) or f"t{index}",
                        name=name,
                        agent=agent,
                        display_name=AGENT_DISPLAY.get(agent, agent),
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

    def _reserved_names(self) -> set[str]:
        """Every call-sign in use across all open workspaces, normalized."""
        return {
            normalize(term.name)
            for session in self._sessions.values()
            for term in session.terminals
        }

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
        await self._persist()
        return session

    async def restore(self, snapshot: resume_store.Snapshot) -> RestoreResult:
        """Reopen EVERY workspace a snapshot describes, starting nothing.

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
                display_name=AGENT_DISPLAY.get(entry.agent, entry.agent),
                index=index,
                column=entry.column,
                slot=entry.slot,
                resume=entry.resume,
                prompts_sent=entry.prompts_sent,
                # The remembered account, re-validated: a pane must come back
                # on the subscription whose history holds its conversation,
                # and an account deleted in the meantime falls back to the
                # active one rather than failing the reopen.
                account=resolve_account(entry.agent, entry.account),
            )
            for index, entry in enumerate(space.terminals)
        ]
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

    def _dedupe_names(self, terminals: list[Terminal]) -> None:
        """Rename any call-sign already spoken for by another open workspace."""
        used = self._reserved_names()
        for term in terminals:
            if normalize(term.name) not in used:
                used.add(normalize(term.name))
                continue
            base, suffix = term.name, 2
            while normalize(f"{base} {suffix}") in used:
                suffix += 1
            term.name = f"{base} {suffix}"
            term.key = normalize(term.name) or term.key
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
        if manager is not None:
            for term in session.terminals:
                if term.pty_id:
                    try:
                        manager.close(term.pty_id)
                    except Exception:  # noqa: BLE001, S110 - best-effort teardown
                        pass
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

    async def attach(
        self,
        key: str,
        cols: int,
        rows: int,
        on_output: Any,
        on_exit: Any,
        workspace_id: str | None = None,
        appearance: str | None = None,
    ) -> Terminal:
        """Point a viewer at terminal ``key``, starting its agent if needed.

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
            term.viewer_output = on_output
            term.viewer_exit = on_exit
            term.reattached = True
            term.stopping = False
            term.transcript.resize(cols, rows)
            manager.resize(term.pty_id, cols, rows)
            replay = term.replay.text()
            if replay:
                # Hand over the raw stream that drew the current screen. A
                # coding agent's TUI is a painted surface, not a log: without
                # this the pane comes back blank until the agent happens to
                # repaint, which looks exactly like a dead terminal.
                await on_output(replay)
            if term.replay.truncated:
                # The tail is all this pane has, and it no longer starts where
                # the agent started drawing. Replaying it alone brings back the
                # one row the agent rewrote last and empty space where its
                # prompt box should be — see ReplayBuffer's docstring. Ask for
                # a fresh paint instead of hoping one arrives.
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
            extra, minted = launch_extra(term.agent)
            argv = (*argv, *extra)
            term.resumed = False
            if minted is not None:
                term.resume = minted

        term.transcript.resize(cols, rows)
        # A fresh process draws a fresh screen: anything the previous one left
        # in the replay buffer belongs to a terminal that no longer exists, and
        # replaying it to the next viewer would show output from a dead agent.
        term.replay.clear()
        term.viewer_output = on_output
        term.viewer_exit = on_exit
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
            # FIRST, before anything that could yield: a CLI asking its terminal
            # for the device type or the screen colours reads the answer within
            # milliseconds of asking. Answering here rather than in the browser
            # keeps the whole exchange inside this process — a reply that has to
            # cross two WebSocket hops arrives after the CLI stopped listening,
            # and then appears as pasted-looking junk in its prompt. Addressed to
            # `tid` rather than `term.pty_id`, which is assigned a moment later.
            replies = term.queries.feed(text)
            if replies:
                manager.write(tid, replies)
            term.transcript.feed(text)
            term.replay.feed(text)
            term.last_output_at = time.time()
            viewer = term.viewer_output
            if viewer is not None:
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
            viewer = term.viewer_exit
            if viewer is not None:
                await viewer(code)

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
                env=_spawn_env(term),
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
        term.last_output_at = time.time()
        if term.resume is None and can_resume(term.agent):
            # A CLI that cannot be told its session id (Codex): find out which
            # one it just created, shortly from now.
            self._schedule_lookup(session, term, session.folder, term.started_at)
        await self._persist()
        return term

    def _prepare_spawn(self, term: Terminal, folder: str) -> dict[str, str] | None:
        """Everything this pane's agent needs on disk, then its environment.

        One thread hop for both, because both are filesystem work that has to
        finish before the process starts, and both exist for the same reason: a
        pane on an added subscription must open as the same session the user's
        own terminal opens (:func:`_spawn_env`), and it must not stop on a "do
        you trust this directory?" dialog on the way there.
        """
        self._pre_trust(term, folder)
        return _spawn_env(term)

    def _pre_trust(self, term: Terminal, folder: str) -> None:
        """Mark this folder trusted in the config dir THIS pane will run from.

        The workspace open already seeded the machine's own config, which covers
        every pane on the built-in login. A pane on an added account reads a
        different directory entirely, so without this it opens on the trust
        dialog — and a dialog nobody can answer from voice or the prompt bar is
        an agent that never starts. Once per folder and account per process.

        Never raises: an unseeded pane costs one click, a failed spawn costs the
        pane.
        """
        home = _redirected_home(term)
        if home is None:
            return
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
        self, owner: Session, term: Terminal, folder: str, started_at: float
    ) -> None:
        """Find a pane's session id a moment after its CLI created it.

        Fire-and-forget, and deliberately not awaited by ``attach``: the pane is
        already usable, and making the user wait for a filesystem scan to learn
        something only needed after a restart would be the wrong trade.

        Bound to the workspace that owns the pane rather than to "the current
        one": with several open, the front workspace can change twice while this
        is sleeping, and a lookup that then read the front one would write a
        Codex conversation id onto a pane in a different folder.
        """

        async def _look() -> None:
            try:
                for delay in DISCOVERY_DELAYS_S:
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

        task = asyncio.ensure_future(_look())
        owner.lookups.add(task)
        task.add_done_callback(owner.lookups.discard)

    def write(self, key: str, data: str, workspace_id: str | None = None) -> bool:
        """Raw keystrokes from the pane's own xterm (not the injection path)."""
        found = self._locate(key, workspace_id)
        if found is None:
            return False
        term = found[1]
        if not term.pty_id:
            return False
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

    def resize(self, key: str, cols: int, rows: int, workspace_id: str | None = None) -> bool:
        found = self._locate(key, workspace_id)
        if found is None:
            return False
        term = found[1]
        if not term.pty_id:
            return False
        # The replayed screen has to follow the real one; otherwise the
        # transcript keeps wrapping at the old width.
        term.transcript.resize(cols, rows)
        return self._manager().resize(term.pty_id, cols, rows)

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
        current = term.viewer_output
        # Compared by equality, not only by identity: a bound method is a brand
        # new object on every attribute access, so `is` would answer "you are
        # not the viewer" to the very callback sitting in the slot.
        if viewer is not None and current is not viewer and current != viewer:
            # Somebody else is watching this pane now. Leaving quietly is the
            # whole job — the slot belongs to the newer viewer.
            logger.debug(
                "Agentic IDE: a departing viewer left {} to the one that replaced it",
                term.name,
            )
            return
        term.viewer_output = None
        term.viewer_exit = None

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

        ``account`` names which subscription of that agent to run on; without one
        the pane inherits the anchor's, falling back to the active account. The
        inheritance matters more than it looks: splitting a pane that runs the
        second plan should stay on the second plan, or a "split" would quietly
        move the work onto a different bill.
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
            if chosen not in AGENT_BINARIES:
                raise SessionError(f"Unknown agent: {chosen}")
            if agent_argv(chosen) is None:
                pretty = AGENT_DISPLAY.get(chosen, chosen)
                raise SessionError(f"{pretty} is not installed or not on this machine's PATH.")

            # Unused ACROSS every open workspace: a split must not hand this
            # pane a call-sign another tab already answers to.
            used = self._reserved_names()
            wanted = (name or "").strip()
            if not wanted:
                # Next unused call-sign from the pool, so names stay speakable
                # however many times the user splits.
                pool = default_names(MAX_TERMINALS * MAX_WORKSPACES)
                wanted = next(
                    (n for n in pool if normalize(n) not in used),
                    f"T{len(session.terminals) + 1}",
                )
            final = wanted
            suffix = 2
            while normalize(final) in used:
                final = f"{wanted} {suffix}"
                suffix += 1

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

            # Inherit the anchor's account only when the split stays on the same
            # CLI — a Claude account id means nothing to Codex.
            inherited = base.account if base is not None and base.agent == chosen else None
            term = Terminal(
                key=normalize(final) or f"t{len(session.terminals)}",
                name=final,
                agent=chosen,
                display_name=AGENT_DISPLAY.get(chosen, chosen),
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
        drift from what the split buttons do.

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
                session.terminals.remove(term)
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
        _session, term = found
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
        term.submitted = submitted
        term.sent_multiline = multiline and submitted is True
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

        Call-signs are unique across open workspaces (see ``start``), so a name
        identifies exactly one pane and searching beyond the front one cannot be
        ambiguous. It is also what a user means: "tell Kai to run the tests" is
        an instruction to Kai, not a request to first go and find which tab Kai
        is in. The front workspace is still tried first, so the common case
        never depends on iteration order.
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
        """The 'no such pane' error, naming every pane that DOES exist."""
        if not self._sessions:
            return SessionError("No Agentic-IDE session is running.")
        known = ", ".join(term.name for s in self._sessions.values() for term in s.terminals)
        return SessionError(f"No terminal called {wanted!r}. Running: {known or 'none'}.")


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
    "Registry",
    "Session",
    "SessionError",
    "SessionNotReady",
    "Terminal",
    "agent_argv",
    "get_registry",
    "reset_registry",
    "sanitize_prompt",
    "terminals_added_event",
]
