"""Spend & token accounting — a read model over what the app already records.

Nothing here writes. Every number is derived from stores that other parts of
the app fill on their own (``sessions.db``, ``missions.db``,
``agent_chat.db``), so the section shows historic spend the moment it is
opened and can never race a live voice turn for a write lock.

Public surface:

- :class:`~jarvis.costs.model.CostEntry` — one normalised, priced line item.
- :func:`~jarvis.costs.sources.collect_entries` — read every source.
- :func:`~jarvis.costs.aggregate.build_report` — group, total, rank.
"""
from __future__ import annotations

from .aggregate import CostReport, build_report
from .model import (
    ROLE_AGENT,
    ROLE_PIPELINE,
    ROLE_REALTIME,
    ROLE_TOOL,
    ROLE_WORKER,
    SURFACE_AGENT_CHAT,
    SURFACE_MISSION,
    SURFACE_VOICE,
    CostEntry,
)
from .sources import CostSources, collect_entries, default_sources

__all__ = [
    "ROLE_AGENT",
    "ROLE_PIPELINE",
    "ROLE_REALTIME",
    "ROLE_TOOL",
    "ROLE_WORKER",
    "SURFACE_AGENT_CHAT",
    "SURFACE_MISSION",
    "SURFACE_VOICE",
    "CostEntry",
    "CostReport",
    "CostSources",
    "build_report",
    "collect_entries",
    "default_sources",
]
