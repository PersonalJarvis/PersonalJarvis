"""Acknowledgment-text generator for the perceived-latency-reduction pattern.

Hauptjarvis emits a brief, task-specific spoken ack the moment it has decided
on a tool call so the user hears something within ~200ms instead of waiting
through the full reasoning + tool-execution roundtrip in silence. The
substantive answer follows after the tool finishes.

Design constraints:

* **No LLM call.** Phrases are deterministic pool lookups + one random pick.
  Render time must stay well under one millisecond, otherwise the latency
  win is gone.
* **Varied, never robotic.** Every tool family carries a POOL of phrase
  variants per language and a process-wide no-repeat memory
  (:class:`AckPhrasePicker`) guarantees consecutive acks never sound
  identical — the 2026-07-05 forensic finding was the same one-word
  German ack spoken three times in one session (once per utterance).
* **Tool-family-specific.** Per-tool handlers extract the most informative
  arg (search query, app name, skill name, CLI service) into the ack so the
  user knows the right intent was understood — generic "okay, one moment"
  variants only as fallback for tools whose args are too noisy to echo
  (raw shell commands, long harness tasks).
* **Skip-list.** Passive state reads (awareness-snapshot, screen-snapshot,
  wiki lookups) and low-latency individual UI events (click, hotkey,
  type-text) return ``None`` because a chat-style ack would chatter or feel
  uncanny.
* **Trilingual de/en/es.** Language is picked per request from the resolved
  turn language; every pool exists for all three supported languages
  (runtime-output-language doctrine). User strings stay in their natural
  form (German with real umlauts) — only the surrounding code is English
  per the project's output-language policy.

Companion functions ``final_summary_marker`` and ``should_prepend_marker``
support the second half of the pattern: a short completion marker
prepended to the final response, unless the brain already self-confirmed.

.. note:: The phrase strings in this module are runtime voice/TTS product
   content (multilingual product surface). German lines carry inline
   ``i18n-allow`` markers.
"""
from __future__ import annotations

