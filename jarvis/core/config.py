"""Config loading with layers: TOML → YAML profiles → Env → Runtime.

Secrets do NOT come from the config file. They resolve through the OS credential
store via `keyring` (Windows Credential Manager / macOS Keychain / Linux Secret
Service), then an ENV-variable fallback, then `.env`, and — on a headless host with
no OS keyring (e.g. python:3.11-slim) — a local 0600 file (see
`_ensure_keyring_backend`). The `get_secret()` getter is the single access point.

Hot-reload: watchdog monitors the config file and dispatches `ConfigReloaded`
on change. Subscribers decide whether to reinitialise themselves.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

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


def resolve_config_path() -> Path:
    """Return the active ``jarvis.toml`` path, honouring ``JARVIS_CONFIG``.

    Cloud-first: a headless ``python:3.11-slim`` container (or any VPS where
    ``PROJECT_ROOT`` is read-only / does not exist) sets ``JARVIS_CONFIG`` to a
    writable path. A blank / whitespace value is ignored so an empty export does
    not shadow the bundled default. Both the reader (``load_config``) and the
    Control-API write path (``AtomicConfigWriter``) resolve through here.
    """
    override = os.environ.get("JARVIS_CONFIG")
    if override and override.strip():
        return Path(override.strip())
    return DEFAULT_CONFIG_FILE

KEYRING_SERVICE = "personal-jarvis"

# Provider-secrets are intentionally kept out of TOML. Keep the accepted
# Credential-Manager slots and ENV fallbacks in one place so pre-boot checks,
# Frontier resolving and provider adapters do not disagree about whether a
# provider is configured.
PROVIDER_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "claude-api": (("anthropic_api_key", "ANTHROPIC_API_KEY"),),
    "openai": (("openai_api_key", "OPENAI_API_KEY"),),
    # Codex-as-brain uses its own key slot, falling back to the general OpenAI key.
    "codex": (
        ("codex_openai_api_key", "OPENAI_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
    ),
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
    """Reserved ``[persona]`` table. The assistant's name is no longer stored
    here — it derives solely from the wake phrase (see
    ``jarvis.brain.assistant_name.resolve_assistant_name`` and the 2026-06-20
    coupling design). A legacy ``[persona] name`` key in an existing jarvis.toml
    is ignored (Pydantic ``extra="ignore"``); the next wake-word save strips it.
    """

    # Explicit so the "legacy name key is ignored" contract above cannot be
    # silently broken by a future base-class / project-wide model_config change.
    model_config = ConfigDict(extra="ignore")


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
    # Silence window (seconds) after which a CONVERSATION-mode voice session
    # (``single_turn_mode = false``) auto-hangs-up while waiting for the next
    # user turn. Set to 0 — or any value <= 0 — to DISABLE the auto-hangup
    # entirely: the session then stays active until you hang up manually (say
    # "auflegen" or press the hangup hotkey). User mandate 2026-06-30. Has no
    # effect in single-turn mode (each turn ends after Jarvis answers anyway).
    # Wired into ``SpeechPipeline(idle_timeout_s=...)`` at every construction
    # site; the constructor default (30 s) stays the safe baseline for a fresh
    # download so an accidental wake never holds the mic open forever.
    session_idle_timeout_s: float = 30.0
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

        A blank string means the user explicitly cleared that action (Settings
        Clear button) — filtered out here so an unbound key never reaches
        ``HotkeyTrigger`` as a bogus single-element tuple containing ``""``.
        """
        if self.push_to_talk:
            call, ptt = (self.hotkey_call,), (self.hotkey,)
        else:
            call, ptt = (self.hotkey, self.hotkey_call), ()
        return (
            tuple(h for h in call if h.strip()),
            tuple(h for h in ptt if h.strip()),
        )
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
    # ``model`` is the local FasterWhisperProvider's post-wake utterance model
    # (used whenever ``provider = "faster-whisper"``; the Groq cloud plugin
    # hardcodes its own multilingual model and ignores this). Must be a
    # faster-whisper-compatible name (see faster_whisper/utils.py). It MUST be
    # multilingual for the bilingual default: ``distil-large-v3`` is ENGLISH-ONLY
    # and mangles German/Spanish speech into English words. ``large-v3-turbo`` is
    # the fast multilingual checkpoint. (FasterWhisperProvider also guards this at
    # runtime: an English-only model + a non-"en" language auto-upgrades.)
    model: str = "large-v3-turbo"
    # Cloud-first default: "cpu". A fresh clone on a VPS or a laptop must never
    # assume a local GPU. Set to "cuda" in jarvis.toml on a CUDA box; the local
    # faster-whisper path also tolerates "cuda" with a no-CUDA runtime fallback.
    device: str = "cpu"
    compute_type: str = "int8_float16"
    # The LOCAL wake-match / live-preview Whisper (distinct from ``model``, which
    # is the post-wake utterance model — often a cloud provider). It only powers
    # wake-phrase transcript matching + the listening-bubble probe, both
    # latency-tolerant, so it defaults to a small model on CPU. This matters a
    # lot for boot: on a Blackwell GPU (RTX 50xx) CTranslate2 JIT-compiles kernels
    # at model-load, costing ~71 s on CUDA vs ~0.45 s for ``base`` on CPU — the
    # dominant warm-up cost. CPU is also the cloud-first floor (no GPU assumed).
    # Power users on an older GPU may set ``wake_device = "cuda"``.
    wake_model: str = "base"
    wake_device: str = "cpu"
    wake_compute_type: str = "int8"
    # When True AND a CUDA GPU is present, a CUSTOM wake phrase (the
    # transcription-based ``stt_match`` path) runs the strong ``large-v3-turbo``
    # model on the GPU instead of the small ``base`` model on the CPU.
    # DEFAULT False (2026-06-30, live-log evidence): on the maintainer's Blackwell
    # GPU (RTX 5070 Ti / sm_120) CTranslate2's ``model.transcribe`` HANGS on every
    # live inference (an 8 s timeout every time -> the wake self-heal drops and
    # rebuilds the model -> a fresh cold inference hangs again -> a vicious cycle
    # that leaves the wake permanently deaf). Enabling GPU turbo there made the
    # wake WORSE, not better. It is kept as an opt-in for GPUs where CTranslate2
    # inference is stable (RTX 30xx/40xx), but the transcription wake fundamentally
    # cannot reach "Hey Google" reliability — that needs a trained neural
    # keyword-spotting model (the ``custom_onnx`` engine). Set True only on a GPU
    # you have verified does not hang on repeated faster-whisper inference.
    wake_high_accuracy: bool = False
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
    cu_model: str | None = None        # Optional: model the Computer-Use loop uses
                                       # (Phase 3). None -> use this provider's `model`.
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

    # Intelligent router (2026-06-21 user mandate "Jarvis must choose wisely among
    # ALL tools, like Claude Code"). When the ACTIVE talker cannot emit tool_calls
    # at runtime (the subscription-CLI brains — Antigravity over the Google login,
    # Codex over the ChatGPT login — drop ALL tools), a tool-capable provider
    # (the deep_brain / router, e.g. Gemini) leads every SUBSTANTIVE turn and the
    # LLM itself picks the tool via its tool-use loop + the router system prompt —
    # no signal-word list decides the tool. If the router picks NO tool (pure
    # conversation), the turn FALLS THROUGH to the chosen talker so the user keeps
    # their selected brain's voice. Tool-capable talkers are unaffected (they
    # already pick tools in their own loop). The deterministic gates (force-spawn,
    # match_local_action, on-screen, build-artifact) remain as HIGH-PRECISION
    # guardrails for the obvious cases. This flag is the reversible kill switch:
    # set false → exactly the prior behaviour (the narrower action-intent
    # delegation). See manager._build_fallback_chain / the router fall-through.
    intelligent_router: bool = True

    # Per-mission MCP relevance filter (mirror of the router's plugin-relevance
    # gate, one layer below). A mission WORKER runs --permission-mode
    # bypassPermissions, so every exported MCP server is actually reachable; an
    # off-topic server (e.g. NotebookLM on a flight question) would re-introduce
    # the ~35 s wrong-MCP stall the router gate removed. When True (default), the
    # servers exported to a worker are filtered to those RELEVANT to that
    # mission's task text via the SAME relevance definition the router uses
    # (jarvis.marketplace.plugin_relevance.plugin_is_relevant). This flag is the
    # reversible kill switch: set false → exactly the prior behaviour (every
    # enabled MCP server + every connected plugin exported to every mission). A
    # relevance fault always degrades to exporting (never strips). See
    # jarvis.missions.init._assemble_worker_mcp_servers.
    worker_mcp_relevance_filter: bool = True

    # Heavy-research force-spawn (live bug 2026-06-14, the Berlin→Melbourne
    # turn): a multi-step research/analysis request must be OFFLOADED to a
    # background mission, not run inline on the deep brain where it blows the
    # ~20 s voice budget and gets beheaded. Conjunctive gate (precision over
    # recall): a research/analysis VERB must be present AND a heaviness signal —
    # a horizon/multi-step marker, OR >= heavy_research_min_verbs_multiclause
    # verb matches (multi-clause), OR length >= heavy_research_min_chars with a
    # verb. Length alone never spawns; a quick "recherchier das mal kurz" stays
    # inline. Strict-mode only, evaluated after every stand-down guard (skills /
    # open-app / instructional / nav / pointer still win). See ADR-0011.
    heavy_research_enabled: bool = True
    heavy_research_verbs: list[str] = Field(default_factory=lambda: [
        "recherchier", "analysier", "untersuch",  # i18n-allow: DE routing verb stems
        "vergleich", "evaluier", "bewert",  # i18n-allow: DE routing verb stems
        "research", "analyz", "analys", "investigat", "compar", "evaluat",
        "assess", "summari",
    ])
    heavy_research_markers: list[str] = Field(default_factory=lambda: [
        "nächsten", "naechsten", "kommenden",  # i18n-allow: DE routing markers
        "mehrere", "verschiedene", "schritt für schritt",  # i18n-allow: DE routing markers
        "brauche", "benötige", "benoetige", "checkliste",  # i18n-allow: DE routing markers
        "next two weeks", "over the next", "step by step", "checklist",
    ])
    heavy_research_min_chars: int = 120
    heavy_research_min_verbs_multiclause: int = 2

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

    2026-06-08 (Wave-2 latency fix): ``enabled`` defaults to ``False``. The
    permanent per-turn screenshot injection roughly doubled think-time
    (tokens_in 25k -> 50-143k) on EVERY turn and is meaningless on a headless
    VPS (cloud-first). Computer-Use keeps its own on-demand screen capture — the
    two are decoupled in ``jarvis.brain.factory`` so this default does NOT
    disable "klick auf X". Turn it on only on a desktop where you want Jarvis to
    spontaneously see the screen on ordinary turns.
    """
    enabled: bool = False
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


class EvidenceDomainsConfig(BaseModel):
    """Evidence-required domains (CLI first-class capabilities, 2026-06-10).

    Questions in these domains are never answered from the model's head:
    either a capability covers the domain (the gate injects a mandatory-tool
    directive) or the gate returns a deterministic honest refusal. Keyword
    lists are DE+EN, lowercase; matching is word-boundary, umlaut-normalised
    (jarvis/brain/evidence_gate.py). TOML shape:

        [brain.evidence_domains]
        enabled = true
        [brain.evidence_domains.domains]
        calendar = ["kalender", "termin", ...]
    """

    enabled: bool = True
    domains: dict[str, list[str]] = Field(default_factory=lambda: {
        "calendar": [
            "kalender", "termin", "termine", "steht heute", "steht morgen",
            "steht diese woche", "calendar", "appointment", "appointments",
        ],
        "email": [
            "mail", "mails", "e-mail", "e-mails", "email", "emails",
            "posteingang", "postfach", "inbox", "ungelesene",
        ],
        "tasks": [
            "aufgaben", "todo", "todos", "to-do", "task", "tasks",
        ],
        "repos": [
            "pull request", "pull requests", "pull-request", "pr", "prs",
            "issue", "issues", "repo", "repos", "repository",
        ],
        "deployments": [
            "deployment", "deployments", "deploy-status",
            "build-status", "build status",
        ],
        # Cloud cost / billing. Mapped to the connected gcloud CLI via
        # capability_provider.connected_domain_tool_map (gcloud declares the
        # "cloud" domain), so a billing question deterministically FORCES a
        # real cli_gcloud call (or an honest refusal) instead of relying on the
        # model's discretion (live 2026-06-17). Keywords are cloud/billing
        # specific — NO bare "kosten"/"cost" so "was kostet X" never hijacks, and
        # NO bare "budget" so a travel/household/project budget never forces a
        # billing call (live 2026-06-30 Bora-Bora session: "bei meinem Budget bei
        # 25.000 Euro" voided a good travel answer). Cloud-budget phrasing is kept
        # via the explicit "cloud budget"/"gcp budget" phrases instead.
        "cloud": [
            "google cloud", "gcp", "gcloud", "cloud-cli", "cloud cli",
            "google-kosten", "google kosten", "cloud-kosten", "cloud kosten",
            "cloud-rechnung", "cloud rechnung", "cloud billing", "billing account",
            "cloud budget", "cloud-budget", "gcp budget", "gcloud budget",
            "abrechnung", "abrechnungen", "guthaben", "billing",
        ],
        # Local screen / window-activity history. Served by the always-on
        # internal `awareness-recall` tool (wired into the domain→tool map in
        # BrainManager._run_evidence_gate, NOT a connected CLI), so a question
        # like "was hatte ich heute offen / was habe ich gemacht / which
        # windows were open" deterministically FORCES an awareness-recall call
        # instead of letting the (esp. fast-tier) model confabulate "der lokale
        # Verlaufsspeicher ist nicht verfügbar" without ever calling the tool
        # (live 2026-06-18, proven from the log: no tool execution line, yet the
        # refusal was spoken). Keywords are PHRASE-specific to opened
        # windows/apps/today's on-device activity — never a bare "offen"/"open"
        # token, so "ist die Frage noch offen" can't hijack the domain.
        "activity": [
            "offen hatte", "offen gehabt", "heute offen", "was war offen",
            "was hatte ich auf", "geoeffnet hatte", "geoeffnete fenster",
            "geoeffneten fenster", "offene fenster", "offene programme",
            "welche fenster", "welche programme", "welche anwendungen",
            "welche apps", "geoeffnete anwendungen", "geoeffneten anwendungen",
            "am rechner gemacht", "am rechner offen", "am pc gemacht",
            "am computer gemacht", "heute gemacht", "heute am rechner",
            "woran hab ich gearbeitet", "woran habe ich gearbeitet",
            "what did i have open", "what was open", "what did i do today",
            "what have i been working on", "what was i working on",
            "which windows", "which apps", "windows were open",
            "apps were open", "my activity today", "earlier today",
        ],
    })


class BrainConfig(BaseModel):
    # populate_by_name=True lets callers use the Python field name alongside the
    # validation aliases (needed so both new and old TOML keys populate the fields).
    model_config = ConfigDict(populate_by_name=True)

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
    # subprocess via Mission Manager. The ``worker`` field (renamed from
    # ``sub_jarvis`` in the Jarvis-Agents rename, 2026-06-29) accepts both the
    # new TOML key ``[brain.worker]`` and the old ``[brain.sub_jarvis]`` key
    # via AliasChoices so pre-rename installs keep working.
    router: BrainTierConfig | None = None
    # ``validation_alias`` back-compat: old installs use [brain.sub_jarvis];
    # new installs use [brain.worker]. Both populate this field transparently.
    worker: BrainTierConfig | None = Field(
        default=None,
        validation_alias=AliasChoices("worker", "sub_jarvis"),
    )
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
    # CLI first-class capabilities: evidence-required external-data domains.
    evidence_domains: EvidenceDomainsConfig = Field(
        default_factory=EvidenceDomainsConfig,
    )
    healthcheck_on_start: bool = True
    # Frontier model auto-switch (Phase F.3). When True, the boot hook
    # ``apply_frontier_resolution`` queries each provider's /v1/models and may
    # rewrite ``[brain.providers.<p>].model`` to a newer model on every start.
    # Default False (2026-06-20, user mandate "providers must NOT switch by
    # themselves"): the boot hook becomes a no-op and the configured models are
    # kept verbatim. A newer model is only ever adopted by an explicit user pick
    # in the per-provider model picker. Flip to True to restore the old
    # auto-frontier behaviour.
    frontier_auto_apply: bool = False
    # Two-turn spoken confirmation (forensic 2026-06-18): on a conversational
    # turn a consequential ``ask``-tier tool (e.g. gmail send) is deferred into a
    # spoken "Soll ich das wirklich tun? Sag ja." instead of blocking on a UI
    # approval no voice user can give (which the no-first-frame ceiling then
    # beheads with a misleading "took too long" phrase). Set False to fall back to
    # the UI-approval path.
    voice_confirm: bool = True


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
    # Headroom for a complete proposal; the streaming truncation guard
    # rejects any residual length-capped generation.
    max_output_tokens: int = 4000
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
        Slug of the user's own entity page (schema default ``ruben``).
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
    # A 400-word digest paragraph needs ~700 tokens of headroom; the
    # streaming truncation guard rejects anything still length-capped.
    max_output_tokens: int = 1200
    timeout_s: float = 30.0
    user_entity_slug: str = "ruben"
    # D2 (2026-06): the awareness-episode -> durable session-page feed is
    # retired. The worker still READS awareness episodes and still produces
    # the rollup paragraph (live awareness is unaffected), but the durable
    # wiki *page write* is gated off by default. Conversation (VoiceFactBridge)
    # is the sole wiki feed now. Flip to True only to re-enable the legacy
    # window-focus session pages.
    wiki_write_enabled: bool = False


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
    # Wave-2 journal pressure: once this many candidate facts sit pending
    # in the Stage-1 journal, a JOURNAL trigger asks the consolidator to
    # drain a batch (still subject to cooldown + lock).
    consolidate_after_candidates: int = 8


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


class ExtractorConfig(BaseModel):
    """Settings for the Stage-1 ``ConversationFactExtractor`` (Wave 2).

    ``[memory.wiki.extractor]``. The extractor's provider/model are NOT
    configured here — both curator stages resolve through the single
    ``[memory.wiki.curator]`` provider/model pair (the Wiki settings card
    drives them together). This section only holds the extraction gates.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    # Turns shorter than this never reach the LLM (smalltalk floor).
    min_user_chars: int = 12
    max_output_tokens: int = 800
    timeout_s: float = 30.0


