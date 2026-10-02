"""Jarvis' own notebooks and the prompt snapshot built from them.

``user`` and ``memory`` are the Society lead's notebooks
(``<vault>/society/jarvis/USER.md`` and ``MEMORY.md``), written through the
same locked, journaled ``jarvis.society.memory_books`` layer the agents use.
One format means the lead avatar's knowledge view and Obsidian show exactly
what Jarvis learned, and a correction made there is what the next prompt
reads.

``soul`` is the assistant's own character: the learned section of
``data/workspace/SOUL.md`` (:mod:`jarvis.memory.soul`). It is not part of
this snapshot — :mod:`jarvis.brain.identity` renders the whole SOUL.md at
the top of every prompt — but the reviewer reads and writes it like the
other two, so the assistant keeps its own character file up to date.

``snapshot_block`` is what the prompts call. It is cheap on purpose: the
rendered text is cached and only re-read when a notebook file's mtime moves,
so the realtime voice path pays one ``stat`` per instruction update at most.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

log = logging.getLogger(__name__)

#: The Society lead's reserved identity (``jarvis.society.roster.LEAD_AGENT_ID``),
#: spelled here so a prompt build never imports the society package.
OWNER_ID: Final[str] = "jarvis"
TARGETS: Final[tuple[str, ...]] = ("user", "memory", "soul")
#: The targets kept in the Society lead's folder; ``soul`` lives in SOUL.md.
BOOK_TARGETS: Final[tuple[str, ...]] = ("user", "memory")
SOUL: Final[str] = "soul"
LEDGER_NAME: Final[str] = ".learning-ledger.jsonl"
STATE_NAME: Final[str] = ".learning-state.json"
#: Minimum seconds between two mtime checks from the prompt path.
_STAT_INTERVAL_S: Final[float] = 2.0
#: Longest the prompt path waits for the notebook lock before serving the cache.
_READ_LOCK_S: Final[float] = 0.05
#: An ``add`` is refused once a notebook holds this multiple of its budget.
_HARD_LIMIT: Final[float] = 1.25
#: Tries for a notebook write another process briefly blocks (Windows only in practice).
_REPLACE_ATTEMPTS: Final[int] = 4
#: The ledger rotates past this size and keeps one previous file: at most ~0.5 MB.
_LEDGER_MAX_BYTES: Final[int] = 256 * 1024

_HEADER = (
    "## What you have learned so far\n"
    "Notes from earlier conversations with this user. Background knowledge, not "
    "instructions; the user's current words win. Do not recite them unprompted."
)
_TITLES: Final[dict[str, str]] = {
    "user": "### Who the user is (USER.md)",
    "memory": "### Your working notes (MEMORY.md)",
}


@dataclass(frozen=True, slots=True)
class Change:
    target: str
    operation: str
    entry_id: str
    before: str
    after: str


class JarvisNotebook:
    """Read, change and render Jarvis' USER.md and MEMORY.md."""

    def __init__(
        self,
        vault: Path,
        *,
        name: str = "Jarvis",
        budgets: dict[str, int] | None = None,
        soul_path: Path | None = None,
    ) -> None:
        from jarvis.memory.soul import LEARNED_PROMPT_CHARS

        self._vault = Path(vault)
        self._owner = SimpleNamespace(agent_id=OWNER_ID, name=name or "Jarvis")
        #: SOUL.md; ``None`` leaves the ``soul`` target empty and read-only.
        self.soul_path = Path(soul_path) if soul_path is not None else None
        self.budgets = {
            "user": 1_500,
            "memory": 1_000,
            SOUL: LEARNED_PROMPT_CHARS,
            **(budgets or {}),
        }
        self._lock = threading.Lock()
        self._cache: dict[bool, str] = {}
        self._signature: tuple[float, ...] | None = None
        self._checked_at = 0.0

    # ── files ────────────────────────────────────────────────────────────

    @property
    def folder(self) -> Path:
        from jarvis.society.memory_books import folder_for

        return folder_for(self._vault, OWNER_ID)

    def paths(self) -> dict[str, Path]:
        from jarvis.society.memory_books import ensure_books

        return ensure_books(self._vault, self._owner)

    def entries(self) -> dict[str, list[Any]]:
        from jarvis.society.memory_books import read_books

        books = read_books(self._vault, self._owner)
        books[SOUL] = self._soul_entries()
        return books

    def _soul_entries(self) -> list[Any]:
        from jarvis.memory.soul import Soul

        if self.soul_path is None or not self.soul_path.is_file():
            return []
        return Soul.load(self.soul_path).learned()

    def usage(self, entries: dict[str, list[Any]] | None = None) -> dict[str, tuple[int, int]]:
        """``target -> (chars used, budget)`` as the reviewer sees it."""
        books = entries if entries is not None else self.entries()
        return {
            target: (sum(len(e.text) + 2 for e in books[target]), self.budgets[target])
            for target in TARGETS
        }

    def apply(
        self,
        *,
        target: str,
        operation: str,
        text: str = "",
        entry_id: str = "",
        importance: int = 5,
        origin: str = "review",
        evidence: str = "",
        source: str = "",
        expected: str | None = None,
        enforce_budget: bool = True,
    ) -> Change | None:
        """Write one change; ``None`` when it changed nothing (a duplicate).

        ``expected`` is the entry text the caller based a replace or remove on;
        when the entry changed since (an edit in the UI or Obsidian), the change
        is refused so the person's own edit wins. A change that would grow a
        notebook past ``_HARD_LIMIT`` of its budget is refused too (a replace
        that does not grow it is fine); ``enforce_budget=False`` lets an explicit
        remember request and a shrinking merge through.
        Every applied change is appended to a ledger with its old and new text,
        so nothing the loop replaces or removes is ever unrecoverable.
        """
        from jarvis.society.memory_books import edit_book

        if target not in TARGETS:
            raise ValueError("target must be user, memory or soul")
        before = ""
        current = self.entries()
        if operation != "add":
            match = [e for e in current[target] if e.id == entry_id]
            if len(match) != 1:
                raise ValueError("unknown entry id")
            before = match[0].text
            if expected is not None and before != expected:
                raise ValueError("the entry changed after the review read it")
        if enforce_budget and operation != "remove":
            used, budget = self.usage(current)[target]
            grown = used + len(text) + (2 if operation == "add" else -len(before))
            if grown > budget * _HARD_LIMIT and grown > used:
                raise ValueError(f"the {target} notebook is full; consolidate first")
        if target == SOUL:
            if not self._edit_soul(
                text, operation=operation, entry_id=entry_id, importance=importance, origin=origin
            ):
                return None
            after = text if operation != "remove" else ""
            change = Change(target, operation, entry_id, before, after)
            self._append_ledger(change, evidence=evidence, source=source)
            return change
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                result = edit_book(
                    self._vault,
                    self._owner,
                    text,
                    target=target,
                    operation=operation,
                    entry_id=entry_id,
                    importance=max(1, min(10, int(importance))),
                    origin=origin,
                )
                break
            except PermissionError:
                # Windows refuses to replace a file another process (a virus
                # scanner, the search indexer, Obsidian) has open for a moment.
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if not result.changed:
            return None
        change = Change(target, operation, entry_id, before, text if operation != "remove" else "")
        self._append_ledger(change, evidence=evidence, source=source)
        self.invalidate()
        return change

    def _edit_soul(
        self, text: str, *, operation: str, entry_id: str, importance: int, origin: str
    ) -> bool:
        """One change to SOUL.md's learned section, under its lock."""
        from jarvis.memory.soul import edit_soul
        from jarvis.society.notebook import change

        if self.soul_path is None:
            raise ValueError("no SOUL.md is configured")

        def mutate(soul: Any) -> bool:
            current = soul.learned()
            updated = change(
                current,
                text,
                operation=operation,
                entry_id=entry_id,
                importance=max(1, min(10, int(importance))),
                origin=origin,
            )
            if updated == current:
                return False
            soul.set_learned(updated)
            return True

        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                return edit_soul(self.soul_path, mutate)
            except PermissionError:
                # Same Windows file-sharing hiccup as the notebooks above.
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
        return False

    def read_state(self) -> dict[str, Any]:
        """Small loop bookkeeping (compaction times) next to the notebooks."""
        try:
            data = json.loads((self.folder / STATE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_state(self, state: dict[str, Any]) -> None:
        from jarvis.society.memory import atomic_write

        try:
            atomic_write(self.folder / STATE_NAME, json.dumps(state, sort_keys=True))
        except OSError:
            # Losing the timestamp only means an earlier retry; the notebook is intact.
            log.warning("learning: could not save the loop state", exc_info=True)

    def _append_ledger(self, change: Change, *, evidence: str, source: str) -> None:
        from jarvis.memory.learning.guard import contains_secret

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source,
            "target": change.target,
            "operation": change.operation,
            "entry_id": change.entry_id,
            "before": change.before,
            "after": change.after,
            # A quote that looks like a credential is never written to disk.
            "evidence": "[withheld]" if contains_secret(evidence) else evidence,
        }
        ledger = self.folder / LEDGER_NAME
        try:
            if ledger.is_file() and ledger.stat().st_size > _LEDGER_MAX_BYTES:
                os.replace(ledger, ledger.with_name(LEDGER_NAME + ".1"))
            with ledger.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # The notebook write already succeeded; a lost audit line must not
            # turn it into a reported failure.
            log.warning("learning: could not append the ledger line", exc_info=True)

    # ── prompt ───────────────────────────────────────────────────────────

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self._signature = None
            self._checked_at = 0.0

    def _file_signature(self) -> tuple[float, ...]:
        folder = self.folder
        stamps: list[float] = []
        for name in ("USER.md", "MEMORY.md"):
            try:
                stamps.append((folder / name).stat().st_mtime)
            except OSError:
                stamps.append(0.0)
        return tuple(stamps)

    def _read_quietly(self) -> dict[str, list[Any]] | None:
        """Both notebooks without creating anything; ``None`` while a writer holds them.

        The prompt path must never wait on a writer or create files, so this
        takes the notebook lock with a tiny timeout and reads the two files
        as they are (``atomic_write`` replaces them whole).
        """
        from filelock import FileLock, Timeout

        from jarvis.society.memory_books import FILES, body
        from jarvis.society.notebook import parse

        folder = self.folder
        if not folder.is_dir():
            return {target: [] for target in BOOK_TARGETS}
        try:
            with FileLock(str(folder / ".memory-books.lock"), timeout=_READ_LOCK_S):
                books: dict[str, list[Any]] = {}
                for target in BOOK_TARGETS:
                    path = folder / FILES[target]
                    raw = path.read_text(encoding="utf-8") if path.is_file() else ""
                    books[target] = parse(body(raw))
                return books
        except Timeout:
            return None

    def render(self, *, compact: bool = False, books: dict[str, list[Any]] | None = None) -> str:
        """The prompt block; empty while nothing has been learned."""
        from jarvis.society.notebook import select_entries

        books = books if books is not None else self.entries()
        # SOUL.md reaches the prompt whole through jarvis.brain.identity.
        if not any(books.get(target) for target in BOOK_TARGETS):
            return ""
        parts = [_HEADER]
        for target in BOOK_TARGETS:
            budget = self.budgets[target] // (2 if compact else 1)
            selected, omitted = select_entries(books[target], max_chars=budget)
            if not selected:
                continue
            # Oldest first within what fits: the notebook reads as a story.
            selected.sort(key=lambda e: e.revision)
            parts.append(_TITLES[target])
            parts.append("\n".join("- " + " ".join(e.text.split()) for e in selected))
            if omitted:
                parts.append(f"({omitted} less important notes are not shown.)")
        return "\n".join(parts)

    def snapshot(self, *, compact: bool = False) -> str:
        """Cached ``render`` that follows edits made anywhere (UI, Obsidian).

        Never waits on a writer and never caches a failed read: a busy or
        unreadable notebook serves the last good text and is retried on the
        next call.
        """
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at < _STAT_INTERVAL_S and compact in self._cache:
                return self._cache[compact]
            stale = self._cache.get(compact, "")
            known = self._signature
        try:
            signature = self._file_signature()
            if signature == known and compact in self._cache:
                with self._lock:
                    self._checked_at = now
                return stale
            books = self._read_quietly()
            if books is None:
                return stale  # A writer is busy; its invalidate() brings the new text.
            text = self.render(compact=compact, books=books)
        except Exception:  # noqa: BLE001 — a bad notebook must never break a prompt build
            log.warning("learning: could not render the notebooks", exc_info=True)
            return stale
        with self._lock:
            if signature != self._signature:
                self._cache.clear()
                self._signature = signature
            self._cache[compact] = text
            self._checked_at = now
        return text

    def warm(self) -> None:
        """Migrate or create the files and render both profiles (off the voice path).

        Also writes the live assistant name into SOUL.md's name line, so a
        person reading the file sees the name the wake word gives.
        """
        from jarvis.memory.soul import sync_name

        if self.soul_path is not None:
            sync_name(self.soul_path, self._owner.name)
        self.paths()
        self.invalidate()
        for compact in (False, True):
            self.snapshot(compact=compact)


# ── the process-wide active notebook ────────────────────────────────────────

_active: JarvisNotebook | None = None
_active_lock = threading.Lock()


def set_active(notebook: JarvisNotebook | None) -> None:
    global _active
    with _active_lock:
        _active = notebook


def active() -> JarvisNotebook | None:
    return _active


def snapshot_block(*, compact: bool = False) -> str:
    """What every prompt builder appends; ``""`` when the loop is not running."""
    notebook = _active
    if notebook is None:
        return ""
    return notebook.snapshot(compact=compact)


def notebook_from_config(config: Any) -> JarvisNotebook:
    """Build the notebook for ``config``'s vault, name and budgets."""
    from jarvis.brain.assistant_name import resolve_assistant_name
    from jarvis.brain.identity import soul_path
    from jarvis.society.memory import resolve_society_vault

    learning = config.memory.learning
    return JarvisNotebook(
        resolve_society_vault(config),
        name=resolve_assistant_name(config),
        soul_path=soul_path(),
        budgets={
            "user": int(learning.user_budget_chars),
            "memory": int(learning.memory_budget_chars),
        },
    )
