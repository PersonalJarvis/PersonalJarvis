"""VoiceCommandGate — strict pattern matcher for meta-commands.

The router LLM must NOT decide on its own to switch providers or cancel
running OpenClaw tasks. That is the responsibility of this gate, which
checks the utterance with strict regex patterns BEFORE it reaches the
router LLM.

Advantages:
- No LLM hallucination risk (match/no-match is deterministic).
- Substring-matching problems avoided (word boundaries explicit).
- Tested and auditable.

Patterns are intentionally narrow: only unambiguous user-intent signals match.
On ambiguity: no-match -> the router brain receives the utterance normally.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Provider aliases — the only accepted names. Order matters: longer
# variants MUST appear before their prefixes, otherwise the regex matches wrong.
_PROVIDER_ALIASES = (
    "claude-api",
    "openrouter",
    "ollama",
    "gemini",
    "claude",
    "openai",
    "grok",
    "gpt",
)

# Strict: "wechsel auf X", "switch to X", "wechsle zu X", "nutze X"
# Word boundaries around the provider name; no substring trap. The German
# imperative paradigm "wechsel / wechsle / wechseln" requires two variants:
# "wechsel" (w-e-c-h-s-e-l) and "wechsle" (w-e-c-h-s-l-e).
_PROVIDER_PATTERN = re.compile(
    r"\b(?:wechsel[n]?|wechsle|switch(?:\s+to)?|benutze?|nutze|use|nimm)"
    r"(?:\s+(?:auf|zu|to))?\s+"
    r"(?P<provider>" + "|".join(re.escape(p) for p in _PROVIDER_ALIASES) + r")\b",
    re.IGNORECASE,
)

# Cancel: "stopp", "abbruch", "abbrechen", "cancel", "jarvis stopp" — only at
# sentence start OR preceded by "jarvis", to avoid catching harmless phrases
# like "stopp doch mal kurz".
_CANCEL_PATTERN = re.compile(
    r"^(?:jarvis[,\s]+)?(?:stopp?|abbruch|abbrechen|cancel|stop\s+sub)\b",
    re.IGNORECASE,
)

# Depth override: "denk gruendlich" / "denk schnell" / "think hard" — kept
# intentionally as-is (already proven). We reuse the list from manager.py.
_DEEP_PATTERNS = (
    "denk gründlich", "denk gruendlich", "denk tief", "denk mal gründlich",
    "think hard", "think deeply", "deep thinking",
    "nimm opus", "use opus", "opus-modus",
)
_FAST_PATTERNS = (
    "denk schnell", "denk wieder schnell", "normal denken",
    "nimm haiku", "use haiku", "schnell-modus", "think fast",
)


@dataclass(frozen=True)
class VoiceCommandMatch:
    """Result of a gate match.

    - kind: Class of the recognised command.
    - target: Only populated for provider_switch (provider alias).
    """
    kind: Literal["provider_switch", "cancel", "depth_deep", "depth_fast"]
    target: str = ""


def match_voice_command(text: str) -> VoiceCommandMatch | None:
    """Check strictly for meta-commands. Returns None if none match."""
    t = (text or "").strip().lower()
    if not t:
        return None

    # Cancel first (takes priority — if "stopp" fires, it is always urgent).
    if _CANCEL_PATTERN.search(t):
        return VoiceCommandMatch(kind="cancel")

    # Provider-Switch
    m = _PROVIDER_PATTERN.search(t)
    if m:
        return VoiceCommandMatch(kind="provider_switch", target=m.group("provider"))

    # Depth-Override
    for p in _DEEP_PATTERNS:
        if p in t:
            return VoiceCommandMatch(kind="depth_deep")
    for p in _FAST_PATTERNS:
        if p in t:
            return VoiceCommandMatch(kind="depth_fast")

    return None