class WikiMemoryConfig(BaseModel):
    """Root of the ``[memory.wiki]`` block (Phase B1+B7+B8 + Wave 2).

    Holds the Curator LLM section (B1), the session-rollup section (B7),
    the voice-bridge section (B8 aggressive ingest), and the Stage-1
    extractor section (Wave 2). Defaults are chosen so a config without
    the section loads cleanly as ``WikiMemoryConfig()``.
    """

    model_config = ConfigDict(extra="allow")

    curator: WikiCuratorConfig = Field(default_factory=WikiCuratorConfig)
    session_rollup: SessionRollupConfig = Field(default_factory=SessionRollupConfig)
    voice_bridge: VoiceBridgeConfig = Field(default_factory=VoiceBridgeConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)


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
    # chromadb was removed (2026-06-28); there is no chroma backend in
    # jarvis/memory/. Default to the sqlite store so a fresh install does not
    # point the archival tier at a backend that no longer exists.
    archival_store: str = "sqlite"
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


class JarvisAgentNotificationConfig(BaseModel):
    """Notification behaviour of the Jarvis-Agent worker harness (bridge docs §4.2).

    Mandate AD-17: the bridge pipes ``summary_de`` from the Kontrollierer
    signature into the existing ``_on_announcement`` bus (pipeline.py:647).
    Voice readback only when voice is currently listening; toast always.
    """
    model_config = ConfigDict(extra="forbid")

    # Default is the bus bypass — see pipeline._on_announcement.
    via: str = "announcement_bus"
    toast: bool = True
    voice_when_active: bool = True


