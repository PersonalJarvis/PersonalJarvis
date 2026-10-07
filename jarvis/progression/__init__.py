"""The Jarvis Verse level system: XP, levels, titles and cosmetic rewards.

``rules`` is the pure rulebook, ``store`` the SQLite ledger and ``service``
the bus listener that pays XP for real achievements. Contract:
``docs/agent-society/level-system.md``.
"""

from .rules import MAX_LEVEL, REWARDS, RULES, TITLES
from .service import ProgressionService, UnknownAction

__all__ = ["MAX_LEVEL", "REWARDS", "RULES", "TITLES", "ProgressionService", "UnknownAction"]