import random
import re
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = [
    "ACK_SKIP_TOOLS",
    "AckPhrasePicker",
    "describe_tool_action",
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
    # "please") and punctuation. This stops narrative phrases like the German
    # "lauter Applaus war zu hoeren" ("loud applause could be heard") or
    # "still im Gespraech" ("quiet in conversation") from triggering.  # i18n-allow: quoted German input example
    r"^\s*(?:"
    # German
    r"(?:mach\s+)?(?:lauter|leiser|laut|leise)(?:\s+machen)?"
    r"|sei\s+(?:bitte\s+)?(?:still|leise|stiller)"
    r"|halt(?:\s+(?:die\s+)?klappe)?"
    r"|stop(?:p)?(?:\s+(?:sprechen|reden|talking))?"
    r"|pause(?:\s+(?:die\s+)?(?:wiedergabe|musik|sprache))?"  # i18n-allow: German voice-control input vocabulary, matched against the user's utterance
    r"|pausier(?:e|en|t)?"
    r"|stumm(?:\s+schalten)?"
    r"|schweig(?:e|en)?"
    r"|nicht\s+(?:so\s+)?(?:laut|leise)"  # i18n-allow: German voice-control input vocabulary
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
#   (a) Passive state reads — there is no user-visible action to confirm,
#       and the fast ones (wiki lookup: ~70 ms) would ack a sub-100 ms read.
#   (b) Low-latency individual UI events — a per-event chat ack would
#       generate dozens of TTS interruptions during a single keypress
#       sequence (type-text streams characters; click is single-pixel).
ACK_SKIP_TOOLS: frozenset[str] = frozenset({
    # passive observations
    "awareness_snapshot",
    "screen_snapshot",
    "whoami",
    # fast passive memory reads (2026-07-06: wiki-recall answered in 72 ms —
    # a spoken "one moment" for that is pure chatter)
    "wiki_recall",
    "wiki_search",
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
    "de": "Erledigt.",  # i18n-allow: German runtime voice product (completion marker)
    "en": "Done.",
    "es": "Listo.",
}

# A brain text that already opens with one of these is treated as
# self-confirming, so we don't double up with "Erledigt. Okay, ..."
_ALREADY_CONFIRMING_RE = re.compile(
    r"^\s*(erledigt|fertig|okay|ok|alright|done|got\s+it|verstanden|in\s+ordnung|sure)\b",  # i18n-allow: bilingual (de/en) self-confirmation matching data, checked against generated brain text
    re.IGNORECASE,
)


class AckPhrasePicker:
    """Phrase selection with a short no-repeat memory.

    The 2026-07-05 forensic complaint was not "an ack existed" but "the SAME
    ack, three times in a row". The picker keeps the last few spoken phrases
    (global across tool families — repetition is the irritant regardless of
    family) and never picks one of them again while an alternative exists.
    A pool smaller than the memory still always yields a phrase (falls back
    to the full pool), so the ack can never be silenced by its own memory.

    Phrase variety, not cryptography — ``random.choice`` is deliberate.
    """

    def __init__(self, memory: int = 4) -> None:
        self._recent: deque[str] = deque(maxlen=memory)

    def pick(self, pool: Sequence[str]) -> str:
        """Return one phrase from ``pool``, avoiding the most recent picks.

        Guarantee: for any pool with at least two entries, two CONSECUTIVE
        picks are never identical — when the whole pool sits in memory (pool
        smaller than the memory), the fallback still excludes the very last
        pick before resorting to the full pool.
        """
        last = self._recent[-1] if self._recent else None
        candidates = (
            [p for p in pool if p not in self._recent]
            or [p for p in pool if p != last]
            or list(pool)
        )
        choice = random.choice(candidates)  # noqa: S311 — variety, not security
        self._recent.append(choice)
        return choice


# Process-wide default memory: consecutive utterances in one session share it,
# which is exactly the scope of the "three identical acks in a row" bug.
_DEFAULT_PICKER = AckPhrasePicker()


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
# Phrase pools
# ---------------------------------------------------------------------------
#
# Every entry: language code -> tuple of variants. All three supported
# languages are present in every pool (runtime-output-language doctrine).
# German strings are runtime voice product content (i18n-allow). Phrases are
# promissory ("I'm on it"), never completion claims — the tool has not run yet.

_GENERIC_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, einen Moment.",  # i18n-allow: German runtime voice product
        "Einen Augenblick bitte.",  # i18n-allow: German runtime voice product
        "Kurzen Moment, ich bin dran.",  # i18n-allow: German runtime voice product
        "Geht klar, Sekunde.",  # i18n-allow: German runtime voice product
        "Ich kümmere mich drum.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, one moment.",
        "Just a second.",
        "On it, give me a moment.",
        "One sec, working on it.",
        "Alright, hang on.",
    ),
    "es": (
        "Vale, un momento.",
        "Un segundo.",
        "Enseguida, dame un momento.",
        "Voy, un segundito.",
        "De acuerdo, espera un poco.",
    ),
}

_SHELL_ACK: dict[str, tuple[str, ...]] = {
    # Shell commands are technical noise the user doesn't want spoken back —
    # the phrases stay content-free but warm.
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Einen Moment, ich schaue nach.",  # i18n-allow: German runtime voice product
        "Ich prüfe das kurz.",  # i18n-allow: German runtime voice product
        "Sekunde, ich sehe nach.",  # i18n-allow: German runtime voice product
        "Ich werfe kurz einen Blick drauf.",  # i18n-allow: German runtime voice product
        "Moment, ich schaue mir das an.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "One moment, let me check.",
        "Let me take a quick look.",
        "Checking that now.",
        "Give me a second to look.",
        "Taking a quick look.",
    ),
    "es": (
        "Un momento, lo compruebo.",
        "Déjame echar un vistazo.",
        "Lo miro ahora mismo.",
        "Dame un segundo para mirarlo.",
        "Le echo un vistazo rápido.",
    ),
}