class JarvisAgentHarnessConfig(BaseModel):
    """Top-level ``[harness.openclaw]`` config for the Jarvis-Agent worker harness.

    ⚠️ INERT TODAY (2026-06-28): OpenClaw is NOT a registered harness — there is
    no ``openclaw`` entry-point in pyproject.toml (Welle-4 removed the subprocess
    worker, ~92% hang; see docs/BUGS.md). This block is a Wave-2 schema stub:
    setting ``enabled = true`` has NO effect — the harness cannot be dispatched
    and "start a subagent" routes to ``spawn_worker`` regardless. The boot path
    logs a warning when ``enabled`` is true but the harness is unregistered
    (see ``warn_if_phantom_worker_harness`` in jarvis/brain/factory.py). Heavy
    sub-agent work runs through the Mission-Manager (ClaudeDirectWorker), not
    here, until Wave 3 actually wires the bridge.

    Schema matches ``docs/openclaw-bridge.md §4.2`` post-Wave-1
    (with AD-22..AD-24 findings incorporated). Wave 2 delivers only
    the schema + default block in jarvis.toml; Wave 3 wires the bridge.

    Deliberately NO Anthropic lock in the ``model`` default: an empty ``model``
    means the bridge resolves the frontier-pro of the active Personal Jarvis
    provider (``cfg.brain.primary``) via the provider-slug mapping from AD-6
    (gemini→google/gemini-..., claude-api→anthropic/..., openai→openai/...).
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
    # Explicitly set e.g. "anthropic/claude-fable-5" or
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

    notification: JarvisAgentNotificationConfig = Field(
        default_factory=JarvisAgentNotificationConfig,
    )


class HarnessConfig(BaseModel):
    """Config for the harness dispatcher (Phase 4)."""
    # populate_by_name=True lets callers use the Python field name alongside
    # validation aliases for the renamed openclaw → jarvis_agent field.
    model_config = ConfigDict(populate_by_name=True)

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
    # Jarvis-Agent worker harness (Wave 2). None = block missing in jarvis.toml,
    # bridge stays inactive. When the block is present, ``version`` is required.
    # ``validation_alias`` back-compat: old installs use [harness.openclaw];
    # new installs use [harness.jarvis_agent]. Both populate this field.
    jarvis_agent: JarvisAgentHarnessConfig | None = Field(
        default=None,
        validation_alias=AliasChoices("jarvis_agent", "openclaw"),
    )


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
    # Global master switch for all synthesized UI earcons (wake "ding",
    # hang-up tone, boot-ready tone, "still listening" earcon). The spoken TTS
    # voice is NOT affected — only the non-verbal effect tones. Default on;
    # toggled live from Settings → Behavior, persisted to [ui] sound_effects.
    sound_effects: bool = True
    # Interface (display) language of the whole app — every label, button and
    # message. The backend home for what used to be a frontend-only localStorage
    # value, so a voice command or the Control API can change it and the open UI
    # switches live (a ConfigReloaded / UiLanguageChanged event reaches the
    # frontend over /ws). Distinct from brain.reply_language (what Jarvis SPEAKS).
    language: Literal["en", "de", "es"] = "en"
    # Dev mode: the frontend is not mounted from frontend/dist/ but loaded from
    # a running Vite dev server (HMR). Activated via ENV JARVIS_DEV=1 or CLI
    # --dev; the fields here simply hold the parameters.
    dev_mode: bool = False
    vite_dev_url: str = "http://localhost:5173"
    # ENV variable that provides the session token for the WebView.
    # Default: JARVIS_UI_TOKEN. The token is freshly generated at startup
    # and pywebview injects it via evaluate_js into window.__JARVIS_TOKEN.
    auth_token_env: str = "JARVIS_UI_TOKEN"
    # On-screen overlay style: "jarvis_bar" (slim default), "mascot" (ghost
    # orb), or "none". The mascot remains fully selectable.
    orb_style: str = "jarvis_bar"
    # Optional explicit path to the mascot PNG. Empty = search for default asset.
    orb_mascot_path: str = ""
    # Jarvis bar: persistent (always-visible dots pill) vs only-when-active.
    bar_persistent: bool = True
    # Hex accent the bar lights up with during activity (gold on-brand).
    bar_accent: str = "#e7c46e"
    # Remembered "open with" choice for Outputs artifacts: an opener id
    # ("default" = OS default app, "browser", or an editor key like "code").
    # Empty = ask via the chooser dialog on first open. Desktop-only.
    preferred_opener: str = ""

    @field_validator("orb_style", mode="before")
    @classmethod
    def _normalize_orb_style(cls, v: object) -> object:
        # Backwards-compat: the slim-bar style was historically persisted as
        # "whisper_bar". It was renamed to "jarvis_bar" to avoid a
        # trademark. Normalize the legacy value on load so an existing
        # jarvis.toml keeps showing the bar instead of falling back to the
        # mascot orb (the unknown-style default in _build_overlay_surface).
        if isinstance(v, str) and v.strip().lower() == "whisper_bar":
            return "jarvis_bar"
        return v


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

    ``enabled`` defaults to True (approved design spec §5 — "default ON, user
    mandate"). On the first boot after this feature ships, the self-healing
    reconcile finds no entry and installs it, so Jarvis launches at login and
    "Hey Jarvis" works right after a reboot. The Settings toggle is the intended
    off-switch. On a headless host (no display) the autostart manager is a
    graceful no-op, so default-on stays safe for the cloud-first / VPS base
    install — nothing is registered where there is no GUI login session.

    ``extra="allow"`` so a future ``[autostart.*]`` sub-key — or a self-mod /
    drift-guard write of an as-yet-unknown field — never trips pre-validate
    (AP-16). Spec: docs/superpowers/specs/2026-05-30-cross-platform-autostart-design.md
    """

    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    # Window-visibility hint for the autostart launch. Default False = open the
    # desktop window visibly at login, so the user sees Jarvis came up (user
    # choice 2026-06-09). On Windows it maps to the fallback shortcut's WindowStyle
    # (7 = minimized/tray, 1 = normal); the logon scheduled task launches visibly
    # regardless. macOS/Linux ignore it.
    start_minimized: bool = False


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


