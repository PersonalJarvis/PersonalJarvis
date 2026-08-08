"""Local-outcome gate — say-do guard for local file/folder/system actions.

Why this exists
---------------
Maintainer report (2026-08-08): "Erstell einen neuen Ordner auf meinem
Desktop" was answered with the dictated capability refusal ("mir fehlt das
passende Werkzeug") although ``run_shell`` was registered AND visible in the
turn's tool surface. Root cause: the ``tool.run-shell`` capability vocabulary
only matched users who NAME the vehicle (shell/terminal/befehl), and no
deterministic mandate backed natural outcome phrasings — whether the model
mapped "create a folder" onto a shell command was model whim (a timer request
worked one day, a folder request was refused the next).

This module mirrors ``contact_intent.resolve_save_mandate`` (the say-do write
guard) and ``evidence_gate.py`` (the read guard): it spots, deterministically,
that a turn wants a LOCAL file/folder/system outcome, so the manager can
mandate ``run_shell`` for the turn — the prompt directive makes the call
non-optional, and the existing unverified-answer backstop replaces a fake
"done" with an honest line when the tool never ran. Pure regex + the shared
normalizer — NO LLM, NO IO (AP-9/AP-11).

The German phrases quoted above and in the vocabulary below are speech-input
matching data / forensic quotes of the failing voice requests (i18n-allow
rationale identical to ``local_action_gate.py``).

Matching contract
-----------------
A mandate needs BOTH an outcome verb ("erstell", "loesch", "rename", …) AND a
file-system object ("ordner", "datei", "folder", "desktop", …) — the noun is
what separates "erstell einen Ordner" (shell) from "erstell einen
Kalendereintrag" (plugin/calendar flow, untouched). A second, independent
class covers timers/alarms/countdowns ("stell einen Timer auf 10 Minuten"):
a timer noun plus a set/stop verb or a bare duration, with its own directive
that forces a DETACHED background process (the tool call times out at ~30s).

Stand-downs (each defers to the flow that owns the turn):
  - external-integration dispatch ("schick die Datei per Mail") — the
    capability/plugin machinery owns it;
  - cloud storage nouns (Drive/Dropbox/OneDrive/…) — not a local disk;
  - how-to / definition questions ("wie erstelle ich einen Ordner") — an
    inline answer, never an action;
  - explicit GUI vehicle ("klick auf den Ordner") — computer-use owns the
    screen when the user asks for it;
  - explicit heavy vehicle ("spawn"/"subagent") or a named skill — AD-S9:
    a user-named vehicle always wins.
"""

from __future__ import annotations

import re

from jarvis.brain.local_action_gate import requires_external_integration
from jarvis.core.capabilities import _normalize

# Outcome verbs (already umlaut-normalised — matched on ``_normalize`` output).
# ``\w*`` suffixes cover the German conjugations; the stems are chosen so the
# common imperative/infinitive forms all start with them.
_FS_VERB_RE = re.compile(
    r"\b(?:"
    r"erstell\w*|anleg\w*|create|mkdir|"  # i18n-allow
    r"loesch\w*|entfern\w*|delete|remove|"  # i18n-allow
    r"umbenenn\w*|benenn\w*|rename|"  # i18n-allow
    r"verschieb\w*|schieb\w*|move|"  # i18n-allow
    r"kopier\w*|copy|dupliz\w*|duplicate|"  # i18n-allow
    r"entpack\w*|unzip|zipp\w*|zip|komprimier\w*|extract|pack\w*|"  # i18n-allow
    r"liste\w*|list|zeig\w*|show|"  # i18n-allow
    r"oeffn\w*|open|aufmach\w*|"  # i18n-allow
    r"lies|lese|liest|read|vorles\w*|"  # i18n-allow
    r"leg\w*|mach\w*|make|"  # i18n-allow
    r"fuehr\w*|ausfuehr\w*|run|execute|start\w*"  # i18n-allow
    r")\b"
)

