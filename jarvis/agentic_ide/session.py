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
Windows, which ConPTY cannot execute directly, so it is wrapped in a
one-shot ``cmd /c`` (one-shot, so rule 1 above still holds).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

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


def sanitize_prompt(text: str) -> str:
    """Injectable form of ``text``: printable characters only, length-capped.

    Escape sequences are removed whole (so ``ESC [ A`` does not leave a stray
    ``[A`` in the prompt), then newlines and tabs collapse to spaces and every
    remaining C0 control is dropped — the caller cannot smuggle Ctrl-C, ESC, or
    EOF into a running agent.
    """
    from .transcript import strip_ansi

    cleaned = "".join(
        " " if ch in "\r\n\t" else ch
        for ch in strip_ansi(text)
        if ch >= " " or ch in "\r\n\t"
    )
    return " ".join(cleaned.split())[:MAX_PROMPT_CHARS]


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
    status: Status = "pending"
    pty_id: str | None = None
    exit_code: int | None = None
    error: str = ""
    started_at: float | None = None
    last_output_at: float | None = None
    prompts_sent: int = 0
    last_prompt: str = ""
    transcript: Transcript = field(default_factory=Transcript)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "agent": self.agent,
            "display_name": self.display_name,
            "index": self.index,
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
            "lines_captured": len(self.transcript.lines()),
        }


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
            await self._close_locked()

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
                    )
                )

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
            # Remember the workspace so the next visit is one click, replaying
            # the same terminal count and agent split (see recents.py).
            split: dict[str, int] = {}
            for term in terminals:
                split[term.agent] = split.get(term.agent, 0) + 1
            try:
                from . import recents

                await asyncio.to_thread(
                    recents.remember,
                    str(root),
                    terminals=len(terminals),
                    agents=split,
                )
            except Exception as exc:  # noqa: BLE001 - convenience, never fatal
                logger.warning("Agentic IDE: recents not updated: {}", exc)
            logger.info(
                "Agentic IDE session started: {} terminals in {}",
                len(terminals),
                root,
            )
            return session

    async def end(self) -> bool:
        async with self._lock:
            existed = self._session is not None
            await self._close_locked()
            return existed

    async def _close_locked(self) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        manager = self._pty
        if manager is not None:
            for term in session.terminals:
                if term.pty_id:
                    try:
                        manager.close(term.pty_id)
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

        term.transcript.resize(cols, rows)

        async def _output(_tid: str, text: str) -> None:
            term.transcript.feed(text)
            term.last_output_at = time.time()
            await on_output(text)

        async def _closed(_tid: str, code: int) -> None:
            term.status = "exited"
            term.exit_code = code
            term.pty_id = None
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
        return term

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

    # --------------------------------------------------------------- prompt
    async def send_prompt(self, wanted: str, text: str) -> Terminal:
        """Type ``text`` into a terminal and press Enter.

        Raises ``SessionError`` when the terminal is unknown, not running, or
        the prompt sanitizes down to nothing.
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
        payload = sanitize_prompt(text)
        if not payload:
            raise SessionError("The prompt was empty after cleanup.")

        manager = self._manager()
        if not manager.write(term.pty_id, payload):
            raise SessionError(f"Could not write to {term.name}.")
        await asyncio.sleep(ENTER_DELAY_S)
        manager.write(term.pty_id, "\r")

        term.prompts_sent += 1
        term.last_prompt = payload
        logger.info("Agentic IDE prompt -> {}: {}", term.name, payload[:120])
        return term

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
]
