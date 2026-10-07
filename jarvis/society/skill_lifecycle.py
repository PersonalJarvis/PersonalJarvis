"""Usage counts and an age-based lifecycle for an agent's private skills.

An agent keeps learning skills in its one endless chat; without care its list
only grows. Each private skill therefore carries a usage record, and a purely
deterministic pass (no model call, decision 8 of MASTERPLAN §2) moves it
between three states:

* ``active`` — used or written recently; listed in the agent's briefing;
* ``stale`` — unused for :data:`STALE_AFTER_DAYS`; still listed and runnable;
* ``archived`` — unused for :data:`ARCHIVE_AFTER_DAYS`; left out of the
  briefing but never deleted. Running or rewriting it makes it active again.

A skill that was never used ages from the moment it was first seen, so a new
skill is never archived before it had a fair chance.
"""

# Portions adapted from NousResearch/hermes-agent @ e473f5a
# (agent/curator.py ``apply_automatic_transitions``, tools/skill_usage.py),
# MIT License, Copyright (c) 2025 Nous Research. See third_party/hermes-agent/LICENSE.

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Final

from filelock import FileLock, Timeout

from .events import now_ms

log = logging.getLogger(__name__)

__all__ = [
    "ACTIVE",
    "ARCHIVE_AFTER_DAYS",
    "ARCHIVED",
    "STALE",
    "STALE_AFTER_DAYS",
    "SkillUsage",
]

ACTIVE: Final[str] = "active"
STALE: Final[str] = "stale"
ARCHIVED: Final[str] = "archived"
STALE_AFTER_DAYS: Final[int] = 14
ARCHIVE_AFTER_DAYS: Final[int] = 30
_DAY_MS: Final[int] = 86_400_000
_FILE: Final[str] = ".usage.json"


class SkillUsage:
    """The usage records of one agent's private skill folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / _FILE
        self._lock = FileLock(str(self.root / ".usage.lock"), timeout=2)

    # ------------------------------------------------------------ storage

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # no skill has been used yet: an empty record is correct
            return {}
        except (OSError, ValueError):
            log.warning("society skills: usage file %s unreadable; starting over", self.path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(self, records: dict[str, dict[str, Any]]) -> None:
        from .memory import atomic_write

        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(records, indent=1, sort_keys=True))

    # ------------------------------------------------------------ writes

    def record(self, slug: str, *, kind: str = "use", now: int | None = None) -> None:
        """Note that ``slug`` was run (``use``) or created/rewritten (``write``)."""
        stamp = now if now is not None else now_ms()
        try:
            with self._lock:
                records = self._load()
                row = records.setdefault(slug, {"first_seen_ms": stamp, "uses": 0})
                if kind == "use":
                    row["uses"] = int(row.get("uses", 0) or 0) + 1
                row["last_activity_ms"] = stamp
                row["state"] = ACTIVE
                self._save(records)
        except (Timeout, OSError):
            # A missed count only delays aging by one event; the skill itself is safe.
            log.warning("society skills: usage of %s not recorded", slug, exc_info=True)

    def transitions(self, slugs: list[str], *, now: int | None = None) -> dict[str, int]:
        """Move every listed skill to the state its last activity calls for."""
        stamp = now if now is not None else now_ms()
        stale_cutoff = stamp - STALE_AFTER_DAYS * _DAY_MS
        archive_cutoff = stamp - ARCHIVE_AFTER_DAYS * _DAY_MS
        counts = {"seeded": 0, "stale": 0, "archived": 0, "reactivated": 0}
        try:
            with self._lock:
                records = self._load()
                changed = False
                for slug in slugs:
                    row = records.get(slug)
                    if row is None:
                        # First sight: its clock starts now, it ages from here.
                        records[slug] = {"first_seen_ms": stamp, "uses": 0, "state": ACTIVE}
                        counts["seeded"] += 1
                        changed = True
                        continue
                    anchor = int(next(
                        (row[k] for k in ("last_activity_ms", "first_seen_ms") if k in row),
                        stamp,
                    ))
                    current = str(row.get("state") or ACTIVE)
                    if anchor <= archive_cutoff and current != ARCHIVED:
                        row["state"] = ARCHIVED
                        counts["archived"] += 1
                        changed = True
                    elif archive_cutoff < anchor <= stale_cutoff and current == ACTIVE:
                        row["state"] = STALE
                        counts["stale"] += 1
                        changed = True
                    elif anchor > stale_cutoff and current != ACTIVE:
                        row["state"] = ACTIVE
                        counts["reactivated"] += 1
                        changed = True
                if changed:
                    self._save(records)
        except (Timeout, OSError):
            # Aging waits for the next pass; listing the skills must still work.
            log.warning("society skills: lifecycle pass skipped for %s", self.root, exc_info=True)
        return counts

    # ------------------------------------------------------------ reads

    def rows(self) -> dict[str, dict[str, Any]]:
        return self._load()
