"""Regex-only secret/PII guard for wiki page bodies (AP-2).

Wiki pages now persist deliberately (the curator's ``create``/``update``
operations land on disk and are full-text indexed). A page body that
contains an API key, bearer token, password, or other long opaque
credential must never be written: it would leak the secret into the
vault, the FTS index, and any ``wiki-recall`` voice readback.

This module is the deterministic, **regex-only** gate (no LLM call —
the write path must stay fast and offline; cf. AP-11 on the voice
path). It is a pure function: ``contains_secret(body) -> bool`` plus a
diagnostic ``find_secrets(body) -> list[str]`` returning the names of
the patterns that fired (for logging, never the matched value).

The patterns mirror the credential shapes enumerated in
``jarvis/core/config.py`` (``PROVIDER_SECRET_CANDIDATES``: OpenAI
``sk-``/``sk-proj-``, Google/xAI keys, bearer tokens) and the
long-base64 guard in ``jarvis/brain/output_filter.py``
(``LONG_BASE64_RE``).

Deliberately conservative: a few prose words ("the password is on the
sticky note") trip the ``password:`` rule only when followed by a
value-shaped token, so ordinary biographical notes pass. False
positives are cheaper than a leaked credential — a blocked page is
reported, never silently dropped.
"""
from __future__ import annotations

import re

# --- credential shapes -------------------------------------------------
# 1) OpenAI-style keys: sk-, sk-proj-, sk-ant-, etc. >=20 trailing chars.
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-|ant-|or-|live-)?[A-Za-z0-9_-]{20,}\b")
# 2) Generic provider keys: AIza… (Google), xai-…, gsk_… (Groq), ghp_/gho_ (GitHub).
_PROVIDER_KEY_RE = re.compile(
    r"\b(?:AIza[0-9A-Za-z_-]{30,}"
    r"|xai-[A-Za-z0-9]{20,}"
    r"|gsk_[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,})\b"
)
# 3) Bearer / Authorization tokens.
_BEARER_RE = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{16,}\b")
_AUTH_HEADER_RE = re.compile(
    r"(?im)^\s*authorization\s*[:=]\s*\S{12,}\s*$"
)
# 4) Inline "api_key = …" / "password: …" / "secret = …" / "token: …"
#    with a value-shaped token (>=8 non-space chars) right after.
_LABELLED_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|pwd|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
    r"\s*[:=]\s*"
    r"['\"]?[^\s'\"]{8,}"
)
# 5) JWT (three base64url segments separated by dots). The two eyJ-prefixed
#    payload segments are specific enough that even a short/truncated
#    signature segment (>=3 chars) should still be treated as a credential.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\b")
# 6) Long opaque secrets: >=64 contiguous hex, or >=64 contiguous base64.
#    Mirrors output_filter.LONG_BASE64_RE (>=200) but tighter, because a
#    page body should never legitimately contain such a run. The hex
#    threshold is 64 (SHA-256 length), NOT 40: a 40-char run is a git
#    SHA-1 commit hash, which appears legitimately in biographical wiki
#    prose about the user's projects and must never be refused.
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{64,}\b")
_LONG_B64_RE = re.compile(r"\b[A-Za-z0-9+/]{64,}={0,2}\b")
# 7) Private-key PEM headers.
_PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", _OPENAI_KEY_RE),
    ("provider_key", _PROVIDER_KEY_RE),
    ("bearer_token", _BEARER_RE),
    ("authorization_header", _AUTH_HEADER_RE),
    ("labelled_secret", _LABELLED_SECRET_RE),
    ("jwt", _JWT_RE),
    ("pem_private_key", _PEM_RE),
    ("long_hex_secret", _LONG_HEX_RE),
    ("long_base64_secret", _LONG_B64_RE),
)


def find_secrets(body: str) -> list[str]:
    """Return the names of every secret pattern that matches ``body``.

    Pure function. Returns the *pattern names* (e.g. ``"openai_key"``),
    never the matched substring — the caller logs these names without
    ever echoing the credential itself.
    """
    if not body:
        return []
    return [name for name, pat in _PATTERNS if pat.search(body)]


def contains_secret(body: str) -> bool:
    """``True`` if ``body`` matches any credential/secret shape."""
    if not body:
        return False
    return any(pat.search(body) for _, pat in _PATTERNS)


__all__ = ["contains_secret", "find_secrets"]
