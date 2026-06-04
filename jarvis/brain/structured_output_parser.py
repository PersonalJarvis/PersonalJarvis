"""Defensive JSON parser for Gemini structured-output responses.

The Computer-Use vision path routes structured-output requests through
BrainManager (Gemini-Pro-Vision). Even with Google's `responseSchema`
enforcement, providers occasionally emit almost-but-not-quite-valid JSON
(trailing commas, single quotes, code-fence prose around the object).
A single bad frame should not abort an in-flight mission.

This module is the single repair point. The happy path is a plain
`json.loads`. Only on `JSONDecodeError` do we lazy-import `json_repair`
to avoid paying its startup cost on every interpreter boot.

Hard constraints:
- No module-level `json_repair` import. Lazy-only.
- Never raises on parse failure — returns a structured envelope so the
  caller can decide whether to retry, escalate, or fall through.
- Does not mutate the input string. Diagnostics report char count only,
  never the payload itself (privacy: vision payloads may contain PII).

Return contract for `parse_gemini_json`:
    parsed: the decoded object on success (`status` in {"ok","repaired"}),
            otherwise `None`.
    status: "ok" — strict `json.loads` succeeded.
            "repaired" — `json_repair.loads` recovered usable JSON.
            "failed" — neither path produced a value (`error` is set).
    error:  one-line failure summary on `status == "failed"`, else `None`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

ParseStatus = Literal["ok", "repaired", "failed"]


@dataclass(frozen=True)
class ParsedJson:
    """Result envelope for a single structured-output parse attempt."""

    parsed: Any
    status: ParseStatus
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize for inclusion in a smoke / audit envelope.

        The `parsed` payload is included as-is; callers that need to
        strip PII should do so before persisting.
        """
        return {
            "parsed": self.parsed,
            "parse_status": self.status,
            "parse_error": self.error,
        }


def parse_gemini_json(response_text: str) -> ParsedJson:
    """Parse a Gemini structured-output response with a json-repair fallback.

    Args:
        response_text: Raw text body of the model response. Typically the
            top-level string returned by the BrainManager callback after a
            structured-output Gemini call. Must not be None.

    Returns:
        A `ParsedJson` envelope. Status `"ok"` means the strict parse
        succeeded; `"repaired"` means the fallback recovered the payload;
        `"failed"` means both paths gave up (`parsed=None`, `error` set).

    Never raises on parse failure. AttributeError / TypeError from a
    non-string input still propagate — that is a programmer error.
    """
    if response_text is None:
        return ParsedJson(parsed=None, status="failed", error="response_text is None")

    text = response_text.strip()
    if not text:
        return ParsedJson(parsed=None, status="failed", error="response_text is empty")

    try:
        return ParsedJson(parsed=json.loads(text), status="ok")
    except json.JSONDecodeError as exc:
        strict_error = f"{type(exc).__name__}: {exc.msg} at char {exc.pos}"

    # Lazy import: the happy path above never touches json_repair, so
    # interpreters that never hit a malformed payload pay zero startup
    # cost. (AC: "do not add `json-repair` as a top-level import".)
    try:
        import json_repair  # type: ignore[import-untyped]
    except ImportError as exc:
        return ParsedJson(
            parsed=None,
            status="failed",
            error=f"strict parse failed ({strict_error}); json_repair unavailable: {exc}",
        )

    try:
        repaired = json_repair.loads(text)
    except Exception as exc:  # json_repair has no narrow exception class
        return ParsedJson(
            parsed=None,
            status="failed",
            error=f"strict parse failed ({strict_error}); repair also failed: {type(exc).__name__}: {exc}",
        )

    # json_repair.loads returns "" for unrecoverable input rather than
    # raising. Treat that as a failed parse so callers can branch.
    if repaired == "" and text not in ("", '""'):
        return ParsedJson(
            parsed=None,
            status="failed",
            error=f"strict parse failed ({strict_error}); repair returned empty string for non-empty input",
        )

    return ParsedJson(parsed=repaired, status="repaired")


__all__ = ["ParsedJson", "ParseStatus", "parse_gemini_json"]
