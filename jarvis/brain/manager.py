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
from jarvis.core.turn_language import (
    DEFAULT_LOCALE,
    detect_text_language,
    is_substantive_turn,
    resolve_output_language,
    resolve_turn_language,
)
from jarvis.voice.action_phrases import action_phrase, cu_failure_readback
from jarvis.memory import CoreMemory, PersonStore, RecallStore, Soul, UserProfile
from jarvis.memory.curator import Curator
from jarvis.safety.tool_executor import ToolExecutor

from .dispatcher import BrainDispatcher
from .healthcheck import BrainConfigError
from .intent_router import RoutingDecision, classify
from .local_action_gate import (
    HARNESS_NAME,
    LocalActionMode,
    LocalToolCall,
    _looks_like_desktop_control,
    is_open_app_intent,
    match_local_action,
    requires_external_integration,
)
from .local_action_gate import _normalize as _gate_normalize
from .mission_command_gate import match_mission_command
from .assistant_name import (
    DEFAULT_ASSISTANT_NAME,
    PERSONA_BASELINE_NAME,
    resolve_assistant_name,
)
from .persona_loader import load_effective_persona_prompt
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

SUBAGENT_ONLY_BRAIN_PROVIDERS: frozenset[str] = frozenset(
    {"antigravity", "codex", "openai-codex"}
)

_MAIN_BRAIN_FALLBACK_PROVIDER_ORDER: tuple[str, ...] = (
    "grok",
    "gemini",
    "claude-api",
    "openai",
    "openrouter",
)

# Human-readable display names for each brain provider id. Used to tell the
# answering LLM which provider/model it is embodying this turn (the system
# prompt never carried this before, so a "which model are you?" question got a
# guessed answer that defaulted to "Gemini" — forensic 2026-06-20, voice session
# 15:15: Grok was live and answering, yet Jarvis claimed to be Gemini). Kept
# self-contained in the brain layer (no UI-catalog import — that would invert the
# layer dependency) and defensive: an unmapped id degrades to a readable label.
_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "claude-api": "Anthropic Claude",
    "openai": "OpenAI GPT",
    # Both the CLI brain id ("codex") and the sub-agent value ("openai-codex")
    # map to the same readable label, so whichever surfaces as a turn prov_name
    # is named correctly (the user explicitly wants "Codex / GPT-5.5" recognised).
    "codex": "OpenAI Codex (GPT-5.5)",
    "openai-codex": "OpenAI Codex (GPT-5.5)",
    "openrouter": "OpenRouter",
    "gemini": "Google Gemini",
    "grok": "Grok (xAI)",
    "antigravity": "Google Antigravity (Gemini)",
}


def _provider_display_name(provider: str) -> str:
    """A readable label for a brain provider id (never crashes on unknown ids)."""
    pid = (provider or "").strip()
    mapped = _PROVIDER_DISPLAY_NAMES.get(pid)
    if mapped:
        return mapped
    # Unknown id → readable fallback: "some-new_provider" → "Some New Provider".
    return pid.replace("-", " ").replace("_", " ").title() or "the configured provider"


def _provider_identity_directive(provider: str, model: str | None, name: str) -> str:
    """Authoritative, anti-guessing self-identity line for the system prompt.

    The single source of truth for "which AI model am I right now?". Names the
    *actual* provider/model answering this turn (set per fallback-chain attempt
    in ``generate()``), and carves out the one allowed exception to the persona's
    "never discuss your technical nature" rule: a direct provider/model question
    gets an honest, specific answer instead of a guessed "Gemini".
    """
    label = _provider_display_name(provider)
    model_str = (model or "").strip() or "the provider's default"
    return (
        f"ACTIVE BRAIN MODEL — INFRASTRUCTURE FACT (authoritative): You are right "
        f"now running on the brain provider {label} (model: {model_str}). {label} "
        f"is the provider actually generating your reply this turn. If the user "
        f"asks which provider, backend, or AI model is powering you right now, "
        f"answer truthfully and specifically with this — never guess, and never "
        f"name a provider other than the one stated here (a recurring failure was "
        f'wrongly claiming to be "Gemini"). This is the one allowed exception to '
        f"the persona rule about not discussing your own technical nature: a "
        f"direct question about your underlying provider/model gets an honest, "
        f"specific answer; otherwise you stay {name} and never raise it unprompted."
    )


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
        # grok-4.3 (released 2026-04-30) is simultaneously the fastest
        # AND most capable Grok — replaces 4.1-fast in both tiers.
        "grok": "grok-4.3",
        "deepseek": "deepseek-chat",
        "openrouter": "anthropic/claude-haiku-4.5",
        "mistral": "mistral-small-3.1",
    },
    "deep": {
        # Frontier 2026-Q2 — deep brain (user mandate 2026-04-29:
        # frontier everywhere). 2026-06-14: switched from claude-fable-5 to
        # claude-opus-4-8 — fable-5 is approved-access-only and the Claude Max
        # subscription cannot reach it ("Claude Fable 5 is currently
        # unavailable"); this deep tier calls the Brain API directly and has no
        # model-unavailable retry, so the pinned model must be one we can reach.
        "claude-api": "claude-opus-4-8",
        "gemini": "gemini-3.1-pro-preview",
        "openai": "gpt-5.5-pro",
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


def _coerce_main_brain_provider(provider: str | None, *fallbacks: str | None) -> str:
    """Return a main-brain-capable provider.

    Some provider integrations exist only for the heavy subagent worker. They
    must remain present in the codebase for that path, but an old persisted
    ``brain.primary`` must not make the main router run through them.
    """
    candidate = (provider or "").strip()
    if candidate and candidate not in SUBAGENT_ONLY_BRAIN_PROVIDERS:
        return candidate
    for fallback in (*fallbacks, *_MAIN_BRAIN_FALLBACK_PROVIDER_ORDER):
        value = (fallback or "").strip()
        if value and value not in SUBAGENT_ONLY_BRAIN_PROVIDERS:
            return value
    return _MAIN_BRAIN_FALLBACK_PROVIDER_ORDER[0]


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


# ``MessageSent.source_layer`` values for CONVERSATIONAL turns that must NEVER
# force-spawn a mission, whatever their text contains. A drag-dropped mission
# recap (``ui.web.ws.mission_inject``) embeds the dropped card's OWN text
# verbatim, so a title carrying a spawn trigger ("sub-agent") or an action verb
# ("Write …") would otherwise leak that trigger into the directive and spawn a
# NEW mission whose only deliverable is a conversational recap (no file) ->
# empty diff -> critic_loop_exhausted. The doom-loop fixed 2026-06-16: every
# failed mission the user dragged in to discuss spawned another failed mission.
# Keep in sync with ``jarvis.ui.web.mission_inject.MISSION_INJECT_SOURCE_LAYER``
# and ``jarvis.brain.drop_context.DROP_SOURCE_LAYER`` (parity tests in
# tests/unit/brain/test_routing.py). ``ui.drop`` = a dragged-and-dropped file /
# image / text: reacted to inline, never auto-dispatched as a worker.
_NON_SPAWN_SOURCE_LAYERS: frozenset[str] = frozenset(
    {"ui.web.ws.mission_inject", "ui.drop"}
)


# Two-turn voice/chat confirmation for a consequential ``ask``-tier tool. Turn N
# defers the action (the executor returns ``VOICE_CONFIRM_SENTINEL``) and speaks a
# question; this holds what turn N+1 needs to resolve the user's "ja"/"nein".
# Bounded re-asks avoid a soft-lock on a persistently ambiguous answer.
_MAX_CONFIRM_REASKS = 2


class _PendingVoiceConfirm:
    """A deferred consequential action awaiting the user's next yes/no."""

    __slots__ = ("trace_id", "lang", "tool_name", "reasks")

    def __init__(self, trace_id: UUID, lang: str, tool_name: str, reasks: int = 0) -> None:
        self.trace_id = trace_id
        self.lang = lang
        self.tool_name = tool_name
        self.reasks = reasks


# Option A (2026-06-15): a heavy-research request whose deliverable is an ANSWER
# (comparison / overview / recommendation / summary) is answered INLINE via the
# router's search_web tool; only research that BUILDS a verifiable ARTIFACT (a
# file / report / document) offloads to a sub-agent mission, because the
# Worker->Critic pipeline grades artifacts via git diff and is hostile to an
# answer-only research turn (empty-diff veto -> critic_loop_exhausted, live
# mission 019ecb56, 2026-06-15). These three regexes decide "wants an artifact".
#
# A build/produce VERB (write/create/build/generate/export/save + DE forms).
# Deliberately disjoint from the research/analysis verbs in
# ``heavy_research_verbs`` (recherchier/analysier/compar/...) so a pure research
# answer never matches.
_BUILD_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(writ|wrote|creat|build|buil|generat|produc|draft|compil|render|"
    r"export|saved?|"
    r"schreib|geschrieben|erstell|baue|bau|generier|verfass|speicher|exportier)"
    r"\w*",
    re.IGNORECASE,
)
# A document / artefact NOUN — the thing being built is a file-shaped deliverable.
_DOC_NOUN_RE: re.Pattern[str] = re.compile(
    r"\b(report|document|deck|slides?|spreadsheet|presentation|"
    # build-a-deliverable nouns (a file-shaped result the Worker->Critic
    # pipeline can verify via git diff): web/app/doc artefacts, DE + EN. A
    # build VERB is still required by _research_wants_artifact, so a bare
    # question ("what is an html file") never matches. "summary" is
    # deliberately EXCLUDED — it is an ANSWER, not a file (discriminator test).
    r"website|web ?page|webseite|html|app|application|anwendung|"
    r"dashboard|landing ?page|visuali\w+|script|skript|"
    r"bericht|dokument|tabelle|praesentation|präsentation)\b",  # i18n-allow: DE artefact nouns
    re.IGNORECASE,
)
# A named file / real extension, or an explicit "into a file" instruction — an
# artefact deliverable on its own, no build verb required ("... into ai_news.md").
_NAMED_FILE_RE: re.Pattern[str] = re.compile(
    r"\.(md|txt|html?|json|csv|pdf|docx?|xlsx?|pptx?|ya?ml|toml)\b"
    r"|\bfile\s+named\b|\bnamed\s+\S+\.\w+"
    r"|\bdatei\s+namens\b|\bin\s+eine\s+datei\b|\bin\s+die\s+datei\b"  # i18n-allow: DE file phrasing
    r"|\binto\s+a\s+file\b",
    re.IGNORECASE,
)


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
    # The live SURFACE the user names to act ON — screen / Bildschirm. A request
    # that points at the screen is computer-use (a worker has no desktop), so
    # naming it must register here: it keeps the turn OFF the sub-agent path and
    # marks it an action turn so a tool-incapable talker delegates it to a
    # tool-capable provider that picks computer_use (user pain 2026-06-21:
    # "mach es am Bildschirm" / "do it on screen" was not recognized at all).
    r"bildschirm|screen|"
    r"maus|mouse|cursor"
    r")\w*\b",
    re.IGNORECASE,
)

_INSTRUCTIONAL_QUESTION_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:"
    # "wie <verb> ich/man …" — a HOW-TO question, never a build request. The
    # build/create verbs are listed too so "wie erstelle/baue/schreibe ich eine
    # HTML-Datei" stays an inline answer and is not mistaken for "build me a file"
    # (live bug 2026-06-21). The English how-to is already caught by "how do/can …".
    r"wie\s+(?:kann|koennte|könnte|muss|soll|mach|mache|macht|geht|funktioniert"
    r"|erstell|erstelle|erstellt|baue|bau|baut|schreib|schreibe|schreibt"
    r"|programmier|programmiere|generier|generiere|implementier|implementiere)\s+"
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
# leading tokens collapse ("Hey Jarvis, hallo, öffne ..."), and swallows the i18n-allow
# trailing separators (comma / period / …). Longer tokens ("hey jarvis") precede
# their prefix ("hey") so the longest run is consumed. Live bug 2026-06-07
# (data/jarvis_desktop.log 18:19:07): "Hallo, öffne ihn für mich" was silenced i18n-allow
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


# A clear ACTION / request signal inside an utterance that ALSO matched the
# smalltalk allowlist. A continuation-recombine (or a polite preamble) can glue
# an answered chit-chat turn onto a real command (a smalltalk greeting followed
# by "open the oldest Bill-Gates post for me") or trail one ("open Chrome,
# thanks"). The smalltalk allowlist then matches the conversational part and
# (without this signal) the WHOLE turn is demoted to a tool-less smalltalk turn,
# hiding computer_use / spawn_worker (live bug 2026-06-19 11:43, the Bill-Gates
# turn: the deep brain answered a no-op "saved your note" reply and never opened
# the browser). When this signal is present the turn is a COMMAND, not
# chit-chat, so ``_is_smalltalk`` keeps the action tools visible. Pure regex, no
# LLM (AP-11). Intentionally NARROW (high-signal tokens + explicit request
# framing only) so a long but signal-less friendly remark carries no match and
# stays smalltalk, preserving the anti-fake-spawn tool-hiding.
_ACTION_REQUEST_RE = re.compile(
    r"(?:"
    # open / launch an app, file, page, browser
    r"\b(?:oeffn\w*|öffn\w*|aufmach\w*|aufzumach\w*|start\w*|open\w*|launch\w*)\b|"  # i18n-allow
    # research / analysis / search
    r"\b(?:recherchier\w*|research\w*|analys\w*|analyz\w*|untersuch\w*|"  # i18n-allow
    r"vergleich\w*|such\w*|search\w*|google\w*)\b|"  # i18n-allow
    # explicit action verbs
    r"\b(?:zeig\w*|lies|lese|liest|schreib\w*|install\w*|deinstallier\w*|"  # i18n-allow
    r"deploy\w*|spawn\w*|delegier\w*)\b|"
    # request framing (DE)
    r"\bich\s+(?:möchte|will|brauche|hätte\s+gern)\b|"  # i18n-allow
    r"\b(?:kannst|könntest|würdest)\s+du\b|"  # i18n-allow
    r"\b(?:mach|zeig|gib|hol|such|lies|öffne|bau)\s+(?:mir|mal|uns)\b|"  # i18n-allow
    # request framing (EN)
    r"\b(?:can|could|would)\s+you\b|\bi\s+(?:want|need)\b|\bi'?d\s+like\b|"
    r"\b(?:show|give|help)\s+me\b"
    r")",
    re.IGNORECASE,
)


def _looks_like_pc_control(user_text: str) -> bool:
    """Detects local screen/PC control requests intended for the computer-use harness."""
    return bool(_PC_CONTROL_RE.search(user_text or ""))


# Subset of ``force_spawn_phrases`` that NAMES the execution vehicle (a worker),
# as opposed to merely describing how THOROUGH the work should be. This is a
# PARTITION of the existing trigger phrases, not a new detection list: it only
# decides which matched trigger keeps absolute priority over the computer-use
# stand-down. Naming the vehicle ("subagent" / "spawn" / "openclaw" / "delegate")
# is an UNAMBIGUOUS spawn request (mandate 2026-06-15) and wins over everything.
# A DEPTH marker ("deep dive" / "gründlich" / "umfassend" / …) is ambiguous — it
# overlaps with computer-use requests ("Mach einen Deep Dive mit Computer Use in
# meinem Chrome Browser …") — so it must NOT override an explicit on-screen
# request; that computer-use-vs-spawn call is the LLM router's. Matched as a
# substring of the trigger the regex returned, so conjugations are covered
# ("spawne"/"gespawnt" -> "spawn", "delegiert" -> "delegier"). No depth phrase
# contains any of these stems, so the partition is clean.
_VEHICLE_NAME_TRIGGER_STEMS: frozenset[str] = frozenset({
    "openclaw", "open claw", "open-claw",
    "subagent", "sub-agent", "sub agent",
    "spawn", "delegier", "delegate",
})


