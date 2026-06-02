"""Config loading with layers: TOML → YAML profiles → Env → Runtime.

Secrets do NOT come from the config file; they come from the Windows Credential
Manager via `keyring`. The `get_secret()` getter is the single access point.

Hot-reload: watchdog monitors the config file and dispatches `ConfigReloaded`
on change. Subscribers decide whether to reinitialise themselves.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# wake_constants is pure stdlib (no jarvis imports) — safe to import from this
# foundational config module without a cycle. Single source of truth for the
# wake-engine enum + the default phrase.
from jarvis.speech.wake_constants import DEFAULT_WAKE_PHRASE, WAKE_ENGINES

from .protocols import RiskTier

# Sub-config from the awareness sub-package. A top-level import is fine because
# jarvis.awareness.config only knows Pydantic and never calls back into core.* —
# no circular-import risk.
from jarvis.awareness.config import AwarenessConfig

# AckBrainConfig lives under jarvis.brain.ack_brain.config. We cannot
# import it at module top because jarvis.brain.__init__ eagerly loads
# brain.manager + brain.router, both of which import JarvisConfig from
# this module — a circular import. The deferred import + model_rebuild
# at the bottom of this file resolves the forward reference once
# JarvisConfig is already in this module's namespace.
if TYPE_CHECKING:
    from jarvis.brain.ack_brain.config import AckBrainConfig

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "jarvis.toml"
PROFILES_DIR = PROJECT_ROOT / "profiles"
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"

KEYRING_SERVICE = "personal-jarvis"

# Provider-secrets are intentionally kept out of TOML. Keep the accepted
# Credential-Manager slots and ENV fallbacks in one place so pre-boot checks,
# Frontier resolving and provider adapters do not disagree about whether a
# provider is configured.
PROVIDER_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "claude-api": (("anthropic_api_key", "ANTHROPIC_API_KEY"),),
    "openai": (("openai_api_key", "OPENAI_API_KEY"),),
    "openrouter": (("openrouter_api_key", "OPENROUTER_API_KEY"),),
    "gemini": (
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    ),
    "grok": (
        ("grok_api_key", "GROK_API_KEY"),
        ("xai_api_key", "XAI_API_KEY"),
    ),
}


# ----------------------------------------------------------------------
# Sub-configs (Pydantic models per layer)
# ----------------------------------------------------------------------

class ProfileConfig(BaseModel):
    name: str = "default"
    language: str = "auto"


class PersonaConfig(BaseModel):
    """The assistant's own identity — how it refers to itself.

    ``name`` is the spoken/written name the assistant uses ("Du bist <name>").
    Empty (default) means "derive it from the wake phrase" — so setting the wake
    word to "Micron" makes the assistant call itself Micron, with no second
    field to fill in. Set a value here to decouple the name from the wake word
    (e.g. wake "Hey Computer" but identity "Friday"). Resolved by
    ``jarvis.brain.assistant_name.resolve_assistant_name``.
    """

    name: str = ""


class WakeWordConfig(BaseModel):
    """User-editable ``[trigger.wake_word]`` — the custom-wake-word config.

    ``phrase`` is the single source of truth (the human wake word). ``engine``
    selects how it is detected; ``resolve_wake_plan`` turns this into a concrete
    plan. See docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md.
    """

    # extra="allow": survive future [trigger.wake_word.*] sub-keys and any
    # legacy key through a self-mod pre-validate round-trip (AP-16).
    model_config = ConfigDict(extra="allow")

    # The human wake word the user wants — e.g. "Hey Jarvis", "Computer",
    # "Athena". The single source of truth the UI/wizard edit.
    phrase: str = DEFAULT_WAKE_PHRASE
    # Detection engine. "auto" resolves the best path for the phrase:
    #   pretrained openWakeWord model (jarvis/alexa/mycroft/rhasspy) -> else
    #   local-Whisper transcript match for an arbitrary phrase -> else
    #   graceful fallback to "Hey Jarvis" with a clear message.
    # Validated against wake_constants.WAKE_ENGINES; unknown coerces to "auto"
    # so a stale/hand-edited value cannot brick the boot (AP-16).
    engine: str = "auto"
    # Path to a user-supplied/trained .onnx wake model (engine="custom_onnx").
    custom_model_path: str = ""
    # 0..1 mapped onto the openWakeWord activation threshold; 0.5 == the
    # data-driven PRODUCTION_WAKE_THRESHOLD default (BUG-009 floor preserved).
    sensitivity: float = 0.5
    # STT transcript-match tolerance for transcription drift (engine="stt_match").
    fuzzy_match_ratio: float = 0.8
    # --- Deprecated porcupine-era keys (never wired). Kept so an old
    # jarvis.toml still validates cleanly; the active fields are phrase/engine.
    provider: str = "openwakeword"
    keyword: str = "jarvis"
    custom_keyword_file: str = ""

    @field_validator("engine", mode="before")
    @classmethod
    def _coerce_engine(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in WAKE_ENGINES else "auto"


class TriggerConfig(BaseModel):
    wake_word_enabled: bool = False
    hotkey: str = "ctrl+right_alt+j"
    # Call/answer toggle key. Was hardcoded "f3+f4" in resolve_hotkeys() and at
    # the SpeechPipeline call sites; now user-editable via /api/settings/keybinds.
    hotkey_call: str = "f3+f4"
    # Hangup key. Was hardcoded ("f1+f2",) at the SpeechPipeline call sites; now
    # user-editable via /api/settings/keybinds. Read directly at bootstrap.
    hotkey_hangup: str = "f1+f2"
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    # When true (default), every voice turn ends after Jarvis finishes
    # speaking and a fresh "Hey Jarvis" wake is required to start the
    # next turn. When false, the pipeline keeps the mic open after the
    # response (legacy conversation mode introduced 2026-05-05) and only
    # hangs up via HANGUP_RE, the idle timeout, or a hotkey. User mandate
    # 2026-05-18: single-turn is the canonical behaviour — open-mic mode
    # made Jarvis trigger on every word in the room.
    single_turn_mode: bool = True
    # When True (default, user mandate 2026-05-29), the configured ``hotkey`` is
    # a true push-to-talk key: holding it records, releasing it submits the
    # captured audio as one prompt (one-shot — Jarvis answers once, then the
    # session ends; the next prompt needs another hold). The VAD silence
    # endpoint is bypassed for the duration of the hold, so a thinking pause
    # never cuts the user off mid-sentence. When False, the hotkey falls back to
    # the legacy toggle: a single press starts a normal wake-style session whose
    # end is decided by the VAD / idle timeout (the pre-2026-05-29 behaviour).
    # The F3+F4 chord always stays a toggle regardless of this flag.
    push_to_talk: bool = True

    def resolve_hotkeys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split the configured hotkeys into ``(call_hotkeys, ptt_hotkeys)``
        for ``SpeechPipeline``.

        With ``push_to_talk`` on (default), the configured ``hotkey`` becomes a
        true push-to-talk key (hold = record, release = submit) and ``hotkey_call``
        stays a quick wake-style toggle. With it off, ``hotkey`` is a toggle
        alongside ``hotkey_call`` and there is no PTT (the pre-2026-05-29 wiring).
        Hangup is a separate value read from ``hotkey_hangup`` at the
        SpeechPipeline call sites.
        """
        if self.push_to_talk:
            return (self.hotkey_call,), (self.hotkey,)
        return (self.hotkey, self.hotkey_call), ()
    # When False (default), the local wake path is lightweight: openWakeWord
    # only (~3.5 MB ONNX, CPU-only, bundled in jarvis/assets/wakeword/), no
    # faster-whisper anywhere — no GPU, no ~1 GB model download. When True, the
    # heavy RollingWhisperWake low-volume backstop + the faster-whisper VAD
    # stability probe are enabled as an opt-in power-user extra (needs a local
    # faster-whisper install; see docs/local-wakeword/RESEARCH-AND-DESIGN.md).
    heavy_local_whisper: bool = False
    # When True (default), a fast OpenWakeWord hit is treated as a *candidate*
    # only — the wake loop transcribes the few seconds preceding the hit with
    # the cloud STT used for utterance turns and requires a strict
    # "hey/hi/hallo + jarv" pattern before activating. This eliminates the
    # bare-"Jarvis" false fires that the neural OWW model produces without
    # pendulumming its activation threshold (BUG-009 floor stays intact).
    # Set False to restore the legacy raw-OWW behaviour.
    require_hey_prefix: bool = True


class STTConfig(BaseModel):
    provider: str = "groq-api"
    # NOTE: ``model`` is consumed by the local FasterWhisperProvider for the
    # Wake-Detector's rolling-whisper instance even when the post-wake STT
    # provider is set to a cloud one. Must remain a faster-whisper-compatible
    # name (see faster_whisper/utils.py for the allowlist). The Groq plugin
    # hardcodes its own model name internally.
    model: str = "distil-large-v3"
    # Cloud-first default: "cpu". A fresh clone on a VPS or a laptop must never
    # assume a local GPU. Set to "cuda" in jarvis.toml on a CUDA box; the local
    # faster-whisper path also tolerates "cuda" with a no-CUDA runtime fallback.
    device: str = "cpu"
    compute_type: str = "int8_float16"
    language: str = "auto"
    # Vocabulary biasing passed to Whisper's ``prompt`` field — the same
    # mechanism dictation tools like Wispr Flow use to keep proper nouns and
    # domain terms stable. Empty string means "no bias", and the cloud STT
    # plugin caps overly-long values internally. Read by the cloud STT
    # plugins (currently Groq); the local FasterWhisperProvider intentionally
    # ignores it because an initial-prompt on silent audio used to
    # hallucinate the prompt itself as the transcript.
    bias_prompt: str = ""


