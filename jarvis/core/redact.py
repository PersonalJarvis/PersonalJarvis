"""Redact-and-cap helper for telemetry previews (decision-log / Run Inspector).

The Session-Decision-Log persists, per action, a short preview of what a tool
returned (``ActionExecuted.output_preview``) and the brain's natural-language
rationale (``ActionProposed.rationale``). Both are derived from live data that
could *theoretically* echo a credential (a tool that printed an API key, a model
that quoted a token back), and both land on disk in ``data/sessions.db`` and in
the local Markdown diary. So neither may be persisted raw.

``safe_preview`` is the single gate every such preview passes before it touches
an event/the DB/the diary: stringify -> mask credential shapes -> cap length.

It is **regex-only** (no LLM call) so it is safe to run inline on the bus-publish
path without adding latency (cf. AP-11 on the voice path). The credential shapes
mirror ``jarvis/memory/wiki/secret_guard.py`` (the wiki write-guard) and the
provider key shapes in ``jarvis/core/config.py`` — kept here as an independent
copy because this module lives in the lowest layer (``jarvis.core``) and must not
import upward into ``jarvis.memory``. The two differ in intent: ``secret_guard``
*detects and blocks*; this *masks and keeps* a usable, secret-free preview.
"""
from __future__ import annotations

import re
from typing import Any

# Default cap for a persisted preview. Big enough to be useful when scrolling
# back through the decision log, small enough that a tool cannot smuggle a large
# blob (a screenshot data-URI, a giant JSON dump) into the session DB / diary.
DEFAULT_PREVIEW_CHARS = 2048

# --- credential shapes (mirror of secret_guard._PATTERNS, for substitution) ---
_REDACTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # OpenAI-style keys: sk-, sk-proj-, sk-ant-, sk-or-, sk-live-, >=20 trailing.
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-|or-|live-)?[A-Za-z0-9_-]{20,}\b")),
    # Provider keys: AIza… (Google), xai-…, gsk_… (Groq), gh[pousr]_ (GitHub).
    (
        "provider_key",
        re.compile(
            r"\b(?:AIza[0-9A-Za-z_-]{30,}"
            r"|xai-[A-Za-z0-9]{20,}"
            r"|gsk_[A-Za-z0-9]{20,}"
            r"|gh[pousr]_[A-Za-z0-9]{30,})\b"
        ),
    ),
    # Bearer / Authorization tokens.
    ("bearer_token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{16,}\b")),
    (
        "authorization_header",
        re.compile(r"(?im)^\s*authorization\s*[:=]\s*\S{12,}\s*$"),
    ),
    # Inline "api_key = …" / "password: …" / "secret = …" / "token: …".
    (
        "labelled_secret",
        re.compile(
            r"(?i)(\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|pwd"
            r"|access[_-]?token|auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*)"
            r"['\"]?[^\s'\"]{8,}"
        ),
    ),
    # JWT (three base64url segments separated by dots).
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\b")),
    # Private-key PEM headers (mask the whole block start marker).
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    # Long opaque secrets: >=64 contiguous hex (SHA-256 is 64, a git SHA-1 is 40
    # and stays untouched) or >=64 contiguous base64.
    ("long_hex_secret", re.compile(r"\b[0-9a-fA-F]{64,}\b")),
    ("long_base64_secret", re.compile(r"\b[A-Za-z0-9+/]{64,}={0,2}\b")),
)

# The ``labelled_secret`` pattern keeps its label group (so "api_key=" stays
# readable) and only masks the value; every other pattern masks the whole match.
_KEEP_PREFIX = frozenset({"labelled_secret"})


def redact_secrets(text: str) -> str:
    """Mask credential-shaped substrings, leaving the rest of ``text`` intact.

    Pure function. Each credential shape is replaced by ``<redacted:NAME>`` so a
    human reading the decision log still sees *that* a secret was there (and of
    what kind) without the value. The ``labelled_secret`` shape keeps its label
    (``api_key=<redacted:labelled_secret>``) for readability.
    """
    if not text:
        return text
    out = text
    for name, pat in _REDACTORS:
        replacement = (
            (r"\1" + f"<redacted:{name}>") if name in _KEEP_PREFIX else f"<redacted:{name}>"
        )
        out = pat.sub(replacement, out)
    return out


def safe_preview(value: Any, *, max_chars: int = DEFAULT_PREVIEW_CHARS) -> str:
    """Stringify ``value`` -> mask credential shapes -> cap to ``max_chars``.

    The one gate every persisted telemetry preview passes. Never raises: a value
    whose ``str()`` blows up degrades to its type name. The cap appends a
    ``… (+N more chars)`` marker so a truncated preview is honest about being
    truncated rather than silently cut.
    """
    if value is None:
        return ""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 — a preview must never crash the caller
        return f"<{type(value).__name__}>"
    text = redact_secrets(text)
    if max_chars > 0 and len(text) > max_chars:
        dropped = len(text) - max_chars
        text = text[:max_chars] + f"… (+{dropped} more chars)"
    return text


__all__ = ["DEFAULT_PREVIEW_CHARS", "redact_secrets", "safe_preview"]
