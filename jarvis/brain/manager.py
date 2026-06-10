"""BrainManager: Intent-Router + Smart-Fallback + Pipeline-Adapter.

Architecture:

1. **Router** (`jarvis/brain/router.py`) classifies user intent:
   - `fast` → fast model (Haiku) for tool actions, smalltalk
   - `deep` → reasoning model (Opus) for analysis, planning, explanation
   - `code` → OpenClaw-backed heavy worker

2. **Model-Cache**: `(provider_name, model) → Brain-Instance` — multiple
   models of the same family coexist without re-instantiation.

3. **Fallback-Chain**: On error (429, 500, auth, …) the manager tries in order:
   - same provider, deep_model (if fast is rate-limited, try deeper)
   - `claude-api` (OAuth Max plan)
   - `claude-api` (separate quota)
   - `gemini`, `openrouter`, `openai`, `grok` (when keys are present)
   - Ollama was completely removed from the project on 2026-04-21.

4. **Pipeline-Adapter**: `__call__(text) -> str` for `speech/pipeline.py`.

5. **Voice-Commands**: "wechsel auf gemini", "denk gründlich", "denk schnell".
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import (
    ActionExecuted,
    AnnouncementRequested,
    BrainProviderSwitched,
    BrainTurnCompleted,
    BrainTurnStarted,
    ResponseGenerated,
    VisionInjected,
)
from jarvis.core.protocols import (
    Brain,
    BrainMessage,
    BrainRequest,
    CostRecord,
    ImageBlock,
    Tool,
)
from jarvis.memory import CoreMemory, PersonStore, RecallStore, Soul, UserProfile
from jarvis.memory.curator import Curator
from jarvis.safety.tool_executor import ToolExecutor

from .dispatcher import BrainDispatcher
from .healthcheck import BrainConfigError
from .intent_router import RoutingDecision, classify
from .local_action_gate import (
    HARNESS_NAME,
    LocalActionMode,
    _looks_like_desktop_control,
    is_open_app_intent,
    match_local_action,
    requires_external_integration,
)
from .local_action_gate import _normalize as _gate_normalize
from .mission_command_gate import match_mission_command
from .assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name
from .persona_loader import load_persona_prompt
from .provider_registry import BrainProviderRegistry
from .rate_limit_tracker import RateLimitTracker
from .streaming import aggregate
from .voice_command_gate import match_voice_command

if TYPE_CHECKING:
    from jarvis.awareness.manager import AwarenessManager
    from jarvis.brain.wiki_context import WikiContextInjector
    from jarvis.control.cost import CostMeter as CostMeterLike

log = logging.getLogger(__name__)

#: Hard bound on the per-turn vision capture (Wave-3 latency fix). ``vision.
#: current()`` can stall (mss BitBlt hang, paused-state miss, slow disk); without
#: a cap it blocks the whole brain turn on the hot path. On timeout the turn
#: proceeds text-only.
_VISION_COLLECT_TIMEOUT_S: float = 2.5


def _estimate_usd_from_usage(
    meter: Any,
    model: str,
    usage: dict[str, int],
) -> float:
    """Maps `agg.usage` to the CostMeter price table.

    Returns 0.0 when the model is not in the price table — tracking still
    occurs but the budget gate does not trigger. This is intentional:
    prefer no gate over a wrong gate (see BudgetConfig.estimate_usd).
    """
    config = getattr(meter, "_config", None)
    if config is None:
        return 0.0
    prices = getattr(config, "prices", None) or {}
    from jarvis.control.cost import BudgetConfig as _BC
    return _BC.estimate_usd(
        prices, model,
        tokens_in=int(usage.get("input_tokens", 0)),
        tokens_out=int(usage.get("output_tokens", 0)),
        tokens_cache_hit=int(usage.get("cache_hit_tokens", 0)),
    )


PROVIDER_ALIASES = {
    "claude": "claude-api",
    "opus": "claude-api",
    "haiku": "claude-api",
    "sonnet": "claude-api",
    "gpt": "openai",
    "chatgpt": "openai",
    "openai": "openai",
    "gemini": "gemini",
    "flash": "gemini",
    "pro": "gemini",
    "grok": "grok",
    "openrouter": "openrouter",
}

# Mapping of Credential-Manager slot -> Brain provider ID. Brain slots only;
# TTS/STT providers have their own lifecycles outside BrainManager.
# Used by the SecretConfigured subscriber to remove the corresponding provider
# from _dead_providers after the user sets a key, so it is retried on the next
# turn without requiring an app restart.
_SECRET_KEY_TO_BRAIN: dict[str, str] = {
    "gemini_api_key": "gemini",
    "google_aistudio_api_key": "gemini",
    "google_api_key": "gemini",
    "anthropic_api_key": "claude-api",
    "openai_api_key": "openai",
    "openrouter_api_key": "openrouter",
    "grok_api_key": "grok",
    "xai_api_key": "grok",
}

# ──────────────────────────────────────────────────────────────────
# Tier defaults per provider (source of truth for fast/frontier mapping)
# ──────────────────────────────────────────────────────────────────
#
# As of 2026-04. Update when providers release new models or deprecate old
# ones. Structure: tier → provider → model-id.
#
# "router" = fast tier (<1s first token, tool use, cheap).
# "deep"   = frontier tier (reasoning, long context, more expensive).
#
# Wave-4 migration: the second key was previously named ``"sub_jarvis"``
# because the frontier model drove the Sub-Jarvis tier. The Sub-Jarvis tier
# was removed with the OpenClaw-Bridge migration, but the frontier mapping
# itself is retained as the deep-brain source — hence simply ``"deep"``.
#
# Aliases like "haiku"/"opus" are NOT mapped here — PROVIDER_ALIASES
# resolves them to the canonical provider name first, then
# _resolve_tier_model() looks up here.

# Tool names whose successful execution means a real on-screen DESKTOP ACTION
# happened (open an app, click, type, scroll, …). When the router brain runs
# one of these and then produces NO narration text — a known Gemini behaviour
# after a function call — the turn is NOT empty/confused: a confirmation must be
# spoken, never a clarifying question (live bug 2026-06-09, AP-19-adjacent: a
# successful computer_use run that opened Chrome was answered with "Wie meinst
# du das genau?"). ``computer_use`` + ``open_app`` are the router-reachable
# desktop tools; the rest are the in-loop GUI primitives, listed for robustness
# so a future router-exposed action stays covered.
_DESKTOP_ACTION_TOOL_NAMES: frozenset[str] = frozenset({
    "computer_use",
    "open_app",
    "click",
    "click_element",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "switch_window",
})


TIER_DEFAULTS_BY_PROVIDER: dict[str, dict[str, str]] = {
    "router": {
        # Frontier 2026-Q2 — main Jarvis tier (latency-first, pure dispatcher).
        # 2026-04-29: gemini-3-flash is only available as -preview (Google API
        # returns 404 NOT_FOUND without -preview).
        "claude-api": "claude-haiku-4-5-20251001",
        "gemini": "gemini-3-flash-preview",
        "openai": "gpt-5.5",
        # Codex MUST be listed or `_fast_model("codex")` returns None and
        # `_build_fallback_chain` silently drops codex from the chain (its
        # `if fast:` guard) — so an explicitly-active codex brain never gets
        # called and the turn falls through to a fallback (the live "Gemini
        # answered while Codex was the active brain" bug, 2026-06-09). gpt-5.5
        # is the model `codex exec` itself reports; the CLI (ChatGPT-login) path
        # ignores it, the OpenAI-key path uses it.
        "codex": "gpt-5.5",
        # grok-4.3 (released 2026-04-30) is simultaneously the fastest
        # AND most capable Grok — replaces 4.1-fast in both tiers.
        "grok": "grok-4.3",
        "deepseek": "deepseek-chat",
        "openrouter": "anthropic/claude-haiku-4.5",
        "mistral": "mistral-small-3.1",
    },
    "deep": {
        # Frontier 2026-Q2 — deep brain (user mandate 2026-04-29:
        # frontier everywhere). 2026-05-28: Opus 4.7 -> 4.8 (claude-opus-4-8
        # is the current Anthropic frontier; 4.7 is superseded). Stable
        # alias, no dated snapshot so the ID does not rotate per release.
        "claude-api": "claude-opus-4-8",
        "gemini": "gemini-3.1-pro-preview",
        "openai": "gpt-5.5-pro",
        # Codex deep-tier anchor (same reason as the router tier above). The CLI
        # path ignores the model; this keeps codex in the deep/code chain too.
        "codex": "gpt-5.5",
        "grok": "grok-4.3",
        "deepseek": "deepseek-reasoner",
        "openrouter": "anthropic/claude-opus-4.8",
        "mistral": "mistral-large-3",
    },
}


def _resolve_tier_model(
    tier: str,
    provider: str,
    explicit_model: str | None,
) -> str:
    """Returns the model for (tier, provider).

    1. If `explicit_model` is set (from [brain.router] in jarvis.toml),
       that value is used — user override takes precedence.
    2. Otherwise look up in TIER_DEFAULTS_BY_PROVIDER.
    3. Otherwise return an empty string (the Brain constructor then uses its
       hardcoded DEFAULT_MODEL as a fallback).

    Unknown providers do NOT raise — every brain plugin has its own
    DEFAULT_MODEL as an emergency anchor.
    """
    if explicit_model:
        return explicit_model
    return TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(provider, "")


def get_tier_default_model(tier: str, provider: str) -> str | None:
    """Public API for the setup wizard / UI / voice_command_gate.

    Returns the default model for (tier, provider) or None if no default
    exists. The caller can use this to decide whether the provider is
    supported at all.
    """
    return TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(provider)


# ──────────────────────────────────────────────────────────────────
# Force-spawn pattern builder (persona mandate phase 3)
# ──────────────────────────────────────────────────────────────────
#
# The three lists in ``BrainRoutingConfig`` are compiled into three regex
# patterns here. ``BrainManager._should_force_spawn`` evaluates them in
# order: smalltalk allowlist wins (no spawn), otherwise verb match → spawn,
# otherwise marker match → spawn.
#
# Pattern matches ``\bnichts\b`` as a "negative-lookahead-no-match" sentinel
# for empty lists — prevents an empty verb list from degenerating into a
# greedy match-everything regex.

_NEVER_MATCH_RE: re.Pattern[str] = re.compile(r"(?!.*)", re.IGNORECASE)


# BUG-LIVE-04 (Recon-Agent 3, 2026-05-16): Whisper transcribes silence,
# background TV, music, jingles into a small set of well-known sentinel
# strings. Empirical sample from data/jarvis_desktop.log (~75% mission
# fail rate on 2026-05-16 — half driven by these phrases).
#
# 2026-05-17 (H2 from audit-team 10): the original single-set + startswith
# match was too greedy for short Single-Token seeds. "you" filtered every
# English utterance starting with "You" (e.g. "You there?"), "musik"
# filtered "Musik lauter machen", "applaus" filtered "Applaus für die
# Band". Real user voice queries were silently dropped from the
# force-spawn path. The fix splits the seeds into two buckets:
#
#   _WHISPER_FP_EXACT_ONLY     -- short tokens that also appear in
#                                 legitimate speech; only the *whole*
#                                 utterance must equal the seed (after
#                                 punctuation strip).
#   _WHISPER_FP_PREFIX_OK      -- multi-word phrases distinctive enough
#                                 that any utterance starting with them
#                                 is almost certainly a Whisper artefact;
#                                 startswith match still allowed.
#
# An entry must appear in exactly ONE bucket. The combined frozenset
# WHISPER_FALSE_POSITIVE_SEEDS below is kept as a backwards-compatible
# alias for any external caller (tests, telemetry) that wants the
# complete catalogue.
_WHISPER_FP_EXACT_ONLY: frozenset[str] = frozenset({
    # Short tokens / single words that legit user speech also starts with.
    "you",
    "musik",
    "[musik]",
    "applaus",
    "[applaus]",
    "subscribe",
    "tschüss",
    "untertitel",
    "untertitelung",
    "thank you",
    "thank you.",
})

_WHISPER_FP_PREFIX_OK: frozenset[str] = frozenset({
    # Multi-word phrases distinctive enough that startswith is safe.
    "untertitelung des zdf für funk, 2017",
    "untertitelung des zdf für funk",
    "vielen dank",
    "vielen dank fürs zuschauen",
    "vielen dank für ihre aufmerksamkeit",
    "bis zum nächsten mal",
    "bis zum nächsten mal!",
    "thanks for watching",
    "thank you for watching",
    "see you next time",
    "ich verstehe es nicht",
})

# Backwards-compatible alias — equals the union of both buckets so any
# external introspection (telemetry, eval harness) still sees the full
# catalogue. Disjoint by construction; assertion at import time catches
# accidental duplication when the lists are edited.
_WHISPER_FALSE_POSITIVE_SEEDS: frozenset[str] = (
    _WHISPER_FP_EXACT_ONLY | _WHISPER_FP_PREFIX_OK
)
assert not (_WHISPER_FP_EXACT_ONLY & _WHISPER_FP_PREFIX_OK), (
    "Whisper FP seed lists must be disjoint"
)
_PC_CONTROL_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"klick|click|tippe|tipp|type|schreib|schreibe|reinschreib|prompt|prompten|"
    r"absenden|sende|send|drueck|druecke|drück|drücke|press|taste|hotkey|"
    r"browser|fenster|feld|eingabefeld|chatgpt|tab|button|pc|desktop|"
    r"maus|mouse|cursor"
    r")\w*\b",
    re.IGNORECASE,
)

_INSTRUCTIONAL_QUESTION_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:"
    r"wie\s+(?:kann|koennte|könnte|muss|soll|mach|mache|macht|geht|funktioniert)\s+"
    r"|was\s+(?:ist|bedeutet|heisst|heißt)\s+"
    r"|woran\s+erkenne\s+"
    r"|warum\s+"
    r"|how\s+(?:do|can|could|should|would)\s+"
    r"|what\s+(?:is|does|are)\s+"
    r"|why\s+"
    r")",
    re.IGNORECASE,
)


def _build_verb_pattern(terms: list[str]) -> re.Pattern[str]:
    """``\\b<term>\\w*\\b`` regex for action verbs including conjugated forms."""
    if not terms:
        return _NEVER_MATCH_RE
    parts = [re.escape(t) + r"\w*" for t in terms]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def _build_marker_pattern(markers: list[str]) -> re.Pattern[str]:
    """``\\b<marker>\\b`` regex for external-system markers (PR/Repo/...)."""
    if not markers:
        return _NEVER_MATCH_RE
    parts = [re.escape(m) for m in markers]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def _build_smalltalk_pattern(allowlist: list[str]) -> re.Pattern[str]:
    """Smalltalk allowlist as a case-insensitive substring match."""
    if not allowlist:
        return _NEVER_MATCH_RE
    parts = [re.escape(p) for p in allowlist]
    return re.compile(r"(?:^|\b)(?:" + "|".join(parts) + r")(?:\b|$)", re.IGNORECASE)


# Leading greeting / wake-word / politeness run, stripped before the smalltalk
# re-check in ``BrainManager._is_smalltalk``. Anchored at ^, repeats so several
# leading tokens collapse ("Hey Jarvis, hallo, öffne ..."), and swallows the
# trailing separators (comma / period / …). Longer tokens ("hey jarvis") precede
# their prefix ("hey") so the longest run is consumed. Live bug 2026-06-07
# (data/jarvis_desktop.log 18:19:07): "Hallo, öffne ihn für mich" was silenced
# as smalltalk because the allowlist substring-matched the leading "Hallo".
_GREETING_PREFIX_RE = re.compile(
    r"^(?:\s*(?:"
    r"hey\s+jarvis|hi\s+jarvis|hallo\s+jarvis|ok(?:ay)?\s+jarvis|jarvis|"
    r"guten\s+morgen|guten\s+abend|guten\s+tag|good\s+morning|good\s+evening|"
    r"hey|hi|hallo|hello|moin|servus|"
    r"ok|okay|bitte|danke|thanks|thank\s+you"
    r")\b[\s,.!?:;-]*)+",
    re.IGNORECASE,
)


def _looks_like_pc_control(user_text: str) -> bool:
    """Detects local screen/PC control requests intended for the computer-use harness."""
    return bool(_PC_CONTROL_RE.search(user_text or ""))


def _is_instructional_question(user_text: str) -> bool:
    """True for how-to / explanatory questions that should be answered directly."""
    return bool(_INSTRUCTIONAL_QUESTION_RE.search(user_text or ""))


def _balanced_json_objects(text: str) -> list[str]:
    """Return every top-level balanced ``{...}`` substring of ``text``.

    String/escape-aware brace walk so a tool_use object can be recovered even
    when a provider wraps it in prose or markdown. Used by
    :func:`_extract_leaked_spawn_call` to find a tool_use block that a provider
    emitted as TEXT instead of executing.
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objects.append(text[start:i + 1])
                    start = -1
    return objects


def _extract_leaked_spawn_call(text: str) -> dict[str, Any] | None:
    """Return the ``input`` dict of a leaked ``spawn_worker`` tool_use, else None.

    Some providers (notably Gemini) intermittently emit a ``tool_use`` block as
    the response *text* instead of invoking the tool — the brain reply becomes
    raw ``[{"type":"tool_use","name":"spawn_worker","input":{...}}]`` JSON,
    which would otherwise be spoken (scrubbed to "Es trat ein Fehler auf") and
    the delegated sub-agent would never run. This detects that leak (bare
    object or list, with or without prose/markdown fences) and returns the
    tool ``input`` (possibly empty) so the caller can execute it deterministically.
    """
    if not text or "spawn_worker" not in text or "tool_use" not in text:
        return None

    candidates: list[Any] = []
    stripped = text.strip()
    # Drop a leading ```json / ``` fence if the whole reply is fenced.
    if stripped.startswith("```"):
        stripped = stripped.lstrip("`")
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()
    try:
        candidates.append(json.loads(stripped))
    except (json.JSONDecodeError, ValueError):
        # Embedded in prose — recover balanced {...} objects individually.
        for obj_str in _balanced_json_objects(text):
            try:
                candidates.append(json.loads(obj_str))
            except (json.JSONDecodeError, ValueError):
                continue

    for cand in candidates:
        blocks = cand if isinstance(cand, list) else [cand]
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "spawn_worker"
            ):
                inp = block.get("input")
                return inp if isinstance(inp, dict) else {}
    return None


def _looks_like_tool_use_leak(text: str) -> bool:
    """True if ``text`` looks like a provider leaked a tool_use block as TEXT.

    A natural voice reply never starts with ``[`` or ``{`` — structured JSON at
    the very start (optionally inside a ```json fence) means the provider emitted
    a function call as *content* instead of invoking it. Cheap enough to run on
    the growing streamed buffer so the raw JSON is never handed to TTS.
    """
    if not text:
        return False
    s = text.lstrip()
    if s.startswith("```"):
        s = s.lstrip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.lstrip()
    return s.startswith("[") or s.startswith("{")