def _trigger_names_vehicle(matched_trigger: str) -> bool:
    """True iff the matched force-spawn trigger NAMES a worker vehicle (vs. a
    thoroughness/depth descriptor). Only a vehicle name keeps absolute priority
    over the computer-use stand-down — a depth marker yields to it."""
    m = (matched_trigger or "").strip().lower()
    return any(stem in m for stem in _VEHICLE_NAME_TRIGGER_STEMS)


def _is_instructional_question(user_text: str) -> bool:
    """True for how-to / explanatory questions that should be answered directly."""
    return bool(_INSTRUCTIONAL_QUESTION_RE.search(user_text or ""))


# Opinion / advice / recommendation / decision questions, and casual
# question-openers ("ich hab da mal eine Frage"). These are CONVERSATION, not
# work: the brain answers them inline — they must NEVER force-spawn a worker,
# even when they contain an everyday word that collides with an action verb in
# the universal catalogue ("Frage" -> "frag"/"frage", the filler particle
# "halt" -> "halt"). Live bug 2026-06-19 (voice session 11:53, San-Francisco
# emigration turn): "ich hab ne Frage ... was würdest du mir empfehlen?"
# force-spawned because has_action_intent matched "Frage"/"halt", so
# _is_generic_subagent_work classified a pure chat turn as generic sub-agent
# work; the answer then returned out-of-band via the MissionAnnouncer and never
# reached the session transcript. Precision over recall: matched only by clear
# opinion/advice/decision phrasings, not by every question. DE/EN/ES, with
# umlaut + ASCII variants (STT emits either). Pure regex (AP-11 safe).
_OPINION_ADVICE_QUESTION_RE = re.compile(
    r"(?:"
    # advice / recommendation (DE)
    r"was\s+(?:w[üu]rdest|wuerdest|w[üu]rde|wuerde)\s+du\b"
    r"|was\s+(?:empfiehlst|r[äa]tst|raetst|schl[äa]gst|schlaegst)\s+du\b"
    r"|(?:hast|h[äa]ttest|haettest)\s+du\s+(?:einen?\s+)?(?:tipp|rat|empfehlung|vorschlag)"
    # opinion (DE)
    r"|was\s+(?:h[äa]ltst|haeltst|meinst|denkst|sagst)\s+du\b"
    r"|wie\s+(?:siehst|findest|beurteilst)\s+du\b"
    r"|(?:deiner|aus\s+deiner)\s+(?:meinung|sicht)\b"
    r"|was\s+ist\s+deine\s+(?:meinung|empfehlung|einsch[äa]tzung|einschaetzung)"
    # decision help (DE)
    r"|soll(?:te)?\s+ich\b[^?]*\boder\b"
    r"|was\s+(?:ist|w[äa]re|waere)\s+(?:besser|kl[üu]ger|klueger|sinnvoller)\b"
    # conversational question opener (DE)
    r"|ich\s+(?:hab|habe|h[äa]tte|haette)\s+(?:da\s+)?(?:mal\s+)?(?:noch\s+)?(?:'?ne|eine)\s+frage"
    r"|kann\s+ich\s+dich\s+(?:mal\s+)?(?:was|etwas)\s+fragen"
    # advice / opinion (EN)
    r"|what\s+(?:would|do)\s+you\s+(?:recommend|suggest|advise|think)\b"
    r"|what\s+should\s+i\b"
    r"|should\s+i\b[^?]*\bor\b"
    r"|(?:what(?:'s|\s+is)\s+)?your\s+(?:opinion|advice|take|recommendation)\b"
    r"|do\s+you\s+think\b"
    r"|i\s+(?:have|'ve\s+got|got)\s+a\s+question\b"
    r"|can\s+i\s+ask\s+you\b"
    # advice / opinion (ES)
    r"|qu[ée]\s+(?:me\s+)?(?:recomiendas|aconsejas|sugieres)\b"
    r"|qu[ée]\s+(?:opinas|piensas|crees)\b"
    r"|tengo\s+una\s+pregunta\b"
    r"|deber[íi]a\s+"
    r")",
    re.IGNORECASE,
)


def _is_opinion_advice_question(user_text: str) -> bool:
    """True for opinion / advice / recommendation / decision questions (and
    casual question-openers) that must be answered inline, never force-spawned.

    See ``_OPINION_ADVICE_QUESTION_RE`` for the full rationale (live bug
    2026-06-19): a conversational turn must not be dispatched to a worker just
    because an everyday word collides with an action verb.
    """
    return bool(_OPINION_ADVICE_QUESTION_RE.search(user_text or ""))


# A spawn / sub-agent / worker token in DE/EN/ES (declined forms included). Used
# by both the decline guard below and nowhere else — kept local on purpose.
_SPAWN_TOKEN = (
    r"(?:sub-?agent\w*|subagent\w*|subagente\w*|worker\w*|trabajador\w*|"
    r"spawn\w*|delegier\w*|delegate\w*|delega\w*)"
)

# Explicit spawn DECLINE: the user literally says "don't spawn a subagent" /
# "no sub-agent" / "talk to me directly". The explicit heavy-work trigger hoist
# in ``_should_force_spawn`` is NEGATION-BLIND — it substring-matches the
# trigger word ("Subagent"/"spawn") and force-spawns, doing the exact OPPOSITE
# of what the user asked. A decline must therefore HARD-stand-down BEFORE that
# hoist. Live bug 2026-06-19 (voice session 18:41, Turn 2): "Nee, ich möchte,
# dass du keinen Subagent dafür spawnst. Ich möchte, dass du direkt mit mir
# sprichst." force-spawned (trigger match='Subagent'). Recall over precision: a
# missed decline re-spawns against an explicit "no" (the user-hostile bug);
# a rare over-match only hands the choice back to the brain, which still sees
# spawn_worker and can spawn if it judges so. Pure regex (AP-11), DE/EN/ES,
# umlaut + ASCII variants. Char-window (not word-count) so commas between the
# negation and the token ("nicht, dass du das spawnst") still match.
_SPAWN_DECLINE_RE = re.compile(
    r"(?:"
    # negated spawn / subagent / worker (DE): kein* / nicht / ohne / niemals
    r"\bkein(?:e|en|er|es|s)?\b[^.?!]{0,20}" + _SPAWN_TOKEN
    + r"|\b(?:nicht|ohne|niemals)\b[^.?!]{0,20}" + _SPAWN_TOKEN
    # talk-to-me-directly (DE)
    + r"|\bdirekt\s+mit\s+mir\b"
    + r"|\b(?:sprich|red|rede|antwort|antworte|sag)\w*\b[^.?!]{0,15}\bdirekt\b"
    # negated spawn / subagent / worker (EN)
    + r"|\b(?:no|without|don'?t|do\s+not|dont|never|not)\b[^.?!]{0,22}" + _SPAWN_TOKEN
    # talk-to-me-directly (EN). NB: a bare "just talk/tell me" arm was
    # deliberately removed (review MAJOR-1) — without a directness/spawn token
    # it false-matched a compound command ("Just tell me, spawn a subagent to
    # analyse the logs"), swallowing a genuine spawn request. The directness
    # intent is already carried by the \bdirectly\b / \bdirekt mit mir\b arms.
    + r"|\b(?:talk|answer|respond|speak)\b[^.?!]{0,12}\bdirectly\b"
    # negated spawn / subagent + talk-directly (ES)
    + r"|\bno\b[^.?!]{0,22}" + _SPAWN_TOKEN
    + r"|\bh[áa]bla(?:me)?\b[^.?!]{0,15}\bdirectamente\b"
    + r")",
    re.IGNORECASE,
)


def _is_spawn_decline(user_text: str) -> bool:
    """True when the user explicitly declines a worker spawn — "don't spawn a
    subagent", "no sub-agent", "talk to me directly". Must override the
    negation-blind explicit-trigger hoist in ``_should_force_spawn``.

    See ``_SPAWN_DECLINE_RE`` for the full rationale (live bug 2026-06-19,
    Turn 2): an explicit decline that NAMES the vehicle ("Subagent"/"spawn")
    must never be read as a spawn request.
    """
    return bool(_SPAWN_DECLINE_RE.search(user_text or ""))


# Conversational coaching: "help me [get better at a soft / cognitive /
# conversational skill]" — asking, thinking, phrasing, deciding, understanding,
# expressing, communicating. This is CONVERSATION (Jarvis answers inline and
# asks the user smart questions), never a heavy-worker spawn. It trips the
# action-verb catalogue when the coaching OBJECT is itself a verb ("intelligent
# zu fragen" -> "frag"/"frage"). Live bug 2026-06-19 (voice session 18:41,
# Turn 1): "Hilf mir aber dabei, intelligent zu fragen. Für mich ist Fragen
# einer der Schlüssel für Erfolg, verstehst du?" -> matched action verbs
# ['frag','frage'] -> has_action_intent -> _is_generic_subagent_work ->
# force-spawn. High precision: a help/teach framing AND a cognitive/
# conversational object verb must BOTH be present, so "Hilf mir, eine E-Mail zu
# schreiben und zu senden" (concrete artifact) does NOT match and stays
# spawnable. Pure regex (AP-11), DE/EN/ES, umlaut + ASCII variants (the runtime
# output-language doctrine mandates all three locales; the sibling guards cover
# es too).
_CONVERSATIONAL_COACHING_RE = re.compile(
    r"(?:"
    # DE: help / teach / show-me-how framing ...
    r"\b(?:hilf|hilfst|helfen|helf|bring\s+mir\s+bei|beibring\w*|lehr\w*|"
    r"zeig\s+mir,?\s+wie)\b"
    r"[^.?!]{0,50}"
    # ... + a cognitive / conversational skill object
    r"\b(?:frag\w*|denk\w*|nachdenk\w*|formulier\w*|verstehen|verstehe|"
    r"entscheid\w*|reflektier\w*|aus(?:zu)?dr[üu]ck\w*|kommunizier\w*|"
    r"argumentier\w*|[üu]berleg\w*|zuh[öo]r\w*|reden|sprechen)\b"
    # EN: help / teach / show me ... + a cognitive skill object
    r"|\b(?:help|teach|show)\s+me\b[^.?!]{0,50}"
    r"\b(?:ask|think|phrase|formulate|understand|decide|reflect|communicate|"
    r"express|reason|articulate|listen|converse)\b"
    # ES: ayúdame / enséñame / muéstrame cómo ... + a cognitive skill object
    r"|\b(?:ay[úu]dame|ens[eé][ñn]ame|mu[ée]strame\s+c[óo]mo)\b[^.?!]{0,50}"
    r"\b(?:pregunt\w*|pensar|formular|decidir|comunicar|reflexionar|"
    r"expresar|razonar|escuchar|hablar|conversar)\b"
    r")",
    re.IGNORECASE,
)


def _is_conversational_coaching(user_text: str) -> bool:
    """True when the user asks for help getting better at a soft / cognitive /
    conversational skill (asking, thinking, phrasing, deciding, understanding).
    Answered inline, never force-spawned.

    See ``_CONVERSATIONAL_COACHING_RE`` for the full rationale (live bug
    2026-06-19, Turn 1): a coaching request must not be dispatched to a worker
    just because its skill object collides with an action verb ("fragen").
    """
    return bool(_CONVERSATIONAL_COACHING_RE.search(user_text or ""))


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


_CLI_TOOL_PREFIX = "cli_"
# Lines a CLI prints when it wants interactive confirmation — never spoken.
_CLI_PROMPT_NOISE_RE = re.compile(
    r"\(y/n\)|\[y/n\]|would you like to|do you want to continue|press any key",
    re.IGNORECASE,
)


def _extract_cli_error_line(stderr: str) -> str:
    """Pick the most informative, speakable line out of a CLI's stderr.

    Prefers an ``ERROR:``-prefixed line (the actionable cause), strips the
    ``ERROR: (gcloud.x.y)`` command-path prefix, and skips interactive-prompt
    noise such as ``Would you like to enable and retry (y/N)?``. Returns ``""``
    when nothing speakable remains. Pure string work — no LLM (AP-11).
    """
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    speakable = [ln for ln in lines if not _CLI_PROMPT_NOISE_RE.search(ln)]
    error_lines = [ln for ln in speakable if ln.upper().startswith("ERROR")]
    chosen = (error_lines or speakable or [""])[0]
    # Drop a leading "ERROR: (gcloud.billing.budgets.list) " command-path prefix.
    chosen = re.sub(r"^ERROR:\s*(\([^)]*\)\s*)?", "", chosen, flags=re.IGNORECASE)
    return chosen.strip()[:200]


def _cli_failure_reason(output: Any, error: str | None, *, german: bool) -> str:
    """Honest spoken readback for a FAILED ``cli_<name>`` call.

    The user must never hear a bare ``exit 1`` (the CLI tool's ``error`` field)
    nor "Dazu habe ich nichts gefunden". Surface the stderr cause if present,
    else name the exit code. Static, no LLM (AP-11); mirrors
    :func:`jarvis.voice.action_phrases.cu_failure_readback` (live repro
    2026-06-17, ``gcloud billing budgets list`` → exit 1 narrated as "nothing
    found").
    """
    stderr = ""
    exit_code: int | None = None
    if isinstance(output, dict):
        stderr = str(output.get("stderr") or "")
        ec = output.get("exit_code")
        if isinstance(ec, int):
            exit_code = ec
    cause = _extract_cli_error_line(stderr)
    if cause:
        de = f"Der Befehl ist fehlgeschlagen: {cause}"  # i18n-allow: German TTS
        return de if german else f"The command failed: {cause}"
    if exit_code is None and error:
        m = re.search(r"exit\s+(-?\d+)", error)
        if m:
            exit_code = int(m.group(1))
    if exit_code is not None:
        de = f"Der Befehl ist mit Fehlercode {exit_code} fehlgeschlagen."  # i18n-allow: German TTS
        return de if german else f"The command failed with exit code {exit_code}."
    de = "Der Befehl ist fehlgeschlagen."  # i18n-allow: German TTS
    return de if german else "The command failed."


def _evidence_answer_is_unverified(
    required_tool: str, executed: "set[str]", response_text: str, *, suppressed: bool
) -> bool:
    """True when a mandated-tool turn produced an answer WITHOUT calling the tool.

    The evidence gate's ``require_tool`` directive tells the model to answer ONLY
    from the tool's result. If the model returns a non-empty answer but the
    mandated tool never ran (``executed_tool_names``), that answer is necessarily
    unverified — at worst a confabulation (live repro 2026-06-17, session
    296abc82: the model invented "the gcloud tool blocked execution because it
    classified the request as an explanatory question"). Empty answers and
    fire-and-forget ``suppress_response`` turns are handled elsewhere, so they
    are excluded here.
    """
    if not required_tool or suppressed:
        return False
    if required_tool in (executed or set()):
        return False
    return bool((response_text or "").strip())


_EVIDENCE_UNFULFILLED_PHRASES: dict[str, str] = {
    "de": (
        "Ich konnte das gerade nicht abrufen — der Zugriff über das passende "  # i18n-allow: German TTS
        "Werkzeug ist nicht durchgelaufen. Sag noch mal Bescheid, dann "  # i18n-allow: German TTS
        "versuche ich es erneut."  # i18n-allow: German TTS
    ),
    "en": (
        "I couldn't retrieve that just now — the tool call didn't go through. "
        "Say the word and I'll try again."
    ),
    "es": (
        "No pude obtener eso ahora mismo — la llamada a la herramienta no se "
        "completó. Avísame y lo intento de nuevo."
    ),
}


