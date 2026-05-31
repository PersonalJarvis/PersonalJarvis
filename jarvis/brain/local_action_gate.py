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
    re.compile(r"^(?:hey\s+)?(?:jarvis[,\s]+)?oeffne\s+(?P<app>.+?)\s*[?.!]*$", re.I),
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


def _looks_like_desktop_control(text: str) -> bool:
    """True for GUI-manipulation commands that should drive the computer-use loop.

    Conservative on purpose: only fires for a compound open-and-operate command
    or an unambiguous GUI verb, so plain "oeffne chrome" stays the fast DIRECT
    path and "schreib mir ein Gedicht" stays a normal brain answer.
    """
    return bool(_COMPOUND_OPEN_CONTROL_RE.match(text) or _GUI_VERB_RE.search(text))

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

    return None


def _match_scripted_local_plan(text: str) -> LocalActionPlan | None:
    terminal_count = _parse_terminal_count(text)

    if terminal_count is not None:
        return LocalActionPlan(
            mode=LocalActionMode.DIRECT,
            tool_calls=_open_terminal_calls(terminal_count),
        )

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
