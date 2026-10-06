"""The title a pane's coding CLI gave its own session — taken, never written.

Claude Code and Codex both name a conversation themselves, with their own
subscription, a moment after it starts: Claude Code appends an ``ai-title``
record to its transcript (and a ``custom-title`` one on ``/rename``) and puts
the same words in the terminal's window title as ``"✳ <topic>"``; Codex keeps
a ``thread_name`` per thread in ``session_index.jsonl``. That is exactly the
navigation label a pane header wants — so the header takes it instead of
paying a second model to write another one.

Two sources, in order:

* **The CLI's own record on disk.** Durable: it survives a backend restart and
  a pane that has not repainted its title since. Read off the event loop,
  incrementally (only the bytes appended since the last read), and re-checked
  on a slow clock, because a CLI renames a session rarely.
* **The live window title** (OSC 0/2, :attr:`ScreenBuffer.title`). Works for
  every CLI that sets one, before the record exists, and on a pane running on
  another computer whose files are not on this disk. Spinner and status glyphs
  are stripped, and a title that only names the program, the folder or a
  shell executable is not a title.

Never raises: this sits on the path of every state read.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from . import agent_sessions

#: How often a pane's record is looked at again. A CLI names a session once,
#: shortly after the first message, and renames it rarely; a few seconds of
#: delay on a rename is invisible, a file read per pane per poll is not.
RECHECK_S = 15.0

#: Longest title kept. A CLI's own title is a few words; this only stops a
#: pathological window title from becoming the transport's problem.
MAX_CHARS = 120

#: Entries are dropped on pane close (:func:`forget`); this bound only keeps a
#: process that never closes anything from growing without limit.
MAX_ENTRIES = 512

#: Window titles that name the program rather than the session.
_GENERIC = frozenset({"claude", "claude code", "codex", "openai codex", "gemini", "opencode"})

#: Leading spinner / status glyphs: anything up to the first letter or digit.
_LEAD_RE = re.compile(r"^[\W_]+", flags=re.UNICODE)

#: A shell or executable path set as the title (``C:\WINDOWS\system32\cmd.exe``).
_EXE_RE = re.compile(r"(?i)^(?:[a-z]:)?[\\/].*|.*\.(?:exe|cmd|bat|ps1)$")


@dataclass
class _Record:
    """What has been read of one pane's record file so far."""

    path: Path | None = None
    offset: int = 0
    ai_title: str = ""
    custom_title: str = ""
    checked_at: float = 0.0
    inflight: bool = False

    @property
    def title(self) -> str:
        return self.custom_title or self.ai_title


@dataclass
class _CodexIndex:
    """``session_index.jsonl`` of one CODEX_HOME, read incrementally."""

    offset: int = 0
    names: dict[str, str] = field(default_factory=dict)


_records: dict[tuple[str, str], _Record] = {}
#: Chat threads keep their own cache: a long thread list must not evict the
#: panes' entries, nor the panes the threads'.
_thread_records: dict[tuple[str, str], _Record] = {}
#: Thread titles are read here, never on the request that lists the threads.
_reader = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cli-title")
#: Bound on the thread cache — far above any real thread list.
MAX_THREAD_ENTRIES = 4096
_codex_indexes: dict[str, _CodexIndex] = {}
#: Several panes' reads run on executor threads and share one index per home.
_codex_lock = threading.Lock()


def title_for(term: Any) -> str:
    """The title ``term``'s CLI gave its session, or "" when it has none yet."""
    try:
        return _clip(_recorded(term) or window_title(term))
    except Exception as exc:  # noqa: BLE001 - a title must never break a state read
        logger.debug("Agentic IDE: CLI title of {} unreadable: {}", getattr(term, "key", "?"), exc)
        return ""


def window_title(term: Any) -> str:
    """The pane's live window title, cleaned — "" when it names no session."""
    transcript = getattr(term, "transcript", None)
    screen = getattr(transcript, "screen", None)
    return clean_window_title(
        str(getattr(screen, "title", "") or ""),
        names=(
            str(getattr(term, "agent", "") or ""),
            str(getattr(term, "display_name", "") or ""),
            Path(str(getattr(term, "folder", "") or "")).name,
        ),
    )


