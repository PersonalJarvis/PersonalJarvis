"""Durable readback of every prompt handed to an Agentic-IDE pane.

The workspace snapshot intentionally stays metadata-only because it is read on
the IDE's first screen and may describe hundreds of panes. Full prompts live in
separate append-only JSONL files instead: writing one prompt does not rewrite a
large history, and opening the history is the only operation that reads it.

Files are keyed by an opaque pane-lifetime id, never by a user-provided call-sign
or folder path. Renaming a pane therefore keeps its history, while closing it
and later reusing the same visible name cannot inherit somebody else's prompts.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loguru import logger

_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class PromptHistoryEntry:
    """One exact prompt and the delivery result recorded beside it."""

    id: str
    sequence: int
    text: str
    at: float
    submitted: bool | None

    @staticmethod
    def from_dict(value: Any) -> PromptHistoryEntry | None:
        """Parse one row defensively; a damaged row never hides valid ones."""
        if not isinstance(value, dict):
            return None
        entry_id = value.get("id")
        text = value.get("text")
        if not isinstance(entry_id, str) or not entry_id or not isinstance(text, str):
            return None
        try:
            sequence = int(value.get("sequence") or 0)
            at = float(value.get("at") or 0.0)
        except (TypeError, ValueError):
            # Invalid persisted numeric metadata makes only this row unusable.
            return None
        submitted = value.get("submitted")
        if submitted not in (True, False, None):
            submitted = None
        return PromptHistoryEntry(
            id=entry_id,
            sequence=max(0, sequence),
            text=text,
            at=max(0.0, at),
            submitted=submitted,
        )


def _store_dir() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "prompt_history"


def _path(history_id: str) -> Path:
    """Map an opaque id to a fixed safe filename even after a hand-edited restore."""
    digest = hashlib.sha256(history_id.encode("utf-8", errors="replace")).hexdigest()
    return _store_dir() / f"{digest}.jsonl"


def append(history_id: str, entry: PromptHistoryEntry) -> None:
    """Append one complete row; callers decide whether a write failure is fatal."""
    target = _path(history_id)
    line = json.dumps(asdict(entry), ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.write("\n")


def load(history_id: str) -> list[PromptHistoryEntry]:
    """Return valid rows oldest-first, skipping a crash-truncated final line."""
    target = _path(history_id)
    if not target.is_file():
        return []
    entries: list[PromptHistoryEntry] = []
    damaged = 0
    try:
        with _WRITE_LOCK, target.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    parsed = PromptHistoryEntry.from_dict(json.loads(line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # A damaged row is counted and reported after the scan.
                    parsed = None
                if parsed is None:
                    damaged += 1
                else:
                    entries.append(parsed)
    except OSError as exc:
        logger.warning("Agentic IDE: could not read prompt history {}: {}", target, exc)
        return []
    if damaged:
        logger.warning(
            "Agentic IDE: ignored {} damaged prompt-history row(s) in {}",
            damaged,
            target,
        )
    return entries


def merged(
    stored: list[PromptHistoryEntry], current: list[PromptHistoryEntry]
) -> list[PromptHistoryEntry]:
    """Combine disk and live-memory records once, preserving chronological order."""
    by_id = {entry.id: entry for entry in stored}
    by_id.update((entry.id, entry) for entry in current)
    return sorted(by_id.values(), key=lambda entry: (entry.at, entry.sequence, entry.id))
