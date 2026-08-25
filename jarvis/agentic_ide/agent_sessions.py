"""How each coding CLI names, keeps and reopens one of its conversations.

A pane of the Agentic IDE is not the conversation it shows. The conversation
lives inside the agent's own CLI — every coding CLI here (Claude Code, Codex,
OpenCode, Kimi Code, Grok Build, Antigravity, and the launch profiles over
them) keeps its own history on disk — and all this module does is hold the
one string needed to point back at it: a *resume handle*.

That indirection is what makes a workspace survivable. Closing the browser kills
every agent process (deliberately: an unwatched agent burns tokens invisibly),
and a restarted app forgets the workspace entirely. With a handle per pane, both
can be undone — the pane comes back and so does what it already knows.

The CLIs do NOT work the same way, and the difference decides the design. Two
shapes cover all of them — ASSIGNED at launch, or DISCOVERED afterwards:

* **Claude Code** accepts ``--session-id <uuid>``, so the id can be *assigned*
  at launch. We mint one per pane and hand it over; resuming is then simply
  ``--resume <uuid>``. Unambiguous, no guessing, no filesystem involved.
* **Codex** has no such flag. Its id can only be *discovered* afterwards, from
  the rollout file it writes per session. ``codex resume --last`` looks like a
  shortcut and is a trap: it means "the newest session in this folder", so three
  Codex panes in one repository would all resume the SAME conversation and two
  users' work would silently vanish. Every pane must carry its own id.
* **Grok Build** is the Claude Code shape (``--session-id`` at launch,
  ``--resume`` later); **OpenCode**, **Kimi Code** and **Antigravity** are
  the Codex shape (the id is theirs to choose — a database row, a session
  directory, a conversation UUID — and ours to find by folder and time).
  Each adapter below says which.

Callers never branch on an agent name (AP-21). They ask this module whether a
handle can be minted, spent, or found, and an agent that answers "no" degrades
to a fresh start with an honest message — which is also what any coding CLI
added later gets for free.

Every lookup takes an optional ``home`` — the config directory whose history is
meant. It exists because a pane can run any of several subscriptions of the same
CLI (:mod:`jarvis.agent_accounts`), and each account keeps a SEPARATE history in
its own directory. Without it, a pane on the second account would look for its
transcript in the first account's folder, find nothing, and start fresh with no
sign that anything was lost. ``None`` keeps the old behaviour: the environment
override if one is set, otherwise the CLI's conventional home.

Cross-platform: nothing here is OS-specific. ``CODEX_HOME`` is honoured when
set, otherwise the CLI's conventional home directory is used, and paths are
compared through ``os.path.normcase`` so a drive-letter difference on Windows
does not hide a pane's own session from it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from loguru import logger

# How far BEFORE a pane's recorded start a session file may claim to have been
# created and still be counted as that pane's. The CLI always stamps itself
# AFTER we read our clock, so this only absorbs clock jitter — and it must stay
# small, because panes opened in one batch are seconds apart and a generous
# window would let one pane claim its neighbour's conversation.
_SKEW_S = 1.5

# Upper bound on how many rollout files one discovery may open. A heavy Codex
# user accumulates thousands; reading the first line of every one of them would
# turn a pane restart into a disk crawl. The newest files are tried first, and a
# pane's own session is by construction among the newest.
_MAX_CANDIDATES = 400


@dataclass(frozen=True, slots=True)
class ResumeHandle:
    """The one string that points back at a coding CLI's own conversation.

    ``kind`` is not decoration: spending a Codex id on Claude Code would at best
    fail the launch and at worst reopen a stranger's conversation, so the kind is
    checked before the handle is ever turned into arguments.
    """

    kind: str
    id: str
    captured_at: float

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "captured_at": self.captured_at}

    @staticmethod
    def from_dict(data: Any) -> ResumeHandle | None:
        """Rebuild a handle from stored JSON, or None if it is not one.

        Defensive by design: this reads a file that survived a crash, an app
        upgrade, or a hand edit. Anything unrecognisable means "no handle",
        which costs a fresh conversation — never an exception on a path the user
        is waiting on.
        """
        if not isinstance(data, dict):
            return None
        kind = str(data.get("kind") or "").strip()
        session_id = str(data.get("id") or "").strip()
        if not kind or not session_id:
            return None
        try:
            captured_at = float(data.get("captured_at") or 0.0)
        except (TypeError, ValueError):
            captured_at = 0.0
        return ResumeHandle(kind=kind, id=session_id, captured_at=captured_at)


@dataclass(frozen=True, slots=True)
class _Adapter:
    """What one coding CLI can do with its own sessions."""

    kind: str
    # Extra argv for a FRESH start, plus the handle that start will produce.
    launch: Callable[[], tuple[tuple[str, ...], ResumeHandle | None]]
    # Extra argv that reopens the conversation behind an id.
    resume: Callable[[str], tuple[str, ...]]
    # Finds the id afterwards, for CLIs that cannot be told one. The third
    # argument is the set of ids other panes already hold — see `discover`; the
    # fourth is the config dir whose history to search (None = the default).
    discover: Callable[[str, float, Collection[str], Path | None], ResumeHandle | None] | None
    # Is there actually a conversation behind an id? See `has_conversation`.
    exists: Callable[[ResumeHandle, Path | None], bool]


def _claude_launch() -> tuple[tuple[str, ...], ResumeHandle | None]:
    # Claude Code requires a valid UUID here and refuses anything else.
    session_id = str(uuid4())
    return (
        ("--session-id", session_id),
        ResumeHandle(kind="claude_session", id=session_id, captured_at=time.time()),
    )


def _grok_launch() -> tuple[tuple[str, ...], ResumeHandle | None]:
    # Same shape as Claude Code: Grok accepts a client-supplied UUID at
    # launch (``--session-id``) and refuses anything that is not one. Minting
    # here means we never have to guess which session a pane created.
    session_id = str(uuid4())
    return (
        ("--session-id", session_id),
        ResumeHandle(kind="grok_session", id=session_id, captured_at=time.time()),
    )


def _codex_launch() -> tuple[tuple[str, ...], ResumeHandle | None]:
    # Nothing can be passed: the id is Codex's to choose and ours to find later.
    return ((), None)


def _discovered_launch() -> tuple[tuple[str, ...], ResumeHandle | None]:
    """Nothing can be passed: the id is the CLI's to choose and ours to find."""
    return ((), None)


