"""What Jarvis recently did with app actions: ran, failed, or was blocked.

A bounded list (newest first) kept in memory and mirrored to
``DATA_DIR/state/app_action_history.json`` so the Jarvis-actions page still
shows it after a restart. Only action ids, outcomes and short app messages are
kept — never arguments, which may carry personal text.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

__all__ = ["history_path", "recent", "record"]

_MAX: Final[int] = 200
_DEDUPE_S: Final[float] = 3.0
_FILE_NAME: Final[str] = "app_action_history.json"
_lock = threading.Lock()
_entries: deque[dict[str, Any]] | None = None


def history_path() -> Path:
    from jarvis.core import config as cfg_mod  # lazy: tests monkeypatch DATA_DIR

    return Path(cfg_mod.DATA_DIR) / "state" / _FILE_NAME


def _load() -> deque[dict[str, Any]]:
    global _entries
    if _entries is None:
        rows: list[dict[str, Any]] = []
        try:
            raw = json.loads(history_path().read_text(encoding="utf-8"))
            rows = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        except FileNotFoundError:
            # No history file yet is the normal first-run state.
            pass
        except (OSError, ValueError):
            log.warning("app-actions: unreadable history — starting empty", exc_info=True)
        _entries = deque(rows[:_MAX], maxlen=_MAX)
    return _entries


def record(action_id: str, outcome: str, detail: str = "", *, via: str = "") -> None:
    """Add one entry. ``outcome`` is ``ran``, ``failed`` or ``blocked``.

    The same blocked action reported again within a few seconds is one event:
    the tier hook runs once per guard that inspects the call.
    """
    now = time.time()
    with _lock:
        entries = _load()
        if entries and outcome == "blocked":
            last = entries[0]
            if (
                last.get("action") == action_id
                and last.get("outcome") == outcome
                and now - float(last.get("at", 0)) < _DEDUPE_S
            ):
                return
        entries.appendleft(
            {"action": action_id, "outcome": outcome, "detail": detail[:300], "via": via, "at": now}
        )
        snapshot = list(entries)
    path = history_path()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(snapshot), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        log.warning("app-actions: history not persisted", exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)


def recent(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        return list(_load())[: max(0, limit)]


def reset_for_tests() -> None:
    global _entries
    with _lock:
        _entries = None
