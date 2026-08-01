"""Web-search skill — public re-exports.

See :class:`WebSearchSkill` for the dispatchable surface and ADR-021 for
the architecture decisions (Y-statement TL;DR in the file header).
"""

from __future__ import annotations

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
    VOICE_LATENCY_BUDGET_MS,
    VOICE_MAX_RESULTS,
    VOICE_MAX_SUMMARY_CHARS,
    SearchSettings,
    apply_voice_override,
    scrub_for_speech,
)
from .skill import (
    RISK_TIER,
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