class JarvisAgentsOutputConfig(BaseModel):
    """Config for Jarvis-Agent output management, GitHub push, and verification."""
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
    only a Gemini API key gets a Gemini bio; a user with Claude configured gets
    Opus. Multi-provider compliance is mandatory.

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
    # Which Computer-Use engine runs (reversible switch). "v2" (default) = the
    # rebuilt perceive->act->verify engine (jarvis/cu/engine.py): per-frame
    # coordinate mapping, provider coordinate conventions, UI-idle capture,
    # effect-checked actions and the idempotency ledger. Legacy engines stay
    # available as fallbacks: "current" = the last maintained legacy loop,
    # "june13" / "stable" = frozen known-good snapshots. The harness reads
    # this per mission and logs which engine is live, so a flip applies on
    # the next mission with no restart. Roll back any time with "current".
    engine: Literal["v2", "current", "june13", "stable"] = "v2"
    # Coordinate space the vision model's click coordinates are parsed in
    # (CU v2 only). "auto" (default) resolves per provider: an explicit
    # ``coordinate_convention`` capability on the brain wins, else the
    # provider family's documented convention (Gemini -> 0-1000 normalized;
    # Claude/OpenAI -> pixels on the sent image; unknown -> normalized).
    # Pin "normalized_1000" or "image_pixels" only to override a wrong guess.
    coordinate_space: Literal["auto", "normalized_1000", "image_pixels"] = "auto"
    # How Computer-Use relates to multiple monitors. DEFAULT "primary": CU brings
    # the target window onto the MAIN monitor (the G8 move-to-primary hook) AND
    # the screenshot FOLLOWS that window — so the normal case lands on the main
    # screen, while a window that genuinely cannot be moved (Wayland / owned /
    # fixed-placement) is still captured + clicked WHERE IT IS instead of CU
    # filming an empty primary and doing nothing (Problem 1, 2026-06-28). The
    # negative-X absolute-click fix makes secondary clicks land. "foreground" =
    # follow the active window without moving it; "all" = capture the whole
    # virtual desktop. Cross-platform: the primary is identified natively (Win
    # MONITORINFOF_PRIMARY, macOS CGMainDisplayID, X11 XRRGetOutputPrimary), NOT
    # by assuming origin (0,0). The capture STRATEGY is derived via
    # jarvis.vision.screenshot.cu_capture_strategy (primary/foreground -> follow).
    monitor: Literal["primary", "foreground", "all"] = "primary"
    # Which screen counts as "the main monitor" when monitor="primary" (audit G8a).
    # "primary" (default) = the OS primary; "largest" = the biggest-area screen;
    # or an explicit id (a monitor-name substring, or a 1-based index "1"/"2").
    # An unknown id falls back to the OS primary (never a silent wrong screen).
    main_monitor: str = "primary"
    # Master switch for the per-action read-back verification suite (claude-in-
    # chrome parity): after a type, confirm the text landed in the field; after a
    # click_element, confirm the intended state changed; don't blind-batch a
    # type/Enter behind a focus-click. Deterministic accessibility-tree read-back
    # (no extra model call), so it makes CU more reliable without making it slower.
    # Default ON; set false to fall back to the legacy dispatch-and-hope behaviour.
    strict_verify: bool = True
    max_steps: int = Field(default=100, ge=1, le=1000)
    # In the Set-of-Marks ReAct loop each cycle plans ONE action, so every
    # successful step also exhausts its one-step plan and counts as a "replan".
    # The cap therefore bounds total actions, not just retries — raised from 5
    # so real multi-step flows (open app -> navigate -> act -> verify) fit. The
    # no-progress guard in the loop still aborts dead-ends early.
    max_replans: int = Field(default=2, ge=0, le=40)
    per_step_timeout_s: float = Field(default=30.0, gt=0.0, le=300.0)
    # L10 (CU speed): ceiling on a single CU model (think/plan/judge) call.
    # Default keeps the legacy 10.0 (no behaviour change); lower it -- with the
    # cu_bench harness as proof -- to bound tail latency. The configured
    # per_step_timeout_s still applies when it is smaller.
    think_timeout_cap_s: float = Field(default=10.0, gt=0.0, le=60.0)
    # L7 (CU speed): per-screenshot byte budget sent to the model. Default keeps
    # 300_000 (no change); lowering it -- with the cu_bench harness as proof --
    # shrinks the vision payload for faster inference, at some grounding risk.
    image_max_bytes: int = Field(default=300_000, ge=20_000, le=2_000_000)
    # L7 (CU speed + grounding): per-screenshot longest-side pixel cap sent to
    # the model. Default 1366 — vision models ground small controls MORE
    # reliably on frames near the XGA/WXGA band than on raw 2K/4K captures
    # (provider guidance: downscale yourself, do not rely on API-side
    # resizing), and the smaller payload cuts encode + upload + image-token
    # latency. Raise it only with the cu_bench harness as proof. 0 disables
    # the dimension cap entirely.
    image_max_dimension: int = Field(default=1366, ge=0, le=8192)
    # L8 (CU speed): multiplier on the loop's fixed settle waits (pre-type and
    # post-click-verify pauses). Default keeps 1.0 (no change: every settle is
    # byte-for-byte the legacy duration); lower it -- with the cu_bench harness
    # as proof -- to trim dead time, at some risk of typing before a freshly
    # focused input is listening (the CU leading-char-drop bug it guards).
    settle_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    # L9 (CU speed): optional cheaper model id for trivial, unambiguous steps
    # (a deterministic click_element whose name is a known control label).
    # Default "" disables routing entirely -- today's behaviour, every step
    # uses the normal model. Set a fast model id to opt in once a live gate
    # wires the helper into the per-step model selection (see TODO L9 in
    # screenshot_only_loop.py).
    fast_step_model: str = Field(default="")
    verify_after_each_step: bool = True
    # Proactively zoom-refine each click target BEFORE clicking. DEFAULT OFF
    # since 2026-06-27: making it default-on added an extra model call AND a new
    # re-plan-on-not-found failure path to EVERY targeted click, which degraded
    # accuracy and latency instead of helping. The known-good pipeline clicks
    # the coarse point first and only refines AFTER a verified miss. Opt back in
    # per [computer_use] once a benchmark proves it nets out positive. When on:
    # the loop grabs a live zoomed crop, re-locates the target, then clicks —
    # and re-plans when the target is not in the crop. Internal crop only.
    zoom_before_click: bool = False
    # UIA fallback that snaps a verified-MISSED pixel click to the nearest
    # accessibility element. DEFAULT OFF since 2026-06-27: added 2026-06-24, it
    # snapped almost every near-miss to a large container's center (~screen
    # centre) — a wild click that also short-circuited the LLM refine that used
    # to correct misses (BUG-CU-UIASNAP). The pre-snap pipeline (coarse click ->
    # verify -> LLM refine on miss) is the known-good behaviour; re-enable per
    # [computer_use] only with a benchmark.
    uia_click_fallback: bool = False
    # Spoken per-step milestones ("Schritt N von M erledigt."). Default OFF
    # (2026-06-10): the milestone counter tracks successful actions, not
    # verified plan steps, so it announced "6 von 6 erledigt" on a mission
    # that was still struggling. Opt-in for users who want the narration.
    announce_progress: bool = False
    # 2026-06-14: switched from claude-fable-5 to claude-opus-4-8. The CU
    # planner calls the Brain API directly with no model-unavailable retry, and
    # fable-5 is approved-access-only / unreachable on the Claude Max
    # subscription ("Claude Fable 5 is currently unavailable") — so the planner
    # default must be a model we can actually reach.
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
    # When the user trails off on an incomplete/dangling fragment, OR the brain
    # returns an empty turn (Gemini function_call without narration / a slow CLI
    # brain timing out on the voice path), Jarvis can speak a short clarifying
    # question ("Wie meinst du das genau?") instead of staying silent.
    #
    # DEFAULT OFF since 2026-06-09 (maintainer mandate, REVERSES the 2026-06-08
    # opt-in): in practice the question fired on every empty brain turn —
    # interrogating the user about perfectly clear commands ("kannst du mein
    # Spotify öffnen?" → "Wie meinst du das genau?") and so blaming the user for
    # a brain-side glitch. The original "Jarvis hört für immer zu" report it was
    # built for had its real root cause (the playback-watchdog stale counter,
    # BUG-032) fixed separately, so the question lost its only justification and
    # was left as pure annoyance. With this off, an empty turn stays silent and
    # a normal turn answers normally — the genuinely useful AD-OE6 acks
    # (brain-unavailable message, "Erledigt." after a wordless desktop action,
    # silent fire-and-forget spawn) are independent of this flag and unaffected.
    # Set true to opt back into the clarifying-question behaviour.
    clarify_incomplete_enabled: bool = False
    # Grace window after an incomplete fragment is buffered before the
    # clarifying question fires. Long enough not to cut off a thinking pause
    # (the VAD already waited ``vad_silence_ms`` of silence before yielding the
    # fragment), short enough that the user is never left hanging. A continuation
    # arriving within this window cancels the question and joins the turn.
    clarify_after_ms: int = 2500
    # --- Continuation recombine (2026-06-16) -------------------------------
    # When the user keeps talking AFTER an utterance was already dispatched to
    # the brain (the brain is already thinking/speaking), abort the half-formed
    # answer and re-think the COMBINED sentence as one turn, instead of dropping
    # the earlier half as a fresh, context-less message. Master switch; false =
    # behaves exactly as before this feature. Spec:
    # docs/superpowers/specs/2026-06-16-voice-continuation-recombine-while-thinking-design.md
    continuation_interrupt_enabled: bool = True
    # How long AFTER the answer finished a new utterance still counts as a
    # continuation (the "kurze Nachfrist"). Kept short to bound the risk that a
    # genuinely new command is mis-attached.
    continuation_grace_ms: int = 2500
    # Max fragments coalesced into one turn before the next utterance is a fresh
    # turn (mirrors completion_max_chain — bounds indefinite chaining). Set
    # generously: users who correct themselves in several short bursts while the
    # brain is still thinking ("…nicht australische" → "Australien oder so, nein"
    # → "sondern der weiteste Ort") chain many fragments into ONE intended
    # prompt; a low cap drops the earliest context on the (cap+1)-th fragment.
    continuation_max_chain: int = 8
    # Floor (seconds) below which the canned "that took too long, say it again"
    # phrase is structurally SUPPRESSED, as a stale-state guard. None of the
    # three timeout paths (20 s no-first-frame ceiling / 30 s no-progress stall /
    # 30 s total cap) can legitimately fire faster than this, so a turn that
    # genuinely ran under the floor and is still about to apologise for slowness
    # is being driven by stale per-turn state (the no-first-frame mark — an
    # AP-19/BUG-032-class process-global flag), not a real timeout. Live user
    # report 2026-06-14: Jarvis apologised "right after" a sub-second turn.
    # Defaults to the stall window so the two stay consistent; the pipeline
    # clamps the effective value to <= the stall window so it can never muzzle a
    # genuine timeout. Raising it above the stall window has no effect (clamped).
    min_timeout_phrase_s: float = 30.0
    # Per-site floor for the NO-FIRST-FRAME timeout path specifically. That path
    # is beheaded at the (shorter) TTS no-first-frame ceiling, not the brain
    # stall window, so its suppression floor must track the ceiling — clamping it
    # to the 30 s stall window (as min_timeout_phrase_s does) would make a real
    # ~20 s abort fall under the floor and stay silent (live bug 2026-06-14: the
    # Berlin→Melbourne research turn). None → the pipeline derives it as a
    # fraction of the no-first-frame ceiling. Any set value is clamped to <= the
    # ceiling so it can never invert and re-introduce guaranteed silence.
    no_first_frame_phrase_floor_s: float | None = None


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

    # Voice endpoint silence window: how long the VAD waits in silence before
    # treating an utterance as finished. User-tunable "think buffer" (desktop
    # Settings → Voice slider). Range-clamped 500–5000 ms; default 1500 ms
    # ("1.5s rule"). Read at SpeechPipeline construction and live-applied via the
    # /api/settings/silence-window route. extra="allow" already on SpeechConfig
    # keeps the self-mod pre-validate pipeline safe (AP-16).
    vad_silence_ms: int = Field(default=1500, ge=500, le=5000)


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


