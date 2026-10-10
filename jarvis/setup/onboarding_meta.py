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

CURRENT_TERMS_VERSION = "1.1"

# Canonical step order — must match SETUP_STEP_IDS in the frontend
# (components/onboarding/setup/setupSteps.ts). Setup is one window with three
# steps — the assistant's name (which is its wake word), connecting an AI
# (subscriptions and an API key), and how the user talks to it — followed by
# "tour": the user's pet walks the real app and explains each section. The
# walk is one persisted step; onboarding completes, with its one unconditional
# fresh restart (onboarding_routes._schedule_fresh_restart), when it ends, so
# the wake-word and macOS permission changes made during setup take effect.
# There is NO permissions step (just-in-time permissions, AP-35): nothing asks
# macOS for anything during first run except where a switch IS the gesture.
# A stored legacy step id (e.g. "permissions") resumes at "name" (frontend
# ``resumeStep``); the backend never validates the stored id.
ONBOARDING_STEPS: list[str] = [
    "name",
    "connect",
    "voice",
    "tour",
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
    f"Personal Jarvis — Terms of Use & Disclaimer (v{CURRENT_TERMS_VERSION})\n\n"
    'This software is provided free and open-source, "as is", without warranty. '
    "You are solely responsible for how you use it, including your choice of activation "
    "word and compliance with applicable trademark law. Not affiliated with any rights "
    "holder. Personal Jarvis is the software project's name, separate from the "
    "assistant name and activation word you choose. Working data is stored on the "
    "computer or server running the app; remote and connected features send data to "
    "their configured services. The shipped Google and Slack sign-in clients use the "
    "project-run token.personaljarvis.ai OAuth service, hosted on Cloudflare, for token "
    "exchange and refresh. It receives authorization codes, PKCE verifiers, client and "
    "redirect information, and refresh tokens, and handles provider token responses. "
    "Slack's browser callback also passes through this service. These terms do not "
    "guarantee its server-side logging, storage, or retention behavior. Connected "
    "services have their own terms; your accounts, keys, and costs are your "
    "responsibility. Use microphone recording lawfully, including any required "
    "consent. To the maximum extent permitted by law, the authors and contributors "
    "are not liable for claims, damage, or loss arising from use. English is "
    "authoritative. Acceptance is recorded locally. The full terms document could "
    "not be loaded from disk."
)


def read_terms_text() -> str:
    """Return the canonical English Terms text. Best-effort: never raises."""
    import sysconfig

    # A wheel can be installed in a virtual environment or with pip --user.
    data_roots = (
        sysconfig.get_path("data"),
        sysconfig.get_path("data", scheme=sysconfig.get_preferred_scheme("user")),
    )
    installed_paths = (
        Path(root) / "share" / "personal-jarvis" / "docs" / "legal" / "TERMS.md"
        for root in data_roots
    )
    for terms_path in dict.fromkeys((_TERMS_PATH, *installed_paths)):
        try:
            return terms_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("read_terms_text: cannot read %s: %s", terms_path, exc)
    logger.warning("read_terms_text: canonical document unavailable; using fallback")
    return _TERMS_FALLBACK


__all__ = [
    "CURRENT_TERMS_VERSION",
    "ONBOARDING_STEPS",
    "WAKE_WORD_LEGAL_REFERENCES",
    "read_terms_text",
]
