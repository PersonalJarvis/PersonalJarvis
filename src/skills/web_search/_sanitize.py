"""Query sanitisation for the web-search skill.

Sanitises raw user input before it crosses the trust boundary into the
LLM-routed search backend. The contract is purely defensive — every public
function is total (never raises on string input) and its output satisfies a
fixed set of invariants checked by the property test in
``tests/skills/test_web_search_sanitize.py``.

Invariants (post-sanitise):
    * length ≤ ``MAX_QUERY_LEN``
    * no ASCII control chars except plain space (0x20)
    * NFKC-normalised
    * no leading / trailing whitespace
    * no occurrence of any token in ``INJECTION_TOKENS`` (case-insensitive)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

MAX_QUERY_LEN: Final[int] = 512

INJECTION_TOKENS: Final[tuple[str, ...]] = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "you are now",
    "act as system",
    "<|im_start|>",
    "<|im_end|>",
    "</system>",
    "<system>",
)

_CONTROL_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


class QueryRejectedError(ValueError):
    """Raised when a query cannot be sanitised into a safe form."""


def sanitize_query(raw: str) -> str:
    """Return a safe, normalised search query.

    Total over ``str`` input — never raises for a ``str``, only for non-str
    (``TypeError``) or for strings that resolve to empty after stripping
    (``QueryRejectedError``).
    """
    if not isinstance(raw, str):
        raise TypeError(f"sanitize_query expects str, got {type(raw).__name__}")

    normalised = unicodedata.normalize("NFKC", raw)
    stripped = _CONTROL_CHARS_RE.sub(" ", normalised)
    collapsed = _WHITESPACE_RUN_RE.sub(" ", stripped).strip()

    if not collapsed:
        raise QueryRejectedError("query is empty after sanitisation")

    lowered = collapsed.lower()
    for token in INJECTION_TOKENS:
        if token in lowered:
            raise QueryRejectedError(f"query contains injection token: {token!r}")

    if len(collapsed) > MAX_QUERY_LEN:
        collapsed = collapsed[:MAX_QUERY_LEN].rstrip()

    return collapsed


def is_safe(raw: str) -> bool:
    """Predicate form of :func:`sanitize_query` — never raises."""
    try:
        sanitize_query(raw)
    except (QueryRejectedError, TypeError):
        return False
    return True
