"""Projects and their chats — the library the new chat surface reads.

## What this replaces

The Agentic IDE used to open a *workspace*: a wizard asked for a folder, a
terminal count and an agent split, and what came back was a grid of panes that
lived until it was closed. There was no name for "the conversation I had with
Claude Code in this repo last Tuesday", so there was no way back to it either —
the pane was the conversation, and closing the pane ended the story.

This module gives those two things names and a place to live:

* a **project** is a folder somebody works in. It is the same folder the panes
  are rooted in; the difference is that it now outlives every session in it and
  carries a name, an order and an archive flag of its own.
* a **thread** is one conversation with one coding agent inside a project. It
  knows which CLI it belongs to, which model and subscription it was opened on,
  and — crucially — the handle that points back at the CLI's own stored
  conversation (see :mod:`.agent_sessions`).

A thread is therefore *metadata about* a conversation, never the conversation
itself. The messages stay where the coding CLI already writes them, which is
what keeps this file small enough to list instantly and what lets a chat be
opened months later without this module having stored a single word of it.

## Why one file per project

Threads live in ``threads/<project_id>.json`` rather than in one big file. Two
reasons, both practical:

* **Listing is per project.** Opening a project must not read the chats of the
  eleven others. The sidebar loads a project's list when the project is
  expanded, and that is one small file.
* **Damage is bounded.** A truncated or hand-edited file costs that project's
  chat list, not the whole library. Every read is defensive and degrades to an
  empty list rather than raising into the route that called it.

Writes are atomic (temp file + ``os.replace``) and serialized through one lock,
because saves arrive from request handlers on different threads and the last
one has to be the one that lands. Storage is the per-user data directory —
never the repo, never ``jarvis.toml``.

## Identity

A project's id is derived from its path (``os.path.normcase`` + SHA-1), not
minted at random. Opening the same folder twice is the same project even if the
store was lost, moved, or never written — which is what makes the library safe
to rebuild from recents on a fresh install. Thread ids are random, because two
conversations in one folder have nothing to distinguish them but their identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

#: Saves arrive from several request handlers at once; the last writer has to be
#: the one that lands rather than the one that finished its rename first.
_WRITE_LOCK = threading.RLock()

#: Bumped when the stored shape changes incompatibly. An unknown version reads
#: as "empty": half-understanding a newer build's file would show a library with
#: pieces missing, which is worse than showing none and rebuilding it.
SCHEMA_VERSION = 1

#: How long a thread title may be. Titles are generated from the first prompt,
#: and an unbounded one would push the sidebar's layout around.
TITLE_MAX = 120

#: How much of the last message the list carries. Enough for a subtitle line,
#: short enough that a project with 200 chats is still a small file.
PREVIEW_MAX = 160


@dataclass(slots=True)
class Project:
    """One folder somebody works in, and everything the sidebar shows about it."""

    id: str
    path: str
    name: str
    #: Accent colour for the project's mark. None = derived from the id, so the
    #: UI always has one and the user never has to pick.
    color: str | None = None
    pinned: bool = False
    archived: bool = False
    created_at: float = 0.0
    last_opened_at: float = 0.0
    #: Is this the holder for chats started WITHOUT choosing a folder?
    #:
    #: There is exactly one of these per install (see :func:`ensure_scratch`).
    #: A coding agent always runs somewhere, so it still has a path — the home
    #: directory — but it is not a project somebody picked, and the sidebar
    #: lists its chats on their own rather than among the real projects.
    scratch: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Thread:
    """One conversation with one coding agent inside a project."""

    id: str
    project_id: str
    #: Generated from the first prompt and renameable. Empty until the first
    #: prompt lands — the UI shows "New chat" for those rather than storing a
    #: placeholder that would then need translating.
    title: str = ""
    #: Which coding CLI this conversation belongs to (``claude``, ``codex``, …).
    #: Never branched on outside the adapter layer (AP-21) — carried so the
    #: right adapter can be asked, and so the row can wear the right mark.
    agent: str = ""
    #: Model and subscription the thread was opened on. Both optional: a CLI
    #: that does not let us choose either simply reports none.
    model: str | None = None
    account: str | None = None
    #: Points back at the CLI's own stored conversation. Serialized shape of
    #: :class:`.agent_sessions.ResumeHandle`; None until the CLI has one.
    resume: dict[str, Any] | None = None
    #: The live pane this thread is attached to right now, if any. Cleared when
    #: the pane closes — a thread outlives its terminal, that is the point.
    terminal: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    archived: bool = False
    #: Last thing said, for the sidebar's second line. Truncated on write.
    preview: str = ""
    #: How many prompts the user has sent. Cheap activity signal for the list;
    #: the real count lives in the CLI's own transcript.
    prompts_sent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #


def _root() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "library"


def _projects_path() -> Path:
    return _root() / "projects.json"


def _threads_path(project_id: str) -> Path:
    return _root() / "threads" / f"{project_id}.json"


def _read_json(path: Path) -> Any:
    """Parsed contents of ``path``, or None when it cannot be used.

    Every failure mode ends the same way on purpose — missing, unreadable,
    truncated, hand-edited — because the caller's answer to all of them is "act
    as if the library were empty". A missing file is the normal first-run case
    and is not worth a log line; anything else is.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Silent on purpose: no library file yet is the normal first-run state,
        # not a failure. Every other read error below is logged.
        return None
    except OSError as exc:
        logger.warning("Chat library: cannot read {}: {}", path.name, exc)
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        logger.warning("Chat library: unreadable {}, ignoring it: {}", path.name, exc)
        return None