# File-system / shell objects. Includes the legacy vehicle nouns (shell,
# terminal, skript, …) so "fuehr das Skript aus" mandates exactly like the
# outcome phrasings do. Deliberately NOT included: bilder/pictures/musik
# ("zeig mir Bilder von Katzen" is a search, not a directory listing) and
# domain nouns owned by other flows (termin, kontakt, mail, wiki).
_FS_OBJECT_RE = re.compile(
    r"\b(?:"
    r"ordner|unterordner|verzeichnis\w*|datei(?:en)?|"  # i18n-allow
    r"folders?|director(?:y|ies)|files?|"
    r"desktop|downloads?|papierkorb|recycle\s+bin|"  # i18n-allow
    r"archiv\w*|archive|zip|"  # i18n-allow
    r"skript\w*|scripts?|befehl\w*|kommando\w*|commands?|"  # i18n-allow
    r"terminal|shell|powershell|bash|cmd"
    r")\b"
)

# How-to / definition questions are answered inline, never acted on. Matched
# unanchored so a greeting prefix ("Hey Jarvis, wie erstelle ich …") cannot
# defeat the guard (mirrors the greeting-prefix lesson in _is_smalltalk).
_HOWTO_DEFINITION_RE = re.compile(
    r"\b(?:"
    r"wie\s+\w+\s+(?:ich|man)\b|"  # i18n-allow
    r"how\s+(?:do|can|could|should|would)\s+(?:i|you|one)\b|how\s+to\b|"
    r"was\s+(?:ist|sind|bedeutet|heisst)\s+(?:ein|eine|der|die|das)\b|"  # i18n-allow
    r"what\s+(?:is|are|does)\s+an?\b"
    r")"
)

# The user explicitly asks for the SCREEN to be operated — computer-use owns
# that vehicle (AD-S9: a named vehicle wins). "desktop" alone is NOT a GUI
# marker here: "auf meinem Desktop" names a folder location, not the screen.
_GUI_VEHICLE_RE = re.compile(
    r"\b(?:klick\w*|click\w*|maus|mouse|cursor|scroll\w*|per\s+hand)\b"  # i18n-allow
)

# The user explicitly names a heavy-work or skill vehicle — those flows own
# the turn (AD-S9), the mandate must never fight them.
_OTHER_VEHICLE_RE = re.compile(
    r"\b(?:subagent\w*|sub-agents?|spawn\w*|worker|skills?|deep\s*dive)\b"
)

# Cloud storage is not the local disk — the plugin/CLI machinery owns it.
_CLOUD_STORAGE_RE = re.compile(
    r"\b(?:google\s*drive|gdrive|dropbox|onedrive|one\s*drive|icloud|"
    r"sharepoint|nextcloud|cloud)\b"
)

# A foreign-domain noun makes the generic FS noun AMBIENT, not the target:
# "Verschieb die Mail in einen anderen Ordner" is a mailbox operation on a
# mail FOLDER, not a disk folder — the plugin/calendar/contact flows own it.
# Unconditional co-occurrence stand-down (code-review finding 2026-08-08):
# ``requires_external_integration`` alone cannot catch these because it needs
# a DISPATCH verb (schick/sende/…), and the outcome verbs this gate matches
# (verschieb/loesch/kopier/…) are deliberately not dispatch verbs.
_FOREIGN_DOMAIN_RE = re.compile(
    r"\b(?:"
    r"mails?|e-?mails?|gmail|outlook|postfach|posteingang|inbox|"  # i18n-allow
    r"nachrichten?|messages?|chats?|"  # i18n-allow
    r"whatsapp|telegram|discord|slack|signal|"
    r"termine?|kalender\w*|calendar|events?|"  # i18n-allow
    r"kontakte?|contacts?|adressbuch|"  # i18n-allow
    r"spotify|playlists?|wiki|notizen?"  # i18n-allow
    r")\b"
)

# Timer/alarm/countdown nouns — the second mandate class (maintainer report
# 2026-08-08, follow-up): "stell einen Timer" worked one day and was refused
# the next, because a timer utterance carries no file-system noun and thus no
# mandate. No dedicated timer feature exists in Jarvis (checked 2026-08-08:
# every "timer" hit in the tree is an internal code timer), so run_shell is
# the only vehicle and mandating it hijacks nothing.
_TIMER_OBJECT_RE = re.compile(
    r"\b(?:timers?|wecker|alarme?|countdowns?|stoppuhr)\b"  # i18n-allow
)