_ADAPTERS: dict[str, _Adapter] = {
    "claude": _Adapter(
        kind="claude_session",
        launch=_claude_launch,
        resume=lambda session_id: ("--resume", session_id),
        discover=None,
        exists=lambda handle, home: _claude_conversation_exists(handle, home),
    ),
    "codex": _Adapter(
        kind="codex_rollout",
        launch=_codex_launch,
        # `codex resume <id>` is a subcommand, so it comes before nothing else —
        # it is simply appended to the binary the pane already resolved.
        resume=lambda session_id: ("resume", session_id),
        # Late-bound on purpose: the discovery function is defined further down,
        # next to the file-format knowledge it needs.
        discover=lambda cwd, started, taken, home: _discover_codex(cwd, started, taken, home),
        exists=lambda handle, home: _codex_conversation_exists(handle, home),
    ),
    "opencode": _Adapter(
        kind="opencode_session",
        launch=_discovered_launch,
        # Long form on purpose. The short flags of a TUI are the ones that get
        # reused for something else between releases; `--session` has to keep
        # meaning this or the CLI would break its own users.
        resume=lambda session_id: ("--session", session_id),
        discover=lambda cwd, started, taken, home: _discover_opencode(cwd, started, taken, home),
        exists=lambda handle, home: _opencode_conversation_exists(handle, home),
    ),
    "kimi": _Adapter(
        kind="kimi_session",
        launch=_discovered_launch,
        # Long form again, and here it is not a preference: the two shipping
        # generations disagree on the SHORT flag (-C versus -c), so a short one
        # would mean something different depending on which is installed.
        resume=lambda session_id: ("--session", session_id),
        discover=lambda cwd, started, taken, home: _discover_kimi(cwd, started, taken, home),
        exists=lambda handle, home: _kimi_conversation_exists(handle, home),
    ),
    "grok-build": _Adapter(
        kind="grok_session",
        launch=_grok_launch,
        resume=lambda session_id: ("--resume", session_id),
        discover=None,
        exists=lambda handle, home: _grok_conversation_exists(handle, home),
    ),
    "antigravity": _Adapter(
        kind="agy_session",
        launch=_discovered_launch,
        # ``agy --conversation <id>`` is the long form; there is no short flag.
        resume=lambda session_id: ("--conversation", session_id),
        discover=lambda cwd, started, taken, home: _discover_antigravity(
            cwd, started, taken, home
        ),
        exists=lambda handle, home: _agy_conversation_exists(handle, home),
    ),
}


def _adapter_for(agent: str) -> _Adapter | None:
    """The adapter that reopens ``agent``'s conversations.

    Goes through the registry so an entry may name ANOTHER entry's adapter. That
    is what lets a launch profile over an existing binary — the same CLI pointed
    at a different vendor — inherit resume exactly rather than owning a second
    copy of it that can drift.
    """
    from jarvis.workspace import agents as workspace_agents

    entry = workspace_agents.get_agent(agent)
    return _ADAPTERS.get(entry.adapter_key if entry is not None else agent)


def can_resume(agent: str) -> bool:
    """True when this coding CLI can reopen one of its own conversations."""
    return _adapter_for(agent) is not None


def launch_extra(agent: str) -> tuple[tuple[str, ...], ResumeHandle | None]:
    """Extra argv for a fresh start, plus the handle it will be reachable by.

    A ``None`` handle does not mean "not resumable" — most CLIs cannot be told
    their id, so the handle arrives later through :func:`discover`.
    """
    adapter = _adapter_for(agent)
    if adapter is None:
        return ((), None)
    return adapter.launch()


def resume_argv(agent: str, handle: ResumeHandle | None) -> tuple[str, ...] | None:
    """Extra argv that reopens ``handle``'s conversation, or None if it cannot.

    None is the answer for an unknown agent, a missing handle, and a handle of
    the wrong kind. Every one of those means the same thing to the caller: start
    fresh and say so.
    """
    if handle is None:
        return None
    adapter = _adapter_for(agent)
    if adapter is None or adapter.kind != handle.kind:
        return None
    return adapter.resume(handle.id)


