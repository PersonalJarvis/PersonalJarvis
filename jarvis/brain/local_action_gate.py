"""Pure local action gate for low-latency computer-use routing.

Visual-target and desktop-control utterances are routed to the
screenshot-based computer-use harness (:data:`HARNESS_NAME`). The harness
runs the in-process screenshot/click/keyboard loop
(``jarvis/harness/screenshot_only_loop.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

#: Entry-point name of the screenshot-based computer-use harness (see
#: ``pyproject.toml`` ``[project.entry-points."jarvis.harness"]`` +
#: ``jarvis/plugins/harness/computer_use.py``). Canonical home for the
#: literal so call sites import this constant instead of hardcoding it.
HARNESS_NAME = "screenshot"


class LocalActionMode(str, Enum):
    DIRECT = "DIRECT"
    COMPUTER_USE = "COMPUTER_USE"
    #: Returned when the utterance looks like an action request but no
    #: registered capability covers it.  ``manager.py`` must route this
    #: straight to TTS, skipping brain dispatch entirely.
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class LocalToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LocalActionPlan:
    mode: LocalActionMode
    tool_calls: tuple[LocalToolCall, ...] = field(default_factory=tuple)
    harness: str | None = None
    prompt: str | None = None
    #: Populated only when ``mode == UNSUPPORTED``.  A deterministic,
    #: human-readable rejection message in the user's language that
    #: ``manager.py`` forwards verbatim to TTS.
    response_text: str | None = None


# ---------------------------------------------------------------------------
# Capability registry protocol
#
# The *real* registry lives in ``jarvis.core.capabilities`` (Agent A).
# We define a structural Protocol here so that:
#   a) production code can do a late import of the real singleton, and
#   b) tests can inject a lightweight fake without importing the real module.
# ---------------------------------------------------------------------------


@runtime_checkable
class _CapabilityRegistryLike(Protocol):
    """Minimal surface of ``CapabilityRegistry`` needed by this module."""

    def has_action_intent(self, utterance: str) -> bool: ...
    def resolve_intent(self, utterance: str) -> object | None: ...


_DIRECT_PATTERNS = (
    # ``(?:er)?oeffne(?:t|st|n)?`` covers öffne / öffnet / öffnest / öffnen and the
    # er-prefixed eröffne / eröffnet / … so "Eröffnet den Explorer" also takes the
    # clean DIRECT launch instead of the computer-use vision loop (live 2026-06-08).
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:er)?oeffne(?:t|st|n)?\s+(?P<app>.+?)\s*[?.!]*$",
        re.I,
    ),
    re.compile(r"^(?:hey\s+)?(?:jarvis[,\s]+)?starte\s+(?P<app>.+?)\s*[?.!]*$", re.I),
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?mach\s+(?P<app>.+?)\s+(?:auf|auch)\s*[?.!]*$",
        re.I,
    ),
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?kannst\s+du\s+(?P<app>.+?)\s+aufmachen\s*[?.!]*$",
        re.I,
    ),
)

_VISUAL_TARGET_PATTERNS = (
    re.compile(r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:klick|click)\s+.+", re.I),
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?oeffne\s+.+\b"
        r"(?:links|rechts|oben|unten|rote|rotes|roten|gruen|gruene|gruenes|gruenen|"
        r"blaue|blaues|blauen)\b.+\b(?:fenster|button|feld|app)\b",
        re.I,
    ),
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?schreib\s+.+\s+in\s+.+"
        r"(?:button|feld|eingabefeld|chatgpt|fenster|app|seite)\b",
        re.I,
    ),
)

# Desktop-control commands that need the multi-step GUI loop (computer-use),
# NOT a single deterministic open and NOT a canned "I can't" refusal. Two
# unambiguous shapes:
#   1. Compound "oeffne/starte/mach <app> und <do something>" — the user opened
#      an app to operate it (e.g. "oeffne WhatsApp und schreib Mama hallo").
#   2. A standalone GUI manipulation verb (klick/scroll/tippe/doppelklick/...).
# Ambiguous verbs like "schreib"/"such" alone are deliberately excluded (they
# often mean "write me a poem" / "search the web", i.e. a plain brain answer).
_COMPOUND_OPEN_CONTROL_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:oeffne|starte|mach|geh\s+(?:auf|zu))\b"
    r".+\bund\b.+",
    re.I,
)
_GUI_VERB_RE = re.compile(
    r"\b(klick|click|doppelklick|rechtsklick|tippe|tipp\b|scroll|scrolle|"
    r"markier|kopier|einfueg|fueg\b.+\bein\b|waehle\s+aus|wechsel\s+(?:zu|auf)\s+fenster|"
    r"druecke?\s+(?:auf|den|die|das)\b)\w*",
    re.I,
)

# Broader Computer-Use commands the narrow GUI-verb + compound-open patterns
# miss but that are unambiguously "operate the desktop" — they MUST reach the
# computer-use loop deterministically, never depend on the LLM talker calling
# computer_use. Live regression 2026-06-09: the talker (brain.primary=codex, CLI
# OAuth path) DROPS ALL TOOLS and can never emit a tool_call, so every CU task
# that fell to it went silent — ~30% of common CU commands (navigate /
# screenshot / window-ops / drag). Each pattern is high-precision; how-to
# questions ("wie navigiere ich…") are excluded in _looks_like_desktop_control
# so they stay normal brain answers. Inputs are pre-_normalize'd (umlauts → ascii:
# "schließ" → "schliess", "nächste" → "naechste", "vergrößer" → "vergroesser").
# Only directional prepositions (zu/auf/nach/in) — "navigiere DURCH das Menü" is
# deliberately excluded (too close to an explain/walk-me-through request).
_NAVIGATE_RE = re.compile(r"\bnavigier\w*\s+(?:zu|auf|nach|in)\b", re.IGNORECASE)
# Screenshot — verb-anchored on BOTH sides so a TAKE imperative matches
# ("mach/nimm/erstell/knips einen Screenshot", "Screenshot machen") but an
# informational or send/show mention does NOT ("ich habe einen Screenshot
# gemacht", "schick mir einen Screenshot", "zeig den letzten Screenshot") —
# review finding 2026-06-09. "gemacht" never matches \bmach\w* (no word boundary
# before "mach" inside "gemacht").
_SCREENSHOT_RE = re.compile(
    r"\b(?:mach|nimm|erstell|knips|capture|take|grab)\w*\b[\w\s]{0,14}?"
    r"\b(?:screenshot|bildschirmfoto|bildschirmaufnahme)\b"
    r"|\b(?:screenshot|bildschirmfoto|bildschirmaufnahme)\b[\w\s]{0,10}?"
    r"\b(?:mach|nimm|erstell|knips)\w*",
    re.IGNORECASE,
)
# "schließ/minimier/maximier … (das) Fenster/Tab/App" — the verb ALONE is too
# ambiguous ("schließ die Tür"), so a desktop-context noun within ~18 chars is
# required. Plus tab-switching ("wechsel zum nächsten Tab", "nächster Tab").
_WINDOW_OP_RE = re.compile(
    r"\b(?:schliess|minimier|maximier|verklein|vergroesser)\w*\b[\w\s]{0,18}?"
    r"\b(?:fenster|tab|app|programm|browser|seite|dialog)\b"
    r"|\bwechsel\w*\s+(?:zu|auf|zum|zur|in|den|das|die|naechste[nr]?)\b[\w\s]{0,12}?"
    r"\b(?:tab|fenster|app|programm)\b"
    r"|\b(?:naechst|vorherig|letzt|erst)\w*\s+tab\b",
    re.IGNORECASE,
)
# Drag / move — "zieh"/"verschieb"/"drag" are common words, so a desktop object
# or a direction is required to avoid "zieh dich an".
_DRAG_RE = re.compile(
    r"\b(?:zieh|drag|verschieb)\w*\b[\w\s]{0,20}?"
    r"\b(?:fenster|datei|icon|maus|cursor|element|nach\s+(?:links|rechts|oben|unten))\b",
    re.IGNORECASE,
)


def _looks_like_desktop_control(text: str) -> bool:
    """True for GUI-manipulation commands that should drive the computer-use loop.

    Conservative on purpose: a compound open-and-operate command, an unambiguous
    GUI verb (klick/scroll/tippe/…), or a broader desktop-control verb
    (navigate / screenshot / window-op / drag) — so plain "oeffne chrome" stays
    the fast DIRECT path and "schreib mir ein Gedicht" stays a normal brain
    answer. The broader verbs are guarded against how-to questions so
    "wie navigiere ich…" / "wie mache ich einen Screenshot" stay brain answers.
    """
    # A "starte/oeffne X und Y" compound is desktop-control ONLY when X/Y is real
    # GUI work — never when it is heavy worker / research / sub-agent work. Live
    # bug 2026-06-15: "Starte eine Sub-Agent-Mission und recherchiere …" matched
    # _COMPOUND_OPEN_CONTROL_RE and ran on the screenshot harness. Unambiguous GUI
    # verbs (klick/scroll/tippe/…) stay desktop-control regardless of the nouns.
    if _COMPOUND_OPEN_CONTROL_RE.match(text) and not _NOT_OPEN_APP_RE.search(text):
        return True
    if _GUI_VERB_RE.search(text):
        return True
    if _OPEN_INSTRUCTIONAL_RE.search(text):
        return False
    return bool(
        _NAVIGATE_RE.search(text)
        or _SCREENSHOT_RE.search(text)
        or _WINDOW_OP_RE.search(text)
        or _DRAG_RE.search(text)
    )


# Open / launch verbs in ANY conjugation. Inputs are pre-normalised
# (``_normalize``: lowercased + umlauts transliterated, so "öffnest" -> "oeffnest"),
# so ``oeffn\w*`` catches öffne/öffnest/öffnet/öffnen alike. ``start\w*`` catches
# starte/startest/start; "mach … auf" is the separable verb handled separately.
_OPEN_VERB_RE = re.compile(
    r"\b(?:oeffn\w*|aufmach\w*|aufzumach\w*|start\w*|open\w*|launch\w*)\b",
    re.IGNORECASE,
)
# Separable verb "mach … auf" (particle trails the object): "mach mir Spotify auf".
_MACH_AUF_RE = re.compile(r"\bmach(?:e|st|t)?\b[\w\s]*\bauf\b", re.IGNORECASE)
# A coordinating "und" splits an open command from a follow-up action
# ("…Spotify öffnen UND Shape of You spielen") → the request is multi-step and
# belongs on the computer-use loop, not a single DIRECT open. Word-boundaried so
# it never fires inside a token. Operates on the normalised (transliterated)
# utterance, where "und" is stable.
_AND_RE = re.compile(r"\bund\b", re.IGNORECASE)
# Negated open ("ich will Spotify NICHT öffnen", "bitte KEIN Chrome starten",
# "öffne Discord lieber nicht") must NEVER launch. The verb-at-end fallback below
# scans the WHOLE utterance for a known app name, so without this guard it would
# happily launch the very app the user said NOT to open (review finding
# 2026-06-09). High-precision, low-cost: a negation token anywhere in the
# utterance suppresses the deterministic launch; the turn then falls to the brain
# (which, with clarify off by default, does not nag the user). Deliberately NOT
# folded into ``is_open_app_intent`` — that predicate is also the force-spawn
# guard, and a negated open must still count as an open there (to stay OFF the
# sub-agent worker path), just not trigger an actual launch here.
_OPEN_NEGATION_RE = re.compile(r"\bnicht\b|\bkein\w*\b|\bniemals\b", re.IGNORECASE)
# Signals that the request is NOT a plain desktop app-open but heavy worker /
# external-system work, which a sandboxed worker (not computer-use) owns. This
# is the single veto consulted by is_open_app_intent (and therefore by both the
# COMPUTER_USE gate at match_local_action AND the force-spawn guard in
# manager.py), so research / sub-agent / mission vocabulary MUST live here:
# "starte eine Sub-Agent-Mission und recherchiere …" overcaptures "startest" as
# an open-verb, and without this veto it was misrouted to the screenshot harness
# ("ich erledige das am Bildschirm") instead of a research worker (live bug
# 2026-06-15, sessions.db session 236877e6).
_NOT_OPEN_APP_RE = re.compile(
    r"\b(?:"
    r"pr|prs|pull\s*request|repo|repository|github|gitlab|issue|issues|branch|"
    r"baue?|baust|baut|implementier\w*|entwickel\w*|refactor\w*|debugg?\w*|"
    r"analysier\w*|analyz\w*|untersuch\w*|programmier\w*|deploy\w*|"
    r"datei|dateien|file|files|funktion\w*|skript|script|landingpage|"
    r"recherch\w*|research\w*|tiefenrecherche\w*|deep[\s-]?dive|"
    r"sub-?agent\w*|subagent\w*|mission\w*"
    r")\b",
    re.IGNORECASE,
)
# Instructional questions ("wie oeffne ich X?") must never launch anything.
_OPEN_INSTRUCTIONAL_RE = re.compile(
    r"^\s*(?:wie|how|was|what|warum|why|wieso|weshalb)\b", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Browser+URL fast-path (2026-06-10 latency collapse, Task 2).
#
# "oeffne chrome und gehe auf x.com" is ONE deterministic argv launch —
# browsers accept a URL argument on win/mac/linux, and open_app already
# whitelists http(s):// targets and forwards ``arguments``. Without this
# branch the compound regex routed the goal into the vision-LLM loop, which
# took ~3 minutes live (log 2026-06-10 20:46) for what is a 1-second launch.
# Both regexes are end-anchored: a site followed by MORE work ("… und poste
# einen tweet") must keep the computer-use loop, which has to act on the page.
# ---------------------------------------------------------------------------
_BROWSER_TOKENS = (
    "chrome", "firefox", "edge", "brave", "opera", "safari", "chromium",
    "vivaldi",
)
#: A bare domain or URL ("x.com", "https://github.com/foo"). Requires a dot +
#: TLD-like tail so "geh auf nummer sicher" never matches.
_SITE_PATTERN = r"(?P<site>(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?)"
_GOTO_VERBS = (
    r"(?:geh(?:e|st)?\s+(?:auf|zu|nach)|navigiere?\s+(?:zu|auf|nach)"
    r"|go\s+to|navigate\s+to|oeffne|open)"
)
_OPEN_BROWSER_GOTO_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:oeffne|starte|open|start|launch)\b[^.!?]*?"
    r"\b(?P<app>" + "|".join(_BROWSER_TOKENS) + r")\b.*?\b"
    + _GOTO_VERBS + r"\s+" + _SITE_PATTERN + r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_BARE_GOTO_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?" + _GOTO_VERBS + r"\s+" + _SITE_PATTERN
    + r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def _site_to_url(site: str) -> str:
    return site if site.startswith(("http://", "https://")) else f"https://{site}"


def _match_browser_url_fast_path(normalized: str) -> LocalActionPlan | None:
    """Deterministic browser+URL launch, or None to fall through."""
    if _OPEN_NEGATION_RE.search(normalized):
        return None
    if _OPEN_INSTRUCTIONAL_RE.search(normalized):
        return None
    m = _OPEN_BROWSER_GOTO_RE.match(normalized)
    if m:
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(
                name="open_app",
                args={
                    "app_name": m.group("app").lower(),
                    "arguments": _site_to_url(m.group("site")),
                },
            ),),
        )
    m = _BARE_GOTO_RE.match(normalized)
    if m:
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(
                name="open_app",
                args={"app_name": _site_to_url(m.group("site"))},
            ),),
        )
    return None


def is_open_app_intent(text: str) -> bool:
    """True for a request to OPEN / LAUNCH an application or window on the
    desktop — in any conjugation or phrasing — that a sandboxed sub-agent worker
    could never fulfil (a worker runs in an isolated git worktree and has no
    desktop). Such requests belong to the computer-use harness, NEVER to a
    force-spawned worker.

    Live bug 2026-06-08 (data/jarvis_desktop.log 17:37): "Ich möchte, dass du mir
    Hermes Agent öffnest, also …" force-spawned a worker because the capability
    registry resolves verbs strictly (``\\boeffne\\b``, base form only) while the
    action detector matches conjugations (``\\boeffne\\w*\\b``) — so "öffnest"
    counted as an action no capability resolves, i.e. generic sub-agent work.

    Excludes instructional questions ("wie oeffne ich X"), external-system work
    (PR / repo / GitHub), and heavy build / code / file work that genuinely needs
    a worker. Pure regex, no LLM / no IO (AP-9 / AP-11)."""
    t = _normalize(text)
    if not t:
        return False
    if _OPEN_INSTRUCTIONAL_RE.search(t):
        return False
    if _NOT_OPEN_APP_RE.search(t):
        return False
    return bool(_OPEN_VERB_RE.search(t) or _MACH_AUF_RE.search(t))

_APP_ALIASES = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "msedge": "msedge",
    "notepad": "notepad",
    "editor": "notepad",
    "texteditor": "notepad",
    "explorer": "explorer",
    "dateiexplorer": "explorer",
    "windows explorer": "explorer",
    "rechner": "calc",
    "taschenrechner": "calc",
    "calc": "calc",
    "calculator": "calc",
    "windows terminal": "wt",
    "terminal": "wt",
    "wt": "wt",
    "spotify": "spotify",
    # Chat / communication + media apps a voice user opens by name. Every value
    # is already in open_app's KNOWN_APPS and resolvable by app_resolver
    # (App Paths / PATH / the Start Menu .lnk fallback for Squirrel installs
    # like Discord/Slack, added 2026-06-09). Before this, "öffne Discord" was
    # absent here, fell through to the brain, and the router opened it via
    # computer_use but — producing no narration — was answered with the
    # clarifying question "Wie meinst du das genau?" instead of just launching
    # it on the fast deterministic path (live bug 2026-06-09).
    "discord": "discord",
    "slack": "slack",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "whats app": "whatsapp",
    "signal": "signal",
    "teams": "teams",
    "microsoft teams": "teams",
    "zoom": "zoom",
    "skype": "skype",
    "vlc": "vlc",
    "steam": "steam",
    "outlook": "outlook",
}

# Filler words / articles voice users sprinkle between the verb and the app
# name ("oeffne MIR chrome", "starte DEN editor", "mach FUER MICH chrome auf").
# Stripped before alias lookup so the canonical app name survives. Without this,
# "oeffne mir chrome" canonicalised to "mir chrome" -> no alias -> the command
# fell through to the brain instead of the deterministic local launch.
_APP_FILLER_WORDS = frozenset({
    "mir", "mal", "bitte", "doch", "den", "die", "das", "dem", "der",
    "ein", "eine", "einen", "mein", "meine", "meinen",
    "fuer", "mich", "uns", "the", "me", "my", "a", "an", "please",
})

_GERMAN_NUMBER_WORDS = {
    "ein": 1,
    "eine": 1,
    "einen": 1,
    "eins": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fuenf": 5,
}

_MAX_SCRIPTED_TERMINALS = 5

# Sentinel used as the default value for the ``_registry`` parameter of
# ``match_local_action``.  Using a dedicated object (not ``None``) lets the
# function distinguish "caller passed no registry → use real singleton" from
# "caller explicitly passed None → skip gate (registry unavailable)".
_SENTINEL: object = object()


# ADR-0016 L2 — voice-driven orb-recovery patterns. The regex is
# deliberately tight: anchored at ``^`` to reject substring matches and
# constrained to a small phrase list so general "wo bist du gerade?" or
# "weißt du wo der Bus ist?" queries do NOT trigger an orb reset.
# Inputs are run through ``_normalize`` first (lowercased, umlauts
# transliterated, leading "hey jarvis," stripped), so the patterns only
# need to match the post-normalised string.
_ORB_RESET_PATTERNS = (
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:wo\s+bist\s+du|orb\s+zurueck"
        r"|reset\s+(?:den\s+)?orb)\s*[?.!]*$",
        re.I,
    ),
)


def _matches_orb_reset(text: str) -> bool:
    return any(pattern.match(text) for pattern in _ORB_RESET_PATTERNS)


# Voice-driven mascot recovery patterns. Same shape and reasoning as the
# orb-reset patterns above: anchored on ^...$, normalized input only, and
# limited to a hand-picked phrase list so generic "spawne mir ein
# Terminal" or "wo ist mein Schlüssel" does NOT fall through here.
_MASCOT_RESPAWN_PATTERNS = (
    # "Maskottchen wieder auftauchen", "Maskottchen zurück", "Maskottchen
    # kommt zurück", "Maskottchen weg", "Mascot back", "Maskottchen
    # spawnen / reset / respawn".
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?"
        r"(?:das\s+|der\s+|den\s+|mein\s+)?(?:maskottchen|mascot)"
        r"\s+(?:wieder\s+|kommt\s+|soll\s+wieder\s+)?"
        r"(?:auftauchen|auftaucht|zurueck|zurueckkommen|her|holen|da|"
        r"spawnen|spawn|spawne|spawner|reset|respawn|weg|verschwunden|"
        r"come\s+back|back)"
        r"\s*[?.!]*$",
        re.I,
    ),
    # "Spawn das Maskottchen", "spawne das Maskottchen", "respawn the mascot"
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?"
        r"(?:spawn(?:e|en)?|respawn|reset|bring)\s+"
        r"(?:das\s+|den\s+|the\s+|mein\s+|my\s+)?(?:maskottchen|mascot)"
        r"(?:\s+(?:zurueck|back))?"
        r"\s*[?.!]*$",
        re.I,
    ),
    # "wo ist (das) Maskottchen"
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?"
        r"wo\s+ist\s+(?:das\s+|der\s+|mein\s+)?(?:maskottchen|mascot|spawner)"
        r"\s*[?.!]*$",
        re.I,
    ),
    # Standalone "(der) Spawner" — short form the user reaches for.
    re.compile(
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?"
        r"(?:(?:der|den|ein|the|my|mein)\s+)?spawner"
        r"\s*[?.!]*$",
        re.I,
    ),
)


def _matches_mascot_respawn(text: str) -> bool:
    return any(pattern.match(text) for pattern in _MASCOT_RESPAWN_PATTERNS)


def _unsupported_response(text: str, lang: str) -> str:
    """Return a deterministic, no-LLM rejection message.

    The phrasing is taken verbatim from the capability-coupling spec so that
    TTS reads it aloud and the user knows *why* Jarvis declined instead of
    hearing a confused silence.

    Parameters
    ----------
    text:
        The normalised utterance (used only for potential future interpolation;
        currently unused in the message body).
    lang:
        ``"de"`` (default) or ``"en"``.
    """
    if lang == "en":
        return (
            "I can't do that yet. "
            "I don't have a registered tool for it. "
            "Tell me which MCP or integration should handle it and I can learn."
        )
    return (
        "Das kann ich noch nicht. "
        "Mir fehlt dafür ein Werkzeug — wenn du mir verrätst welches MCP "
        "oder welche Integration zuständig wäre, kann ich's lernen."
    )


# A DISPATCH verb: the user wants to drive a real external system (send, play,
# post, book, order, call), not merely build/analyse/parse something. This is
# the disambiguator that separates "schick eine Email" (real dispatch) from
# "implementier eine Email-Validation" (coding task that just NAMES the domain).
# Deliberately excludes ambiguous build/write verbs (schreib/baue/mach) — those
# are generic sub-agent work even next to an integration name.
_EXTERNAL_DISPATCH_VERB_RE = re.compile(
    r"\b("
    r"schick\w*|sende[nt]?|send|verschick\w*|"          # send
    r"poste[nt]?|post|tweete[nt]?|"                      # post
    r"spiel\w*|play|"                                    # play media
    r"trag\w*|eintrag\w*|book|buch\w*|bestell\w*|order[ns]?|"  # book/order/enter
    r"reservier\w*|reserve|"
    r"ruf\w*\s+an|ruf\s+\w+\s+an|call|anruf\w*|"         # call
    r"like[nt]?|liken|folge[nt]?|abonnier\w*"            # social interact
    r")\b",
    re.I,
)

# A SPECIFIC external integration noun a generic sub-agent worker cannot reach
# without a dedicated MCP/connector — a real inbox, calendar, Spotify session,
# social account, or delivery service. The noun ALONE is not enough (a coding
# task may merely mention it); a dispatch verb must also be present. git/GitHub
# is deliberately absent — the worker has git + gh natively.
_EXTERNAL_INTEGRATION_NOUN_RE = re.compile(
    r"\b("
    # Mail
    r"e-?mails?|gmail|outlook|postfach|"
    # Messaging
    r"whats-?app|telegram|signal|imessage|sms|"
    # Calendar
    r"kalender|calendar|termine?|appointments?|"
    # Music
    r"spotify|"
    # Social
    r"tweets?|twitter|instagram|facebook|linkedin|tiktok|"
    # Real-world commerce / transport
    r"pizza|uber\s*eats|lyft|doordash|lieferando|"
    # Travel / lodging / ticketing — no booking integration exists, so
    # "book a trip/flight/hotel" must refuse honestly, not 3-loop-fail a worker
    # (live gap 2026-06-14, mission_019ec761 critic_loop_exhausted).
    r"flights?|fl[uü]ge?|trips?|reisen?|hotels?|unterk[uü]nfte?|airbnb|motel|"
    r"tickets?|zugticket|bahnticket|flugticket|mietwagen|rental\s+car|"
    # Dining reservation
    r"tisch|restaurant|reservierung"
    r")\b",
    re.I,
)


def requires_external_integration(text: str) -> bool:
    """True iff the utterance asks to DRIVE a specific external system a generic
    sub-agent worker cannot reach (send mail, play Spotify, post a tweet, book a
    table, order food).

    Both signals are required: a SPECIFIC integration noun AND a real DISPATCH
    verb. The noun alone is not enough — a coding task that merely mentions the
    domain ("implementier eine Email-Validation", "baue einen Kalender-Parser",
    "schreib Code der Spotify-Playlists liest") is generic sub-agent work and
    must NOT be refused. git/GitHub is never matched (the worker drives them).

    Used by the capability gate AND the force-spawn heuristic to draw the single
    line between "refuse honestly (no tool exists)" and "delegate to the
    sub-agent (the universal capability for generic work)".
    """
    t = text or ""
    return bool(
        _EXTERNAL_INTEGRATION_NOUN_RE.search(t)
        and _EXTERNAL_DISPATCH_VERB_RE.search(t)
    )


def _get_capability_registry() -> _CapabilityRegistryLike | None:
    """Return the global capability registry singleton, or *None* if Agent A's
    module has not been installed yet.

    Using a late import here keeps the gate fast-importable even before
    ``jarvis.core.capabilities`` exists on disk.
    """
    try:
        from jarvis.core.capabilities import get_registry  # type: ignore[import]

        return get_registry()
    except (ImportError, AttributeError):
        return None


def match_local_action(
    text: str,
    lang: Literal["de", "en"] = "de",
    *,
    _registry: _CapabilityRegistryLike | None = _SENTINEL,  # type: ignore[assignment]
) -> LocalActionPlan | None:
    """Return a deterministic local action plan for narrow local commands.

    Parameters
    ----------
    text:
        Raw utterance from the voice pipeline.
    lang:
        Language hint (``"de"`` or ``"en"``) used to select the
        ``UNSUPPORTED`` response copy.
    _registry:
        Capability registry override — **for tests only**.  Pass a fake
        registry to exercise the UNSUPPORTED path without importing the real
        ``jarvis.core.capabilities`` module.  When omitted the production
        singleton is loaded via a late import.
    """
    original = text.strip()
    if not original:
        return None

    normalized = _normalize(original)

    # ------------------------------------------------------------------
    # Capability gate (SPEC §Layer 2, insertion point a)
    #
    # Resolve the registry once per call.  ``_SENTINEL`` (not None) means
    # "use the real singleton"; ``None`` means "registry unavailable, skip
    # gate"; a passed object means "test fake, use directly".
    # ------------------------------------------------------------------
    if _registry is _SENTINEL:
        _registry = _get_capability_registry()

    # Defensive: an empty registry (e.g. unit tests that don't seed it) means
    # "boot has not populated capabilities yet" → skip the gate to avoid false
    # positives.  The production boot path always calls
    # ``capabilities_seed.seed_registry(get_registry())`` before any voice
    # turn, so this no-op only triggers in isolated unit tests.
    if _registry is not None and getattr(_registry, "all", lambda: ())():
        if (
            _registry.has_action_intent(normalized)
            and _registry.resolve_intent(normalized) is None
            # Desktop-control commands are NEVER "unsupported" — computer-use is
            # the universal GUI integration, so route them there instead of the
            # canned refusal (live bug 2026-05-25: "oeffne WhatsApp und schreib"
            # was refused with "das kann ich noch nicht").
            and not _looks_like_desktop_control(normalized)
            # 2026-06-01: only a SPECIFIC external integration (mail/calendar/
            # Spotify/social/delivery) is genuinely unsupported. Generic work
            # (analyse/build/fix/code/research/git) is sub-agent-fulfillable, so
            # it must NOT be refused here — it falls through to the force-spawn
            # path (the sub-agent is the universal capability for generic work).
            and requires_external_integration(normalized)
        ):
            return LocalActionPlan(
                mode=LocalActionMode.UNSUPPORTED,
                response_text=_unsupported_response(normalized, lang),
            )
    # ADR-0016 L2: check the orb-recovery patterns BEFORE the scripted /
    # direct-open-app / visual-target gates so a "wo bist du?" never
    # accidentally falls through to a different branch.
    if _matches_orb_reset(normalized):
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(name="reset_orb_position", args={}),),
        )
    # Mascot recovery — same fast-path tier as the orb reset; checked
    # before the scripted / open-app / visual-target gates so the user
    # gets the deterministic respawn even if the phrase contains words
    # that would otherwise trip another branch.
    if _matches_mascot_respawn(normalized):
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(name="respawn_mascot", args={}),),
        )
    scripted = _match_scripted_local_plan(normalized)
    if scripted is not None:
        return scripted

    direct = _match_direct_open_app(normalized)
    if direct is not None:
        return direct

    # Browser+URL goals are a single argv launch — checked BEFORE the
    # visual-target / desktop-control branches, which would otherwise send
    # them into the multi-minute vision loop (2026-06-10 latency collapse).
    browser_url = _match_browser_url_fast_path(normalized)
    if browser_url is not None:
        return browser_url

    if _matches_visual_target(normalized):
        return LocalActionPlan(
            mode=LocalActionMode.COMPUTER_USE,
            harness=HARNESS_NAME,
            prompt=original,
        )

    # Compound open-and-operate / GUI-manipulation commands → multi-step
    # computer-use loop. Checked AFTER _match_direct_open_app so a plain
    # "oeffne chrome" stays the fast DIRECT path; only commands that actually
    # need to drive the UI ("oeffne WhatsApp und schreib Mama hallo",
    # "scroll runter", "tippe ... und druecke enter") land here.
    if _looks_like_desktop_control(normalized):
        return LocalActionPlan(
            mode=LocalActionMode.COMPUTER_USE,
            harness=HARNESS_NAME,
            prompt=original,
        )

    # Robust open-app fallback (live bug 2026-06-09 17:36): an open-app intent in
    # ANY phrasing the strict verb-first gates above missed — verb-at-end +
    # filler-heavy ("Kannst du bitte für mich einmal mein Spotify öffnen?",
    # "Spotify aufmachen", "öffnest du mir mal Discord"). That utterance matched
    # no DIRECT pattern, fell to the router LLM, which produced no speech (it did
    # not even call computer_use) → "Wie meinst du das genau?" and Spotify NEVER
    # opened. Execute deterministically instead, cross-platform (open_app →
    # app_resolver branches per OS), with no LLM in the loop:
    #   • "…öffnen UND <do more>"  → multi-step → computer-use offload (same path
    #     as the verb-first compounds above) so the follow-up isn't dropped.
    #   • known app, single step    → instant DIRECT open_app.
    #   • app NOT in the known set  → fall through to the brain (unchanged): the
    #     screenshot loop would have to hunt an unknown name on screen, the path
    #     that stalled live 2026-06-08. The force-spawn guard (is_open_app_intent)
    #     still keeps such a turn off the sub-agent path.
    if is_open_app_intent(original) and not _OPEN_NEGATION_RE.search(normalized):
        if _AND_RE.search(normalized):
            # "...öffnen und <do more>" → multi-step. (A benign "und zwar …" also
            # lands here; acceptable — the CU loop still opens the app and handles
            # the qualifier, at the cost of one screenshot cycle.)
            return LocalActionPlan(
                mode=LocalActionMode.COMPUTER_USE,
                harness=HARNESS_NAME,
                prompt=original,
            )
        # Gate already confirmed by is_open_app_intent (+ negation excluded) above,
        # so a bare app mention in a non-open sentence can never reach here.
        app = _extract_known_app(normalized)
        if app is not None:
            return LocalActionPlan(
                mode=LocalActionMode.DIRECT,
                tool_calls=(LocalToolCall(name="open_app", args={"app_name": app}),),
            )

    return None


def _match_scripted_local_plan(text: str) -> LocalActionPlan | None:
    terminal_count = _parse_terminal_count(text)

    if terminal_count is not None:
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=_open_terminal_calls(terminal_count),
        )

    return None


def _extract_known_app(text: str) -> str | None:
    """Return the canonical app for the FIRST known app name in a normalised
    open-app utterance, else ``None``.

    Scans 2-word then 1-word windows so a multi-word alias ("microsoft teams",
    "windows terminal", "whats app", "google chrome") wins over a 1-word
    sub-match. This catches the verb-at-end and filler-heavy phrasings the strict
    verb-first ``_DIRECT_PATTERNS`` miss ("Kannst du bitte für mich einmal mein
    Spotify öffnen?", "Spotify aufmachen", "öffnest du mir mal Discord"). Callers
    MUST first confirm :func:`is_open_app_intent` so a bare app mention in a
    non-open sentence ("ich höre Spotify gern") never launches anything.
    """
    tokens = [t for t in text.split() if t]
    for window in (2, 1):
        for i in range(len(tokens) - window + 1):
            phrase = " ".join(tokens[i:i + window])
            app = _APP_ALIASES.get(phrase)
            if app is not None:
                return app
    return None


def _match_direct_open_app(text: str) -> LocalActionPlan | None:
    for pattern in _DIRECT_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        app = _canonical_app(match.group("app"))
        if app is None:
            return None
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=(LocalToolCall(name="open_app", args={"app_name": app}),),
        )
    return None


def _matches_visual_target(text: str) -> bool:
    return any(pattern.match(text) for pattern in _VISUAL_TARGET_PATTERNS)


def _canonical_app(raw: str) -> str | None:
    app = re.sub(r"\s+", " ", raw.strip(" \t\r\n?.!")).lower()
    app = re.sub(r"\s+app$", "", app).strip()
    tokens = [t for t in app.split(" ") if t]
    # Drop leading filler/articles: "mir chrome" -> "chrome", "den editor" ->
    # "editor", "fuer mich chrome" -> "chrome". Keep at least one token.
    while len(tokens) > 1 and tokens[0] in _APP_FILLER_WORDS:
        tokens.pop(0)
    # Drop trailing politeness/articles too: "explorer fuer mich" -> "explorer",
    # "notepad fuer mich bitte" -> "notepad". Without this the trailing "fuer
    # mich" left a known app unresolved and the command fell to the computer-use
    # vision loop instead of the clean DIRECT launch (live 2026-06-08).
    while len(tokens) > 1 and tokens[-1] in _APP_FILLER_WORDS:
        tokens.pop()
    cleaned = " ".join(tokens)
    direct = _APP_ALIASES.get(cleaned)
    if direct is not None:
        return direct
    # Trailing qualifier: "chrome browser" -> "chrome".
    if len(tokens) > 1 and tokens[-1] == "browser":
        return _APP_ALIASES.get(" ".join(tokens[:-1]))
    return None


def _parse_terminal_count(text: str) -> int | None:
    patterns = (
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:oeffne|starte)\s+(?P<count>\d+|[a-z]+)\s+terminals?\b",
        r"^(?:hey\s+)?(?:jarvis[,\s]+)?mach\s+(?P<count>\d+|[a-z]+)\s+terminals?\s+auf\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        count = _parse_count(match.group("count"))
        if count is None:
            return None
        return max(1, min(count, _MAX_SCRIPTED_TERMINALS))
    return None


def _parse_count(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    return _GERMAN_NUMBER_WORDS.get(raw)


def _open_terminal_calls(count: int) -> tuple[LocalToolCall, ...]:
    return tuple(
        LocalToolCall(name="open_app", args={"app_name": "wt"}) for _ in range(count)
    )


def _normalize(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace("\u00f6", "oe")
        .replace("\u00d6", "oe")
        .replace("\u00e4", "ae")
        .replace("\u00c4", "ae")
        .replace("\u00fc", "ue")
        .replace("\u00dc", "ue")
        .replace("\u00df", "ss")
    )
