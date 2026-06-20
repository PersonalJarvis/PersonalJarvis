"""Static metadata for the first-time onboarding guide.

Single source of truth for the shipped Terms version, the canonical step
order, and the informational trademark reference links shown on the
wake-word step. There is deliberately NO denylist — the user chooses any
activation word and self-certifies responsibility (see docs/legal/TERMS.md).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CURRENT_TERMS_VERSION = "1.0"

# Canonical step order — must match the frontend step REGISTRY keys.
ONBOARDING_STEPS: list[str] = [
    "welcome",
    "terms",
    "language",
    "wake-word",
    "api-keys",
    "mic-test",
    "persona-theme",
    "system-style",
    "finish",
]

# Informational only; not exhaustive and possibly out of date (stated in the UI).
WAKE_WORD_LEGAL_REFERENCES: list[dict[str, str]] = [
    {"label": "EUIPO trademark search (EU)", "url": "https://euipo.europa.eu/eSearch/"},
    {"label": "USPTO trademark search (US)", "url": "https://www.uspto.gov/trademarks/search"},
    {"label": "WIPO Global Brand Database", "url": "https://branddb.wipo.int/"},
    {"label": "DPMA register (Germany)", "url": "https://register.dpma.de/"},
]

# docs/legal/TERMS.md relative to the repo root (this file: jarvis/setup/onboarding_meta.py).
_TERMS_PATH = Path(__file__).resolve().parents[2] / "docs" / "legal" / "TERMS.md"

_TERMS_FALLBACK = (
    "Personal Jarvis — Terms of Use & Disclaimer (v1.0)\n\n"
    "This software is provided free and open-source, \"as is\", without warranty. "
    "You are solely responsible for how you use it, including your choice of activation "
    "word and compliance with applicable trademark law. Not affiliated with any rights "
    "holder. The terms document could not be loaded from disk."
)


def read_terms_text() -> str:
    """Return the canonical English Terms text. Best-effort: never raises."""
    try:
        return _TERMS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("read_terms_text: cannot read %s: %s", _TERMS_PATH, exc)
        return _TERMS_FALLBACK


__all__ = [
    "CURRENT_TERMS_VERSION",
    "ONBOARDING_STEPS",
    "WAKE_WORD_LEGAL_REFERENCES",
    "read_terms_text",
]