def has_conversation(agent: str, handle: ResumeHandle | None, home: Path | None = None) -> bool:
    """Is there actually something behind this handle, or only a reserved id?

    **The distinction this whole module got wrong at first.** Being handed an id
    at launch does not create a conversation. Claude Code writes a session file
    only once the conversation HAS content, so a pane that was opened and never
    given an instruction leaves nothing behind — and resuming that id makes the
    CLI print "No conversation found" and exit. Measured on a real workspace:
    twelve panes opened, none prompted, twelve dead panes on the way back.

    So a handle is a pointer that has to be dereferenced before it is spent. The
    check is a filename lookup keyed on the id, which is a UUID and therefore
    unique across every project — that keeps it independent of how the CLI
    happens to name its per-project folders, and it costs a few milliseconds.

    Answers False for an unknown agent and for no handle at all. False always
    means the same thing to the caller: start fresh, and say so.
    """
    if handle is None:
        return False
    adapter = _adapter_for(agent)
    if adapter is None or adapter.kind != handle.kind:
        return False
    try:
        return adapter.exists(handle, home)
    except OSError as exc:
        # An unreadable history is not a present conversation. Starting fresh is
        # recoverable; launching into a missing one kills the pane.
        logger.debug("Agentic IDE: could not check {} history: {}", agent, exc)
        return False


def discover(
    agent: str,
    cwd: str,
    started_at: float,
    taken: Collection[str] = (),
    home: Path | None = None,
) -> ResumeHandle | None:
    """Find the session a pane started at ``started_at`` in ``cwd`` created.

    ``taken`` is what makes this safe for a whole workspace rather than a single
    pane: the ids the OTHER panes already hold. Time alone cannot separate panes
    opened in one batch — a coding CLI needs a second or two to write its
    session file, which is longer than the gap between five panes opening, so
    "the first session after I started" points several panes at the same
    conversation. An id that already belongs to a pane is simply not offered
    again, and the timestamps then only decide the order.

    Blocking filesystem work — callers run it in a worker thread. Returns None
    for every agent that does not need discovery (its handle already exists) and
    whenever nothing convincing is found.
    """
    adapter = _adapter_for(agent)
    if adapter is None or adapter.discover is None:
        return None
    try:
        return adapter.discover(cwd, started_at, taken, home)
    except Exception as exc:  # noqa: BLE001 - discovery is a convenience
        logger.warning("Agentic IDE: {} session discovery failed: {}", agent, exc)
        return None


# -------------------------------------------------------------- Claude Code
def _claude_home(override: Path | None = None) -> Path:
    """The config dir whose history to read: the account's, else the default."""
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def _claude_conversation_exists(handle: ResumeHandle, home: Path | None = None) -> bool:
    """True when Claude Code has a transcript filed under this session id.

    Searched by id across all project folders rather than by rebuilding the
    folder name from the working directory. That name is the CLI's own
    convention (separators and spaces folded into dashes) and could change
    without notice, while the id is a UUID and unique everywhere — so this stays
    correct even if the layout shifts, and on any OS.
    """
    session_id = handle.id
    if not session_id or "/" in session_id or "\\" in session_id:
        return False
    projects = _claude_home(home) / "projects"
    if not projects.is_dir():
        return False
    return next(projects.glob(f"*/{session_id}.jsonl"), None) is not None


# --------------------------------------------------------------------- Codex
def _codex_home(override: Path | None = None) -> Path:
    """The CODEX_HOME whose history to read: the account's, else the default."""
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _same_folder(left: str, right: str) -> bool:
    """Path equality that survives Windows drive-letter and separator drift."""
    try:
        a = os.path.normcase(str(Path(left).expanduser().resolve()))
        b = os.path.normcase(str(Path(right).expanduser().resolve()))
    except OSError:
        return False
    return a == b


def _parse_utc(value: Any) -> float | None:
    """ISO-8601 (with or without a trailing ``Z``) as a POSIX timestamp."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _is_top_level_session(payload: dict[str, Any]) -> bool:
    """True unless the record explicitly says it is a subagent's own thread.

    Fail-open on purpose. A filter that must recognise every future Codex record
    shape in order to accept one is a filter that eventually rejects everything —
    the failure mode that made a wake word go deaf (AP-27). Only an explicit
    subagent marker disqualifies a session.
    """
    if payload.get("thread_source") == "subagent":
        return False
    source = payload.get("source")
    return not (isinstance(source, dict) and "subagent" in source)


def _read_meta(path: Path) -> dict[str, Any] | None:
    """The ``session_meta`` record Codex writes as the FIRST line of a rollout.

    Only that first line is read: these files grow to megabytes as the
    conversation does, and everything identifying is in the header.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
    except OSError:
        return None
    if not first.strip():
        return None
    try:
        record = json.loads(first)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _candidate_files(root: Path, started_at: float) -> list[Path]:
    """Rollout files worth opening, newest first and bounded.

    Codex files them under ``sessions/YYYY/MM/DD``. The day folder is named in
    LOCAL time while the record inside is UTC, so a machine far from UTC can
    file a session under the neighbouring day — hence the one-day margin on
    either side rather than an exact date match.
    """
    sessions = root / "sessions"
    if not sessions.is_dir():
        return []
    started_day = datetime.fromtimestamp(started_at, tz=UTC).date()
    keep: list[Path] = []
    for day_dir in sessions.glob("*/*/*"):
        if not day_dir.is_dir():
            continue
        try:
            parts = day_dir.parts[-3:]
            day = datetime(int(parts[0]), int(parts[1]), int(parts[2])).date()
        except (ValueError, IndexError):
            continue
        if (day - started_day).days < -1:
            continue
        keep.extend(day_dir.glob("rollout-*.jsonl"))

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    keep.sort(key=_mtime, reverse=True)
    return keep[:_MAX_CANDIDATES]