def _evidence_unfulfilled_answer(*, lang: str) -> str:
    """Honest spoken fallback for a mandated-tool turn whose tool never ran.

    Static, no LLM (AP-11). Never claims the tool "blocked" or invents a reason.
    Localized for every supported language (de/en/es); an unrecognised code
    degrades to the default locale so the spoken turn never crashes (Runtime
    Output Language doctrine).
    """
    return _EVIDENCE_UNFULFILLED_PHRASES.get(
        lang, _EVIDENCE_UNFULFILLED_PHRASES[DEFAULT_LOCALE]
    )


def _render_recovered_tool_output(output: Any) -> str:
    """Speakable plain-text rendering of a recovered tool's output.

    Why this exists (live repro 2026-06-14, voice "Was hältst du von exp.com?"):
    a *read* tool such as ``search_web`` returns STRUCTURED data
    (``{"query": …, "results": [{"title", "snippet", …}]}``), not a spoken
    sentence. A properly-invoked tool call re-injects that data for a follow-up
    brain turn that phrases it; the leaked-recovery shortcut
    (:meth:`BrainManager._recover_leaked_tool`) has no such turn, so it used to
    return ``str(result.output)`` — a ``{``-prefixed Python repr. The streaming
    guard :func:`_looks_like_tool_use_leak` then mistook that ANSWER for ANOTHER
    leaked tool_use block, dropped it, and the user heard the canned
    "Ich habe die Aktion erkannt, konnte sie aber nicht ausführen." even though
    the search had succeeded.

    This renders structured output to readable text that never begins with
    ``{``/``[``. An empty return means "nothing speakable" — the caller then
    supplies a localized 'nothing found' fallback (never a repr, never the
    failure phrase).
    """
    if output is None:
        return ""
    if isinstance(output, str):
        s = output.strip()
        return "" if _looks_like_tool_use_leak(s) else s
    if isinstance(output, dict):
        results = output.get("results")
        if isinstance(results, list):
            parts: list[str] = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                snippet = str(item.get("snippet") or "").strip()
                if snippet and title and title.lower() not in snippet.lower():
                    parts.append(f"{title}: {snippet}")
                else:
                    parts.append(snippet or title)
            joined = " ".join(p for p in parts if p).strip()
            return joined[:600]
        # CLI tools (cli_<name>) return {exit_code, stdout, stderr, duration_ms}.
        # This renderer is reached only after the caller verified success, so a
        # non-empty stdout IS the answer — surface it instead of dead-ending in
        # "" (which made the caller speak "Dazu habe ich nichts gefunden" even
        # though gcloud returned real project data; live repro 2026-06-17).
        if "exit_code" in output and ("stdout" in output or "stderr" in output):
            stdout = str(output.get("stdout") or "").strip()
            return stdout[:600] if stdout else ""
        # Other structured tools: surface the first human-readable text field,
        # never the dict repr.
        for key in ("text", "answer", "summary", "message", "content", "result"):
            val = output.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:600]
        return ""
    if isinstance(output, (list, tuple)):
        joined = " ".join(str(x).strip() for x in output if str(x).strip()).strip()
        return "" if _looks_like_tool_use_leak(joined) else joined[:600]
    text = str(output).strip()
    return "" if _looks_like_tool_use_leak(text) else text


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
        "Entschuldige, ich komme gerade nicht an mein Sprachmodell. Einen Moment, bitte.",  # i18n-allow
        (
            "Tut mir leid, mein Sprachmodell ist im Moment nicht erreichbar. "  # i18n-allow
            "Ich versuche es gleich erneut."
        ),
        (
            "Ich kann gerade nicht antworten — die Verbindung zu meinem Modell hakt. "  # i18n-allow
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
        # Two-turn voice/chat confirmation (forensic 2026-06-18): an ask-tier tool
        # on a conversational turn is deferred + spoken-confirmed instead of
        # blocking on a UI approval no voice user can give. Enabled by config,
        # opted into per-turn only by conversational callers (``allow_voice_confirm``).
        self._voice_confirm_enabled: bool = bool(
            getattr(getattr(config, "brain", None), "voice_confirm", True)
        )
        self._pending_voice_confirm: _PendingVoiceConfirm | None = None
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
        # Drag-drop: ad-hoc images attached to ONE upcoming turn, keyed by that
        # turn's trace_id (see jarvis/brain/drop_context.py). Popped + cleared in
        # _collect_vision_images, bypassing the screen-vision gate so a dropped
        # picture reaches the multimodal brain even with screen-vision off.
        self._pending_turn_images: dict[UUID, tuple[ImageBlock, ...]] = {}
        # Drag-drop SILENT context: pictures dropped onto the bar/mascot, parked
        # for the NEXT real turn (a drop never triggers a turn). See
        # add_dropped_context / generate. Dropped TEXT goes into _history.
        self._pending_drop_images: tuple[ImageBlock, ...] = ()
        # B5 Agent C: wiki context injector.  None = no-op (Agent B not merged
        # yet, or [wiki_context].enabled = false).  Set by factory.py for the
        # router tier only; sub-tiers never get wiki injection.
        self._wiki_injector: "WikiContextInjector | None" = wiki_injector
        # Per-turn wiki context suffix; set in generate() and consumed by
        # _build_system_prompt().  Reset to "" after each turn.
        self._wiki_context_suffix: str = ""
        # Per-turn detected language (de/en/es or "" when ambiguous/pinned),
        # set at the top of generate(); consumed by _reply_language_directive()
        # in auto mode to hard-pin the turn's language so a tool-synthesis turn
        # cannot drift back to German (live bug 2026-06-14).
        self._turn_detected_lang: str = ""
        # Sticky conversation language (de/en/es, "" until established). Updated
        # only on a SUBSTANTIVE turn so a thin interjection ("Now", "Stop") never
        # flips an established conversation; consumed by _update_turn_language and
        # exposed to the speech pipeline / deterministic tool readbacks so the
        # whole turn stays in one language (natural-flow forensic 2026-06-18).
        self._conversation_language: str = ""
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
        raw_primary = getattr(config.brain, "primary", None)
        router_cfg = getattr(config.brain, "router", None)
        coerced_primary = _coerce_main_brain_provider(
            raw_primary,
            getattr(router_cfg, "provider", None),
            getattr(config.brain, "deep_brain", None),
        )
        if coerced_primary != (raw_primary or "").strip():
            log.warning(
                "Brain provider %r is subagent-only; using %r as main brain.",
                raw_primary,
                coerced_primary,
            )
            config.brain.primary = coerced_primary
        self._active_name: str = coerced_primary
        # The (provider, model) actually answering the CURRENT turn. Set per
        # fallback-chain attempt in generate() right before the dispatcher is
        # built, consumed by _build_system_prompt to inject the authoritative
        # self-identity line (forensic 2026-06-20: the answering LLM never knew
        # which provider it was, so a provider question got a guessed "Gemini").
        # None outside a turn → no identity block on helper prompt builds.
        self._active_turn_identity: tuple[str, str | None] | None = None
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
        # Heavy-research force-spawn patterns (verb + heaviness-marker), lazily
        # compiled from brain.routing.heavy_research_*. Live bug 2026-06-14.
        self._heavy_research_patterns: tuple[
            re.Pattern[str], re.Pattern[str]
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
          - `brain.deep_brain = tier_cfg.fallback_provider`, UNLESS a
            `provider_override` collapses a non-split tier (fallback in
            {None, provider}) — then deep_brain follows the override so a
            user-chosen frontier provider leads deep/code too (see below).

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
        requested_provider = provider_override or tier_cfg.provider
        effective_provider = _coerce_main_brain_provider(
            requested_provider,
            tier_cfg.provider,
            tier_cfg.fallback_provider,
            getattr(config.brain, "deep_brain", None),
        )
        if effective_provider != (requested_provider or "").strip():
            log.warning(
                "Brain provider %r is subagent-only; using %r as router brain.",
                requested_provider,
                effective_provider,
            )
        local_config.brain.primary = effective_provider
        # deep_brain normally mirrors the tier's fallback provider. But when an
        # explicit override redirected the active provider away from the tier
        # default AND there is no deliberate cross-provider deep split
        # (fallback_provider == provider), the deep brain must FOLLOW the override
        # — otherwise a user-chosen frontier provider (grok/codex) still delegates
        # every deep/code turn to the orphaned tier default. Forensic 2026-06-20:
        # primary=grok left deep_brain=gemini, so reasoning turns ran on Gemini
        # despite the user picking Grok ("Grok for everything" mandate). An
        # explicit split (fallback_provider != provider) is preserved.
        deep_provider = tier_cfg.fallback_provider
        if (
            provider_override
            and effective_provider != tier_cfg.provider
            and (
                # No fallback configured at all (None/"") is even less of a
                # deliberate split than a symmetric one — follow the override
                # rather than strand deep_brain at None for the whole session.
                not tier_cfg.fallback_provider
                or tier_cfg.fallback_provider == tier_cfg.provider
            )
        ):
            deep_provider = effective_provider
        local_config.brain.deep_brain = deep_provider

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
                            "Pre-Boot-Key-Check: codex hat keinen OpenAI-API-Key, "
                            "aber ChatGPT-OAuth ist verbunden -> Provider 'codex' "
                            "bleibt als Brain aktiv (CLI-Pfad)."
                        )
                        continue
                manager._dead_providers.add(provider_name)
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

    @property
    def conversation_language(self) -> str:
        """The sticky language of the conversation so far (de/en/es, or "").

        Read by the speech pipeline and threaded into deterministic tool
        readbacks so a thin interjection ("Now") stays in the running
        conversation's language instead of flipping the whole turn (forensic
        2026-06-18). Empty until a substantive turn establishes it; an explicit
        ``reply_language`` pin overrides it everywhere anyway.
        """
        return self._conversation_language

    def _update_turn_language(self, user_text: str) -> None:
        """Resolve this turn's language and maintain the sticky conversation
        language, applied at the top of ``generate()``.

        Stickiness: a thin interjection ("Now", "Stop") inherits the running
        ``conversation_language`` rather than flipping it; only a substantive
        turn with a clear signal (re)defines the conversation. An explicit pin
        leaves ``_turn_detected_lang`` empty so ``_reply_language_directive``
        uses the pin; genuinely ambiguous text stays ``"unknown"`` so the
        directive keeps its soft "mirror the user" form.
        """
        if self._reply_language in _REPLY_LANG_NAMES:
            self._turn_detected_lang = ""
            return
        if self._conversation_language and not is_substantive_turn(user_text):
            self._turn_detected_lang = self._conversation_language
            return
        detected = detect_text_language(user_text)
        self._turn_detected_lang = detected
        if detected in _REPLY_LANG_NAMES:
            self._conversation_language = detected

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

    def _resolve_turn_lang(self) -> str:
        """The de/en/es key this turn's output is localized to.

        The single authoritative resolver consumed by every ``ResponseGenerated``
        publish (success replies AND the total-failure apology) so the recorded
        transcript language is consistent and never the binary ``_looks_german``
        gate — which silently tags any non-German reply "en" and so drops Spanish
        (Runtime Output Language doctrine). An explicit pin wins; in auto mode it
        is THIS turn's detected language (set at the top of generate()); an
        undetected/ambiguous turn keeps the German default.
        """
        lang = self._reply_language
        if lang not in _REPLY_LANG_NAMES:
            lang = getattr(self, "_turn_detected_lang", "") or lang
        return lang if lang in _REPLY_LANG_NAMES else "de"

    def _next_provider_down_phrase(self) -> str:
        """Localized 'I can't reach my model' apology + advance the rotation.

        Spoken when the whole provider chain fails. Provider-agnostic and
        voice-safe (no names/URLs) — the actionable diagnostic is logged, never
        spoken (live complaint 2026-06-01: the grok/Anthropic billing message
        was read aloud while Gemini was the active provider).
        """
        phrase = _provider_down_phrase(
            self._resolve_turn_lang(), self._provider_down_idx
        )
        self._provider_down_idx += 1
        return phrase

    async def _provider_down_reply(self, trace_uuid: UUID) -> str:
        """Total-failure apology, ALSO surfaced to the transcript.

        Returns the localized provider-down phrase AND publishes it as a
        ``ResponseGenerated`` event so the SessionRecorder records it as the
        turn's ``jarvis_text`` (``recorder.py::_on_response_generated``). Without
        this the recorded turn keeps an empty reply and the voice transcript
        shows the user line with no answer, even though the user heard the
        apology aloud (live forensic 2026-06-20, session 09eef351). The apology
        is deliberately NOT appended to the conversation history — an "I can't
        reach my model" line must not pollute the LLM context for later turns.
        """
        phrase = self._next_provider_down_phrase()
        # _next_provider_down_phrase already localized the phrase via
        # _resolve_turn_lang; resolving again here is deterministic (same pin /
        # detected-language inputs, the rotation index does not affect language)
        # and only tags the transcript's jarvis_lang — NEVER _looks_german, which
        # would mislabel a Spanish apology as English (Runtime Output Language).
        await self._bus.publish(ResponseGenerated(
            trace_id=trace_uuid,
            text=phrase,
            language=self._resolve_turn_lang(),
        ))
        return phrase

    def _mandatory_lang_directive(self, name: str) -> str:
        """The hard MANDATORY reply-language pin for a named language.

        Shared by an explicit ``brain.reply_language`` pin and the auto-mode
        per-turn pin (``_turn_detected_lang``) so both carry identical, strong
        wording that survives tool-synthesis.
        """
        return (
            f"REPLY LANGUAGE — MANDATORY: Always reply in {name}, no matter "
            f"which language the user writes or speaks in. This overrides any "
            f"other language cue anywhere in this prompt. Keep proper nouns, "
            f"brand / product / company names and technical identifiers "
            f"(e.g. 'Anthropic', 'GitHub', file paths, code, commands) "
            f"unchanged in their original form — never translate them. Keep the "
            f"reply natural and fluent in {name}."
        )

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
            return self._mandatory_lang_directive(name)
        # auto mode: when THIS turn's language is confidently detected, pin it
        # HARD with the same MANDATORY wording as an explicit pin. A soft
        # "please mirror" line let the model anchor to German on clean English
        # input — most visibly on tool-synthesis turns, where the English
        # question is far from the generation point and the German-heavy prompt
        # wins (live bug 2026-06-14: an English weather turn answered in German).
        # ``_turn_detected_lang`` is set per turn by generate(); ambiguous text
        # detects as "unknown" (not in _REPLY_LANG_NAMES) and falls through to
        # the soft mirror. The pin only changes when the user's language
        # changes, so the cached system prefix stays stable within a
        # single-language conversation.
        turn_name = _REPLY_LANG_NAMES.get(getattr(self, "_turn_detected_lang", ""))
        if turn_name is not None:
            return self._mandatory_lang_directive(turn_name)
        return (
            "REPLY LANGUAGE: Reply in the SAME language as the user's latest "
            "message — detect it fresh each turn and mirror it: English in "
            "English, German in German, Spanish in Spanish. Do NOT default to "
            "German just because the rest of this prompt is German; the user's "
            "language always wins. Keep proper nouns, brand / product names and "
            "technical identifiers in their original form — never translate them."
        )

    def _action_failed_phrase(self, user_text: str) -> str:
        """Localized leak-recovery fallback (live bug 2026-06-10 23:12).

        Spoken when a provider leaked a tool_use block as text and the
        recovery produced no speakable final. Was a hardcoded German string —
        an English turn ("What's weather like tomorrow?") was answered in
        German. A pinned reply language wins; ``auto`` mirrors the user's
        text; ambiguous text keeps the historical German default.

        ``generate()`` only ever receives ``user_text`` (the pipeline resolves
        the STT tag separately), so auto-mode detection is text-only — hence
        ``"unknown"`` as the tag. See ``tool_use_loop._localized_phrase`` for
        the same contract.
        """
        lang = self._reply_language
        if lang not in _ACTION_FAILED_PHRASES:
            lang = resolve_turn_language("unknown", user_text, default="de")
        return _ACTION_FAILED_PHRASES.get(lang, _ACTION_FAILED_PHRASES["de"])

    def _direct_ack_language(self, user_text: str) -> str:
        """Resolve ``de``/``en``/``es`` for a DIRECT fast-path acknowledgement.

        Mirrors :meth:`_action_failed_phrase`: an explicit ``reply_language``
        pin (the desktop "Languages" view) wins; otherwise the turn's language
        is detected from the text; ambiguous text keeps the historical German
        default. The DIRECT path runs off the LLM, so this is the only place the
        turn language can be applied to the spoken acknowledgement.
        """
        lang = self._reply_language
        if lang in _OPEN_APP_ACK_PREFIX:
            return lang
        return resolve_turn_language("unknown", user_text, default="de")

    def _localize_direct_ack(
        self, call: LocalToolCall, raw_output: str, lang: str
    ) -> str:
        """Localize a deterministic DIRECT-path acknowledgement.

        The DIRECT local-action path surfaces the tool ``output`` VERBATIM to
        the user (no LLM re-render), so a tool's hardcoded German success string
        would otherwise reach an English/Spanish speaker untranslated (live bug
        2026-06-15: an English "open my explorer" turn was acknowledged in
        German). ``open_app`` is the one fast-path tool whose success output
        is a spoken acknowledgement; translate only its leading German verb and
        keep the suffix (the actual app / URL it reported). Any non-matching
        output — a future tool, a test stand-in — passes through unchanged.
        """
        if call.name != "open_app" or lang == "de":
            return raw_output
        de_prefix = _OPEN_APP_ACK_PREFIX["de"]
        target_prefix = _OPEN_APP_ACK_PREFIX.get(lang)
        if target_prefix is not None and raw_output.startswith(de_prefix):
            return target_prefix + raw_output[len(de_prefix):]
        return raw_output

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

        # Configurable assistant identity. Derived solely from the wake phrase
        # (so a custom wake word "Micron" makes the assistant call itself
        # Micron). When the name is neither the neutral fallback nor the
        # historical "Jarvis" baseline, a prominent identity directive overrides
        # the "Jarvis" mentions baked into the persona files (SOUL.md /
        # JARVIS_PERSONA.md), which are static and cannot be parameterised.
        # Placed first so it frames everything.
        name = resolve_assistant_name(getattr(self, "_config", None))
        if name not in (DEFAULT_ASSISTANT_NAME, PERSONA_BASELINE_NAME):
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
        # The "effective" loader returns the user's custom system prompt when one
        # is set in Settings (data/custom_system_prompt.md), else the packaged
        # default. Read fresh each turn, so an edit/reset applies on the next turn
        # without a restart. Empty string when nothing is available — no crash.
        persona_block = load_effective_persona_prompt()
        if persona_block:
            parts.append(persona_block)

        # User's own standing-instructions file (AGENTS.md / CLAUDE.md equivalent),
        # named after the assistant (e.g. Alex.md). Distinct from the persona: the
        # user writes personal preferences here, and the block is framed so they
        # refine behaviour but never override safety/confirmations. Read fresh each
        # turn -> an edit applies on the next turn, no restart. A read fault must
        # never break the prompt build.
        try:
            from jarvis.brain import agent_instructions as _agent_instructions

            prefs_block = _agent_instructions.render_for_prompt(getattr(self, "_config", None))
            if prefs_block:
                parts.append(prefs_block)
        except Exception:  # noqa: BLE001
            pass

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
                "CONTACTS: When the user names a person to send them an email or "
                "message ('write an email to Christoph'), call `contact-lookup` "
                "first to resolve the name to the stored email, then send with "
                "`gmail`. Never invent an address — if contact-lookup finds "
                "nothing, say so."
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
            elif not self._skills_omit_warned:
                # AD-S6: silently omitting the section was RC2 of "Jarvis
                # never calls a skill" — warn once per manager lifetime.
                self._skills_omit_warned = True
                log.warning(
                    "skills section omitted: skill context not initialized"
                )
        except Exception:  # noqa: BLE001
            if not self._skills_omit_warned:
                self._skills_omit_warned = True
                log.warning(
                    "skills section omitted: renderer failed", exc_info=True
                )

        # CLI first-class capabilities (design 2026-06-10, §5.3): list the
        # connected CLIs so the brain can pick them for matching requests.
        # Mirrors the skills section above. Rendered from the shared registry
        # published by the UI server; absent registry → section omitted.
        try:
            from jarvis.clis.prompt_section import render_connected_clis_section
            from jarvis.clis.shared import get_active_registry

            _cli_reg = get_active_registry()
            if _cli_reg is not None:
                _cli_section = render_connected_clis_section(_cli_reg)
                if _cli_section:
                    parts.append(_cli_section)
        except Exception:  # noqa: BLE001
            log.debug("connected-CLIs section omitted", exc_info=True)

        # Evidence gate directive (per-turn, AD-CLI8): forces a tool call
        # before any answer about an external-data domain. Empty on normal
        # turns; set by generate() when the gate returns require_tool.
        if self._evidence_directive:
            parts.append(self._evidence_directive)

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

        # Active-model self-awareness: tell THIS turn's actually-answering
        # provider/model who it is, so a "which model are you?" question gets an
        # honest answer instead of a guessed "Gemini" (forensic 2026-06-20: Grok
        # was live and answering yet claimed to be Gemini). Set per fallback-chain
        # attempt in generate(), where the real prov_name/model are known; absent
        # on non-turn prompt builds (compression / wiki-delta base) → no block.
        # Placed late for high recency so it overrides the persona's "never
        # discuss your technical nature" line; provider-stable across same-provider
        # turns, so it stays prompt-cache-friendly. On a fallback the block's
        # provider label changes between attempts within the turn, invalidating
        # the prefix cache for the second attempt — acceptable, since a fallback
        # is already a slow path (and matches the pre-existing per-turn mutable
        # flags). getattr: tolerates __new__-constructed test managers that bypass
        # __init__ (the attr is always set in __init__ for the production path).
        identity = getattr(self, "_active_turn_identity", None)
        if identity:
            parts.append(
                _provider_identity_directive(identity[0], identity[1], name)
            )

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
            # Keep deep_brain following the active provider on a runtime switch
            # when there is no explicit cross-provider deep split (deep_brain
            # tracked the previous active, or was never configured) — so switching
            # to a frontier provider leads ALL intents, not just fast ones (mirror
            # of the from_tier_config override rule; "Grok for everything" mandate
            # 2026-06-20). A None/"" deep_brain must follow too, not stay stranded.
            if not self._config.brain.deep_brain or self._config.brain.deep_brain == previous:
                self._config.brain.deep_brain = canonical
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

    def apply_provider_model(self, provider: str, model: str) -> bool:
        """Live-apply a model override for a brain provider (no restart).

        The model picker in the API-Keys view persists the choice to jarvis.toml
        AND calls this so the running brain uses the new model on the next turn.
        The manager builds its config independently of ``app.state.config``, so
        mutating that route-level config would NOT reach the brain — this method
        updates the manager's OWN ``self._config`` and drops cached brain
        instances for the provider so the next ``_get_brain`` rebuilds with the
        new model.

        An empty string resets the override to ``None`` (the provider then falls
        back to its frontier default via ``_fast_model``).

        Returns ``True`` iff ``provider`` is the currently active brain — i.e.
        the change takes effect immediately. For an inactive provider the
        override is stored and applies as soon as the user switches to it.
        """
        from jarvis.core.config import BrainProviderConfig

        canonical = PROVIDER_ALIASES.get(provider.lower().strip(), provider)
        new_model = model.strip() or None
        providers = self._config.brain.providers
        pc = providers.get(canonical)
        if pc is None:
            providers[canonical] = BrainProviderConfig(model=new_model)
        else:
            try:
                pc.model = new_model
            except Exception:  # noqa: BLE001 — frozen/validation: rebuild the block.
                data = pc.model_dump() if hasattr(pc, "model_dump") else {}
                data["model"] = new_model
                providers[canonical] = BrainProviderConfig(**data)

        # Drop cached instances for this provider so the new model is used; lift
        # any session-level deactivation (mirrors ``reactivate_provider``).
        for key in [k for k in self._brain_cache if k[0] == canonical]:
            self._brain_cache.pop(key, None)
        self._dead_providers.discard(canonical)
        return canonical == self._active_name

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

    def _get_heavy_research_patterns(
        self,
    ) -> tuple[re.Pattern[str], re.Pattern[str]]:
        """Lazily compile the (verb, heaviness-marker) regexes for heavy-research
        force-spawn from BrainRoutingConfig. Verbs use ``\\b<stem>\\w*\\b`` so
        conjugations match; markers are word/phrase boundaries."""
        if self._heavy_research_patterns is None:
            cfg = self._config.brain.routing
            verbs = list(getattr(cfg, "heavy_research_verbs", []) or [])
            markers = list(getattr(cfg, "heavy_research_markers", []) or [])
            self._heavy_research_patterns = (
                _build_verb_pattern(verbs),
                _build_marker_pattern(markers),
            )
        return self._heavy_research_patterns

    def _is_heavy_research(self, user_text: str) -> bool:
        """True iff the utterance is HEAVY multi-step research/analysis that must
        be OFFLOADED to a background mission, not answered inline on the deep
        brain (where it blows the ~20 s voice budget and is beheaded — live bug
        2026-06-14, the Berlin→Melbourne turn).

        Conjunctive gate (precision over recall): a research/analysis VERB must
        be present AND a heaviness signal — a horizon/multi-step/requirements
        marker, OR >= ``heavy_research_min_verbs_multiclause`` verb matches
        (multi-clause), OR length >= ``heavy_research_min_chars`` with a verb.
        Length alone never spawns, so a quick "recherchier das mal kurz" stays
        inline. Pure regex (AP-11 safe, cross-platform). The caller
        (``_should_force_spawn``, strict mode) runs this AFTER every stand-down
        guard, so skills / open-app / instructional / nav / pointer keep
        precedence.
        """
        cfg = self._config.brain.routing
        if not getattr(cfg, "heavy_research_enabled", True):
            return False
        t = (user_text or "").strip()
        if not t:
            return False
        verb_re, marker_re = self._get_heavy_research_patterns()
        verbs_found = verb_re.findall(t)
        if not verbs_found:
            return False  # (A) no research/analysis verb → never heavy research
        min_verbs = max(
            2, int(getattr(cfg, "heavy_research_min_verbs_multiclause", 2))
        )
        if len(verbs_found) >= min_verbs:
            return True  # multi-clause "recherchier X und analysier Y"
        if marker_re.search(t):
            return True  # verb + horizon / multi-step / requirements marker
        min_chars = int(getattr(cfg, "heavy_research_min_chars", 120))
        return len(t) >= min_chars  # verb + sheer length

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
            # delegated natively instead of refused with "kann ich noch nicht"  # i18n-allow
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

    def _run_evidence_gate(self, user_text: str) -> "EvidenceVerdict":
        """Defensive wrapper around ``check_evidence_domain`` (AD-CLI4..8).

        Any infrastructure fault (missing config field, no shared CLI
        registry, capabilities module error) degrades to PASS — the gate adds
        behaviour, it must never block the voice path.
        """
        from jarvis.brain.evidence_gate import EvidenceVerdict, check_evidence_domain

        try:
            cfg = self._config.brain.evidence_domains
            if not cfg.enabled:
                return EvidenceVerdict(kind="pass")
            from jarvis.clis.capability_provider import (
                connected_domain_tool_map,
                merged_evidence_domains,
                refusal_hint,
            )
            from jarvis.clis.shared import get_active_registry
            from jarvis.core.capabilities import get_registry

            cli_reg = get_active_registry()
            domain_map = dict(
                connected_domain_tool_map(cli_reg) if cli_reg is not None else {}
            )
            # The "activity" (screen / window-history) domain is served by the
            # always-on internal awareness-recall tool, not a connected CLI, so
            # wire it into the domain→tool map here. Without a mandated tool the
            # fast brain confabulates "der lokale Verlaufsspeicher ist nicht
            # verfügbar" without ever calling awareness-recall (live 2026-06-18,
            # proven from the log). Guarded on the tool actually being
            # registered so a deployment without awareness degrades to the
            # gate's honest refusal, never a mandate for a missing tool.
            if "awareness-recall" in (getattr(self, "_tools", None) or {}):
                domain_map.setdefault("activity", "awareness-recall")

            def _hint(domain: str, lang: str) -> str:
                if cli_reg is None:
                    return ""
                return refusal_hint(domain, cli_reg, lang)

            return check_evidence_domain(
                user_text,
                enabled=cfg.enabled,
                domains=merged_evidence_domains(cli_reg, cfg.domains)
                if cli_reg is not None
                else cfg.domains,
                capability_registry=get_registry(),
                domain_tool_map=domain_map,
                refusal_hint_fn=_hint,
            )
        except Exception:  # noqa: BLE001
            log.debug("evidence gate degraded to PASS", exc_info=True)
            return EvidenceVerdict(kind="pass")

    async def _prefetch_activity_block(
        self, tool_name: str, user_text: str, *, trace_id: Any = None,
    ) -> str:
        """Deterministically run the safe, read-only awareness-recall tool.

        The evidence gate's ``activity`` domain mandates ``awareness-recall``,
        but the fast brain does not reliably call a soft-mandated tool (live
        2026-06-18). Rather than depend on the model, the manager runs the tool
        itself and injects the rendered timeline as answer-context. Goes through
        the ``ToolExecutor`` (never a direct ``Tool.execute`` — AP-3) so the
        risk-tier/audit path is honoured. Returns the rendered output, or ``""``
        when the tool is missing / errors / yields nothing (the caller then
        keeps the soft mandate so the honest fallback fires, never a
        confabulation).
        """
        tool = (self._tools or {}).get(tool_name)
        if tool is None or self._tool_executor is None:
            log.warning(
                "activity pre-fetch skipped: tool=%r present=%s executor=%s",
                tool_name, tool is not None, self._tool_executor is not None,
            )
            return ""
        try:
            res = await self._tool_executor.execute(
                tool,
                {"query": user_text, "since_minutes": 1440},
                user_utterance=user_text,
                trace_id=trace_id,
            )
        except Exception:  # noqa: BLE001 — pre-fetch is best-effort, never fatal
            log.warning("activity pre-fetch raised", exc_info=True)
            return ""
        ok = bool(getattr(res, "success", False))
        out = str(getattr(res, "output", "") or "").strip()
        log.info(
            "activity pre-fetch result: success=%s out_len=%d err=%r",
            ok, len(out), getattr(res, "error", None),
        )
        if ok:
            return out
        return ""

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
        18:19:07): the user said "Hallo, öffne ihn für mich". The allowlist i18n-allow
        substring-matched the leading "Hallo", the turn was treated as
        smalltalk, the action tools were hidden, and the brain spoke the
        anti-silence refusal "Das kann ich gerade nicht ausführen — mir fehlt i18n-allow
        dafür das passende Werkzeug." A greeting/politeness prefix in front of a i18n-allow
        REAL command is NOT smalltalk: strip the leading greeting run and, if
        what remains is itself a non-smalltalk action request, classify the turn
        as a command (return False) so the tools stay visible and force-spawn
        can fire. Standalone smalltalk ("Hallo", "Hallo, wie geht's?") and
        greeting-less chit-chat ("was machst du") are unaffected.

        2026-06-10 23:13 recurrence (same log): "Hey, what's the weather like
        today?" — the original guard additionally required an ACTION verb in
        the remainder, so a greeting-prefixed information QUESTION stayed
        smalltalk, search_web was hidden, and the brain refused with the
        anti-silence fallback. The greeting prefix must never change the
        classification of the remainder: a non-smalltalk remainder keeps the
        turn a real request, action verb or not (exactly as the same words
        without the greeting would classify).
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
        ):
            return False
        # Smalltalk-head/tail guard (live bug 2026-06-19, the Bill-Gates turn):
        # a continuation-recombine glued the answered "Was geht ab?" turn onto a
        # real command ("… mach mir den ältesten Bill-Gates-Post auf"). The
        # allowlist matched the chit-chat part, so the WHOLE turn was demoted to
        # a tool-less smalltalk turn — computer_use/spawn hidden, the deep brain
        # spoke the no-op "Notiert …" and never opened the browser. When the
        # utterance ALSO carries a clear action/request signal it is a COMMAND,
        # not chit-chat: keep the action tools visible. See _ACTION_REQUEST_RE.
        if _ACTION_REQUEST_RE.search(t):
            return False
        return True

    # Read-only tools that stay visible even on a smalltalk turn. The toolless
    # smalltalk path (2026-05-01) exists to stop the LLM hallucinating a
    # spawn_worker on chit-chat — that risk is the spawn/action tools, NOT the
    # read-only screenshot tool. Keeping `screenshot` here lets the brain look
    # at the screen on demand (Wave 2) even on a greeting-prefixed turn, e.g.
    # "Hallo, lies mir vor was oben links steht" (live failure 2026-05-31).
    # NOTE: `_gate_screen_tool` runs AFTER this override and removes `screenshot`
    # again unless the utterance carries a visual-reference marker (2026-06-14
    # screen-narration guard) — the 2026-05-31 case survives because "lies" /
    # "oben links" / "steht" are markers, so it still reaches the tool.
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
    # AD-S6: warn exactly once per manager lifetime when the AVAILABLE
    # SKILLS section cannot be rendered (RC2 used to be silent).
    _skills_omit_warned: bool = False
    # Evidence gate (CLI first-class capabilities, 2026-06-10): per-turn
    # mandatory-tool directive + the tool that must stay visible even on a
    # smalltalk-classified turn ("was steht heute an" matches the smalltalk
    # allowlist forms). Reset at the start of every generate() turn.
    _evidence_directive: str = ""
    _evidence_required_tool: str = ""

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
        if self._skill_is_blocked(skill):
            log.info("noted skill %r is block-tier — ignored", skill_name)
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
            if res is None:
                return None
            skill = res[0]
            if self._skill_is_blocked(skill):
                log.info(
                    "skill %s matched but is block-tier — turn not captured",
                    getattr(skill, "name", "?"),
                )
                return None
            return skill
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _skill_is_blocked(skill: Any) -> bool:
        """True for risk_policy block-tier skills — they must never capture a
        turn (mirrors the run-skill tool's block gate)."""
        fm = getattr(skill, "frontmatter", None)
        if fm is None:
            return True
        try:
            return fm.risk_policy.default_tier == "block"
        except Exception:  # noqa: BLE001
            return False

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
        if self._evidence_required_tool:
            # "was steht heute an" can classify as smalltalk; the mandated
            # evidence tool must stay visible or the directive is
            # unfulfillable (AD-CLI8).
            allowed = allowed | {self._evidence_required_tool}
        return {
            n: t for n, t in self._tools.items()
            if n in allowed
        }

    def _gate_screen_tool(
        self,
        tools: dict[str, "Tool"],
        *,
        user_text: str,
        has_image: bool,
        pointing_turn: bool = False,
    ) -> dict[str, "Tool"]:
        """Drop the on-demand ``screenshot`` tool on a turn that is not about the screen.

        The validation the screen-narration bug needed (live 2026-06-14): a
        small-talk / knowledge / cut-off fragment with no screen reference
        ("Kannst du mir sagen, was genau...") must not be able to invoke the
        screenshot function and then narrate the screen. Confirm the utterance
        is actually screen-related BEFORE offering the tool.

        The tool stays available when an image is already attached, on a pointer
        turn (which is by definition about the screen), or when the utterance
        carries a visual-reference marker — the same ``should_attach_screenshot``
        signal that gates passive image attach, so the marker-bearing screen
        questions of 2026-05-31 ("lies mir vor was oben links steht") keep it.
        Tradeoff: a genuinely screen-related question that matches no marker
        loses the auto-screenshot fallback; the prompt then steers the brain to
        say it cannot see the screen or ask, rather than fabricate one.
        """
        if not isinstance(tools, dict) or "screenshot" not in tools:
            return tools
        if pointing_turn or has_image:
            return tools
        from jarvis.brain.vision_gate import should_attach_screenshot

        if should_attach_screenshot(user_text):
            return tools
        return {n: t for n, t in tools.items() if n != "screenshot"}

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

    def _suppress_plugins_covered_by_cli(
        self, tools: dict[str, "Tool"]
    ) -> dict[str, "Tool"]:
        """Hide plugin/native tools whose CLI counterpart is connected (req 4).

        A CLI runs a local subprocess and is cheaper than a plugin's MCP/API
        hop, so when a CLI for a service is active its plugin is removed from the
        turn's tool surface (fallback only). Defensive: returns the tools
        unchanged on any fault (never blind the brain on the voice path).
        """
        try:
            from jarvis.clis.capability_provider import (
                suppress_plugin_tools_covered_by_cli,
            )

            return suppress_plugin_tools_covered_by_cli(tools)
        except Exception:  # noqa: BLE001
            log.debug("plugin-CLI suppression failed; full tool set", exc_info=True)
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
        # User mandate (2026-06-15): an EXPLICIT heavy-work trigger ("subagent",
        # "spawn", "openclaw", …) outranks this deterministic "dumb" navigation
        # fast-path — exactly as it outranks the skill guard (AD-S9). A nav-tail
        # combo like "Spawne einen Subagenten UND zeig mir die Socials" names the
        # execution vehicle, so it must reach force-spawn rather than merely
        # switch the sidebar section. Stand down and let the normal path spawn.
        if self._get_force_spawn_pattern().search(user_text):
            log.info(
                "navigation fast-path stands down — explicit heavy-work trigger "
                "in the utterance wins (mission, not a sidebar switch)."
            )
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
        is_de = bool(re.search(r"[äöüÄÖÜß]", user_text)) or bool(  # i18n-allow
            re.search(r"\b(zeig\w*|öffne|oeffne|geh\w*|wechs\w*|spring\w*)\b", user_text, re.I)  # i18n-allow
        )
        return f"Öffne {label}." if is_de else f"Opening {label}."  # i18n-allow

    def _should_force_spawn(
        self, user_text: str, *, source_layer: str | None = None
    ) -> bool:
        """Deterministic spawn guard for action requests.

        Wave-4 migration: previously ``_should_force_sub_jarvis`` with
        ``spawn_sub_jarvis`` tool lookup. The Sub-Jarvis tier was replaced by
        the OpenClaw bridge — see docs/openclaw-bridge.md §11.

        Order (the real evaluation sequence — keep this in sync with the body):
          0. Conversational source (drag-dropped mission recap) → False.
          1. Fatal preconditions, in order → False: empty text; no
             spawn_worker tool/executor; Whisper-FP sentinel; < 6 chars; no
             viable heavy-worker provider. These run FIRST — a spawn is then
             impossible or the transcript is noise.
          2. Explicit spawn DECLINE (``_is_spawn_decline``) → False. **MUST
             precede step 3** — the user negated the very trigger word the hoist
             matches; checking it after the hoist would force-spawn the OPPOSITE
             of "don't spawn a subagent" (live bug 2026-06-19, Turn 2).
          3. Explicit heavy-work trigger (``force_spawn_phrases``) → True. The
             user named the vehicle (AD-S9 / 2026-06-15 mandate); wins over
             every AMBIGUOUS-spawn disambiguation guard below.
          4. Disambiguation stand-downs → False: instructional question;
             opinion/advice question; conversational coaching
             (``_is_conversational_coaching``); pointer; navigation; smalltalk;
             open-app; installed skill; connected-CLI capability; PC control.
          5. Strict mode (default): heavy research → artifact gate; else
             generic sub-agent work (``has_action_intent`` & no capability) →
             True. Permissive mode: action verb / external marker → True.
          6. Otherwise → False.
        """
        # A drag-dropped mission recap is a CONVERSATION about a FINISHED job,
        # never new work — answer it inline regardless of what the quoted card
        # text contains (doom-loop fixed 2026-06-16; see
        # ``_NON_SPAWN_SOURCE_LAYERS``). Checked first so a leaked spawn trigger
        # in the verbatim title cannot reach the explicit-trigger hoist below.
        if source_layer is not None and source_layer in _NON_SPAWN_SOURCE_LAYERS:
            return False
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
        # Explicit spawn DECLINE wins over EVERYTHING below, including the
        # negation-blind explicit-trigger hoist: when the user literally says
        # "don't spawn a subagent" / "talk to me directly", the trigger word
        # ("Subagent"/"spawn") they negated must NOT be read as a request. This
        # is checked BEFORE the hoist precisely because the hoist substring-
        # matches that same word and would force-spawn the opposite of the
        # user's intent. Live bug 2026-06-19 (voice session 18:41, Turn 2).
        if _is_spawn_decline(t):
            log.info("force-spawn skipped: explicit spawn decline — answer inline")
            return False
        # User mandate (2026-06-15, "when I say subagent it MUST spawn"): an
        # EXPLICIT heavy-work trigger that NAMES the execution vehicle
        # ("subagent", "spawn", "openclaw", "delegate") is an UNAMBIGUOUS request
        # to dispatch a worker, so it is checked FIRST — ahead of every
        # disambiguation guard below (instructional / pointer / navigation /
        # smalltalk / open-app / skill). Those guards exist only to suppress
        # AMBIGUOUS, implicit spawns; they must never veto a request in which the
        # user literally named the vehicle. Before this hoist, an explicit
        # "Starte OpenClaw" / "Spawne einen Subagenten und zeig …" was swallowed
        # by the open-app / navigation guard and never spawned ("sometimes saying
        # subagent doesn't spawn a subagent"). The fatal preconditions above (no
        # tool/executor, Whisper-FP seed, min length, worker not viable) still
        # win — they mean a spawn is impossible or the transcript is noise.
        #
        # A DEPTH marker ("deep dive", "gründlich", "umfassend", …) is NOT a
        # vehicle name — it describes thoroughness and OVERLAPS with computer-use
        # requests. It still force-spawns on its own ("Mach einen Deep Dive in
        # meine Google Cloud Kosten") BUT it must NOT override an explicit
        # on-screen / computer / browser request: "Mach einen Deep Dive mit
        # Computer Use in meinem Chrome Browser …" is a Computer-Use turn, not a
        # background mission. When the depth marker co-occurs with a pc-control
        # signal we hand the computer-use-vs-spawn decision to the LLM router (it
        # owns computer_use + the SYSTEM_PROMPT rule "Bildschirm/Browser bedienen
        # ist computer_use, kein spawn_worker") instead of letting the keyword
        # decide. This reuses the existing pc-control detector — no new
        # signal-word list, no widening of force_spawn_phrases.
        _trigger = self._get_force_spawn_pattern().search(t)
        if _trigger is not None:
            if _trigger_names_vehicle(_trigger.group(0)):
                return True
            if not _looks_like_pc_control(t):
                return True  # depth marker, no screen signal → heavy background work
            log.info(
                "force-spawn deferred to LLM: depth trigger %r + computer-use "
                "request — router decides computer_use vs spawn",
                _trigger.group(0),
            )
            return False
        verb_re, marker_re, _smalltalk_re = self._get_routing_patterns()
        if _is_instructional_question(t):
            return False
        # Opinion / advice / recommendation / decision questions, and casual
        # question-openers, are CONVERSATION — the brain answers them inline,
        # never a heavy-worker spawn. Guards the verb-collision false positive
        # where an everyday word ("Frage" -> "frag", the filler "halt") trips
        # has_action_intent and pushes a pure chat turn into
        # _is_generic_subagent_work. Live bug 2026-06-19 (emigration turn). The
        # explicit heavy-work trigger hoisted above still wins, so "spawn a
        # subagent and tell me what you'd recommend" dispatches as asked.
        if _is_opinion_advice_question(t):
            log.info(
                "force-spawn skipped: opinion/advice/conversational question — inline"
            )
            return False
        # Conversational coaching ("hilf mir, intelligent zu fragen / klarer zu
        # denken") is talk, not work — the brain answers inline and asks the
        # user smart questions back. Guards the same verb-collision class as the
        # opinion guard above: the coaching OBJECT is itself an action verb
        # ("fragen" -> "frag"/"frage") that trips has_action_intent ->
        # _is_generic_subagent_work. Live bug 2026-06-19 (voice session 18:41,
        # Turn 1). The explicit heavy-work trigger hoisted above still wins.
        if _is_conversational_coaching(t):
            log.info(
                "force-spawn skipped: conversational coaching request — inline"
            )
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
        # ("Hallo, öffne ...") must NOT block the spawn of the real command that  # i18n-allow
        # follows it. _is_smalltalk strips the greeting and re-evaluates.
        if self._is_smalltalk(t):
            return False
        # Opening / launching an app is ALWAYS a computer-use task — a sub-agent
        # worker runs in an isolated git worktree and has no desktop. The
        # deterministic match_local_action path routes these to computer-use
        # first; this guard is defense-in-depth so a conjugated open verb
        # ("öffnest") can never fall through to a force-spawn (live bug
        # 2026-06-08: "Ich möchte, dass du mir Hermes Agent öffnest, also …").
        # BUT a genuine build-a-deliverable request ("build me a website",
        # "generate a landing page for the product launch") must NOT be vetoed by
        # an is_open_app_intent false positive (it trips on "launch" / English
        # phrasings) — building a file/site/app is a mission, not opening an app.
        # _research_wants_artifact requires a build VERB, so a real "open X"
        # command (no build verb) still stands down to computer-use here.
        if is_open_app_intent(t) and not self._research_wants_artifact(t):
            return False
        # NOTE: the EXPLICIT heavy-work trigger check (AD-S9, 2026-06-10) was
        # hoisted to the top of this method (above every disambiguation guard)
        # per the 2026-06-15 user mandate — see the comment there. It used to sit
        # here, between the open-app guard and the skill guard, which let the
        # open-app / navigation guards veto an explicit "Starte OpenClaw" /
        # "Spawne … und zeig …" before the trigger was ever evaluated.
        # Skill-aware guard (AD-S3, 2026-06-09 rebuild): an utterance that
        # matches an installed, active skill is the skill's turn — never a
        # heavy-worker spawn. generate() sets _skill_turn_match early; the
        # direct probe is defense-in-depth for callers outside generate().
        if self._skill_turn_match is not None or self._match_skill_for_turn(t) is not None:
            log.info("force-spawn skipped: utterance matches an installed skill")
            return False
        # A connected CLI's capability already covers this intent → prefer its
        # cli_<name> tool, never a Computer-Use spawn (the CLI does it headless,
        # no browser login). Mirrors the skill guard above for the CLI surface
        # that capability_provider.sync_registry registers on connect. The
        # explicit heavy-work trigger (hoisted to the top of this method) still
        # wins, so the user can force a worker with "spawn"/"deep dive".
        try:
            from jarvis.core.capabilities import get_registry  # noqa: PLC0415

            _cap = get_registry().resolve_intent(t)
            if _cap is not None and _cap.source == "cli":
                log.info(
                    "force-spawn skipped: connected CLI %s covers the intent", _cap.id
                )
                return False
        except Exception:  # noqa: BLE001 — capability lookup must never break routing
            pass
        # A pc-control request (incl. an explicit "am Bildschirm / on screen")
        # is computer-use, not a sub-agent — stand down. BUT a build-a-deliverable
        # request that merely mentions the screen ("bau mir eine Website und zeig
        # sie am Bildschirm") must still spawn the mission, so the artifact build
        # wins over this stand-down (mirrors the open-app guard above).
        if (
            "dispatch_to_harness" in self._tools
            and _looks_like_pc_control(t)
            and not self._research_wants_artifact(t)
        ):
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
            # Explicit trigger phrases already returned True above (AD-S9
            # moved that check ahead of the skill guard for every mode).
            # 2026-06-01: the sub-agent is the universal capability for generic
            # work. The capability gate no longer refuses such tasks, so spawn
            # them natively here — the user must NOT have to say "Subagent". A
            # request the registry recognises as an action that no capability
            # resolves AND that needs no SPECIFIC external integration
            # (mail/calendar/Spotify/social/delivery) is generic sub-agent work.
            # Live forensic 2026-06-01: a sub-agent task was refused, then only
            # spawned once the user said "Subagent" explicitly.
            # Heavy research routing (Option A, 2026-06-15): a research request
            # whose deliverable is an ANSWER (comparison / overview /
            # recommendation / summary) is answered INLINE via the router's
            # search_web tool — fast, and it avoids the empty-diff critic veto the
            # Worker->Critic pipeline applies to answer-only research (it grades
            # built artifacts via git diff and cannot verify a spoken answer or a
            # web citation → critic_loop_exhausted, live mission 019ecb56).
            # Offload to a mission ONLY when the request asks for a BUILT ARTIFACT
            # (a file / report) the critic can verify. The inline brain is
            # protected from the no-first-frame TTS ceiling by
            # _brain_thinking_heartbeat, so inline research no longer beheads the
            # voice turn — the reason this offload existed (Berlin→Melbourne) is
            # separately fixed. An EXPLICIT mission phrase ("sub-agent"/"deep
            # dive"/"umfassende"/...) already returned True above (AD-S9 trigger).
            if self._is_heavy_research(t):
                return self._research_wants_artifact(t)
            # A request to BUILD a deliverable (an HTML file / website / app /
            # report / document / visualization) is a sub-agent MISSION even
            # without a research verb — the Worker->Critic pipeline verifies the
            # built artefact via git diff. This fires PROVIDER-INDEPENDENTLY: a
            # tool-incapable talker (Codex/Antigravity subscription CLI) cannot
            # spawn via an LLM tool_call, so the deterministic gate is its only
            # spawn path. Live bug 2026-06-21: "build me an HTML file for my
            # Melbourne vacation" fell to the Antigravity deep brain, which (no
            # tools) only asked permission instead of building. NOT a Computer-Use
            # trigger: a build verb is not a screen action — "open/show the file"
            # stays Computer-Use via match_local_action; a bare question is caught
            # by the instructional guard above. _research_wants_artifact requires
            # a build verb, so a pure answer ("write a short summary") stays inline.
            if self._research_wants_artifact(t):
                return True
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

    def _research_wants_artifact(self, t: str) -> bool:
        """True iff a (heavy-research) request asks for a BUILT ARTIFACT — a
        file / report / document — rather than a spoken/written ANSWER.

        Option A (2026-06-15): research whose deliverable is an ANSWER goes
        INLINE via the router's search_web tool (fast, no critic friction);
        research that builds a verifiable file OFFLOADS to a mission (the
        Worker->Critic pipeline grades artifacts via git diff). The
        discriminator: a named file / "into a file" instruction on its own, OR a
        build/produce verb paired with a document noun. A research/analysis verb
        (recherchier/analysier/compare/...) is NOT a build verb, so "research X
        and compare Y" (an answer) does not match — it stays inline. Pure regex
        (AP-11 safe, cross-platform); empty/blank text → False.
        """
        text = t or ""
        if not text.strip():
            return False
        if _NAMED_FILE_RE.search(text):
            return True
        return bool(_BUILD_VERB_RE.search(text) and _DOC_NOUN_RE.search(text))

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
            # The DIRECT path surfaces tool output verbatim (no LLM re-render),
            # so the spoken acknowledgement must be localized HERE — the
            # language pin/detection that governs LLM replies never reaches it.
            ack_lang = self._direct_ack_language(user_text)
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
                    return result.error or action_phrase(
                        "tool_failed", ack_lang, tool=call.name
                    )
                if result.output is not None:
                    outputs.append(
                        self._localize_direct_ack(call, str(result.output), ack_lang)
                    )
            return "\n".join(outputs)

        if plan.mode == LocalActionMode.COMPUTER_USE:
            tool = self._local_action_tools.get("dispatch_to_harness")
            if tool is None:
                return None
            # Resolve the turn language ONCE here (while it is current) for the
            # spoken cost messages, the immediate ACK, and the background
            # readback — the offloaded task runs after the turn returns and must
            # not read the per-turn state itself (live bug 2026-06-15).
            cu_lang = self._direct_ack_language(user_text)
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
                    return action_phrase("cost_cooldown", cu_lang)
                if self._cost_meter.over_task_budget(tid):
                    return action_phrase("task_budget", cu_lang)
                if self._cost_meter.over_daily_budget():
                    return action_phrase("daily_budget", cu_lang)
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
                    lang=cu_lang,
                ),
                name="computer-use-background",
            )
            # Keep a strong reference so the task is not garbage-collected
            # mid-flight, and drop it on completion.
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)
            return action_phrase("cu_dispatch_ack", cu_lang)

        return None

    @staticmethod
    def _cu_failure_detail(output: Any) -> tuple[int | None, str | None]:
        """Pull ``(exit_code, human_detail)`` out of a CU harness failure result.

        ``dispatch_to_harness`` returns ``output`` as a dict with ``exit_code``
        plus ``stderr``/``stdout`` — and the screenshot loop writes the model's
        real ``fail`` reason into ``stderr`` (``"[cu] fail at <tag>: <reason>"``).
        We surface that reason so the readback can forward it instead of the
        opaque ``error="exit N"``. Best-effort: any non-dict / missing field
        yields ``(None, None)`` and the readback degrades to the exit-code phrase.
        """
        if not isinstance(output, dict):
            return None, None
        raw_code = output.get("exit_code")
        exit_code: int | None
        try:
            exit_code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            exit_code = None
        stderr = str(output.get("stderr") or "").strip()
        stdout = str(output.get("stdout") or "").strip()
        detail = stderr or stdout or None
        return exit_code, detail

    @staticmethod
    def _cu_failure_diagnostic(
        *, error: str | None, exit_code: int | None, detail: str | None
    ) -> str | None:
        """Compose the technical failure note for the TRANSCRIPT (never spoken).

        The voice readback is humanized via ``cu_failure_readback`` ("…didn't
        work on screen") — but the raw signal (the exit code plus the harness
        reason) is still valuable for debugging, so it is carried alongside on
        ``AnnouncementRequested.detail`` -> ``SpeechSpoken.detail`` and shown in
        the Transcription view (user request 2026-06-16). Returns None when
        there is nothing diagnostic to record (e.g. a successful run). Capped so
        the persisted payload stays small.
        """
        base = (error or "").strip() or (
            f"exit {exit_code}" if exit_code is not None else ""
        )
        reason = (detail or "").strip()
        note = f"{base} · {reason}" if base and reason else (base or reason)
        note = note.strip()
        return note[:300] if note else None

    async def _run_computer_use_background(
        self,
        *,
        tool: Any,
        harness_name: str,
        prompt: str,
        timeout_s: float,
        user_text: str,
        trace_id: UUID,
        lang: str,
    ) -> None:
        """Run the Computer-Use harness off the voice turn and speak the result.

        Launched fire-and-forget by ``_run_local_action_fast_path`` so the spoken
        turn ACKs immediately (AD-OE1) instead of blocking up to ~31 s on the
        harness. The outcome — success, failure, or timeout — is ALWAYS surfaced
        as an ``AnnouncementRequested(kind="completion")`` readback
        (AD-OE5/OE6: zero silent drops). Never raises — a background-task crash
        must not leak into the event loop.

        ``lang`` is captured at dispatch and threaded in: this task runs AFTER
        the turn returns, so ``self._turn_detected_lang`` may already belong to a
        later turn — reading it here would speak the wrong language (live bug
        2026-06-15: an English CU turn ended with the German "Erledigt.").
        """
        text: str
        # Technical failure note for the transcript (never spoken). Stays None
        # on success; the failure branch fills it with the exit code + reason.
        diag: str | None = None
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
                text = str(result.output or "").strip() or action_phrase("cu_done", lang)
            else:
                err = getattr(result, "error", None)
                exit_code, detail = self._cu_failure_detail(
                    getattr(result, "output", None)
                )
                text = cu_failure_readback(
                    lang, error=err, exit_code=exit_code, detail=detail,
                )
                diag = self._cu_failure_diagnostic(
                    error=err, exit_code=exit_code, detail=detail,
                )
        except TimeoutError:
            text = action_phrase("cu_timeout", lang, secs=f"{timeout_s:.0f}")
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
            text = action_phrase("cu_crashed", lang)
        # AD-OE6 zero silent drops: ALWAYS speak the outcome at the next turn
        # boundary (announcement -> scrub_for_voice -> TTS).
        try:
            await self._bus.publish(AnnouncementRequested(
                text=text,
                priority="normal",
                language=lang,
                # A background Computer-Use task reports the user's requested
                # desktop action as the turn completion.
                kind="completion",
                detail=diag,
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
            language=self._resolve_turn_lang(),
        ))

        if self._curator is not None:
            try:
                asyncio.create_task(
                    self._curator.process_turn(user_text, response_text),
                    name="curator-process-turn",
                )
            except RuntimeError:
                log.debug("Curator-Task nicht scheduled (kein Event-Loop)")

    def _arm_voice_confirm(self, descriptor: dict[str, Any], user_text: str) -> None:
        """Turn N: record a deferred consequential action awaiting yes/no.

        ``descriptor`` is the tool-use loop's ``voice_confirm`` payload
        (``{"trace_id": str, "tool_name": str}``). The language is resolved once
        here (the turn's output language) and reused for both the classifier and
        the outcome phrasing on turn N+1.
        """
        trace_raw = descriptor.get("trace_id")
        try:
            tid = UUID(str(trace_raw))
        except (ValueError, TypeError):
            log.warning("voice-confirm: bad trace_id %r — not arming", trace_raw)
            return
        lang = resolve_output_language(
            self._reply_language, "unknown", user_text,
            default=DEFAULT_LOCALE, conversation_language=self._conversation_language,
        )
        self._pending_voice_confirm = _PendingVoiceConfirm(
            trace_id=tid, lang=lang, tool_name=str(descriptor.get("tool_name", "")),
        )
        log.info(
            "voice-confirm armed: tool=%s trace=%s lang=%s",
            self._pending_voice_confirm.tool_name, tid, lang,
        )

    async def _resume_voice_confirm(self, user_text: str) -> str | None:
        """Turn N+1: classify the user's yes/no and resolve the pending action.

        Returns the spoken OUTCOME when the turn is consumed by the confirmation;
        returns ``None`` when the pending action is dropped and the utterance must
        be processed as a normal turn (the user said something unrelated — they
        moved on, so the consequential action is abandoned, never executed).
        """
        pending = self._pending_voice_confirm
        if pending is None:
            return None
        # Lazy import: a top-level import of these would close a circular chain
        # (jarvis.voice.echo_confirmation → jarvis.core.self_mod → writer →
        # jarvis.brain → manager → echo_confirmation, half-initialized).
        from jarvis.voice.echo_confirmation import classify_response
        from jarvis.voice.tool_confirmation import format_confirm_outcome

        verdict = classify_response(user_text, language=pending.lang)

        if verdict == "confirm":
            self._pending_voice_confirm = None
            try:
                result = await self._tool_executor.execute_confirmed(
                    pending.trace_id, user_utterance=user_text,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("voice-confirm execute failed: %s", exc)
                return format_confirm_outcome(
                    "failed", pending.tool_name, language=pending.lang
                )
            kind = "done" if getattr(result, "success", False) else "failed"
            return format_confirm_outcome(kind, pending.tool_name, language=pending.lang)

        if verdict == "veto":
            self._pending_voice_confirm = None
            await self._cancel_pending_confirm(pending.trace_id)
            return format_confirm_outcome(
                "vetoed", pending.tool_name, language=pending.lang
            )

        if verdict == "ambiguous":
            pending.reasks += 1
            if pending.reasks > _MAX_CONFIRM_REASKS:
                self._pending_voice_confirm = None
                await self._cancel_pending_confirm(pending.trace_id)
                return format_confirm_outcome(
                    "timeout", pending.tool_name, language=pending.lang
                )
            return format_confirm_outcome(
                "unclear", pending.tool_name, language=pending.lang
            )

        # unknown: the user moved on. Drop the pending action (safe — never
        # executed) and let this utterance run as a normal turn.
        self._pending_voice_confirm = None
        await self._cancel_pending_confirm(pending.trace_id)
        return None

    async def _cancel_pending_confirm(self, trace_id: UUID) -> None:
        """Best-effort cancel of a deferred action — never breaks the turn."""
        try:
            await self._tool_executor.cancel_pending(trace_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice-confirm cancel failed: %s", exc)

    def _spawn_ack_language(self, user_text: str) -> str:
        """Resolve the language for the spoken spawn acknowledgement.

        A pinned reply language (``brain.reply_language`` = de/en) wins;
        otherwise detect from the user's words. The spawn-announcement
        composer supports de/en only (ack-brain convention), so an "es"
        pin falls through to detection like "auto" does.
        """
        if self._reply_language in ("de", "en"):
            return self._reply_language
        return "de" if _looks_german(user_text) else "en"

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
        source_layer: str | None = None,
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
        if not self._should_force_spawn(user_text, source_layer=source_layer):
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
            # Empty action: the force-spawn heuristic has no LLM
            # interpretation. The spawn tool's announcement composer then
            # phrases the spoken ACK itself (flash-LLM with the delegation
            # persona, deterministic bilingual fallback) — see
            # jarvis/brain/ack_brain/spawn_announcement.py. Live regression
            # 2026-05-26 / redesign 2026-06-10: no canned template phrases.
            "action": "",
            "target": "",
            # Turn language for the spoken ACK: honour a reply-language pin,
            # otherwise detect from the user's words.
            "language": self._spawn_ack_language(user_text),
        }
        log.info("Force-Spawn OpenClaw: %r", user_text[:160])
        # Stamp the turn's resolved output language so spawn_worker drives the
        # spoken ACK + mission language from the ONE authoritative resolver on
        # the force-spawn path too (the tool-use loop does this for brain
        # function-calls; this caller must do it itself or ctx.config is empty
        # and the language silently falls back — Runtime Output Language).
        out_lang = resolve_output_language(
            self._reply_language, "unknown", user_text,
            default=DEFAULT_LOCALE,
            conversation_language=self._conversation_language,
        )
        result = await self._tool_executor.execute(
            tool,
            args,
            user_utterance=user_text,
            config_snapshot={"output_language": out_lang},
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
            # Prefer the brain-leaked interpretation; the spawn tool's
            # announcement composer validates the leaked spoken_ack (if any)
            # and otherwise phrases the ACK itself.
            "action": str(leaked.get("action") or ""),
            "target": str(leaked.get("target") or ""),
            "spoken_ack": str(leaked.get("spoken_ack") or ""),
            "language": (
                str(leaked.get("language") or "")
                or self._spawn_ack_language(user_text)
            ),
        }
        log.warning(
            "Recovered leaked spawn_worker tool-call from brain text "
            "(provider function-calling leak): %r", user_text[:160],
        )
        # Same authoritative-language stamping as the force-spawn path: without
        # a config snapshot ctx.config is empty and spawn_worker's language
        # falls back instead of honoring the resolver (Runtime Output Language).
        out_lang = resolve_output_language(
            self._reply_language, "unknown", user_text,
            default=DEFAULT_LOCALE,
            conversation_language=self._conversation_language,
        )
        result = await self._tool_executor.execute(
            tool, args, user_utterance=user_text,
            config_snapshot={"output_language": out_lang},
            trace_id=trace_id,
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
            # A failed cli_<name> call carries the real cause in stderr; speak
            # it instead of the bare "exit N" error token (live repro
            # 2026-06-17, gcloud billing budgets list -> exit 1).
            if name.startswith(_CLI_TOOL_PREFIX):
                return _cli_failure_reason(
                    result.output, result.error, german=_looks_german(user_text),
                )
            return result.error or (
                f"Die Aktion '{name}' konnte nicht ausgefuehrt werden."
            )
        # A read tool (search_web, wiki-recall, …) returns STRUCTURED data, not
        # a spoken sentence. Render it to speakable text — ``str(result.output)``
        # on a dict put a ``{``-prefixed repr on the wire that the streaming
        # guard dropped as a "leak", so a successful search dead-ended in the
        # canned action-failed phrase (live repro 2026-06-14 "Was hältst du von
        # exp.com?"). See :func:`_render_recovered_tool_output`.
        spoken = _render_recovered_tool_output(result.output)
        if spoken:
            return spoken
        # Tool ran but produced nothing speakable (e.g. an empty search). Give a
        # real spoken sentence, never silence and never the failure phrase.
        return (
            "Dazu habe ich nichts gefunden."  # i18n-allow: spoken German TTS
            if _looks_german(user_text)
            else "I couldn't find anything on that."
        )

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

    def _brain_can_call_tools(self, provider: str, model: str | None) -> bool:
        """Runtime tool-calling capability of a provider, capability-driven.

        A brain may expose ``can_call_tools()`` to report it cannot emit
        tool_calls right now (the subscription-CLI brains — Codex over the ChatGPT
        login, Antigravity over the Google login — drop ALL tools). Falls back to
        the static ``supports_tools`` ceiling, then True. Any error → True so the
        chain is never blocked by a capability probe."""
        try:
            brain = self._get_brain(provider, model)
        except Exception:  # noqa: BLE001
            return True
        fn = getattr(brain, "can_call_tools", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:  # noqa: BLE001
                return True
        return bool(getattr(brain, "supports_tools", True))

    def _active_can_call_tools(self) -> bool:
        """Whether the ACTIVE talker can emit tool_calls this turn."""
        return self._brain_can_call_tools(
            self._active_name, self._fast_model(self._active_name)
        )

    def _first_tool_capable_provider(
        self, level: str
    ) -> tuple[str, str | None] | None:
        """First AVAILABLE provider that can emit tool_calls — used to lead a
        tool/action turn when the active talker cannot. deep_brain first, then a
        stable cross-provider order. Returns (name, model) or None when no
        tool-capable provider is reachable (then the chain stays unchanged)."""
        available = set(self._registry.available())
        order: list[str] = []
        db = self._config.brain.deep_brain
        if db:
            order.append(db)
        order += ["gemini", "claude-api", "openai", "openrouter", "grok"]
        seen: set[str] = set()
        for name in order:
            if name in seen or name == self._active_name or name not in available:
                continue
            seen.add(name)
            model = (
                self._deep_model(name) if level in ("deep", "code")
                else self._fast_model(name)
            ) or self._fast_model(name)
            if self._brain_can_call_tools(name, model):
                return (name, model)
        return None

    def _turn_has_action_intent(self, user_text: str) -> bool:
        """Best-effort, provider-agnostic 'this turn wants a tool/desktop action'
        using the EXISTING deterministic detectors (no new signal-word list).
        Used only to decide whether a tool-incapable active talker should delegate
        this turn — a pure conversation/knowledge turn returns False and stays on
        the chosen provider."""
        t = user_text or ""
        if is_open_app_intent(t) or _looks_like_pc_control(t):
            return True
        try:
            from jarvis.core.capabilities import get_registry  # noqa: PLC0415

            reg = get_registry()
            if getattr(reg, "all", lambda: ())() and reg.has_action_intent(t):
                return True
        except Exception:  # noqa: BLE001 — registry must never block routing
            pass
        return False

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        """Returns a prioritised list of (provider, model) attempts."""
        active = self._active_name
        chain: list[tuple[str, str | None]] = []
        # Reset the per-turn router-lead marker every build (a stale value would
        # make the loop wrongly fall through). Set below only when we prepend an
        # intelligent-router lead.
        self._router_lead_key: tuple[str, str | None] | None = None

        # Capability-driven tool delegation (NOT a per-provider hardcode): the
        # subscription-CLI brains (Codex over the ChatGPT login, Antigravity over
        # the Google login) cannot emit tool_calls — can_call_tools() == False —
        # so a tool turn reaching them is dropped/confabulated. We hand tool
        # selection to a tool-capable provider; any future CLI brain inherits this.
        if not self._active_can_call_tools():
            intelligent = bool(
                getattr(self._config.brain.routing, "intelligent_router", True)
            )
            if intelligent and getattr(self, "_turn_substantive", False):
                # INTELLIGENT ROUTER (2026-06-21 mandate): a tool-capable provider
                # LEADS every substantive turn and the LLM itself picks the tool
                # via its tool-use loop + the router system prompt — no signal-word
                # list decides the tool. If it picks NO tool (pure conversation),
                # generate()'s chain loop FALLS THROUGH to the chosen talker (see
                # ``_router_lead_key``), so the user keeps their selected brain's
                # voice. The deterministic gates stay as high-precision guardrails.
                helper = self._first_tool_capable_provider(level)
                if helper is not None and helper[0] != active:
                    log.info(
                        "Intelligent router: %s cannot call tools — %s leads this "
                        "turn and picks the tool (falls through to %s if none).",
                        active, helper[0], active,
                    )
                    self._router_lead_key = helper
                    chain.append(helper)
            elif getattr(self, "_turn_needs_tools", False):
                # Flag OFF (kill switch): the narrower action-intent delegation —
                # delegate ONLY when a deterministic action signal fired, and let
                # the tool-capable provider answer the whole turn (no fall-through).
                helper = self._first_tool_capable_provider(level)
                if helper is not None and helper[0] != active:
                    log.info(
                        "Tool delegation (legacy): %s cannot call tools — leading "
                        "this action turn with %s.", active, helper[0],
                    )
                    chain.append(helper)

        # 0. Deep/code intents: dedicated deep_brain first (e.g. gemini via
        #    subscription — bypasses /v1/messages API quota). Bug fix 2026-04-29:
        #    at level=deep the deep_model of the brain MUST be used (previously:
        #    _fast_model → gemini-3-flash for a deep request instead of
        #    gemini-3.1-pro-preview).
        deep_brain = self._config.brain.deep_brain
        # When the user has explicitly made a frontier SUBSCRIPTION brain the
        # active one (codex via ChatGPT),
        # it leads ALL turns — the deep_brain (e.g. gemini) must NOT jump ahead for
        # deep/code intents, or the chosen brain would never actually answer a hard
        # question despite being selected (it would silently fall through to the
        # deep_brain). Other active brains keep the deep_brain routing unchanged.
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
        source_layer: str | None = None,
        allow_voice_confirm: bool = False,
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
        # Clear last turn's provider identity so a helper prompt build before the
        # fallback loop (wiki-delta base) does not carry a stale provider name.
        self._active_turn_identity = None
        turn_trace_id = trace_id or uuid4()

        # auto mode: resolve this turn's language so _reply_language_directive()
        # hard-pins it (a soft "mirror" drifts to German on tool-synthesis
        # turns — live bug 2026-06-14: an English weather turn answered in
        # German). Conversation stickiness: a thin interjection ("Now") inherits
        # the running conversation language instead of flipping it (forensic
        # 2026-06-18); ambiguous text stays "unknown" -> soft mirror; an explicit
        # reply_language pin leaves it empty -> the directive uses the pin.
        self._update_turn_language(user_text)

        # Two-turn voice/chat confirmation resume (turn N+1). MUST run before the
        # cancel-intent intercept: a "nein"/"stop" answer to a pending
        # confirmation is a VETO of that one action, not a global cancel-all.
        # Returns the spoken outcome (turn consumed) or None (user moved on →
        # the pending action is dropped and this utterance runs as a normal turn).
        if self._pending_voice_confirm is not None:
            resumed = await self._resume_voice_confirm(user_text)
            if resumed is not None:
                await self._record_response_side_effects(
                    user_text=user_text, response_text=resumed,
                    use_history=use_history, trace_id=turn_trace_id,
                )
                return resumed

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
        # Evidence-gate state is strictly per-turn — a stale directive must
        # never leak into a later prompt build (e.g. a skill turn that
        # early-returns before the gate runs).
        self._evidence_directive = ""
        self._evidence_required_tool = ""
        # AD-S4: a trigger noted by the speech pipeline / chat hook takes
        # precedence — it carries the captured content and the source label.
        self._consume_pending_skill_trigger(user_text)
        # AD-S9: an explicit heavy-work trigger ("Sub-Agent", "OpenClaw",
        # "spawne", "deep dive", …) names the execution vehicle — the mission
        # path owns such a turn, not the inline skill prompt. Live bug
        # 2026-06-10 14:34: "spawne einen Sub-Agent … Gmail …" became a mute
        # inline gmail-skill turn instead of a mission.
        if (
            self._skill_turn_match is not None
            and self._get_force_spawn_pattern().search(user_text)
        ):
            log.info(
                "Skill match %s stands down — explicit heavy-work trigger in "
                "the utterance wins (AD-S9: mission, not inline skill).",
                getattr(self._skill_turn_match, "name", "?"),
            )
            self._skill_turn_match = None
        # Sibling of AD-S9: a plugin/marketplace skill that merely keyword-
        # matched an APP NAME ("Discord", "Spotify", "Slack") must NOT capture a
        # turn the deterministic desktop-control gate owns. Computer-Use is the
        # universal GUI integration — "open Discord and find the post on screen"
        # must reach it even when the plugin's API/MCP integration is absent,
        # instead of suppressing the local-action fast path and falling through
        # to a tool-less CLI talker that hallucinates a permissions refusal.
        # Live bug 2026-06-21 (sessions.db turn 67276501-…): plugin-discord
        # matched the bare word "Discord", the antigravity deep brain (a CLI
        # talker that drops all tools) then said "ich habe keinen Zugriff auf
        # Discord". The gate decision is authoritative and precise: only a
        # DIRECT open or a COMPUTER_USE plan stands the skill down — a pure
        # dispatch ("schick eine Discord-Nachricht", gate → None/UNSUPPORTED)
        # keeps its skill, and a non-app skill turn ("starte die Morgenroutine",
        # gate → None) is untouched.
        if self._skill_turn_match is not None:
            _gate_plan = match_local_action(user_text)
            if _gate_plan is not None and _gate_plan.mode in (
                LocalActionMode.DIRECT,
                LocalActionMode.COMPUTER_USE,
            ):
                log.info(
                    "Skill match %s stands down — the deterministic local-action "
                    "gate claims this turn as %s; Computer-Use owns it "
                    "(universal GUI integration, not a keyword-matched plugin).",
                    getattr(self._skill_turn_match, "name", "?"),
                    _gate_plan.mode.value,
                )
                self._skill_turn_match = None
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
            user_text, trace_id=turn_trace_id, source_layer=source_layer,
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

        # Evidence gate (AD-CLI4..AD-CLI8): questions about external-data
        # domains (calendar/email/tasks/repos/deployments) are never answered
        # from the model's head. Either a connected CLI covers the domain
        # (mandatory-tool directive for this turn) or the answer is a
        # deterministic honest refusal. Pure regex + registry lookup, no LLM
        # (AP-11). Skill turns already returned above; non-CLI capabilities
        # (paired skills, router tools, MCP) make the gate stand down (PASS).
        verdict = self._run_evidence_gate(user_text)
        if verdict.kind == "honest_refusal":
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=verdict.refusal_text,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return verdict.refusal_text
        if verdict.kind == "require_tool":
            log.info(
                "Evidence gate: domain=%s requires tool %s this turn",
                verdict.domain, verdict.tool_name,
            )
            injected = False
            if verdict.domain == "activity":
                # The fast brain will NOT reliably honor a soft tool directive
                # (live 2026-06-18: awareness-recall was mandated yet never
                # called — executed=[] in the log — and the model confabulated
                # "der lokale Verlaufsspeicher ist nicht verfügbar"). The tool
                # is internal, read-only and safe, so run it deterministically
                # HERE (via the ToolExecutor) and inject its result as concrete
                # answer-context. The brain then answers from real data with no
                # dependency on its tool-calling discretion; the honest-fallback
                # guard is intentionally left disarmed because the data is
                # already in hand.
                block = await self._prefetch_activity_block(
                    verdict.tool_name, user_text, trace_id=turn_trace_id,
                )
                if block:
                    self._evidence_directive = (
                        "The user is asking what they had open / were doing on "
                        "their computer. Their ACTUAL recent on-device activity "
                        "is below — answer the question from THIS data, "
                        "naturally and concisely. The awareness store IS "
                        "available; never claim it is unavailable.\n\n" + block
                    )
                    self._evidence_required_tool = ""
                    injected = True
            if not injected:
                self._evidence_directive = verdict.directive
                self._evidence_required_tool = verdict.tool_name

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

        # 3. Build fallback chain and try each entry.
        # Provider-agnostic tool routing flags (consumed by _build_fallback_chain):
        #  - _turn_substantive: a non-smalltalk turn. With the intelligent router
        #    on, a tool-capable provider LEADS such a turn for a tool-incapable
        #    talker and the LLM picks the tool (or falls through to the talker).
        #  - _turn_needs_tools: the narrower action-intent signal used as the
        #    flag-OFF (kill-switch) delegation. Reuses the deterministic detectors.
        # Reset _router_lead_key here too so a monkeypatched _build_fallback_chain
        # (tests / callers that replace it) never leaves a stale fall-through marker.
        self._router_lead_key = None
        self._turn_substantive = not is_smalltalk_turn
        self._turn_needs_tools = (not is_smalltalk_turn) and self._turn_has_action_intent(
            user_text
        )
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
                        (p, "", "missing_key", "no API key in this session")
                        for p in self._dead_providers
                    ]),
                )
            else:
                log.warning("No brain providers available — spoken fallback used.")
            return await self._provider_down_reply(trace_uuid)

        history = self._history if use_history else []
        _drop_in_hist = sum(
            1 for m in history
            if isinstance(getattr(m, "content", None), str)
            and "\U0001F4CE" in m.content
        )
        if _drop_in_hist:
            log.info(
                "📎 DROP CONTEXT present in this turn's history: %d note(s), "
                "use_history=%s, total history=%d",
                _drop_in_hist, use_history, len(history),
            )
        last_exc: Exception | None = None
        response_text = ""
        used_provider: str | None = None
        used_model: str | None = None
        _turn_executed: set[str] = set()  # tools that REALLY ran this turn
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
        # above. When the utterance points at the mouse cursor ("was ist das da?")  # i18n-allow
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

        # Drag-drop SILENT context: pictures parked by ``add_dropped_context``
        # (a drop never triggers its own turn) are pulled into THIS real turn,
        # once — added AFTER vision + AI-Pointer image logic so neither clobbers
        # them. Cleared on consume; never re-sent on later turns.
        _dropped_imgs = getattr(self, "_pending_drop_images", ()) or ()
        if _dropped_imgs:
            self._pending_drop_images = ()
            images = tuple(_dropped_imgs) + tuple(images)

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
                # Non-smalltalk turn: drop plugin tools irrelevant to this
                # utterance (progressive disclosure), then hide any plugin whose
                # CLI counterpart is connected (req 4: CLI > plugin fallback).
                else self._suppress_plugins_covered_by_cli(
                    self._apply_plugin_relevance(user_text, self._tools)
                )
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
            # Screen-relevance gate (2026-06-14): the on-demand ``screenshot``
            # tool is only in scope when the utterance refers to the screen (or
            # an image is attached / it is a pointer turn). On a plain
            # conversation or cut-off small-talk fragment the brain must not be
            # able to reach for — and then narrate — the screen.
            if isinstance(_turn_tools, dict):
                _turn_tools = self._gate_screen_tool(
                    _turn_tools,
                    user_text=user_text,
                    has_image=bool(images),
                    pointing_turn=pointing_turn,
                )
            # Active-model self-awareness: stamp the provider/model that is about
            # to answer so _build_system_prompt injects the correct, specific
            # self-identity (anti-"I'm Gemini" hallucination, forensic 2026-06-20).
            # Set here — after dead/cooldown skips — so it always names the
            # provider that genuinely runs this attempt, including a fallback win.
            self._active_turn_identity = (prov_name, model)
            disp = self._build_dispatcher(brain, tools_override=_turn_tools)
            # Intelligent router: the router LEAD must NOT stream its conversational
            # text to TTS. On the streaming path (generate_stream) text_consumer
            # speaks each chunk live DURING dispatch — so a no-tool router answer
            # would be spoken and THEN the fall-through talker would speak again
            # (double answer). Suppress the consumer for the lead: if it picks a
            # tool, the result is surfaced by generate_stream's final reconciliation
            # (nothing was yielded → it yields holder["final"]); if it picks none,
            # the chosen talker streams the answer normally after the fall-through.
            _is_router_lead = self._router_lead_key == (prov_name, model)
            _attempt_consumer = None if _is_router_lead else text_consumer
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
                    evidence_required_tool=self._evidence_required_tool,
                    text_consumer=_attempt_consumer,
                    on_progress=on_progress,
                    turn_context=turn_context,
                    reply_language=self._reply_language,
                    conversation_language=self._conversation_language,
                    voice_confirm=(allow_voice_confirm and self._voice_confirm_enabled),
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

                # INTELLIGENT ROUTER fall-through: this attempt is the tool-capable
                # router LEAD that was prepended for a tool-incapable talker. It got
                # first crack at tool selection; if it picked NO tool (pure
                # conversation) and a chosen talker follows in the chain, discard
                # its answer and fall through so the user keeps their selected
                # brain's voice. A tool it DID select (tool_calls non-empty) breaks
                # normally below and IS the turn's result. Placed BEFORE the events
                # publish below, so the discarded router turn is not recorded as the
                # turn; its cost was metered above (it genuinely ran). Reversible
                # via [brain.routing].intelligent_router (then _router_lead_key is
                # never set, so this never fires).
                if (
                    self._router_lead_key == (prov_name, model)
                    and not tool_calls_executed
                    and idx < len(chain) - 1
                ):
                    log.info(
                        "Intelligent router: %s picked no tool — falling through to "
                        "%s for the conversational answer.",
                        prov_name, chain[idx + 1][0],
                    )
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
                # Remember the tools that REALLY ran so the post-recovery
                # evidence-gate enforcement (below) can tell whether a mandated
                # tool was actually called this turn.
                _turn_executed = set(executed)
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
            return await self._provider_down_reply(trace_uuid)

        # Robustness net (2026-05-24): a provider (notably Gemini) sometimes
        # emits a spawn_worker tool_use block as TEXT instead of executing
        # it — response_text becomes raw `[{"type":"tool_use",...}]` JSON.
        # Without this the JSON is spoken (scrubbed to "Es trat ein Fehler
        # auf") and the delegated Opus-4.7 sub-agent never runs. Detect the
        # leak and execute the spawn through the normal tool path so the
        # heavy-work delegation is robust against provider function-calling
        # flakiness.
        # Two-turn voice/chat confirmation (turn N): the tool-use loop deferred a
        # consequential tool and produced a confirmation QUESTION as its text.
        # Arm the pending state and return the question directly — the leaked-tool
        # recovery + evidence gate below do not apply to a deferral (no tool ran,
        # nothing to recover; the answer is a question, not an unverified claim).
        if (
            getattr(agg, "finish_reason", "") == "voice_confirm_pending"
            and getattr(agg, "voice_confirm", None)
        ):
            self._arm_voice_confirm(agg.voice_confirm, user_text)
            await self._record_response_side_effects(
                user_text=user_text, response_text=agg.text,
                use_history=use_history, trace_id=trace_uuid,
            )
            return agg.text

        recovered = await self._recover_leaked_tool(
            response_text, user_text=user_text, trace_id=trace_uuid,
        )
        if recovered is not None:
            response_text = recovered

        # Evidence-gate enforcement (live repro 2026-06-17, session 296abc82):
        # the gate MANDATED a tool this turn, but neither the normal tool loop
        # nor the leaked-tool recovery above actually ran it — so the model's
        # answer is unverified, at worst a confabulation ("the gcloud tool
        # blocked execution because it classified the request as an explanatory
        # question"). Replace it with an honest non-data fallback; never speak an
        # answer a mandated read tool was supposed to ground. Runs AFTER recovery
        # so a leaked-but-recovered mandated tool (real data) is not pre-empted.
        if recovered is None and _evidence_answer_is_unverified(
            self._evidence_required_tool,
            _turn_executed,
            response_text,
            suppressed=self._last_turn_suppressed,
        ):
            log.warning(
                "Evidence gate mandated %s but it never ran (executed=%s) — "
                "replacing the unverified answer with an honest fallback.",
                self._evidence_required_tool,
                sorted(_turn_executed),
            )
            response_text = _evidence_unfulfilled_answer(
                lang=resolve_output_language(
                    self._reply_language, "unknown", user_text, default="de"
                )
            )

        # 4. History + Events
        if use_history:
            self._history.append(BrainMessage(role="user", content=user_text))
            self._history.append(BrainMessage(role="assistant", content=response_text))
            if len(self._history) > 40:
                self._history = self._history[-40:]

        await self._bus.publish(ResponseGenerated(
            trace_id=trace_uuid,
            text=response_text,
            language=self._resolve_turn_lang(),
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

    def inject_images_for_turn(
        self, trace_id: UUID, images: tuple[ImageBlock, ...]
    ) -> None:
        """Attach ad-hoc ``images`` to the upcoming turn identified by ``trace_id``.

        Used by the drag-drop intake (``jarvis/brain/drop_context.py``) so a
        dropped picture reaches the multimodal brain. The images are consumed by
        ``_collect_vision_images`` on that turn and never carry over. A no-op for
        an empty tuple. ``trace_id`` is unique per turn → race-free.
        """
        if not images:
            return
        # Defensive: tolerate a manager built via __new__ (some unit tests bypass
        # __init__), mirroring how _vision_provider is accessed via getattr.
        if getattr(self, "_pending_turn_images", None) is None:
            self._pending_turn_images = {}
        self._pending_turn_images[trace_id] = tuple(images)

    def add_dropped_context(
        self, text: str, images: tuple[ImageBlock, ...] = ()
    ) -> None:
        """Stash drag-and-dropped content as SILENT conversation context.

        A drop must NOT trigger a brain turn — the user keeps the normal speaking
        flow, and the dropped content is simply remembered and used on the NEXT
        real turn (a drop while idle is kept for next time; a drop mid-flow joins
        the running context). The text is appended to history as a user-context
        message so it is naturally in the next turn's context (and persists for
        follow-ups); images are parked and consumed once by the next
        ``generate`` call. getattr-guarded for managers built via ``__new__``.
        """
        if text and text.strip():
            if getattr(self, "_history", None) is None:
                self._history = []
            self._history.append(BrainMessage(role="user", content=text.strip()))
            if len(self._history) > 40:
                self._history = self._history[-40:]
        log.info(
            "📎 DROP CONTEXT stashed: %d text chars, %d images "
            "(history now %d msgs, pending drop images %d)",
            len(text or ""), len(images),
            len(getattr(self, "_history", []) or []),
            len(getattr(self, "_pending_drop_images", ()) or ()) + len(images),
        )
        if images:
            cur = getattr(self, "_pending_drop_images", ()) or ()
            self._pending_drop_images = tuple(cur) + tuple(images)

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
        # Drag-drop: ad-hoc images injected for THIS turn win over (and bypass)
        # the screen-vision path — a dropped picture matters, not the current
        # screen, and it must arrive even with screen-vision off. Pop so it is
        # used exactly once. getattr-guarded for managers built via __new__.
        pending = getattr(self, "_pending_turn_images", None)
        if pending:
            injected = pending.pop(trace_id, None)
            if injected:
                return injected

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
        allow_voice_confirm: bool = False,
    ) -> AsyncIterator[str]:
        """Latency sprint 1: streaming variant of ``generate``.

        Yields each brain text chunk in real time. Tool-use loops run as
        usual; pre-tool-use text is also streamed (the persona prompt forbids
        fillers, so this is uncritical). Evidence-gated turns are buffered
        until ``generate`` returns its authoritative final text, because the
        post-call evidence enforcement may replace an unverified stream.

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
                    allow_voice_confirm=allow_voice_confirm,
                )
            finally:
                # Sentinel signals "brain is done (or crashed)".
                queue.put_nowait(sentinel)

        task = asyncio.create_task(_producer(), name="brain-stream-producer")
        accumulated = ""
        leaked = False
        yielded = False
        evidence_buffered = False
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
                if getattr(self, "_evidence_required_tool", ""):
                    evidence_buffered = True
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
            if leaked or not yielded or evidence_buffered:
                final = (holder.get("final") or "").strip()
                if final and not _looks_like_tool_use_leak(final):
                    yield final
                elif leaked:
                    yield self._action_failed_phrase(user_text)
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

    def drop_last_turn(self, expected_user_text: str) -> bool:
        """Remove the most recent (user, assistant) pair when its user message
        matches ``expected_user_text`` (whitespace-insensitive).

        Used by the voice continuation-recombine path: when a combined turn
        supersedes the immediately-preceding committed turn, the truncated half
        must not be duplicated in history. Safe no-op when fewer than two
        messages are buffered, when the tail is not a user/assistant pair, or
        when the tail user text does not match — so it does nothing when the
        prior turn was aborted before commit (the common interrupt case).
        Returns ``True`` iff a pair was removed.
        """
        if len(self._history) < 2:
            return False
        last = self._history[-1]
        prev = self._history[-2]
        if last.role != "assistant" or prev.role != "user":
            return False
        if (prev.content or "").strip() != (expected_user_text or "").strip():
            return False
        del self._history[-2:]
        return True

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
            from jarvis.brain.factory import (
                _load_local_action_tools,
                _load_tools_for_tier,
                _resolve_mission_manager,
            )
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

            # ROOT CAUSE of the "der lokale Verlaufsspeicher ist nicht verfügbar"
            # voice bug (live 2026-06-18): this rebuild — triggered by EVERY
            # CLI/MCP connect at boot ("Tool-Registry refreshed: 29 -> 107") —
            # used to drop the four shared DI references the boot path passes, so
            # the rebuilt awareness-recall got recall_store=None (and
            # awareness-snapshot/contact/spawn_worker lost their managers too).
            # awareness-recall then returned "awareness recall store unavailable"
            # FOREVER after the first CLI connected, and the brain faithfully
            # relayed that — it was a genuine outage, never a confabulation. The
            # boot DI MUST be mirrored here so a refresh preserves it.
            new_tools = _load_tools_for_tier(
                tier,
                bus=self._bus,
                executor=executor,
                harness_manager=harness_manager,
                user_profile=self._user_profile,
                people=self._people,
                config=self._config,
                mission_manager=_resolve_mission_manager(),
                awareness_manager=self._awareness_manager,
                recall_store=self._recall,
                contacts=self._contacts,
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

    def _select_task_tools(self, allowed_tools: tuple[str, ...]) -> dict[str, Tool]:
        """Filter the live tool set down to a per-task allowlist.

        Unknown grants (e.g. a plugin that isn't connected) are silently
        skipped — the task runs with whatever of its allowlist is live.
        """
        allow = set(allowed_tools)
        return {name: tool for name, tool in self._tools.items() if name in allow}

    async def run_task(
        self,
        *,
        prompt: str,
        allowed_tools: tuple[str, ...] = (),
        model_tier: str = "auto",
        trace_id: UUID | None = None,
    ) -> str:
        """Run one isolated agentic turn for a scheduled task.

        The turn sees ONLY the allowlisted tools and runs with an EMPTY
        history, so it never pollutes the live voice session's ``_history``
        or sticky model level (a scheduled task fires off the chat path).
        Tool calls still flow through the shared ``ToolExecutor`` — so
        read-only (monitor-tier) plugins pass unattended while ask-tier
        actions still hit the approval gate (which, with no human present,
        means they block until the unattended-approval wave wires Option B).

        Returns the final assistant text.
        """
        name = self._active_name
        if model_tier == "deep":
            model = self._deep_model(name) or self._fast_model(name)
            intent = "deep"
        else:
            # "fast" and "auto" both resolve to the fast model — the cheapest
            # correct default for an unattended background turn.
            model = self._fast_model(name)
            intent = "fast"
        brain = self._get_brain(name, model)
        tools = self._select_task_tools(allowed_tools)
        dispatcher = self._build_dispatcher(brain, tools_override=tools)
        agg = await dispatcher.dispatch(
            prompt, history=[], intent_level=intent, trace_id=trace_id,
        )
        return agg.text or ""

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


# Leak-recovery fallback variants — see BrainManager._action_failed_phrase.
_ACTION_FAILED_PHRASES: dict[str, str] = {
    "de": (
        "Ich habe die Aktion erkannt, "  # i18n-allow: spoken German TTS
        "konnte sie aber nicht ausfuehren."  # i18n-allow: spoken German TTS
    ),
    "en": "I recognized the action but couldn't execute it.",
    "es": "Reconocí la acción, pero no pude ejecutarla.",
}

# DIRECT local-action acknowledgement — see BrainManager._localize_direct_ack.
# open_app hardcodes a German launch acknowledgement that the DIRECT fast path
# surfaces VERBATIM (no LLM re-render), so its leading verb is translated to the
# turn language here (live bug 2026-06-15: an English "open my explorer" turn was
# acknowledged in German even with the English pin set). Only the verb prefix is
# swapped — the suffix (the actual app / URL the tool reported) is preserved
# untouched. The "de" entry MUST match open_app's literal prefix in
# jarvis/plugins/tool/open_app.py; a mismatch degrades safely to passthrough
# (the historical German string), never a crash.
_OPEN_APP_ACK_PREFIX: dict[str, str] = {
    "de": "Gestartet:",  # i18n-allow: spoken German TTS acknowledgement
    "en": "Opened:",
    "es": "Abierto:",
}


def _looks_german(text: str) -> bool:
    """True when *text* is clearly German.

    Delegates to the canonical ``detect_text_language`` (the single source of
    truth the pipeline uses for the turn language) instead of a private
    stop-word list. The old heuristic compared two tiny hint lists with
    ``score_de >= score_en``, so any text with no recognised stop-word in
    either list scored 0-0 and was declared German. A clean English sentence
    ("Could you please tell me which city ... in Australia?") therefore tied to
    German and was acknowledged / labelled German (live bug 2026-06-14). The
    canonical detector returns ``"unknown"`` on ambiguity, so English, Spanish
    and zero-signal text are now correctly NOT German.
    """
    return detect_text_language(text) == "de"


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