def clean_window_title(raw: str, *, names: tuple[str, ...] = ()) -> str:
    """``"⠋ Fix login test"`` → ``"Fix login test"``; a program/folder name → ""."""
    if _EXE_RE.match(raw.strip()):
        return ""
    text = " ".join(_LEAD_RE.sub("", raw.strip()).split())
    if not text or _EXE_RE.match(text):
        return ""
    lowered = text.casefold()
    if lowered in _GENERIC or lowered in {n.strip().casefold() for n in names if n.strip()}:
        return ""
    return text


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1].rstrip() + "…"


def _recorded(term: Any) -> str:
    """The title from the CLI's record on disk — cached, read off the loop."""
    if getattr(term, "computer_id", ""):
        return ""  # its record lives on the other computer
    agent = str(getattr(term, "agent", "") or "").strip().lower()
    if agent not in ("claude", "codex"):
        return ""
    handle = getattr(term, "resume", None)
    session_id = str(getattr(handle, "id", "") or "").strip() if handle is not None else ""
    if not session_id:
        return ""
    # Lifetime id, like the recap cache: two workspaces can each hold a "t5".
    pane = str(getattr(term, "history_id", "") or getattr(term, "key", "") or "")
    return _cached_title(_records, MAX_ENTRIES, (pane, session_id), agent, session_id, _home(term))


def session_title(agent: str, session_id: str, account_id: str = "") -> str:
    """The title a CLI gave the conversation ``session_id``, or "" when it has none.

    For a chat thread rather than a pane: the thread knows its CLI, the
    vendor's session id and the subscription seat it runs on, but has no
    terminal and therefore no window title. Same records; the read runs on a
    worker thread, so a list of many threads answers from the cache at once
    and picks a new title up on its next read.
    """
    try:
        agent = (agent or "").strip().lower()
        session_id = (session_id or "").strip()
        if agent not in ("claude", "codex") or not session_id:
            return ""
        if "/" in session_id or "\\" in session_id:
            return ""
        home = _account_home(agent, account_id)
        key = ("thread", session_id)
        title = _cached_title(
            _thread_records, MAX_THREAD_ENTRIES, key, agent, session_id, home, background=True
        )
        return _clip(title)
    except Exception as exc:  # noqa: BLE001 - a title must never break a session list
        logger.debug("Agentic IDE: CLI title of thread {} unreadable: {}", session_id, exc)
        return ""


def _cached_title(
    records: dict[tuple[str, str], _Record],
    limit: int,
    key: tuple[str, str],
    agent: str,
    session_id: str,
    home: Path | None,
    *,
    background: bool = False,
) -> str:
    """The title cached under ``key``, refreshed from the CLI's record on a slow clock."""
    entry = records.get(key)
    if entry is None:
        if len(records) >= limit:
            records.pop(next(iter(records)))
        entry = records[key] = _Record()
    now = time.monotonic()
    if entry.inflight or (entry.checked_at and now - entry.checked_at < RECHECK_S):
        return entry.title
    entry.inflight = True

    def _read() -> None:
        try:
            if agent == "claude":
                _read_claude(entry, session_id, home)
            else:
                entry.ai_title = _codex_name(session_id, home)
        except Exception as exc:  # noqa: BLE001 - a title must never break a state read
            logger.debug("Agentic IDE: title record of {} unreadable: {}", session_id, exc)
        finally:
            entry.checked_at = time.monotonic()
            entry.inflight = False

    if background:
        _reader.submit(_read)
        return entry.title
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (a worker thread, a test): nothing to keep free, so answer now.
        _read()
        return entry.title
    loop.run_in_executor(None, _read)
    return entry.title


