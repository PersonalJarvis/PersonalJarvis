"""Curator — top-level pipeline, wiring Extractor + Validator + Merger.

Single entry point for the BrainManager:

    curator = Curator(brain=haiku, profile=up, people=ps, bus=bus)
    await curator.process_turn(user_text, assistant_text)

Features:
- **Fire-and-forget compatible:** `process_turn` can be started via
  `asyncio.create_task` — errors are logged but not re-raised.
- **Per-profile lock:** prevents concurrent writes to USER.md when two turns
  overlap.
- **Minimum-input gating:** very short turns (<5 words) are skipped — too
  little signal to justify a Haiku call.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..people import PersonStore
from ..user_profile import UserProfile
from .extractor import Extractor
from .merger import Merger, MergeReport
from .validator import Validator

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.protocols import Brain

log = logging.getLogger(__name__)

MIN_USER_WORDS = 5   # below this threshold: skip (too little signal)


class Curator:
    """Coordinates Extractor → Validator → Merger."""

    def __init__(
        self,
        *,
        brain: Brain,
        profile: UserProfile,
        people: PersonStore,
        bus: EventBus | None = None,
    ) -> None:
        self._extractor = Extractor(brain)
        self._validator = Validator(profile, people)
        self._merger = Merger(profile, people, bus)
        self._profile = profile
        self._people = people
        self._bus = bus
        self._write_lock = asyncio.Lock()
        self._review_queue: list[tuple] = []  # (cand, reason) for the UI

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def process_turn(
        self,
        user_text: str,
        assistant_text: str,
    ) -> MergeReport:
        """Full pipeline: extract → validate → merge. Swallows all errors."""
        try:
            # Pre-check: enough signal?
            if len(user_text.split()) < MIN_USER_WORDS:
                return MergeReport()

            # Context for extractor: existing user info + known people
            user_name = self._profile.name
            known_people = [p.name for p in self._people.list_all()]

            candidates = await self._extractor.extract(
                user_text=user_text,
                assistant_text=assistant_text,
                user_name=user_name,
                known_people=known_people,
            )
            if not candidates:
                return MergeReport()

            # Reload profile before writing — in case the user edited it manually
            async with self._write_lock:
                self._profile.reload()
                result = self._validator.validate(candidates)

                # Buffer review candidates (UI can fetch them)
                for cand, reason in result.review:
                    self._review_queue.append((cand, reason))

                report = await self._merger.apply(result.accepted)

                if report.applied:
                    log.info(
                        "Curator: %d facts merged — %s",
                        report.applied, ", ".join(report.details),
                    )
                if result.review:
                    log.info(
                        "Curator: %d facts in review queue (reason: %s)",
                        len(result.review), result.review[0][1],
                    )
                return report
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator-Pipeline fehlgeschlagen: %s", exc, exc_info=log.isEnabledFor(logging.DEBUG))
            return MergeReport(failed=1)

    # ------------------------------------------------------------------
    # Review queue (UI pull)
    # ------------------------------------------------------------------

    def pending_reviews(self) -> list[tuple]:
        return list(self._review_queue)

    def clear_reviews(self) -> None:
        self._review_queue.clear()
