"""Carry a coding-CLI conversation from one subscription seat to another.

Every seat of a CLI (:mod:`jarvis.agent_accounts`) is its own config directory,
and the CLI files its conversations INSIDE that directory. So a conversation
started on seat A is invisible to the same CLI started on seat B: ``--resume
<id>`` there answers "no conversation found", and the agent that was supposed
to carry on after the switch starts from nothing. Switching seats mid-work is
only useful if the work comes along — this module is what makes it come along.

**Copy, never link.** The conversation files are copied into the target seat's
directory at the same relative path the CLI would have used there, so the CLI
finds them exactly as if it had written them itself. A link would tie two seats
to one file that both CLIs append to; a copy is owned by whoever resumes it.

**Newest copy wins, and only forward.** A conversation lives on exactly one
seat at a time — the one its pane runs on — so the copy with the newest
modification time IS the conversation, and every other copy is an older prefix
of it. Switching back and forth therefore always moves the latest state, and a
seat that already holds the newest copy is left untouched. ``shutil.copy2``
keeps the modification time, which is what makes a second carry a no-op.

**What travels, per CLI** (the layouts are measured, not assumed):

* Claude Code — ``projects/<folder>/<id>.jsonl``, the sibling ``<id>/`` folder
  (subagent transcripts, large tool results) and ``file-history/<id>/`` (what
  ``/rewind`` restores from).
* Codex — ``sessions/YYYY/MM/DD/rollout-…-<id>.jsonl``.
* Grok Build — ``sessions/<folder>/<id>/``.

Every other CLI keeps its history somewhere this module cannot carry, and gets
an honest ``False``: the pane then starts fresh on the new seat, which is what
it did before this module existed, and the caller says so.

Never raises. A conversation that cannot be carried is a fresh start, not a
pane that fails to open. Cross-platform: ``pathlib`` throughout, no OS branch.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

#: Bytes read from the END of a transcript when looking for a usage-limit stop.
#: The record that matters is the last turn, so it is always near the tail.
LIMIT_TAIL_BYTES = 256 * 1024

#: A limit stop older than this no longer explains why a pane is idle; the user
#: has had every chance to see it and act. Keeps a long-quiet pane from being
#: switched and nudged hours later for a limit that already reset.
LIMIT_STALE_S = 6 * 3600.0


@dataclass(frozen=True, slots=True)
class _Located:
    """One seat's copy of a conversation: its newest file and what belongs to it."""

    home: Path
    #: The file whose modification time dates this copy.
    anchor: Path
    #: (source path, path relative to the home) for every file or folder to copy.
    parts: tuple[tuple[Path, Path], ...]

    @property
    def mtime(self) -> float:
        try:
            return self.anchor.stat().st_mtime
        except OSError:
            return 0.0


def _safe_id(session_id: str) -> bool:
    return bool(session_id) and not any(sep in session_id for sep in ("/", "\\", "*", "?"))


# ------------------------------------------------------------------ layouts


def _claude_locate(home: Path, session_id: str, _captured_at: float) -> _Located | None:
    projects = home / "projects"
    if not projects.is_dir():
        return None
    transcript = next(projects.glob(f"*/{session_id}.jsonl"), None)
    if transcript is None or not transcript.is_file():
        return None
    parts: list[tuple[Path, Path]] = [(transcript, transcript.relative_to(home))]
    sibling = transcript.with_suffix("")
    if sibling.is_dir():
        parts.append((sibling, sibling.relative_to(home)))
    history = home / "file-history" / session_id
    if history.is_dir():
        parts.append((history, history.relative_to(home)))
    return _Located(home=home, anchor=transcript, parts=tuple(parts))


def _codex_day_folders(sessions: Path, captured_at: float) -> list[Path]:
    """Day folders worth searching; the newest-first full sweep when undated."""
    if captured_at <= 0:
        return []
    day = datetime.fromtimestamp(captured_at, tz=UTC).date()
    folders = []
    for offset in (0, -1, 1):
        shifted = day + timedelta(days=offset)
        folders.append(
            sessions / f"{shifted.year:04d}" / f"{shifted.month:02d}" / f"{shifted.day:02d}"
        )
    return folders


