"""Web-search skill entry point.

This module defines :class:`WebSearchSkill`, the dispatchable surface for
the web-search capability. The risk tier is hardcoded (not read from
config) so that the safety classification cannot drift via runtime mutation
of a TOML — see ADR-021. The single-source constant ``RISK_TIER`` lives
in the package ``__init__`` and is imported here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from . import RISK_TIER
from ._gemini_client import GeminiClient, SearchHit, SearchResponse
from ._sanitize import QueryRejectedError, sanitize_query
from ._voice_override import (
    SearchSettings,
    apply_voice_override,
    scrub_for_speech,
)


SKILL_NAME: Final[str] = "web_search"
SKILL_VERSION: Final[str] = "1.0.0"

# Backwards-compatible re-export. The single source of truth lives in
# ``__init__.py`` as ``RISK_TIER``; this name kept so external callers that
# already imported ``SKILL_RISK_TIER`` keep working. See ADR-021 §Decision.
SKILL_RISK_TIER: Final[str] = RISK_TIER


@dataclass(frozen=True)
class SkillResult:
    """What a caller receives from :meth:`WebSearchSkill.run`."""

    query: str
    summary: str
    spoken_summary: str
    hits: tuple[SearchHit, ...]
    latency_ms: float
    risk_tier: str
    voice: bool


class WebSearchSkill:
    """Coordinates sanitisation, override-apply, and client call.

    The class is intentionally thin: business rules live in the helper
    modules so each can be unit-tested in isolation.

    Parameters
    ----------
    client
        Anything implementing :class:`~._gemini_client.GeminiClient`. In
        production this is :class:`~._gemini_client.DefaultGeminiClient`;
        tests inject :class:`~._gemini_client.FakeGeminiClient`.
    settings
        Default settings before voice-override-apply. May be customised
        per-call by passing a fresh ``SearchSettings``.
    """

    name: Final[str] = SKILL_NAME
    version: Final[str] = SKILL_VERSION
    risk_tier: Final[str] = RISK_TIER

    def __init__(
        self,
        client: GeminiClient,
        *,
        settings: SearchSettings | None = None,
    ) -> None:
        self._client = client
        self._base_settings = settings or SearchSettings()

    def run(
        self,
        raw_query: str,
        *,
        voice: bool = False,
        settings: SearchSettings | None = None,
    ) -> SkillResult:
        """Execute one search turn.

        Raises
        ------
        QueryRejectedError
            If sanitisation refuses the input (empty, injection token).
        """
        query = sanitize_query(raw_query)

        effective = apply_voice_override(
            settings or self._base_settings, voice=voice
        )

        start = time.perf_counter()
        response: SearchResponse = self._client.search(
            query, max_results=effective.max_results
        )
        wall_ms = (time.perf_counter() - start) * 1000.0

        summary = response.summary[: effective.max_summary_chars]
        spoken = scrub_for_speech(summary) if effective.strip_markdown else summary

        return SkillResult(
            query=query,
            summary=summary,
            spoken_summary=spoken,
            hits=response.hits,
            latency_ms=wall_ms,
            risk_tier=self.risk_tier,
            voice=voice,
        )

    # Convenience predicate so callers can budget without invoking the client.
    @staticmethod
    def will_accept(raw_query: str) -> bool:
        try:
            sanitize_query(raw_query)
        except QueryRejectedError:
            return False
        return True
