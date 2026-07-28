"""Local record of what was dictated — raw text, cleaned text, and outcome.

Two reasons this exists, and one reason it is careful:

* **Auditability.** The filler cleanup is deterministic, but "deterministic"
  only helps if you can see what it did. Storing raw *and* cleaned side by side
  is what makes a wrong rule findable instead of merely suspected.
* **Recovery.** When insertion degrades to "it is on your clipboard" and the
  user copies something else before pasting, the transcript is otherwise gone.

The care: dictated text is among the most sensitive data this application ever
holds — it is, by definition, whatever the user is writing. So the store is
local-only (a JSON sidecar under ``user_data_dir()/data/``, never synced,
never sent anywhere), capped in size, aged out on a user-set retention, and
purgeable with one call. It can be switched off entirely
(``[dictation].history_enabled = false``), in which case nothing is written.

Storage pattern mirrors ``jarvis.speech.stt_dictionary.DictionaryStore``:
atomic tempfile + ``os.replace`` so a crash mid-write never leaves a torn file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Hard ceiling regardless of configuration — an abuse guard, not a product limit.
MAX_ENTRIES_CEILING = 5_000
#: Longest single transcript kept. Longer ones are stored truncated with a marker.
MAX_TEXT_LEN = 20_000


@dataclass(frozen=True, slots=True)
class DictationEntry:
    """One completed dictation."""

    id: str
    created_at: str
    #: The transcript exactly as the STT returned it.
    raw_text: str
    #: What was actually inserted (equals ``raw_text`` when no cleanup applied).
    text: str
    #: BCP-47-ish language the STT reported, or "" when unknown.
    language: str = ""
    #: Seconds of audio.
    duration_s: float = 0.0
    #: ``inserted`` | ``clipboard_only`` | ``unavailable`` | ``chat``.
    outcome: str = ""
    #: How it got there, e.g. ``clipboard+ctrl_v``.
    method: str = ""
    #: Words the cleanup removed (0 when it did not run or was refused).
    removed_words: int = 0
    #: Why a cleanup did not apply — ``""`` when it did.
    cleanup_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_history_path() -> Path:
    """``<user data>/data/dictation_history.json``."""
    from jarvis.core.paths import user_data_dir

    return Path(user_data_dir()) / "data" / "dictation_history.json"


class DictationHistory:
    """Append-only-ish store of recent dictations. Never raises to the caller.

    Every public method is wrapped: a broken or unreadable history file must
    never cost the user their dictation. Failures are logged and degrade to
    "no history", which is a cosmetic loss.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_history_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # -- reading ---------------------------------------------------------

    def list_all(self) -> list[DictationEntry]:
        """Newest first. An unreadable file reads as an empty history."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("dictation history unreadable: %s", exc)
            return []
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("dictation history is corrupt, ignoring it: %s", exc)
            return []
        entries: list[DictationEntry] = []
        for item in payload.get("entries", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(
                    DictationEntry(
                        id=str(item.get("id") or uuid.uuid4().hex),
                        created_at=str(item.get("created_at") or ""),
                        raw_text=str(item.get("raw_text") or ""),
                        text=str(item.get("text") or ""),
                        language=str(item.get("language") or ""),
                        duration_s=float(item.get("duration_s") or 0.0),
                        outcome=str(item.get("outcome") or ""),
                        method=str(item.get("method") or ""),
                        removed_words=int(item.get("removed_words") or 0),
                        cleanup_reason=str(item.get("cleanup_reason") or ""),
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
            except (TypeError, ValueError):
                continue  # one bad row never invalidates the rest
        return entries

    # -- writing ---------------------------------------------------------

    def add(
        self,
        *,
        raw_text: str,
        text: str,
        language: str = "",
        duration_s: float = 0.0,
        outcome: str = "",
        method: str = "",
        removed_words: int = 0,
        cleanup_reason: str = "",
        max_entries: int = 200,
        retention_days: int = 30,
    ) -> DictationEntry | None:
        """Record one dictation and prune. ``None`` when nothing was stored."""
        if not (raw_text or text):
            return None
        entry = DictationEntry(
            id=uuid.uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            raw_text=_clip(raw_text),
            text=_clip(text),
            language=str(language or ""),
            duration_s=max(0.0, float(duration_s or 0.0)),
            outcome=str(outcome or ""),
            method=str(method or ""),
            removed_words=max(0, int(removed_words or 0)),
            cleanup_reason=str(cleanup_reason or ""),
        )
        try:
            with self._lock:
                entries = [entry, *self.list_all()]
                entries = _prune(
                    entries,
                    max_entries=max_entries,
                    retention_days=retention_days,
                )
                self._write(entries)
        except Exception:  # noqa: BLE001 — history is never worth a failed dictation
            log.warning("could not record the dictation history entry", exc_info=True)
            return None
        return entry

    def delete(self, entry_id: str) -> bool:
        try:
            with self._lock:
                entries = self.list_all()
                kept = [e for e in entries if e.id != entry_id]
                if len(kept) == len(entries):
                    return False
                self._write(kept)
                return True
        except Exception:  # noqa: BLE001
            log.warning("could not delete a dictation history entry", exc_info=True)
            return False

    def clear(self) -> bool:
        """Purge everything. The user-facing "delete my dictation history"."""
        try:
            with self._lock:
                self._write([])
                return True
        except Exception:  # noqa: BLE001
            log.warning("could not clear the dictation history", exc_info=True)
            return False

    def _write(self, entries: list[DictationEntry]) -> None:
        payload = {"version": 1, "entries": [e.to_dict() for e in entries]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic tempfile + os.replace: a crash mid-write never leaves a torn
        # sidecar (same discipline as the config writer, AP-7).
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".dictation_history_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def _clip(text: str) -> str:
    value = str(text or "")
    if len(value) <= MAX_TEXT_LEN:
        return value
    return value[:MAX_TEXT_LEN] + " […truncated]"


def _prune(
    entries: list[DictationEntry],
    *,
    max_entries: int,
    retention_days: int,
) -> list[DictationEntry]:
    """Drop entries past the count cap or the retention window.

    ``retention_days = 0`` means "keep until the count cap"; an unparseable
    timestamp is kept rather than silently discarded.
    """
    cap = max(0, min(int(max_entries or 0), MAX_ENTRIES_CEILING))
    kept = entries
    if retention_days and retention_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=int(retention_days))
        fresh: list[DictationEntry] = []
        for entry in kept:
            try:
                created = datetime.fromisoformat(entry.created_at)
            except (TypeError, ValueError):
                fresh.append(entry)
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created >= cutoff:
                fresh.append(entry)
        kept = fresh
    return kept[:cap] if cap else []


__all__ = [
    "MAX_ENTRIES_CEILING",
    "MAX_TEXT_LEN",
    "DictationEntry",
    "DictationHistory",
    "default_history_path",
]