def _write_json(path: Path, payload: Any) -> bool:
    """Replace ``path`` atomically. False (and a log line) when it did not land."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.warning("Chat library: could not persist {}: {}", path.name, exc)
        return False


def _envelope(kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, kind: items}


def _unwrap(data: Any, kind: str) -> list[dict[str, Any]]:
    """The item list out of a stored envelope, or [] when it cannot be trusted."""
    if not isinstance(data, dict):
        return []
    if int(data.get("version") or 0) != SCHEMA_VERSION:
        return []
    items = data.get(kind)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #


def project_id_for(path: str | Path) -> str:
    """Stable id for a folder — the same answer on every run, on every machine.

    ``normcase`` is what makes this correct on all three OSes rather than only
    the one it was written on: it lowercases on Windows, where two spellings of
    a path are the same folder, and leaves POSIX paths alone, where they are
    not.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        # Silent on purpose: an unresolvable path (gone, too long, a bad drive
        # letter) still deserves a stable id, and the unresolved spelling is a
        # perfectly good one. Nothing is lost, so there is nothing to report.
        resolved = Path(str(path))
    key = os.path.normcase(str(resolved))
    # Not a security boundary: this is a short, stable name for a local folder
    # path, and nothing downstream trusts it or checks it. `usedforsecurity`
    # says so to the reader and to the linter alike.
    digest = hashlib.sha1(key.encode("utf-8", "replace"), usedforsecurity=False)
    return digest.hexdigest()[:16]


def _load_projects() -> list[Project]:
    out: list[Project] = []
    for item in _unwrap(_read_json(_projects_path()), "projects"):
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        pid = str(item.get("id") or "") or project_id_for(path)
        out.append(
            Project(
                id=pid,
                path=path,
                name=str(item.get("name") or Path(path).name or path),
                color=(str(item["color"]) if item.get("color") else None),
                pinned=bool(item.get("pinned")),
                archived=bool(item.get("archived")),
                created_at=float(item.get("created_at") or 0.0),
                last_opened_at=float(item.get("last_opened_at") or 0.0),
                # Absent in every file written before scratch existed, which
                # reads as False — the correct answer for a folder somebody
                # opened on purpose.
                scratch=bool(item.get("scratch")),
            )
        )
    return out


def _save_projects(projects: list[Project]) -> bool:
    return _write_json(_projects_path(), _envelope("projects", [p.to_dict() for p in projects]))


def list_projects(*, include_archived: bool = False) -> list[Project]:
    """Every project, pinned first, then most recently opened.

    Folders that no longer exist are kept rather than dropped. An unplugged
    external drive or a repo on a network share is a normal, temporary state,
    and silently deleting somebody's chat history because a mount was late is
    not a trade this feature gets to make. The route reports whether the folder
    is reachable and the UI says so; nothing is removed but by request.
    """
    with _WRITE_LOCK:
        projects = _load_projects()
    if not include_archived:
        projects = [p for p in projects if not p.archived]
    projects.sort(key=lambda p: (not p.pinned, -p.last_opened_at, p.name.lower()))
    return projects


def get_project(project_id: str) -> Project | None:
    with _WRITE_LOCK:
        for project in _load_projects():
            if project.id == project_id:
                return project
    return None


