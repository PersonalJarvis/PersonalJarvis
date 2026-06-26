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
    "gpt",
)

# Strict: "wechsel auf X", "switch to X", "wechsle zu X", "nutze X" — plus the
# natural-phrasing filler "[den/the/deinen] [brain ]provider/anbieter/modell"
# between the verb and the target (added 2026-06-08). Without that filler,
# "switch the brain provider to gemini" / "wechsel den Brain-Provider auf X"
# fell through to the router LLM, which (told it had "no authority" to switch)
# refused with "keine Berechtigung". A still-strict gate: it ends in a known
# provider alias with a word boundary, so harmless sentences never match.
# The German imperative paradigm needs two stems: "wechsel" and "wechsle".
_PROVIDER_PATTERN = re.compile(
    r"\b(?:wechsel[n]?|wechsle|switch(?:\s+to)?|benutze?|nutze|use|nimm)"
    r"(?:\s+(?:den|die|das|der|the|deinen|deine|dein|meinen|meine|mein|my))?"
    r"(?:\s+(?:brain[-\s]*provider|provider|anbieter|sprach[-\s]*modell|modell|model))?"
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

# Reply-language switch (added 2026-06-22, broadened after forensic #2). A
# config change like "stell auf Englisch um" / "antworte auf Spanisch" /
# "respond in German" must be a DETERMINISTIC, provider-independent action (set
# brain.reply_language directly) — not an LLM tool-choice, never a worker
# mission. A language word ALONE never matches; it needs an intent marker:
#   (a) an unambiguous CHANGE verb (umstell/umänder/wechsel/änder/switch/change),
#   (b) a bare imperative SPEAK verb (sprich/speak <lang>), or
#   (c) an OUTPUT verb (antwort/respond/reply/answer/rede/set/stell/mach) plus a
#       directional preposition (auf/in/zu/to <lang>).
# So "wie heißt das auf Englisch?", "ich spreche Englisch", "auf Deutsch klingt
# das besser", "erzähl mir was auf Englisch" still fall through to the brain.
_LANG_ALIASES: dict[str, str] = {
    "englisch": "en", "english": "en",
    "deutsch": "de", "german": "de",
    "spanisch": "es", "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
    "automatisch": "auto", "automatik": "auto", "automatic": "auto", "auto": "auto",
}
# (a) Unambiguous change verbs — incl. German separable forms ("umändern",
# "umstellen") whose "um" prefix breaks a plain "\bänder" boundary.
_LANG_CHANGE_VERB = re.compile(
    r"\b(?:um(?:stell|schalt|änder|aender|stellung)\w*|wechsel\w*|wechsle"
    r"|änder\w*|aender\w*|switch\w*|change\w*)\b",
    re.IGNORECASE,
)
# (b) Imperative speak verbs — match directly (no preposition needed):
# "sprich Englisch", "speak English". German "spreche/spricht" (statements) are
# intentionally NOT matched.
_LANG_IMPERATIVE_SPEAK = re.compile(r"\b(?:sprich|speak\w*)\b", re.IGNORECASE)
# (c) Reply / speech verbs — need a directional preposition to anchor the
# language as Jarvis's reply target. Broad creation verbs such as "mach(en)" are
# intentionally excluded: "make an HTML file about what comes up in English" is
# an artifact request, not a persistent reply-language switch.
_LANG_OUTPUT_VERB = re.compile(
    r"\b(?:antwort\w*|respond\w*|repl(?:y|ies)|answer\w*|rede|reden|set|stell\w*"
    r")\b",
    re.IGNORECASE,
)
_LANG_PREP = re.compile(r"\b(?:auf|zu|to|in|on)\b", re.IGNORECASE)


def _match_language_switch(t: str) -> str | None:
    code: str | None = None
    for word, c in _LANG_ALIASES.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            code = c
            break
    if code is None:
        return None
    if _LANG_CHANGE_VERB.search(t):
        return code
    if _LANG_IMPERATIVE_SPEAK.search(t):
        return code
    if _LANG_OUTPUT_VERB.search(t) and _LANG_PREP.search(t):
        return code
    return None


# Sub-agent / Heavy-Task-worker provider switch (added 2026-06-22). Sibling of
# the main provider_switch, but for [brain.sub_jarvis].provider — the worker
# that runs heavy missions, NOT the router brain. The gate stays pure: it only
# recognises the intent + the spoken provider word; the manager handler maps it
# to a canonical subagent slug, validates, and persists via the 3-layer writer
# (config-soll pinned) so the drift-guard cannot revert it. A sub-agent
# QUALIFIER is required, so a bare "switch to gemini" still means the main brain.
_SUBAGENT_QUALIFIER = re.compile(
    r"\b(?:sub[-\s]?agent|subagent|sub[-\s]?jarvis|subjarvis|worker|helfer|helper)\b",
    re.IGNORECASE,
)
# Longer variants first so "openai-codex" wins over "openai".
_SUBAGENT_PROVIDER_WORDS = (
    "openai-codex", "openrouter", "antigravity", "chatgpt", "anthropic",
    "claude", "gemini", "openai", "codex", "gpt",
)
_SUBAGENT_SWITCH_VERB = re.compile(
    r"\b(?:wechsel[n]?|wechsle|umstell\w*|umschalt\w*|stell\w*|set|switch|change|nimm|mach)\b",
    re.IGNORECASE,
)
_SUBAGENT_PREP = re.compile(r"\b(?:auf|zu|to)\b", re.IGNORECASE)
_SUBAGENT_PROVIDER_NOUN = re.compile(r"\b(?:provider|anbieter)\b", re.IGNORECASE)


def _match_subagent_switch(t: str) -> str | None:
    if not _SUBAGENT_QUALIFIER.search(t):
        return None
    for word in _SUBAGENT_PROVIDER_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", t):
            # An explicit change verb is an unambiguous command. Otherwise accept
            # only "... provider auf/to <X>" (a noun + preposition) so a STATEMENT
            # like "der Sub-Agent läuft auf Gemini" (no verb, no 'provider' word)
            # falls through to the brain instead of silently switching.
            if _SUBAGENT_SWITCH_VERB.search(t):
                return word
            if _SUBAGENT_PREP.search(t) and _SUBAGENT_PROVIDER_NOUN.search(t):
                return word
            return None
    return None


@dataclass(frozen=True)
class VoiceCommandMatch:
    """Result of a gate match.

    - kind: Class of the recognised command.
    - target: provider alias (provider_switch), the reply-language code
      de/en/es/auto (language_switch), or the spoken sub-agent provider word
      (subagent_switch — the manager maps it to a canonical slug).
    """
    kind: Literal[
        "provider_switch",
        "subagent_switch",
        "language_switch",
        "cancel",
        "depth_deep",
        "depth_fast",
    ]
    target: str = ""


def match_voice_command(text: str) -> VoiceCommandMatch | None:
    """Check strictly for meta-commands. Returns None if none match."""
    t = (text or "").strip().lower()
    if not t:
        return None

    # Cancel first (takes priority — if "stopp" fires, it is always urgent).
    if _CANCEL_PATTERN.search(t):
        return VoiceCommandMatch(kind="cancel")

    # Sub-agent provider switch BEFORE the main provider switch: a sub-agent
    # qualifier ("switch the SUB-AGENT provider to X") must target the worker,
    # not the router brain.
    sub = _match_subagent_switch(t)
    if sub:
        return VoiceCommandMatch(kind="subagent_switch", target=sub)

    # Provider-Switch
    m = _PROVIDER_PATTERN.search(t)
    if m:
        return VoiceCommandMatch(kind="provider_switch", target=m.group("provider"))

    # Reply-language switch (deterministic — never an LLM tool-choice / spawn).
    lang = _match_language_switch(t)
    if lang:
        return VoiceCommandMatch(kind="language_switch", target=lang)

    # Depth-Override
    for p in _DEEP_PATTERNS:
        if p in t:
            return VoiceCommandMatch(kind="depth_deep")
    for p in _FAST_PATTERNS:
        if p in t:
            return VoiceCommandMatch(kind="depth_fast")

    return None
