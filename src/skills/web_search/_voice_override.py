"""Voice-context overrides for the web-search skill.

When the search request originates from a voice turn, the skill applies
voice-specific overrides — shorter response, no Markdown, no URLs in the
spoken text, and a tighter latency budget. The voice frontend should still
receive the full result for the on-screen card; only the spoken summary is
trimmed.

This module is intentionally tiny and pure. It does *not* import the LLM
client — `skill.py` composes it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

VOICE_MAX_SUMMARY_CHARS: Final[int] = 280
VOICE_LATENCY_BUDGET_MS: Final[int] = 2_500
TEXT_LATENCY_BUDGET_MS: Final[int] = 8_000
VOICE_MAX_RESULTS: Final[int] = 3
TEXT_MAX_RESULTS: Final[int] = 8


@dataclass(frozen=True)
class SearchSettings:
    """Per-turn knobs that the skill applies before calling the client."""

    max_results: int = TEXT_MAX_RESULTS
    max_summary_chars: int = 1_200
    latency_budget_ms: int = TEXT_LATENCY_BUDGET_MS
    strip_markdown: bool = False
    strip_urls_from_summary: bool = False


_MARKDOWN_TOKENS: Final[tuple[str, ...]] = ("**", "__", "`", "###", "##", "#")


def apply_voice_override(settings: SearchSettings, *, voice: bool) -> SearchSettings:
    """Return ``settings`` adjusted for the voice path when ``voice`` is true.

    The function is total and pure — same input yields same output, no I/O.
    Non-voice calls return ``settings`` unchanged (identity).
    """
    if not voice:
        return settings
    return replace(
        settings,
        max_results=min(settings.max_results, VOICE_MAX_RESULTS),
        max_summary_chars=min(settings.max_summary_chars, VOICE_MAX_SUMMARY_CHARS),
        latency_budget_ms=min(settings.latency_budget_ms, VOICE_LATENCY_BUDGET_MS),
        strip_markdown=True,
        strip_urls_from_summary=True,
    )


def scrub_for_speech(summary: str) -> str:
    """Strip Markdown noise and bare URLs so a TTS pass speaks cleanly.

    Pure regex / string ops — no LLM call (latency mandate inherited from
    the Jarvis ``scrub_for_voice`` discipline; see ADR-0010 in the parent
    repo for the equivalent contract on the voice path).
    """
    cleaned = summary
    for token in _MARKDOWN_TOKENS:
        cleaned = cleaned.replace(token, "")

    out_words: list[str] = []
    for word in cleaned.split():
        if word.startswith(("http://", "https://", "www.")):
            continue
        out_words.append(word)
    return " ".join(out_words).strip()
