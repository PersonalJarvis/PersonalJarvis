"""In-process state of the Agentic-IDE workspace.

One registry holds at most one *session*: a chosen folder plus N named
terminals, each running a coding-agent CLI (Claude Code / Codex) in a real
pseudo-terminal rooted in that folder. The registry is what makes the feature
more than an embedded terminal grid — it is the thing Jarvis reads from and
writes to:

* **reads** — every terminal keeps a sanitized transcript, so "what is Mika
  doing?" is answered from what Mika actually printed, not from a guess,
* **writes** — a prompt can be injected into a terminal from the outside
  (voice, chat, CLI), which is how you talk to an agent without touching the
  keyboard.

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

from . import resume_store
from .agent_sessions import (
    ResumeHandle,
    can_resume,
    discover,
    launch_extra,
    resume_argv,
)
from .folders import ProjectProfile, probe_project
from .names import default_names, normalize, resolve
from .transcript import Transcript

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.terminal.pty_manager import PtyManager

# Agents the IDE can run. Kept parallel to jarvis.workspace.agents (same two
# CLIs, same display names) but with argv built for a one-shot agent process
# rather than a persistent shell.
AGENT_BINARIES: dict[str, str] = {"claude": "claude", "codex": "codex"}
AGENT_DISPLAY: dict[str, str] = {"claude": "Claude Code", "codex": "Codex"}

MAX_TERMINALS = 12
MAX_PROMPT_CHARS = 4000
# Delay between the prompt text and the Enter keystroke. Agent TUIs debounce
# fast bursts as a paste; ~120 ms is past every debounce window measured while
# still feeling instant.
ENTER_DELAY_S = 0.12

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
# suspect. A conversation the CLI has since pruned makes it print an error and
# exit almost immediately; a healthy agent that the user quits normally exits
# with code 0 and is never second-guessed. Past this window an exit is just an
# exit, and the pane says so.
RESUME_FAILED_WINDOW_S = 8.0

# When to look for the session id of a CLI that cannot be told one (Codex). It
# writes its rollout file a beat after launching, so asking immediately finds
# nothing; two attempts cover a slow machine without turning into polling.
DISCOVERY_DELAYS_S = (4.0, 12.0)

# Bracketed paste. A TUI that has enabled it receives everything between these
# markers as ONE pasted block rather than as keystrokes, which is the only way
# a structured prompt survives the trip: a bare "\n" written to a PTY IS the
# Enter key, so an unwrapped markdown prompt would submit after its first line.
# This is a terminal-level convention, not an OS API — the same bytes go down
# the same PTY on Windows, macOS and Linux.
PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"

# What an agent TUI draws instead of the text when it collapses a paste into a
# placeholder ("[Pasted text #1 +12 lines]").
_PASTE_PLACEHOLDER_RE = re.compile(r"\[\s*pasted?\s+text[^\]]*\]", re.IGNORECASE)


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

    key: str            # url-safe key, e.g. "mika"
    name: str           # spoken call-sign, e.g. "Mika"
    agent: str          # "claude" | "codex"
    display_name: str   # "Claude Code"
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
    status: Status = "pending"
    pty_id: str | None = None
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
    transcript: Transcript = field(default_factory=Transcript)

    def to_dict(self) -> dict[str, Any]:
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
            "lines_captured": len(self.transcript.lines()),
            "resumed": self.resumed,
            # Whether a handle EXISTS, never the handle itself: it is an
            # internal pointer into the CLI's history and no client needs it.
            "has_resume": self.resume is not None,
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
        )


@dataclass(slots=True)
class Session:
    """A chosen folder plus its named terminals."""

    id: str
    folder: str
    profile: ProjectProfile
    terminals: list[Terminal]
    created_at: float
    # Focus mode: while on, Jarvis answers inside this workspace's context. The
    # flag lives here (not in jarvis.toml) on purpose — it is a mode of the
    # current session, and a restart should land the user back in normal mode
    # rather than silently keeping a narrowed assistant.
    focus_mode: bool = False

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
            "project": self.profile.to_dict(),
            "created_at": self.created_at,
            "focus_mode": self.focus_mode,
            "terminals": [t.to_dict() for t in self.terminals],
        }


class SessionError(RuntimeError):
    """A request the registry refuses, with a user-facing English message."""


class Registry:
    """Process-wide holder of the single active Agentic-IDE session."""

    def __init__(self, pty_manager: PtyManager | None = None) -> None:
        self._session: Session | None = None
        # Injectable so tests can drive the registry against a fake PTY pool
        # without a real pseudo-terminal (and without a coding agent installed).
        self._pty: PtyManager | None = pty_manager
        self._lock = asyncio.Lock()
        # Background session-id lookups. Held so the loop cannot garbage-collect
        # a task mid-flight, and cancelled when the workspace closes.
        self._lookups: set[asyncio.Task[None]] = set()

    # ---------------------------------------------------------------- state
    @property
    def session(self) -> Session | None:
        return self._session

    def state(self) -> dict[str, Any]:
        return {
            "active": self._session is not None,
            "session": self._session.to_dict() if self._session else None,
            "max_terminals": MAX_TERMINALS,
        }

    def _manager(self) -> PtyManager:
        if self._pty is None:
            # Lazy: keeps the terminal stack off the import/boot path (AP-26).
            from jarvis.terminal.pty_manager import PtyManager

            self._pty = PtyManager()
        return self._pty

    # -------------------------------------------------------------- session
    async def start(
        self, folder: str, requested: list[dict[str, Any]]
    ) -> Session:
        """Create a session for ``folder`` with one terminal per request entry.

        ``requested`` entries look like ``{"agent": "claude", "name": "Mika"}``;
        the name is optional and filled from the call-sign pool.
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

            unknown = {str(r.get("agent")) for r in requested} - set(AGENT_BINARIES)
            if unknown:
                raise SessionError(f"Unknown agent(s): {', '.join(sorted(unknown))}")

            missing = sorted(
                {
                    str(r.get("agent"))
                    for r in requested
                    if agent_argv(str(r.get("agent"))) is None
                }
            )
            if missing:
                pretty = ", ".join(AGENT_DISPLAY.get(m, m) for m in missing)
                raise SessionError(
                    f"{pretty} is not installed or not on this machine's PATH. "
                    "Install it from the CLIs page, then try again."
                )

            # Close a previous session before replacing it, or its PTYs leak.
            # It is replaced, not abandoned, so its resume offer stays until
            # this workspace writes its own a moment from now.
            await self._close_locked(forget=False)

            pool = default_names(len(requested))
            used: set[str] = set()
            terminals: list[Terminal] = []
            for index, entry in enumerate(requested):
                agent = str(entry.get("agent"))
                wanted = str(entry.get("name") or "").strip() or pool[index]
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
                    )
                )

            session = await self._open_locked(root, terminals)
            logger.info(
                "Agentic IDE session started: {} terminals in {}",
                len(terminals),
                root,
            )
            return session

    async def _open_locked(self, root: Path, terminals: list[Terminal]) -> Session:
        """Turn a prepared list of panes into THE open session.

        Shared by ``start`` and ``restore`` on purpose. Everything a workspace
        needs before its first pane connects lives here exactly once — the
        project probe, the trust pre-seed, the codebase index — so a resumed
        workspace can never quietly differ from a freshly opened one. Both
        callers hold ``self._lock`` and have already closed any predecessor.
        """
        profile = await asyncio.to_thread(probe_project, root)

        # Pre-seed agent trust for this folder so no terminal stops on a
        # "do you trust this directory?" dialog the user cannot see coming.
        try:
            from jarvis.workspace.trust import ensure_trusted

            await asyncio.to_thread(
                ensure_trusted, root, sorted({t.agent for t in terminals})
            )
        except Exception as exc:  # noqa: BLE001 - trust is a convenience
            logger.warning("Agentic IDE: pre-trust failed: {}", exc)

        session = Session(
            id=f"ide_{uuid4().hex[:12]}",
            folder=str(root),
            profile=profile,
            terminals=terminals,
            created_at=time.time(),
        )
        self._session = session
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

    async def restore(self, snapshot: resume_store.Snapshot) -> Session:
        """Reopen the workspace a snapshot describes, without starting anything.

        The panes come back with their call-signs, their coding CLIs, their grid
        coordinates and their resume handles — and in ``pending``, because
        spawning is not this method's job. The grid attaches its panes the way
        it always does, and ``attach`` spends the handles. That keeps ONE place
        where an agent is started; a second spawn path here would drift from it
        the first time either changed.

        A pane whose CLI is no longer installed is still restored. It shows up
        as an error the moment it tries to connect, which is a far better
        outcome than silently dropping a terminal the user expects to see.
        """
        async with self._lock:
            if not snapshot.terminals:
                raise SessionError("That workspace has no terminals to reopen.")

            root = Path(snapshot.folder).expanduser()  # noqa: ASYNC240
            try:
                if not await asyncio.to_thread(root.is_dir):
                    raise SessionError(
                        f"{root} is no longer on this machine — that workspace "
                        "cannot be reopened."
                    )
            except OSError as exc:
                raise SessionError(f"Cannot open {root}: {exc}") from exc

            await self._close_locked(forget=False)

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
                )
                for index, entry in enumerate(snapshot.terminals)
            ]
            session = await self._open_locked(root, terminals)
            # Pack the grid: a snapshot can carry gaps if it was written between
            # a close and its renumbering, and a gap renders as a blank stripe.
            self._renumber(session)
            await self._persist()
            logger.info(
                "Agentic IDE session resumed: {} terminals in {} ({} with a "
                "conversation to continue)",
                len(terminals),
                root,
                sum(1 for t in terminals if t.resume is not None),
            )
            return session

    async def end(self) -> bool:
        async with self._lock:
            existed = self._session is not None
            await self._close_locked(forget=True)
            return existed

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> resume_store.Snapshot | None:
        """The open workspace in the form the resume store keeps it, or None."""
        session = self._session
        if session is None:
            return None
        return resume_store.snapshot_now(
            session_id=session.id,
            folder=session.folder,
            terminals=[t.to_snapshot() for t in session.terminals],
        )

    async def _persist(self) -> None:
        """Record the workspace so it can be offered back later.

        Best-effort and off the event loop. A resume point is a convenience;
        failing to write one must never break the workspace that is running
        perfectly well right now.
        """
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

    async def _close_locked(self, *, forget: bool) -> None:
        """Tear the workspace down. ``forget`` also withdraws the resume offer.

        The two callers want opposite things. Ending a session is the user
        saying "I am done", so the offer goes with it — re-offering something
        somebody deliberately closed is the kind of prompt people learn to click
        away. Replacing a session (opening or resuming another workspace) must
        NOT withdraw anything: the new workspace writes its own snapshot a
        moment later, and clearing in between would lose the offer if that write
        never happened.
        """
        for task in list(self._lookups):
            task.cancel()
        self._lookups.clear()
        session = self._session
        self._session = None
        if session is None:
            if forget:
                await self._forget()
            return
        if forget:
            await self._forget()
        manager = self._pty
        if manager is not None:
            for term in session.terminals:
                if term.pty_id:
                    try:
                        manager.close(term.pty_id)
                    except Exception:  # noqa: BLE001, S110 - best-effort teardown
                        pass
        # Drop the codebase index with the workspace: keeping it would hand the
        # next session a snapshot of a folder that may have changed since.
        try:
            from . import file_index

            file_index.reset_cache()
        except Exception:  # noqa: BLE001, S110 - best-effort teardown
            pass
        logger.info("Agentic IDE session ended: {}", session.id)

    def set_focus_mode(self, enabled: bool) -> bool:
        """Turn the focused coding mode on/off. Returns the resulting state."""
        session = self._session
        if session is None:
            if enabled:
                raise SessionError(
                    "No Agentic-IDE session is running — open one first."
                )
            return False
        session.focus_mode = bool(enabled)
        logger.info("Agentic IDE focus mode {}", "on" if enabled else "off")
        return session.focus_mode

    # ------------------------------------------------------------------ pty
    async def attach(
        self, key: str, cols: int, rows: int, on_output: Any, on_exit: Any
    ) -> Terminal:
        """Spawn the agent for terminal ``key`` and stream it to the caller.

        ``on_output(text)`` / ``on_exit(code)`` are awaited in this loop. The
        transcript is fed here, so it keeps filling even if the UI pane is
        closed and reconnects later.

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
        session = self._session
        if session is None:
            raise SessionError("No Agentic-IDE session is running.")
        term = session.find(key)
        if term is None:
            raise SessionError(f"Unknown terminal: {key}")

        manager = self._manager()
        if term.pty_id and manager.has(term.pty_id):
            # A second viewer for a pane that is already running: close the old
            # PTY rather than silently running two agents for one call-sign.
            manager.close(term.pty_id)
            term.pty_id = None

        argv = agent_argv(term.agent)
        if argv is None:
            term.status = "error"
            term.error = f"{term.display_name} is not on PATH."
            raise SessionError(term.error)

        continuing = resume_argv(term.agent, term.resume)
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
        # Monotonic: a wall clock can jump (NTP, a laptop waking up) and would
        # then mis-measure how long the agent survived.
        spawned_at = time.monotonic()
        recovered = False

        async def _output(_tid: str, text: str) -> None:
            term.transcript.feed(text)
            term.last_output_at = time.time()
            await on_output(text)

        async def _closed(_tid: str, code: int) -> None:
            nonlocal recovered
            term.pty_id = None
            died_young = time.monotonic() - spawned_at < RESUME_FAILED_WINDOW_S
            # Only a FAILED early exit is blamed on the resume. Quitting an
            # agent normally exits 0, and restarting a pane the user just closed
            # would be its own bug.
            if term.resumed and not recovered and died_young and code != 0:
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
                    await self.attach(key, cols, rows, on_output, on_exit)
                except SessionError as exc:
                    logger.warning(
                        "Agentic IDE: {} could not be restarted: {}", term.name, exc
                    )
                else:
                    # The pane is alive again; telling the viewer it exited
                    # would flash a dead pane for no reason.
                    return
            term.status = "exited"
            term.exit_code = code
            await on_exit(code)

        try:
            pty_session = await manager.spawn(
                shell_argv=argv,
                shell_id=f"agentic-ide:{term.key}",
                cwd=session.folder,
                cols=cols,
                rows=rows,
                on_output=_output,
                on_closed=_closed,
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
            self._schedule_lookup(term, session.folder, term.started_at)
        await self._persist()
        return term

    def _schedule_lookup(self, term: Terminal, folder: str, started_at: float) -> None:
        """Find a pane's session id a moment after its CLI created it.

        Fire-and-forget, and deliberately not awaited by ``attach``: the pane is
        already usable, and making the user wait for a filesystem scan to learn
        something only needed after a restart would be the wrong trade.
        """

        async def _look() -> None:
            try:
                for delay in DISCOVERY_DELAYS_S:
                    await asyncio.sleep(delay)
                    session = self._session
                    if session is None or term not in session.terminals:
                        return  # the pane (or the workspace) is gone
                    if term.resume is not None:
                        return
                    taken = {
                        other.resume.id
                        for other in session.terminals
                        if other.resume is not None
                    }
                    found = await asyncio.to_thread(
                        discover, term.agent, folder, started_at, taken
                    )
                    if found is None:
                        continue
                    term.resume = found
                    logger.debug(
                        "Agentic IDE: {} is conversation {}", term.name, found.id
                    )
                    await self._persist()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a convenience, never fatal
                logger.debug("Agentic IDE: session lookup for {} failed: {}", term.name, exc)

        task = asyncio.ensure_future(_look())
        self._lookups.add(task)
        task.add_done_callback(self._lookups.discard)

    def write(self, key: str, data: str) -> bool:
        """Raw keystrokes from the pane's own xterm (not the injection path)."""
        session = self._session
        if session is None:
            return False
        term = session.find(key)
        if term is None or not term.pty_id:
            return False
        return self._manager().write(term.pty_id, data)

    def resize(self, key: str, cols: int, rows: int) -> bool:
        session = self._session
        if session is None:
            return False
        term = session.find(key)
        if term is None or not term.pty_id:
            return False
        # The replayed screen has to follow the real one; otherwise the
        # transcript keeps wrapping at the old width.
        term.transcript.resize(cols, rows)
        return self._manager().resize(term.pty_id, cols, rows)

    def detach(self, key: str) -> None:
        """Close the PTY behind a pane (the viewer went away)."""
        session = self._session
        if session is None:
            return
        term = session.find(key)
        if term is None or not term.pty_id:
            return
        self._manager().close(term.pty_id)
        term.pty_id = None
        if term.status == "live":
            term.status = "exited"

    # ------------------------------------------------------------- panes
    async def add_terminal(
        self,
        *,
        agent: str | None = None,
        name: str | None = None,
        anchor: str | None = None,
        direction: str = "right",
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
        """
        async with self._lock:
            session = self._session
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

            used = {normalize(t.name) for t in session.terminals}
            wanted = (name or "").strip()
            if not wanted:
                # Next unused call-sign from the pool, so names stay speakable
                # however many times the user splits.
                pool = default_names(MAX_TERMINALS * 2)
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

            term = Terminal(
                key=normalize(final) or f"t{len(session.terminals)}",
                name=final,
                agent=chosen,
                display_name=AGENT_DISPLAY.get(chosen, chosen),
                index=len(session.terminals),
                column=column,
                slot=slot,
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
        self, count: int, *, agent: str | None = None
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
        if self._session is None:
            raise SessionError("No Agentic-IDE session is running.")
        wanted = max(1, int(count))
        created: list[Terminal] = []
        for _ in range(wanted):
            try:
                created.append(await self.add_terminal(agent=agent))
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
        async with self._lock:
            session = self._session
            if session is None:
                raise SessionError("No Agentic-IDE session is running.")
            term = session.find(wanted)
            if term is None:
                known = ", ".join(t.name for t in session.terminals) or "none"
                raise SessionError(f"No terminal called {wanted!r}. Running: {known}.")

            if term.pty_id and self._pty is not None:
                try:
                    self._pty.close(term.pty_id)
                except Exception:  # noqa: BLE001, S110 - best-effort teardown
                    pass
            term.pty_id = None
            term.status = "exited"
            session.terminals.remove(term)
            self._renumber(session)
            await self._persist()
            logger.info("Agentic IDE: closed terminal {}", term.name)
            return term

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
        session = self._session
        if session is None:
            raise SessionError("No Agentic-IDE session is running.")
        term = session.find(wanted)
        if term is None:
            known = ", ".join(t.name for t in session.terminals) or "none"
            raise SessionError(f"No terminal called {wanted!r}. Running: {known}.")
        if term.status != "live" or not term.pty_id:
            raise SessionError(
                f"{term.name} is not running right now "
                f"(status: {term.status}) — nothing was sent."
            )
        payload = sanitize_prompt(text, keep_newlines=True)
        if not payload:
            raise SessionError("The prompt was empty after cleanup.")

        manager = self._manager()
        multiline = "\n" in payload

        submitted = await self._write_and_confirm(term, payload, manager, multiline)
        if not submitted and multiline:
            logger.warning(
                "Agentic IDE: {} did not accept a multi-line prompt — "
                "re-sending it on one line",
                term.name,
            )
            payload = sanitize_prompt(payload)
            multiline = False
            submitted = await self._write_and_confirm(term, payload, manager, False)

        term.prompts_sent += 1
        term.last_prompt = payload
        term.submitted = submitted
        term.sent_multiline = multiline and submitted
        logger.info(
            "Agentic IDE prompt -> {} ({}, {}): {}",
            term.name,
            "submitted" if term.submitted else "STILL IN THE INPUT BOX",
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
    ) -> bool:
        """Type ``payload``, press Enter, and report whether it was accepted."""
        # The completion guard applies to the LAST line: that is the one the
        # cursor sits on when Enter arrives.
        last_line = payload.rsplit("\n", 1)[-1]
        typed = payload + (" " if _opens_completion(last_line) else "")
        if multiline:
            typed = f"{PASTE_START}{typed}{PASTE_END}"
        if not manager.write(term.pty_id or "", typed):
            raise SessionError(f"Could not write to {term.name}.")
        await asyncio.sleep(ENTER_DELAY_S)
        manager.write(term.pty_id or "", "\r")
        return await self._confirm_submitted(term, payload, manager)

    async def _confirm_submitted(
        self, term: Terminal, payload: str, manager: PtyManager
    ) -> bool:
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
        session = self._session
        if session is None:
            raise SessionError("No Agentic-IDE session is running.")
        term = session.find(wanted)
        if term is None:
            known = ", ".join(t.name for t in session.terminals) or "none"
            raise SessionError(f"No terminal called {wanted!r}. Running: {known}.")
        data = term.to_dict()
        data["folder"] = session.folder
        data["transcript"] = term.transcript.tail(max(1, min(lines, 300)))
        return data


def terminals_added_event(
    session: Session, created: list[Terminal], *, source_layer: str
) -> Any:
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
    "ENTER_DELAY_S",
    "MAX_PROMPT_CHARS",
    "MAX_TERMINALS",
    "Registry",
    "Session",
    "SessionError",
    "Terminal",
    "agent_argv",
    "get_registry",
    "reset_registry",
    "sanitize_prompt",
    "terminals_added_event",
]