class TTSConfig(BaseModel):
    # extra="allow" lets per-provider sub-tables like [tts.cartesia] survive
    # the Pydantic round-trip (AP-16 — without it Pydantic silently drops
    # unknown keys and self-mod boots fail). Cartesia reads its sub-table via
    # ``tts_cfg.model_extra.get("cartesia", {})`` in the factory.
    model_config = {"extra": "allow"}

    provider: str = "gemini-flash-tts"
    model: str | None = None
    voice_de: str = "Charon"
    voice_en: str = "Charon"
    language_code: str = "de-DE"
    style_prompt: str | None = None
    voice_auto_switch: bool = True
    speed: float = 1.0
    streaming: bool = True
    # ElevenLabs-specific VoiceSettings (ignored by other providers).
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    # SAPI5 (Windows native robotic TTS) is only an emergency brake.
    # Default `false` prevents the previous silent-fallback bug where a
    # Gemini/Grok/ElevenLabs failure would silently switch to the Windows voice.
    # Set to `true` to guarantee audio output even on a total quota/auth
    # failure — robotic voice is then accepted.
    allow_sapi5_fallback: bool = False
    # Voice-consistency knobs for generative TTS (Gemini). The generative model
    # re-improvises delivery on every call, so the perceived voice drifts.
    # `chunk_by_sentence=False` makes a whole utterance one generation (no
    # mid-answer shift); `seed` pins the RNG so identical text renders the same
    # run-to-run; `temperature` lowers prosody variance. Defaults preserve the
    # historical behaviour; only Gemini reads them today.
    chunk_by_sentence: bool = True
    seed: int | None = None
    temperature: float | None = None
    # Vertex AI path (2026-05-26). When ``use_vertex=True`` the Gemini Flash
    # TTS plugin builds a ``genai.Client(vertexai=True, project=..., location=...)``
    # instead of going through Google AI Studio with a GOOGLE_API_KEY. The
    # motivation is the AI-Studio Preview-Model RPD cap (100 requests/day on
    # ``gemini-3.1-flash-tts-preview``, independent of Pay-as-you-go billing)
    # which forced a daily mid-session Sibling-Bridge switch to
    # ``gemini-2.5-flash-preview-tts`` and broke the user-mandated single-
    # voice contract (Charon). Vertex AI on a paid project does not have the
    # Preview cap, so the bridge fallback should never trigger. Auth uses a
    # service-account JSON exported via ``GOOGLE_APPLICATION_CREDENTIALS`` —
    # not an API key. ``service_account_path`` is optional; when set the
    # plugin exports it into the env before constructing the client so the
    # Cloud SDK auth chain picks it up.
    use_vertex: bool = False
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    service_account_path: str | None = None


class BrainProviderConfig(BaseModel):
    model: str | None = None
    deep_model: str | None = None      # Optional: stronger reasoning model
    auth_mode: str | None = None       # "oauth" | "api_key"
    base_url: str | None = None
    # Latency sprint 1 (2026-04-30): Gemini thinking budget per provider tier.
    # Value is forwarded to ``types.ThinkingConfig.thinking_budget``.
    # ``None``  → SDK default (auto-budget, highest latency footprint).
    # ``0``     → thinking disabled (e.g. router tier — pure tool routing
    #             needs no reasoning).
    # ``-1``    → dynamic-auto (provider decides per request).
    # ``> 0``   → fixed token cap for the thinking portion.
    # Currently only evaluated by ``GeminiBrain``; other providers ignore it.
    thinking_budget: int | None = None

    model_config = {"extra": "allow"}  # allows unknown TOML keys


class BrainPolicyConfig(BaseModel):
    use_routing_model_for_intent: bool = True
    use_realtime_for_smalltalk: bool = False
    prompt_cache_heartbeat_seconds: int = 240
    voice_switch_patterns: list[str] = Field(
        default_factory=lambda: ["wechsel auf", "switch to", "wechsle zu"]
    )


class BrainRouterPolicyConfig(BaseModel):
    """Policy switches for the tier router (Phase 5)."""
    escalate_on_uncertainty: bool = True
    default_intent_on_low_confidence: str = "spawn_worker"

    model_config = {"extra": "allow"}


class BrainPlausibilityConfig(BaseModel):
    """Plausibility thresholds for the tool-execution guard (Phase 4).

    From the persona mandate: before every tool execution with
    ``risk_tier ∈ {ask, monitor}``, ``check_plausibility`` evaluates two signals:

    - Whisper confidence for the current turn (``Transcript.confidence``).
      Values ``< confidence_threshold`` count as uncertain.
    - Wake age (seconds since the last wake-word trigger). Values
      ``> stale_wake_seconds`` count as stale.

    When uncertain OR stale:
      - ``ask`` tier: ``require_confirmation=True`` (additional voice
        confirmation required)
      - ``monitor`` tier: log warning only, no block

    Plausibility is NOT a risk tier. Whitelist-downgraded tools (``safe``)
    continue without a plausibility check — otherwise the whitelist is pointless.
    """
    model_config = {"extra": "allow"}

    confidence_threshold: float = 0.5
    stale_wake_seconds: float = 30.0


class BrainRoutingConfig(BaseModel):
    """Heuristic rules for the deterministic force-spawn classification.

    Persona mandate Phase 3: main Jarvis is a pure dispatcher. When this
    heuristic triggers, ``spawn_worker`` is called deterministically without
    an LLM tool choice — the user utterance is passed verbatim to the OpenClaw
    bridge (Wave-4 migration: previously the sub-Jarvis tier).

    Defaults are chosen so that smalltalk (hello/thanks/how's it going) NEVER
    triggers, while action verbs (lies/baue/installiere/oeffne/mach/zeig)
    plus external system markers (PR, Issue, Repo, GitHub) ALWAYS trigger.

    Fields are compiled into regex patterns in
    ``jarvis.brain.manager._build_force_spawn_re``.
    """
    model_config = {"extra": "allow"}

    # Action verbs (DE + EN). Matched with ``\b...\w*\b`` boundaries
    # — conjugations (lies/lest/liest) are therefore caught automatically.
    spawn_verbs: list[str] = Field(default_factory=lambda: [
        # Repair/implementation (old _FORCE_SPAWN_RE list)
        "umsetz", "reparier", "fix", "behebe", "korrigier",
        "implementier", "entwickel", "refactor", "debug", "repair",
        # File/system action (persona mandate Phase 3)
        "lies", "lese", "liest", "schreib", "schreibe", "schreibt",
        "bau", "baue", "baut", "oeffne", "öffne", "oeffnet", "öffnet",
        "installier", "deinstallier", "deploy",
        "zeig", "zeige", "zeigt",
        "mach", "mache", "macht", "machen",
        # English
        "read", "write", "build", "open", "install", "show", "make",
        # Spawn imperatives (Bug 2026-04-29: user says "Spawn sub-agents." —
        # heuristic fell back to the LLM without a match, which replied with
        # smalltalk. List "spawn" and conjugations explicitly.)
        "spawn", "starte", "start", "starten", "startet",
        "delegier", "delegier",
    ])

    # External system markers — when the utterance mentions a repo/PR/issue,
    # we spawn even without a clear action verb (e.g. "How many PRs are open?").
    external_system_markers: list[str] = Field(default_factory=lambda: [
        "pr", "prs", "issue", "issues", "repo", "repository",
        "github", "gitlab", "branch",
    ])

    # Force-Spawn-Phrases (User-Mandate 2026-05-14): explicit-only trigger
    # list. When `force_spawn_mode = "strict"` (default), ONLY these phrases
    # cause a spawn — everything else stays inline in the router brain.
    # Earlier behaviour (every spawn_verb hit = spawn) was too eager and
    # spawned heavy workers for trivial knowledge questions like
    # "Was ist ein Verbrenner-Motor?". The list captures the user's actual
    # signals for "I want a heavy worker, not a one-shot answer":
    # explicit OpenClaw / sub-agent mentions plus deep-research markers.
    force_spawn_phrases: list[str] = Field(default_factory=lambda: [
        # Explicit OpenClaw / sub-agent mentions
        "openclaw", "open claw", "open-claw",
        "subagent", "subagenten", "sub-agent", "sub-agenten", "sub agent",
        "spawne", "spawn", "spawnen", "spawnt", "gespawnt",
        "delegier", "delegiere", "delegierst", "delegiert", "delegieren",
        "delegate", "delegates",
        # Deep-work markers (declined forms included so partial matches
        # like "umfassenden Bericht" hit reliably — \b boundaries don't
        # forgive German case endings)
        "deep dive", "deep-dive", "deepdive",
        "deep research", "deep-research", "deepresearch",
        "tiefenrecherche", "tiefen-recherche",
        "gruendliche", "gruendlicher", "gruendlichen", "gruendliches",
        "gründliche", "gründlicher", "gründlichen", "gründliches",
        "gruendlich", "gründlich",
        "ausfuehrliche", "ausfuehrlicher", "ausfuehrlichen", "ausfuehrliches",
        "ausführliche", "ausführlicher", "ausführlichen", "ausführliches",
        "ausfuehrlich", "ausführlich",
        "umfassende", "umfassender", "umfassenden", "umfassendes",
        "umfassend",
        "kompletter deep", "kompletten deep", "komplette analyse",
        "vollstaendige analyse", "vollständige analyse",
    ])

    # Force-Spawn-Mode: "strict" honours only `force_spawn_phrases`,
    # "permissive" falls back to the legacy spawn_verbs + external markers
    # heuristic. Default is "strict" per user mandate 2026-05-14.
    force_spawn_mode: str = "strict"

    # Smalltalk allowlist — when the utterance matches one of these patterns,
    # NEVER spawn, even if the verb or marker heuristic fires. Pure wake/
    # smalltalk inputs go straight through the brain, not via OpenClaw spawn.
    smalltalk_allowlist: list[str] = Field(default_factory=lambda: [
        # Greetings / Hangup
        "hallo", "hi", "hey", "moin", "guten morgen", "guten abend",
        "auf wiedersehen", "tschuess", "tschüss", "bye",
        "goodbye", "good morning", "good evening",
        # Smalltalk
        "wie geht", "how are you", "how's it going",
        "was machst du", "was machen wir",  # neutralise "mach" as a verb trigger
        "danke", "thank you", "thanks",
        # Factual question from memory
        "wie spaet", "wie spät", "what time",
        "welcher tag", "what day",
        "hauptstadt", "capital",
    ])