def ensure_project(path: str | Path, *, name: str | None = None) -> Project:
    """The project for ``path``, created on first sight.

    Idempotent by folder, not by call: opening the same repo for the tenth time
    returns the same project with the same id, name and colour, and only moves
    its ``last_opened_at``. That is what lets every entry point — the sidebar,
    a dropped folder, a voice command, the CLI — reach for this without first
    checking whether the project already exists.
    """
    resolved = str(Path(path).expanduser())
    pid = project_id_for(resolved)
    now = time.time()
    with _WRITE_LOCK:
        projects = _load_projects()
        for project in projects:
            if project.id == pid:
                project.last_opened_at = now
                if name:
                    project.name = name[:TITLE_MAX]
                _save_projects(projects)
                return project
        created = Project(
            id=pid,
            path=resolved,
            name=(name or Path(resolved).name or resolved)[:TITLE_MAX],
            created_at=now,
            last_opened_at=now,
        )
        projects.append(created)
        _save_projects(projects)
        return created


#: What the one project-less holder is called on screen. English because every
#: artifact is (CLAUDE.md §1); it is a name, not a translated label.
SCRATCH_NAME = "Sessions"


def scratch_root() -> Path:
    """Where a chat with no chosen folder runs.

    The home directory, because a coding agent always runs SOMEWHERE and this
    is the one folder that exists on every account, on every OS, without asking
    anybody anything. Deliberately not the repo, a temp directory, or the
    process's current folder: the first is not the user's, the second is
    erased under running agents, and the third is wherever the app happened to
    be started from.
    """
    return Path.home()


def ensure_scratch() -> Project:
    """The single holder for chats started without choosing a folder.

    Idempotent like :func:`ensure_project` and derived the same way, so it
    survives a lost store. Existing rows are upgraded in place rather than
    duplicated: an install that opened its home folder as a normal project
    before this existed keeps that project and simply gains the flag.
    """
    resolved = str(scratch_root())
    pid = project_id_for(resolved)
    now = time.time()
    with _WRITE_LOCK:
        projects = _load_projects()
        for project in projects:
            if project.id == pid:
                project.last_opened_at = now
                if not project.scratch:
                    project.scratch = True
                    project.name = SCRATCH_NAME
                _save_projects(projects)
                return project
        created = Project(
            id=pid,
            path=resolved,
            name=SCRATCH_NAME,
            created_at=now,
            last_opened_at=now,
            scratch=True,
        )
        projects.append(created)
        _save_projects(projects)
        return created


def update_project(
    project_id: str,
    *,
    name: str | None = None,
    color: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
) -> Project | None:
    """Change one project. None when there is no such project."""
    with _WRITE_LOCK:
        projects = _load_projects()
        for project in projects:
            if project.id != project_id:
                continue
            if name is not None:
                cleaned = name.strip()[:TITLE_MAX]
                if cleaned:
                    project.name = cleaned
            if color is not None:
                project.color = color.strip() or None
            if pinned is not None:
                project.pinned = pinned
            if archived is not None:
                project.archived = archived
            _save_projects(projects)
            return project
    return None


def touch_project(project_id: str) -> None:
    """Mark a project as just used. Best-effort — ordering is not worth an error."""
    with _WRITE_LOCK:
        projects = _load_projects()
        for project in projects:
            if project.id == project_id:
                project.last_opened_at = time.time()
                _save_projects(projects)
                return


def delete_project(project_id: str) -> bool:
    """Forget a project AND every chat in it. True when something was removed.

    Deliberately destructive and deliberately complete: a project whose entry is
    gone but whose thread file survives would come back the next time the folder
    is opened, carrying chats the user believed they had deleted.
    """
    with _WRITE_LOCK:
        projects = _load_projects()
        kept = [p for p in projects if p.id != project_id]
        if len(kept) == len(projects):
            return False
        _save_projects(kept)
        try:
            _threads_path(project_id).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Chat library: could not drop chats of {}: {}", project_id, exc)
        return True


# --------------------------------------------------------------------------- #
# threads
# --------------------------------------------------------------------------- #


def _load_threads(project_id: str) -> list[Thread]:
    out: list[Thread] = []
    for item in _unwrap(_read_json(_threads_path(project_id)), "threads"):
        tid = str(item.get("id") or "").strip()
        if not tid:
            continue
        resume = item.get("resume")
        out.append(
            Thread(
                id=tid,
                project_id=project_id,
                title=str(item.get("title") or "")[:TITLE_MAX],
                agent=str(item.get("agent") or ""),
                model=(str(item["model"]) if item.get("model") else None),
                account=(str(item["account"]) if item.get("account") else None),
                resume=resume if isinstance(resume, dict) else None,
                terminal=(str(item["terminal"]) if item.get("terminal") else None),
                created_at=float(item.get("created_at") or 0.0),
                updated_at=float(item.get("updated_at") or 0.0),
                archived=bool(item.get("archived")),
                preview=str(item.get("preview") or "")[:PREVIEW_MAX],
                prompts_sent=int(item.get("prompts_sent") or 0),
            )
        )
    return out