def _codex_conversation_exists(handle: ResumeHandle, home: Path | None = None) -> bool:
    """True when Codex still has the rollout file this handle came from.

    The id is in the filename, and ``captured_at`` says roughly when it was
    written, so only the day folders around that moment are searched — a heavy
    user has thousands of rollouts and a full sweep would turn opening a pane
    into a disk crawl. The one-day margin is the same one discovery needs: the
    folder is named in local time and the record inside is UTC.
    """
    session_id = handle.id
    if not session_id or "/" in session_id or "\\" in session_id:
        return False
    sessions = _codex_home(home) / "sessions"
    if not sessions.is_dir():
        return False
    if handle.captured_at <= 0:
        # No hint when it was written — one bounded sweep rather than none.
        return next(sessions.glob(f"*/*/*/rollout-*{session_id}*.jsonl"), None) is not None
    day = datetime.fromtimestamp(handle.captured_at, tz=UTC).date()
    for offset in (0, -1, 1):
        folder = sessions / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if offset:
            shifted = day + timedelta(days=offset)
            folder = (
                sessions / f"{shifted.year:04d}" / f"{shifted.month:02d}" / f"{shifted.day:02d}"
            )
        if next(folder.glob(f"rollout-*{session_id}*.jsonl"), None) is not None:
            return True
    return False


def _discover_codex(
    cwd: str,
    started_at: float,
    taken: Collection[str] = (),
    home: Path | None = None,
) -> ResumeHandle | None:
    """The Codex session a pane in ``cwd`` started at ``started_at`` created.

    Matching is on *(folder, not already claimed, earliest moment at or after
    the pane launched)*. The claim check carries the correctness: Codex takes a
    beat to write its session file, so several panes opened together all see
    each other's files as "the first one after I started". Skipping ids that
    other panes already hold turns that ambiguity into a queue.

    The timestamp comes from INSIDE the file, never from its name. Codex stamps
    the filename in local time and the record in UTC; on a machine at UTC+10 the
    two are ten hours apart, so a filename-based match would place every session
    in the future and find nothing. That is the kind of bug that works perfectly
    on the machine it was written on and nowhere else.
    """
    claimed = {str(t) for t in taken}
    best: tuple[float, str] | None = None
    for path in _candidate_files(_codex_home(home), started_at):
        payload = _read_meta(path)
        if payload is None:
            continue
        folder = payload.get("cwd")
        if not isinstance(folder, str) or not _same_folder(folder, cwd):
            continue
        if not _is_top_level_session(payload):
            continue
        session_id = str(payload.get("id") or payload.get("session_id") or "").strip()
        if not session_id or session_id in claimed:
            continue
        stamp = _parse_utc(payload.get("timestamp"))
        if stamp is None:
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
        if stamp < started_at - _SKEW_S:
            continue
        if best is None or stamp < best[0]:
            best = (stamp, session_id)
    if best is None:
        return None
    return ResumeHandle(kind="codex_rollout", id=best[1], captured_at=best[0])


# ------------------------------------------------------------------ OpenCode
def _opencode_data_dir(override: Path | None = None) -> Path:
    """The directory holding this CLI's session database.

    Follows the XDG data convention it actually uses, on every OS — it does not
    switch to ``%APPDATA%`` on Windows, so hardcoding a Windows-shaped path here
    would look right and find nothing.
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw).expanduser() if raw else Path.home() / ".local" / "share"
    return base / "opencode"


def _opencode_db(home: Path | None = None) -> Path | None:
    """The session database, or ``None`` when this CLI has never run here.

    Globbed rather than named: the file name carries the release channel unless
    that is switched off, so a user on a non-stable channel has a differently
    named database and a hardcoded name would silently find no sessions at all —
    which reads as "nothing to resume" rather than as the lookup bug it is.
    """
    root = _opencode_data_dir(home)
    try:
        candidates = [p for p in root.glob("opencode*.db") if p.is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _opencode_rows(home: Path | None, query: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    """Run a read-only query against the session database.

    Read-only and never ``immutable``: the CLI may well be running and holding a
    write-ahead log, and declaring the file immutable would hand back a stale
    snapshot that is missing exactly the session just created — the one every
    caller here is looking for.
    """
    db = _opencode_db(home)
    if db is None:
        return []
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return []
    try:
        return list(con.execute(query, params))
    except sqlite3.Error as exc:
        # A schema this build does not recognise is not an error the user can
        # act on; it costs a fresh conversation, which is the same degradation
        # every other unreadable history gets.
        logger.debug("Agentic IDE: opencode session query failed: {}", exc)
        return []
    finally:
        con.close()


def _epoch_seconds(value: Any) -> float | None:
    """A stored timestamp as POSIX seconds, whether it was written in ms or s."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # Anything past ~5138 in seconds is milliseconds — no real timestamp lands
    # between the two ranges, so this cannot mis-read a genuine value.
    return number / 1000.0 if number > 1e11 else number