class CodexConfig(BaseModel):
    """``[codex]`` — OpenAI Codex CLI integration.

    ``binary_path`` overrides the on-PATH ``codex`` resolution (Windows installs
    sometimes expose only ``codex.cmd`` in a non-PATH location). Empty = use the
    standard PATH lookup. Read by :class:`jarvis.codex_auth.CodexAuthService` and
    the provider routes; written via ``config_writer.set_codex_binary_path``.
    """
    binary_path: str = ""


class TeamProxyConfig(BaseModel):
    """Client-side team / hosted-proxy mode (2026-06-20 team-proxy spec §4).

    When ``enabled`` and a ``url`` is set, every provider whose id is NOT in
    ``local_providers`` is routed through the proxy at ``{url}/p/{provider_id}``
    using the per-user token (Credential Manager slot ``team_proxy_token`` /
    ENV ``TEAM_PROXY_TOKEN``) instead of a real vendor key. ``local_providers``
    is the escape hatch for providers that must stay direct/local (e.g. local
    Whisper that should never leave the machine).
    """

    enabled: bool = False
    url: str | None = None
    local_providers: list[str] = Field(default_factory=list)
    model_config = {"extra": "allow"}


class JarvisConfig(BaseModel):
    """Root config model."""
    # populate_by_name=True lets callers use Python field names alongside
    # validation aliases for the renamed sub_agents → jarvis_agents field.
    model_config = ConfigDict(populate_by_name=True)

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
    # ``validation_alias`` back-compat: old installs use [sub_agents];
    # new installs use [jarvis_agents]. Both populate this field transparently.
    jarvis_agents: JarvisAgentsOutputConfig = Field(
        default_factory=JarvisAgentsOutputConfig,
        validation_alias=AliasChoices("jarvis_agents", "sub_agents"),
    )
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
    # [codex] — OpenAI Codex CLI integration (binary path override).
    codex: CodexConfig = Field(default_factory=CodexConfig)
    # [team_proxy] — client-side team/hosted-proxy mode (2026-06-20 spec). When
    # enabled, providers are routed through a shared key proxy via a per-user
    # token instead of holding real vendor keys locally.
    team_proxy: TeamProxyConfig = Field(default_factory=TeamProxyConfig)


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
    # Post-rename name (2026-06-29 Jarvis-Agents rename): config_writer now
    # writes to this key; the drift-guard / boot-heal reads it going forward.
    "JARVIS__BRAIN__WORKER__PROVIDER",
    # Back-compat: pre-rename installs have this key in the Windows registry.
    # Kept so refresh_persisted_env_from_user_registry still heals it at boot.
    "JARVIS__BRAIN__SUB_JARVIS__PROVIDER",
    "JARVIS__TTS__PROVIDER",
    "JARVIS__STT__PROVIDER",
    # ack_brain subsystem master + flash provider selection. Same drift-guard
    # 3-layer sync as the provider tiers above, so a stale inherited value must
    # heal at boot too. Forensic 2026-06-21: an in-app restart inherited a
    # pre-change ancestor env with JARVIS__ACK_BRAIN__ENABLED=false /
    # PROVIDER=gemini; absent from this list it survived the restart (env > toml)
    # and kept the grounded spawn announcer in canned-pool mode even though the
    # registry already held enabled=true / provider=grok. The spoken spawn ACK
    # then stayed a generic stock phrase instead of context-aware text.
    "JARVIS__ACK_BRAIN__ENABLED",
    "JARVIS__ACK_BRAIN__PROVIDER",
    "JARVIS__ACK_BRAIN__FALLBACK_PROVIDER",
    # TTS engine selection beyond the provider tier. Forensic 2026-06-22: an
    # in-app restart inherited a stale env (JARVIS__TTS__USE_VERTEX=true /
    # MODEL=sonic-2 / VOICE_*=leo) from a pre-change ancestor. PROVIDER above
    # healed, but these did not, so Gemini-TTS stayed on the wrong Vertex
    # billing path (the user had topped up the AI-Studio key, which Vertex
    # ignores) with a bogus model name → 404 on every sentence, silent voice.
    # Pinning them here makes a restart honour the registry's corrected values.
    "JARVIS__TTS__MODEL",
    "JARVIS__TTS__USE_VERTEX",
    "JARVIS__TTS__VOICE_DE",
    "JARVIS__TTS__VOICE_EN",
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


