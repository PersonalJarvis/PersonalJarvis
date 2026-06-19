"""Web-search skill — public re-exports.

See :class:`WebSearchSkill` for the dispatchable surface and ADR-021 for
the architecture decisions (Y-statement TL;DR in the file header).
"""

from __future__ import annotations

from typing import Final

# Single source of truth for the skill's risk classification.
#
# Anti-drift contract (see ADR-021 §Decision): this literal lives in
# exactly one place across the whole skill package. ``skill.py`` imports
# it via ``from . import RISK_TIER`` and binds it to every public-facing
# surface (``SKILL_RISK_TIER`` module constant + ``WebSearchSkill.risk_tier``
# class attribute).
#
# IMPORTANT: this definition MUST appear before the ``from .skill import …``
# line below. ``skill.py`` resolves ``from . import RISK_TIER`` against the
# *partially-initialised* package namespace at import time; reordering the
# block would break the import.
RISK_TIER: Final[str] = "monitor"

from ._gemini_client import (
    DefaultGeminiClient,
    FakeGeminiClient,
    GeminiClient,
    SearchHit,
    SearchResponse,
)
from ._sanitize import (
    INJECTION_TOKENS,
    MAX_QUERY_LEN,
    QueryRejectedError,
    is_safe,
    sanitize_query,
)
from ._voice_override import (
    SearchSettings,
    VOICE_LATENCY_BUDGET_MS,
    VOICE_MAX_RESULTS,
    VOICE_MAX_SUMMARY_CHARS,
    apply_voice_override,
    scrub_for_speech,
)
from .skill import (
    SKILL_NAME,
    SKILL_RISK_TIER,
    SKILL_VERSION,
    SkillResult,
    WebSearchSkill,
)

__all__ = [
    "DefaultGeminiClient",
    "FakeGeminiClient",
    "GeminiClient",
    "INJECTION_TOKENS",
    "MAX_QUERY_LEN",
    "QueryRejectedError",
    "RISK_TIER",
    "SearchHit",
    "SearchResponse",
    "SearchSettings",
    "SkillResult",
    "SKILL_NAME",
    "SKILL_RISK_TIER",
    "SKILL_VERSION",
    "VOICE_LATENCY_BUDGET_MS",
    "VOICE_MAX_RESULTS",
    "VOICE_MAX_SUMMARY_CHARS",
    "WebSearchSkill",
    "apply_voice_override",
    "is_safe",
    "sanitize_query",
    "scrub_for_speech",
]