def _opencode_conversation_exists(handle: ResumeHandle, home: Path | None = None) -> bool:
    rows = _opencode_rows(home, "SELECT 1 FROM session WHERE id = ? LIMIT 1", (handle.id,))
    return bool(rows)


def _discover_opencode(
    cwd: str,
    started_at: float,
    taken: Collection[str] = (),
    home: Path | None = None,
) -> ResumeHandle | None:
    """The session a pane in ``cwd`` started at ``started_at`` created.

    Same matching rule as every other discovered CLI — folder, not already
    claimed, earliest moment at or after the pane launched — so several panes
    opened together queue rather than all claiming the first session.

    ``parent_id IS NULL`` is the one CLI-specific part: this agent files its
    subagents' threads in the same table, and a pane that resumed one of those
    would reopen a fragment of its own previous run instead of the conversation
    the user was having.
    """
    claimed = {str(t) for t in taken}
    # DESC, and that single word is the whole correctness of the cap. This
    # database is ONE global store for every project on the machine, so the
    # limit bites long before a single project has many sessions — and a plain
    # `LIMIT` sorts ASCENDING, which keeps the OLDEST rows and throws away the
    # pane's own, newest one. Every pane would then come back as a fresh
    # conversation, silently, once the machine had accumulated enough history.
    #
    # The bound is deliberately a row count and not a timestamp: the column's
    # unit is not guaranteed (see `_epoch_seconds`, which reads both seconds and
    # milliseconds), and a WHERE clause in the wrong unit filters everything out
    # just as silently as the wrong sort order did.
    #
    # `parent_id IS NULL` is the CLI-specific half: it files its subagents'
    # threads in the same table, and resuming one of those reopens a fragment of
    # the pane's own previous run instead of the conversation.
    rows = _opencode_rows(
        home,
        "SELECT id, directory, time_created FROM session "
        "WHERE parent_id IS NULL ORDER BY time_created DESC LIMIT ?",
        (_MAX_CANDIDATES,),
    )
    # Back to oldest-first, because the rule is "the EARLIEST session at or
    # after the pane started" — that is what lets several panes opened together
    # queue instead of all claiming the first one.
    for session_id, directory, created in reversed(rows):
        session_id = str(session_id or "").strip()
        if not session_id or session_id in claimed:
            continue
        if not isinstance(directory, str) or not _same_folder(directory, cwd):
            continue
        stamp = _epoch_seconds(created)
        if stamp is None or stamp < started_at - _SKEW_S:
            continue
        return ResumeHandle(kind="opencode_session", id=session_id, captured_at=stamp)
    return None


# ---------------------------------------------------------------- Kimi Code
def _kimi_root(home: Path | None = None) -> Path | None:
    """The data root of whichever generation of this CLI is installed.

    Two generations ship under one binary name and keep separate roots. Asking
    the registry which one is present — rather than picking one — is what stops
    a pane reading the other generation's history and reporting "nothing to
    resume" on a machine that has plenty.
    """
    if home is not None:
        return Path(home).expanduser()
    from jarvis.workspace import agents as workspace_agents

    generation = workspace_agents.generation_of("kimi")
    if generation == workspace_agents.KIMI_LEGACY:
        return Path.home() / ".kimi"
    if generation == workspace_agents.KIMI_CURRENT:
        return Path.home() / ".kimi-code"
    return None


def _kimi_folder_key(cwd: str) -> str | None:
    """The per-working-directory folder name the LEGACY generation derives.

    MEASURED against a live install rather than taken from documentation: the
    folder is the MD5 of the working directory exactly as the OS spells it
    (``C:\\Users\\...`` on Windows), which is why the native string is hashed and
    not a normalised or POSIX one.

    In that generation this is the ONLY link between a session and the folder
    it belongs to — the files inside record no working directory — so when the
    derived folder is absent there is simply nothing to match against. The
    current generation records the working directory inside each session (see
    :func:`_kimi_session_folder`) and names its buckets differently, so it is
    never matched through this key.
    """
    import hashlib

    try:
        native = str(Path(cwd).expanduser().resolve())
    except OSError:
        return None
    if not native:
        return None
    return hashlib.md5(native.encode("utf-8"), usedforsecurity=False).hexdigest()


# The current generation names its per-folder buckets like this. Anything else
# under ``sessions/`` is the legacy generation's MD5 bucket (or a stranger).
_KIMI_WORKSPACE_BUCKET_PREFIX = "wd_"

# What the current generation writes into a session's wire log the moment the
# session OPENS, before anybody has typed anything — MEASURED across eight
# opened-and-abandoned sessions on a live install, each exactly these records
# and nothing else. A log that holds any record beyond them has a conversation
# in it. Keyed on "anything beyond the setup prefix" rather than on the name of
# the first-turn record on purpose: a future release can rename its turn
# records and this still reads a real conversation as real.
_KIMI_SETUP_RECORDS = frozenset({"metadata", "config.update", "tools.set_active_tools"})


