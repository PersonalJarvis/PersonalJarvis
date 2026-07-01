"""Acknowledgment-text generator for the perceived-latency-reduction pattern.

Hauptjarvis emits a brief, task-specific spoken ack the moment it has decided
on a tool call ("Verstanden, ich kuemmere mich darum.") so the user hears
something within ~200ms instead of waiting through the full reasoning +
tool-execution roundtrip in silence. The substantive answer follows after
the tool finishes.

Design constraints:

* **No LLM call.** Templates are deterministic dict lookups. Render time
  must stay well under one millisecond, otherwise the latency win is gone.
* **Tool-family-specific.** Per-tool handlers extract the most informative
  arg (search query, app name, skill name) into the ack so the user knows
  the right intent was understood — generic "okay, one moment" only as
  fallback for tools whose args are too noisy to echo (shell commands,
  long harness tasks).
* **Skip-list.** Passive state reads (awareness-snapshot, screen-snapshot)
  and low-latency individual UI events (click, hotkey, type-text) return
  ``None`` because a chat-style ack would chatter or feel uncanny.
* **Bilingual de/en.** Language is picked per request from the user's
  utterance language; templates exist for both. User strings stay in
  their natural form (German with umlauts) — only the surrounding code
  is English per the project's output-language policy.

Companion functions ``final_summary_marker`` and ``should_prepend_marker``
support the second half of the pattern: a short "Erledigt." prepended to
the brain's final response, unless the brain already self-confirmed.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "ACK_SKIP_TOOLS",
    "final_summary_marker",
    "generate_ack",
    "is_voice_control_utterance",
    "should_prepend_marker",
]


# Utterance-level skip patterns. Even if the brain decides to fire a tool
# call for one of these, the ack must stay silent — the user explicitly
# asked for these categories to be exempted in the dropdown spec, and the
# action itself (volume change, playback pause) is the confirmation.
#
# Kept as a module-level frozenset so the cost is one set lookup per match.
# Patterns cover: volume, stop/pause, mute. Bilingual de/en.
_VOICE_CONTROL_PATTERN = re.compile(
    # Full-match style: the entire utterance must be a voice-control command,
    # allowing only trailing politeness modifiers ("bitte", "mal", "jetzt",
    # "please") and punctuation. This stops narrative phrases like "lauter
    # Applaus war zu hoeren" or "still im Gespraech" from triggering.
    r"^\s*(?:"
    # German
    r"(?:mach\s+)?(?:lauter|leiser|laut|leise)(?:\s+machen)?"
    r"|sei\s+(?:bitte\s+)?(?:still|leise|stiller)"
    r"|halt(?:\s+(?:die\s+)?klappe)?"
    r"|stop(?:p)?(?:\s+(?:sprechen|reden|talking))?"
    r"|pause(?:\s+(?:die\s+)?(?:wiedergabe|musik|sprache))?"
    r"|pausier(?:e|en|t)?"
    r"|stumm(?:\s+schalten)?"
    r"|schweig(?:e|en)?"
    r"|nicht\s+(?:so\s+)?(?:laut|leise)"
    # English
    r"|(?:be\s+)?quiet"
    r"|shut\s+up"
    r"|louder|quieter|softer"
    r"|volume\s+(?:up|down)"
    r"|(?:please\s+)?stop(?:\s+(?:speaking|talking))?"
    r"|mute(?:\s+yourself)?"
    r")"
    # Optional trailing politeness / acknowledgment modifier
    r"(?:\s+(?:bitte|mal|jetzt|please|now|please\s+now))?"
    r"\s*[!.?]?\s*$",
    re.IGNORECASE,
)


def is_voice_control_utterance(utterance: str | None) -> bool:
    """True if the utterance is a Voice-Control command (skip-category 3).

    These bypass the ack pattern entirely — the spec is explicit that
    "lauter / leiser / stop / pause" must not get a spoken ack because
    the action itself is the confirmation. Pure regex match, no LLM call.
    """
    if not utterance:
        return False
    return bool(_VOICE_CONTROL_PATTERN.match(utterance.strip()))

# Tools that must NOT emit an ack. Two reasons:
#   (a) Passive state reads — there is no user-visible action to confirm.
#   (b) Low-latency individual UI events — a per-event chat ack would
#       generate dozens of TTS interruptions during a single keypress
#       sequence (type-text streams characters; click is single-pixel).
ACK_SKIP_TOOLS: frozenset[str] = frozenset({
    # passive observations
    "awareness_snapshot",
    "screen_snapshot",
    "whoami",
    # low-latency UI events
    "click",
    "hotkey",
    "move_mouse",
    "type_text",
    # silent meta tools (Phase 7.3 read-only)
    "list_mutable_settings",
    "get_config_value",
})

_FINAL_MARKERS: dict[str, str] = {
    "de": "Erledigt.",
    "en": "Done.",
    "es": "Listo.",
}

_GENERIC_ACK: dict[str, str] = {
    "de": "Okay, einen Moment.",
    "en": "Okay, one moment.",
    "es": "Vale, un momento.",
}

# A brain text that already opens with one of these is treated as
# self-confirming, so we don't double up with "Erledigt. Okay, ..."
_ALREADY_CONFIRMING_RE = re.compile(
    r"^\s*(erledigt|fertig|okay|ok|alright|done|got\s+it|verstanden|in\s+ordnung|sure)\b",
    re.IGNORECASE,
)


def _normalize_tool_name(name: str) -> str:
    """Tool calls arrive as either 'dispatch-to-harness' or 'dispatch_to_harness'.

    Internally we key everything off the underscore form; this normalizes
    both spellings + lowercases + strips whitespace.
    """
    return (name or "").replace("-", "_").lower().strip()


def _normalize_language(language: str | None) -> str:
    """Reduce any language hint to a supported code ('de', 'en' or 'es').

    The caller resolves the authoritative turn language through the ONE
    resolver (jarvis/core/turn_language.py) and passes a concrete code, so
    'es' must survive here — collapsing it to 'de' would flip a Spanish turn's
    ack to German (a runtime-output-language doctrine violation). Anything
    unrecognised falls back to 'de' as the module's last resort; the real
    default-locale decision already happened upstream.
    """
    if not language:
        return "de"
    low = language.lower()
    if low.startswith("en"):
        return "en"
    if low.startswith("es"):
        return "es"
    return "de"


def _trim_to_words(text: str, max_chars: int) -> str:
    """Trim ``text`` to ``max_chars`` at the nearest word boundary, ellipsizing."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:.")
    return cut + "..."