def _codex_locate(home: Path, session_id: str, captured_at: float) -> _Located | None:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return None
    found: Path | None = None
    for folder in _codex_day_folders(sessions, captured_at):
        found = next(folder.glob(f"rollout-*{session_id}*.jsonl"), None)
        if found is not None:
            break
    if found is None:
        found = next(sessions.glob(f"*/*/*/rollout-*{session_id}*.jsonl"), None)
    if found is None or not found.is_file():
        return None
    return _Located(home=home, anchor=found, parts=((found, found.relative_to(home)),))


def _grok_locate(home: Path, session_id: str, _captured_at: float) -> _Located | None:
    root = home / "sessions"
    if not root.is_dir():
        return None
    folder = next(root.glob(f"*/{session_id}"), None)
    if folder is None or not folder.is_dir():
        return None
    log = folder / "updates.jsonl"
    return _Located(
        home=home,
        anchor=log if log.is_file() else folder,
        parts=((folder, folder.relative_to(home)),),
    )


_LAYOUTS = {
    "claude_session": _claude_locate,
    "codex_rollout": _codex_locate,
    "grok_session": _grok_locate,
}


def can_carry(kind: str) -> bool:
    """Whether conversations of this resume kind can follow a seat switch."""
    return kind in _LAYOUTS


# -------------------------------------------------------------------- carry