def _kimi_buckets_for(cwd: str, root: Path) -> list[Path]:
    """Every bucket under ``root/sessions`` that may hold ``cwd``'s sessions.

    Both generations at once, because a machine can carry both histories and
    the launch path does not know which one a given pane wrote into:

    * **Legacy** — one bucket named ``md5(cwd)``; it IS the folder scoping.
    * **Current** — buckets named ``wd_<slug>_<hash>``, mapped back to their
      folder by the CLI's own ``workspaces.json`` (``workspaces[key].root``).
      When that index is missing or unreadable every ``wd_`` bucket is offered
      and the per-session ``state.json`` does the filtering instead, so a
      damaged index costs a few more ``stat`` calls and never a conversation.
    """
    sessions = root / "sessions"
    if not sessions.is_dir():
        return []
    out: list[Path] = []
    key = _kimi_folder_key(cwd)
    if key is not None and (sessions / key).is_dir():
        out.append(sessions / key)

    mapped: list[Path] | None = None
    index = root / "workspaces.json"
    try:
        payload = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else None
    except (OSError, ValueError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("workspaces"), dict):
        mapped = []
        for bucket_name, entry in payload["workspaces"].items():
            if not isinstance(bucket_name, str) or not isinstance(entry, dict):
                continue
            bucket_root = entry.get("root")
            if isinstance(bucket_root, str) and _same_folder(bucket_root, cwd):
                bucket = sessions / bucket_name
                if bucket.is_dir():
                    mapped.append(bucket)
    if mapped is None:
        try:
            mapped = [
                child
                for child in sessions.iterdir()
                if child.is_dir() and child.name.startswith(_KIMI_WORKSPACE_BUCKET_PREFIX)
            ]
        except OSError:
            mapped = []
    out.extend(bucket for bucket in mapped if bucket not in out)
    return out


def _kimi_session_state(folder: Path) -> dict[str, Any] | None:
    """The current generation's ``state.json`` for a session, or None (legacy)."""
    state = folder / "state.json"
    try:
        if not state.is_file():
            return None
        payload = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _kimi_session_folder(folder: Path) -> str | None:
    """The working directory a session belongs to, when the session says so.

    Only the current generation records it (``state.json`` → ``workDir``). None
    means "the session does not say" — the legacy layout, where the bucket name
    already is the scoping — and NOT "no folder".
    """
    state = _kimi_session_state(folder)
    if state is None:
        return None
    work_dir = state.get("workDir")
    return work_dir if isinstance(work_dir, str) and work_dir else None


def _kimi_session_created(folder: Path) -> float | None:
    """When a session opened: its own ``createdAt`` when recorded, else mtime.

    The legacy generation records nothing, and there the directory's mtime is
    the moment it was created (the CLI never touches the directory afterwards —
    it writes INTO it). The current generation stamps ``createdAt`` in
    ``state.json``; that is read first because the directory's mtime there moves
    whenever a file inside is added.
    """
    state = _kimi_session_state(folder)
    if state is not None:
        stamp = _parse_utc(state.get("createdAt"))
        if stamp is not None:
            return stamp
    try:
        return folder.stat().st_mtime
    except OSError:
        return None


def _kimi_wire_has_turn(log: Path) -> bool:
    """Does this wire log hold anything beyond the records written at open?

    Reads line by line and stops at the first record outside
    :data:`_KIMI_SETUP_RECORDS`, so a long conversation costs the few setup
    lines and one more, never the whole file. A line that is not JSON is taken
    as content: the CLI writes only JSON here, so anything else was written by
    a turn that was cut off mid-line — which is still a conversation.
    """
    try:
        with log.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    return True
                kind = record.get("type") if isinstance(record, dict) else None
                if kind not in _KIMI_SETUP_RECORDS:
                    return True
    except OSError:
        return False
    return False


def _kimi_session_has_content(folder: Path) -> bool:
    """Is there a conversation in this session directory, or only its shell?

    Both generations create the directory the moment a session opens, so "the
    directory exists" is no evidence of anything. What counts as content
    differs per layout and is MEASURED, not assumed:

    * **Current** — ``agents/<name>/wire.jsonl`` exists from the start and is
      already tens of kilobytes (the system prompt is logged into it), so its
      SIZE says nothing; what says something is a record beyond the setup
      prefix (:func:`_kimi_wire_has_turn`).
    * **Legacy** — ``context.jsonl``/``wire.jsonl`` are created at open and
      stay EMPTY until the first turn, so any non-empty regular file is a
      conversation.
    """
    try:
        wires = sorted(folder.glob("agents/*/wire.jsonl"))
    except OSError:
        return False
    if wires:
        return any(_kimi_wire_has_turn(wire) for wire in wires)
    try:
        return any(child.is_file() and child.stat().st_size > 0 for child in folder.iterdir())
    except OSError:
        return False


def _kimi_conversation_exists(handle: ResumeHandle, home: Path | None = None) -> bool:
    """True when this CLI still holds a conversation behind the handle.

    Searched by id across every bucket, for the same reason the Claude check
    is: the id is unique everywhere (a UUID, prefixed ``session_`` in the
    current generation), while the bucket name is the CLI's own convention and
    differs between its two generations already.
    """
    session_id = handle.id
    if not session_id or "/" in session_id or "\\" in session_id:
        return False
    root = _kimi_root(home)
    if root is None:
        return False
    sessions = root / "sessions"
    if not sessions.is_dir():
        return False
    try:
        return any(
            child.is_dir() and _kimi_session_has_content(child)
            for child in sessions.glob(f"*/{session_id}")
        )
    except OSError:
        return False