# ---------------------------------------------------------------------------
# Per-tool template handlers
# ---------------------------------------------------------------------------
#
# Each handler takes the tool's arg dict + the resolved language code and
# returns a short ack string. Handlers must never raise — the dispatcher
# falls back to ``_GENERIC_ACK`` on any exception, so a broken template
# never silences the ack entirely.

def _ack_dispatch_harness(args: Mapping[str, Any], lang: str) -> str:
    # Harness tasks are usually long sentences — echoing them sounds robotic.
    # Stay generic but warm.
    return {
        "de": "Verstanden, ich kuemmere mich darum.",
        "en": "Got it, on it.",
    }[lang]


def _ack_run_shell(args: Mapping[str, Any], lang: str) -> str:
    # Shell commands are technical noise the user doesn't want spoken back.
    return {"de": "Moment.", "en": "One moment."}[lang]


def _ack_search_web(args: Mapping[str, Any], lang: str) -> str:
    query = str(args.get("query") or args.get("q") or "").strip()
    if query and len(query) <= 40 and " " not in query[:3]:
        topic = _trim_to_words(query, 40)
        return {
            "de": f"Okay, ich schau mir {topic} an.",
            "en": f"Okay, looking up {topic}.",
        }[lang]
    return {"de": "Okay, ich recherchiere.", "en": "Okay, researching."}[lang]


def _ack_spawn_sub_jarvis(args: Mapping[str, Any], lang: str) -> str:
    # Sub-Jarvis spawns are the exact case the user complained about ("stille
    # Pause vor langer Antwort"). Keep the ack as short and warm as possible.
    return {
        "de": "Verstanden, ich kuemmere mich drum.",
        "en": "Got it, on it.",
    }[lang]


def _ack_multi_spawn(args: Mapping[str, Any], lang: str) -> str:
    tasks = args.get("tasks") or args.get("jobs") or []
    n = len(tasks) if isinstance(tasks, (list, tuple)) else 0
    if n >= 2:
        return {
            "de": f"Okay, ich erledige {n} Sachen parallel.",
            "en": f"Okay, running {n} tasks in parallel.",
        }[lang]
    return _GENERIC_ACK[lang]


def _ack_open_app(args: Mapping[str, Any], lang: str) -> str:
    app = str(
        args.get("app") or args.get("app_name") or args.get("name") or ""
    ).strip()
    if app and len(app) <= 30:
        return {
            "de": f"Okay, ich oeffne {app}.",
            "en": f"Okay, opening {app}.",
        }[lang]
    return _GENERIC_ACK[lang]


def _ack_run_skill(args: Mapping[str, Any], lang: str) -> str:
    skill = str(
        args.get("skill") or args.get("skill_name") or args.get("name") or ""
    ).strip()
    if skill and len(skill) <= 40:
        return {
            "de": f"Okay, ich starte {skill}.",
            "en": f"Okay, running {skill}.",
        }[lang]
    return _GENERIC_ACK[lang]