# {service} is interpolated with a human-readable CLI service name
# (cli_gh -> "GitHub"). See _CLI_SERVICE_NAMES / _cli_pool.
_CLI_SERVICE_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Ich schaue kurz auf {service} nach.",  # i18n-allow: German runtime voice product
        "Einen Moment, ich frage {service} ab.",  # i18n-allow: German runtime voice product
        "Ich werfe einen Blick in {service}.",  # i18n-allow: German runtime voice product
        "Sekunde, ich schaue in {service} nach.",  # i18n-allow: German runtime voice product
        "Ich hole das eben aus {service}.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Taking a quick look at {service}.",
        "One moment, checking {service}.",
        "Let me pull that from {service}.",
        "Checking {service} now.",
        "Having a quick look in {service}.",
    ),
    "es": (
        "Echo un vistazo rápido a {service}.",
        "Un momento, consulto {service}.",
        "Déjame mirarlo en {service}.",
        "Reviso {service} ahora mismo.",
        "Voy a ver en {service}.",
    ),
}

# Human service names for cli_<name> tools. Unknown suffixes are title-cased
# ("cli_foo_bar" -> "Foo Bar") — still informative, never wrong.
_CLI_SERVICE_NAMES: dict[str, str] = {
    "aws": "AWS",
    "az": "Azure",
    "docker": "Docker",
    "firebase": "Firebase",
    "fly": "Fly.io",
    "gcloud": "Google Cloud",
    "gh": "GitHub",
    "github": "GitHub",
    "kubectl": "Kubernetes",
    "netlify": "Netlify",
    "npm": "npm",
    "railway": "Railway",
    "stripe": "Stripe",
    "supabase": "Supabase",
    "vercel": "Vercel",
}

_HARNESS_ACK: dict[str, tuple[str, ...]] = {
    # Harness / worker tasks are usually long sentences — echoing them sounds
    # robotic. Stay generic but warm.
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Verstanden, ich kümmere mich darum.",  # i18n-allow: German runtime voice product
        "Geht klar, das übernehme ich.",  # i18n-allow: German runtime voice product
        "Alles klar, ich bin dran.",  # i18n-allow: German runtime voice product
        "Okay, ich setze mich dran.",  # i18n-allow: German runtime voice product
        "Mach ich, einen Moment.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Got it, on it.",
        "Alright, I'll take that on.",
        "Understood, working on it.",
        "Okay, I'm on it now.",
        "Sure, give me a moment.",
    ),
    "es": (
        "Entendido, me encargo.",
        "Vale, lo asumo yo.",
        "De acuerdo, estoy en ello.",
        "Okay, me pongo con ello.",
        "Claro, un momento.",
    ),
}

_SEARCH_TOPIC_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich schaue mir {topic} an.",  # i18n-allow: German runtime voice product
        "Ich suche kurz nach {topic}.",  # i18n-allow: German runtime voice product
        "Moment, ich recherchiere zu {topic}.",  # i18n-allow: German runtime voice product
        "Ich schaue, was ich zu {topic} finde.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, looking up {topic}.",
        "Searching for {topic} real quick.",
        "One moment, digging into {topic}.",
        "Let me see what I find on {topic}.",
    ),
    "es": (
        "Vale, busco {topic}.",
        "Un momento, investigo {topic}.",
        "Voy a ver qué encuentro sobre {topic}.",
        "Buscando {topic} ahora.",
    ),
}

_SEARCH_GENERIC_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich recherchiere kurz.",  # i18n-allow: German runtime voice product
        "Ich suche das eben raus.",  # i18n-allow: German runtime voice product
        "Moment, ich schaue mich um.",  # i18n-allow: German runtime voice product
        "Einen Moment, ich sammle die Fakten.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, researching.",
        "Let me dig that up.",
        "One moment, looking around.",
        "Give me a moment to gather the facts.",
    ),
    "es": (
        "Vale, investigo un poco.",
        "Déjame buscarlo.",
        "Un momento, echo un vistazo.",
        "Dame un momento para reunir los datos.",
    ),
}

_MULTI_SPAWN_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich erledige {n} Sachen parallel.",  # i18n-allow: German runtime voice product
        "Ich nehme mir die {n} Aufgaben gleichzeitig vor.",  # i18n-allow: German voice product
        "{n} Dinge parallel — läuft.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, running {n} tasks in parallel.",
        "Taking on those {n} jobs at once.",
        "{n} things in parallel — on it.",
    ),
    "es": (
        "Vale, hago {n} cosas en paralelo.",
        "Me pongo con las {n} tareas a la vez.",
        "{n} cosas en paralelo — voy.",
    ),
}

