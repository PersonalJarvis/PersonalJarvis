"""Recently opened Agentic-IDE workspaces.

Opening the same project twice should not mean browsing to it twice. Each started
session records where it ran and how it was laid out, so the next visit is one
click — and the recorded terminal count and agent split are replayed, because
someone who opened a repo with "3 × Claude Code" almost always wants that again.

Storage is a single small JSON file under the per-user data directory (never in
the repo, never in ``jarvis.toml``). Writes are atomic (temp file + ``os.replace``)
and every read is defensive: a truncated or hand-edited file degrades to "no
recents" instead of breaking the wizard. Entries that no longer exist on disk are
dropped on read, so a deleted or unplugged folder disappears by itself rather
than failing at launch.

**Only folders a person actually chose belong here.** Scratch directories are
filtered out on the way in AND on the way out, because this list is the wizard's
front page: a user who sees six folders they never opened cannot tell which of
them they are supposed to trust. That is not hypothetical — an automated run
against the real store once pushed the one genuine entry to the bottom of the
list behind its own throwaway folders, so the filter runs on read as well and
heals a store that was already polluted.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

MAX_RECENTS = 8


def _temp_roots() -> list[Path]:
    """Every directory this machine treats as scratch space.

    ``gettempdir()`` is the authoritative answer on all three OSes, but the
    environment variables are checked too: a session can be started with a
    different ``TMPDIR``/``TEMP`` than the one the running process resolved, and
    a folder under either of them is throwaway all the same.
    """
    seen: list[Path] = []
    candidates = [tempfile.gettempdir(), *(os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP"))]
    for raw in candidates:
        if not raw:
            continue
        try:
            resolved = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def is_throwaway(path: str | Path) -> bool:
    """True when ``path`` lives in scratch space and must not be remembered.

    A folder under the system temp directory is by definition disposable — a
    test fixture, an unpacked archive, an editor's scratch checkout. Opening one
    still works; it simply does not earn a place in the history, because the
    folder is usually gone before the user looks at the list again.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    for root in _temp_roots():
        if resolved == root:
            return True
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


@dataclass(slots=True)
class RecentWorkspace:
    """One previously opened workspace."""

    path: str
    name: str
    terminals: int = 1
    # agent name -> how many terminals ran it, e.g. {"claude": 2, "codex": 1}
    agents: dict[str, int] = field(default_factory=dict)
    last_used: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _store_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "recents.json"


def _load_raw() -> list[dict[str, Any]]:
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Agentic IDE: unreadable recents file, ignoring it: {}", exc)
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def load(*, verify_exists: bool = True) -> list[RecentWorkspace]:
    """Recent workspaces, newest first. Vanished folders are filtered out."""
    out: list[RecentWorkspace] = []
    for item in _load_raw():
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        # Filtered on read as well as on write, so a store polluted before this
        # rule existed cleans itself up the next time anything is written.
        if is_throwaway(path):
            continue
        if verify_exists:
            try:
                if not Path(path).is_dir():
                    continue
            except OSError:
                continue
        agents = item.get("agents")
        out.append(
            RecentWorkspace(
                path=path,
                name=str(item.get("name") or Path(path).name or path),
                terminals=max(1, int(item.get("terminals") or 1)),
                agents={
                    str(k): int(v)
                    for k, v in (agents.items() if isinstance(agents, dict) else [])
                },
                last_used=float(item.get("last_used") or 0.0),
            )
        )
    out.sort(key=lambda r: r.last_used, reverse=True)
    return out[:MAX_RECENTS]


def remember(path: str, *, terminals: int, agents: dict[str, int]) -> None:
    """Record (or refresh) one workspace as the most recent.

    Scratch folders are declined outright — see :func:`is_throwaway`. This is the
    fail-closed half of the rule: the caller does not have to know which folders
    deserve a place in the history, and a future caller that forgets cannot
    pollute the list.

    Best-effort: a failure to persist must never stop a session from opening, so
    problems are logged and swallowed.
    """
    if is_throwaway(path):
        logger.debug("Agentic IDE: not remembering throwaway folder {}", path)
        return
    resolved = str(Path(path).expanduser())
    entry = RecentWorkspace(
        path=resolved,
        name=Path(resolved).name or resolved,
        terminals=max(1, terminals),
        agents=dict(agents),
        last_used=time.time(),
    )
    kept = [r for r in load(verify_exists=False) if r.path.lower() != resolved.lower()]
    payload = [entry.to_dict(), *(r.to_dict() for r in kept)][:MAX_RECENTS]

    target = _store_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("Agentic IDE: could not persist recents: {}", exc)


def forget(path: str) -> bool:
    """Remove one entry. Returns True when something was removed."""
    resolved = str(Path(path).expanduser()).lower()
    current = load(verify_exists=False)
    kept = [r for r in current if r.path.lower() != resolved]
    if len(kept) == len(current):
        return False
    target = _store_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([r.to_dict() for r in kept], indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError as exc:
        logger.warning("Agentic IDE: could not update recents: {}", exc)
        return False


__all__ = [
    "MAX_RECENTS",
    "RecentWorkspace",
    "forget",
    "is_throwaway",
    "load",
    "remember",
]
