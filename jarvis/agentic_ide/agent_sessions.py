"""How each coding CLI names, keeps and reopens one of its conversations.

A pane of the Agentic IDE is not the conversation it shows. The conversation
lives inside the agent's own CLI — Claude Code and Codex each keep their own
history on disk — and all this module does is hold the one string needed to
point back at it: a *resume handle*.

That indirection is what makes a workspace survivable. Closing the browser kills
every agent process (deliberately: an unwatched agent burns tokens invisibly),
and a restarted app forgets the workspace entirely. With a handle per pane, both
can be undone — the pane comes back and so does what it already knows.

The two CLIs do NOT work the same way, and the difference decides the design:

* **Claude Code** accepts ``--session-id <uuid>``, so the id can be *assigned*
  at launch. We mint one per pane and hand it over; resuming is then simply
  ``--resume <uuid>``. Unambiguous, no guessing, no filesystem involved.
* **Codex** has no such flag. Its id can only be *discovered* afterwards, from
  the rollout file it writes per session. ``codex resume --last`` looks like a
  shortcut and is a trap: it means "the newest session in this folder", so three
  Codex panes in one repository would all resume the SAME conversation and two
  users' work would silently vanish. Every pane must carry its own id.

Callers never branch on an agent name (AP-21). They ask this module whether a
handle can be minted, spent, or found, and an agent that answers "no" degrades
to a fresh start with an honest message — which is also what any coding CLI
added later gets for free.

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
    # argument is the set of ids other panes already hold — see `discover`.
    discover: Callable[[str, float, Collection[str]], ResumeHandle | None] | None


def _claude_launch() -> tuple[tuple[str, ...], ResumeHandle | None]:
    # Claude Code requires a valid UUID here and refuses anything else.
    session_id = str(uuid4())
    return (
        ("--session-id", session_id),
        ResumeHandle(kind="claude_session", id=session_id, captured_at=time.time()),
    )


def _codex_launch() -> tuple[tuple[str, ...], ResumeHandle | None]:
    # Nothing can be passed: the id is Codex's to choose and ours to find later.
    return ((), None)


_ADAPTERS: dict[str, _Adapter] = {
    "claude": _Adapter(
        kind="claude_session",
        launch=_claude_launch,
        resume=lambda session_id: ("--resume", session_id),
        discover=None,
    ),
    "codex": _Adapter(
        kind="codex_rollout",
        launch=_codex_launch,
        # `codex resume <id>` is a subcommand, so it comes before nothing else —
        # it is simply appended to the binary the pane already resolved.
        resume=lambda session_id: ("resume", session_id),
        # Late-bound on purpose: the discovery function is defined further down,
        # next to the file-format knowledge it needs.
        discover=lambda cwd, started, taken: _discover_codex(cwd, started, taken),
    ),
}


def can_resume(agent: str) -> bool:
    """True when this coding CLI can reopen one of its own conversations."""
    return agent in _ADAPTERS


def launch_extra(agent: str) -> tuple[tuple[str, ...], ResumeHandle | None]:
    """Extra argv for a fresh start, plus the handle it will be reachable by.

    A ``None`` handle does not mean "not resumable" — Codex simply cannot be
    told its id, so the handle arrives later through :func:`discover`.
    """
    adapter = _ADAPTERS.get(agent)
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
    adapter = _ADAPTERS.get(agent)
    if adapter is None or adapter.kind != handle.kind:
        return None
    return adapter.resume(handle.id)


def discover(
    agent: str,
    cwd: str,
    started_at: float,
    taken: Collection[str] = (),
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
    adapter = _ADAPTERS.get(agent)
    if adapter is None or adapter.discover is None:
        return None
    try:
        return adapter.discover(cwd, started_at, taken)
    except Exception as exc:  # noqa: BLE001 - discovery is a convenience
        logger.warning("Agentic IDE: {} session discovery failed: {}", agent, exc)
        return None


# --------------------------------------------------------------------- Codex
def _codex_home() -> Path:
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


def _discover_codex(
    cwd: str, started_at: float, taken: Collection[str] = ()
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
    for path in _candidate_files(_codex_home(), started_at):
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


__all__ = [
    "ResumeHandle",
    "can_resume",
    "discover",
    "launch_extra",
    "resume_argv",
]