# A timer turn needs a set/start/stop verb OR a bare duration ("Timer 10
# Minuten" is common verbless voice phrasing).
_TIMER_VERB_RE = re.compile(
    r"\b(?:"
    r"stell\w*|setz\w*|start\w*|mach\w*|leg\w*|aktivier\w*|erstell\w*|"  # i18n-allow
    r"set|create|"
    r"stopp\w*|stop|beende\w*|abbrech\w*|cancel|loesch\w*|entfern\w*|"  # i18n-allow
    r"delete|remove"
    r")\b"
)
_DURATION_RE = re.compile(
    r"\b\d+\s*(?:sekunden?|minuten?|stunden?|seconds?|minutes?|hours?|"  # i18n-allow
    r"sek|min|std|s|m|h)\b"  # i18n-allow
)

# Per-turn directive injected into the system prompt when the mandate fires
# (English — LLM-facing, mirrors CONTACT_WRITE_DIRECTIVE / the read gate).
RUN_SHELL_OUTCOME_DIRECTIVE = (
    "MANDATORY THIS TURN: the user asks for a local file/folder/system action "
    "on THIS computer (create/delete/rename/move/copy/list/read/open or "
    "similar). You MUST accomplish it NOW by calling the `run_shell` tool "
    "with a fitting command — write the command for the shell named in the "
    "tool description and quote paths that contain spaces. The user never "
    "needs to say 'terminal' or dictate a command; translating the outcome "
    "into a command is YOUR job. NEVER claim you lack a tool for this — "
    "`run_shell` IS the tool, and clicking through the GUI via computer_use "
    "is the wrong vehicle unless the user explicitly asked for the screen. "
    "Afterwards report what the tool result actually says: confirm on "
    "success, state the concrete error on failure. Only for an irreversible "
    "bulk delete/overwrite whose target is ambiguous, ask ONE short "
    "clarifying question instead of guessing."
)

RUN_SHELL_TIMER_DIRECTIVE = (
    "MANDATORY THIS TURN: the user asks for a local timer/alarm/countdown on "
    "THIS computer. You MUST arrange it NOW by calling the `run_shell` tool. "
    "CRITICAL: the tool call itself times out after ~30 seconds, so NEVER "
    "block with a plain sleep — start a DETACHED background process that "
    "waits and then notifies audibly/visibly (Windows: Start-Process "
    "powershell -WindowStyle Hidden with a Start-Sleep + beep/toast/msg "
    "script, or schtasks for clock times; POSIX: nohup sh -c 'sleep …; "
    "notify-send/afplay …' & ). To cancel a timer, find and kill that "
    "process honestly. NEVER claim you lack a timer tool — run_shell IS the "
    "tool. Confirm from the tool result only: the spawn must have succeeded; "
    "on failure say exactly what failed."
)


def resolve_local_outcome_mandate(utterance: str) -> tuple[str, str] | None:
    """Map a local file/folder/system request to ``(tool_name, directive)``.

    Returns ``("run_shell", RUN_SHELL_OUTCOME_DIRECTIVE)`` when the turn
    deterministically asks for a local outcome a shell command can achieve,
    ``None`` otherwise. Single consumption point: ``BrainManager.generate()``
    (exactly like ``resolve_save_mandate``).
    """
    raw = (utterance or "").strip()
    # Mirrors the force-spawn minimum-length gate: shorter fragments are
    # Whisper artefacts, not commands.
    if len(raw) < 6:
        return None
    t = _normalize(raw)
    if _HOWTO_DEFINITION_RE.search(t):
        return None
    if _GUI_VEHICLE_RE.search(t) or _OTHER_VEHICLE_RE.search(t):
        return None
    if _CLOUD_STORAGE_RE.search(t):
        return None
    if _FOREIGN_DOMAIN_RE.search(t):
        return None
    # External dispatch ("schick … per Mail") — checked on the RAW text,
    # the detector does its own normalisation.
    if requires_external_integration(raw):
        return None
    # Timer class first: a timer noun plus a set/stop verb or a bare duration
    # ("Timer 10 Minuten"). Independent of the file-system nouns below.
    if _TIMER_OBJECT_RE.search(t) and (
        _TIMER_VERB_RE.search(t) or _DURATION_RE.search(t)
    ):
        return ("run_shell", RUN_SHELL_TIMER_DIRECTIVE)
    if not _FS_OBJECT_RE.search(t):
        return None
    if not _FS_VERB_RE.search(t):
        return None
    return ("run_shell", RUN_SHELL_OUTCOME_DIRECTIVE)


__all__ = [
    "RUN_SHELL_OUTCOME_DIRECTIVE",
    "RUN_SHELL_TIMER_DIRECTIVE",
    "resolve_local_outcome_mandate",
]
