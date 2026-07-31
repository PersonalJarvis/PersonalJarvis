"""VoiceCommandGate — strict pattern matcher for meta-commands.

The router LLM must NOT decide on its own to switch providers or cancel
running Jarvis-Agent tasks. That is the responsibility of this gate, which
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
    "grok",
    "anthropic",
    "chatgpt",
    "ollama",
    "gemini",
    "claude",
    "openai",
    "gpt",
)

# Strict: "wechsel auf X", "switch to X", "wechsle zu X", "nutze X" — plus the
# natural-phrasing filler "[den/the/deinen] [brain ]provider/anbieter/modell"
# between the verb and the target (added 2026-06-08). Without that filler,
# "switch the brain provider to gemini" / "wechsel den Brain-Provider auf X"  # i18n-allow: quoted German input example
# fell through to the router LLM, which (told it had "no authority" to switch)
# refused with "keine Berechtigung" ("no permission"). A still-strict gate: it ends in a known  # i18n-allow: quoted German runtime phrase
# provider alias with a word boundary, so harmless sentences never match.
# The German imperative paradigm needs two stems: "wechsel" and "wechsle".
_PROVIDER_PATTERN = re.compile(
    r"\b(?:wechsel[n]?|wechsle|änder\w*|aender\w*|setz\w*|stell\w*"  # i18n-allow: German provider-switch input-matching data
    r"|switch(?:\s+to)?|benutze?|nutze|use|nimm)"
    r"(?:\s+(?:den|die|das|der|the|deinen|deine|dein|meinen|meine|mein|my))?"  # i18n-allow: German provider-switch input-matching data
    r"(?:\s+(?:brain[-\s]*provider|provider|anbieter|sprach[-\s]*modell|modell|model))?"
    # Optional "von/from <source>" so "switch FROM gemini TO openai" targets the
    # destination after auf/zu/to, not the source (forensic 2026-06-27).
    r"(?:\s+(?:von|from)\s+(?:" + "|".join(re.escape(p) for p in _PROVIDER_ALIASES) + r"))?"
    r"(?:\s+(?:auf|zu|to))?\s+"
    r"(?P<provider>" + "|".join(re.escape(p) for p in _PROVIDER_ALIASES) + r")\b",
    re.IGNORECASE,
)

# Cancel: "stopp", "abbruch", "abbrechen", "cancel", "jarvis stopp" — only at  # i18n-allow: quoted German cancel-command examples
# sentence start OR preceded by "jarvis", to avoid catching harmless phrases
# like "stopp doch mal kurz".
_CANCEL_PATTERN = re.compile(
    r"^(?:jarvis[,\s]+)?(?:stopp?|abbruch|abbrechen|cancel|stop\s+sub|halt)\b",  # i18n-allow: German cancel-command input-matching data
    re.IGNORECASE,
)

# Depth override: "denk gruendlich" / "denk schnell" / "think hard" — kept
# intentionally as-is (already proven). We reuse the list from manager.py.
_DEEP_PATTERNS = (
    "denk gründlich", "denk gruendlich", "denk tief", "denk mal gründlich",  # i18n-allow: German depth-override input-matching data
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
#   (a) an unambiguous CHANGE verb (umstell/umänder/wechsel/änder/switch/change),  # i18n-allow: quoted German input-matching verb stems
#   (b) a bare imperative SPEAK verb (sprich/speak <lang>), or
#   (c) an OUTPUT verb (antwort/respond/reply/answer/rede/set/stell/mach) plus a
#       directional preposition (auf/in/zu/to <lang>).
# So "wie heißt das auf Englisch?", "ich spreche Englisch", "auf Deutsch klingt  # i18n-allow: quoted German input examples that must NOT match
# das besser", "erzähl mir was auf Englisch" still fall through to the brain.  # i18n-allow: quoted German input example
_LANG_ALIASES: dict[str, str] = {
    "englisch": "en", "english": "en",
    "deutsch": "de", "german": "de",
    "spanisch": "es", "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
    "automatisch": "auto", "automatik": "auto", "automatic": "auto", "auto": "auto",
}
# (a) Unambiguous change verbs — incl. German separable forms ("umändern",  # i18n-allow: quoted German verb-form examples
# "umstellen") whose "um" prefix breaks a plain "\bänder" boundary.  # i18n-allow: quoted German verb-form example
_LANG_CHANGE_VERB = re.compile(
    r"\b(?:um(?:stell|schalt|änder|aender|stellung)\w*|wechsel\w*|wechsle"  # i18n-allow: German change-verb input-matching data
    r"|änder\w*|aender\w*|switch\w*|change\w*)\b",  # i18n-allow: German change-verb input-matching data
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
#: How far from the spoken language word an ingredient may sit and still belong
#: to the same command. Real switches are compact — a spoken
#: "stell auf Englisch um" / "antworte ab jetzt auf Englisch" /  # i18n-allow: quoted input examples
#: "respond in German" all fit inside ~35 characters — so this is generous for
#: anything a user actually says, while far too tight to assemble a command out
#: of words that merely co-occur somewhere in a dictated paragraph.
_LANG_WINDOW_CHARS = 60
_SENTENCE_END_RE = re.compile(r"[.!?]")


def _command_window(t: str, start: int, end: int) -> str:
    """The span around a language word that one switch command could occupy.

    Bounded twice, because either bound alone is too weak: the sentence
    boundary stops an ingredient in a NEIGHBOURING sentence from counting, and
    the character window stops one that shares a single, very long dictated
    sentence with it. See ``_match_language_switch`` for the failure both exist
    to prevent.
    """
    left, right = t[:start], t[end:]
    boundary = max(left.rfind("."), left.rfind("!"), left.rfind("?"))
    lo = max(boundary + 1, start - _LANG_WINDOW_CHARS)
    ahead = _SENTENCE_END_RE.search(right)
    hi = end + min(ahead.start() if ahead else len(right), _LANG_WINDOW_CHARS)
    return t[lo:hi]


def _language_words(t: str) -> list[tuple[int, int, str]]:
    """Every spoken language word, in the order the utterance says them.

    ``finditer`` rather than one ``search`` per alias: a language word may be
    said more than once, and only ONE of those places may be the one carrying
    the command.
    """
    hits: list[tuple[int, int, str]] = []
    for word, code in _LANG_ALIASES.items():
        for m in re.finditer(rf"\b{re.escape(word)}\b", t):
            hits.append((m.start(), m.end(), code))
    hits.sort()
    return hits


def _match_language_switch(t: str) -> str | None:
    """The reply language this utterance switches to, or ``None``.

    Every ingredient is looked for in the WINDOW around the language word, not
    in the whole utterance. Searching globally is what made this gate claim a
    turn it had no business in (live 2026-07-28 20:34): a dictated paragraph
    asking two coding agents for work contained the word for "automatically"
    (the user DESCRIBING a bug — text is not inserted automatically), the verb
    "stellen" 408 characters later (from "Rückfragen stellen", i.e. to ASK  # i18n-allow: quoted transcript tokens
    questions) and a preposition somewhere before both. Three unrelated
    clauses assembled into a language command, the gate answered the turn at
    once, and the Agentic-IDE delivery path further down ``generate()`` was
    never reached — so no agent was briefed while the user was told they were.

    The EARLIEST qualifying language still wins, so an utterance naming two
    languages keeps resolving to the first one spoken (forensic 2026-06-27) —
    but a language word with no command around it is now skipped rather than
    deciding the turn.
    """
    for start, end, code in _language_words(t):
        window = _command_window(t, start, end)
        if _LANG_CHANGE_VERB.search(window):
            return code
        if _LANG_IMPERATIVE_SPEAK.search(window):
            return code
        if _LANG_OUTPUT_VERB.search(window) and _LANG_PREP.search(window):
            return code
    return None


# Sub-agent / Heavy-Task-worker provider switch (added 2026-06-22). Sibling of
# the main provider_switch, but for [brain.sub_jarvis].provider — the worker
# that runs heavy missions, NOT the router brain. The gate stays pure: it only
# recognises the intent + the spoken provider word; the manager handler maps it
# to a canonical subagent slug, validates, and persists via the 3-layer writer
# (config-soll pinned) so the drift-guard cannot revert it. A sub-agent  # i18n-allow: "config-soll" is a Soll/Ist ("target-state") technical term, not prose
# QUALIFIER is required, so a bare "switch to gemini" still means the main brain.
_SUBAGENT_QUALIFIER = re.compile(
    r"\b(?:sub[-\s]?agent|subagent|sub[-\s]?jarvis|subjarvis|worker|helfer|helper)\b",
    re.IGNORECASE,
)
# Longer variants first so "openai-codex" wins over "openai".
_SUBAGENT_PROVIDER_WORDS = (
    "openai-codex", "openrouter", "antigravity", "chatgpt", "anthropic",
    "claude", "gemini", "openai", "grok", "codex", "gpt",
)
_SUBAGENT_SWITCH_VERB = re.compile(
    r"\b(?:wechsel[n]?|wechsle|umstell\w*|umschalt\w*|stell\w*|set|switch|change|nimm|mach)\b",
    re.IGNORECASE,
)
_SUBAGENT_PREP = re.compile(r"\b(?:auf|zu|to)\b", re.IGNORECASE)
_SUBAGENT_PROVIDER_NOUN = re.compile(r"\b(?:provider|anbieter)\b", re.IGNORECASE)


def _first_provider_word(text: str) -> str | None:
    """First provider alias in ``text``, scanned longest-first so 'openai-codex'
    wins over its 'openai' / 'codex' substrings."""
    for word in _SUBAGENT_PROVIDER_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text):
            return word
    return None


def _match_subagent_switch(t: str) -> str | None:
    if not _SUBAGENT_QUALIFIER.search(t):
        return None
    # Gate: an explicit change verb is an unambiguous command; otherwise accept
    # only "... provider auf/to <X>" (a noun + preposition) so a STATEMENT like
    # "der Sub-Agent läuft auf Gemini" (no verb, no 'provider' word) falls through  # i18n-allow: quoted German input example
    # to the brain instead of silently switching.
    if not (
        _SUBAGENT_SWITCH_VERB.search(t)
        or (_SUBAGENT_PREP.search(t) and _SUBAGENT_PROVIDER_NOUN.search(t))
    ):
        return None
    # The TARGET provider follows the directional preposition: "switch ... TO
    # codex", "von Antigravity AUF Codex". Look for a provider word AFTER the last  # i18n-allow: quoted German input example
    # auf/zu/to first (the target); only then fall back to the whole sentence.
    # Without this the first word in alias-list ORDER wins, so "von Antigravity
    # auf Codex" switched to antigravity — the SOURCE, not the target (forensic
    # 2026-06-27).
    preps = list(_SUBAGENT_PREP.finditer(t))
    if preps:
        after_prep = _first_provider_word(t[preps[-1].end():])
        if after_prep:
            return after_prep
    return _first_provider_word(t)


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