def _discover_kimi(
    cwd: str,
    started_at: float,
    taken: Collection[str] = (),
    home: Path | None = None,
) -> ResumeHandle | None:
    """The session a pane in ``cwd`` started at ``started_at`` created.

    A session here is a DIRECTORY named after its id — in the current generation
    ``session_<uuid>``, which is also exactly what ``--session`` expects, so the
    directory name is the handle. The two generations scope a session to its
    folder differently (see :func:`_kimi_buckets_for`), and this walks whichever
    buckets can hold ``cwd``'s sessions, in either layout.

    A session without content is skipped: both generations create the directory
    the moment the session opens, so resuming an empty one reopens nothing and
    costs the pane its launch. A current-generation session whose ``state.json``
    names another folder is skipped too — that only happens when the bucket
    index was unreadable and every bucket is being offered.
    """
    root = _kimi_root(home)
    if root is None:
        return None
    claimed = {str(t) for t in taken}
    candidates: list[Path] = []
    for bucket in _kimi_buckets_for(cwd, root):
        try:
            candidates.extend(child for child in bucket.iterdir() if child.is_dir())
        except OSError:
            continue
    if not candidates:
        return None

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # Newest first BEFORE the cap. Session folders are named with random ids, so
    # directory order carries no recency at all — truncating it would drop the
    # pane's own folder for an arbitrary 400 others, and the pane would come
    # back as a fresh conversation with nothing to explain why. A pane's own
    # session is by construction among the newest.
    candidates.sort(key=_mtime, reverse=True)
    best: tuple[float, str] | None = None
    for child in candidates[:_MAX_CANDIDATES]:
        session_id = child.name
        if session_id in claimed:
            continue
        recorded_folder = _kimi_session_folder(child)
        if recorded_folder is not None and not _same_folder(recorded_folder, cwd):
            continue
        stamp = _kimi_session_created(child)
        if stamp is None or stamp < started_at - _SKEW_S:
            continue
        if not _kimi_session_has_content(child):
            continue
        if best is None or stamp < best[0]:
            best = (stamp, session_id)
    if best is None:
        return None
    return ResumeHandle(kind="kimi_session", id=best[1], captured_at=best[0])