class RouterVisionConfig(BaseModel):
    """Config for permanent vision in main Jarvis (RouterBrain).

    Wave-1 B4 — additive to `[brain.router]` / `[brain.router.policy]`.
    Controls the continuous screenshot feed that the router receives as context.
    All fields have defaults: existing configs without this section load cleanly.
    """
    enabled: bool = True
    refresh_interval_s: float = 2.0
    max_staleness_s: float = 2.0
    capture_mode: str = "screenshot"      # "screenshot" | "composite"
    max_image_kb: int = 500
    pause_on_idle: bool = True
    voice_pause_phrase_de: str = "privacy"
    voice_pause_phrase_en: str = "privacy mode"
    voice_resume_phrase_de: str = "du darfst wieder sehen"
    voice_resume_phrase_en: str = "vision back on"

    model_config = {"extra": "allow"}


class BrainTierConfig(BaseModel):
    """Tier-specific brain configuration.

    ``model`` and ``fallback_model`` may be left empty — in that case
    ``jarvis.brain.manager._resolve_tier_model`` pulls the default model for
    the chosen provider from ``TIER_DEFAULTS_BY_PROVIDER``. This allows a
    provider switch (``[brain.router].provider = "gemini"``) without
    also editing the model field.
    """
    model_config = ConfigDict(extra="allow")

    provider: str
    model: str | None = None   # CHANGED — war: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_provider_2: str | None = None
    fallback_model_2: str | None = None
    # Relevant only for the router tier.
    policy: BrainRouterPolicyConfig | None = None
    # Permanent vision (Wave-1 B4). Semantically used only for the router tier.
    vision: RouterVisionConfig = Field(default_factory=RouterVisionConfig)


class BrainConfig(BaseModel):
    primary: str = "claude-api"
    # For deep/code intents an API-key provider can be preferred.
    deep_brain: str | None = None
    routing_provider: str = "claude-api"
    routing_model: str = "claude-sonnet-4-6"
    local_fallback: str = "claude-api"
    local_fallback_model: str = "claude-haiku-4-5-20251001"
    providers: dict[str, BrainProviderConfig] = Field(default_factory=dict)
    policy: BrainPolicyConfig = Field(default_factory=BrainPolicyConfig)
    # Per-response output ceiling (tokens) for every spoken/chat reply. This is
    # a SAFETY CEILING, not a target: the model still stops on its own
    # (``finish_reason == "stop"``), so a short question keeps its short answer.
    # The ceiling only bites a genuinely long answer — without it the provider
    # stops at the cap and the reply is read aloud truncated mid-sentence (the
    # voice path sets no continuation). Raised 4096 -> 8192 on 2026-06-01 after
    # a live cut-off report; kept configurable so an operator can trade speech
    # length against latency/cost. ~8192 tokens ≈ several minutes of speech.
    max_tokens: int = Field(default=8192, ge=256, le=32_768)
    # Phase 5 tiered routing — Wave-4 migration: the ``sub_jarvis`` tier was
    # replaced by the OpenClaw bridge (see docs/openclaw-bridge.md §11).
    # Only ``router`` remains as a tier; the heavy worker runs as an external
    # subprocess via Mission Manager. The ``sub_jarvis`` field is kept with a
    # default of ``None`` so that old ``[brain.sub_jarvis]`` blocks in
    # jarvis.toml do not cause a Pydantic validation error — the values are
    # ignored regardless.
    router: BrainTierConfig | None = None
    sub_jarvis: BrainTierConfig | None = None  # legacy, ignored post-Wave-4
    # User-facing reply language pin (desktop "Languages" view → Reply Language).
    # "auto" mirrors the user's input language (DE/EN/ES); "de"/"en"/"es" force
    # that language as a hard rule for every Jarvis reply. Consumed by
    # ``BrainManager._reply_language_directive``. Persisted via
    # ``config_writer.set_reply_language``.
    reply_language: str = "auto"
    # Persona mandate Phase 3: deterministic spawn heuristic for the router.
    routing: BrainRoutingConfig = Field(default_factory=BrainRoutingConfig)
    # Persona mandate Phase 4: plausibility thresholds for tool execution.
    plausibility: BrainPlausibilityConfig = Field(
        default_factory=BrainPlausibilityConfig,
    )
    healthcheck_on_start: bool = True


class WikiCuratorConfig(BaseModel):
    """Curator LLM settings for the long-term wiki memory (Phase B1).

    The curator turns one new source (a BrainTurnCompleted summary, an
    EpisodeRecorded entry, a MissionCompleted hand-off) into a small set
    of structured wiki page updates. The LLM is intentionally provider-
    agnostic: ``provider=""`` falls back to ``brain.primary`` and
    ``model=""`` falls back to the resolved provider's ``model`` field
    under ``brain.providers``. Pattern mirrors
    ``AwarenessVerdichterConfig`` (Plan §6).
    """

    model_config = ConfigDict(extra="allow")

    provider: str = ""                  # "" = fall back to brain.primary
    model: str = ""                     # "" = provider default model
    max_input_tokens: int = 8000
    max_output_tokens: int = 2000
    timeout_s: float = 90.0