_OPEN_APP_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich öffne {app}.",  # i18n-allow: German runtime voice product
        "Moment, {app} kommt.",  # i18n-allow: German runtime voice product
        "Ich starte {app}.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, opening {app}.",
        "One sec, launching {app}.",
        "Starting {app}.",
    ),
    "es": (
        "Vale, abro {app}.",
        "Un segundo, lanzo {app}.",
        "Inicio {app}.",
    ),
}

_RUN_SKILL_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich starte {skill}.",  # i18n-allow: German runtime voice product
        "Moment, {skill} läuft gleich.",  # i18n-allow: German runtime voice product
        "Ich lasse {skill} laufen.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, running {skill}.",
        "One moment, starting {skill}.",
        "Kicking off {skill} now.",
    ),
    "es": (
        "Vale, ejecuto {skill}.",
        "Un momento, arranco {skill}.",
        "Pongo {skill} en marcha.",
    ),
}

_GMAIL_READ_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich schaue in deine Mails.",  # i18n-allow: German runtime voice product
        "Moment, ich öffne dein Postfach.",  # i18n-allow: German runtime voice product
        "Ich sehe kurz in deinen Mails nach.",  # i18n-allow: German runtime voice product
        "Ich werfe einen Blick in dein Postfach.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, checking your mail.",
        "One moment, opening your inbox.",
        "Taking a quick look at your email.",
        "Peeking into your inbox.",
    ),
    "es": (
        "Vale, reviso tu correo.",
        "Un momento, abro tu bandeja.",
        "Echo un vistazo a tus correos.",
        "Miro tu bandeja de entrada.",
    ),
}

_CALENDAR_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich schaue in deinen Kalender.",  # i18n-allow: German runtime voice product
        "Moment, ich prüfe deine Termine.",  # i18n-allow: German runtime voice product
        "Ich sehe in deinem Kalender nach.",  # i18n-allow: German runtime voice product
        "Kurzer Blick in deinen Kalender.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, checking your calendar.",
        "One moment, looking at your schedule.",
        "Let me check your appointments.",
        "Quick look at your calendar.",
    ),
    "es": (
        "Vale, reviso tu calendario.",
        "Un momento, miro tu agenda.",
        "Compruebo tus citas.",
        "Un vistazo rápido a tu calendario.",
    ),
}

_REMEMBER_ACK: dict[str, tuple[str, ...]] = {
    # Promissory only — "noted"-style completion claims would be a lie at
    # selection time (the tool has not run yet).
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich merke mir das.",  # i18n-allow: German runtime voice product
        "Geht klar, das behalte ich.",  # i18n-allow: German runtime voice product
        "Ich schreibe es mir auf.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, noting that.",
        "Got it, I'll remember that.",
        "Writing that down.",
    ),
    "es": (
        "Vale, lo apunto.",
        "Entendido, lo recordaré.",
        "Me lo apunto.",
    ),
}

_VERIFY_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich prüfe das.",  # i18n-allow: German runtime voice product
        "Moment, ich teste das kurz.",  # i18n-allow: German runtime voice product
        "Ich checke das eben.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, checking.",
        "One moment, testing that.",
        "Let me verify that.",
    ),
    "es": (
        "Vale, lo compruebo.",
        "Un momento, lo pruebo.",
        "Déjame verificarlo.",
    ),
}

_SERVER_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich starte den Server.",  # i18n-allow: German runtime voice product
        "Moment, der Server fährt hoch.",  # i18n-allow: German runtime voice product
        "Ich fahre den Server hoch.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, starting the server.",
        "One moment, spinning up the server.",
        "Bringing the server up.",
    ),
    "es": (
        "Vale, inicio el servidor.",
        "Un momento, arranco el servidor.",
        "Levanto el servidor.",
    ),
}

_SET_CONFIG_ACK: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow: German runtime voice product (ack phrase pool)
        "Okay, ich ändere das.",  # i18n-allow: German runtime voice product
        "Moment, ich stelle das um.",  # i18n-allow: German runtime voice product
        "Ich passe das an.",  # i18n-allow: German runtime voice product
    ),
    "en": (
        "Okay, updating.",
        "One moment, changing that.",
        "Adjusting that now.",
    ),
    "es": (
        "Vale, lo cambio.",
        "Un momento, lo ajusto.",
        "Lo adapto ahora.",
    ),
}


