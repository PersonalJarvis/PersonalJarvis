"""Turn another agent's report into a short spoken sentence for the user.

Two things report back to the user through Jarvis: a coding agent in an
Agentic-IDE pane that finished the job Jarvis handed it, and a Jarvis agent
answering the lead. Both used to be read out raw — a markdown report clipped
at a character count, often mid-word, in whatever language the agent wrote.
What the user wants to hear is what Jarvis would say about it: one short,
natural sentence, in the conversation's language.

So the report goes through the same bounded flash composer every other
readback uses (:mod:`jarvis.voice.contextual_readback`), with the report as
the only facts it may use. When that composer is unavailable or its answer is
rejected, the canned line is spoken instead — built from the report's leading
sentences, cut at a sentence or word boundary and never inside a word.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from loguru import logger

__all__ = ["compose_report", "plain_excerpt", "set_composer"]

#: Off the turn-critical path: a background result may take a little longer to
#: phrase than an acknowledgement, but it must not hang on a dead provider.
LATENCY_BUDGET_MS = 4000

#: How much of a report the composer is shown. The end of a long report is
#: where an agent states its result, so a longer one keeps both ends.
MAX_REPORT_CHARS = 1800

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKUP_RE = re.compile(r"(^|\s)(?:#{1,6}|[-*+>]|\d+[.)])\s+", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"[*_~]{1,3}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_composer: Any = None
_composer_lock = asyncio.Lock()


def _plain(text: str) -> str:
    """``text`` without markdown, code blocks and line structure."""
    value = _CODE_FENCE_RE.sub(" ", str(text or ""))
    value = _LINK_RE.sub(r"\1", value)
    value = _INLINE_CODE_RE.sub(r"\1", value)
    value = _MARKUP_RE.sub(r"\1", value)
    value = _EMPHASIS_RE.sub("", value)
    return " ".join(value.split())


def plain_excerpt(text: str, *, max_words: int = 40) -> str:
    """The leading sentences of ``text`` as plain speech, within ``max_words``.

    Cut at a sentence boundary when one fits, else at a word boundary — never
    inside a word, which is how the old 400-character cut ended readbacks.
    """
    plain = _plain(text)
    if not plain:
        return ""
    kept: list[str] = []
    total = 0
    for sentence in _SENTENCE_SPLIT_RE.split(plain):
        words = sentence.split()
        if not words:
            continue
        if total + len(words) > max_words:
            if not kept:
                kept.append(" ".join(words[:max_words]).rstrip(",;:") + " …")
            break
        kept.append(sentence.strip())
        total += len(words)
    return " ".join(kept)


def _report_facts(report: str) -> str:
    plain = _plain(report)
    if len(plain) <= MAX_REPORT_CHARS:
        return plain
    half = MAX_REPORT_CHARS // 2
    return f"{plain[:half]} … {plain[-half:]}"


async def _get_composer() -> Any:
    """The readback composer, built once and off the event loop.

    Building it reads ``jarvis.toml`` and constructs a provider client, so it
    runs in a thread; it never raises and falls back to canned-only mode.
    """
    global _composer
    if _composer is not None:
        return _composer
    async with _composer_lock:
        if _composer is None:
            from jarvis.brain.factory import build_readback_composer

            _composer = await asyncio.to_thread(build_readback_composer)
    return _composer


async def compose_report(
    *,
    instruction: str,
    language: str,
    canned: Callable[[], str],
    report: str,
    facts: dict[str, object] | None = None,
) -> str:
    """One short spoken sentence about ``report``, or the canned line.

    ``instruction`` describes the situation in English; ``report`` is the
    agent's own words, the only content the sentence may draw on. Never raises.
    """
    from jarvis.voice.contextual_readback import render_readback

    all_facts: dict[str, object] = dict(facts or {})
    if report.strip():
        all_facts["report"] = _report_facts(report)
    try:
        composer = await _get_composer()
    except Exception:  # noqa: BLE001 - a missing composer only costs the phrasing
        logger.opt(exception=True).warning("Report readback: composer unavailable")
        composer = None
    return await render_readback(
        composer,
        instruction=instruction,
        language=language,
        canned=canned,
        facts=all_facts,
        # The report is usually in another language than the speech (briefs are
        # English), so the content-overlap guard would reject every good
        # rephrasing; the digit and vocabulary guards still apply.
        honesty_bound=False,
        latency_budget_ms=LATENCY_BUDGET_MS,
    )


def set_composer(composer: Any) -> None:
    """Use ``composer`` from now on; ``None`` builds it again on next use."""
    global _composer
    _composer = composer