def _save_threads(project_id: str, threads: list[Thread]) -> bool:
    return _write_json(
        _threads_path(project_id), _envelope("threads", [t.to_dict() for t in threads])
    )


def list_threads(project_id: str, *, include_archived: bool = False) -> list[Thread]:
    """One project's chats, most recently touched first."""
    with _WRITE_LOCK:
        threads = _load_threads(project_id)
    if not include_archived:
        threads = [t for t in threads if not t.archived]
    threads.sort(key=lambda t: (-t.updated_at, -t.created_at))
    return threads


def get_thread(project_id: str, thread_id: str) -> Thread | None:
    with _WRITE_LOCK:
        for thread in _load_threads(project_id):
            if thread.id == thread_id:
                return thread
    return None


def find_thread(thread_id: str) -> Thread | None:
    """A chat by id alone, without knowing its project.

    Costs one small read per project, which is why the routes take the project
    id where they have it. It exists for the callers that genuinely do not — a
    notification carrying only a thread id, a resumed pane rebinding itself.
    """
    with _WRITE_LOCK:
        for project in _load_projects():
            for thread in _load_threads(project.id):
                if thread.id == thread_id:
                    return thread
    return None


def create_thread(
    project_id: str,
    *,
    agent: str,
    model: str | None = None,
    account: str | None = None,
    title: str = "",
) -> Thread:
    """Start a new chat in a project.

    Created empty and untitled: a chat earns its title from the first prompt,
    and one created by mistake is then visibly a blank rather than a plausible
    entry in the list.
    """
    now = time.time()
    thread = Thread(
        id=uuid4().hex,
        project_id=project_id,
        title=title.strip()[:TITLE_MAX],
        agent=agent,
        model=model,
        account=account,
        created_at=now,
        updated_at=now,
    )
    with _WRITE_LOCK:
        threads = _load_threads(project_id)
        threads.append(thread)
        _save_threads(project_id, threads)
    return thread


def update_thread(project_id: str, thread_id: str, /, **changes: Any) -> Thread | None:
    """Change one chat. Unknown keys are ignored; None when there is no such chat.

    ``**changes`` rather than a wall of keyword arguments because the callers
    are genuinely varied — a rename from the UI, a resume handle from the pane
    supervisor, a preview and a counter from the prompt path — and each touches
    a different two or three fields. The allowlist below is what keeps that
    from becoming a way to write arbitrary keys into the store.

    The two ids are positional-ONLY for the same reason. A caller forwarding a
    dict of fields straight through would otherwise hit ``TypeError: got
    multiple values for argument 'project_id'`` the moment that dict happened to
    carry an ``id`` or ``project_id`` key — which is exactly the case the
    allowlist exists to absorb quietly.
    """
    allowed = {
        "title",
        "agent",
        "model",
        "account",
        "resume",
        "terminal",
        "archived",
        "preview",
        "prompts_sent",
    }
    with _WRITE_LOCK:
        threads = _load_threads(project_id)
        for thread in threads:
            if thread.id != thread_id:
                continue
            for key, value in changes.items():
                if key not in allowed:
                    continue
                if key == "title" and isinstance(value, str):
                    value = value.strip()[:TITLE_MAX]
                elif key == "preview" and isinstance(value, str):
                    value = " ".join(value.split())[:PREVIEW_MAX]
                setattr(thread, key, value)
            thread.updated_at = time.time()
            _save_threads(project_id, threads)
            return thread
    return None


def delete_thread(project_id: str, thread_id: str) -> bool:
    """Forget one chat. True when something was removed.

    Only the library entry goes. The CLI's own conversation on disk is left
    alone — it is that CLI's data, written before Jarvis was involved and
    readable without it, and deleting another program's files on a user's
    behalf is not this feature's call.
    """
    with _WRITE_LOCK:
        threads = _load_threads(project_id)
        kept = [t for t in threads if t.id != thread_id]
        if len(kept) == len(threads):
            return False
        _save_threads(project_id, kept)
        return True


def title_from_prompt(prompt: str) -> str:
    """A chat title out of the first thing the user said.

    The first line, trimmed to a readable length on a word boundary. Deliberately
    not a model call: this runs on the path the user is waiting through, and a
    title is not worth a round trip. A chat can always be renamed.
    """
    first = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    collapsed = " ".join(first.split())
    if len(collapsed) <= 48:
        return collapsed[:TITLE_MAX]
    cut = collapsed[:48]
    space = cut.rfind(" ")
    return (cut[:space] if space > 24 else cut).rstrip(" ,.;:—-") + "…"
