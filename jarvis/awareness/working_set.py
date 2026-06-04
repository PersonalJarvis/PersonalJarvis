"""WorkingSet — Multi-Context LRU for Phase A4.

Phase A4 problem (Plan §8): currently Jarvis only knows the last episode.
When the user switches between 3 contexts (VS Code → Slack → VS Code), on
the next wake-word they would get the Slack thread — incorrect. The Working
Set holds up to 5 ``Context`` slots in RAM (LRU). On re-activation of a
known context, ``snapshot_for_prompt`` returns the episode for that context —
not "the last whatever-episode".

Hard negatives (from the spec):
- ``WorkingSet`` is NOT persisted — RAM-only. SQLite holds all episodes.
- No singletons — one instance per ``AwarenessManager`` (DI).
- Eviction does NOT delete episodes — only the RAM pointer; episodes remain in DB.
- Cap = 5 slots (Plan §8).
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict

from jarvis.awareness.context import Context

logger = logging.getLogger(__name__)


# Plan §8: max 5 LRU slots.
DEFAULT_MAX_SLOTS: int = 5


class WorkingSet:
    """LRU cache of ``Context`` slots.

    Identity key is ``Context.project_root`` (strings — hostname/cwd/
    process_name). The ordering inside the internal ``OrderedDict`` is
    recency order: last entry = most recent context, first entry =
    oldest (= eviction candidate).

    API:
        ``observe(context)``       Mark a context as active, promotes it to top.
        ``set_episode(root, id)``  Link a persisted episode ID to the
                                   corresponding context slot.
        ``get(root)``              LRU-fresh lookup, returns Context or None.
        ``contexts()``             Iterable in MRU order (most recent first).
        ``size``                   Number of slots currently occupied.
    """

    def __init__(self, *, max_slots: int = DEFAULT_MAX_SLOTS) -> None:
        if max_slots <= 0:
            raise ValueError(f"max_slots must be > 0, got {max_slots}")
        self._max_slots = max_slots
        # OrderedDict: insertion-order is recency. move_to_end()=promote.
        self._slots: OrderedDict[str, Context] = OrderedDict()

    # ---- Mutation -----------------------------------------------------------

    def observe(self, context: Context) -> Context | None:
        """Mark a context as just-active (LRU promote).

        - If ``project_root`` is already present: the context is re-promoted
          (moved to the end of the OrderedDict). Fields are merged: the new
          ``task_label``/``last_seen_ns`` overwrites the old one; the old
          ``last_episode_id`` is preserved unless the new
          ``context.last_episode_id`` is not None.
        - If ``project_root`` is not yet present: a new slot is created.
          If the LRU is full: ``popitem(last=False)`` evicts the oldest
          (== first entry).

        Returns: the evicted Context if an eviction occurred; otherwise None.
        (Used by the AwarenessManager to optionally emit ``ContextSwitched``
        events.)
        """
        evicted: Context | None = None
        existing = self._slots.get(context.project_root)
        if existing is not None:
            # Merge: adopt new fields; keep last_episode_id only if the new
            # value is not None (otherwise retain the old one).
            merged = Context(
                project_root=context.project_root,
                task_label=context.task_label or existing.task_label,
                last_episode_id=(
                    context.last_episode_id
                    if context.last_episode_id is not None
                    else existing.last_episode_id
                ),
                # Always take the new value here: this is an observe() call,
                # so the caller is asserting "this slot was just touched". A
                # bare ``or`` would short-circuit on the integer 0 — uncommon
                # but semantically wrong; ``time.time_ns()`` of a Unix-epoch
                # boot would otherwise be discarded in favour of the older
                # mtime, the exact opposite of LRU's promote-on-observe rule.
                last_seen_ns=context.last_seen_ns,
                process_name=context.process_name or existing.process_name,
            )
            self._slots[context.project_root] = merged
            self._slots.move_to_end(context.project_root)
            return None

        # New slot — check whether the LRU is full.
        if len(self._slots) >= self._max_slots:
            # popitem(last=False) removes the OLDEST (FIFO-insertion order).
            _, evicted = self._slots.popitem(last=False)
        self._slots[context.project_root] = context
        return evicted

    def set_episode(self, project_root: str, episode_id: int) -> bool:
        """Links a just-persisted episode to its context slot.

        Called by the AwarenessManager on ``EpisodeRecorded`` — the episode
        belongs to the CURRENT frame context, whose project_root was already
        registered via ``observe``.

        Returns True if the slot was updated, False if the slot was evicted
        in the meantime (e.g. during rapid multi-context switches within a
        Verdichter call).
        """
        existing = self._slots.get(project_root)
        if existing is None:
            return False
        updated = Context(
            project_root=existing.project_root,
            task_label=existing.task_label,
            last_episode_id=episode_id,
            last_seen_ns=existing.last_seen_ns,
            process_name=existing.process_name,
        )
        self._slots[project_root] = updated
        # An episode update does NOT promote — recency comes from observe().
        return True

    # ---- Read ---------------------------------------------------------------

    def get(self, project_root: str) -> Context | None:
        """Lookup without promote — read-only, non-mutating."""
        return self._slots.get(project_root)

    def contexts(self) -> list[Context]:
        """Snapshot of all contexts in MRU order (most recent first).

        Returns a new list — the caller may mutate it without affecting the
        WorkingSet.
        """
        # OrderedDict iterates in insertion order (FIFO); we want MRU-first,
        # so reverse.
        return list(reversed(self._slots.values()))

    @property
    def size(self) -> int:
        return len(self._slots)

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def current(self) -> Context | None:
        """The most recent context (MRU-first), or None if the set is empty."""
        if not self._slots:
            return None
        # next() over reversed gives MRU.
        return next(reversed(self._slots.values()))

    # ---- Render -------------------------------------------------------------

    def render_for_prompt(self, *, max_chars: int = 400) -> str:
        """Compact plain-text render for system-prompt injection.

        Returns a multi-line block (or empty string when 0 or 1 slot — with
        only one context the working set adds nothing,
        ``AwarenessState.snapshot_for_prompt`` already renders the single
        episode).

        Format:
            Active contexts:
            - <project_root>: <task_label> [Episode #<id>]
            - <project_root>: <task_label> [Episode #<id>]
            ...
        """
        if len(self._slots) <= 1:
            return ""

        lines = ["Active contexts:"]
        for ctx in self.contexts():
            ep_part = (
                f" [Episode #{ctx.last_episode_id}]"
                if ctx.last_episode_id is not None
                else ""
            )
            label = ctx.task_label or ctx.project_root
            lines.append(f"- {ctx.project_root}: {label}{ep_part}")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text

    # ---- Test-Helper --------------------------------------------------------

    def __len__(self) -> int:
        return len(self._slots)

    def __contains__(self, project_root: object) -> bool:
        return project_root in self._slots


def now_ns() -> int:
    """Wrapper for time.time_ns() — for test patching."""
    return time.time_ns()