def _migrate_worker_env_vars() -> None:
    """Process-local back-compat shim for the sub_jarvis → worker rename.

    If the OLD env vars (JARVIS__BRAIN__SUB_JARVIS__*) are set in os.environ
    but the NEW ones (JARVIS__BRAIN__WORKER__*) are not, copy old → new so
    _apply_env_overrides and pydantic's AliasChoices both see the expected
    values. This is process-local only (os.environ, NOT setx/registry).
    Called once from load_config before _apply_env_overrides.
    """
    for old_name, new_name in (
        ("JARVIS__BRAIN__SUB_JARVIS__PROVIDER", "JARVIS__BRAIN__WORKER__PROVIDER"),
        ("JARVIS__BRAIN__SUB_JARVIS__MODEL", "JARVIS__BRAIN__WORKER__MODEL"),
    ):
        old_val = os.environ.get(old_name)
        if old_val and not os.environ.get(new_name):
            os.environ[new_name] = old_val  # process-local only, no setx


def load_config(
    config_file: Path | None = None,
    profile: str | None = None,
) -> JarvisConfig:
    """Load config from TOML + optional YAML profile + env overrides.

    Precedence (lowest → highest):
      1. jarvis.toml (defaults)
      2. profiles/<active>.yaml
      3. Environment variables (JARVIS__*)

    ``config_file=None`` resolves through :func:`resolve_config_path` so the
    ``JARVIS_CONFIG`` override is honoured (cloud-first). An explicit path still
    wins for callers that target a specific file.
    """
    if config_file is None:
        config_file = resolve_config_path()
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

    # Back-compat shim: copy old JARVIS__BRAIN__SUB_JARVIS__* to new
    # JARVIS__BRAIN__WORKER__* if only the old names are set (process-local).
    _migrate_worker_env_vars()
    data = _apply_env_overrides(data)
    return JarvisConfig(**data)


