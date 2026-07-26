"""The one workspace an Agentic-IDE user can get back.

``Registry`` holds the open workspace in process memory, which means it lasts
exactly as long as the process does. Close the browser and every agent is
stopped on purpose; restart the app and even the arrangement is gone. This
module is the thin layer that makes that recoverable: a small JSON file naming
the folder, every pane's call-sign, which coding CLI ran in it, where it sat in
the grid, and the handle that points back at its conversation.

Same shape and the same discipline as ``recents.py``, which already proved the
pattern in this feature: one small file under the per-user data directory (never
in the repo, never in ``jarvis.toml``), atomic writes (temp file + ``os.replace``)
and defensive reads. A truncated, hand-edited or newer-version file degrades to
"there is nothing to resume" rather than breaking the view that reads it.

Two rules worth stating outright:

* **A snapshot is an offer, never a promise.** What it names may not exist any
  more — the folder can be deleted, the coding CLI uninstalled, the
  conversation pruned by the CLI itself. :func:`offer` re-checks every entry
  against the machine as it is NOW, so the user is told which panes will really
  come back *before* clicking, instead of finding out by asking a resumed agent
  a follow-up question and getting a blank stare.
* **Closing a workspace deliberately withdraws it.** The offer is meant for the
  cases nobody chose — a closed window, a restarted app, a reboot. Re-offering
  something the user shut down on purpose is the kind of prompt people learn to
  dismiss without reading, which would cost the offer its meaning.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from .agent_sessions import ResumeHandle

# Saves arrive from more than one thread (see `save`), and the last one has to
# be the one that lands rather than the one that happened to finish its rename
# first.
_WRITE_LOCK = threading.Lock()

# Bumped whenever the stored shape changes incompatibly. An unknown version
# reads as "nothing to resume": half-understanding a newer build's file would
# reopen a workspace with pieces missing, which is worse than offering nothing.
SCHEMA_VERSION = 1


@dataclass(slots=True)
class SnapshotTerminal:
    """One remembered pane."""

    key: str
    name: str
    agent: str
    column: int = 0
    slot: int = 0
    resume: ResumeHandle | None = None
    prompts_sent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "agent": self.agent,
            "column": self.column,
            "slot": self.slot,
            "resume": self.resume.to_dict() if self.resume else None,
            "prompts_sent": self.prompts_sent,
        }

    @staticmethod
    def from_dict(data: Any) -> SnapshotTerminal | None:
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        agent = str(data.get("agent") or "").strip()
        if not name or not agent:
            return None
        return SnapshotTerminal(
            key=str(data.get("key") or "").strip() or name.lower(),
            name=name,
            agent=agent,
            column=_as_int(data.get("column")),
            slot=_as_int(data.get("slot")),
            resume=ResumeHandle.from_dict(data.get("resume")),
            prompts_sent=_as_int(data.get("prompts_sent")),
        )


@dataclass(slots=True)
class Snapshot:
    """A workspace as it stood, ready to be offered back."""

    session_id: str
    folder: str
    saved_at: float
    terminals: list[SnapshotTerminal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "folder": self.folder,
            "saved_at": self.saved_at,
            "terminals": [t.to_dict() for t in self.terminals],
        }

    @staticmethod
    def from_dict(data: Any) -> Snapshot | None:
        if not isinstance(data, dict):
            return None
        if _as_int(data.get("version")) != SCHEMA_VERSION:
            return None
        folder = str(data.get("folder") or "").strip()
        if not folder:
            return None
        raw = data.get("terminals")
        terminals = [
            parsed
            for parsed in (
                SnapshotTerminal.from_dict(item) for item in (raw if isinstance(raw, list) else [])
            )
            if parsed is not None
        ]
        if not terminals:
            # A workspace with no panes is not an offer, it is an empty screen.
            return None
        try:
            saved_at = float(data.get("saved_at") or 0.0)
        except (TypeError, ValueError):
            saved_at = 0.0
        return Snapshot(
            session_id=str(data.get("session_id") or ""),
            folder=folder,
            saved_at=saved_at,
            terminals=terminals,
        )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _store_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "last_session.json"


def save(snapshot: Snapshot) -> None:
    """Record ``snapshot`` as the workspace that can be resumed.

    Best-effort by design: a full disk or a locked file must never take down a
    workspace that is otherwise running perfectly. Failures are logged and
    swallowed, and the previous snapshot survives untouched — the temp file is
    written first and only then replaces the real one.

    **Two writes really do collide here.** A pane connecting saves the workspace,
    and a moment later the background lookup that finds a Codex conversation id
    saves it again — from a different thread. Sharing one temp filename made
    those two clobber each other's file, and on Windows the second ``os.replace``
    then failed outright with a sharing violation, silently losing exactly the
    conversation id this feature exists to keep. So each write gets its own temp
    name, and the lock keeps the last writer's file the one that lands.
    """
    if not snapshot.terminals:
        # Nothing to come back to. Clearing beats storing an empty offer that
        # would render as a card with no panes in it.
        clear()
        return
    target = _store_path()
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid4().hex[:8]}")
    with _WRITE_LOCK:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning(
                "Agentic IDE: could not persist the resume snapshot: {}", exc
            )
            # Never leave a half-written temp file behind to be found later.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # noqa: S110 - cleanup is best-effort
                pass


def load() -> Snapshot | None:
    """The stored workspace, or None when there is nothing usable to offer."""
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Agentic IDE: unreadable resume snapshot, ignoring it: {}", exc)
        return None
    return Snapshot.from_dict(data)


def clear() -> bool:
    """Withdraw the offer. True when something was actually removed."""
    path = _store_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Agentic IDE: could not clear the resume snapshot: {}", exc)
        return False


def offer(snapshot: Snapshot | None, *, installed: set[str]) -> dict[str, Any]:
    """The snapshot re-checked against this machine, as the user should see it.

    ``installed`` is the set of coding CLIs actually runnable here — which is a
    question about THIS machine and not about the snapshot. The same file
    carried to a fresh install, or read after somebody uninstalled Codex, must
    say so rather than promise a pane that will fail to start.

    Two flags per pane, and they mean different things:

    * ``available`` — the pane can be opened at all (its CLI is installed).
    * ``resumable`` — its CONVERSATION comes back, not just its call-sign.

    A pane that never received a prompt, or whose CLI cannot resume, is
    ``available`` but not ``resumable``: it returns with the right name in the
    right place and an empty history. Saying so up front is the whole point.
    """
    if snapshot is None:
        return {
            "available": False,
            "folder": "",
            "folder_name": "",
            "folder_exists": False,
            "saved_at": 0.0,
            "session_id": "",
            "resumable_count": 0,
            "terminals": [],
        }

    try:
        folder_exists = Path(snapshot.folder).expanduser().is_dir()
    except OSError:
        folder_exists = False

    display = _display_names()
    panes: list[dict[str, Any]] = []
    for term in snapshot.terminals:
        agent_available = term.agent in installed
        panes.append(
            {
                "key": term.key,
                "name": term.name,
                "agent": term.agent,
                "display_name": display.get(term.agent, term.agent),
                "column": term.column,
                "slot": term.slot,
                "available": agent_available,
                "resumable": agent_available and term.resume is not None,
                "prompts_sent": term.prompts_sent,
            }
        )

    return {
        "available": folder_exists and any(p["available"] for p in panes),
        "folder": snapshot.folder,
        "folder_name": Path(snapshot.folder).name or snapshot.folder,
        "folder_exists": folder_exists,
        "saved_at": snapshot.saved_at,
        "session_id": snapshot.session_id,
        "resumable_count": sum(1 for p in panes if p["resumable"]),
        "terminals": panes,
    }


def _display_names() -> dict[str, str]:
    """Human labels for the coding CLIs, or an empty map if unavailable.

    Imported lazily: ``session`` imports this module, so reaching back into it
    at module level would close a cycle. The fallback is the agent's own name,
    which is ugly but never wrong.
    """
    try:
        from .session import AGENT_DISPLAY

        return dict(AGENT_DISPLAY)
    except Exception:  # noqa: BLE001 - labels are cosmetic
        return {}


def snapshot_now(*, session_id: str, folder: str, terminals: list[SnapshotTerminal]) -> Snapshot:
    """A snapshot stamped with the current time."""
    return Snapshot(
        session_id=session_id,
        folder=folder,
        saved_at=time.time(),
        terminals=terminals,
    )


__all__ = [
    "SCHEMA_VERSION",
    "Snapshot",
    "SnapshotTerminal",
    "clear",
    "load",
    "offer",
    "save",
    "snapshot_now",
]
