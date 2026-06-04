"""Append-only writer for ``wiki/obsidian-vault/log.md``.

The log is part of the maintenance contract: every wiki ingest produces
exactly one log entry, the entries are chronological, and existing
entries are never edited or reordered. See ``wiki/obsidian-vault/schema.md``
section "The ``log.md`` File" for the binding format.

Atomicity strategy
------------------

Each append goes through a write-to-tempfile-then-replace dance so that
a crash mid-write cannot leave the on-disk file with a partial entry:

1. Read the current contents (or accept ``b""`` if the file is missing).
2. Render the new entry to a string.
3. Open a tempfile **in the same directory** as ``log.md`` — same drive
   is the precondition for ``os.replace`` being atomic on Windows.
4. Write ``current + new_entry`` into the tempfile, ``fsync`` it.
5. ``os.replace(tmp, log.md)``.

If step 4 raises (disk full, simulated crash in tests, etc.) the
tempfile is unlinked and the original log file is left untouched. The
caller sees the exception; the on-disk vault remains consistent.

Concurrent writers are guarded by an instance-level ``asyncio.Lock`` —
the curator runs one ingest at a time, but the lock keeps two
coroutines on the same writer from racing each other.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TimestampFn = Callable[[], datetime]

# Valid verbs from schema.md "The log.md File". Anything outside this
# set is rejected with a ``ValueError`` — protects against silent typos
# leaking into the log.
VALID_VERBS: frozenset[str] = frozenset(
    {"ingest", "update", "create", "merge", "rename", "archive", "delete"}
)


class LogWriter:
    """Append entries to a ``log.md`` file atomically.

    Parameters
    ----------
    log_path:
        Absolute path to the ``log.md`` file. The parent directory must
        already exist.
    clock:
        Optional callable returning the timestamp to stamp each entry
        with. Defaults to ``datetime.now``. Tests inject a frozen clock
        for deterministic output.
    """

    def __init__(
        self,
        log_path: Path,
        *,
        clock: TimestampFn | None = None,
    ) -> None:
        self._log_path = Path(log_path)
        self._clock = clock or datetime.now
        self._lock = asyncio.Lock()

    @property
    def log_path(self) -> Path:
        """Return the path of the underlying log file."""
        return self._log_path

    async def append_log_entry(
        self,
        *,
        verb: str,
        subject: str,
        pages_touched: Iterable[str],
        source: str,
        summary: str,
    ) -> None:
        """Append one entry to the log atomically.

        Raises
        ------
        ValueError
            If ``verb`` is not one of ``VALID_VERBS`` or ``subject`` /
            ``source`` / ``summary`` are empty after stripping.
        FileNotFoundError
            If the parent directory of the log file does not exist.
        """
        verb_norm = (verb or "").strip().lower()
        if verb_norm not in VALID_VERBS:
            raise ValueError(
                f"LogWriter: verb {verb_norm!r} not in {sorted(VALID_VERBS)}"
            )
        subject_norm = (subject or "").strip()
        source_norm = (source or "").strip()
        summary_norm = " ".join((summary or "").split())
        if not subject_norm:
            raise ValueError("LogWriter: subject must not be empty")
        if not source_norm:
            raise ValueError("LogWriter: source must not be empty")
        if not summary_norm:
            raise ValueError("LogWriter: summary must not be empty")

        rendered_touched = _render_pages_touched(pages_touched)
        timestamp = self._clock().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"\n## [{timestamp}] {verb_norm} | {subject_norm}\n\n"
            f"- pages touched: {rendered_touched}\n"
            f"- source: {source_norm}\n"
            f"- summary: {summary_norm}\n"
        )

        async with self._lock:
            await asyncio.to_thread(self._append_atomic, entry)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _append_atomic(self, entry: str) -> None:
        """Read current log, write current + entry to tempfile, replace.

        Runs synchronously inside ``asyncio.to_thread``. The hook
        ``_pre_replace_hook`` is a test seam — production code never
        sets it, tests use it to raise mid-write and verify rollback.
        """
        parent = self._log_path.parent
        if not parent.exists():
            raise FileNotFoundError(
                f"LogWriter: parent directory missing: {parent}"
            )
        if self._log_path.exists():
            current = self._log_path.read_bytes()
        else:
            current = b""
        encoded = entry.encode("utf-8")
        new_content = current + encoded

        fd, tmp_name = tempfile.mkstemp(
            prefix=".log.md.", suffix=".tmp", dir=str(parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(new_content)
                fh.flush()
                os.fsync(fh.fileno())
            # Test seam — only used by the crash-simulation test. None in
            # production. If the hook raises, the tempfile is cleaned up
            # in the finally branch below and log.md stays untouched.
            self._pre_replace_hook()
            os.replace(tmp_path, self._log_path)
        except BaseException:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:  # pragma: no cover — cleanup best-effort
                logger.warning(
                    "LogWriter: tempfile cleanup failed for %s", tmp_path
                )
            raise

    def _pre_replace_hook(self) -> None:
        """Test seam — fires immediately before ``os.replace``.

        Production code never overrides this. The crash-mid-write test
        monkey-patches it to raise, which lets us verify that ``log.md``
        keeps its old contents and the tempfile is cleaned up.
        """
        return None


def _render_pages_touched(pages: Iterable[str]) -> str:
    """Render the ``pages touched`` value as a comma-separated list.

    Each entry is wrapped in ``[[...]]`` if not already. Empty
    iterables render as ``(none)`` — the schema requires the field be
    present even when no pages were materially changed.
    """
    rendered: list[str] = []
    for raw in pages:
        token = (raw or "").strip()
        if not token:
            continue
        if not token.startswith("[["):
            if token.endswith(".md"):
                token = token[:-3]
            token = f"[[{token}]]"
        rendered.append(token)
    if not rendered:
        return "(none)"
    return ", ".join(rendered)