# ---------------------------------------------------------------------------
# Per-tool pool handlers
# ---------------------------------------------------------------------------
#
# Each handler takes the tool's arg dict + the resolved language code and
# returns a tuple of candidate phrases. Handlers must never raise — the
# dispatcher falls back to ``_GENERIC_ACK`` on any exception, so a broken
# template never silences the ack entirely.

def _interpolate(pool: tuple[str, ...], **kwargs: Any) -> tuple[str, ...]:
    """Format every variant in ``pool`` with ``kwargs``."""
    return tuple(p.format(**kwargs) for p in pool)


def _ack_dispatch_harness(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _HARNESS_ACK[lang]


def _ack_run_shell(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _SHELL_ACK[lang]


def _ack_search_web(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    query = str(args.get("query") or args.get("q") or "").strip()
    # Echo the topic only when it is topic-shaped (short, few words) — a full
    # question sentence read back sounds robotic.
    if query and len(query) <= 40 and len(query.split()) <= 4:
        topic = _trim_to_words(query, 40)
        return _interpolate(_SEARCH_TOPIC_ACK[lang], topic=topic)
    return _SEARCH_GENERIC_ACK[lang]


def _ack_spawn_sub_jarvis(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    # Sub-Jarvis spawns are the exact case the user complained about ("silent
    # pause before a long answer"). Keep the ack short and warm.
    return _HARNESS_ACK[lang]


def _ack_multi_spawn(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    tasks = args.get("tasks") or args.get("jobs") or []
    n = len(tasks) if isinstance(tasks, (list, tuple)) else 0
    if n >= 2:
        return _interpolate(_MULTI_SPAWN_ACK[lang], n=n)
    return _GENERIC_ACK[lang]


def _ack_open_app(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    app = str(
        args.get("app") or args.get("app_name") or args.get("name") or ""
    ).strip()
    if app and len(app) <= 30:
        return _interpolate(_OPEN_APP_ACK[lang], app=app)
    return _GENERIC_ACK[lang]


def _ack_run_skill(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    skill = str(
        args.get("skill") or args.get("skill_name") or args.get("name") or ""
    ).strip()
    if skill and len(skill) <= 40:
        return _interpolate(_RUN_SKILL_ACK[lang], skill=skill)
    return _GENERIC_ACK[lang]


def _ack_gmail(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    # Grounded per-tool ack for the email plugin (the user's slow-plugin
    # example). Action-aware so a SEND is not mis-announced as a read: a
    # ``send_message`` still goes through echo-confirmation, so it gets a
    # neutral filler, while reads get the specific "checking your mail" pool.
    action = str(args.get("action") or "list_messages").strip()
    if action == "send_message":
        return _GENERIC_ACK[lang]
    return _GMAIL_READ_ACK[lang]


def _ack_google_calendar(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _CALENDAR_ACK[lang]


def _ack_remember(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _REMEMBER_ACK[lang]


def _ack_verify(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _VERIFY_ACK[lang]


def _ack_start_preview_server(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _SERVER_ACK[lang]


def _ack_set_config(args: Mapping[str, Any], lang: str) -> tuple[str, ...]:
    return _SET_CONFIG_ACK[lang]


_TemplateFn = Callable[[Mapping[str, Any], str], tuple[str, ...]]

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
    # the generic CLI entry shares the shell-like content-free pool
    "cli_tools": _ack_run_shell,
}


def _cli_pool(norm_tool: str, lang: str) -> tuple[str, ...]:
    """Informative pool for a ``cli_<name>`` tool, naming the service.

    ``cli_gh`` resolves to "GitHub"; unknown suffixes are title-cased so the
    ack still names SOMETHING recognizable instead of a bare "one moment".
    """
    suffix = norm_tool[len("cli_"):].strip("_")
    if not suffix:
        return _SHELL_ACK[lang]
    service = _CLI_SERVICE_NAMES.get(
        suffix, suffix.replace("_", " ").title()
    )
    return _interpolate(_CLI_SERVICE_ACK[lang], service=service)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_ack(
    tool_name: str,
    tool_args: Mapping[str, Any] | None = None,
    *,
    language: str = "de",
    picker: AckPhrasePicker | None = None,
) -> str | None:
    """Render a short, task-specific ack string for the given tool call.

    Returns ``None`` if the tool is in :data:`ACK_SKIP_TOOLS` — the caller
    should treat that as "do not emit an announcement at all".

    The function is total: it never raises. Unknown tool names fall through
    to the generic pool rather than aborting. Selection is intentionally
    varied: the (process-wide) ``picker`` avoids repeating any of the last
    few phrases, so back-to-back acks never sound identical. Pass an
    explicit ``picker`` to isolate state (tests).
    """
    norm = _normalize_tool_name(tool_name)
    if not norm or norm in ACK_SKIP_TOOLS:
        return None
    lang = _normalize_language(language)
    chooser = picker if picker is not None else _DEFAULT_PICKER

    # cli_<name> aliases (cli_supabase, cli_gh, cli_vercel, ...) resolve a
    # human service name for an informative ack.
    if norm.startswith("cli_") and norm not in _TEMPLATES:
        try:
            return chooser.pick(_cli_pool(norm, lang))
        except Exception:  # noqa: BLE001 — never let a broken pool muzzle the ack
            return chooser.pick(_GENERIC_ACK[lang])

    handler = _TEMPLATES.get(norm)
    if handler is not None:
        try:
            pool = handler(tool_args or {}, lang)
            if pool:
                return chooser.pick(pool)
        except Exception:  # noqa: BLE001 — never let a broken template muzzle the ack
            pass
    return chooser.pick(_GENERIC_ACK[lang])


def describe_tool_action(
    tool_name: str, tool_args: Mapping[str, Any] | None = None
) -> str:
    """Compact ENGLISH description of what the tool call is about to do.

    Prompt input for the contextual interim composer (`ReadbackComposer`),
    NOT product surface — the composer answers in the resolved turn language.
    Extracts the most informative arg (query / app / skill / CLI service)
    without ever echoing raw shell commands. Total: never raises; unknown
    tools get a neutral "working on the request".
    """
    norm = _normalize_tool_name(tool_name)
    args: Mapping[str, Any] = tool_args or {}

    def _arg(*names: str, max_len: int = 60) -> str:
        for name in names:
            try:
                value = str(args.get(name) or "").strip()
            except Exception:  # noqa: BLE001 — garbage args must not break the ack
                value = ""
            if value:
                return _trim_to_words(value, max_len)
        return ""

    try:
        if norm.startswith("cli_") and norm != "cli_tools":
            suffix = norm[len("cli_"):].strip("_")
            service = _CLI_SERVICE_NAMES.get(
                suffix, suffix.replace("_", " ").title()
            )
            return f"querying {service}" if service else "running a quick lookup"
        if norm == "search_web":
            query = _arg("query", "q")
            return (
                f"running a web search for {query!r}" if query
                else "running a web search"
            )
        if norm in ("run_shell", "cli_tools"):
            return "running a quick check on the computer"
        if norm in ("dispatch_to_harness", "dispatch_with_review",
                    "spawn_sub_jarvis", "spawn_worker"):
            return "handing the task to a background helper"
        if norm == "multi_spawn":
            tasks = args.get("tasks") or args.get("jobs") or []
            n = len(tasks) if isinstance(tasks, (list, tuple)) else 0
            return (
                f"starting {n} tasks in parallel" if n >= 2
                else "starting background tasks"
            )
        if norm == "open_app":
            app = _arg("app", "app_name", "name", max_len=30)
            return f"opening {app}" if app else "opening an application"
        if norm == "run_skill":
            skill = _arg("skill", "skill_name", "name", max_len=40)
            return f"running the {skill} routine" if skill else "running a routine"
        if norm == "gmail":
            action = str(args.get("action") or "list_messages").strip()
            if action == "send_message":
                return "preparing an email"
            return "fetching the user's email"
        if norm == "google_calendar":
            return "checking the user's calendar"
        if norm == "remember":
            return "saving a note to memory"
        if norm in ("verify_via_curl", "verify_localhost"):
            return "verifying the result"
        if norm == "start_preview_server":
            return "starting the preview server"
        if norm == "set_config_value":
            return "updating a setting"
    except Exception:  # noqa: BLE001 — a description bug must not mute the ack
        pass
    return "working on the request"


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
