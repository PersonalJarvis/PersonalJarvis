"""MissionCommandGate — pattern matcher for Jarvis-Agent mission meta-commands.

AD-12 + AP-OC5 (see ``docs/jarvis-agents-bridge.md``): status phrases
("laeuft das noch?", "wie weit?", "Status?") and stop phrases ("brich ab",  # i18n-allow
"stop openclaw") MUST NOT lead to a new spawn — the router brain must
detect them deterministically via regex, otherwise there is a risk of
(a) latency from LLM tool-choice and (b) hallucination ("ja klar laeuft  # i18n-allow
das noch" even though the mission is dead).  # i18n-allow

Architecture:

- Pure function ``match_mission_command(text)`` -> ``MissionCommandMatch | None``.
- Bilingual DE+EN. Patterns are intentionally strict (word boundaries,
  start-of-sentence anchors for stop phrases) so smalltalk like
  "Wie weit ist das Buch?" or "Lass das stoppen, das Lied" does not  # i18n-allow
  match incorrectly.
- Optional: extract mission ID if the user explicitly names a mission
  ("status mission abc123" / "stop mission xyz"). Default is
  None = "applies to all active Jarvis-Agent missions".
- Result is a lightweight frozen dataclass; the caller
  (``BrainManager.generate``) maps it to a Mission-Manager read or cancel.

Comparison to ``voice_command_gate.match_voice_command``:
  The voice-command gate handles global meta-commands (provider switch,
  general cancel, depth override) that already take effect in
  ``BrainManager.generate`` before the force-spawn check. This module is
  narrower: only Jarvis-Agent/mission-specific status and stop requests that
  would otherwise be misinterpreted as spawn verbs ("brich ab" would
  otherwise be treated as an action verb and trigger a new sub-spawn).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_INTENT_T = Literal["status", "cancel"]


# --- Status phrases -----------------------------------------------------------
#
# Match: "laeuft das noch?", "Laeuft die Mission noch?", "Status?",  # i18n-allow
# "Status der Mission", "wie weit?", "wie weit bist du?",
# "is it still running?", "what's the status?", "how far are we?".
#
# Two pattern groups so German and English phrases remain clearly separable —
# English with its own verb patterns, German with "laeufst/laeuft" + "Status"  # i18n-allow
# + "wie weit". Both typically end with "?" but that is optional (STT does
# not reliably produce punctuation).
_STATUS_PATTERN_DE = re.compile(
    r"""
    (
        # 'laeuft (das|die mission|er|es) (noch|gerade|weiter)?'  # i18n-allow
        \bl(?:ä|ae)uft\s+(?:das|die\s+mission|der|er|es)\s+(?:noch|gerade|weiter)\b  # i18n-allow
        |
        # bare 'wie weit' at sentence start or before '?'/end — asks about progress.
        # NOT 'wie weit ist Berlin' etc. (qualifier-free only matches when
        # the question character is recognisable via '?'/end).
        ^\s*(?:jarvis[,\s]+)?wie\s+weit\s*\??\s*$
        |
        # 'wie weit' + person/mission qualifier
        \bwie\s+weit\s+(?:bist\s+du|sind\s+wir|sind\s+sie|sind\s+die\s+mission|ist\s+(?:die\s+mission|der\s+sub|claw|openclaw))\b  # i18n-allow
        |
        # 'status' alone (at start or after 'jarvis,'); MUST have question/end
        # character, otherwise it matches 'Status der Wirtschaft' incorrectly.
        ^(?:jarvis[,\s]+)?status\s*[?.!]*\s*$
        |
        # 'status der mission' / 'status vom sub' / 'status von openclaw'  # i18n-allow
        \bstatus\s+(?:der\s+mission|vom\s+sub|von\s+(?:openclaw|claw)|bei\s+(?:openclaw|claw))\b  # i18n-allow
        |
        # 'wo stehen wir' / 'wo steht das' / 'wo steht die mission'  # i18n-allow
        \bwo\s+steh(?:en|t)\s+(?:wir|das|die\s+mission|der\s+sub|claw|openclaw)\b  # i18n-allow
        |
        # 'noch am laufen' / 'noch dran'
        \b(?:noch|immer)\s+(?:am\s+laufen|dran|aktiv|beschäftigt|beschaeftigt)\b  # i18n-allow
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_STATUS_PATTERN_EN = re.compile(
    r"""
    \b(
        # 'is it still running' / 'is the mission still running'
        is\s+(?:it|the\s+mission|that|this|claw|openclaw)\s+still\s+running
        |
        # 'are we still running' / 'are you still running'
        are\s+(?:we|you|they)\s+still\s+(?:running|working|going)
        |
        # 'what's the status' / 'what is the status'
        what(?:'s|\s+is)\s+the\s+status
        |
        # 'status?' as standalone or after 'jarvis,' — same restriction as DE
        ^(?:jarvis[,\s]+)?status\s*[?.!]*\s*$
        |
        # 'how far (are we|along|is it)'
        how\s+far\s+(?:are|is|along)
        |
        # 'progress?' / 'any progress'
        (?:^|\s)(?:any\s+)?progress\b
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


# --- Cancel phrases -----------------------------------------------------------
#
# Match: "brich ab", "brich die Mission ab", "stop openclaw",
# "stoppe die Mission", "abbrechen", "cancel openclaw",  # i18n-allow
# "kill the mission".
#
# Important: the existing ``voice_command_gate._CANCEL_PATTERN`` already
# matches a generic "stopp / cancel / abbruch" at the sentence start —
# that is relied on for background-cancelling sub-Jarvis tasks.
# This module focuses specifically on **mission-cancel intent**:
# explicit mention of "Mission" / "openclaw" / "den Auftrag" /
# "the task". When both match, MissionCommandMatch wins (narrower scope).
_CANCEL_PATTERN_DE = re.compile(
    r"""
    (
        # 'brich (das|die|den|alles) ab'  # i18n-allow
        \bbrich\s+(?:das|die\s+mission|den\s+auftrag|alles|openclaw|claw|sub|den)\s*\w*\s*ab\b  # i18n-allow
        |
        # 'brich ab' — short form
        ^(?:jarvis[,\s]+)?brich\s+ab\b
        |
        # 'stop(pe) (die mission|openclaw|claw|den auftrag)'  # i18n-allow
        \bstopp?(?:e)?\s+(?:die\s+mission|openclaw|claw|den\s+auftrag|den\s+sub|alles)\b  # i18n-allow
        |
        # 'mission abbrechen' / 'auftrag abbrechen'  # i18n-allow
        \b(?:mission|auftrag|openclaw|claw)\s+(?:bitte\s+)?abbrechen\b  # i18n-allow
        |
        # 'abbruch der mission' / 'abbruch openclaw'
        \babbruch\s+(?:der\s+mission|openclaw|claw|vom\s+sub)\b  # i18n-allow
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CANCEL_PATTERN_EN = re.compile(
    r"""
    (
        # 'cancel (the )?(mission|openclaw|claw|task|job|sub|all)'
        \bcancel\s+(?:the\s+)?(?:mission|openclaw|claw|task|job|sub|all)\b
        |
        # 'stop (the )?(mission|openclaw|claw|task|job|sub)'
        \bstop\s+(?:the\s+)?(?:mission|openclaw|claw|task|job|sub)\b
        |
        # 'abort (the )?(mission|...)'
        \babort\s+(?:the\s+)?(?:mission|openclaw|claw|task|job|sub)\b
        |
        # 'kill (the )?(mission|...)'
        \bkill\s+(?:the\s+)?(?:mission|openclaw|claw|task|job|sub)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# --- Mission ID extraction ----------------------------------------------------
#
# Optional. User phrases like "Status der Mission abc-123" or
# "stop mission xyz" sometimes contain a mission ID. UUIDv7 strings are
# matched with a hex pattern; free aliases (e.g. "die erste Mission")
# remain None and the caller then filters for all active missions.
_MISSION_ID_PATTERN = re.compile(
    r"""
    \bmission\s+
    (?P<mid>
        [0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}
        |
        [0-9a-zA-Z][0-9a-zA-Z_\-]{2,40}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class MissionCommandMatch:
    """Result of a recognised Jarvis-Agent mission meta-command.

    Fields:
      intent: ``"status"`` or ``"cancel"``.
      mission_id: optional, present when the user explicitly names an ID.
        ``None`` = "all active Jarvis-Agent missions" (caller filters).
      language: detected language (``"de"`` or ``"en"``); default
        ``"de"`` so the caller can render the voice reply in the correct
        language.
    """

    intent: _INTENT_T
    mission_id: str | None = None
    language: Literal["de", "en"] = "de"


def match_mission_command(text: str) -> MissionCommandMatch | None:
    """Strictly checks for Jarvis-Agent mission meta-commands.

    Returns:
        ``MissionCommandMatch`` when the pattern hits a status or cancel
        phrase, otherwise ``None``. Order:
          1. Cancel first (takes priority — when the user says "stop
             openclaw", status is irrelevant).
          2. Status afterwards.
        On ambiguity (e.g. "wie weit cancel?" — rare), cancel wins.

    Examples (all return a match):
      - "Laeuft das noch?"             -> status / de  # i18n-allow
      - "Status?"                      -> status / de
      - "Wie weit bist du?"            -> status / de
      - "Brich die Mission ab"         -> cancel / de
      - "Stop openclaw"                -> cancel / en (or de — doesn't matter)
      - "Cancel the mission"           -> cancel / en

    Examples (no match):
      - "Wie weit ist Berlin von hier?"  (no mission context)  # i18n-allow
      - "Status der Wirtschaft"          (status without mission/claw)
      - "Lass das stoppen, das Lied"     (no mission context)
      - ""
    """
    t = (text or "").strip()
    if not t:
        return None

    # Language heuristic: if a German pattern matches, the language is
    # ``de``, otherwise (English pattern) ``en``. When no clear assignment,
    # default ``de`` (user profile default).
    lower = t.lower()

    # 1. Cancel first — a stop instruction must not degrade to status.
    # Order: EN before DE, because 'stop openclaw' / 'stop claw' matches both
    # languages (unambiguous English verb form 'stop'); DE-specific phrases
    # ('stoppe', 'brich ab', 'die mission', 'auftrag') only match DE and
    # win automatically.
    cancel_en = _CANCEL_PATTERN_EN.search(lower)
    if cancel_en:
        return MissionCommandMatch(
            intent="cancel",
            mission_id=_extract_mission_id(lower),
            language="en",
        )
    cancel_de = _CANCEL_PATTERN_DE.search(lower)
    if cancel_de:
        return MissionCommandMatch(
            intent="cancel",
            mission_id=_extract_mission_id(lower),
            language="de",
        )

    # 2. Status afterwards. DE first (profile default language); 'status'
    # alone would also match the EN pattern, but DE is the default language.
    status_de = _STATUS_PATTERN_DE.search(lower)
    if status_de:
        return MissionCommandMatch(
            intent="status",
            mission_id=_extract_mission_id(lower),
            language="de",
        )
    status_en = _STATUS_PATTERN_EN.search(lower)
    if status_en:
        return MissionCommandMatch(
            intent="status",
            mission_id=_extract_mission_id(lower),
            language="en",
        )

    return None


def _extract_mission_id(text: str) -> str | None:
    """Extracts an optional mission ID from the user text.

    Recognises UUIDv4/v7 strings and short alphanumeric aliases after the
    word "mission". Returns ``None`` when no ID is recognisable — the caller
    then filters for "all active Jarvis-Agent missions".
    """
    m = _MISSION_ID_PATTERN.search(text)
    if not m:
        return None
    raw = m.group("mid").strip()
    # Normalise UUID format: lowercase, with hyphens.
    if len(raw.replace("-", "")) == 32 and all(
        c in "0123456789abcdef-" for c in raw
    ):
        bare = raw.replace("-", "")
        return f"{bare[0:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:32]}"
    return raw


__all__ = ["MissionCommandMatch", "match_mission_command"]
