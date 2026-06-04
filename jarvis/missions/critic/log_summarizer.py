"""Log triage for the Critic prompt.

Pattern from Research-Doc §F.5: head30 + tail50 + errors_grep. Optional
Haiku triage for very large logs (>50 KB). Pure-Python fallback when no
BrainManager is available — the Critic must never block due to triage setup.

Token budget: tail-only slice ~= 4 KB, triage output ~400 tokens. Both
fit comfortably within the 8k-Sonnet context budget of the Critic call.
"""
from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable, Final

from jarvis.missions.stream_evidence import extract_stream_evidence

logger = logging.getLogger(__name__)

HEAD_LINES: Final[int] = 30
TAIL_LINES: Final[int] = 50
# Deliberately without word boundaries: `\bError\b` does NOT match "ValueError" /
# "RuntimeError" / "TypeError", because the preceding word character provides no
# boundary. For log triage we want to collect exactly these Python exception names,
# hence the broader match.
ERROR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(Error|Exception|Traceback|FAIL|failed|panic|fatal)"
)
TRIAGE_THRESHOLD_CHARS: Final[int] = 50_000
DEFAULT_MAX_CHARS: Final[int] = 4_000


# Type alias for the optional Haiku triage function (e.g. BrainManager.summarize).
TriageFn = Callable[[str], Awaitable[str]]


def _head_tail_grep(log_text: str) -> str:
    """Pure-Python pre-triage: head30 + tail50 + errors_grep, deduplicated."""
    if not log_text.strip():
        return ""

    lines = log_text.splitlines()

    head = lines[:HEAD_LINES]
    tail = lines[-TAIL_LINES:] if len(lines) > TAIL_LINES else lines[HEAD_LINES:]

    seen: set[str] = set()
    error_lines: list[str] = []
    for line in lines:
        if ERROR_PATTERN.search(line) and line not in seen:
            seen.add(line)
            error_lines.append(line)

    sections: list[str] = []
    if head:
        sections.append("=== HEAD (first 30 lines) ===")
        sections.extend(head)
    if error_lines:
        sections.append("")
        sections.append("=== ERRORS / TRACEBACKS (grep) ===")
        sections.extend(error_lines)
    if tail and tail != head:
        sections.append("")
        sections.append("=== TAIL (last 50 lines) ===")
        sections.extend(tail)

    return "\n".join(sections)


def _evidence_header(log_text: str) -> str:
    """Tool-call evidence + final answer, parsed from a claude stream.

    Returns "" for non-stream logs. This guarantees the critic always SEES that
    real tools ran + the worker's answer — even when the rich tool_result frames
    fall outside the head/tail/grep window (the 2026-05-24 read-only failure:
    inherited SessionStart hook frames pushed the github result past the cap).
    """
    ev = extract_stream_evidence(log_text)
    if not ev.tool_calls and not ev.final_answer:
        return ""
    parts = ["=== TOOL EVIDENCE (parsed from stream) ==="]
    if ev.tool_calls:
        parts.append("tools_invoked: " + ", ".join(ev.tool_calls))
    for res in ev.tool_results[:4]:
        parts.append("tool_result: " + res[:300])
    if ev.final_answer:
        parts.append("worker_final_answer: " + ev.final_answer[:800])
    return "\n".join(parts)


async def summarize_log(
    log_text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    triage_fn: TriageFn | None = None,
) -> str:
    """Prepare the log for the Critic prompt.

    Args:
        log_text: Raw stream.jsonl or stderr.log content.
        max_chars: Hard cap for the output (default 4 KB ~ 1k tokens).
        triage_fn: Optional `async fn(text) -> summary`. When provided AND
            `len(log_text) > TRIAGE_THRESHOLD_CHARS`, the triage function is
            called first (typically Haiku pre-summarize). Falls back to
            pure-Python head/tail/grep on exception.

    Returns:
        String <= max_chars; empty string when log_text is empty.
    """
    if not log_text.strip():
        return ""

    triaged: str
    if triage_fn is not None and len(log_text) > TRIAGE_THRESHOLD_CHARS:
        try:
            triaged = await triage_fn(log_text)
        except Exception:  # noqa: BLE001 — triage must never block the Critic.
            logger.warning(
                "summarize_log: triage_fn raised; falling back to head/tail/grep",
                exc_info=True,
            )
            triaged = _head_tail_grep(log_text)
    else:
        triaged = _head_tail_grep(log_text)

    # Tool-evidence header is built FIRST and never truncated, so the critic
    # always sees which tools ran + the worker's answer even when the raw
    # head/tail body would otherwise crowd them out.
    header = _evidence_header(log_text)
    if header:
        body_budget = max(0, max_chars - len(header) - 64)
        body = triaged[:body_budget]
        out = header + "\n\n=== RAW LOG (head/tail/grep) ===\n" + body
        if len(triaged) > body_budget:
            out += f"\n... [truncated at {body_budget} chars]"
        return out

    if len(triaged) > max_chars:
        # Hard truncate with a notice; otherwise large stack traces spam the prompt.
        cut = triaged[:max_chars]
        return cut + f"\n... [truncated at {max_chars} chars]"
    return triaged


__all__ = [
    "DEFAULT_MAX_CHARS",
    "ERROR_PATTERN",
    "HEAD_LINES",
    "TAIL_LINES",
    "TRIAGE_THRESHOLD_CHARS",
    "TriageFn",
    "summarize_log",
]