def _read_claude(entry: _Record, session_id: str, home: Path | None) -> None:
    """Fold the records appended since the last read into ``entry``."""
    if entry.path is None:
        projects = agent_sessions._claude_home(home) / "projects"
        if not projects.is_dir():
            return
        entry.path = next(projects.glob(f"*/{session_id}.jsonl"), None)
        if entry.path is None:
            return
    try:
        size = entry.path.stat().st_size
    except OSError:
        entry.path = None  # moved or deleted; look it up again next time
        return
    if size < entry.offset:
        entry.offset = 0  # rewritten from scratch
    if size == entry.offset:
        return
    with entry.path.open("rb") as handle:
        handle.seek(entry.offset)
        chunk = handle.read(size - entry.offset)
    # Only whole lines: a record being appended right now is read next time.
    end = chunk.rfind(b"\n") + 1
    entry.offset += end
    for line in chunk[:end].splitlines():
        if b"-title" not in line:
            continue  # cheap pre-filter; almost every line is conversation
        row = _json(line)
        kind = row.get("type")
        if kind == "custom-title":
            entry.custom_title = str(row.get("customTitle") or "").strip()
        elif kind == "ai-title":
            entry.ai_title = str(row.get("aiTitle") or "").strip()


def _codex_name(session_id: str, home: Path | None) -> str:
    """The newest ``thread_name`` Codex recorded for ``session_id``."""
    path = agent_sessions._codex_home(home) / "session_index.jsonl"
    with _codex_lock:
        return _codex_name_locked(path, session_id)


def _codex_name_locked(path: Path, session_id: str) -> str:
    index = _codex_indexes.setdefault(str(path), _CodexIndex())
    try:
        size = path.stat().st_size
    except OSError:  # A missing or locked CLI index leaves the live terminal title in use.
        return ""
    if size < index.offset:
        index.offset, index.names = 0, {}
    if size > index.offset:
        with path.open("rb") as handle:
            handle.seek(index.offset)
            chunk = handle.read(size - index.offset)
        end = chunk.rfind(b"\n") + 1
        index.offset += end
        for line in chunk[:end].splitlines():
            row = _json(line)
            thread, name = str(row.get("id") or ""), str(row.get("thread_name") or "").strip()
            if thread and name:
                index.names[thread] = name
    return index.names.get(session_id, "")


def _json(line: bytes) -> dict[str, Any]:
    try:
        row = json.loads(line)
    except ValueError:  # An incomplete CLI record has no usable title; keep the existing title.
        return {}
    return row if isinstance(row, dict) else {}


def _home(term: Any) -> Path | None:
    """The config dir this pane's CLI keeps its history in, when redirected."""
    account = str(getattr(term, "account", "") or "")
    agent = str(getattr(term, "agent", "") or "")
    if not account:
        return None
    try:
        from jarvis import agent_accounts

        if agent not in agent_accounts.platforms():
            return None
        return agent_accounts.config_dir_for(agent, account)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the default home is the honest fallback
        logger.debug("Agentic IDE: account folder for {} is unknown: {}", agent, exc)
        return None


def _account_home(agent: str, account_id: str) -> Path | None:
    """The config dir a chat thread's CLI writes its record to.

    A thread with no seat of its own runs on the platform's active account,
    so that account's folder is where its record lands.
    """
    try:
        from jarvis import agent_accounts

        if agent not in agent_accounts.platforms():
            return None
        seat = account_id or agent_accounts.active_account(agent).id  # type: ignore[arg-type]
        return agent_accounts.config_dir_for(agent, seat)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the default home is the honest fallback
        logger.debug("Agentic IDE: account folder for thread on {} is unknown: {}", agent, exc)
        return None


def forget(key: str) -> None:
    """Drop what is remembered about one pane (its lifetime id) — it has been closed."""
    for cached in [item for item in _records if item[0] == key]:
        _records.pop(cached, None)


def reset_for_tests() -> None:
    _records.clear()
    _thread_records.clear()
    _codex_indexes.clear()


__all__ = [
    "RECHECK_S",
    "clean_window_title",
    "forget",
    "reset_for_tests",
    "session_title",
    "title_for",
    "window_title",
]