# ----------------------------------------------------------------------
# Secrets (Windows Credential Manager via keyring)
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Headless credential store (C1, open-source AP-22)
# ----------------------------------------------------------------------
# On a headless Linux/VPS (python:3.11-slim — no D-Bus Secret Service / gnome-
# keyring / KWallet) the platform keyring resolves to ``fail.Keyring`` and every
# in-app key save / channel-connect / plugin-connect raises → the whole API-Keys
# section is unusable. We fall back to a local 0600 JSON file so a bare-VPS user
# can paste a key in the UI and have it persist. NOT a security feature; the OS
# keyring stays the secure path whenever it is functional (the file backend is
# installed ONLY when the current backend is the no-op fail.Keyring).
_KEYRING_BACKEND_READY: bool = False


class _FileCredStore:
    """Minimal 0600 JSON credential store keyed by ``(service, username)``."""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit = path

    def _file(self) -> Path:
        p = self._explicit if self._explicit is not None else (DATA_DIR / "credentials.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load(self) -> dict[str, str]:
        try:
            f = self._file()
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except Exception:  # noqa: BLE001 — a corrupt store must never crash a read
            return {}

    def _save(self, data: dict[str, str]) -> None:
        f = self._file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:  # noqa: BLE001 — chmod is a no-op on Windows
            pass
        os.replace(tmp, f)

    @staticmethod
    def _k(service: str, username: str) -> str:
        return f"{service}\x00{username}"

    def get(self, service: str, username: str) -> str | None:
        return self._load().get(self._k(service, username))

    def set(self, service: str, username: str, password: str) -> None:
        data = self._load()
        data[self._k(service, username)] = password
        self._save(data)

    def delete(self, service: str, username: str) -> None:
        data = self._load()
        data.pop(self._k(service, username), None)
        self._save(data)


def _ensure_keyring_backend() -> None:
    """Install the local-file credential store when the OS keyring is non-functional.

    Runs once. Installs the file backend ONLY when the current backend is the no-op
    ``fail.Keyring`` (so a working Windows/macOS/Linux OS keyring is never replaced).
    Any error is swallowed — a missing keyring must never break boot.
    """
    global _KEYRING_BACKEND_READY
    if _KEYRING_BACKEND_READY:
        return
    _KEYRING_BACKEND_READY = True
    try:
        import keyring
        import keyring.backend
        from keyring.backends import fail

        if not isinstance(keyring.get_keyring(), fail.Keyring):
            return

        _store = _FileCredStore()

        class _FileKeyringBackend(keyring.backend.KeyringBackend):
            priority = 0.1  # type: ignore[assignment]  # below any real OS backend

            def get_password(self, service: str, username: str) -> str | None:
                return _store.get(service, username)

            def set_password(self, service: str, username: str, password: str) -> None:
                _store.set(service, username, password)

            def delete_password(self, service: str, username: str) -> None:
                _store.delete(service, username)

        keyring.set_keyring(_FileKeyringBackend())
        logging.getLogger(__name__).warning(
            "No OS credential store available (headless host) — API keys are stored "
            "in a local 0600 file under %s. Configure a Secret Service / Keychain "
            "for OS-encrypted storage.", DATA_DIR / "credentials.json",
        )
    except Exception:  # noqa: BLE001
        pass


def get_secret(key: str, env_fallback: str | None = None) -> str | None:
    """Retrieve a secret value. Priority: keyring → ENV fallback → .env.

    Args:
        key: Secret name in the Credential Manager (e.g. "anthropic_api_key").
        env_fallback: ENV variable checked when keyring is empty (e.g. "ANTHROPIC_API_KEY").
    """
    _ensure_keyring_backend()
    # H2 (open-source AP-22 / headless VPS): when the caller passes no explicit ENV
    # var, derive it from the slot name (``groq_api_key`` → ``GROQ_API_KEY``) so the
    # documented keyring → ENV → .env hierarchy holds for EVERY slot — not only the
    # brain providers whose callers happen to pass one. On a host with no OS keyring
    # (python:3.11-slim) the ENV path is the only credential input until C1 lands.
    if env_fallback is None:
        env_fallback = key.upper()

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


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """Effective endpoint + credential for a provider on this turn.

    ``via_proxy`` is always False in W1a; the team-proxy slice (W2) sets it True
    when the team proxy is the resolved target. ``base_url=None`` means "use the
    SDK's own default endpoint".
    """

    base_url: str | None
    credential: str | None
    via_proxy: bool


def resolve_provider_endpoint(
    provider_id: str,
    *,
    vendor_default_base_url: str | None = None,
    config: JarvisConfig | None = None,
) -> ResolvedEndpoint:
    """Resolve the effective endpoint + credential for a provider.

    W1a precedence: an explicit ``[brain.providers.<id>].base_url`` override if
    set, else the caller's ``vendor_default_base_url``. The credential stays the
    provider's own configured secret (``get_provider_secret``). The ``config``
    argument exists for tests; production passes ``None`` → ``load_config()``.

    This is purely additive in direct mode: with no override configured,
    ``base_url`` equals the vendor default (or ``None``) and behaviour is
    unchanged.

    Team mode (W2): when ``[team_proxy].enabled`` and a ``url`` is set and the
    provider is not in ``local_providers``, the endpoint becomes
    ``{url}/p/{provider_id}`` and the credential becomes the per-user team token
    (``team_proxy_token``) — the same flip for every provider class.
    """
    cfg_obj = config if config is not None else load_config()

    team = cfg_obj.team_proxy
    if team.enabled and team.url and provider_id not in team.local_providers:
        base_url = f"{team.url.rstrip('/')}/p/{provider_id}"
        token = get_secret("team_proxy_token", "TEAM_PROXY_TOKEN")
        return ResolvedEndpoint(base_url=base_url, credential=token, via_proxy=True)

    override: str | None = None
    prov = cfg_obj.brain.providers.get(provider_id)
    if prov is not None and prov.base_url:
        override = prov.base_url
    base_url = override or vendor_default_base_url
    credential = get_provider_secret(provider_id)
    return ResolvedEndpoint(base_url=base_url, credential=credential, via_proxy=False)


def set_secret(key: str, value: str) -> bool:
    """Store a secret in the OS keyring (or the headless 0600 file fallback).

    Returns True on success. C1: the in-app API-Keys section writes through here,
    so the headless file fallback is what makes a fresh VPS user able to save a key.
    """
    _ensure_keyring_backend()
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def delete_secret(key: str) -> bool:
    """Remove a secret from the OS keyring (or the headless file fallback)."""
    _ensure_keyring_backend()
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, key)
        return True
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------
# First-run check
# ----------------------------------------------------------------------