# ---------------------------------------------------------------- Grok Build
def _grok_home(override: Path | None = None) -> Path:
    """The GROK_HOME whose history to read: the account's, else the default."""
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get("GROK_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".grok"


def _grok_conversation_exists(handle: ResumeHandle, home: Path | None = None) -> bool:
    """True when Grok Build has a transcript filed under this session id.

    Searched by id across every working-directory group rather than by
    reconstructing the encoded folder name. That name is the CLI's own
    convention (URL-encoded cwd, or a slug-plus-hash when the encoding is
    over 255 bytes) and could change without notice, while the id is a UUID
    and unique everywhere.

    The directory and its ``updates.jsonl`` exist from the moment the session
    OPENS — MEASURED on a live install: a pane opened and abandoned leaves a
    log holding the ``session_start`` hook run and nothing else, so neither
    the directory nor a non-empty log is evidence of a conversation. What is
    evidence is a user turn in that log (``"sessionUpdate":
    "user_message_chunk"``). Resuming a session without one reopens an empty
    conversation, which is indistinguishable from a dead pane.
    """
    session_id = handle.id
    if not session_id or "/" in session_id or "\\" in session_id:
        return False
    root = _grok_home(home) / "sessions"
    if not root.is_dir():
        return False
    found = next(root.glob(f"*/{session_id}"), None)
    if found is None or not found.is_dir():
        return False
    return _grok_log_has_user_turn(found / "updates.jsonl")


# The session-update record Grok Build writes when the USER says something.
# Every other record in `updates.jsonl` (hook runs, agent chunks, turn ends) can
# appear without a conversation ever having happened.
_GROK_USER_TURN_MARKER = b'"user_message_chunk"'
_AGY_USER_TURN_MARKER = b'"USER_INPUT"'


def _grok_log_has_user_turn(log: Path) -> bool:
    """Does this update log record at least one user turn?

    Read as bytes, line by line, stopping at the first hit: a long conversation
    has its first user turn within the first few lines, and an abandoned pane's
    log is one line long.
    """
    try:
        with log.open("rb") as handle:
            return any(_GROK_USER_TURN_MARKER in line for line in handle)
    except OSError:
        return False


# -------------------------------------------------------------- Antigravity
def _agy_cli_root(override: Path | None = None) -> Path:
    """The Antigravity CLI data root: ``<gemini-home>/antigravity-cli``.

    ``GEMINI_HOME`` (when set) IS the ``.gemini`` directory, not its parent —
    the same convention ``jarvis.google_cli.isolated_home`` uses. A test
    passes an explicit home and files live at ``<home>/.gemini/antigravity-cli``.
    """
    if override is not None:
        return Path(override).expanduser() / ".gemini" / "antigravity-cli"
    raw = os.environ.get("GEMINI_HOME", "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / ".gemini"
    return base / "antigravity-cli"


def _agy_conversation_db(root: Path, session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    path = root / "conversations" / f"{session_id}.db"
    return path if path.is_file() else None


def _agy_transcript(root: Path, session_id: str) -> Path:
    return (
        root
        / "brain"
        / session_id
        / ".system_generated"
        / "logs"
        / "transcript.jsonl"
    )


def _agy_log_has_user_turn(log: Path) -> bool:
    """Does this transcript record at least one user turn?

    Opening a pane already writes the conversation database; resuming one
    without a user turn reopens an empty TUI. The marker is the JSONL
    ``type`` Antigravity writes for an explicit user message (measured on
    agy 1.1.20).
    """
    try:
        with log.open("rb") as handle:
            return any(_AGY_USER_TURN_MARKER in line for line in handle)
    except OSError:
        return False


def _agy_conversation_exists(handle: ResumeHandle, home: Path | None = None) -> bool:
    """True when Antigravity has a conversation with at least one user turn."""
    session_id = handle.id
    root = _agy_cli_root(home)
    if _agy_conversation_db(root, session_id) is None:
        return False
    return _agy_log_has_user_turn(_agy_transcript(root, session_id))


def _agy_file_uri_path(uri: str) -> str:
    """``file:///C:/Users/...`` → a filesystem path, or empty when it is not one."""
    raw = (uri or "").strip()
    if not raw.lower().startswith("file:"):
        return ""
    parsed = urlparse(raw)
    path = unquote(parsed.path or "")
    if os.name == "nt" and path.startswith("/") and len(path) >= 3 and path[2] == ":":
        path = path[1:]
    return path


def _agy_cwd_matches(recorded: str, cwd: str) -> bool:
    if not recorded or not cwd:
        return False
    try:
        return os.path.normcase(os.path.normpath(recorded)) == os.path.normcase(
            os.path.normpath(cwd)
        )
    except (OSError, ValueError, TypeError):
        return False


def _agy_last_conversations(root: Path) -> dict[str, str]:
    path = root / "cache" / "last_conversations.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        cwd = str(key or "").strip()
        session_id = str(value or "").strip()
        if cwd and session_id:
            out[cwd] = session_id
    return out


def _agy_summaries_for_cwd(root: Path, cwd: str) -> list[tuple[float, str]]:
    """``(mtime, session_id)`` of conversations whose workspace is ``cwd``."""
    db = root / "conversation_summaries.db"
    if not db.is_file():
        return []
    import sqlite3

    try:
        uri = f"file:{db.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    except Exception:  # noqa: BLE001 - summaries are a convenience
        return []
    found: list[tuple[float, str]] = []
    try:
        for row in conn.execute(
            "SELECT conversation_id, workspace_uris, last_modified_time, "
            "last_user_input_step_index FROM conversation_summaries"
        ):
            session_id = str(row[0] or "").strip()
            if not session_id:
                continue
            try:
                uris = json.loads(row[1] or "[]")
            except (TypeError, ValueError):
                uris = []
            if not isinstance(uris, list):
                continue
            if not any(
                _agy_cwd_matches(_agy_file_uri_path(str(uri)), cwd) for uri in uris
            ):
                continue
            try:
                stamp = datetime.fromisoformat(str(row[2] or "").replace("Z", "+00:00"))
                mtime = stamp.timestamp()
            except (TypeError, ValueError):
                mtime = 0.0
            found.append((mtime, session_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Agentic IDE: antigravity summaries unreadable: {}", exc)
    finally:
        conn.close()
    return found


def _discover_antigravity(
    cwd: str,
    started_at: float,
    taken: Collection[str],
    home: Path | None,
) -> ResumeHandle | None:
    """Find the conversation a pane started at ``started_at`` in ``cwd`` created.

    Antigravity assigns the id; we cannot mint one. ``last_conversations.json``
    maps a folder to its newest conversation, and the summaries database
    records every conversation's workspace URI. Both are searched, newest
    first, and an id another pane already holds is skipped.
    """
    root = _agy_cli_root(home)
    taken_ids = {str(item) for item in taken}
    floor = started_at - _SKEW_S
    candidates: list[tuple[float, str]] = []

    for recorded_cwd, session_id in _agy_last_conversations(root).items():
        if session_id in taken_ids or not _agy_cwd_matches(recorded_cwd, cwd):
            continue
        db = _agy_conversation_db(root, session_id)
        if db is None:
            continue
        try:
            mtime = db.stat().st_mtime
        except OSError:
            continue
        if mtime < floor:
            continue
        candidates.append((mtime, session_id))

    for mtime, session_id in _agy_summaries_for_cwd(root, cwd):
        if session_id in taken_ids or mtime < floor:
            continue
        if _agy_conversation_db(root, session_id) is None:
            continue
        candidates.append((mtime, session_id))

    seen: set[str] = set()
    ordered: list[tuple[float, str]] = []
    for stamp, session_id in sorted(candidates, key=lambda item: item[0]):
        if session_id in seen or session_id in taken_ids:
            continue
        seen.add(session_id)
        if not _agy_log_has_user_turn(_agy_transcript(root, session_id)):
            continue
        ordered.append((stamp, session_id))
        if len(ordered) >= _MAX_CANDIDATES:
            break
    if not ordered:
        return None
    # The earliest unclaimed conversation after the pane started is this
    # pane's — later ones belong to panes opened in the same batch.
    _stamp, session_id = ordered[0]
    return ResumeHandle(kind="agy_session", id=session_id, captured_at=_stamp)


__all__ = [
    "ResumeHandle",
    "can_resume",
    "discover",
    "has_conversation",
    "launch_extra",
    "resume_argv",
]
