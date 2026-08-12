"""What an Agentic-IDE user can get back after everything went away.

``Registry`` holds its workspaces in process memory, which means they last
exactly as long as the process does. This module is the thin layer that makes
that recoverable: a small JSON file naming every workspace that was open, each
pane's call-sign, which coding CLI ran in it, on which account, where it sat in
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
* **Closing does NOT withdraw the offer.** An earlier version cleared the
  snapshot when the last workspace was closed, reasoning that re-proposing
  something somebody just shut down is noise. That was wrong, and the maintainer
  said so plainly: closing for the day and picking the same folders up tomorrow
  is the main thing this is FOR. The only thing that discards a restore point is
  the user asking for it ("Start fresh" / ``DELETE /resume``).
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

from .agent_sessions import ResumeHandle, has_conversation

# Saves arrive from more than one thread (see `save`), and the last one has to
# be the one that lands rather than the one that happened to finish its rename
# first.
_WRITE_LOCK = threading.Lock()

# Bumped whenever the stored shape changes incompatibly. An unknown version
# reads as "nothing to resume": half-understanding a newer build's file would
# reopen a workspace with pieces missing, which is worse than offering nothing.
SCHEMA_VERSION = 2


@dataclass(slots=True)
class SnapshotTerminal:
    """One remembered pane."""

    key: str
    name: str
    agent: str
    # Opaque identity of the pane's on-demand prompt-history stream. Additive:
    # older snapshots leave it empty and receive a fresh id when restored.
    history_id: str = ""
    column: int = 0
    slot: int = 0
    resume: ResumeHandle | None = None
    prompts_sent: int = 0
    # Which subscription this pane ran on. Remembered because the resume handle
    # alone is not enough: an account keeps its conversations in its OWN config
    # directory, so reopening on a different one would search the wrong history
    # and silently start fresh. An absent value (an older snapshot) means "the
    # account that is active when it reopens" — the old behaviour exactly.
    account: str | None = None
    # True when that seat was chosen deliberately (wizard picker, explicit API
    # request, split of such a pane) rather than merely following the active
    # default. Splits of a restored pane consult it — see ``Terminal.account_pinned``.
    # Missing on older snapshots means False: an unpinned pane's splits follow
    # the switcher, which is the safe direction to fail in.
    account_pinned: bool = False
    # True only when the pane was observed actively working when its last live
    # state was checkpointed.  This is the evidence the Continue control needs:
    # a conversation existing does not mean its last turn was interrupted.
    # Missing on older snapshots deliberately means False (fail closed).
    continuation_needed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "agent": self.agent,
            "history_id": self.history_id,
            "column": self.column,
            "slot": self.slot,
            "resume": self.resume.to_dict() if self.resume else None,
            "prompts_sent": self.prompts_sent,
            "account": self.account,
            "account_pinned": self.account_pinned,
            "continuation_needed": self.continuation_needed,
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
            history_id=str(data.get("history_id") or "").strip(),
            column=_as_int(data.get("column")),
            slot=_as_int(data.get("slot")),
            resume=ResumeHandle.from_dict(data.get("resume")),
            prompts_sent=_as_int(data.get("prompts_sent")),
            account=(
                str(data.get("account")).strip() or None
                if isinstance(data.get("account"), str)
                else None
            ),
            account_pinned=data.get("account_pinned") is True,
            continuation_needed=data.get("continuation_needed") is True,
        )


def _account_home(agent: str, account_id: str | None) -> Path | None:
    """The config dir a remembered pane's conversation lives in.

    Kept local (rather than imported from ``session``) to keep this module free
    of the import cycle: ``session`` reads this store, not the other way round.
    """
    if not account_id:
        return None
    try:
        from jarvis import agent_accounts

        if agent not in agent_accounts.platforms():
            return None
        return agent_accounts.config_dir_for(agent, account_id)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the offer must never fail on this
        logger.debug("Agentic IDE: account folder for {} is unknown: {}", agent, exc)
        return None


@dataclass(slots=True)
class SnapshotWorkspace:
    """One remembered workspace: a folder and the panes that were in it."""

    session_id: str
    folder: str
    # Custom tab label. Empty for snapshots written before renaming existed.
    name: str = ""
    terminals: list[SnapshotTerminal] = field(default_factory=list)
    # The workspace's split tree (``layout_tree.to_dict`` form), or None for
    # snapshots written before the tree existed. Deliberately additive and
    # deliberately opaque here: this module never interprets it, and a pane's
    # legacy column/slot still rides along on every terminal — so an OLDER
    # build reading a newer file simply ignores this key and still places
    # every pane on the coarse grid, and a newer build reading an older file
    # rebuilds the tree from those hints.
    layout: dict[str, Any] | None = None
    # When this workspace was last recorded. Its own stamp rather than the
    # file's, because the file holds workspaces that closed at different times
    # and the merge in `save` has to know which record is the newer one.
    saved_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "folder": self.folder,
            "name": self.name,
            "saved_at": self.saved_at,
            "layout": self.layout,
            "terminals": [t.to_dict() for t in self.terminals],
        }

    @staticmethod
    def from_dict(data: Any) -> SnapshotWorkspace | None:
        if not isinstance(data, dict):
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
        raw_layout = data.get("layout")
        return SnapshotWorkspace(
            session_id=str(data.get("session_id") or ""),
            folder=folder,
            name=str(data.get("name") or "").strip(),
            terminals=terminals,
            layout=raw_layout if isinstance(raw_layout, dict) else None,
            saved_at=saved_at,
        )


# Two workspaces belong to the same save when their stamps are this close.
# `snapshot_now` gives every LIVE workspace one identical timestamp, so this is
# an exact-match test in practice; the tolerance only guards against a float
# surviving a JSON round trip a hair off.
_SAME_SAVE_EPSILON = 0.001


@dataclass(slots=True)
class Snapshot:
    """Everything that was open, ready to be offered back.

    All workspaces, not just whichever was on screen. The question this answers
    is "give me back what I had", and somebody running four folders side by side
    had four — handing back one and silently dropping three is the same kind of
    lie as promising a conversation that is not there.

    Restoring them all costs nothing on its own: a restored workspace starts no
    agent. Panes only launch when a pane connects, and only the workspace on
    screen has panes mounted, so five workspaces in the bar are five folders
    waiting, not five folders' worth of coding agents.

    **The file holds MORE than the last session** — see ``_merged_with_stored``:
    folders closed days ago stay remembered so a new workspace cannot erase
    them. Which of them was actually open last is therefore a question the
    consumer has to ask, not something the list answers by existing:
    :meth:`last_session` is that question.
    """

    saved_at: float
    workspaces: list[SnapshotWorkspace] = field(default_factory=list)
    # Which workspace was on screen, by its id at the time. Recorded rather than
    # implied by position, because the list is in TAB order — the bar must come
    # back in the arrangement it had, and the tab you were working in is not
    # necessarily the first one.
    active_session_id: str = ""

    @property
    def terminal_count(self) -> int:
        return sum(len(w.terminals) for w in self.workspaces)

    def last_session(self) -> list[SnapshotWorkspace]:
        """Only the workspaces that were OPEN when this file was last written.

        **The bug this exists for.** Remembering closed folders (so opening one
        new workspace could not wipe out the twelve panes you shut down an hour
        ago) turned the restore point into an archive of recently used folders
        ever opened — and "Resume all sessions" reopened all ten. Restarting
        therefore brought back Tuesday's folders beside today's, each one
        carrying the same call-signs out of the same pool, so the deduplicator
        renamed the collisions and the screen filled with "Alex", "Alex 2",
        "Alex 3". Reported as "it duplicates my terminals and restores stuff
        from days ago" — one root cause, both symptoms.

        Telling the two apart needs no extra bookkeeping: ``snapshot_now``
        stamps every live workspace with ONE instant, and a workspace that was
        merely remembered keeps the older stamp it had when it was last open.
        So "was open at the last save" is exactly "carries the newest stamp".

        A file whose workspaces carry no stamp at all (written before per
        workspace stamps existed, or hand-built in a test) cannot be split, so
        all of it counts as the last session — the old behaviour, unchanged.
        """
        newest = max((w.saved_at for w in self.workspaces), default=0.0)
        if newest <= 0.0:
            return list(self.workspaces)
        return [w for w in self.workspaces if newest - w.saved_at <= _SAME_SAVE_EPSILON]

    def is_from_last_session(self, workspace: SnapshotWorkspace) -> bool:
        """Was ``workspace`` open at the last save (rather than just remembered)?"""
        newest = max((w.saved_at for w in self.workspaces), default=0.0)
        if newest <= 0.0:
            return True
        return newest - workspace.saved_at <= _SAME_SAVE_EPSILON

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "saved_at": self.saved_at,
            "active_session_id": self.active_session_id,
            "workspaces": [w.to_dict() for w in self.workspaces],
        }

    @staticmethod
    def from_dict(data: Any) -> Snapshot | None:
        if not isinstance(data, dict):
            return None
        version = _as_int(data.get("version"))
        if version == 1:
            # Written before workspaces could be held open side by side: one
            # workspace, its fields at the top level. Lifted rather than
            # discarded, so an upgrade does not cost somebody their restore
            # point on the one restart where they would most want it.
            data = {
                "version": SCHEMA_VERSION,
                "saved_at": data.get("saved_at"),
                "workspaces": [
                    {
                        "session_id": data.get("session_id"),
                        "folder": data.get("folder"),
                        "terminals": data.get("terminals"),
                    }
                ],
            }
        elif version != SCHEMA_VERSION:
            return None

        raw = data.get("workspaces")
        workspaces = [
            parsed
            for parsed in (
                SnapshotWorkspace.from_dict(item) for item in (raw if isinstance(raw, list) else [])
            )
            if parsed is not None
        ]
        if not workspaces:
            return None
        try:
            saved_at = float(data.get("saved_at") or 0.0)
        except (TypeError, ValueError):
            saved_at = 0.0
        return Snapshot(
            saved_at=saved_at,
            workspaces=workspaces,
            active_session_id=str(data.get("active_session_id") or ""),
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
    if not snapshot.workspaces:
        # Nothing to come back to. Clearing beats storing an empty offer that
        # would render as a card with no panes in it.
        clear()
        return
    target = _store_path()
    snapshot = _merged_with_stored(snapshot)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid4().hex[:8]}")
    with _WRITE_LOCK:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning("Agentic IDE: could not persist the resume snapshot: {}", exc)
            # Never leave a half-written temp file behind to be found later.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # noqa: S110 - cleanup is best-effort
                pass


# How many CLOSED workspaces one restore point may remember in addition to all
# currently open ones. The history stays a screen rather than an archive, but
# this must never trim a live workspace merely because the user opened many.
MAX_REMEMBERED_WORKSPACES = 20


def _merged_with_stored(snapshot: Snapshot) -> Snapshot:
    """Fold the live workspaces into what is already remembered, keyed by folder.

    **The failure this exists for.** A save used to replace the file outright, so
    the restore point only ever held what happened to be open at that moment.
    Work twelve panes in one folder, close them, then open a single pane
    somewhere to check one thing — and the twelve were gone for good, with no way
    to notice until the offer came back holding one pane. Reported as "it only
    resumed one".

    So a save UPDATES rather than replaces: a folder that is open now overwrites
    its own record (the newest arrangement of that folder is the truth), and
    every other remembered folder is left exactly as it was. Only the user
    asking to start fresh throws any of it away.

    Ordered newest-first and trimmed, so the file cannot grow without limit and
    the offer leads with what was most recently worked in.
    """
    stamped = [w if w.saved_at else _restamp(w, snapshot.saved_at) for w in snapshot.workspaces]
    live_folders = {folder_key(w.folder) for w in stamped}
    try:
        stored = load()
    except Exception:  # noqa: BLE001 - a broken file must not block the write
        stored = None
    kept = (
        [w for w in stored.workspaces if folder_key(w.folder) not in live_folders]
        if stored is not None
        else []
    )
    # The open ones keep the bar's order — that arrangement is what comes back.
    # Folders remembered from earlier follow, most recently used first, and the
    # trim falls on the oldest of those rather than on anything open now.
    kept.sort(key=lambda w: w.saved_at, reverse=True)
    merged = [*stamped, *kept[:MAX_REMEMBERED_WORKSPACES]]
    return Snapshot(
        saved_at=snapshot.saved_at,
        workspaces=merged,
        active_session_id=snapshot.active_session_id,
    )


def _restamp(workspace: SnapshotWorkspace, when: float) -> SnapshotWorkspace:
    return SnapshotWorkspace(
        session_id=workspace.session_id,
        folder=workspace.folder,
        name=workspace.name,
        terminals=workspace.terminals,
        layout=workspace.layout,
        saved_at=when,
    )


def folder_key(folder: str) -> str:
    """Comparable form of a folder path — the same one twice must not be two.

    Public because the registry compares the same paths when it decides whether
    a remembered workspace is already open; two different notions of "the same
    folder" would be a second bug waiting for a symlinked checkout.
    """
    try:
        return os.path.normcase(str(Path(folder).expanduser().resolve()))
    except OSError:
        return os.path.normcase(folder)


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

    ``resumable`` asks the coding CLI's own history whether the conversation is
    really there, rather than trusting that a handle exists. Holding an id and
    having a conversation are different things — a pane opened and never given
    an instruction has the first and not the second — and promising twelve
    restored conversations that all turn out to be empty is exactly the lie this
    screen is meant to prevent.

    **The counts describe what resuming will actually do**, which is the LAST
    SESSION and not the whole file (see :meth:`Snapshot.last_session`). Every
    remembered workspace is still listed — a folder from Tuesday is worth
    seeing, and each carries ``in_last_session`` plus its own ``saved_at`` so
    the screen can separate "what you had open" from "also remembered" instead
    of presenting a ten-folder archive as one restart's worth of work.
    """
    if snapshot is None:
        return {
            "available": False,
            "saved_at": 0.0,
            "workspace_count": 0,
            "terminal_count": 0,
            "resumable_count": 0,
            "earlier_count": 0,
            "workspaces": [],
        }

    display = _display_names()
    workspaces: list[dict[str, Any]] = []
    for space in snapshot.workspaces:
        try:
            folder_exists = Path(space.folder).expanduser().is_dir()
        except OSError:
            folder_exists = False

        panes: list[dict[str, Any]] = []
        for term in space.terminals:
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
                    "resumable": agent_available
                    and has_conversation(
                        term.agent,
                        term.resume,
                        _account_home(term.agent, term.account),
                    ),
                    "prompts_sent": term.prompts_sent,
                }
            )

        workspaces.append(
            {
                "session_id": space.session_id,
                "folder": space.folder,
                "folder_name": Path(space.folder).name or space.folder,
                # The label the user gave the tab, empty when never renamed. Kept
                # beside the folder name rather than replacing it: somebody who
                # renamed a tab recognises the workspace by that label, and
                # somebody who did not still needs the folder.
                "name": space.name,
                "folder_exists": folder_exists,
                "available": folder_exists and any(p["available"] for p in panes),
                "resumable_count": sum(1 for p in panes if p["resumable"]),
                # When this workspace was last open, and whether that was the
                # session being offered back. Both belong on screen: a card that
                # says "last open 2 minutes ago" over a folder nobody has
                # touched since Tuesday is the misreport this feature was
                # accused of.
                "saved_at": space.saved_at,
                "in_last_session": snapshot.is_from_last_session(space),
                "terminals": panes,
            }
        )

    coming_back = [w for w in workspaces if w["in_last_session"]]
    return {
        "available": any(w["available"] for w in coming_back),
        "saved_at": snapshot.saved_at,
        "workspace_count": len(coming_back),
        "terminal_count": sum(len(w["terminals"]) for w in coming_back),
        "resumable_count": sum(w["resumable_count"] for w in coming_back),
        # Remembered, but NOT part of what resuming reopens.
        "earlier_count": len(workspaces) - len(coming_back),
        "workspaces": workspaces,
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


def snapshot_now(workspaces: list[SnapshotWorkspace], *, active_session_id: str = "") -> Snapshot:
    """A snapshot of every open workspace, stamped with the current time."""
    now = time.time()
    return Snapshot(
        saved_at=now,
        workspaces=[_restamp(w, now) for w in workspaces],
        active_session_id=active_session_id,
    )


__all__ = [
    "MAX_REMEMBERED_WORKSPACES",
    "SCHEMA_VERSION",
    "Snapshot",
    "SnapshotTerminal",
    "SnapshotWorkspace",
    "clear",
    "folder_key",
    "load",
    "offer",
    "save",
    "snapshot_now",
]
