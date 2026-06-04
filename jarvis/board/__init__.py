"""Jarvis Board — Personal Mastery Dashboard (Phase A).

Local, offline-first dashboard that aggregates the ``FlightRecorder`` JSONL
stream into daily stats and personal records. No network calls, no forwarding
of voice text or tool arguments — the aggregator operates exclusively on
safe aggregate fields.

See ``docs/jarvis-board/RECON.md`` and ``docs/jarvis-board/ARCHITECTURE.md``
for the design.
"""
from __future__ import annotations

from .achievements import ACHIEVEMENTS, ACHIEVEMENTS_BY_ID, AchievementSpec
from .aggregator import BoardAggregator, DailyStats, PersonalRecord
from .evaluator import AchievementEvaluator
from .store import BoardStore

__all__ = [
    "ACHIEVEMENTS",
    "ACHIEVEMENTS_BY_ID",
    "AchievementEvaluator",
    "AchievementSpec",
    "BoardAggregator",
    "BoardStore",
    "DailyStats",
    "PersonalRecord",
]