def ensure_project_root_cwd() -> Path:
    """Pin the process working directory to the project root. Returns the CWD.

    Several persistence paths are resolved relative to ``os.getcwd()`` under the
    historical assumption that the desktop app always launches from the repo
    root — the onboarding state file (``data/setup_state.json``), the SQLite DBs
    (chats / sessions / missions / friends / jarvis), the flight recorder, and
    the self-mod / review audit logs. That assumption is false in practice: the
    autostart Scheduled Task sets a WorkingDirectory, but a manual start or an
    in-app restart inherits the user home (observed live CWD: ``C:\\Users\\<user>``).
    The same install then read/wrote a *different* ``data/`` dir per start method
    — re-showing the first-run setup guide on every restart and splitting the
    user's Chats/Sessions/Missions across two folders.

    It also pins the repo root onto ``sys.path``. ``python -m`` seeds
    ``sys.path[0]`` from the *start-time* cwd, and a later ``os.chdir`` does NOT
    patch the import path. A start from a foreign cwd (manual launch / an in-app
    restart inheriting the user home) therefore left the repo root off
    ``sys.path``, so the ROOT packages ``ui`` and ``conductor`` — which live
    outside the editable-installed ``jarvis`` package — failed to import
    ("No module named 'ui'"), silently disabling the on-screen overlay
    (jarvis-bar) and the Conductor view. Putting the root on the path makes
    those imports resolve regardless of how the process was started.

    Call this once, as early as possible in every process entry point (before
    ``load_config`` and before the server touches any ``data/`` path). It is
    idempotent and never raises: a chdir failure is logged and the process
    continues with whatever CWD it had.
    """
    import logging

    root = str(PROJECT_ROOT)
    if root not in sys.path:
        # First, mirroring the `python -m` cwd seeding the working boots had.
        sys.path.insert(0, root)

    if Path.cwd() != PROJECT_ROOT:
        try:
            os.chdir(PROJECT_ROOT)
            logging.getLogger(__name__).info(
                "Pinned working directory to project root: %s", PROJECT_ROOT
            )
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Could not pin CWD to project root %s: %s", PROJECT_ROOT, exc
            )
    return Path.cwd()


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
        ack_continuation_grace_ms: int = 1200

JarvisConfig.model_rebuild()