def _ack_gmail(args: Mapping[str, Any], lang: str) -> str:
    # Grounded per-tool ack for the email plugin (the user's slow-plugin
    # example). Action-aware so a SEND is not mis-announced as a read: a
    # ``send_message`` still goes through echo-confirmation, so it gets a
    # neutral filler, while reads get the specific "checking your mail" line.
    action = str(args.get("action") or "list_messages").strip()
    if action == "send_message":
        return _GENERIC_ACK[lang]
    return {
        "de": "Okay, ich schaue in deine Mails.",
        "en": "Okay, checking your mail.",
        "es": "Vale, reviso tu correo.",
    }[lang]


def _ack_google_calendar(args: Mapping[str, Any], lang: str) -> str:
    return {
        "de": "Okay, ich schaue in deinen Kalender.",
        "en": "Okay, checking your calendar.",
        "es": "Vale, reviso tu calendario.",
    }[lang]


def _ack_remember(args: Mapping[str, Any], lang: str) -> str:
    return {"de": "Okay, ich merk's mir.", "en": "Okay, noted."}[lang]


def _ack_verify(args: Mapping[str, Any], lang: str) -> str:
    return {"de": "Okay, ich pruefe das.", "en": "Okay, checking."}[lang]


def _ack_start_preview_server(args: Mapping[str, Any], lang: str) -> str:
    return {
        "de": "Okay, ich starte den Server.",
        "en": "Okay, starting the server.",
    }[lang]


def _ack_set_config(args: Mapping[str, Any], lang: str) -> str:
    return {"de": "Okay, ich aendere das.", "en": "Okay, updating."}[lang]


_TemplateFn = Callable[[Mapping[str, Any], str], str]

_TEMPLATES: dict[str, _TemplateFn] = {
    "dispatch_to_harness": _ack_dispatch_harness,
    "dispatch_with_review": _ack_dispatch_harness,
    "run_shell": _ack_run_shell,
    "search_web": _ack_search_web,
    "spawn_sub_jarvis": _ack_spawn_sub_jarvis,
    "multi_spawn": _ack_multi_spawn,
    "open_app": _ack_open_app,
    "run_skill": _ack_run_skill,
    "gmail": _ack_gmail,
    "google_calendar": _ack_google_calendar,
    "remember": _ack_remember,
    "verify_via_curl": _ack_verify,
    "verify_localhost": _ack_verify,
    "start_preview_server": _ack_start_preview_server,
    "set_config_value": _ack_set_config,
    # cli_<name> tools all share the same shell-like minimal ack
    "cli_tools": _ack_run_shell,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_ack(
    tool_name: str,
    tool_args: Mapping[str, Any] | None = None,
    *,
    language: str = "de",
) -> str | None:
    """Render a short, task-specific ack string for the given tool call.

    Returns ``None`` if the tool is in :data:`ACK_SKIP_TOOLS` — the caller
    should treat that as "do not emit an announcement at all".

    The function is pure and total: it never raises. Unknown tool names
    fall through to a generic "okay, one moment" rather than aborting.
    """
    norm = _normalize_tool_name(tool_name)
    if not norm or norm in ACK_SKIP_TOOLS:
        return None
    # cli_<name> aliases (cli_supabase, cli_gh, cli_vercel, ...) all map
    # to the cli_tools handler.
    if norm.startswith("cli_") and norm not in _TEMPLATES:
        return _ack_run_shell(tool_args or {}, _normalize_language(language))

    lang = _normalize_language(language)
    handler = _TEMPLATES.get(norm)
    if handler is not None:
        try:
            return handler(tool_args or {}, lang)
        except Exception:  # noqa: BLE001 — never let a broken template muzzle the ack
            pass
    return _GENERIC_ACK[lang]


def final_summary_marker(language: str = "de") -> str:
    """Return the short completion phrase ('Erledigt.' / 'Done.')."""
    return _FINAL_MARKERS[_normalize_language(language)]


def should_prepend_marker(brain_text: str | None) -> bool:
    """Decide whether to prefix a 'Erledigt.' marker to the brain's reply.

    ``True`` when the reply is empty (so the marker becomes the whole reply)
    or when it does not already open with a confirmation word. ``False``
    when the brain itself already self-confirmed — in that case prepending
    would produce 'Erledigt. Okay, ...' which sounds like a stutter.
    """
    if not brain_text or not brain_text.strip():
        return True
    return not bool(_ALREADY_CONFIRMING_RE.match(brain_text))