def _extract_leaked_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Return ``(tool_name, input_dict)`` of ANY leaked tool_use block, else None.

    Generalises :func:`_extract_leaked_spawn_call` (spawn-only) to EVERY router
    tool — ``cli_*``, ``open_app``, ``dispatch_to_harness``, ``screenshot`` …
    Gemini intermittently emits the ``tool_use`` block as response *text*
    instead of invoking it; in the streaming voice path that JSON would be
    spoken (scrubbed to silence) and the action would never run. This recovers
    the call so it can be executed deterministically (see
    :meth:`BrainManager._recover_leaked_tool`).
    """
    if not text or "tool_use" not in text:
        return None
    candidates: list[Any] = []
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.lstrip("`")
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()
    try:
        candidates.append(json.loads(stripped))
    except (json.JSONDecodeError, ValueError):
        for obj_str in _balanced_json_objects(text):
            try:
                candidates.append(json.loads(obj_str))
            except (json.JSONDecodeError, ValueError):
                continue
    for cand in candidates:
        blocks = cand if isinstance(cand, list) else [cand]
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if isinstance(name, str) and name:
                    inp = block.get("input")
                    return name, (inp if isinstance(inp, dict) else {})
    return None


# Single source of truth for the reply-language vocabulary (Python ↔ REST ↔ TS).
# "auto" = mirror the user's input language; the rest hard-pin that language.
SUPPORTED_REPLY_LANGUAGES: tuple[str, ...] = ("auto", "de", "en", "es")
_REPLY_LANGS: frozenset[str] = frozenset(SUPPORTED_REPLY_LANGUAGES)
_REPLY_LANG_NAMES: dict[str, str] = {"de": "German", "en": "English", "es": "Spanish"}


def normalize_reply_language(value: object) -> str:
    """Coerce a raw reply-language value to a known code, else ``"auto"``.

    Accepts case-insensitive, whitespace-padded input. Unknown / empty / None
    fall back to ``"auto"`` (mirror the user's input language) so a typo in
    jarvis.toml never silently breaks the voice/chat path.
    """
    if not isinstance(value, str):
        return "auto"
    code = value.strip().lower()
    return code if code in _REPLY_LANGS else "auto"


# Spoken fallback when the ENTIRE provider chain fails (no key, depleted
# credits, all rate-limited). The detailed provider/billing diagnostic
# (``_format_provider_chain_error``) is developer-facing and must NEVER reach
# the voice path — a butler does not read "Account-Problem bei grok …
# console.x.ai/team/billing" aloud (live complaint 2026-06-01). Instead we
# speak a short, provider-agnostic apology in the user's SELECTED reply
# language (de/en/es; "auto" → German, the default locale). Three variants
# per language so repeated failures in one session don't sound robotic
# (mirrors the ACK-variant approach). The full diagnostic stays in the logs.
_PROVIDER_DOWN_PHRASES: dict[str, tuple[str, ...]] = {
    "de": (
        "Entschuldige, ich komme gerade nicht an mein Sprachmodell. Einen Moment, bitte.",
        (
            "Tut mir leid, mein Sprachmodell ist im Moment nicht erreichbar. "
            "Ich versuche es gleich erneut."
        ),
        (
            "Ich kann gerade nicht antworten — die Verbindung zu meinem Modell hakt. "
            "Gib mir kurz Zeit."
        ),
    ),
    "en": (
        "Sorry, I can't reach my language model right now. One moment, please.",
        "I'm afraid my language model is unavailable at the moment. I'll try again shortly.",
        "I can't answer just now — my connection to the model is failing. Give me a second.",
    ),
    "es": (
        "Lo siento, ahora mismo no puedo acceder a mi modelo de lenguaje. Un momento, por favor.",
        (
            "Me temo que mi modelo de lenguaje no está disponible en este momento. "
            "Lo intentaré de nuevo enseguida."
        ),
        "No puedo responder ahora mismo: la conexión con mi modelo está fallando. Dame un segundo.",
    ),
}


def _provider_down_phrase(lang: str, idx: int) -> str:
    """Localized, provider-agnostic apology for a total brain-chain failure.

    ``lang`` is a reply-language code (de/en/es); anything else — notably
    "auto" — falls back to German (the default locale). ``idx`` rotates
    deterministically through the three variants so repeated failures in one
    session don't repeat the identical sentence. Voice-safe by construction:
    no provider names, no URLs, no jargon (anti-AP-11 / ADR-0010).
    """
    variants = _PROVIDER_DOWN_PHRASES.get(
        (lang or "").strip().lower(), _PROVIDER_DOWN_PHRASES["de"]
    )
    return variants[idx % len(variants)]


class BrainManager:
    """Top-level orchestrator with intent router and smart fallback."""

    def __init__(
        self,
        config: JarvisConfig,
        bus: EventBus,
        *,
        core_memory: CoreMemory | None = None,
        recall: RecallStore | None = None,
        tools: dict[str, Tool] | None = None,
        local_action_tools: dict[str, Tool] | None = None,
        tool_executor: ToolExecutor | None = None,
        system_prompt_extra: str = "",
        user_profile: UserProfile | None = None,
        soul: Soul | None = None,
        people: PersonStore | None = None,
        curator: Curator | None = None,
        cost_meter: "CostMeterLike | None" = None,  # noqa: UP037
        awareness_manager: "AwarenessManager | None" = None,  # noqa: UP037
        wiki_injector: "WikiContextInjector | None" = None,  # noqa: UP037
        contacts: Any = None,
    ) -> None:
        self._config = config
        self._bus = bus
        # User-facing reply-language pin. "auto" mirrors the user's input
        # language (DE/EN/ES); a pinned code forces that language for every
        # reply (desktop "Languages" view). Consumed by
        # _reply_language_directive(); mutated live via set_reply_language().
        self._reply_language: str = normalize_reply_language(
            getattr(getattr(config, "brain", None), "reply_language", None)
        )
        self._core_memory = core_memory
        self._recall = recall
        self._tools = tools or {}
        self._local_action_tools = dict(local_action_tools or {})
        self._tool_executor = tool_executor
        self._system_prompt_extra = system_prompt_extra
        self._user_profile = user_profile
        self._soul = soul
        self._people = people
        # Chunk B (contacts): optional ContactStore (Contract 1, owned by Chunk
        # A). When set, _build_system_prompt() appends its compact name-index
        # (names + relationship only; details on demand via contact-lookup).
        # None until Chunk A is merged — the block is simply omitted (graceful).
        self._contacts = contacts
        # Phase A1: optional AwarenessManager. When set, _build_system_prompt()
        # injects a compact live snapshot (window/idle) as a fallback in case
        # the LLM does NOT call the awareness-snapshot tool. Plan §5 "Files to Modify".
        self._awareness_manager = awareness_manager
        # Phase 5 / ADR-0006: optional budget hook. Fed with aggregated usage
        # post-call; pre-call blocks when in cooldown or when the task/daily
        # budget is exceeded. When None, the feature is completely inactive —
        # no effect on the dispatch path.
        self._cost_meter = cost_meter
        self._curator = curator
        self._vision_provider = None
        # B5 Agent C: wiki context injector.  None = no-op (Agent B not merged
        # yet, or [wiki_context].enabled = false).  Set by factory.py for the
        # router tier only; sub-tiers never get wiki injection.
        self._wiki_injector: "WikiContextInjector | None" = wiki_injector
        # Per-turn wiki context suffix; set in generate() and consumed by
        # _build_system_prompt().  Reset to "" after each turn.
        self._wiki_context_suffix: str = ""
        # AD-OE6 zero-silent-drop signal. True for exactly one turn after the
        # whole provider fallback chain failed (no key / depleted credits /
        # rate-limited everywhere). The voice pipeline reads this to decide
        # whether to speak a spoken "all providers are down" fallback instead
        # of returning silently to LISTENING. A legitimate empty turn
        # (suppress_response fire-and-forget) leaves this False.
        self._last_turn_all_failed: bool = False
        # AD-OE6 companion signal. True for exactly one turn when the winning
        # provider finished with ``suppress_response`` (a fire-and-forget
        # ``spawn_worker`` background mission that reports back over the bus).
        # The voice pipeline reads this to tell a LEGIT silent turn (spawn —
        # stay silent) from a turn that produced no speech for any other reason
        # (function_call/CU without speech, empty content). The latter must NOT
        # drop the user into silence — it gets a spoken clarifying question
        # (live "Jarvis antwortet nie" cause 2026-06-08: conversational turns
        # returned a function_call and the turn ended mute).
        self._last_turn_suppressed: bool = False
        # AD-OE6 companion signal #2. True for exactly one turn when the winning
        # provider executed a DESKTOP-ACTION tool (computer_use / open_app /
        # click / type / …) but produced no narration text. A wordless desktop
        # action is a SUCCESS the user must hear confirmed — NOT a clarifying
        # question. Live bug 2026-06-09 (data/jarvis_desktop.log 16:27): the
        # router brain called computer_use, the CU loop opened Chrome ([cu] step
        # 1.1 open_app → step 2 done), Gemini emitted no text, and the pipeline
        # spoke "Wie meinst du das genau?" — so a successful action looked like
        # incomprehension. The pipeline reads this to speak a confirmation
        # instead. Reset to False each turn; only the winning provider sets it.
        self._last_turn_executed_action_tool: bool = False
        # Rotation cursor for the localized "brain unreachable" spoken fallback
        # (_provider_down_phrase). Advances once per total-failure turn so the
        # phrase varies instead of repeating verbatim.
        self._provider_down_idx: int = 0

        self._registry = BrainProviderRegistry()
        self._active_name: str = config.brain.primary
        # Last persist-to-disk outcome of ``switch(..., persist=True)``.
        # ``None`` = no persisting switch attempted yet. The provider route
        # reads this to report the ACTUAL disk result instead of echoing the
        # request flag (anti-silent-drop, AD-OE6).
        self.last_persist_ok: bool | None = None
        # Cache: (provider-name, model-name-or-None) → Brain-Instance
        self._brain_cache: dict[tuple[str, str | None], Brain] = {}

        # Latency sprint 2: provider caching is communicated to the brain plugins
        # via environment variables (they are stateless API adapters, not DI).
        # Always set rather than only-when-true so that a subsequent
        # reconfiguration via hot-reload works in both directions (true→false
        # disables it).
        import os as _os
        perf = getattr(config, "performance", None)
        if perf is not None:
            _os.environ["JARVIS_ANTHROPIC_PROMPT_CACHE"] = (
                "1" if getattr(perf, "anthropic_prompt_cache", False) else "0"
            )
            _os.environ["JARVIS_GEMINI_CONTEXT_CACHE"] = (
                "1" if getattr(perf, "gemini_context_cache", False) else "0"
            )
        self._history: list[BrainMessage] = []
        self._lock = asyncio.Lock()
        # Sticky override: "denk gründlich" sets _force_level="deep"
        # until the user says "denk schnell".
        self._force_level: str | None = None
        # Circuit breaker for 429-limited providers (skip for 30s)
        self._rate_tracker = RateLimitTracker(cooldown_s=30.0)
        # Session dead-list: providers that definitely have no key/auth in
        # THIS session. Filtered from the chain until session end or until the
        # next provider switch (user sets a key in the UI → switch triggers
        # reset). Prevents each voice turn from running through 8 sequential
        # "no API key" failures.
        self._dead_providers: set[str] = set()
        # Populated by from_tier_config(). Tier fallbacks are runtime
        # priorities, not just healthcheck metadata.
        self._configured_fallbacks: list[tuple[str, str | None]] = []
        # Persona mandate phase 3: deterministic force-spawn heuristic.
        # Lazily compiled from self._config.brain.routing.
        self._routing_patterns: tuple[
            re.Pattern[str], re.Pattern[str], re.Pattern[str]
        ] | None = None
        # User-Mandate 2026-05-14: strict-mode trigger-phrase regex
        # (compiled from `brain.routing.force_spawn_phrases`). Cached so
        # the hot path stays cheap.
        self._force_spawn_pattern: re.Pattern[str] | None = None
        # AD-12 / AP-OC5 (wave-4 router): optional handlers for OpenClaw
        # mission status/cancel. Injected via ``set_mission_command_handlers``
        # after bootstrap so the BrainManager constructor has no hard
        # dependency on MissionManager.
        self._openclaw_status_fn: (
            Callable[[str | None], Awaitable[str]] | None
        ) = None
        self._openclaw_cancel_fn: (
            Callable[[str | None], Awaitable[str]] | None
        ) = None

    # ------------------------------------------------------------------
    # Tiered-Routing-Factory (Phase 5)
    # ------------------------------------------------------------------

    @classmethod
    def from_tier_config(
        cls,
        tier: Literal["router"],
        config: JarvisConfig,
        bus: EventBus,
        *,
        provider_override: str | None = None,
        tools: dict[str, Tool] | None = None,
        local_action_tools: dict[str, Tool] | None = None,
        tool_executor: ToolExecutor | None = None,
        core_memory: CoreMemory | None = None,
        recall: RecallStore | None = None,
        user_profile: UserProfile | None = None,
        soul: Soul | None = None,
        people: PersonStore | None = None,
        awareness_manager: "AwarenessManager | None" = None,  # noqa: UP037
        contacts: Any = None,
    ) -> BrainManager:
        """Builds a BrainManager from the tier-specific config.

        Wave-4 migration: previously there were two tiers, ``router`` and
        ``sub_jarvis``. The Sub-Jarvis tier was replaced by the OpenClaw bridge
        (see docs/openclaw-bridge.md §11); only ``router`` remains.

        Reads `config.brain.router` and writes into a deep copy of JarvisConfig:
          - `brain.primary = tier_cfg.provider` (or `provider_override`)
          - `brain.deep_brain = tier_cfg.fallback_provider`

        The global `config` instance is left unchanged.

        Args:
            provider_override: When set, `tier_cfg.provider` is ignored and
                the override is used. This is the hook for the live provider
                switch: when the user says "wechsel auf gemini" via voice.
                The associated `tier_cfg.model` is then NOT used (it was
                intended for the original provider) — instead the default
                from TIER_DEFAULTS_BY_PROVIDER applies for the new provider.
        """
        tier_cfg = getattr(config.brain, tier, None)
        if tier_cfg is None:
            raise BrainConfigError(
                f"No [brain.{tier}] block in config. "
                f"Tiered routing requires [brain.router] in jarvis.toml."
            )

        local_config = config.model_copy(deep=True)
        effective_provider = provider_override or tier_cfg.provider
        local_config.brain.primary = effective_provider
        local_config.brain.deep_brain = tier_cfg.fallback_provider

        # Tier model resolver:
        # - If a live override is active: ignore tier_cfg.model (it was for
        #   the old provider). The default from TIER_DEFAULTS applies.
        # - If no override: respect tier_cfg.model, then fall back to the default.
        explicit_model = None if provider_override else tier_cfg.model
        resolved_model = _resolve_tier_model(tier, effective_provider, explicit_model)
        if resolved_model and effective_provider in (local_config.brain.providers or {}):
            local_config.brain.providers[effective_provider].model = resolved_model

        # BUG-LATENCY (2026-05-24): the router is a pure dispatcher — it must not
        # burn seconds on "extended thinking". Cap the thinking budget on the
        # router provider config. ``local_config`` is a deep copy, so this affects
        # ONLY the router brain — workers/critic (separate config load) keep full
        # frontier reasoning (user mandate). Gemini honours thinking_budget=0 as
        # "no thinking"; providers without the field ignore it harmlessly.
        router_prov_cfg = local_config.brain.providers.get(effective_provider)
        if router_prov_cfg is not None:
            try:
                router_prov_cfg.thinking_budget = 0
            except (AttributeError, TypeError):
                pass

        configured_fallbacks: list[tuple[str, str | None]] = []

        if tier_cfg.fallback_provider:
            resolved_fallback = _resolve_tier_model(
                tier, tier_cfg.fallback_provider, tier_cfg.fallback_model
            )
            configured_fallbacks.append((tier_cfg.fallback_provider, resolved_fallback))
        # BUG-LATENCY (2026-05-24): only mutate the fallback provider's `model`
        # when it is a DIFFERENT provider than the primary. When primary ==
        # fallback (e.g. [brain.router] provider="gemini" + fallback_provider=
        # "gemini"), both share the same providers["gemini"] entry, so this
        # write used to clobber the primary's fast model (flash) with the deep
        # fallback model (pro) — the router then ran every turn on the slow
        # reasoning model (~9 s thinking). The same-provider fallback model is
        # still carried in `configured_fallbacks` for the chain below.
        if (
            tier_cfg.fallback_provider
            and tier_cfg.fallback_provider != effective_provider
            and tier_cfg.fallback_provider in (local_config.brain.providers or {})
        ):
            resolved_fallback = _resolve_tier_model(
                tier, tier_cfg.fallback_provider, tier_cfg.fallback_model
            )
            if resolved_fallback:
                local_config.brain.providers[tier_cfg.fallback_provider].model = resolved_fallback

        if tier_cfg.fallback_provider_2:
            resolved_fallback_2 = _resolve_tier_model(
                tier, tier_cfg.fallback_provider_2, tier_cfg.fallback_model_2
            )
            configured_fallbacks.append((tier_cfg.fallback_provider_2, resolved_fallback_2))
            if (
                resolved_fallback_2
                and tier_cfg.fallback_provider_2 != effective_provider
                and tier_cfg.fallback_provider_2 in (local_config.brain.providers or {})
            ):
                local_config.brain.providers[tier_cfg.fallback_provider_2].model = (
                    resolved_fallback_2
                )

        manager = cls(
            config=local_config,
            bus=bus,
            core_memory=core_memory,
            recall=recall,
            tools=tools,
            local_action_tools=local_action_tools,
            tool_executor=tool_executor,
            user_profile=user_profile,
            soul=soul,
            people=people,
            awareness_manager=awareness_manager,
            contacts=contacts,
        )
        manager._configured_fallbacks = configured_fallbacks

        # Bug E fix (2026-04-29) — pre-boot key check.
        # Push providers without an API key directly into _dead_providers,
        # otherwise they produce BrainTurnStarted hallucination tags in the DB
        # before _ensure_client() crashes. Example: user only has Anthropic +
        # Gemini + xAI keys → openai/openrouter are not tried at all.
        from jarvis.core import config as _cfg_mod
        from jarvis.core.config import PROVIDER_SECRET_CANDIDATES
        provider_to_slots: dict[str, list[str]] = {}
        for secret_key, provider_name in _SECRET_KEY_TO_BRAIN.items():
            provider_to_slots.setdefault(provider_name, []).append(secret_key)
        for provider_name, secret_specs in PROVIDER_SECRET_CANDIDATES.items():
            try:
                key_value = _cfg_mod.get_secret_any(secret_specs)
            except Exception:  # noqa: BLE001
                key_value = None
            if not key_value:
                manager._dead_providers.add(provider_name)
                # codex-as-BRAIN genuinely needs an OpenAI API key, so disabling
                # it here is correct. But codex-as-SUBAGENT runs on the ChatGPT
                # OAuth login (no API key), so the bare "deaktiviert" line read as
                # "codex is unusable" when the sub-agent still works. Log honestly.
                if provider_name == "codex":
                    oauth_ok = False
                    try:
                        from jarvis.codex_auth import CodexAuthService

                        st = CodexAuthService().status()
                        oauth_ok = bool(st.connected and st.mode == "chatgpt")
                    except Exception:  # noqa: BLE001
                        oauth_ok = False
                    if oauth_ok:
                        log.info(
                            "Pre-Boot-Key-Check: codex hat keinen OpenAI-API-Key -> "
                            "als BRAIN-Provider deaktiviert, aber als Sub-Agent "
                            "nutzbar (ChatGPT-OAuth-Login)."
                        )
                        continue
                log.info(
                    "Pre-Boot-Key-Check: kein Key in %s -> Provider '%s' deaktiviert.",
                    provider_to_slots.get(provider_name, [provider_name]),
                    provider_name,
                )
        return manager

    # ------------------------------------------------------------------
    # Provider instance cache
    # ------------------------------------------------------------------

    def available_providers(self) -> list[str]:
        return self._registry.available()

    def failed_providers(self) -> dict[str, str]:
        return self._registry.failed()

    def _provider_cfg(self, name: str):
        return self._config.brain.providers.get(name)

    def _fast_model(self, name: str) -> str | None:
        cfg = self._provider_cfg(name)
        if cfg is None:
            return get_tier_default_model("router", name)
        return cfg.model or get_tier_default_model("router", name)

    def _deep_model(self, name: str) -> str | None:
        cfg = self._provider_cfg(name)
        if cfg is None:
            return get_tier_default_model("deep", name)
        return (
            getattr(cfg, "deep_model", None)
            or get_tier_default_model("deep", name)
        )

    def _get_brain(self, name: str, model: str | None = None) -> Brain:
        """Retrieves a Brain instance from the cache, or builds a new one."""
        key = (name, model)
        if key in self._brain_cache:
            return self._brain_cache[key]

        kwargs: dict[str, Any] = {}
        if model:
            kwargs["model"] = model
        cfg = self._provider_cfg(name)
        if cfg is not None and cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        # Latency sprint 1: pass through thinking budget — currently only Gemini
        # accepts this parameter. Other providers raise TypeError, then the
        # second attempt below retries without kwargs.
        if (
            name == "gemini"
            and cfg is not None
            and getattr(cfg, "thinking_budget", None) is not None
        ):
            tb = cfg.thinking_budget
            # Gemini Pro models REQUIRE thinking mode and reject budget=0 with
            # 400 "Budget 0 is invalid. This model only works in thinking mode."
            # The router caps budget to 0 for its fast (flash) model, but the
            # SAME gemini provider config is reused for the deep/pro fallback —
            # forwarding 0 there 400s the call and silently drops the turn.
            # Only forward budget=0 to non-pro models; let pro fall back to the
            # SDK default (auto thinking).
            eff_model = (model or getattr(cfg, "model", "") or "")
            if not (tb == 0 and "pro" in eff_model.lower()):
                kwargs["thinking_budget"] = tb

        try:
            inst = self._registry.instantiate(name, **kwargs)
        except TypeError:
            inst = self._registry.instantiate(name)
        self._brain_cache[key] = inst
        return inst

    @property
    def active_provider(self) -> str:
        return self._active_name

    # ------------------------------------------------------------------
    # Dispatcher builder
    # ------------------------------------------------------------------

    def _build_dispatcher(
        self,
        brain: Brain,
        *,
        tools_override: dict[str, Tool] | None = None,
    ) -> BrainDispatcher:
        """Builds the dispatcher with an optional tool override.

        Bug fix 2026-05-01 (voice session 2026-04-30 22:38): when smalltalk is
        clearly identified, ``tools_override={}`` is set — the LLM then has no
        tools in its toolbox and cannot be tempted to hallucinate
        ``spawn_worker``. ``None`` (default) = full tool visibility.
        """
        tools = tools_override if tools_override is not None else self._tools
        system_prompt = self._build_system_prompt()
        # Per-plugin usage guidance for whichever plugins are active this turn
        # (the "MCP + thin skill" reliability layer). Appended last so it sits
        # closest to the turn; only present when a plugin tool is in scope.
        cards = self._plugin_usage_cards_block(tools)
        if cards:
            system_prompt = f"{system_prompt}\n\n{cards}"
        return BrainDispatcher(
            brain,
            tools=tools,
            executor=self._tool_executor,
            system_prompt=system_prompt,
            max_tokens=self._config.brain.max_tokens,
        )

    @property
    def reply_language(self) -> str:
        """The active reply-language pin: ``auto`` | ``de`` | ``en`` | ``es``."""
        return self._reply_language

    def set_reply_language(self, lang: str) -> None:
        """Live-switch the reply-language pin (desktop "Languages" view).

        Takes effect on the next turn (the directive is rebuilt per call to
        ``_build_system_prompt``). Raises ``ValueError`` for unknown codes so a
        bad REST payload surfaces as a 4xx instead of silently no-op'ing.
        """
        code = lang.strip().lower() if isinstance(lang, str) else ""
        if code not in _REPLY_LANGS:
            raise ValueError(
                f"unknown reply language {lang!r} (allowed: {sorted(_REPLY_LANGS)})"
            )
        self._reply_language = code

    def _next_provider_down_phrase(self) -> str:
        """Localized 'I can't reach my model' apology + advance the rotation.

        Spoken when the whole provider chain fails. Provider-agnostic and
        voice-safe (no names/URLs) — the actionable diagnostic is logged, never
        spoken (live complaint 2026-06-01: the grok/Anthropic billing message
        was read aloud while Gemini was the active provider).
        """
        phrase = _provider_down_phrase(self._reply_language, self._provider_down_idx)
        self._provider_down_idx += 1
        return phrase

    def _reply_language_directive(self) -> str:
        """The reply-language instruction appended last to the system prompt.

        Written in English (Output Language Policy) but names the target
        language explicitly and is placed last so it overrides the otherwise
        German prompt. Pinned modes carve out proper nouns / brand names /
        technical identifiers so e.g. "Anthropic" or "GitHub" are never
        translated — the user's explicit requirement.
        """
        name = _REPLY_LANG_NAMES.get(self._reply_language)
        if name is not None:
            return (
                f"REPLY LANGUAGE — MANDATORY: Always reply in {name}, no matter "
                f"which language the user writes or speaks in. This overrides any "
                f"other language cue anywhere in this prompt. Keep proper nouns, "
                f"brand / product / company names and technical identifiers "
                f"(e.g. 'Anthropic', 'GitHub', file paths, code, commands) "
                f"unchanged in their original form — never translate them. Keep the "
                f"reply natural and fluent in {name}."
            )
        # auto: mirror whatever the user used.
        return (
            "REPLY LANGUAGE: Reply in the same language the user writes or speaks "
            "in — auto-detect German, English or Spanish and mirror it. Keep proper "
            "nouns and technical identifiers in their original form."
        )

    def _build_system_prompt(self) -> str:
        """Builds the system prompt with OpenClaw-style workspace injection.

        Layer order (OpenClaw priority map):
        1. SOUL.md           — Jarvis' own persona (who I am, tone rules)
        2. JARVIS_PERSONA.md — voice persona incl. ECHO-PARAPHRASE section
                               and hangup contract (mandate phase 2 effect)
        3. USER.md           — about the user (name, communication style, values, …)
        4. people/           — list of known people in the user's environment
        5. CoreMemory        — legacy JSON facts (transitional, kept for back-compat)
        6. Base-Prompt       — voice rules
        """
        parts: list[str] = []

        # Configurable assistant identity. Derived from the wake phrase (so a
        # custom wake word "Micron" makes the assistant call itself Micron) or
        # an explicit [persona].name. When it is NOT the historical "Jarvis", a
        # prominent identity directive overrides the "Jarvis" mentions baked
        # into the persona files (SOUL.md / JARVIS_PERSONA.md), which are static
        # and cannot be parameterised. Placed first so it frames everything.
        name = resolve_assistant_name(getattr(self, "_config", None))
        if name != DEFAULT_ASSISTANT_NAME:
            parts.append(
                f"DEIN NAME IST {name.upper()}. Du heisst {name} — nicht Jarvis. "
                f"Wo die folgende Persona-Beschreibung 'Jarvis' sagt, gilt {name}. "
                f"Stell dich als {name} vor und unterschreibe, wenn ueberhaupt, als {name}."
            )

        if self._soul is not None:
            try:
                parts.append(self._soul.render_for_prompt())
            except Exception:  # noqa: BLE001
                pass

        # Mandate phase 2 (reactivated 2026-04-28): persona block from
        # JARVIS_PERSONA.md incl. ECHO-PARAPHRASE section and hangup contract.
        # Loader returns empty string when file is missing — no init crash.
        persona_block = load_persona_prompt()
        if persona_block:
            parts.append(persona_block)

        if self._user_profile is not None:
            try:
                parts.append(self._user_profile.render_for_prompt())
            except Exception:  # noqa: BLE001
                pass

        # Profile-write directive — only when the update_profile tool is actually
        # wired (else this would contradict the hard "do not invent tools" rule).
        # The legacy auto-curator is soft-disabled, so the brain itself must
        # persist durable personal facts via the tool; the next turn's profile
        # block (rendered above) then reflects them. See profile_update.py.
        if self._user_profile is not None and "update_profile" in self._tools:
            parts.append(
                "PROFIL-PFLEGE: Wenn der User einen dauerhaften Fakt ueber SICH "
                "SELBST nennt oder korrigiert — Name, Anrede, Sprache(n), Zeitzone, "
                "Geraete, Werte, Pet-Peeves, Kommunikations- oder Feedback-Stil — "
                "rufe still das Tool `update_profile`, um ihn ins Profil zu "
                "schreiben (zusaetzlich zu deiner normalen Antwort, ohne "
                "Rueckfrage). Keine sensiblen Kategorien (Politik/Religion/Gesundheit)."
            )

        # Chunk B (contacts): e-mail-by-name rule. No new e-mail tool exists —
        # the path is contact-lookup (resolve name -> e-mail) THEN gmail (send).
        # Only emitted when BOTH tools are wired (never instruct a tool that is
        # not present — the hard "do not invent tools" rule). The literal
        # "contact-lookup first" phrase is the directive's unambiguous marker.
        # ``getattr`` guard: some tests build the manager via __new__ (bypassing
        # __init__) and set only the attrs the prompt needs — tolerate a missing
        # _tools the same way the rest of this builder tolerates missing state.
        _tools_now = getattr(self, "_tools", None) or {}
        if "contact-lookup" in _tools_now and "gmail" in _tools_now:
            parts.append(
                "KONTAKTE: Wenn der User eine Person beim Namen nennt, um ihr eine "
                "E-Mail oder Nachricht zu schicken ('schreib eine Mail an Christoph'), "
                "rufe `contact-lookup` first, um den Namen zur gespeicherten E-Mail "
                "aufzuloesen, und sende dann mit `gmail`. Erfinde nie eine Adresse — "
                "wenn contact-lookup nichts findet, sag das."
            )

        if self._people is not None:
            try:
                people_block = self._people.render_for_prompt()
                if people_block:
                    parts.append(people_block)
            except Exception:  # noqa: BLE001
                pass

        # Chunk B (contacts): compact name-index of the user-curated contact
        # book (names + relationship only; e-mails/phones/address fetched on
        # demand via contact-lookup). None until Chunk A merges, "" when the
        # book is empty — either way no block is injected. Defensive try/except
        # so a store error never crashes the system-prompt build (AP-9-adjacent);
        # ``getattr`` guard tolerates __init__-bypassing tests (see _tools above).
        _contacts = getattr(self, "_contacts", None)
        if _contacts is not None:
            try:
                contacts_block = _contacts.render_for_prompt(max_chars=800)
                if contacts_block:
                    parts.append(contacts_block)
            except Exception:  # noqa: BLE001
                pass

        if self._core_memory is not None:
            # Mandatory: re-read BEFORE rendering. Otherwise the LLM only sees
            # facts that existed at init time — UI additions and remember-tool
            # writes from this process are in the file but not in the cache.
            try:
                self._core_memory.reload()
            except Exception:  # noqa: BLE001
                pass
            cm = self._core_memory.render_system_prompt_block()
            # Cap substantially larger than the old 400 characters — otherwise
            # even 5-10 facts get cut off mid-block and the LLM claims it knows
            # nothing. 2500 corresponds to ~600 tokens, stays prompt-cache-friendly.
            if len(cm) > 2500:
                cm = cm[:2500] + "…"
            parts.append(cm)

        # Phase A1: awareness snapshot as fallback when the LLM does not
        # actively call the awareness-snapshot tool. Defensive try/except
        # because a state read must never crash the system-prompt build.
        # Wave 2 (omni-latency): in cache-optimized mode this moves to the
        # per-turn user message (_build_turn_context) so the cached system
        # prefix stays byte-stable across turns. Legacy mode keeps it here.
        if self._awareness_manager is not None and not self._cache_optimized():
            try:
                snap = self._awareness_manager.state.snapshot_for_prompt(max_chars=600)
                if snap:
                    parts.append(f"AKTUELLER KONTEXT (auto-injected):\n{snap}")
            except Exception:  # noqa: BLE001
                pass

        # Skills-Brain-Integration (Track B): surface the installed, active
        # user skills so the router-tier brain can actually choose ``run_skill``
        # for them. Without this block the ``run-skill`` tool is registered but
        # the brain never learns which skills exist, so it is never selected.
        # Static content (changes only on install/promote), so unlike the
        # per-turn awareness snapshot above it stays in the cached system
        # prefix — mirrors the capability block below. Defensive try/except:
        # a renderer fault must never crash the system-prompt build. The lazy
        # import is intentional so a monkeypatched renderer resolves correctly.
        try:
            from jarvis.skills.prompt_injection import (
                render_available_skills_section,
            )
            from jarvis.skills.skill_context import try_get_skill_context

            _skill_ctx = try_get_skill_context()
            if _skill_ctx is not None:
                _skills_section = render_available_skills_section(_skill_ctx.registry)
                if _skills_section:
                    parts.append(_skills_section)
        except Exception:  # noqa: BLE001
            pass

        if self._system_prompt_extra:
            parts.append(self._system_prompt_extra)

        base = (
            f"Du bist {name}, der persoenliche Meta-Orchestrator dieses Users auf Windows 11. "
            "Stil: trocken, praezise, ein Hauch britischer Butler im Tony-Stark-JARVIS-Stil "
            "— nie servil, nie beflissen, nie speichelleckerisch. "
            "Sprich kurz (1 Satz), natuerlich, KEIN Markdown. "
            "STRENG VERBOTEN — generische Greeter-/Smalltalk-Phrasen, jede einzelne. Beispiele: "
            "'Hallo. Was brauchst du?', 'Was kann ich fuer dich tun?', 'Wie kann ich helfen?', "
            "'Womit kann ich dienen?', 'Mir gehts gut.', 'Schoen von dir zu hoeren.', "
            "'Gerne!', 'Grossartige Frage!', 'Selbstverstaendlich, Sir.' und alle Varianten davon. "
            "Diese Phrasen sind LEERLAUF — sie tragen null Information und sind explizit nicht erwuenscht. "
            f"Wenn der User gruesst ('Hallo', 'Hey', '{name}'), antworte SUBSTANZIELL: "
            "z.B. mit aktuellem Status, einer relevanten Beobachtung, oder einer trockenen Replik "
            "— NIE mit einer Greeter-Phrase. Wenn dir nichts substanzielles einfaellt, schweige (leerer Output). "
            "Bei Aktionen: Tools sofort aufrufen, mehrere im selben Turn wenn noetig. "
            "Bei Coding/Research/Deep-Reasoning: dispatch_to_harness mit openclaw oder python-script."
        )
        parts.append(base)

        # Tool selection rules — prevents the LLM from wildly firing
        # ``cli_supabase`` for "recherchiere zu Supabase" instead of using
        # ``search_web``. Intent → tool class → concrete tool.
        # Agent-C (capability-coupling): render registered capabilities
        # dynamically from the CapabilityRegistry when available.  Fall back
        # to the hardcoded block so the system degrades gracefully when
        # jarvis/core/capabilities.py has not been deployed yet (Agent A).
        lang = "de"  # system-prompt language is always DE (user preference)
        capability_block: str = ""
        try:
            from jarvis.core.capabilities import get_registry  # type: ignore[import]
            cap_reg = get_registry()
            rendered = cap_reg.render_for_prompt(lang)
            if rendered:
                capability_block = (
                    "REGISTRIERTE WERKZEUGE (vollständige Liste — keine anderen existieren):\n"
                    + rendered
                    + "\n\n"
                    "STRENGE REGEL: Du darfst NIEMALS behaupten, eine Aktion auszuführen, "
                    "die nicht in der obigen Liste steht. "
                    "Wenn der User danach fragt, antworte: "
                    "'Das kann ich noch nicht — mir fehlt das passende Werkzeug.' "
                    "Erfinde keine Tools.\n"
                    "You must NEVER claim to perform an action that is not in the list above. "
                    "If the user asks for one, reply: "
                    "'I can\\'t do that yet — I don\\'t have a registered tool for it.' "
                    "Do not invent tools."
                )
        except Exception:  # noqa: BLE001 — module not yet deployed, use fallback
            pass

        if capability_block:
            parts.append(capability_block)
        else:
            tool_routing = (
                "TOOL-SELECTION-REGELN (strikt):\n"
                "1) RECHERCHIEREN/ANALYSIEREN/ERKLÄREN/VERGLEICHEN/ZUSAMMENFASSEN "
                "(Info *über* ein Thema, nicht Aktion darauf):\n"
                "   → NUTZE: search_web (Primary). Fallback: dispatch_to_harness.\n"
                "   → NIEMALS: cli_* Tools, MCP-Action-Tools.\n"
                "   → Bsp: 'recherchiere zu Supabase' → search_web('Supabase'), NICHT cli_supabase.\n"
                "2) AKTION auf verbundenem System (öffne, starte, deploye, migrate, liste MEINE X):\n"
                "   → NUTZE: cli_* / MCP-Tools / dispatch_to_harness.\n"
                "   → Bsp: 'liste meine Supabase-Projekte' → cli_supabase 'supabase projects list'.\n"
                "3) CODE SCHREIBEN/REFACTOREN/DEBUGGEN:\n"
                "   → NUTZE: dispatch_to_harness (openclaw).\n"
                "4) Unklar? → search_web (Read-only, kein Schaden) oder Rückfrage an User.\n"
                "Der Unterschied zwischen (1) und (2) liegt am Intent, nicht am Thema: "
                "'über X' = Search, 'mit X tun' = Action.\n\n"
                "STRENGE REGEL: Du darfst NIEMALS behaupten, eine Aktion auszuführen, "
                "die nicht in der obigen Liste steht. "
                "Wenn der User danach fragt, antworte: "
                "'Das kann ich noch nicht — mir fehlt das passende Werkzeug.' "
                "Erfinde keine Tools.\n"
                "You must NEVER claim to perform an action that is not in the list above. "
                "If the user asks for one, reply: "
                "'I can\\'t do that yet — I don\\'t have a registered tool for it.' "
                "Do not invent tools."
            )
            parts.append(tool_routing)

        # B5 Agent C: per-turn wiki context suffix.  Set by generate() via
        # maybe_inject() just before the first provider call, consumed here,
        # and reset to "" in the finally-block of generate().  Empty string
        # = no injection (no-op path, search returned nothing, or timed out).
        # Wave 2 (omni-latency): wiki context also moves to the per-turn user
        # message in cache-optimized mode (keeps the cached system prefix stable).
        if self._wiki_context_suffix and not self._cache_optimized():
            parts.append(self._wiki_context_suffix)

        # Reply-language directive LAST — highest recency-salience so it wins
        # over the otherwise German prompt above it. Byte-stable across turns
        # (only changes on an explicit language switch), so it stays prompt-
        # cache-friendly.
        parts.append(self._reply_language_directive())

        return "\n\n".join(p for p in parts if p)

    def _cache_optimized(self) -> bool:
        """True when the cache-optimized prompt layout (Wave 2) is enabled."""
        perf = getattr(self._config, "performance", None)
        return bool(getattr(perf, "cache_optimized_prompt", False))

    def _is_pointer_intent(self, user_text: str) -> bool:
        """True when this is a deictic AI-Pointer turn ("worauf zeige ich?").

        Cheap regex gate, honoured only when ``[pointer].enabled``. Drives the
        per-turn grounding (scope images to the cursor crop, drop the full-screen
        screenshot tool) so the brain answers from the cursor, not a screen guess.
        """
        cfg = getattr(self._config, "pointer", None)
        if not bool(getattr(cfg, "enabled", True)):
            return False
        try:
            from jarvis.pointer.intent import is_pointing_intent  # noqa: PLC0415

            return is_pointing_intent(user_text)
        except Exception:  # noqa: BLE001 — gate must never block a turn
            return False

    def _start_pointer_task(self, user_text: str, is_smalltalk_turn: bool):
        """Launch the deictic AI-Pointer resolution as a background task (AP-9).

        Returns an ``asyncio.Task`` resolving to ``(prompt_block, crop_image)``, or
        ``None`` when the feature is disabled or the turn is smalltalk. The task
        does the regex deictic gate itself, so a non-pointing utterance completes
        instantly with ``("", None)`` and a headless host fast-skips before any
        worker-thread dispatch. Started before the vision-image await so it runs
        concurrently rather than serially on the hot path. See
        docs/plans/ai-pointer/DESIGN.md.
        """
        try:
            import asyncio  # noqa: PLC0415

            from jarvis.pointer.turn import resolve_turn_pointer

            cfg = getattr(self._config, "pointer", None)
            if not bool(getattr(cfg, "enabled", True)) or is_smalltalk_turn:
                return None
            return asyncio.create_task(
                resolve_turn_pointer(
                    user_text,
                    enabled=True,
                    timeout_s=float(getattr(cfg, "timeout_s", 0.12)),
                    crop_radius=int(getattr(cfg, "crop_radius", 64)),
                )
            )
        except Exception:  # noqa: BLE001 — never crash a turn on pointer setup
            log.debug("AI Pointer task launch skipped", exc_info=True)
            return None

    def _build_turn_context(self) -> str:
        """Per-turn dynamic context for the user message (cache-optimized mode).

        Date/time + awareness snapshot + wiki context. Empty in legacy mode
        (there these live in the system prompt instead). Riding on the user
        message keeps the cached system prefix byte-stable across turns, which
        is what actually lets the Gemini/Anthropic prompt cache hit.
        """
        if not self._cache_optimized():
            return ""
        from datetime import datetime

        parts: list[str] = [
            # Date/time belongs per-turn, never in the cached prefix (also fixes
            # the missing BUG-005 date injection).
            f"[Aktueller Zeitpunkt: {datetime.now().strftime('%A, %d.%m.%Y %H:%M')}]"
        ]
        if self._awareness_manager is not None:
            try:
                snap = self._awareness_manager.state.snapshot_for_prompt(max_chars=600)
                if snap:
                    parts.append(f"AKTUELLER KONTEXT (auto-injected):\n{snap}")
            except Exception:  # noqa: BLE001
                pass
        if self._wiki_context_suffix:
            parts.append(self._wiki_context_suffix)
        return "\n\n".join(p for p in parts if p)

    # ------------------------------------------------------------------
    # Explicit switching
    # ------------------------------------------------------------------

    async def switch(self, provider_name: str, *, persist: bool = False) -> None:
        """Switches the active provider.

        Even switching to the ALREADY active provider has an effect: it acts
        as the reset button for session caches (dead-list, brain-cache,
        rate-tracker). Users typically click "Set as active" in the UI
        immediately after setting an API key — this should bring the fresh key
        into the chain right away rather than being a no-op.

        Args:
            provider_name: Provider ID (entry-point name) or voice alias.
            persist: If True, the selection is written to jarvis.toml [brain]
                primary and survives a restart.
        """
        canonical = PROVIDER_ALIASES.get(provider_name.lower().strip(), provider_name)
        async with self._lock:
            if canonical == self._active_name:
                # Re-activation of the already active provider — reset caches
                # so a newly set key takes effect on the next turn (otherwise
                # the provider stays in _dead_providers).
                self._reset_provider_caches()
                self.last_persist_ok = (
                    self._persist_primary(canonical) if persist else False
                )
                return
            try:
                self._get_brain(canonical, self._fast_model(canonical))
            except KeyError:
                log.error("Unbekannter Provider: %s", canonical)
                self.last_persist_ok = False
                return
            previous = self._active_name
            self._active_name = canonical
            self._reset_provider_caches()
            self.last_persist_ok = (
                self._persist_primary(canonical) if persist else False
            )
            await self._bus.publish(
                BrainProviderSwitched(from_provider=previous, to_provider=canonical)
            )

    def _reset_provider_caches(self) -> None:
        """Clears session state that would block a freshly set key."""
        self._dead_providers.clear()
        self._brain_cache.clear()
        self._rate_tracker.clear()

    def reactivate_provider(self, provider: str) -> None:
        """Lifts the session-level deactivation of a provider.

        Called by the ``SecretConfigured`` event handler when the user sets a
        key via Sidebar → API Keys. Effects:
          1. Provider leaves ``_dead_providers`` → returns to the chain.
          2. Its brain cache entry is discarded so the next ``_get_brain``
             call instantiates a fresh instance with the new key.
          3. Any active rate-limit cooldown is also cleared — the user reset
             clearly signals "I want to use this now".

        Idempotent: calling twice is allowed and is a no-op with a clean cache.
        """
        was_dead = provider in self._dead_providers
        self._dead_providers.discard(provider)
        keys_to_drop = [k for k in self._brain_cache if k[0] == provider]
        for k in keys_to_drop:
            self._brain_cache.pop(k, None)
        self._rate_tracker.clear(provider)
        if was_dead or keys_to_drop:
            log.info(
                "Provider '%s' reaktiviert (dead=%s, brain_cache_dropped=%d)",
                provider, was_dead, len(keys_to_drop),
            )

    @staticmethod
    def _persist_primary(name: str) -> bool:
        """Persist ``brain.primary`` to disk (all three layers via config_writer).

        Returns ``True`` iff the write actually succeeded, ``False`` otherwise.
        A failure is logged loudly (anti-silent-drop) so the caller can report
        the real disk outcome up to the UI instead of echoing the request flag.
        """
        # Lazy import: config_writer needs tomlkit (optional dep in the wizard path).
        try:
            from jarvis.core import config_writer

            config_writer.set_brain_primary(name)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to persist brain.primary=%r: %s", name, exc)
            return False

    def _detect_switch_intent(self, text: str) -> str | None:
        """Strict gate-based detector — no more substring matching.

        Delegates to `voice_command_gate.match_voice_command`, which only
        returns a match for unambiguous patterns like "wechsel auf gemini".
        Harmless sentences like "ich gehe auf meinem Weg" no longer match.
        """
        match = match_voice_command(text)
        if match is None or match.kind != "provider_switch":
            return None
        return match.target

    def _detect_cancel_intent(self, text: str) -> bool:
        """True when the user wants to cancel a running OpenClaw task."""
        match = match_voice_command(text)
        return match is not None and match.kind == "cancel"

    def _detect_depth_override(self, text: str) -> str | None:
        """Detects 'denk gründlich/schnell' → sticky override to deep/fast.

        Uses the VoiceCommandGate for consistent pattern lists.
        """
        match = match_voice_command(text)
        if match is None:
            return None
        if match.kind == "depth_deep":
            return "deep"
        if match.kind == "depth_fast":
            return "fast"
        return None

    # ------------------------------------------------------------------
    # Force-Spawn-Heuristik (Persona-Mandat Phase 3)
    # ------------------------------------------------------------------

    def _get_routing_patterns(
        self,
    ) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
        """Lazily compiles the three force-spawn regexes from BrainRoutingConfig."""
        if self._routing_patterns is None:
            cfg = self._config.brain.routing
            self._routing_patterns = (
                _build_verb_pattern(list(cfg.spawn_verbs)),
                _build_marker_pattern(list(cfg.external_system_markers)),
                _build_smalltalk_pattern(list(cfg.smalltalk_allowlist)),
            )
        return self._routing_patterns

    def _get_force_spawn_pattern(self) -> re.Pattern[str]:
        """Compile the strict-mode trigger-phrase regex (User-Mandate 2026-05-14).

        Multi-word phrases are matched literal-substring, single-word
        markers with `\\b` boundaries so 'spawn' matches 'spawn' /
        'spawne' / 'spawnen' but not arbitrary substrings.
        """
        if self._force_spawn_pattern is None:
            phrases = list(self._config.brain.routing.force_spawn_phrases)
            if not phrases:
                self._force_spawn_pattern = _NEVER_MATCH_RE
            else:
                parts = [re.escape(p) for p in phrases]
                # Each part is a literal substring; boundary handling
                # mirrors `_build_smalltalk_pattern` so multi-word
                # phrases like "deep dive" match without requiring word
                # boundaries inside.
                self._force_spawn_pattern = re.compile(
                    r"(?:^|\b)(?:" + "|".join(parts) + r")(?:\b|$)",
                    re.IGNORECASE,
                )
        return self._force_spawn_pattern

    def set_mission_command_handlers(
        self,
        *,
        status_fn: Callable[[str | None], Awaitable[str]] | None,
        cancel_fn: Callable[[str | None], Awaitable[str]] | None,
    ) -> None:
        """Wires the status/cancel handlers for OpenClaw mission reads.

        Called by bootstrap (e.g. ``jarvis/missions/init.py`` or server
        startup) once the MissionManager is ready.

        AD-12 (status read via voice without spawn): when ``status_fn`` is
        set, the ``generate()`` path deterministically calls
        ``status_fn(mission_id)`` on a detected status phrase instead of
        asking the LLM or triggering a new OpenClaw spawn.

        AP-OC5: pattern-match-first discipline — when no handlers are
        registered (e.g. tests, headless mode), the code falls back to the
        normal force-spawn/tool-use path (no crash).

        Args:
            status_fn: ``async (mission_id: str | None) -> str`` — returns a
                TTS-safe status announcement. ``mission_id=None`` means
                "summarise all active missions".
            cancel_fn: ``async (mission_id: str | None) -> str`` — cancels
                mission(s) and returns a confirmation. ``mission_id=None``
                means "cancel all active OpenClaw missions".
        """
        self._openclaw_status_fn = status_fn
        self._openclaw_cancel_fn = cancel_fn

    def _check_unsupported_intent(self, user_text: str) -> str | None:
        """Agent-C capability gate: return a deterministic refusal when the
        utterance has an action intent that no registered capability covers.

        Returns ``None`` when:
        - The CapabilityRegistry module is not yet deployed (graceful no-op).
        - The utterance is smalltalk / Q&A (no action intent detected).
        - A registered capability resolves the intent.

        Returns a short, TTS-safe refusal string when:
        - ``registry.has_action_intent(text)`` is True AND
        - ``registry.resolve_intent(text)`` is None AND
        - ``_is_smalltalk(text)`` is False.

        No LLM call is made here — pure regex + registry lookup (AP-11).
        """
        try:
            from jarvis.core.capabilities import get_registry  # type: ignore[import]
        except Exception:  # noqa: BLE001 — module not yet deployed
            return None

        try:
            reg = get_registry()
            t = (user_text or "").strip()
            if not t:
                return None
            if self._is_smalltalk(t):
                return None
            # Empty/unseeded registry → step aside (fail-safe). has_action_intent
            # matches the STATIC universal verb catalogue (seed-independent), so
            # without this guard an unseeded registry refuses EVERY action
            # utterance (resolve_intent is always None when nothing is
            # registered) and pre-empts the force-spawn path. Mirrors the
            # populated-guard in local_action_gate.match_local_action. Defense in
            # depth behind the boot seed in brain/factory.build_default_brain —
            # live bug 2026-05-25 ("Kannst du einen Subagent spawnen").
            if not getattr(reg, "all", lambda: ())():
                return None
            # Desktop-control commands (compound open-and-operate, GUI verbs)
            # are never "unsupported" — computer-use is the universal GUI
            # integration and the fast path routes them there. Defense in depth
            # so a sparse/older registry can't pre-empt that with the canned
            # refusal (live bug 2026-05-25: "oeffne WhatsApp und schreib").
            if _looks_like_desktop_control(_gate_normalize(t)):
                return None
            # 2026-06-01: the sub-agent is the universal capability for generic
            # work (analyse/build/fix/code/research/git). Only a SPECIFIC
            # external integration the worker cannot satisfy (mail/calendar/
            # Spotify/social/delivery) is genuinely "unsupported". Everything
            # else falls through to the force-spawn path so a sub-agent task is
            # delegated natively instead of refused with "kann ich noch nicht"
            # (live forensic 2026-06-01: a sub-agent task was refused, then only
            # spawned once the user said "Subagent" explicitly).
            if not requires_external_integration(t):
                return None
            if reg.has_action_intent(t) and reg.resolve_intent(t) is None:
                # Detect user language from text heuristic (simple: if latin
                # chars + german umlaut present → DE, else EN).
                _de_markers = re.search(r"[äöüÄÖÜß]|(?:bitte|kannst|schick|trag|sende)", t, re.I)
                if _de_markers:
                    return (
                        "Das kann ich noch nicht. Mir fehlt dafür ein Werkzeug — "
                        "wenn du mir verrätst welches MCP oder welche Integration "
                        "zuständig wäre, kann ich's lernen."
                    )
                return (
                    "I can't do that yet. I don't have a registered tool for it. "
                    "Tell me which MCP or integration should handle it and I can learn."
                )
        except Exception:  # noqa: BLE001 — registry error must not crash generate()
            log.debug("_check_unsupported_intent: registry error", exc_info=True)
        return None

    def _is_smalltalk(self, user_text: str) -> bool:
        """Pure smalltalk allowlist check — independent of spawn-verb logic.

        Bug fix 2026-05-01 (voice session 2026-04-30 22:38): the user said
        "es geht ab", the smalltalk allowlist did not match (phrase was
        missing), force-spawn did nothing, the LLM had full tool visibility
        and hallucinated an OpenClaw spawn. Result: main Jarvis claimed to have
        started tests that it never started.

        Used in ``generate()`` to hide tools on clear smalltalk turns — the
        tool-use loop receives ``tools={}``, so the LLM can no longer spawn.

        Greeting-prefix guard (live bug 2026-06-07, data/jarvis_desktop.log
        18:19:07): the user said "Hallo, öffne ihn für mich". The allowlist
        substring-matched the leading "Hallo", the turn was treated as
        smalltalk, the action tools were hidden, and the brain spoke the
        anti-silence refusal "Das kann ich gerade nicht ausführen — mir fehlt
        dafür das passende Werkzeug." A greeting/politeness prefix in front of a
        REAL command is NOT smalltalk: strip the leading greeting run and, if
        what remains is itself a non-smalltalk action request, classify the turn
        as a command (return False) so the tools stay visible and force-spawn
        can fire. Standalone smalltalk ("Hallo", "Hallo, wie geht's?") and
        greeting-less chit-chat ("was machst du") are unaffected.
        """
        t = (user_text or "").strip()
        if not t:
            return False
        _, _, smalltalk_re = self._get_routing_patterns()
        if not smalltalk_re.search(t):
            return False
        stripped = _GREETING_PREFIX_RE.sub("", t).strip()
        if (
            stripped                                # something survives the greeting
            and stripped != t                       # a greeting prefix was removed
            and not smalltalk_re.search(stripped)   # the remainder isn't smalltalk too
            and self._remainder_has_action(stripped)  # the remainder is a real command
        ):
            return False
        return True

    def _remainder_has_action(self, text: str) -> bool:
        """True when *text* (a greeting-stripped remainder) is a real action
        command — an action verb, an external-system marker, or a desktop-control
        phrase. Pure regex, no LLM / no registry IO (AP-9/AP-11)."""
        verb_re, marker_re, _ = self._get_routing_patterns()
        if verb_re.search(text) or marker_re.search(text):
            return True
        return _looks_like_desktop_control(_gate_normalize(text))

    # Read-only tools that stay visible even on a smalltalk turn. The toolless
    # smalltalk path (2026-05-01) exists to stop the LLM hallucinating a
    # spawn_worker on chit-chat — that risk is the spawn/action tools, NOT the
    # read-only screenshot tool. Keeping `screenshot` visible lets the brain
    # look at the screen on demand (Wave 2) even on a greeting-prefixed turn,
    # e.g. "Hallo, lies mir vor was oben links steht" (live failure 2026-05-31).
    _SMALLTALK_SAFE_TOOLS: frozenset[str] = frozenset({"screenshot"})

    # Skill-aware routing guard (AD-S3, 2026-06-09 rebuild): the Skill matched
    # for the CURRENT turn, set early in generate() and overwritten on every
    # turn. While set, force-spawn and the local-action fast path stand down
    # and run-skill stays visible even on smalltalk turns.
    _skill_turn_match: Any | None = None
    # Direct-trigger handoff (AD-S4): the speech pipeline / chat hook notes a
    # trigger match here instead of macro-running it; generate() consumes it
    # on the next call and injects the skill instructions into the turn.
    _pending_forced_skill: tuple[str, str, str] | None = None
    _skill_turn_content: str = ""
    _skill_turn_source: str = "match"

    def note_skill_trigger(
        self, skill_name: str, *, content: str = "", source: str = "trigger"
    ) -> None:
        """Record a direct trigger match for the next generate() turn (AD-S4).

        Called by the speech pipeline / desktop chat hook when the
        TriggerMatcher fires. The skill is NOT executed here — generate()
        resolves it, injects its instructions into the turn context (or
        dispatches a mission for ``execution: mission`` skills), and the
        normal brain turn produces the spoken answer.
        """
        self._pending_forced_skill = (skill_name, content, source)

    def _consume_pending_skill_trigger(self, user_text: str) -> None:
        """Fold a noted trigger into this turn's skill match (AD-S4)."""
        pending = self._pending_forced_skill
        self._pending_forced_skill = None
        self._skill_turn_content = ""
        self._skill_turn_source = "match"
        if pending is None:
            return
        skill_name, content, source = pending
        try:
            from jarvis.skills.skill_context import try_get_skill_context

            ctx = try_get_skill_context()
            if ctx is None:
                return
            skill = ctx.registry.get(skill_name)
        except Exception:  # noqa: BLE001
            log.warning("noted skill trigger %r could not be resolved", skill_name)
            return
        self._skill_turn_match = skill
        self._skill_turn_content = content
        self._skill_turn_source = source

    def _match_skill_for_turn(self, user_text: str, lang: str = "auto") -> Any | None:
        """Deterministic skill-match probe (AD-S3). Returns the matched Skill or None.

        Uses the TriggerMatcher (incl. its tolerant filler-stripping pass) over
        the live SkillContext registry. Never raises — routing must not break
        when the skill subsystem is absent (headless/mock boots).
        """
        try:
            from jarvis.skills.skill_context import try_get_skill_context
            from jarvis.skills.trigger_matcher import TriggerMatcher

            ctx = try_get_skill_context()
            if ctx is None:
                return None
            res = TriggerMatcher(ctx.registry).match_voice_with_match(
                user_text, lang=lang
            )
            return res[0] if res is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _render_skill_turn_hint(self) -> str | None:
        """Steering hint appended to the turn context on a skill-matched turn."""
        skill = self._skill_turn_match
        if skill is None:
            return None
        name = getattr(skill, "name", "")
        return (
            f"[Skill match] The user's request matches the installed skill "
            f"`{name}` — call the run-skill tool with skill_name=\"{name}\" "
            "now and follow the returned instructions, unless that is "
            "clearly wrong."
        )

    def _render_skill_turn_injection(self, user_text: str) -> str | None:
        """Render the matched skill's instructions for direct turn injection.

        AD-S4: a matched turn short-circuits the run-skill round trip — the
        rendered instructions ride on the turn context, so the model executes
        them in this very turn (guaranteed invocation). Publishes
        ``SkillInvoked``. Falls back to the steering hint when rendering
        fails (the model can still call run-skill itself).
        """
        skill = self._skill_turn_match
        if skill is None:
            return None
        name = getattr(skill, "name", "")
        try:
            from jarvis.skills.skill_context import try_get_skill_context

            ctx = try_get_skill_context()
            if ctx is None:
                return self._render_skill_turn_hint()
            instructions = ctx.runner.render_instructions(
                skill,
                args={
                    "content": self._skill_turn_content,
                    "utterance": user_text,
                    "_trigger": self._skill_turn_source,
                },
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "skill instruction render failed for %s — hint fallback", name,
                exc_info=True,
            )
            return self._render_skill_turn_hint()
        self._publish_skill_invoked(name, source=self._skill_turn_source)
        return (
            f"[Skill instructions for `{name}` — the user's request matched "
            "this installed skill]\n"
            f"{instructions}\n\n"
            "Follow these skill instructions now, step by step, using your "
            "available tools; skip a step gracefully when its integration is "
            "unavailable. Answer the user with the RESULT — never read the "
            "instructions aloud."
        )

    def _publish_skill_invoked(self, skill_name: str, *, source: str) -> None:
        """Fire-and-forget SkillInvoked publish (AD-S6 observability)."""
        try:
            from jarvis.skills.schema import SkillInvoked

            event = SkillInvoked(
                source_layer="brain.manager",
                skill_name=skill_name,
                source=source,
            )
            loop = asyncio.get_running_loop()
            loop.create_task(self._bus.publish(event))
        except Exception:  # noqa: BLE001
            log.debug("SkillInvoked publish failed", exc_info=True)

    async def _maybe_dispatch_skill_mission(
        self, user_text: str, *, trace_id: UUID | None = None
    ) -> str | None:
        """Dispatch an ``execution: mission`` skill as a worker brief (AD-S5).

        Returns the optimistic ACK string when the mission was dispatched, or
        ``None`` for inline skills / when dispatch is impossible (the caller
        then keeps the inline-injection path — AD-OE6: no silent drop).
        """
        skill = self._skill_turn_match
        if skill is None:
            return None
        fm = getattr(skill, "frontmatter", None)
        if fm is None or getattr(fm, "execution", "inline") != "mission":
            return None
        tool = self._tools.get("spawn_worker")
        if tool is None or self._tool_executor is None:
            log.warning(
                "mission skill %s matched but spawn_worker unavailable — "
                "falling back to inline execution",
                getattr(skill, "name", "?"),
            )
            return None
        name = getattr(skill, "name", "")
        try:
            from jarvis.skills.skill_context import try_get_skill_context

            ctx = try_get_skill_context()
            if ctx is None:
                return None
            instructions = ctx.runner.render_instructions(
                skill,
                args={
                    "content": self._skill_turn_content,
                    "utterance": user_text,
                    "_trigger": self._skill_turn_source,
                },
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "mission skill %s could not render — inline fallback", name,
                exc_info=True,
            )
            return None
        args = {
            "utterance": (
                f"Execute the installed skill '{name}' as a background "
                f"mission. The user said: {user_text!r}\n\n"
                f"Skill instructions:\n{instructions}"
            ),
            "context_hints": [
                f"Dispatched deterministically from the skill system "
                f"(execution: mission, skill: {name})."
            ],
            "action": "",
            "target": "",
        }
        log.info("Mission skill dispatch: %s (%r)", name, user_text[:120])
        try:
            result = await self._tool_executor.execute(
                tool,
                args,
                user_utterance=user_text,
                trace_id=trace_id or uuid4(),
            )
        except Exception:  # noqa: BLE001
            log.warning("mission skill dispatch failed — inline fallback", exc_info=True)
            return None
        if not result.success:
            log.warning(
                "mission skill dispatch unsuccessful (%s) — inline fallback",
                result.error,
            )
            return None
        self._publish_skill_invoked(name, source=self._skill_turn_source)
        return str(result.output or "")

    def _smalltalk_tool_override(self) -> dict[str, "Tool"]:
        """Tool set visible on a smalltalk turn: only the read-only safe tools.

        Returns ``{}`` when none of the safe tools are registered — identical to
        the previous full-hide behaviour for deployments without a screenshot
        tool, so the anti-fake-spawn guard is unchanged there. On a
        skill-matched turn (AD-S3) ``run-skill`` stays visible so a greeting-
        style trigger ("guten Morgen" → morning-routine) can still invoke the
        skill.
        """
        allowed = self._SMALLTALK_SAFE_TOOLS
        if self._skill_turn_match is not None:
            allowed = allowed | {"run-skill"}
        return {
            n: t for n, t in self._tools.items()
            if n in allowed
        }

    def _apply_plugin_relevance(
        self, user_text: str, tools: dict[str, "Tool"]
    ) -> dict[str, "Tool"]:
        """Drop plugin tools (namespaced ``<id>/<tool>``) irrelevant to this turn.

        Keyword-only, no LLM / no IO (AP-9). Native (non-namespaced) tools are
        untouched. Defensive: any failure returns the unfiltered dict so a gate
        bug can never blind the brain on the voice path.
        """
        try:
            from jarvis.marketplace.plugin_relevance import filter_plugin_tools

            kept = filter_plugin_tools(user_text, list(tools.values()))
            kept_names = {t.name for t in kept}
            return {name: t for name, t in tools.items() if t.name in kept_names}
        except Exception:  # noqa: BLE001
            log.debug("plugin relevance gate failed; using full tool set", exc_info=True)
            return tools

    def _plugin_usage_cards_block(self, tools: dict[str, "Tool"]) -> str:
        """Markdown block of usage cards for the plugins active in this turn.

        Only the plugins whose tools are in ``tools`` (already relevance-gated)
        contribute, so the prompt stays small. Returns ``""`` when no plugin
        tools are active. Defensive: never raises on the prompt-build path.
        """
        try:
            from jarvis.marketplace.usage_cards.loader import load_usage_card

            plugin_ids: list[str] = []
            for name in tools:
                pid, sep, _ = name.partition("/")
                if sep and pid not in plugin_ids:
                    plugin_ids.append(pid)
            blocks: list[str] = []
            for pid in plugin_ids:
                card = load_usage_card(pid)
                if card and card.body:
                    blocks.append(f"### Plugin: {pid}\n{card.body}")
            if not blocks:
                return ""
            return "## Connected plugins — how to use them\n\n" + "\n\n".join(blocks)
        except Exception:  # noqa: BLE001
            log.debug("plugin usage-card block failed; omitting", exc_info=True)
            return ""

    async def _run_navigation_fast_path(
        self,
        user_text: str,
        *,
        trace_id: UUID | None = None,
    ) -> str | None:
        """Move the desktop UI to a section on a clear navigation command.

        Navigation is a deterministic "dumb" action (AD-OE3): a spoken/typed
        "zeig die Socials" / "open settings" switches the active sidebar section
        WITHOUT the LLM, and crucially before the capability gate — which would
        otherwise refuse it ('social' is an external-integration marker) — and
        before force-spawn. Executes the ``navigate`` tool (which publishes
        ``NavigateSidebar`` for the frontend) and returns a short spoken
        confirmation. Returns ``None`` when the utterance is not a navigation
        request, so the normal path runs. Pure regex match, no LLM (AP-11).
        """
        from jarvis.brain.navigation_intent import match_navigation_intent

        section = match_navigation_intent(user_text)
        if section is None:
            return None
        tool = self._tools.get("navigate")
        if tool is None or self._tool_executor is None:
            return None
        tid = trace_id or uuid4()
        try:
            await self._tool_executor.execute(
                tool,
                {"section": section},
                user_utterance=user_text,
                trace_id=tid,
            )
        except Exception:  # noqa: BLE001 — navigation must never crash the turn
            log.warning(
                "navigation fast-path failed for section %r", section, exc_info=True
            )
            return None
        label = section.replace("-", " ").title()
        is_de = bool(re.search(r"[äöüÄÖÜß]", user_text)) or bool(
            re.search(r"\b(zeig\w*|öffne|oeffne|geh\w*|wechs\w*|spring\w*)\b", user_text, re.I)
        )
        return f"Öffne {label}." if is_de else f"Opening {label}."

    def _should_force_spawn(self, user_text: str) -> bool:
        """Deterministic spawn guard for action requests.

        Wave-4 migration: previously ``_should_force_sub_jarvis`` with
        ``spawn_sub_jarvis`` tool lookup. The Sub-Jarvis tier was replaced by
        the OpenClaw bridge — see docs/openclaw-bridge.md §11.

        Order:
          1. Empty text or no spawn_worker tool → False.
          2. Smalltalk allowlist wins → False (even on verb hit).
          3. Action verb (``spawn_verbs``) → True.
          4. External system marker (``external_system_markers``) → True.
          5. Otherwise → False.
        """
        t = (user_text or "").strip()
        if not t:
            return False
        if "spawn_worker" not in self._tools or self._tool_executor is None:
            return False
        # BUG-LIVE-04 (Recon-Agent 3, 2026-05-16): Whisper transcribes
        # background TV / music / silence into well-known sentinel strings
        # ("Untertitelung des ZDF für funk", "Vielen Dank fürs Zuschauen",
        # "Musik", "Applaus", "you", "Tschüss", "Bis zum nächsten Mal").
        # In permissive mode these matched action verbs and triggered
        # heavy worker spawns that hung 630s and produced "Mission
        # fehlgeschlagen" announcements — without the user having said
        # anything. Filter the well-known seeds before the verb match.
        lowered = t.lower().rstrip(".!? ").strip()
        # H2 (2026-05-17): exact-only bucket runs first because it's the
        # cheap O(1) set lookup; prefix bucket needs an O(N) sweep.
        # `log` is the module-level logger bound at L73 -- BUG-026 fix.
        if lowered in _WHISPER_FP_EXACT_ONLY:
            log.info(
                "force_openclaw skipped: Whisper FP exact-only seed %r",
                lowered,
            )
            return False
        for _seed in _WHISPER_FP_PREFIX_OK:
            if lowered == _seed or lowered.startswith(_seed + " "):
                log.info(
                    "force_openclaw skipped: Whisper FP prefix seed %r",
                    _seed,
                )
                return False
        # Minimum-length gate: anything shorter than 6 chars after
        # stripping is almost certainly a Whisper artefact, not an
        # intentional command.
        if len(lowered) < 6:
            return False
        # Force-spawn viability follows the WORKER, not the talker. The heavy
        # worker is selected from [brain.sub_jarvis].provider and runs regardless
        # of which provider talks to the user (jarvis/missions/init.py
        # _select_subagent_worker_kind). The original BUG-017 (2026-05-13) guard
        # gated on brain.primary, which silenced EVERY action request the moment
        # the user switched the talker to grok / openai / codex / openrouter —
        # re-introducing the "Das kann ich nicht ausführen" refusal through the
        # LLM fallback path (live bug class, forensic 2026-06-07). See
        # _heavy_worker_provider_viable.
        if not self._heavy_worker_provider_viable():
            return False
        verb_re, marker_re, _smalltalk_re = self._get_routing_patterns()
        if _is_instructional_question(t):
            return False
        # AI Pointer: a deictic "what is this?" is a Q&A about the element under
        # the cursor — answered inline from the pushed pointer context, NEVER a
        # heavy-worker spawn. Guard here so a pointing verb like "zeige" cannot
        # fall through to the permissive verb heuristic or generic-subagent
        # detection. See docs/plans/ai-pointer/DESIGN.md.
        try:
            from jarvis.pointer.intent import is_pointing_intent  # noqa: PLC0415

            if is_pointing_intent(t):
                return False
        except Exception:  # noqa: BLE001 — pointer gate must never block routing
            pass
        # UI navigation ("zeig die Socials", "open settings") is a deterministic
        # dumb action handled by the navigation fast-path in generate() — never a
        # heavy worker spawn. Guard here too so a navigation verb cannot fall
        # through to the generic-subagent heuristic. See ADR-0011 "Navigate tool".
        try:
            from jarvis.brain.navigation_intent import (  # noqa: PLC0415
                match_navigation_intent,
            )

            if match_navigation_intent(t) is not None:
                return False
        except Exception:  # noqa: BLE001 — nav gate must never block routing
            pass
        # Greeting-aware smalltalk check (live bug 2026-06-07): a greeting prefix
        # ("Hallo, öffne ...") must NOT block the spawn of the real command that
        # follows it. _is_smalltalk strips the greeting and re-evaluates.
        if self._is_smalltalk(t):
            return False
        # Opening / launching an app is ALWAYS a computer-use task — a sub-agent
        # worker runs in an isolated git worktree and has no desktop. The
        # deterministic match_local_action path routes these to computer-use
        # first; this guard is defense-in-depth so a conjugated open verb
        # ("öffnest") can never fall through to a force-spawn (live bug
        # 2026-06-08: "Ich möchte, dass du mir Hermes Agent öffnest, also …").
        if is_open_app_intent(t):
            return False
        # Skill-aware guard (AD-S3, 2026-06-09 rebuild): an utterance that
        # matches an installed, active skill is the skill's turn — never a
        # heavy-worker spawn. generate() sets _skill_turn_match early; the
        # direct probe is defense-in-depth for callers outside generate().
        if self._skill_turn_match is not None or self._match_skill_for_turn(t) is not None:
            log.info("force-spawn skipped: utterance matches an installed skill")
            return False
        if "dispatch_to_harness" in self._tools and _looks_like_pc_control(t):
            return False
        # User-Mandate 2026-05-14: strict-mode is the default. The router
        # used to spawn on every spawn_verb hit ("schreib", "mach",
        # "zeig", "lies", ...), which fired heavy workers for everyday
        # utterances. In strict mode we only spawn when the user
        # explicitly names a heavy-work trigger ("OpenClaw", "Sub-Agent",
        # "spawn", "deep dive", "gründliche Recherche", ...). The legacy
        # verb/marker heuristic stays available via
        # `brain.routing.force_spawn_mode = "permissive"`.
        mode = (self._config.brain.routing.force_spawn_mode or "strict").lower()
        if mode == "strict":
            if self._get_force_spawn_pattern().search(t):
                return True
            # 2026-06-01: the sub-agent is the universal capability for generic
            # work. The capability gate no longer refuses such tasks, so spawn
            # them natively here — the user must NOT have to say "Subagent". A
            # request the registry recognises as an action that no capability
            # resolves AND that needs no SPECIFIC external integration
            # (mail/calendar/Spotify/social/delivery) is generic sub-agent work.
            # Live forensic 2026-06-01: a sub-agent task was refused, then only
            # spawned once the user said "Subagent" explicitly.
            return self._is_generic_subagent_work(t)
        if verb_re.search(t):
            return True
        if marker_re.search(t):
            return True
        return False

    def _is_generic_subagent_work(self, t: str) -> bool:
        """True iff the utterance is generic, sub-agent-fulfillable work.

        Mirrors the capability gate's class exactly — an action the registry
        recognises that no capability resolves — but FLIPS the verdict from
        "refuse" to "spawn". A specific external integration the worker cannot
        satisfy (mail/calendar/Spotify/social/delivery) is excluded so it keeps
        the honest refusal. Defensive: an unavailable/empty registry returns
        False so the explicit-trigger path stays the sole strict-mode spawn
        signal (mirrors the empty-registry guard in _check_unsupported_intent).
        """
        if requires_external_integration(t):
            return False
        try:
            from jarvis.core.capabilities import get_registry  # type: ignore[import]

            reg = get_registry()
            if not getattr(reg, "all", lambda: ())():
                return False
            return bool(reg.has_action_intent(t) and reg.resolve_intent(t) is None)
        except Exception:  # noqa: BLE001 — registry error must not block spawn decision
            return False

    def _heavy_worker_provider_viable(self) -> bool:
        """True when a heavy-worker backend can run a force-spawn, decoupled from
        the talker provider (``brain.primary``).

        The worker is ``[brain.sub_jarvis].provider`` (jarvis/missions/init.py
        ``_select_subagent_worker_kind``) and is chosen independently of which
        provider talks to the user. A configured worker provider always maps to a
        real worker (claude-api -> ClaudeDirectWorker, codex -> CodexDirectWorker,
        else the OpenClaw/default path), so it is viable for ANY talker — this is
        what lets the user switch ``brain.primary`` to grok / openai / codex
        without silencing every action request (AP-6: never couple routing to a
        hardcoded talker provider).

        Only the LEGACY no-worker-configured path keeps the conservative
        ``brain.primary in {claude-api, gemini}`` check, because there the mission
        factory may fall back to the Gemini API worker, which 403s on an account
        without Gemini access (the original BUG-017, 2026-05-13)."""
        try:
            sub = getattr(self._config.brain, "sub_jarvis", None)
            worker_provider = (getattr(sub, "provider", "") or "").strip().lower()
        except Exception:  # noqa: BLE001 — config hiccup must not block dispatch
            return True
        if worker_provider:
            return True
        try:
            primary = (self._config.brain.primary or "").strip().lower()
        except Exception:  # noqa: BLE001
            primary = ""
        return primary in ("claude-api", "gemini")

    async def _run_local_action_fast_path(
        self,
        user_text: str,
        *,
        trace_id: UUID | None = None,
    ) -> str | None:
        """Execute narrow local actions before vision/provider work.

        The tools used here are intentionally hidden from ``self._tools`` so
        they never appear in the router LLM schema.
        """
        local_cfg = getattr(self._config, "local_action", None)
        if local_cfg is not None and not getattr(local_cfg, "enabled", True):
            return None
        if self._tool_executor is None:
            return None

        plan = match_local_action(user_text)
        if plan is None:
            return None

        tid = trace_id or uuid4()
        if plan.mode == LocalActionMode.UNSUPPORTED:
            # The gate recognised an action request but no registered capability
            # covers it. Speak its deterministic rejection (response_text)
            # instead of dropping it and leaving the user with silence — the
            # gate docstring mandates "route straight to TTS, skipping brain".
            # Without this branch the plan fell through to `return None` and the
            # rejection copy was lost.
            return plan.response_text or None
        if plan.mode == LocalActionMode.DIRECT:
            outputs: list[str] = []
            timeout_s = float(getattr(local_cfg, "direct_timeout_s", 3.0))
            for call in plan.tool_calls:
                tool = self._local_action_tools.get(call.name)
                if tool is None:
                    return None
                try:
                    result = await asyncio.wait_for(
                        self._tool_executor.execute(
                            tool,
                            dict(call.args),
                            user_utterance=user_text,
                            trace_id=tid,
                        ),
                        timeout=timeout_s,
                    )
                except asyncio.TimeoutError:
                    await self._bus.publish(ActionExecuted(
                        trace_id=tid,
                        tool_name=call.name,
                        success=False,
                        duration_ms=int(timeout_s * 1000),
                        error=f"timeout after {timeout_s:.3g}s",
                    ))
                    return f"{call.name} timeout after {timeout_s:.3g}s"
                if not result.success:
                    return result.error or f"{call.name} fehlgeschlagen."
                if result.output is not None:
                    outputs.append(str(result.output))
            return "\n".join(outputs)

        if plan.mode == LocalActionMode.COMPUTER_USE:
            tool = self._local_action_tools.get("dispatch_to_harness")
            if tool is None:
                return None
            # A multi-step CU mission ("navigate to amazon, search, click") needs a
            # generous OUTER cap — the harness has its own per-step timeout +
            # step-budget + no-progress/consecutive-failure guards, so this is only
            # a backstop. The old 30 s ``harness_timeout_s`` aborted legit
            # multi-step missions; the router-tool path already used 120 s. The
            # mission is OFFLOADED (immediate ACK), so a longer cap never blocks
            # the spoken turn. Honour a larger configured value if set.
            _configured_timeout = float(getattr(local_cfg, "harness_timeout_s", 30.0))
            timeout_s = max(_configured_timeout, 180.0)
            if _configured_timeout < 180.0:
                log.debug(
                    "CU offload: harness_timeout_s=%.0fs raised to 180s floor "
                    "(offloaded multi-step mission; harness has its own per-step + "
                    "step-budget + no-progress guards)",
                    _configured_timeout,
                )
            if self._cost_meter is not None:
                if self._cost_meter.is_in_cooldown():
                    return ("Cost-Cooldown aktiv — Tagesbudget erschoepft. "
                            "Neue Anfragen werden erst nach dem Cooldown-Ende bearbeitet.")
                if self._cost_meter.over_task_budget(tid):
                    return "Task-Budget fuer diese Konversation ueberschritten."
                if self._cost_meter.over_daily_budget():
                    return "Tagesbudget ueberschritten."
            # Wave-4 latency fix: Computer-Use is OFFLOADED off the voice turn.
            # Previously the harness was awaited inline for up to ~31 s, so a
            # "do it on screen" command froze the spoken turn the whole time.
            # Now we launch the harness as a BACKGROUND task and return an
            # immediate ACK (AD-OE1); its outcome — success, failure, or timeout
            # — is spoken at the next turn boundary via an
            # AnnouncementRequested(kind="completion") readback (AD-OE5/OE6, zero
            # silent drops). Harness identity comes from the gate; fall back to
            # the canonical in-process harness name (routes to ComputerUseHarness,
            # never a claude-cli worker spawn).
            #
            # Note: the result readback rides the announcement bus, which the
            # voice pipeline speaks. A text-chat-initiated Computer-Use command
            # therefore still executes and is ACK'd, but its result lands as a
            # voice announcement rather than in the chat transcript — an
            # acceptable trade for never freezing the spoken turn.
            # HARNESS_NAME ("screenshot") is the REGISTERED in-process CU harness
            # entry-point; "computer-use" is the router-tool name, NOT a harness,
            # so the old fallback would KeyError in HarnessManager if plan.harness
            # were ever empty. The gate always sets plan.harness=HARNESS_NAME, so
            # this is hygiene — but use the correct constant (review 2026-06-09).
            harness_name = plan.harness or HARNESS_NAME
            bg_tasks = getattr(self, "_cu_background_tasks", None)
            if bg_tasks is None:
                bg_tasks = set()
                self._cu_background_tasks = bg_tasks
            task = asyncio.create_task(
                self._run_computer_use_background(
                    tool=tool,
                    harness_name=harness_name,
                    prompt=plan.prompt or user_text,
                    timeout_s=timeout_s,
                    user_text=user_text,
                    trace_id=tid,
                ),
                name="computer-use-background",
            )
            # Keep a strong reference so the task is not garbage-collected
            # mid-flight, and drop it on completion.
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)
            return (
                "Mach ich — ich erledige das direkt am Bildschirm und sage "
                "Bescheid, sobald es fertig ist."
            )

        return None

    async def _run_computer_use_background(
        self,
        *,
        tool: Any,
        harness_name: str,
        prompt: str,
        timeout_s: float,
        user_text: str,
        trace_id: UUID,
    ) -> None:
        """Run the Computer-Use harness off the voice turn and speak the result.

        Launched fire-and-forget by ``_run_local_action_fast_path`` so the spoken
        turn ACKs immediately (AD-OE1) instead of blocking up to ~31 s on the
        harness. The outcome — success, failure, or timeout — is ALWAYS surfaced
        as an ``AnnouncementRequested(kind="completion")`` readback
        (AD-OE5/OE6: zero silent drops). Never raises — a background-task crash
        must not leak into the event loop.
        """
        text: str
        try:
            result = await asyncio.wait_for(
                self._tool_executor.execute(
                    tool,
                    {
                        "harness": harness_name,
                        "prompt": prompt,
                        "timeout_s": timeout_s,
                    },
                    user_utterance=user_text,
                    trace_id=trace_id,
                ),
                timeout=timeout_s + 1.0,
            )
            if result.success:
                text = str(result.output or "").strip() or "Erledigt."
            else:
                err = getattr(result, "error", None)
                text = (
                    f"Das am Bildschirm hat nicht geklappt: {err}"
                    if err else "Das am Bildschirm hat nicht geklappt."
                )
        except TimeoutError:
            text = (
                f"Das am Bildschirm hat zu lange gedauert "
                f"(ueber {timeout_s:.0f} Sekunden) und wurde abgebrochen."
            )
            try:
                await self._bus.publish(ActionExecuted(
                    trace_id=trace_id,
                    tool_name="dispatch_to_harness",
                    success=False,
                    duration_ms=int((timeout_s + 1.0) * 1000),
                    error=f"timeout after {timeout_s:.3g}s",
                ))
            except Exception:  # noqa: BLE001
                log.debug("CU-background ActionExecuted publish failed", exc_info=True)
        except Exception as exc:  # noqa: BLE001 — a background crash must not leak
            log.error("Computer-Use background task failed: %r", exc, exc_info=True)
            text = "Beim Erledigen am Bildschirm ist leider etwas schiefgegangen."
        # AD-OE6 zero silent drops: ALWAYS speak the outcome at the next turn
        # boundary (announcement -> scrub_for_voice -> TTS).
        try:
            await self._bus.publish(AnnouncementRequested(
                text=text,
                priority="normal",
                language="de",
                kind="completion",
            ))
        except Exception:  # noqa: BLE001
            log.debug("CU-background completion announce failed", exc_info=True)

    async def _record_response_side_effects(
        self,
        *,
        user_text: str,
        response_text: str,
        use_history: bool,
        trace_id: UUID | None = None,
    ) -> None:
        """Apply the normal response side effects for non-provider paths too."""
        if use_history:
            self._history.append(BrainMessage(role="user", content=user_text))
            self._history.append(BrainMessage(role="assistant", content=response_text))
            if len(self._history) > 40:
                self._history = self._history[-40:]

        await self._bus.publish(ResponseGenerated(
            trace_id=trace_id or uuid4(),
            text=response_text,
            language="de" if _looks_german(response_text) else "en",
        ))

        if self._curator is not None:
            try:
                asyncio.create_task(
                    self._curator.process_turn(user_text, response_text),
                    name="curator-process-turn",
                )
            except RuntimeError:
                log.debug("Curator-Task nicht scheduled (kein Event-Loop)")

    def _build_history_hints(self, *, max_turns: int = 3, max_chars_per_msg: int = 240) -> list[str]:
        """Formats the last N turn pairs as compact ``context_hints``.

        Conversation memory bridge to the OpenClaw worker (bug fix 2026-04-30,
        wave-4 rebrand): the worker is architecturally stateless. Without this
        bridge it does not know the previous turns, even when the user
        explicitly refers to them ("erklaer mir das genauer",
        "was war der zweite Punkt?").

        One hint per turn pair in the format:
          ``Frueherer Turn — User: '...' | Du sagtest: '...'``
        Truncated to ``max_chars_per_msg`` to prevent long replies from
        bloating the worker context.
        """
        if not self._history:
            return []
        recent = self._history[-(2 * max_turns):]
        hints: list[str] = []
        for i in range(0, len(recent) - 1, 2):
            u = recent[i]
            a = recent[i + 1]
            if u.role != "user" or a.role != "assistant":
                continue
            u_text = str(u.content)[:max_chars_per_msg]
            a_text = str(a.content)[:max_chars_per_msg]
            hints.append(f"Frueherer Turn — User: {u_text!r} | Du sagtest: {a_text!r}")
        if hints:
            hints.insert(0, "Konversations-Kontext (letzte Turns, juengster zuletzt):")
        return hints

    async def _force_spawn_worker(
        self,
        user_text: str,
        *,
        trace_id: UUID | None = None,
    ) -> str | None:
        """Starts ``spawn_worker`` deterministically, without LLM tool-choice.

        Wave-4 migration: previously ``_force_spawn_sub_jarvis`` with the
        ``spawn_sub_jarvis`` tool. The Sub-Jarvis tier was replaced by the
        OpenClaw bridge — see docs/openclaw-bridge.md §11.

        Returns:
            ``None`` when the heuristic does not trigger or the tool is absent.
            Otherwise the OpenClaw output (the mission manager delivers a
            TTS-safe shortened summary via the voice listener path). The caller
            (``generate``) forwards the string as the final brain response.
        """
        if not self._should_force_spawn(user_text):
            return None

        tool = self._tools.get("spawn_worker")
        if tool is None or self._tool_executor is None:
            return None

        tid = trace_id or uuid4()
        context_hints: list[str] = [
            "Deterministically delegated (persona mandate phase 3): "
            "verb/marker heuristic triggered, smalltalk allowlist did not.",
        ]
        # Bug fix 2026-04-30: pass conversation history to the worker so
        # follow-up questions ("erklaer das genauer") do not spawn into a
        # void. The stateless worker stays architecturally compliant — it
        # receives a snapshot of the last turns as a hint, not the full
        # manager state.
        context_hints.extend(self._build_history_hints())
        # Phase 5 (opt-in): include active-window hint so the OpenClaw worker
        # knows which app the user is currently working in. Default OFF
        # (costs 200-400 ms latency, not worth it for every spawn). 250 ms
        # timeout in the module; failure mode 4 (pywinauto crash) is caught.
        try:
            from jarvis.brain.vision_context import get_active_window_hint

            vision_cfg = getattr(self._config, "vision", None)
            hint = await get_active_window_hint(config=vision_cfg)
        except Exception as exc:  # noqa: BLE001
            log.debug("Vision-Context-Probe failed (non-fatal): %s", exc)
            hint = None
        if hint:
            context_hints.append(hint)

        args = {
            "utterance": user_text,
            "context_hints": context_hints,
            # Empty action signals the spawn tool's ACK builder to pick from
            # its short variant rotation instead of emitting the long generic
            # template phrase. Live regression 2026-05-26: the previously
            # hardcoded "den vom User beschriebenen Workflow" turned every
            # force-spawn ACK into the same canned 17-syllable sentence.
            "action": "",
            "target": "",
        }
        log.info("Force-Spawn OpenClaw: %r", user_text[:160])
        result = await self._tool_executor.execute(
            tool,
            args,
            user_utterance=user_text,
            trace_id=tid,
        )
        if not result.success:
            return result.error or "OpenClaw konnte nicht gestartet werden."
        return str(result.output or "")

    async def _recover_leaked_spawn(
        self,
        response_text: str,
        *,
        user_text: str,
        trace_id: UUID,
    ) -> str | None:
        """Execute a ``spawn_worker`` call a provider leaked as TEXT.

        Root cause (live repro 2026-05-24, mission "erstelle mir eine Datei
        test-opus.md"): Gemini intermittently emits the ``spawn_worker``
        tool_use block as the response content instead of invoking it. The raw
        JSON then reaches TTS as "Es trat ein Fehler auf" and the delegated
        Opus-4.7 sub-agent never runs — even though the brain *decided* to
        delegate. This is a provider function-calling leak, independent of the
        force-spawn heuristic (which stays strict).

        If ``response_text`` carries such a leaked call, run it through the same
        tool path ``_force_spawn_worker`` uses and return the spawn ACK;
        otherwise return ``None`` (caller keeps the original text).
        """
        leaked = _extract_leaked_spawn_call(response_text)
        if leaked is None:
            return None
        tool = self._tools.get("spawn_worker")
        if tool is None or self._tool_executor is None:
            return None

        utterance = str(leaked.get("utterance") or user_text).strip() or user_text
        context_hints = [
            "Recovered from a leaked tool_use block: the brain emitted the "
            "spawn_worker call as text instead of executing it (provider "
            "function-calling leak). Re-dispatched deterministically so the "
            "sub-agent runs and the user is not left with a spoken error.",
            *self._build_history_hints(),
        ]
        args = {
            "utterance": utterance,
            "context_hints": context_hints,
            # Prefer the brain-leaked action verb; empty fallback rotates
            # short generic ACK variants instead of the old long template.
            "action": str(leaked.get("action") or ""),
            "target": str(leaked.get("target") or ""),
        }
        log.warning(
            "Recovered leaked spawn_worker tool-call from brain text "
            "(provider function-calling leak): %r", user_text[:160],
        )
        result = await self._tool_executor.execute(
            tool, args, user_utterance=user_text, trace_id=trace_id,
        )
        if not result.success:
            return result.error or (
                "Der Hintergrund-Worker konnte nicht gestartet werden."
            )
        return str(result.output or "")

    async def _recover_leaked_tool(
        self,
        response_text: str,
        *,
        user_text: str,
        trace_id: UUID,
    ) -> str | None:
        """Execute ANY tool a provider leaked as TEXT (generalises spawn-only).

        Root cause (live repro 2026-05-25, voice "oeffne den Editor"): Gemini
        emits the ``tool_use`` block (``open_app`` / ``dispatch_to_harness`` /
        ``cli_*`` …) as response *text* instead of invoking it. The
        spawn-only :meth:`_recover_leaked_spawn` ignored every non-spawn tool,
        so the raw JSON reached TTS (scrubbed to silence) and the action never
        ran — while plain chit-chat (no tool) worked fine.

        ``spawn_worker`` keeps its specialised path (history hints, ACK).
        Every other leaked tool runs through the same ``ToolExecutor`` a
        structured tool_use would take. Returns a speakable result string, or
        ``None`` if there is no leak / the tool is not runnable.
        """
        parsed = _extract_leaked_tool_call(response_text)
        if parsed is None:
            return None
        name, inp = parsed
        if name == "spawn_worker":
            return await self._recover_leaked_spawn(
                response_text, user_text=user_text, trace_id=trace_id,
            )
        if self._tool_executor is None:
            return None
        tool = self._tools.get(name)
        if tool is None:
            return None
        log.warning(
            "Recovered leaked %s tool-call from brain text "
            "(provider function-calling leak): %r", name, user_text[:160],
        )
        result = await self._tool_executor.execute(
            tool, inp, user_utterance=user_text, trace_id=trace_id,
        )
        if not result.success:
            return result.error or (
                f"Die Aktion '{name}' konnte nicht ausgefuehrt werden."
            )
        return str(result.output or "")

    def _cancel_all_background_tasks(self) -> int:
        """Cancels all running background OpenClaw tasks.

        Matches via `task.get_name()` against the "openclaw-" prefix. The
        convention is set by the `spawn_worker` tool in `create_task(...)`.
        Returns the number of cancelled tasks.
        """
        cancelled = 0
        try:
            running = asyncio.all_tasks()
        except RuntimeError:
            # No running event loop (sync context) — nothing to cancel.
            return 0
        for task in running:
            name = task.get_name() or ""
            if name.startswith("openclaw-") and not task.done():
                task.cancel()
                cancelled += 1
        log.info("Cancelled %d background openclaw task(s)", cancelled)
        return cancelled

    # ------------------------------------------------------------------
    # Intent-Router → (provider, model) chain
    # ------------------------------------------------------------------

    def _picked_level(self, user_text: str) -> RoutingDecision:
        if self._force_level == "deep":
            return RoutingDecision(level="deep", reason="sticky-deep")
        if self._force_level == "fast":
            return RoutingDecision(level="fast", reason="sticky-fast")
        return classify(user_text)

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        """Returns a prioritised list of (provider, model) attempts."""
        active = self._active_name
        chain: list[tuple[str, str | None]] = []

        # 0. Deep/code intents: dedicated deep_brain first (e.g. gemini via
        #    subscription — bypasses /v1/messages API quota). Bug fix 2026-04-29:
        #    at level=deep the deep_model of the brain MUST be used (previously:
        #    _fast_model → gemini-3-flash for a deep request instead of
        #    gemini-3.1-pro-preview).
        deep_brain = self._config.brain.deep_brain
        # When the user has explicitly made codex the active brain, codex leads
        # for ALL turns — the deep_brain (e.g. gemini) must NOT jump ahead for
        # deep/code intents, or codex would never actually answer a hard question
        # despite being the chosen brain (it would silently fall through to the
        # deep_brain). codex (gpt-5.5) is itself a frontier model, so there is no
        # capability reason to delegate. Other active brains keep the deep_brain
        # routing unchanged.
        if (
            level in ("deep", "code")
            and deep_brain
            and deep_brain != active
            and active != "codex"
            and deep_brain in self._registry.available()
        ):
            preferred_deep = self._deep_model(deep_brain) or self._fast_model(deep_brain)
            chain.append((deep_brain, preferred_deep))

        # 1. Active provider with the appropriate model for the level
        fast = self._fast_model(active)
        deep = self._deep_model(active)

        if level == "fast":
            if fast:
                chain.append((active, fast))
            if deep:  # on Haiku rate-limit → try Opus in the same provider
                chain.append((active, deep))
        elif level in ("deep", "code"):
            if deep:
                chain.append((active, deep))
            if fast:  # if Opus fails, use Haiku (better than nothing)
                chain.append((active, fast))
        else:
            if fast:
                chain.append((active, fast))

        # 2. Explicit tier fallbacks from jarvis.toml. These must run before
        # generic cross-provider probing so runtime matches healthcheck order.
        available = set(self._registry.available())
        for name, configured_model in self._configured_fallbacks:
            if name == active:
                continue
            if name not in available:
                continue
            m_fast = self._fast_model(name)
            m_deep = self._deep_model(name)
            preferred = configured_model or (m_deep if level in ("deep", "code") else m_fast)
            chain.append((name, preferred or m_fast or m_deep))

        # 3. Cross-provider fallbacks (same provider family first).
        # Ollama completely removed (2026-04-21) — user decision, pure
        # cloud/API provider chain.
        cross_order = [
            "claude-api",           # separate Anthropic-Quota
            "gemini",               # Google AI Studio
            "openrouter",           # universal gateway
            "openai",
            "grok",
        ]
        for name in cross_order:
            if name == active:
                continue
            if name not in available:
                continue
            m_fast = self._fast_model(name)
            m_deep = self._deep_model(name)
            preferred = m_deep if level in ("deep", "code") else m_fast
            chain.append((name, preferred or m_fast or m_deep))

        # Deduplicate (first instance wins) + filter dead providers.
        # Dead = provider already failed with "no API key" in this session.
        # Skip it so the voice turn does not run 8x sequentially against
        # missing keys. Reset on provider switch or manager restart.
        seen: set[tuple[str, str | None]] = set()
        deduped: list[tuple[str, str | None]] = []
        for item in chain:
            if item in seen:
                continue
            if item[0] in self._dead_providers:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    # ------------------------------------------------------------------
    # Generate — Haupt-Entrypoint
    # ------------------------------------------------------------------

    async def generate(
        self,
        user_text: str,
        *,
        use_history: bool = True,
        trace_id: UUID | None = None,
        text_consumer: "Callable[[str], None] | None" = None,
        on_progress: Callable[[], None] | None = None,
    ) -> str:
        # 1. Intercept meta-commands (cancel, switch, depth override).
        # User request 2026-04-25: no standardised confirmation phrases
        # ("OK, ich wechsle auf X", "Abgebrochen ..."). State changes remain
        # functional; feedback runs visually via bus events
        # (BrainProviderSwitched) or UI indicators. The pipeline stays silent
        # on empty responses (see pipeline.py:937).
        # AD-OE6 zero-silent-drop signal: reset per turn. Only the
        # failure-diagnostic returns below flip it True; the meta-command
        # early-returns ("") that follow correctly leave it False.
        self._last_turn_all_failed = False
        self._last_turn_suppressed = False
        self._last_turn_executed_action_tool = False
        turn_trace_id = trace_id or uuid4()

        if self._detect_cancel_intent(user_text):
            self._cancel_all_background_tasks()
            return ""

        switch_target = self._detect_switch_intent(user_text)
        if switch_target:
            await self.switch(switch_target)
            return ""

        depth_override = self._detect_depth_override(user_text)
        if depth_override == "deep":
            self._force_level = "deep"
            return ""
        if depth_override == "fast":
            self._force_level = "fast"
            return ""

        # AD-12 + AP-OC5 (OpenClaw bridge wave-4 router): intercept status/cancel
        # phrases via pattern match BEFORE the force-spawn heuristic
        # misinterprets them as action verbs ("brich ab" contains the verb 'ab'
        # and would otherwise trigger a new spawn). Pattern-match-first is
        # mandatory — no LLM hallucination risk on "laeuft das noch?".
        oc_match = match_mission_command(user_text)
        if oc_match is not None:
            log.info(
                "OpenClaw-Command erkannt: intent=%s id=%s lang=%s text=%r",
                oc_match.intent,
                oc_match.mission_id,
                oc_match.language,
                user_text[:120],
            )
            if (
                oc_match.intent == "status"
                and self._openclaw_status_fn is not None
            ):
                response = await self._openclaw_status_fn(oc_match.mission_id)
                await self._record_response_side_effects(
                    user_text=user_text,
                    response_text=response,
                    use_history=use_history,
                    trace_id=turn_trace_id,
                )
                return response
            if (
                oc_match.intent == "cancel"
                and self._openclaw_cancel_fn is not None
            ):
                response = await self._openclaw_cancel_fn(oc_match.mission_id)
                await self._record_response_side_effects(
                    user_text=user_text,
                    response_text=response,
                    use_history=use_history,
                    trace_id=turn_trace_id,
                )
                return response
            # Pattern matched, but no handler registered — fall through to
            # the normal path. Logging aids debugging ("why does the status
            # read still spawn?": handlers not wired).
            log.warning(
                "OpenClaw-Command-Match ohne Handler — fallback to normal "
                "generate-pfad. Bootstrap muss "
                "set_mission_command_handlers() rufen."
            )

        # Skill-aware routing guard (AD-S3): probe ONCE per turn, before any
        # fast path can grab the utterance. "starte die Morgenroutine" is an
        # is_open_app_intent hit AND a spawn-verb hit — without this early
        # probe the skill never gets a chance (the root cause of "Jarvis
        # never calls a skill"). Overwritten on every turn.
        self._skill_turn_match = self._match_skill_for_turn(user_text)
        # AD-S4: a trigger noted by the speech pipeline / chat hook takes
        # precedence — it carries the captured content and the source label.
        self._consume_pending_skill_trigger(user_text)
        if self._skill_turn_match is not None:
            log.info(
                "Skill-matched turn: %r → skill %s (fast paths stand down)",
                user_text[:80],
                getattr(self._skill_turn_match, "name", "?"),
            )
            # AD-S5: mission skills never run inline — dispatch the worker
            # with the rendered instructions as the brief and return the
            # optimistic ACK. Falls through to the inline path when the
            # dispatch is not possible (AD-OE6: no silent drop).
            mission_reply = await self._maybe_dispatch_skill_mission(
                user_text, trace_id=turn_trace_id,
            )
            if mission_reply is not None:
                await self._record_response_side_effects(
                    user_text=user_text,
                    response_text=mission_reply,
                    use_history=use_history,
                    trace_id=turn_trace_id,
                )
                return mission_reply

        if self._skill_turn_match is None:
            local_action = await self._run_local_action_fast_path(
                user_text, trace_id=turn_trace_id,
            )
            if local_action is not None:
                await self._record_response_side_effects(
                    user_text=user_text,
                    response_text=local_action,
                    use_history=use_history,
                    trace_id=turn_trace_id,
                )
                return local_action

        # Navigation fast-path: a clear "go to section X" command moves the UI
        # deterministically (a dumb action, AD-OE3). Placed BEFORE the capability
        # gate — which would refuse "zeig die Socials" because 'social' is an
        # external-integration marker — and before force-spawn. Pure regex, no
        # LLM (AP-11). See ADR-0011 amendment "Navigate tool".
        nav_reply = await self._run_navigation_fast_path(
            user_text, trace_id=turn_trace_id,
        )
        if nav_reply is not None:
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=nav_reply,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return nav_reply

        # Agent-C (capability-coupling): pre-generation capability gate.
        # If the utterance looks like an action request but no registered
        # capability covers it, return a deterministic "not supported" reply
        # and skip both brain and openclaw.  No LLM call, no latency cost
        # (AP-11 compliant — pure regex + registry lookup).
        # AD-S3: a matched skill IS the capability — the unsupported-intent
        # refusal must not fire on a skill turn.
        unsupported = (
            None
            if self._skill_turn_match is not None
            else self._check_unsupported_intent(user_text)
        )
        if unsupported is not None:
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=unsupported,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return unsupported

        # Persona mandate phase 3: deterministic force-spawn heuristic before
        # the LLM tool-use loop. Prevents spawn reflex on ambiguous smalltalk
        # inputs (see docs/persona-research.md section 2 — 60% empty smalltalk
        # outputs from the reflexive LLM spawn path).
        forced_spawn = await self._force_spawn_worker(
            user_text, trace_id=turn_trace_id,
        )
        if forced_spawn is not None:
            # Bug fix 2026-04-30: history update also in the force-spawn path.
            # Previously returned directly → main Jarvis had no memory on the
            # NEXT turn that this question was ever asked.
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=forced_spawn,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return forced_spawn

        # Phase 5 / ADR-0006: pre-call budget gate. Block rather than request
        # when cooldown is active or the task/daily budget is exhausted.
        trace_uuid = turn_trace_id
        if self._cost_meter is not None:
            if self._cost_meter.is_in_cooldown():
                return ("Cost-Cooldown aktiv — Tagesbudget erschoepft. "
                        "Neue Anfragen werden erst nach dem Cooldown-Ende bearbeitet.")
            if self._cost_meter.over_task_budget(trace_uuid):
                return "Task-Budget fuer diese Konversation ueberschritten."
            if self._cost_meter.over_daily_budget():
                return "Tagesbudget ueberschritten."

        # Smalltalk near-toolless path (bug fix 2026-05-01): on clearly
        # identified smalltalk the spawn/action tools are hidden so the LLM
        # cannot be tempted to hallucinate "spawn_worker" (see voice session
        # 2026-04-30 22:38, "es geht ab" → fake spawn). The read-only screenshot
        # tool stays visible (see _smalltalk_tool_override) so the brain can
        # still look at the screen on demand even on a greeting-prefixed turn
        # like "Hallo, lies mir vor was oben links steht" (live failure
        # 2026-05-31). Force-spawn already ran (smalltalk wins there against verb
        # match); now we also constrain the LLM tool-choice path.
        is_smalltalk_turn = self._is_smalltalk(user_text)
        if is_smalltalk_turn:
            log.info(
                "Smalltalk-Turn → nur read-only Tools fuer LLM sichtbar: %r",
                user_text[:80],
            )

        # 2. Router: which level applies?
        decision = self._picked_level(user_text)
        log.debug("Router-Decision: level=%s reason=%s", decision.level, decision.reason)

        # 3. Build fallback chain and try each entry
        chain = self._build_fallback_chain(decision.level)
        if not chain:
            # Empty chain means either (a) no providers registered or
            # (b) all filtered out by _dead_providers (no key set).
            # In production (b) is the common case — provide an actionable message.
            self._last_turn_all_failed = True
            # Keep the actionable provider/key diagnostic in the LOG (UI/console
            # surface it), but SPEAK only a localized, provider-agnostic apology
            # — never read setup hints or provider names aloud (AP-11/ADR-0010).
            if self._dead_providers:
                log.warning(
                    "Provider chain empty (all dead/keyless) — spoken fallback. "
                    "Diagnostic: %s",
                    _format_provider_chain_error([
                        (p, "", "missing_key", "kein API-Key in dieser Session")
                        for p in self._dead_providers
                    ]),
                )
            else:
                log.warning("No brain providers available — spoken fallback used.")
            return self._next_provider_down_phrase()

        history = self._history if use_history else []
        last_exc: Exception | None = None
        response_text = ""
        used_provider: str | None = None
        used_model: str | None = None
        # AI Pointer (deictic push): launch the cursor-element resolution BEFORE
        # the vision-image await so it overlaps with it instead of running serially
        # after (AP-9: keep the deictic turn off the serial hot path). The task does
        # the regex gate itself, so non-deictic turns complete instantly with
        # ("", None) and fast-skip on a headless host. Awaited just below.
        pointer_task = self._start_pointer_task(user_text, is_smalltalk_turn)
        images: tuple[ImageBlock, ...] = await self._collect_vision_images(
            trace_id=trace_uuid,
            user_text=user_text,
            is_smalltalk=is_smalltalk_turn,
        )
        # Per-provider error aggregation for a meaningful user message when
        # the whole chain fails. Pattern: (provider, model, kind, detail).
        # kind ∈ {"rate_limit", "missing_key", "skipped_cooldown", "init_fail",
        #         "call_fail"}
        provider_errors: list[tuple[str, str, str, str]] = []

        # B5 Agent C: wiki context injection — run once before the provider
        # loop so all providers in the fallback chain see the same enriched
        # system prompt.  The injector is a no-op when _wiki_injector is None
        # (Agent B not merged, or [wiki_context].enabled = false).
        # _wiki_context_suffix is reset in the finally block at the end of
        # generate() to prevent stale context leaking into the next turn.
        try:
            if self._wiki_injector is not None:
                base_prompt = self._build_system_prompt()
                injected_prompt = await self._wiki_injector.maybe_inject(
                    user_text=user_text,
                    system_prompt=base_prompt,
                )
                # Store the delta (only the appended wiki block, not the whole
                # prompt) so _build_system_prompt() can append it once without
                # duplicating the rest of the prompt.
                if injected_prompt != base_prompt:
                    # Extract only the appended wiki section
                    self._wiki_context_suffix = injected_prompt[len(base_prompt):]
                else:
                    self._wiki_context_suffix = ""
        except Exception:  # noqa: BLE001
            # Any unexpected error in the injector must never crash a voice turn.
            log.warning("WikiContextInjector raised unexpectedly — skipping", exc_info=True)
            self._wiki_context_suffix = ""

        # Wave 2 (omni-latency): assemble the per-turn dynamic context (date +
        # awareness + wiki) once. In cache-optimized mode it rides on the user
        # message (keeping the cached system prompt stable); empty in legacy
        # mode. Reused for every provider in the fallback chain below.
        turn_context = self._build_turn_context()

        # AD-S3/S4: on a skill-matched turn the rendered instructions ride on
        # the per-turn context (guaranteed invocation, no run-skill round
        # trip needed) — deterministic code, not a prompt-only hope. The
        # cached system prefix stays byte-stable.
        _skill_block = self._render_skill_turn_injection(user_text)
        if _skill_block:
            turn_context = (
                f"{turn_context}\n\n{_skill_block}" if turn_context else _skill_block
            )

        # AI Pointer (deictic push): collect the result of the resolution started
        # above. When the utterance points at the mouse cursor ("was ist das da?")
        # the resolved element rides on this turn's context + a tight crop is
        # attached only when the element is unlabeled. Unrelated turns ("how's the
        # weather?") yield ("", None). See docs/plans/ai-pointer/DESIGN.md.
        pointer_block = ""
        pointer_image: ImageBlock | None = None
        if pointer_task is not None:
            try:
                pointer_block, pointer_image = await pointer_task
            except Exception:  # noqa: BLE001 — never crash a turn on pointer context
                log.debug("AI Pointer per-turn injection skipped", exc_info=True)
                pointer_block, pointer_image = "", None

        # AI Pointer grounding (2026-06-02): a deictic pointer turn ("worauf zeige
        # ich?") must be scoped to the CURSOR region so the brain answers from the
        # cursor element/crop — it must NOT guess the pointing target from the
        # full-screen permanent-vision image (the live "described something
        # completely elsewhere" bug). On such a turn we (1) replace the full-screen
        # image with the tight cursor crop (or none, for a labelled element),
        # (2) drop the full-screen screenshot + inspect-pointer tools (below), and
        # (3) inject a "do not guess" instruction when resolution failed.
        pointing_turn = (not is_smalltalk_turn) and self._is_pointer_intent(user_text)
        if pointing_turn:
            images = (pointer_image,) if pointer_image is not None else ()
            if not pointer_block:
                pointer_block = (
                    "[AI Pointer] The user asked what they are pointing at, but the "
                    "element under the cursor could not be read right now. Tell them "
                    "you cannot tell what is under the cursor at the moment — do NOT "
                    "guess from the rest of the screen."
                )
            turn_context = (
                f"{turn_context}\n\n{pointer_block}" if turn_context else pointer_block
            )

        for idx, (prov_name, model) in enumerate(chain):
            # Skip providers already marked dead in THIS turn.
            # Example: gemini-fast fails with missing_key → gemini-deep would
            # still be in the chain but would fail for the same reason. Skip
            # saves an avoidable subprocess/network call.
            if prov_name in self._dead_providers:
                continue
            # Circuit breaker: skip rate-limited providers during cooldown
            if not self._rate_tracker.is_available(prov_name, model):
                log.debug("Skip rate-limited: %s(%s)", prov_name, model)
                provider_errors.append(
                    (prov_name, model, "skipped_cooldown",
                     "still in 30s rate-limit cooldown"))
                continue

            try:
                brain = self._get_brain(prov_name, model)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                kind = _classify_provider_error(msg, default="init_fail")
                # On missing_key: remove provider from the chain for the rest
                # of the session. Prevents each voice turn from running 8x
                # sequentially against the same missing keys.
                if kind in ("missing_key", "account_blocked") and prov_name not in self._dead_providers:
                    self._dead_providers.add(prov_name)
                    if kind == "missing_key":
                        log.warning(
                            "Provider %s ohne API-Key — fuer diese Session deaktiviert. "
                            "Setup: Sidebar -> API-Keys.", prov_name)
                    else:
                        log.warning(
                            "Provider %s account-blocked (Credit/Quota/Tier) — "
                            "fuer diese Session deaktiviert. Detail: %s",
                            prov_name, msg[:160])
                else:
                    log.debug(
                        "Brain %s(%s) konnte nicht instantiiert werden: %s",
                        prov_name, model, exc)
                provider_errors.append((prov_name, model, kind, msg[:200]))
                continue

            _turn_tools = (
                self._smalltalk_tool_override() if is_smalltalk_turn
                # Non-smalltalk turn: pass the full set minus plugin tools
                # that are irrelevant to this utterance (progressive
                # disclosure — keeps the surface small once 3+ plugins are
                # connected). None would mean "use self._tools verbatim".
                else self._apply_plugin_relevance(user_text, self._tools)
            )
            # AI Pointer: on a deictic pointer turn the cursor crop is already the
            # only attached image, so drop the redundant ``inspect-pointer`` PULL
            # tool (calling it produced an empty spoken answer — observed live).
            # The full-screen ``screenshot`` tool is deliberately KEPT: removing it
            # made the router refuse "Was siehst du hier?" with "I lack a tool"
            # (the capability gate maps "see" to a vision tool). With the tool
            # present there is no refusal, and the injected crop + prompt steer the
            # brain to answer from the crop, not the whole screen. See
            # docs/plans/ai-pointer/DESIGN.md.
            if pointing_turn and isinstance(_turn_tools, dict):
                _turn_tools = {
                    k: v for k, v in _turn_tools.items() if k != "inspect-pointer"
                }
            disp = self._build_dispatcher(brain, tools_override=_turn_tools)
            try:
                # CostMeter: start per-trace tracking (idempotent if already started).
                if self._cost_meter is not None:
                    self._cost_meter.start(trace_uuid, prov_name, model)
                agg = await disp.dispatch(
                    user_text,
                    images=images,
                    history=history,
                    trace_id=trace_id,
                    intent_level=decision.level,
                    text_consumer=text_consumer,
                    on_progress=on_progress,
                    turn_context=turn_context,
                )
                # Post-call cost hook: aggregated usage → meter.
                # The meter cancels on overrun via CancelToken (see ADR-0006);
                # the pre-call gate above catches that on the next turn.
                if self._cost_meter is not None and agg.usage:
                    usd = _estimate_usd_from_usage(self._cost_meter, model, agg.usage)
                    self._cost_meter.add(CostRecord(
                        trace_id=trace_uuid, provider=prov_name, model=model,
                        tokens_in=int(agg.usage.get("input_tokens", 0)),
                        tokens_out=int(agg.usage.get("output_tokens", 0)),
                        tokens_cache_hit=int(agg.usage.get("cache_hit_tokens", 0)),
                        usd=usd, timestamp_ns=time.time_ns(),
                    ))
                # Empty-Response-Guard: wenn der Provider zwar erfolgreich
                # antwortet aber **leeren** Content liefert (Safety-Block,
                # truncated-Response, Schema-Mismatch), behandeln wir das wie
                # einen Soft-Fail und gehen zum naechsten Provider in der
                # Chain. Frueher: response_text = "" + break → die globale
                # `if not response_text`-Logik unten verschickte dann irrefuehrend
                # "Provider X, Y unerreichbar" statt einen anderen Provider zu
                # probieren. Empty != fail-permanently, aber empty != success.
                #
                # 2026-04-29 Fix: Tool-Calls + suppress_response sind LEGITIME
                # leere Texte. Beispiel: spawn_worker ist fire-and-forget
                # mit suppress_response=True; der Tool-Use-Loop setzt dann
                # final_agg.text="" und finish_reason="suppress_response". Vorher
                # hat das den Empty-Response-Guard getriggert, der dann zum
                # naechsten Provider gefallen ist — der hat denselben Spawn
                # nochmal probiert. Die Folge: 3 Provider gecallt, 2 Spawns
                # abgelehnt, drittes fiel auf multi_spawn zurueck und
                # scheiterte ebenfalls.
                response_empty = not (agg.text or "").strip()
                tool_calls_executed = bool(agg.tool_calls)
                suppressed = (agg.finish_reason == "suppress_response")
                if response_empty and not tool_calls_executed and not suppressed:
                    log.warning(
                        "Brain %s(%s) lieferte leeren Content — "
                        "vermutlich Safety-Block oder Empty-Response. "
                        "Versuche naechsten Provider in der Chain.",
                        prov_name, model,
                    )
                    provider_errors.append((
                        prov_name, model, "empty_response",
                        "Provider gab leere Antwort zurueck (Safety/Schema?)",
                    ))
                    continue

                response_text = agg.text
                # Record whether THIS (winning) turn was a fire-and-forget
                # ``suppress_response`` spawn, so the voice pipeline can stay
                # silent for it but speak a clarifying question for a different
                # empty turn (function_call/CU without speech). See
                # ``SpeechPipeline._handle_silent_brain_turn``.
                self._last_turn_suppressed = suppressed
                # AD-OE6 companion signal #2: did THIS winning turn SUCCESSFULLY
                # execute a desktop-action tool (computer_use / open_app / …)?
                # If so and it produced no narration, the voice pipeline speaks
                # a success confirmation instead of a clarifying question
                # (live bug 2026-06-09). Read ``executed_tool_names`` — the tools
                # that REALLY ran — not ``tool_calls`` (which also holds calls a
                # guard blocked, e.g. computer_use refused on a how-to question);
                # speaking "Erledigt." for a blocked action would be a lie.
                executed = getattr(agg, "executed_tool_names", None) or set()
                self._last_turn_executed_action_tool = bool(
                    set(executed) & _DESKTOP_ACTION_TOOL_NAMES
                )
                used_provider, used_model = prov_name, model

                # Bug C Fix (2026-04-29) — BrainTurnStarted/Completed publishen
                # NUR wenn der Brain-Call erfolgreich war (Stream lieferte
                # Tokens oder Tool-Calls). Vorher: Event wurde publisht bevor
                # _ensure_client crashte → Halluzinations-Tag in voice_turns
                # ("openai/gpt-4o" ohne Key). Jetzt: wir wissen dass dieser
                # Call wirklich Daten lieferte (`continue`-Pfade kommen hier
                # nicht an), also schreiben wir nur den ECHTEN Provider in
                # die Voice-Session-DB.
                tokens_in_total = int(agg.usage.get("input_tokens", 0)) if agg.usage else 0
                tokens_out_total = int(agg.usage.get("output_tokens", 0)) if agg.usage else 0
                cost_usd_total = 0.0
                try:
                    from jarvis.brain.cost import calculate_cost_usd
                    cost_usd_total = calculate_cost_usd(model, tokens_in_total, tokens_out_total)
                except Exception:  # noqa: BLE001
                    pass
                await self._bus.publish(BrainTurnStarted(
                    provider=prov_name,
                    model=model,
                    intent_level=decision.level,
                ))
                await self._bus.publish(BrainTurnCompleted(
                    provider=prov_name,
                    model=model,
                    tokens_in=tokens_in_total,
                    tokens_out=tokens_out_total,
                    cost_usd=cost_usd_total,
                    text_len=len(response_text or ""),
                    finish_reason=str(getattr(agg, "finish_reason", "ok") or "ok"),
                ))

                if idx > 0:
                    log.info(
                        "Fallback-Hit: %s(%s) — %d provider übersprungen",
                        prov_name, model, idx,
                    )
                    await self._bus.publish(BrainProviderSwitched(
                        from_provider=self._active_name,
                        to_provider=prov_name,
                    ))
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                # 429 Rate-Limit: markieren für 30s
                if _is_rate_limit_exc(exc):
                    self._rate_tracker.mark_rate_limited(prov_name, model)
                    log.warning("Rate-Limited %s(%s) — 30s Cooldown aktiviert", prov_name, model)
                    provider_errors.append(
                        (prov_name, model, "rate_limit", "HTTP 429"))
                else:
                    log.warning("Brain %s(%s) fehlgeschlagen: %s", prov_name, model, exc)
                    kind = _classify_provider_error(msg, default="call_fail")
                    if kind == "missing_key" and prov_name not in self._dead_providers:
                        self._dead_providers.add(prov_name)
                        log.warning(
                            "Provider %s ohne API-Key — fuer diese Session deaktiviert. "
                            "Setup: Sidebar -> API-Keys.", prov_name)
                    provider_errors.append((prov_name, model, kind, msg[:200]))
                # NOTE BUG-019 (2026-05-11): this generic ``continue`` does
                # not touch the failing provider's *internal* state. For
                # most providers that's correct (an HTTP error is purely
                # transient). For Gemini specifically, however, a 403
                # "CachedContent not found" means the locally-cached
                # ``self._cached_content_name`` is stale — and because we
                # don't clear it here, every subsequent voice turn re-uses
                # the same dead cache id and re-fails, sending the whole
                # fallback chain into the 40-second pipeline timeout and
                # leaving the user with silent THINKING → LISTENING. The
                # root-cause annotation lives at the actual failure site
                # in ``jarvis/plugins/brain/gemini.py`` (search for
                # "BUG-019 ROOT CAUSE"). The right place to fix this is
                # *inside* the provider (catch the cache-not-found error,
                # call its own ``invalidate_cache()``, retry without the
                # cached_content field) — not by leaking Gemini-specific
                # error matching into this cross-provider chain.
                continue

        # When `used_provider` is set, AT LEAST ONE provider completed the turn
        # successfully — even if `response_text` is empty (e.g. suppress_response
        # for fire-and-forget tools like spawn_worker). In that case do NOT
        # return the "all failed" message — the UI receives feedback via bus events
        # (OpenClawAnnouncement, etc.).
        # B5 Agent C: reset per-turn wiki suffix regardless of outcome so
        # stale context cannot leak into the next voice turn.
        self._wiki_context_suffix = ""

        if used_provider is None:
            self._last_turn_all_failed = True
            log.error("Alle %d Provider-Versuche fehlgeschlagen. Letzter Fehler: %s",
                     len(chain), last_exc)
            # Developer diagnostic → LOG only. The voice path gets a localized,
            # provider-agnostic apology (live complaint 2026-06-01: the grok/
            # Anthropic billing diagnostic was spoken while Gemini was active).
            log.warning(
                "Spoken fallback used instead of chain diagnostic: %s",
                _format_provider_chain_error(provider_errors),
            )
            return self._next_provider_down_phrase()

        # Robustness net (2026-05-24): a provider (notably Gemini) sometimes
        # emits a spawn_worker tool_use block as TEXT instead of executing
        # it — response_text becomes raw `[{"type":"tool_use",...}]` JSON.
        # Without this the JSON is spoken (scrubbed to "Es trat ein Fehler
        # auf") and the delegated Opus-4.7 sub-agent never runs. Detect the
        # leak and execute the spawn through the normal tool path so the
        # heavy-work delegation is robust against provider function-calling
        # flakiness.
        recovered = await self._recover_leaked_tool(
            response_text, user_text=user_text, trace_id=trace_uuid,
        )
        if recovered is not None:
            response_text = recovered

        # 4. History + Events
        if use_history:
            self._history.append(BrainMessage(role="user", content=user_text))
            self._history.append(BrainMessage(role="assistant", content=response_text))
            if len(self._history) > 40:
                self._history = self._history[-40:]

        await self._bus.publish(ResponseGenerated(
            trace_id=trace_uuid,
            text=response_text,
            language="de" if _looks_german(response_text) else "en",
        ))

        # Fire-and-forget: the curator extracts personal facts from the turn
        # and merges them into USER.md / people/*.md in a controlled manner.
        # Runs async, does not block the response.
        if self._curator is not None:
            try:
                asyncio.create_task(
                    self._curator.process_turn(user_text, response_text),
                    name="curator-process-turn",
                )
            except RuntimeError:
                # No running event loop (sync context) — skip.
                log.debug("Curator-Task nicht scheduled (kein Event-Loop)")

        return response_text

    async def _collect_vision_images(
        self,
        *,
        trace_id: UUID,
        user_text: str = "",
        is_smalltalk: bool = False,
    ) -> tuple[ImageBlock, ...]:
        """Returns the current screen as an ImageBlock for the brain turn.

        Factory/voice start the VisionContextProvider on the BrainManager.
        Without this bridge, blobs were captured but the actual brain call
        remained text-only.
        """
        vision = getattr(self, "_vision_provider", None)
        vision_none = vision is None
        paused = (
            bool(getattr(vision, "is_paused", False))
            if vision is not None
            else None
        )
        log.info(
            "Vision-Inject Diagnose: path=BrainManager vision_none=%s "
            "is_paused=%s brain_provider=%s",
            vision_none,
            paused,
            self._active_name,
        )
        if vision is None or paused:
            return ()

        # Wave 1 (omni-latency): conditional vision — skip the screenshot on
        # confidently text-only turns (skip-when-safe). Keep the per-turn image
        # tax only where the screen might actually matter. Anti-regression vs.
        # 2026-04-28: when in doubt, the gate keeps the image.
        perf = getattr(self._config, "performance", None)
        if getattr(perf, "conditional_vision", False):
            from jarvis.brain.vision_gate import should_attach_screenshot

            if not should_attach_screenshot(user_text, is_smalltalk=is_smalltalk):
                log.info("Vision-Inject skipped: text-only turn (%r)", user_text[:60])
                return ()

        try:
            from jarvis.brain.router import _read_observation_image_b64

            obs = await asyncio.wait_for(
                vision.current(), timeout=_VISION_COLLECT_TIMEOUT_S
            )
            hash_prefix = (obs.screenshot_hash or "")[:16]
            log.info(
                "Vision-Inject Observation: screenshot_path=%s "
                "screenshot_hash=%s window=%r",
                obs.screenshot_path,
                hash_prefix,
                getattr(obs, "window_title", None),
            )
            mime, image_b64 = await _read_observation_image_b64(obs)
            # Wave 1 (omni-latency): enforce max_image_kb (was dead config) —
            # cap the per-turn payload before it ships; no-op if already small.
            from jarvis.vision.image_budget import cap_image_b64

            vcfg = getattr(getattr(self._config.brain, "router", None), "vision", None)
            max_kb = int(getattr(vcfg, "max_image_kb", 0) or 0)
            if max_kb > 0:
                mime, image_b64 = cap_image_b64(mime, image_b64, max_kb * 1024)
            log.info(
                "Vision-Inject encoded: brain_provider=%s mime=%s "
                "screenshot_hash=%s len_image_b64=%d",
                self._active_name,
                mime,
                hash_prefix,
                len(image_b64),
            )
            if self._bus is not None:
                bytes_size = len(image_b64) * 3 // 4
                age_ms = int((time.time_ns() - obs.timestamp_ns) / 1_000_000)
                await self._bus.publish(VisionInjected(
                    trace_id=trace_id,
                    screenshot_hash=obs.screenshot_hash,
                    bytes_size=bytes_size,
                    capture_age_ms=age_ms,
                ))
            return (
                ImageBlock(
                    mime=mime,
                    data_b64=image_b64,
                    source_hash=obs.screenshot_hash,
                ),
            )
        except TimeoutError:
            log.warning(
                "Vision-Inject skipped: capture exceeded %.1fs — proceeding "
                "text-only (no hot-path hang). brain_provider=%s",
                _VISION_COLLECT_TIMEOUT_S,
                self._active_name,
            )
            return ()
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Vision-Inject fehlgeschlagen: path=BrainManager "
                "brain_provider=%s exc=%r",
                self._active_name,
                exc,
                exc_info=True,
            )
            return ()

    # Pipeline-Adapter
    async def __call__(self, text: str) -> str:
        return await self.generate(text)

    async def generate_stream(
        self,
        user_text: str,
        *,
        use_history: bool = True,
        trace_id: UUID | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> AsyncIterator[str]:
        """Latency sprint 1: streaming variant of ``generate``.

        Yields each brain text chunk in real time. Tool-use loops run as
        usual; pre-tool-use text is also streamed (the persona prompt forbids
        fillers, so this is uncritical).

        ``on_progress`` (stall-timeout signal): forwarded to the tool-use loop,
        which pings it at every model-round + tool boundary. The speech pipeline
        passes its ``_mark_brain_progress`` here so its *no-progress* deadline
        resets while a vision/tool turn is genuinely working but streaming no
        text (live bug 2026-06-01). ``None`` (default) is a no-op.

        Consumed via an ``asyncio.Queue`` between the producer task
        (``generate``) and the caller (``async for``). If the caller cancels
        the generator, the producer is also cancelled.

        Callers can reassemble the final aggregated text from the yielded
        chunks themselves — a helper may be added later if needed.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        sentinel: str | None = None
        # generate() returns the FINAL text — recovery-corrected when a leaked
        # tool_use was executed (see _recover_leaked_tool). Streaming previously
        # discarded this (BUG-028 pattern), so a leaked action-tool reached TTS
        # as raw JSON and the action was lost. We capture it here.
        holder: dict[str, str | None] = {"final": None}

        def _consumer(chunk: str) -> None:
            # ``put_nowait`` because the consumer is called on the sync
            # aggregator path (no await possible). Queue is unbounded.
            try:
                queue.put_nowait(chunk)
            except Exception:  # noqa: BLE001
                pass

        async def _producer() -> None:
            try:
                holder["final"] = await self.generate(
                    user_text,
                    use_history=use_history,
                    trace_id=trace_id,
                    text_consumer=_consumer,
                    on_progress=on_progress,
                )
            finally:
                # Sentinel signals "brain is done (or crashed)".
                queue.put_nowait(sentinel)

        task = asyncio.create_task(_producer(), name="brain-stream-producer")
        accumulated = ""
        leaked = False
        yielded = False
        try:
            while True:
                chunk = await queue.get()
                if chunk is sentinel:
                    break
                accumulated += chunk
                # A provider sometimes streams a tool_use block as TEXT instead
                # of invoking it ("oeffne den Editor" -> open_app/dispatch JSON).
                # Withhold those chunks so the raw JSON is never spoken (it would
                # scrub to silence and the action would be lost). generate()
                # recovers + executes the leaked tool and returns a speakable
                # result, which we yield once the stream ends.
                if not leaked and _looks_like_tool_use_leak(accumulated):
                    leaked = True
                if leaked:
                    continue
                yield chunk
                yielded = True
            # Surface generate()'s authoritative final text whenever NOTHING was
            # streamed to TTS — either because a leaked tool_use JSON was
            # withheld, OR because the brain produced a STRUCTURED / suppress
            # tool-call with no text chunks at all (dispatch_to_harness result,
            # spawn_worker ACK, recovered tool). Without this the user hears
            # silence on exactly those action turns — live repro 2026-05-25
            # "oeffne mir Chrome" returned empty while plain chat worked. The
            # old code only surfaced the final on the leaked-JSON path.
            if leaked or not yielded:
                final = (holder.get("final") or "").strip()
                if final and not _looks_like_tool_use_leak(final):
                    yield final
                elif leaked:
                    yield "Ich habe die Aktion erkannt, konnte sie aber nicht ausfuehren."
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    # Summarize — fuer OpenClaw-Announcements (Phase 5, Welle-4-rebrand)
    # ------------------------------------------------------------------

    async def summarize(self, text: str, *, max_tokens: int = 120) -> str:
        """Compresses text via the fast model of the active provider.

        Purpose: TTS announcements in 1-2 sentences, suitable for speech output.
        The stream is fully aggregated and capped at ~max_tokens * 4 characters
        (rough UTF-8 token heuristic).
        """
        if not text.strip():
            return ""

        brain = self._get_brain(self._active_name, self._fast_model(self._active_name))
        system_prompt = (
            "Du fasst Texte in 1-2 Saetzen zusammen, klar und praezise fuer "
            "Sprachausgabe. Antworte ausschliesslich mit der Zusammenfassung."
        )
        req = BrainRequest(
            messages=(
                BrainMessage(
                    role="user",
                    content=f"Fasse in 1-2 Saetzen zusammen, klar und praezise fuer Sprachausgabe: {text}",
                ),
            ),
            system=system_prompt,
            temperature=0.3,
            max_tokens=max_tokens,
            stream=True,
        )

        agg = await aggregate(brain.complete(req))
        summary = (agg.text or "").strip()

        char_cap = max_tokens * 4
        if len(summary) > char_cap:
            summary = summary[:char_cap].rstrip()
        return summary

    # ------------------------------------------------------------------
    # Tool-Registry
    # ------------------------------------------------------------------

    def set_tools(self, tools: dict[str, Tool]) -> None:
        self._tools = dict(tools)

    def add_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def clear_history(self) -> None:
        self._history = []

    # Roles the brain conversation buffer accepts for seeding. ``tool``
    # messages need a tool_call_id pairing and are never seeded standalone;
    # UI-only roles (e.g. ``preamble`` pre-ack bubbles) are not conversation.
    _SEEDABLE_ROLES: frozenset[str] = frozenset({"user", "assistant", "system"})
    # Same window the auto-append paths enforce (see the ``self._history =
    # self._history[-40:]`` trims throughout generate()/force-spawn).
    _HISTORY_MAX: int = 40

    def seed_history(self, turns: Iterable[Any]) -> None:
        """Preseed the conversation buffer with prior turns.

        Replaces ``_history`` so a re-opened chat (text continuation via
        ``POST /api/chats/{kind}/{id}/resume``) or a "Speak in this
        conversation" voice session (``.../speak``) continues coherently.
        This is the single primitive behind both Chats-manager paths.

        Pure in-memory, no LLM call and no I/O — safe to call before a voice
        session is armed without touching the voice critical path (AP-9/AP-11).

        Accepts an iterable of :class:`BrainMessage`, ``(role, text)`` tuples,
        or ``{"role": ..., "content"|"text": ...}`` dicts. Entries whose role
        is outside :attr:`_SEEDABLE_ROLES` (e.g. the UI-only ``preamble``
        bubble) and entries with empty text are dropped. The result is capped
        to :attr:`_HISTORY_MAX`, keeping the most recent turns — an empty
        input therefore behaves like :meth:`clear_history`.
        """
        seeded: list[BrainMessage] = []
        for item in turns:
            if isinstance(item, BrainMessage):
                role: Any = item.role
                content: Any = item.content
            elif isinstance(item, dict):
                role = item.get("role")
                content = item.get("content", item.get("text"))
            else:
                try:
                    role, content = item
                except (TypeError, ValueError):
                    continue
            if role not in self._SEEDABLE_ROLES:
                continue
            if isinstance(content, str):
                if not content.strip():
                    continue
            elif not content:
                continue
            seeded.append(
                item
                if isinstance(item, BrainMessage)
                else BrainMessage(role=role, content=content)
            )
        self._history = seeded[-self._HISTORY_MAX :]

    # ------------------------------------------------------------------
    # Live reload for the CLI tool registry (CLI integration, task 2)
    # ------------------------------------------------------------------

    def refresh_tools(self) -> None:
        """Reloads the tool dict from the factory.

        Triggered by the ``BrainToolsChanged`` event handler (see
        ``attach_to_bus``) after a new CLI connects. Idempotent — if the
        factory returns the same dict, effectively nothing changes.

        The simplest approach runs through ``_load_tools_for_tier`` and
        replaces ``self._tools`` in-place. The tier is derived from an
        internally set marker (the factory sets ``_tier`` during build).
        If no tier is known, the tool dict stays unchanged — the user must
        restart manually in that case.
        """
        tier = getattr(self, "_tier", None)
        if not tier:
            log.debug("refresh_tools: kein _tier gesetzt, skip")
            return
        try:
            # Lazy import: the factory may pull in heavy modules depending on
            # config (vision, harness). The import happens only on refresh,
            # not during BrainManager setup.
            from jarvis.brain.factory import _load_local_action_tools, _load_tools_for_tier
            from jarvis.harness.manager import HarnessManager
            from jarvis.safety import (
                ApprovalWorkflow,
                RiskTierEvaluator,
                ToolExecutor,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh_tools: Factory-Module nicht importierbar: %s", exc)
            return

        try:
            # Minimally invasive re-init for the tool load: the existing
            # ToolExecutor is retained (risk policy + approval are session-stable);
            # only the tool instances are re-instantiated.
            executor = self._tool_executor
            if executor is None:
                # Fallback: build an executor so tools can still be loaded —
                # in practice the manager always has one.
                from jarvis.clis.risk_integration import make_cli_patterns_fn
                evaluator = RiskTierEvaluator(
                    self._config.safety,
                    extra_patterns_fn=make_cli_patterns_fn(),
                )
                approval = ApprovalWorkflow(self._bus)
                executor = ToolExecutor(self._bus, evaluator, approval)

            harness_manager = HarnessManager(bus=self._bus)

            new_tools = _load_tools_for_tier(
                tier,
                bus=self._bus,
                executor=executor,
                harness_manager=harness_manager,
                user_profile=self._user_profile,
                people=self._people,
                config=self._config,
            )
            new_local_action_tools = _load_local_action_tools(
                bus=self._bus,
                harness_manager=harness_manager,
                config=self._config,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh_tools: Factory-Call fehlgeschlagen: %s", exc)
            return

        old_count = len(self._tools)
        self._tools = new_tools
        self._local_action_tools = new_local_action_tools
        log.info(
            "Tool-Registry refreshed: %d -> %d tools",
            old_count, len(new_tools),
        )

    def attach_to_bus(self, bus: EventBus | None = None) -> None:
        """Registers live-reload subscriptions on the event bus.

        Called after the factory build (``factory.py``). Currently:
        - ``BrainToolsChanged`` → ``refresh_tools()``
        - ``SecretConfigured`` → ``reactivate_provider()`` for the brain
          provider whose key was just set. Prevents a provider that already
          failed with "no API key" from being excluded from the fallback chain
          until the app is restarted.

        Called separately rather than in ``__init__`` so BrainManager can be
        constructed for tests without a bus subscription.
        """
        from jarvis.core.events import BrainToolsChanged, SecretConfigured

        target_bus = bus or self._bus
        if target_bus is None:
            return

        async def _on_tools_changed(ev: BrainToolsChanged) -> None:
            log.info("BrainToolsChanged empfangen (reason=%s) -> refresh_tools()", ev.reason)
            self.refresh_tools()

        target_bus.subscribe(BrainToolsChanged, _on_tools_changed)

        async def _on_secret_configured(ev: SecretConfigured) -> None:
            if ev.action != "set":
                return
            provider = _SECRET_KEY_TO_BRAIN.get(ev.key)
            if not provider:
                return
            self.reactivate_provider(provider)

        target_bus.subscribe(SecretConfigured, _on_secret_configured)

        from jarvis.core.events import ConfigReloaded

        async def _on_config_reloaded(ev: ConfigReloaded) -> None:
            # Hot-reload the reply-language pin so a Self-Mod / Control-API write
            # to ``brain.reply_language`` (SAFE, needs_restart=False) takes effect
            # on the NEXT turn without an app restart. The event carries only the
            # changed keys, so re-read the persisted value from disk. Never let a
            # bad value (ValueError) escape — that would kill the bus (AP-18).
            if "brain.reply_language" not in ev.changed_keys:
                return
            try:
                import asyncio as _asyncio

                from jarvis.core.config import load_config

                # Off the event loop — load_config() is a blocking disk read and
                # this subscriber fires on every SAFE-tier config write.
                cfg = await _asyncio.to_thread(load_config)
                raw = getattr(cfg.brain, "reply_language", "auto")
                self.set_reply_language(normalize_reply_language(raw))
            except Exception:  # noqa: BLE001 — survive without a live switch
                log.warning("reply-language hot-reload failed", exc_info=True)

        target_bus.subscribe(ConfigReloaded, _on_config_reloaded)

    # ------------------------------------------------------------------
    # Back-compat aliases (for existing tests)
    # ------------------------------------------------------------------

    @property
    def _providers(self) -> dict[str, Brain]:
        """Back-compat: exposes the cache as {provider_name: active_instance}."""
        out: dict[str, Brain] = {}
        for (name, _model), inst in self._brain_cache.items():
            out.setdefault(name, inst)
        return out

    @property
    def _tool_executor_ref(self) -> ToolExecutor | None:
        return self._tool_executor

    def _get_or_create(self, name: str) -> Brain:
        """Back-compat wrapper — uses the config model when available."""
        return self._get_brain(name, self._fast_model(name))

    async def use_deep_model(self) -> bool:
        deep = self._deep_model(self._active_name)
        if not deep:
            return False
        self._force_level = "deep"
        return True

    async def use_fast_model(self) -> bool:
        fast = self._fast_model(self._active_name)
        if not fast:
            return False
        self._force_level = "fast"
        return True

    @property
    def dispatcher(self) -> BrainDispatcher:
        """Back-compat: builds a dispatcher with the fast model of the active provider."""
        brain = self._get_brain(self._active_name, self._fast_model(self._active_name))
        return self._build_dispatcher(brain)

    def snapshot(self) -> dict[str, Any]:
        return {
            "active_provider": self._active_name,
            "force_level": self._force_level,
            "history_size": len(self._history),
            "tools_available": sorted(self._tools.keys()),
            "providers_available": self.available_providers(),
            "providers_failed": self.failed_providers(),
            "fast_model": self._fast_model(self._active_name),
            "deep_model": self._deep_model(self._active_name),
        }


def _is_rate_limit_exc(exc: Exception) -> bool:
    """Heuristic: 429 / rate_limit_error / status_code=429."""
    msg = str(exc).lower()
    if "429" in msg or "rate_limit" in msg or "rate-limit" in msg:
        return True
    if "rate limit" in msg or "too many requests" in msg:
        return True
    # Anthropic-SDK-RateLimitError
    if type(exc).__name__ == "RateLimitError":
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    return False


def _looks_german(text: str) -> bool:
    t = text.lower()
    hints_de = ("ich", "nicht", "das", "ist", "und", "oder", "bitte", "entschuldigung", "ja", "nein")
    hints_en = ("the", "and", "is", "are", "hello", "hi", "yes", "no")
    score_de = sum(1 for h in hints_de if f" {h} " in f" {t} ")
    score_en = sum(1 for h in hints_en if f" {h} " in f" {t} ")
    return score_de >= score_en


def _is_missing_key_exc(msg: str) -> bool:
    """Heuristic: provider reports a missing API key or invalid auth state."""
    m = msg.lower()
    return any(k in m for k in (
        "kein grok-api-key", "kein gemini-api-key", "kein openai-api-key",
        "kein anthropic-api-key", "kein claude-credential",
        "kein openrouter-api-key", "kein xai-api-key",
        "api_key not set", "api key not found",
        "api_key is not set", "api key is not set",
        "anthropic_api_key is not set", "openai_api_key is not set",
        "gemini_api_key is not set", "xai_api_key is not set",
        "api-key gefunden", "missing api key", "no api key",
        "not configured",
        "api-key nicht gesetzt", "apikey missing",
        "not logged in", "please run /login", "credentials.json",
    ))


def _is_account_blocked_exc(msg: str) -> bool:
    """Heuristic: provider account has a terminal auth/quota/billing problem.
    Examples observed live (all 2026-04-29):

      - Anthropic 400: ``Your credit balance is too low to access the
        Anthropic API. Please go to Plans & Billing.``
      - xAI 404: ``The model grok-4.1-fast does not exist or your team
        e6d8f57e-... does not have access to it.``
      - OpenAI 403: ``The model `o1-pro` is not available on your tier.``
      - Gemini 403: ``Quota exceeded for ...`` (unlike 429 — terminal).

    These providers are dead for the session (a simple retry won't help).
    BrainManager pushes them immediately into _dead_providers and emits a
    user-actionable setup message instead of "provider unreachable".
    """
    m = msg.lower()
    return any(k in m for k in (
        "credit balance is too low",
        "credit balance too low",
        "plans & billing",
        "billing required",
        "your team",
        "your team does not have access",
        "team does not have access",
        "team_does_not_have_access",
        "not available on your tier",
        "subscription required",
        "upgrade plan",
        "upgrade your plan",
        "exceeded your quota",  # Gemini-style, terminal vs. 429
        "quota exceeded for",
        "billing not active",
        "payment required",
        "account is suspended",
    ))


# User-friendly labels per provider — what the user needs to do.
def _is_invalid_model_exc(msg: str) -> bool:
    """Heuristic: provider reports an unknown/invalid model ID.

    Do NOT use when the error is more likely an account problem
    (see `_is_account_blocked_exc`) — otherwise an account 404 would
    incorrectly land as "config bug, fix jarvis.toml".
    """
    if _is_account_blocked_exc(msg):
        return False
    m = msg.lower()
    return any(k in m for k in (
        "model_not_found", "model not found", "model does not exist",
        "unknown model", "invalid model", "invalid_model",
        "not a valid model", "unsupported model",
    ))


def _classify_provider_error(msg: str, *, default: str) -> str:
    """Central classifier for provider error strings.

    Order is intentional:
      1. missing_key (auth/config — important for the dead-list).
      2. account_blocked (credit/quota/tier — also dead-list, different message).
      3. invalid_model (config bug — different action: fix jarvis.toml).
      4. rate_limit (transient — handled by its own cooldown path).
      5. default (init_fail or call_fail — caller decides).

    missing_key is checked before rate_limit so an auth error that happens to
    contain "limit" (e.g. "exceeded the rate limit for this resource") is not
    incorrectly classified as a 429 cooldown.
    """
    if _is_missing_key_exc(msg):
        return "missing_key"
    if _is_account_blocked_exc(msg):
        return "account_blocked"
    if _is_invalid_model_exc(msg):
        return "invalid_model"
    m = msg.lower()
    if any(s in m for s in ("429", "rate_limit", "rate-limit",
                             "rate limit", "too many requests")):
        return "rate_limit"
    return default


_PROVIDER_SETUP_HINTS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY setzen (Key via https://aistudio.google.com/apikey)",
    "claude-api": "ANTHROPIC_API_KEY setzen",
    "openai": "OPENAI_API_KEY setzen",
    "openrouter": "OPENROUTER_API_KEY setzen",
    "grok": "XAI_API_KEY setzen",
    "ollama-local": "Ollama-Server starten (localhost:11434)",
    "ollama-cloud": "Ollama-Cloud-Token setzen",
}


def _format_provider_chain_error(
    errors: list[tuple[str, str, str, str]],
) -> str:
    """Builds a meaningful user message from the per-provider error list.

    Prioritises root causes: when the **primary** provider has no key,
    THAT is the main message. Rate limits are listed as secondary.
    """
    if not errors:
        return ("Keine Brain-Provider konfiguriert. "
                "Setze mindestens GEMINI_API_KEY oder ANTHROPIC_API_KEY.")

    missing_keys: list[str] = []
    account_blocked: list[str] = []
    invalid_models: list[str] = []
    rate_limited: list[str] = []
    empty_responses: list[str] = []
    other_fails: list[str] = []
    for prov_name, _model, kind, _detail in errors:
        if kind == "missing_key":
            missing_keys.append(prov_name)
        elif kind == "account_blocked":
            account_blocked.append(prov_name)
        elif kind == "invalid_model":
            invalid_models.append(prov_name)
        elif kind in ("rate_limit", "skipped_cooldown"):
            rate_limited.append(prov_name)
        elif kind == "empty_response":
            empty_responses.append(prov_name)
        else:
            other_fails.append(prov_name)

    # Deduplicate while preserving order (first-listed priority).
    def _uniq(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return out

    missing_keys = _uniq(missing_keys)
    account_blocked = _uniq(account_blocked)
    invalid_models = _uniq(invalid_models)
    rate_limited = _uniq(rate_limited)
    empty_responses = _uniq(empty_responses)
    other_fails = _uniq(other_fails)

    parts: list[str] = []
    # 1. Setup hint for the most important missing keys (max 2).
    # Priority: Sidebar → API Keys is the easiest setup path for non-coders.
    # Specific ENV/CLI hints for power users come after.
    if missing_keys:
        hints = [
            _PROVIDER_SETUP_HINTS.get(p, f"{p}: Setup pruefen")
            for p in missing_keys[:2]
        ]
        parts.append(
            "Kein Brain-Key gefunden. Sidebar -> API-Keys oeffnen und "
            f"einen Key setzen ({' oder '.join(hints)})."
        )
    # 2. Account block (credit/quota/tier) — user must take action
    if account_blocked:
        parts.append(
            f"Account-Problem bei {', '.join(account_blocked)}: "
            "Credit aufladen, Plan upgraden oder Modell-Tier freischalten. "
            "Bei Anthropic: console.anthropic.com/settings/billing. "
            "Bei xAI: console.x.ai/team/billing."
        )
    if invalid_models:
        parts.append(
            f"Ungueltige Model-ID bei {', '.join(invalid_models)}. "
            "jarvis.toml und TIER_DEFAULTS_BY_PROVIDER pruefen."
        )
    # 2. Rate limits are listed as supplementary info
    if rate_limited:
        prefix = "Ausserdem rate" if parts else "Rate"
        parts.append(
            f"{prefix}-limited: {', '.join(rate_limited)}. "
            "Einen Moment abwarten oder auf anderen Provider wechseln."
        )
    # 3. Empty responses (safety block) — separate user-actionable case
    if empty_responses and not missing_keys and not invalid_models:
        parts.append(
            f"Provider {', '.join(empty_responses)} hat leer geantwortet "
            "(vermutlich Safety-Filter). Anders formulieren oder anderen "
            "Provider per UI aktivieren."
        )
    # 4. Other failures only mentioned when there is no clear root cause
    if (not missing_keys and not invalid_models and not rate_limited
            and not empty_responses and other_fails):
        parts.append(
            f"Provider {', '.join(other_fails)} unerreichbar. "
            "Netzwerk pruefen."
        )
    return " ".join(parts)