def _same_dir(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _copy_file(source: Path, target: Path) -> None:
    """Atomic replace, so a CLI never opens a half-copied transcript."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{uuid4().hex[:8]}.carry")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _copy_part(source: Path, target: Path) -> None:
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_part(child, target / child.name)
        return
    try:
        if target.is_file():
            src, dst = source.stat(), target.stat()
            if dst.st_mtime >= src.st_mtime and dst.st_size == src.st_size:
                return
    except OSError:
        pass
    _copy_file(source, target)


def carry_conversation(
    kind: str,
    session_id: str,
    target_home: Path,
    homes: list[Path],
    *,
    captured_at: float = 0.0,
) -> bool:
    """Make the conversation ``session_id`` resumable from ``target_home``.

    ``homes`` are every seat directory of the same CLI; the newest copy among
    them (target included) is the conversation. Returns True when the target
    holds the newest copy afterwards — already did, or does now — and False
    when this kind cannot be carried, nothing holds the conversation, or the
    copy failed.
    """
    locate = _LAYOUTS.get(kind)
    if locate is None or not _safe_id(session_id):
        return False
    target_home = Path(target_home).expanduser()
    try:
        current = locate(target_home, session_id, captured_at)
        newest: _Located | None = None
        for home in homes:
            home = Path(home).expanduser()
            if _same_dir(home, target_home):
                continue
            found = locate(home, session_id, captured_at)
            if found is not None and (newest is None or found.mtime > newest.mtime):
                newest = found
        if newest is None:
            return current is not None
        if current is not None and current.mtime >= newest.mtime:
            return True
        for source, relative in newest.parts:
            _copy_part(source, target_home / relative)
    except OSError as exc:
        logger.warning(
            "Seat handoff: conversation {} could not be carried to {}: {}",
            session_id[:8],
            target_home,
            exc,
        )
        return False
    logger.info(
        "Seat handoff: carried conversation {} from {} to {}",
        session_id[:8],
        newest.home,
        target_home,
    )
    return True


# ------------------------------------------------------------- limit stops


def _ts(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    return None


def _tail_rows(path: Path, limit: int = LIMIT_TAIL_BYTES) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()
            raw_lines = handle.readlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for raw in raw_lines:
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _claude_row(row: dict[str, Any]) -> str:
    """``limit`` | ``user`` | ``progress`` | "" for one Claude Code record."""
    if row.get("isSidechain") or row.get("isMeta"):
        return ""
    kind = row.get("type")
    if kind == "assistant":
        status = row.get("apiErrorStatus")
        if row.get("isApiErrorMessage") and (row.get("error") == "rate_limit" or status == 429):
            return "limit"
        return "progress"
    if kind == "user" and not row.get("queueTranscriptOnly"):
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (
            isinstance(content, list)
            and content
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result" for block in content
            )
        ):
            return "progress"
        return "user"
    return ""


def _codex_row(row: dict[str, Any]) -> str:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return ""
    kind, ptype = row.get("type"), payload.get("type")
    if kind == "event_msg":
        if ptype == "user_message":
            return "user"
        if ptype == "error":
            info = str(payload.get("codex_error_info") or "").lower()
            message = str(payload.get("message") or "").lower()
            if "usage_limit" in info or "usage limit" in message:
                return "limit"
            return ""
        if ptype == "token_count":
            limits = payload.get("rate_limits")
            if isinstance(limits, dict) and limits.get("rate_limit_reached_type"):
                return "limit"
            return ""
        if ptype in {"agent_message", "task_started"}:
            return "progress"
    return ""


_ROW_READERS = {"claude_session": _claude_row, "codex_rollout": _codex_row}

#: The watch asks about every running pane every few seconds, and nearly every
#: time nothing changed. A transcript is re-read only when its size or
#: modification time moved; the located path is kept while it still exists.
#: Bounded by the number of panes, cleared wholesale past the cap.
_CACHE_CAP = 256
_LOCATED: dict[tuple[str, str, str], Path] = {}
_READ: dict[str, tuple[int, int, float | None]] = {}


def _cached_anchor(kind: str, session_id: str, home: Path, captured_at: float) -> Path | None:
    key = (kind, session_id, os.path.normcase(str(home)))
    known = _LOCATED.get(key)
    if known is not None and known.is_file():
        return known
    found = _LAYOUTS[kind](home, session_id, captured_at)
    if found is None:
        _LOCATED.pop(key, None)
        return None
    if len(_LOCATED) >= _CACHE_CAP:
        _LOCATED.clear()
    _LOCATED[key] = found.anchor
    return found.anchor


def _last_stop(kind: str, anchor: Path) -> float | None:
    """The unanswered limit stop at the end of ``anchor``, re-read only on change."""
    try:
        stat = anchor.stat()
    except OSError:
        return None
    key = os.path.normcase(str(anchor))
    cached = _READ.get(key)
    if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    reader = _ROW_READERS[kind]
    stop: float | None = None
    for row in _tail_rows(anchor):
        verdict = reader(row)
        if verdict == "limit":
            stop = _ts(row.get("timestamp")) or stop
        elif verdict in ("user", "progress"):
            # A message from the user, or an agent that carried on after the
            # stop (a retry that went through): the stop is answered.
            stop = None
    if len(_READ) >= _CACHE_CAP:
        _READ.clear()
    _READ[key] = (stat.st_mtime_ns, stat.st_size, stop)
    return stop


def can_detect_limit(kind: str) -> bool:
    """Whether a usage-limit stop is readable from this CLI's own transcript."""
    return kind in _ROW_READERS


def limit_stop(
    kind: str,
    session_id: str,
    home: Path,
    *,
    captured_at: float = 0.0,
    after: float = 0.0,
    now: float | None = None,
) -> float | None:
    """When the conversation's LAST turn was cut off by a plan limit, or None.

    Read from the CLI's own protocol records, never from screen text: Claude
    Code files a synthetic assistant message flagged ``isApiErrorMessage``
    with ``error: "rate_limit"`` (HTTP 429), Codex an ``error`` event naming the
    usage limit or a ``token_count`` whose rate limits say one was reached.

    "Last turn" is the point: a limit hit an hour ago that the user already
    typed past is history, so a stop only counts when nothing the user sent
    and nothing the agent did came after it. ``after`` ignores stops at or
    before a moment the caller already handled, which is what keeps a carried
    transcript — whose tail still holds the stop that caused the carry — from
    triggering a second switch on the new seat.
    """
    if kind not in _ROW_READERS or kind not in _LAYOUTS or not _safe_id(session_id):
        return None
    try:
        anchor = _cached_anchor(kind, session_id, Path(home).expanduser(), captured_at)
    except OSError:
        return None
    if anchor is None:
        return None
    stop = _last_stop(kind, anchor)
    moment = time.time() if now is None else now
    if stop is None or stop <= after or moment - stop > LIMIT_STALE_S:
        return None
    return stop


__all__ = [
    "LIMIT_STALE_S",
    "can_carry",
    "can_detect_limit",
    "carry_conversation",
    "limit_stop",
]