class SessionRollupConfig(BaseModel):
    """Session-rollup worker settings (Phase B7, mid-term memory tier).

    The rollup worker watches the awareness ``IdleEntered`` event stream
    and turns the L2 episodes of a single work session into one
    Markdown digest under ``data/workspace/sessions/<date>-<id>.md``.
    Provider resolution follows ``WikiCuratorConfig`` — empty fields
    fall back to ``brain.primary`` and the provider's default model.

    Trigger thresholds:

    ``session_idle_threshold_minutes``
        How long idle must be before the worker treats it as session-end.
        Default 120 minutes (2 hours) — bridges short lunch breaks
        without flushing, captures end-of-day naturally.

    ``min_episodes_for_rollup``
        Skip the LLM call when there are fewer episodes — a one-episode
        "session" rarely justifies a digest.

    ``max_active_sessions``
        Rolling window cap. Sessions older than this number get moved
        to ``data/workspace/_archive/sessions/``. Default 5 per the plan.

    ``timeout_s``
        Outer ``asyncio.wait_for`` cap on the brain call.

    ``user_entity_slug``
        Slug of the user's own entity page (schema default ``alex``).
        Every session page links it in the ``## Related`` backbone footer
        when the page exists, so each session is wired into the graph
        through the shared user hub instead of floating as an island.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    provider: str = ""
    model: str = ""
    session_idle_threshold_minutes: int = 120
    min_episodes_for_rollup: int = 2
    max_active_sessions: int = 5
    max_output_tokens: int = 600
    timeout_s: float = 30.0
    user_entity_slug: str = "alex"


class SchedulerConfig(BaseModel):
    """Settings for ``CuratorScheduler`` (Phase B5 — Agent D).

    Controls the cooldown window, the optional periodic-run gate, the
    lock-file location, and the stale-lock threshold.

    The defaults are deliberately conservative — periodic runs are
    disabled by default so the system stays quiet unless explicitly
    opted in.

    ``lock_path`` must NOT live inside the Obsidian vault directory
    (``wiki/obsidian-vault/``); that path is watched by Obsidian and a
    lock file there would create noise in the sidebar.  The default
    ``data/wiki_curator.lock`` is gitignored.
    """

    model_config = ConfigDict(extra="allow")

    cooldown_seconds: int = 60
    enable_periodic: bool = False
    periodic_interval_minutes: int = 30
    lock_path: Path = Path("data/wiki_curator.lock")
    lock_stale_after_seconds: int = 300


class VoiceBridgeConfig(BaseModel):
    """``VoiceFactBridge`` settings (Phase B8 — aggressive-ingest mode).

    The bridge has two paths from voice turn -> wiki:

    * **Ack path** (always on): ingest when the brain reply contains an
      explicit "notiert" / "vermerkt" / ... keyword. Narrow, false-positive
      free.
    * **Aggressive path** (this section's toggle): every user turn with
      at least ``min_user_chars`` characters is handed to the curator
      regardless of how the brain replied. The curator's prompt is the
      salience filter -- smalltalk returns an empty list, facts produce
      pages.

    The aggressive path is the safety net for the case "user states a
    fact, brain replies conversationally without an ack-keyword". B1 §3.8
    planned this but never activated it; this section turns it on by
    default.

    Rate-limit: at most one aggressive ingest per
    ``rate_limit_seconds`` window. Prevents an LLM call on every single
    voice turn while staying responsive enough for normal conversation
    pacing.
    """

    model_config = ConfigDict(extra="allow")

    aggressive_mode: bool = True
    min_user_chars: int = 30
    rate_limit_seconds: int = 60


class WikiMemoryConfig(BaseModel):
    """Root of the ``[memory.wiki]`` block (Phase B1+B7+B8).

    Holds the Curator LLM section (B1), the session-rollup section (B7),
    and the voice-bridge section (B8 aggressive ingest). Defaults are
    chosen so a config without the section loads cleanly as
    ``WikiMemoryConfig()``.
    """

    model_config = ConfigDict(extra="allow")

    curator: WikiCuratorConfig = Field(default_factory=WikiCuratorConfig)
    session_rollup: SessionRollupConfig = Field(default_factory=SessionRollupConfig)
    voice_bridge: VoiceBridgeConfig = Field(default_factory=VoiceBridgeConfig)


class LegacyCuratorConfig(BaseModel):
    """B4 Soft-Disable gate (2026-05-17).

    The Phase 0-2 :class:`jarvis.memory.curator.Curator` writes facts to
    ``data/workspace/{USER.md,SOUL.md,people/*.md}``.  Since the Phase B1
    :class:`jarvis.memory.wiki.curator.WikiCurator` took over (writing to
    ``wiki/obsidian-vault/``), the two systems coexist — which means two
    notebooks that the brain has to reason about.  This flag stops the
    legacy writer without deleting the package; the legacy files stay on
    disk as a frozen snapshot, the 35 reader sites keep working against
    the last-known state.  Set ``enabled = true`` to bring it back if
    anything regresses.

    Pinned in ``scripts/config-soll.json`` so the drift-guard does not
    silently re-enable it.
    """

    enabled: bool = False


class MemoryConfig(BaseModel):
    recall_store: str = "sqlite"
    archival_store: str = "chroma"
    embedding_model: str = "bge-m3"
    retention_days_recall: int = 90
    data_dir: str = "./data"
    wiki: WikiMemoryConfig = Field(default_factory=WikiMemoryConfig)
    legacy_curator: LegacyCuratorConfig = Field(default_factory=LegacyCuratorConfig)


class SafetyWhitelistConfig(BaseModel):
    commands: list[str] = Field(default_factory=list)


class SafetyBlacklistConfig(BaseModel):
    commands: list[str] = Field(default_factory=list)


class SafetyConfig(BaseModel):
    default_tier: RiskTier = "safe"
    always_confirm_tiers: list[RiskTier] = Field(default_factory=lambda: ["ask"])
    always_block_tiers: list[RiskTier] = Field(default_factory=lambda: ["block"])
    whitelist: SafetyWhitelistConfig = Field(default_factory=SafetyWhitelistConfig)
    blacklist: SafetyBlacklistConfig = Field(default_factory=SafetyBlacklistConfig)


class OpenClawNotificationConfig(BaseModel):
    """Notification behaviour of the OpenClaw bridge (bridge docs §4.2).

    Mandate AD-17: the bridge pipes ``summary_de`` from the Kontrollierer
    signature into the existing ``_on_announcement`` bus (pipeline.py:647).
    Voice readback only when voice is currently listening; toast always.
    """
    model_config = ConfigDict(extra="forbid")

    # Default is the bus bypass — see pipeline._on_announcement.
    via: str = "announcement_bus"
    toast: bool = True
    voice_when_active: bool = True


class OpenClawConfig(BaseModel):
    """Top-level ``[harness.openclaw]`` config for the OpenClaw bridge.

    Schema matches ``docs/openclaw-bridge.md §4.2`` post-Wave-1
    (with AD-22..AD-24 findings incorporated). Wave 2 delivers only
    the schema + default block in jarvis.toml; Wave 3 wires the bridge.

    Deliberately NO Anthropic lock in the ``model`` default: an empty ``model``
    means the bridge resolves the frontier-pro of the active Personal Jarvis
    provider (``cfg.brain.primary``) via the provider-slug mapping from AD-6
    (gemini→google/gemini-..., claude-api→anthropic/..., grok→xai/...).
    This way OpenClaw automatically follows the user's provider choice.

    AD-21 pin-version mandate: ``version`` must be set whenever the block
    exists at all — a Pydantic required field, no default. Guards against
    silent upstream drifts. Loading without the block (``HarnessConfig.
    openclaw is None``) falls back to "bridge inactive".
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # AD-21: pin to empirically tested upstream version. NO default —
    # empty would mean "whatever npm i -g installs", which makes bridge
    # tests worthless.
    version: str
    # On PATH or absolute path. Default matches "npm i -g openclaw"
    # (wrapper in the NPM global bin folder).
    binary_path: str = "openclaw"
    # Empty = bridge resolves frontier-pro from cfg.brain.primary (AD-6).
    # Explicitly set e.g. "anthropic/claude-opus-4-7" or
    # "google/gemini-3.1-pro-preview" to pin the model statically.
    model: str | None = None
    # Time-cap fixed per AD-19; per-mission override deliberately not allowed.
    time_cap_min: int = Field(default=30, ge=1, le=240)
    # Up to N OpenClaw missions in parallel; the fourth lands in the queue (AD-13).
    concurrency: int = Field(default=3, ge=1, le=10)
    # AD-20 reserved for v2 cost-cap retrofit. v1 None = no cap.
    cost_cap_eur: float | None = Field(default=None, ge=0.0)
    # AD-23: workspace isolation per mission. The bridge creates
    # ``<mission_id>/openclaw_state/`` underneath and sets MISSION_STATE_DIR
    # to it so cross-mission state and persona defaults from
    # ~/.openclaw/workspace/ cannot leak (AP-OC15).
    state_dir_root: str = "data/openclaw_state"

    notification: OpenClawNotificationConfig = Field(
        default_factory=OpenClawNotificationConfig,
    )


class HarnessConfig(BaseModel):
    """Config for the harness dispatcher (Phase 4)."""
    enabled: list[str] = Field(
        default_factory=lambda: ["python-script", "mcp-remote"]
    )
    default_timeout_s: int = 600
    default_risk_tier: RiskTier = "monitor"
    # Output limit per harness turn back to the brain — prevents large
    # build logs from blowing the context window.
    max_output_chars: int = 4000
    # Per-harness overrides: e.g. {"openclaw": {"model": "opus", "max_turns": 10}}
    per_harness: dict[str, dict[str, object]] = Field(default_factory=dict)
    # OpenClaw bridge (Wave 2). None = block missing in jarvis.toml,
    # bridge stays inactive. When the block is present, ``version`` is required.
    openclaw: OpenClawConfig | None = None


class MCPServerConfig(BaseModel):
    """Config for Jarvis-as-MCP-server (Phase 4)."""
    enabled: bool = True
    transport: str = "stdio"             # "stdio" | "http"
    http_host: str = "127.0.0.1"
    http_port: int = 47822
    auth_token_env: str = "JARVIS_MCP_TOKEN"
    max_call_depth: int = 3              # loop guard


class AudioConfig(BaseModel):
    input_device: str = "auto-headset"
    output_device: str = "auto-headset"
    echo_cancellation: bool = True
    sample_rate: int = 16000
    frame_ms: int = 10


class UIConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    tray_enabled: bool = True
    admin_api_port: int = 47821
    startup_chime: bool = True
    # Dev mode: the frontend is not mounted from frontend/dist/ but loaded from
    # a running Vite dev server (HMR). Activated via ENV JARVIS_DEV=1 or CLI
    # --dev; the fields here simply hold the parameters.
    dev_mode: bool = False
    vite_dev_url: str = "http://localhost:5173"
    # ENV variable that provides the session token for the WebView.
    # Default: JARVIS_UI_TOKEN. The token is freshly generated at startup
    # and pywebview injects it via evaluate_js into window.__JARVIS_TOKEN.
    auth_token_env: str = "JARVIS_UI_TOKEN"
    # On-screen overlay style: "whisper_bar" (slim default), "mascot" (ghost
    # orb), or "none". The mascot remains fully selectable.
    orb_style: str = "whisper_bar"
    # Optional explicit path to the mascot PNG. Empty = search for default asset.
    orb_mascot_path: str = ""
    # Whisper bar: persistent (always-visible dots pill) vs only-when-active.
    bar_persistent: bool = True
    # Hex accent the bar lights up with during activity (gold on-brand).
    bar_accent: str = "#e7c46e"


class DuckingConfig(BaseModel):
    """Audio ducking — "Mute music while dictating" (Taskbar section).

    When ``enabled``, the audio-duck controller mutes every OTHER app's audio
    session for the duration of a voice session (excluding Jarvis's own PID, so
    the TTS voice is never muted) and restores them when the session ends.
    Windows-only (pycaw); a graceful no-op elsewhere. Default off (opt-in).
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    # Grace before restoring other apps' volume (lets the TTS tail finish).
    restore_delay_ms: int = 400
    # App process names never to mute (e.g. "Discord.exe"). Empty = mute all others.
    never_mute: list[str] = Field(default_factory=list)


class AutostartConfig(BaseModel):
    """Cross-platform login autostart (the 7th cross-platform port).

    ``enabled`` defaults to False (cloud-first / least-surprise): a fresh install
    must not register login autostart without the user opting in — via the setup
    wizard, the Settings toggle, or an explicit ``[autostart] enabled = true`` in
    jarvis.toml. On a headless host (no display) the autostart manager is a
    graceful no-op anyway.

    ``extra="allow"`` so a future ``[autostart.*]`` sub-key — or a self-mod /
    drift-guard write of an as-yet-unknown field — never trips pre-validate
    (AP-16). Spec: docs/superpowers/specs/2026-05-30-cross-platform-autostart-design.md
    """

    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    # Windows shortcut WindowStyle hint (7 = minimized/tray-friendly); other OSes
    # ignore it.
    start_minimized: bool = True


class TelemetryConfig(BaseModel):
    # extra="allow" so a future [telemetry.*] sub-key never trips the self-mod
    # pre-validate round-trip (AP-16), consistent with the other config models.
    model_config = ConfigDict(extra="allow")
    flight_recorder: bool = True
    # Auto-delete captured screenshot blobs (data/flight_recorder/blobs/) older
    # than this many days. Jarvis captures screenshots for in-session context;
    # they are throwaway afterwards and otherwise grow without bound. ``0``
    # disables retention (keep forever). See jarvis/telemetry/retention.py.
    flight_recorder_retention_days: int = 10
    otel_endpoint: str = ""
    metrics_port: int = 9090
    log_level: str = "INFO"


class SubAgentsConfig(BaseModel):
    """Config for sub-agent output management, GitHub push, and verification."""
    github_auto_push: bool = False
    github_repo_url: str = ""
    max_verification_iterations: int = 3
    output_dir_mirror_desktop: bool = True


class SecurityConfig(BaseModel):
    """Gate for sensitive UI actions (e.g. built-in skill editing).

    Empty hash = no admin mode set — built-in edits are locked.
    To set: write the SHA-256 hex of the password into ``admin_password_hash``,
    e.g. via ``python -c "import hashlib; print(hashlib.sha256(b'<pass>').hexdigest())"``.
    """
    admin_password_hash: str = ""


class TelegramConfig(BaseModel):
    """Telegram integration: workflow notifications + bidirectional chat channel.

    The bot token lives in the Credential Manager (key ``telegram_bot_token``,
    ENV fallback ``TELEGRAM_BOT_TOKEN``) — never in the config file.

    Setup steps for the user:
      1. Message ``@BotFather`` in Telegram → ``/newbot`` → receive the token.
      2. ``python -m jarvis --wizard`` — stores the token in the Credential Manager.
      3. Send ``/start`` to the bot — the wizard whitelists the user ID.
      4. Set ``enabled = true`` in this config.

    Security default: ``allowed_user_ids = []`` and ``group_policy =
    "allowlist"`` means the bot replies to nothing until you explicitly
    allow user IDs or chat IDs.
    """

    # Notification mode (compatible with pre-Friends).
    chat_id: str = ""
    parse_mode: str = "Markdown"

    # Channel adapter mode (Friends F1).
    enabled: bool = False
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_chat_ids: list[int] = Field(default_factory=list)
    group_policy: str = "allowlist"  # "open" | "allowlist" | "disabled"
    require_mention: bool = True
    polling_interval_s: float = 1.0
    auto_register_friends: bool = False
    # Marketplace connect cannot know the user's Telegram ID. On the first
    # private message, an otherwise empty allowlist is claimed by that sender
    # and persisted to jarvis.toml.
    pair_on_first_private_message: bool = True


class TwilioConfig(BaseModel):
    """Twilio telephony integration: call a phone number and talk to Jarvis.

    The caller dials a Twilio number; Twilio bridges the call audio to Jarvis
    over Media Streams (raw audio over a WebSocket) so Jarvis can run its OWN
    STT -> Brain -> TTS stack and answer in its OWN Charon voice — identical to
    the "Hey Jarvis" microphone path (design spec AD-T1/AD-T2).

    The Twilio Auth Token is a SECRET and lives in the Credential Manager
    (key ``twilio_auth_token``, ENV fallback ``TWILIO_AUTH_TOKEN``) — never in
    this config file. The Account SID is an account identifier (not a secret),
    so it is fine to keep here.

    Setup steps for the user:
      1. Create a Twilio account, buy a voice-capable phone number.
      2. ``python -m jarvis --wizard`` — stores the Auth Token in the
         Credential Manager.
      3. Set ``account_sid``, ``phone_number`` and ``public_base_url`` (the
         HTTPS URL Twilio can reach — a VPS domain or a tunnel).
      4. Point the number's Voice webhook at
         ``{public_base_url}/api/telephony/voice`` (or run
         ``scripts/telephony_provision.py``).
      5. Set ``enabled = true``.

    ``fallback_mode`` is reserved: ``"media"`` is the v1 raw-audio path;
    ``"conversationrelay"`` is a future degraded fallback (Twilio TTS voices)
    and is out of scope for v1.
    """

    enabled: bool = False
    account_sid: str = ""           # AC... (account identifier, not a secret)
    phone_number: str = ""          # E.164, e.g. +49...
    public_base_url: str = ""       # https://jarvis.example.com (no trailing slash)
    greeting: str = ""              # optional spoken welcome; empty = butler default
    language_code: str = "de-DE"    # default TTS/STT language hint
    fallback_mode: str = "media"    # reserved: "media" (v1) | "conversationrelay"
    max_call_seconds: int = 600     # safety cap to end runaway calls


class DiscordConfig(BaseModel):
    """Discord integration: bidirectional chat channel via a Discord bot.

    Like Telegram, Discord is a *communication channel*: a user messages the
    bot (DM or a guild channel) and the message is forwarded into the normal
    Jarvis chat path — chatting with the bot is the same as prompting Jarvis.

    The bot token lives in the Credential Manager (key ``discord_bot_token``,
    ENV fallback ``DISCORD_BOT_TOKEN``) — never in this config file.

    Setup steps for the user:
      1. Create an application + bot at https://discord.com/developers/applications.
      2. Enable the **Message Content Intent** (Bot → Privileged Gateway
         Intents) — without it the bot cannot read message text.
      3. ``python -m jarvis --wizard`` — stores the bot token in the
         Credential Manager.
      4. Invite the bot to a server, or open a DM with it.
      5. Set ``enabled = true`` in this config.

    Security default: ``allowed_user_ids = []`` with ``guild_policy =
    "allowlist"`` means the bot replies to nothing until you explicitly allow a
    user id or channel id. ``pair_on_first_dm`` claims the empty allowlist for
    the first direct-message sender so the common "invite + DM" setup is not
    silently dropped.
    """

    enabled: bool = False
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_channel_ids: list[int] = Field(default_factory=list)
    guild_policy: str = "allowlist"  # "open" | "allowlist" | "disabled"
    require_mention: bool = True
    auto_register_friends: bool = False
    pair_on_first_dm: bool = True


class IntegrationsConfig(BaseModel):
    """External service integrations (Telegram, Discord, WhatsApp, Twilio, ...)."""

    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    twilio: TwilioConfig = Field(default_factory=TwilioConfig)


CHANNEL_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "telegram": (("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),),
    "discord": (("discord_bot_token", "DISCORD_BOT_TOKEN"),),
    "twilio": (("twilio_auth_token", "TWILIO_AUTH_TOKEN"),),
}


class BoardFederationConfig(BaseModel):
    """Federation settings for the Jarvis board backend (Phase C).

    The admin token and private sync key do NOT live here — they go into
    the Credential Manager (keys ``board_admin_token``,
    ``board_sync_privkey_hex``). Only the operational profile is here.

    Setup steps:
      1. Deploy the backend (see ``board-backend/README.md``).
      2. Set ``backend_url``, e.g. ``https://board.mydomain.tld``.
      3. ``setx JARVIS_BOARD_ADMIN_TOKEN <token>`` (from the backend owner).
      4. On first start, Jarvis registers itself automatically.

    ``enabled = false`` disables the entire phase — local-only mode.
    """
    enabled: bool = False
    backend_url: str = ""              # e.g. "https://board.example.com"
    sync_interval_s: int = 60
    display_name: str = ""             # empty → user_data_dir owner


class BoardBioConfig(BaseModel):
    """Knobs for the AI profile generator (BioGenerator).

    Important: NO provider/model default. The bio dynamically uses the
    frontier model of the currently configured primary provider
    (see ``jarvis/brain/resolver.py:resolve_frontier_brain``). A user with
    only a Grok API key gets a Grok bio; a user with Claude configured gets
    Opus 4.7. Multi-provider compliance is mandatory.

    ``override_provider`` / ``override_model`` are power-user fields for
    explicitly pinning a model for the bio only. Leave empty in 99% of cases.
    """
    model_config = {"extra": "allow"}

    temperature: float = Field(default=0.85, ge=0.0, le=2.0)
    max_tokens: int = Field(default=400, ge=80, le=2000)
    override_provider: str | None = None
    override_model: str | None = None
    # Cold start: trigger the first bio after this minimum age in days
    # (instead of waiting until Sunday when no bio exists yet).
    cold_start_min_days: int = Field(default=1, ge=0, le=14)


class BoardConfig(BaseModel):
    """Container for all board subsystems."""
    federation: BoardFederationConfig = Field(default_factory=BoardFederationConfig)
    bio: BoardBioConfig = Field(default_factory=BoardBioConfig)


class VisionContextConfig(BaseModel):
    """Top-level ``[vision]`` config for Phase-5 vision anticipation.

    Mandate: on every ``spawn_worker`` call, an optional active-window hint
    (process name + window title) is passed as an additional ``context_hint``
    to the worker. Default OFF because the UIA tree lookup costs 200-400 ms
    of extra latency per spawn and does not pay off for every OpenClaw turn.

    Enable via either:
      - ENV ``JARVIS_VISION_CONTEXT=1``
      - ``[vision].context_hint_on_spawn = true`` in jarvis.toml
    """
    model_config = {"extra": "allow"}

    context_hint_on_spawn: bool = False
    timeout_s: float = 0.25     # mandate: 250 ms latency cap per spawn


class ComputerUseConfig(BaseModel):
    """Top-level ``[computer_use]`` config for the Computer-Use harness.

    Controls the screenshot-click loop in
    ``jarvis/harness/screenshot_only_loop.py``. Default OFF — the harness
    only runs when ``enabled = true`` is set (Phase 5 shell module,
    see ADR-0008).
    """
    model_config = {"extra": "allow"}

    enabled: bool = False
    max_steps: int = Field(default=100, ge=1, le=1000)
    # In the Set-of-Marks ReAct loop each cycle plans ONE action, so every
    # successful step also exhausts its one-step plan and counts as a "replan".
    # The cap therefore bounds total actions, not just retries — raised from 5
    # so real multi-step flows (open app -> navigate -> act -> verify) fit. The
    # no-progress guard in the loop still aborts dead-ends early.
    max_replans: int = Field(default=2, ge=0, le=40)
    per_step_timeout_s: float = Field(default=30.0, gt=0.0, le=300.0)
    verify_after_each_step: bool = True
    plan_model: str = "claude-opus-4-8"
    step_model: str = "claude-haiku-4-5-20251001"
    step_budget: int = Field(default=100, ge=1, le=1000)
    # Virtual-mouse overlay: when true, the real cursor glides to each target
    # (instead of teleporting) and a gold halo + click pulse shows where the
    # agent acts, so the user can watch Computer-Use. Desktop-only; degrades to
    # a no-op on a headless VPS. ``cursor_glide_ms`` is the glide duration
    # (0 = instant move, overlay pulse only).
    # Default off after the 2026-05-26 black-screen incident: a fullscreen
    # WS_EX_LAYERED overlay across the whole virtual desktop is fragile on
    # multi-monitor + new GPU driver combos. Opt in once the live-alignment
    # smoke (``scripts/virtual_cursor_demo.py``) has been verified on the
    # target machine.
    show_virtual_cursor: bool = False
    cursor_glide_ms: int = Field(default=220, ge=0, le=2000)
    # Hybrid native Computer-Use (Wave 3, 2026-05-29). When true AND the active
    # provider is Gemini, the loop's per-step action decision uses Gemini's
    # native ``computer_use`` tool (CU-trained grounding) instead of the
    # hand-rolled vision+JSON prompt; browser-only predefined functions are
    # excluded so it acts as a generic screen-grounding engine. Any native
    # failure falls back to the hand-rolled path for that step, so enabling
    # this can never make the loop worse than the default. Default OFF until
    # live-verified against the CU model on the user's account (the model is
    # preview + browser-scoped). See ADR-0023 + plan goofy-singing-piglet.md.
    prefer_native: bool = False
    native_model: str = "gemini-3-flash-preview"


class LocalActionConfig(BaseModel):
    """Low-latency local action fast path settings."""

    enabled: bool = True
    direct_timeout_s: float = Field(default=3.0, gt=0.0, le=30.0)
    harness_timeout_s: float = Field(default=30.0, gt=0.0, le=300.0)


class PerformanceConfig(BaseModel):
    """Latency optimisations with master switches for rollback.

    Sprint 1 (2026-04-30):
      - ``streaming_tts``: brain output is forwarded to TTS live in sentence
        chunks instead of waiting for the full brain stream. Drastically lowers
        perceived latency (time-to-first-audio). When ``False`` the old serial
        pipeline runs unchanged.

    Sprint 2 (2026-04-30, test branch ``latency-sprint-2-caching``):
      - ``anthropic_prompt_cache``: sets ``cache_control`` on the system prompt
        + tool definitions, plus a 1 h TTL via the beta header. On a cache hit:
        ~80% TTFT reduction, cached-token cost drops to 10%. Quality identical.
      - ``gemini_context_cache``: creates a Gemini context cache with the system
        prompt + tools on the first call and references it in subsequent calls.
        TTL 1 h. Equivalent quality retention to Anthropic.

    Defaults: Sprint-1 levers are live (streaming_tts=True). Sprint-2 caching
    is in test mode (False) until the test phase completes and the branch is
    merged into a stable branch.
    """
    model_config = {"extra": "allow"}

    streaming_tts: bool = True
    anthropic_prompt_cache: bool = False
    gemini_context_cache: bool = False
    # TTS look-ahead pipelining (2026-05-28): how many sentences may be
    # synthesized AHEAD of playback so synthesis of sentence N+1 overlaps
    # playback of N (provider-agnostic latency fix in ``_brain_streaming``).
    # 1 is enough to hide one sentence's synthesis latency; raise only if
    # profiling shows residual inter-sentence gaps. Bounds speculative synth
    # cost on the 1-vCPU VPS and caps wasted work on barge-over to one sentence.
    tts_lookahead_sentences: int = 1
    # Wave 1 (omni-latency): conditional vision. Drop the screenshot on
    # confidently text-only turns (skip-when-safe gate, jarvis/brain/vision_gate.py);
    # keep it whenever in doubt. Cuts the per-turn image tax on cheap turns.
    conditional_vision: bool = True
    # Wave 2 (omni-latency): cache-optimized prompt layout. Static prefix in the
    # system prompt, per-turn dynamic context (awareness/wiki/date) moved into the
    # user message so the provider prompt cache actually hits.
    cache_optimized_prompt: bool = True

    @field_validator("tts_lookahead_sentences")
    @classmethod
    def _floor_lookahead(cls, v: int) -> int:
        # A look-ahead < 1 would stall the synth/playback queue — no sentence
        # could be synthesized ahead of playback, deadlocking the producer on
        # an empty bounded channel. Floor at 1.
        return max(1, int(v))


class LatencyConfig(BaseModel):
    """Hot-path latency instrumentation (Wave 0 — omni-latency suite).

    ``enabled`` toggles ``LatencyTracker`` emission. Off = the tracker becomes a
    near-zero no-op (no LatencySpan events on the bus). Marks use perf_counter
    and emit fire-and-forget, so the hot path never blocks on telemetry.
    """

    model_config = {"extra": "allow"}

    enabled: bool = True
    log_jsonl: bool = False
    log_path: str = "state/latency_log.jsonl"


class ReviewRubricConfig(BaseModel):
    """A single review rubric (plan §6.4).

    `items` is the list of evaluation criteria that the reviewer
    must work through for a given task class.
    """
    items: list[str] = Field(default_factory=list, min_length=1)


def _default_rubrics() -> dict[str, ReviewRubricConfig]:
    """Default rubrics from plan §6.4."""
    return {
        "default": ReviewRubricConfig(items=[
            "task_completion",
            "tool_output_fidelity",
            "completeness",
            "voice_friendliness",
            "tool_use_efficiency",
        ]),
        "code_generation": ReviewRubricConfig(items=[
            "task_completion",
            "no_stub_code",
            "tests_pass_locally",
            "no_secret_leakage",
            "voice_friendliness",
        ]),
        "skill_authoring": ReviewRubricConfig(items=[
            "frontmatter_valid",
            "trigger_keywords_unique",
            "instructions_actionable",
            "no_malicious_bash",
        ]),
        "research": ReviewRubricConfig(items=[
            "task_completion",
            "factual_accuracy",
            "source_citation",
            "voice_friendliness",
        ]),
    }


class ReviewConfig(BaseModel):
    """Top-level ``[review]`` config for the quality-gate pipeline (Phase 8).

    Mutation of these values is NOT in the self-mod allowlist (plan §AD-1):
    the Phase 8 architecture requires a code edit + review to change them,
    because the pipeline parameters (max_iterations, hard_ceiling) determine
    the cost and latency profile of the main Jarvis path.
    """
    enabled: bool = True
    max_iterations: int = Field(default=3, ge=1, le=5)
    hard_ceiling: int = Field(default=5, ge=1, le=5)
    worker_model: str = "sonnet"
    reviewer_model: str = "opus"
    reviewer_provider: str = "claude-subscription"
    output_dir: str = "data/review/runs"
    audit_log: str = "data/review.log"
    gc_after_days: int = Field(default=30, ge=1)
    default_rubric: str = "default"
    rubrics: dict[str, ReviewRubricConfig] = Field(default_factory=_default_rubrics)


class WikiIntegrationConfig(BaseModel):
    """Bootstrap configuration for the wiki write-wiring (Phase B5, Agent A).

    Controls whether the ``SessionRollupWorker`` (B7) and ``WikiCurator``
    (B1) are wired into the app's startup flow and subscribed to the
    ``IdleEntered`` event bus event.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    vault_root: Path = Path("wiki/obsidian-vault")
    subscribe_idle: bool = True              # listen for IdleEntered
    fallback_to_direct_ingest: bool = True   # when scheduler is missing


class WikiContextConfig(BaseModel):
    """Configuration for the wiki context injector (B5 Agent C).

    Controls latency-bounded wiki-snippet injection into the brain system
    prompt before each router-tier turn.  ``enabled = false`` disables the
    whole injection path with zero overhead.

    Wave-2 cleanup task: nest this under ``WikiIntegrationConfig.context``
    and migrate callers off the top-level ``cfg.wiki_context`` field.

    ``extra="allow"`` is mandatory and matches every sibling wiki sub-table
    (WikiCurator/SessionRollup/Scheduler/VoiceBridge/WikiMemory/
    WikiIntegration): a self-mod or drift-guard write of an unknown future
    key must survive validation rather than being silently dropped (AP-16).
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    max_chars: int = 1500
    latency_budget_ms: int = 80
    min_keyword_length: int = 4


class VoiceConfig(BaseModel):
    """Voice-flow knobs that are not STT/TTS/Trigger-specific.

    Currently hosts the incomplete-prompt completion buffer settings (see
    ``docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md``).
    ``extra="allow"`` is mandatory — a self-mod or drift-guard write of an
    unknown future key must NOT block boot (AP-16).
    """

    model_config = ConfigDict(extra="allow")

    # Master switch for the completion classifier + waiting state. When false
    # the pipeline behaves exactly as before this feature landed.
    completion_detection_enabled: bool = True
    # Per-gap budget after which a stale pending fragment is silently
    # discarded (user-mandated 2026-05-26 — was: flushed/spoken). NOT a total
    # budget — every continuation resets the timer. Bumped from 8 s to 15 s
    # because the previous value was experienced as Jarvis interrupting the
    # user mid-thought. The bubble + open mic carry the "still listening"
    # signal silently; tunable in jarvis.toml.
    completion_wait_ms: int = 15000
    # Short grace window applied AFTER a COMPLETE classification before the
    # text is dispatched to the brain. Allows conversational chaining like
    # "Hey Jarvis, was geht ab? [pause] Ich wollte wissen ..." to land as ONE
    # merged turn instead of two separate brain calls. User-mandated
    # 2026-05-26 — was: 0 ms (immediate dispatch). 1500 ms is the natural
    # speaker beat between question and follow-up; bump higher for more pause
    # tolerance, set to 0 if the added latency is too costly.
    complete_grace_ms: int = 1500
    # Maximum number of continuations to chain before a forced flush. Bounds
    # the wait to a finite duration even for indefinite trailing fragments.
    completion_max_chain: int = 3


class CompletenessConfig(BaseModel):
    """Configuration for the utterance-completeness pre-processing classifier.

    Controls the classifier that runs in front of the main agent and decides
    whether a finalized transcript is a complete actionable instruction or an
    incomplete / abruptly-aborted utterance.

    Spec: docs/superpowers/specs/2026-05-25-utterance-completeness-design.md §6

    TOML path: [speech.completeness]
    Attribute path: JarvisConfig.speech.completeness
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    signal_mode: Literal["auto", "earcon", "spoken"] = "auto"
    # Replaces the old auto-flush-to-brain timer. When the pending fragment
    # buffer ages past this threshold it is DISCARDED, never flushed to the
    # brain. Must be strictly positive.
    pending_discard_s: float = 8.0
    max_pending_fragments: int = 2
    # Approach B (gray-zone LLM escalation) — reserved, default OFF.
    # Wiring an LLM call here would violate the "no LLM on the voice critical
    # path" doctrine (AP-9/AP-11). Only enable for offline evaluation.
    llm_escalation_enabled: bool = False

    @field_validator("pending_discard_s")
    @classmethod
    def _pending_discard_s_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(
                f"pending_discard_s must be > 0, got {v!r}. "
                "Use a positive value such as 8.0 (seconds)."
            )
        return v


class SpeechConfig(BaseModel):
    """Top-level [speech] config block.

    Groups all speech-pipeline sub-configs that do not already have a
    dedicated top-level field on JarvisConfig (e.g. stt, tts, trigger are
    kept at the root for backward compatibility). New sub-configs go here.

    TOML path: [speech]
    Attribute path: JarvisConfig.speech
    """

    model_config = ConfigDict(extra="allow")

    completeness: CompletenessConfig = Field(default_factory=CompletenessConfig)


class MarketplaceConfig(BaseModel):
    """Plugin-marketplace connect settings (OAuth redirect mode).

    ``public_callback_base_url`` switches redirect-based OAuth handlers from the
    loopback callback server (desktop, browser reaches 127.0.0.1) to a hosted
    FastAPI callback at ``<base>/api/marketplace/oauth/callback`` (headless VPS).
    Empty string keeps the loopback/desktop behavior.
    """

    public_callback_base_url: str = ""
    model_config = ConfigDict(extra="allow")


class PointerConfig(BaseModel):
    """[pointer] — AI Pointer: understand what the mouse cursor points at.

    The deictic-gated context provider resolves the on-screen element under the
    cursor via the OS accessibility tree (not blind screenshots) and rides it on
    the turn only when the utterance points at the cursor. ``extra="allow"`` so a
    future key cannot break the self-mod pre-validate pipeline (AP-16).
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    # Hard wall-clock budget for the off-hot-path cursor resolution (AP-9). On
    # timeout the turn proceeds with no pointer context. ElementFromPoint is a
    # single OS hit-test, so 120 ms is a generous ceiling.
    timeout_s: float = 0.12
    # Half-side (px) of the tight crop captured around the cursor. 110 px (220 px
    # square) is readable for a word in a terminal/editor while staying focused.
    crop_radius: int = 110


class JarvisConfig(BaseModel):
    """Root config model."""
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    brain: BrainConfig = Field(default_factory=BrainConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    mcp_server: MCPServerConfig = Field(default_factory=MCPServerConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    # Audio ducking — "Mute music while dictating" (Taskbar section).
    ducking: DuckingConfig = Field(default_factory=DuckingConfig)
    # Cross-platform login autostart (Windows .lnk / macOS LaunchAgent / Linux
    # XDG .desktop). Default ON; headless host = graceful no-op.
    autostart: AutostartConfig = Field(default_factory=AutostartConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    sub_agents: SubAgentsConfig = Field(default_factory=SubAgentsConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    # Wave 2 — plugin-marketplace OAuth connect (hosted vs loopback callback).
    marketplace: MarketplaceConfig = Field(default_factory=MarketplaceConfig)
    board: BoardConfig = Field(default_factory=BoardConfig)
    # Persona-Mandat Phase 5: top-level ``[vision]``-Section.
    vision: VisionContextConfig = Field(default_factory=VisionContextConfig)
    # Phase 5/6 — Computer-Use-POAV-Harness (ADR-0008).
    computer_use: ComputerUseConfig = Field(default_factory=ComputerUseConfig)
    # Low-latency local-action gate. Hidden tools only; never exposed in the
    # router LLM schema.
    local_action: LocalActionConfig = Field(default_factory=LocalActionConfig)
    # Phase 8.4 — review pipeline configuration.
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    # Latency sprint 1 (2026-04-30) — master switches for performance levers.
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    # Wave 0 (omni-latency) — hot-path latency span instrumentation toggle.
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    # Phase A0+: awareness layer (continuous context). Entire subsystem
    # hot-disabled via [awareness].enabled = false (plan §15).
    awareness: AwarenessConfig = Field(default_factory=AwarenessConfig)
    # Phase B5 — wiki write-wiring: SessionRollupWorker + WikiCurator bootstrap (Agent A).
    wiki_integration: WikiIntegrationConfig = Field(default_factory=WikiIntegrationConfig)
    # Phase B5 — CuratorScheduler (Agent D). Top-level field — Wave-2 cleanup task
    # is to move this into ``WikiIntegrationConfig.scheduler`` and migrate callers.
    wiki_scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    # Phase B5 — wiki context injection (Agent C). Top-level field — Wave-2
    # cleanup task is to move this into ``WikiIntegrationConfig.context``.
    wiki_context: WikiContextConfig = Field(default_factory=WikiContextConfig)
    # Pre-Thinking-Ack Flash-Brain (parallel-running short butler-style
    # acknowledgment LLM). Opt-in via [ack_brain].enabled = true.
    # Forward-reference + late import at the bottom of this module avoids
    # the brain<->core.config circular import.
    ack_brain: "AckBrainConfig" = Field(  # noqa: F821 (resolved by model_rebuild below)
        default_factory=lambda: AckBrainConfig()
    )
    # Speech pipeline sub-configs (completeness classifier, …).
    # TOML path: [speech] / [speech.completeness]
    speech: SpeechConfig = Field(default_factory=SpeechConfig)
    # Voice-flow knobs (incomplete-prompt completion buffer settings).
    # Spec: docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    # AI Pointer — deictic-gated "what is under the mouse cursor" context.
    # Spec: docs/plans/ai-pointer/DESIGN.md
    pointer: PointerConfig = Field(default_factory=PointerConfig)


# ----------------------------------------------------------------------
# Loading logic
# ----------------------------------------------------------------------

def _load_toml(path: Path) -> dict[str, Any]:
    # tomllib does not accept UTF-8 BOM; Windows editors (Notepad etc.)
    # write it automatically on Save-As. If the file is otherwise readable,
    # we should not silently cripple the entire brain stack — so strip the
    # BOM once.
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge: overlay overrides base, lists are replaced."""
    result = dict(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# User-switchable provider selections that the config-drift-guard keeps in sync
# across jarvis.toml + config-soll.json + the User-scope registry. They must be
# healed at boot (see refresh_persisted_env_from_user_registry) because a stale
# inherited process-env value would otherwise win over the persisted choice via
# _apply_env_overrides (env > toml). Symptom this fixes: a TTS switch to e.g.
# cartesia reverting to gemini-flash-tts on every restart.
_PERSISTED_PROVIDER_ENV_KEYS: tuple[str, ...] = (
    "JARVIS__BRAIN__PRIMARY",
    "JARVIS__BRAIN__SUB_JARVIS__PROVIDER",
    "JARVIS__TTS__PROVIDER",
    "JARVIS__STT__PROVIDER",
)


def _read_user_env_var(name: str) -> str | None:
    """Read a User-scope env var from ``HKCU\\Environment``.

    Returns the registry string, or ``None`` when the value is absent or the
    platform is not Windows (cloud-first / Linux VPS — there is no such hive,
    and the persisted choice lives in jarvis.toml alone). winreg is imported
    lazily so this module imports cleanly off Windows.
    """
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value)
    except (FileNotFoundError, OSError):
        return None


def refresh_persisted_env_from_user_registry(
    keys: tuple[str, ...] = _PERSISTED_PROVIDER_ENV_KEYS,
    *,
    read: Any = None,
) -> dict[str, str]:
    """Overwrite ``os.environ`` for the persistent provider keys with the
    authoritative User-registry value, healing a stale inherited process env.

    Call this ONCE at app boot, BEFORE :func:`load_config`. A long-running
    ancestor process (Explorer at login) can freeze an outdated value of e.g.
    ``JARVIS__TTS__PROVIDER`` and pass it to a freshly launched Jarvis; since
    ``_apply_env_overrides`` lets ``JARVIS__*`` win over the TOML, that stale
    value would silently revert the user's persisted choice. The drift-guard
    keeps the registry in sync with jarvis.toml + config-soll.json, so refreshing
    from it makes the boot honour the real choice regardless of what env the
    process inherited.

    ``read`` is an injectable ``name -> str | None`` reader (defaults to the
    HKCU\\Environment reader); tests pass a dict's ``.get`` so no real registry
    is touched. Returns the mapping of keys it actually changed (for logging).
    """
    reader = read if read is not None else _read_user_env_var
    changed: dict[str, str] = {}
    for name in keys:
        value = reader(name)
        if value is not None and os.environ.get(name) != value:
            os.environ[name] = value
            changed[name] = value
    return changed


def _apply_env_overrides(data: dict[str, Any], prefix: str = "JARVIS__") -> dict[str, Any]:
    """Override config with env variables in the format JARVIS__SECTION__KEY=value.

    Example: JARVIS__BRAIN__PRIMARY=openrouter → config["brain"]["primary"]
    """
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        path = env_key[len(prefix):].lower().split("__")
        cursor = data
        for segment in path[:-1]:
            cursor = cursor.setdefault(segment, {})
        cursor[path[-1]] = _coerce_env_value(env_val)
    return data


def _coerce_env_value(v: str) -> Any:
    """Coerce a string env value to bool/int/float/str."""
    lv = v.strip().lower()
    if lv in ("true", "yes", "1"):
        return True
    if lv in ("false", "no", "0"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load_config(
    config_file: Path = DEFAULT_CONFIG_FILE,
    profile: str | None = None,
) -> JarvisConfig:
    """Load config from TOML + optional YAML profile + env overrides.

    Precedence (lowest → highest):
      1. jarvis.toml (defaults)
      2. profiles/<active>.yaml
      3. Environment variables (JARVIS__*)
    """
    if not config_file.exists():
        # No config file → pure defaults (useful for tests)
        data: dict[str, Any] = {}
    else:
        data = _load_toml(config_file)

    if profile is None:
        profile = os.environ.get("JARVIS_PROFILE") or data.get("profile", {}).get("name")

    if profile and profile != "default":
        profile_file = PROFILES_DIR / f"{profile}.yaml"
        if profile_file.exists():
            data = _deep_merge(data, _load_yaml(profile_file))

    data = _apply_env_overrides(data)
    return JarvisConfig(**data)


# ----------------------------------------------------------------------
# Secrets (Windows Credential Manager via keyring)
# ----------------------------------------------------------------------

def get_secret(key: str, env_fallback: str | None = None) -> str | None:
    """Retrieve a secret value. Priority: keyring → ENV fallback → .env.

    Args:
        key: Secret name in the Credential Manager (e.g. "anthropic_api_key").
        env_fallback: ENV variable checked when keyring is empty (e.g. "ANTHROPIC_API_KEY").
    """
    # Lazy import — keyring requires pywin32 on Windows
    try:
        import keyring

        val = keyring.get_password(KEYRING_SERVICE, key)
        if val:
            return val
    except Exception:  # noqa: BLE001
        # keyring failed — silent fallback to env
        pass

    if env_fallback and (val := os.environ.get(env_fallback)):
        return val

    # Last fallback: .env file (dev only)
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists() and env_fallback:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == env_fallback:
                return v.strip().strip('"').strip("'")

    return None


def get_secret_any(candidates: tuple[tuple[str, str | None], ...]) -> str | None:
    """Return the first configured secret from ``(keyring_key, env_var)`` pairs."""
    for key, env_fallback in candidates:
        val = get_secret(key, env_fallback=env_fallback)
        if val:
            return val
    return None


def get_provider_secret(provider: str) -> str | None:
    """Return the API key for a Brain provider, including accepted aliases."""
    return get_secret_any(PROVIDER_SECRET_CANDIDATES.get(provider, ()))


def set_secret(key: str, value: str) -> bool:
    """Store a secret in the Credential Manager. Returns True on success."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def delete_secret(key: str) -> bool:
    """Remove a secret from the Credential Manager."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, key)
        return True
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------
# First-run check
# ----------------------------------------------------------------------

def is_first_run() -> bool:
    """Return True when the user has not yet completed the setup wizard."""
    marker = DATA_DIR / ".setup-complete"
    return not marker.exists()


def mark_setup_complete() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ".setup-complete").write_text(
        f"Setup abgeschlossen auf Python {sys.version.split()[0]}\n",
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# Deferred imports / forward-reference resolution
# ----------------------------------------------------------------------
#
# AckBrainConfig (Pre-Thinking-Ack Flash-Brain) cannot be imported at the
# top of this file because jarvis.brain.__init__ eagerly imports
# brain.manager + brain.router, both of which top-level-import JarvisConfig
# from this very module. Resolving that circular requires us to declare
# the field with a forward reference and pull the real class in only after
# JarvisConfig is fully defined, then rebuild the model so Pydantic
# resolves the string annotation against this module's namespace.
try:
    from jarvis.brain.ack_brain.config import AckBrainConfig  # noqa: E402
except ModuleNotFoundError:
    class AckBrainConfig(BaseModel):  # type: ignore[no-redef]
        """Fallback when the optional ack_brain package is not installed."""

        model_config = ConfigDict(extra="allow")

        enabled: bool = False
        provider: str = "follow_brain"
        timeout_ms: int = 1500
        on_failure: str = "silent"
        circuit_breaker_threshold: int = 3
        circuit_breaker_cooldown_s: int = 60
        suppress_if_brain_faster_than_ms: int = 2000

JarvisConfig.model_rebuild()
