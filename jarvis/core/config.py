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
import secrets
import sys
import threading
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

# Sub-config from the awareness sub-package. A top-level import is fine because
# jarvis.awareness.config only knows Pydantic and never calls back into core.* —
# no circular-import risk.
from jarvis.awareness.config import AwarenessConfig

# wake_constants is pure stdlib (no jarvis imports) — safe to import from this
# foundational config module without a cycle. Single source of truth for the
# wake-engine enum + the default phrase.
from jarvis.speech.wake_constants import DEFAULT_WAKE_PHRASE, WAKE_ENGINES

from .branding import CONFIG_FILE_NAME, KEYRING_SERVICE_NAME
from .protocols import RiskTier

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
DEFAULT_CONFIG_FILE = PROJECT_ROOT / CONFIG_FILE_NAME
PROFILES_DIR = PROJECT_ROOT / "profiles"
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"

# Guards _resolve_writable_data_dir()'s lazy writability probe below. Named
# separately from config_writer's _WRITE_LOCK — this protects the (rare,
# once-per-process) directory-resolution probe, not a jarvis.toml write.
_data_dir_lock = threading.Lock()
_data_dir_cache: tuple[Path, Path] | None = None  # (DATA_DIR seen, resolved dir)


def _resolve_writable_data_dir() -> Path:
    """Return the directory to use for local credential-store persistence.

    Honors ``JARVIS_DATA_DIR`` when set (headless hosts / read-only
    site-packages installs where ``PROJECT_ROOT/data`` cannot be created).
    Otherwise defaults to :data:`DATA_DIR`, falling back to the per-user
    app-data directory when that path turns out not to be writable. The
    writability probe is cheap but still touches the filesystem, so it runs
    once — lazily, on first use, never at import time — and the result is
    cached until ``DATA_DIR`` itself changes (tests monkeypatch it directly).

    Scoped to the local-file credential fallback only; every other consumer
    of :data:`DATA_DIR` in this codebase keeps reading that constant
    directly and is unaffected.
    """
    global _data_dir_cache
    env_dir = os.environ.get("JARVIS_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip())
    with _data_dir_lock:
        if _data_dir_cache is not None and _data_dir_cache[0] == DATA_DIR:
            return _data_dir_cache[1]
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            probe = DATA_DIR / f".write_probe_{os.getpid()}"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            resolved = DATA_DIR
        except OSError:
            from jarvis.core.paths import user_data_dir

            resolved = user_data_dir()
            logging.getLogger(__name__).info(
                "project data directory %s is not writable — using %s for "
                "local credential storage instead",
                DATA_DIR,
                resolved,
            )
        _data_dir_cache = (DATA_DIR, resolved)
        return resolved


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

KEYRING_SERVICE = KEYRING_SERVICE_NAME

# Provider-secrets are intentionally kept out of TOML. Keep the accepted
# Credential-Manager slots and ENV fallbacks in one place so pre-boot checks,
# Frontier resolving and provider adapters do not disagree about whether a
# provider is configured.
PROVIDER_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "claude-api": (("anthropic_api_key", "ANTHROPIC_API_KEY"),),
    # The trailing realtime slot is a LAST-RESORT cross-read for single-key
    # installs (see the comment above "openai-realtime" below); the generic
    # slot always wins when both exist.
    "openai": (
        ("openai_api_key", "OPENAI_API_KEY"),
        ("realtime_openai_api_key", "JARVIS_REALTIME_OPENAI_API_KEY"),
    ),
    # Codex-as-brain uses its own key slot, falling back to the general OpenAI key.
    "codex": (
        ("codex_openai_api_key", "OPENAI_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
    ),
    "openrouter": (("openrouter_api_key", "OPENROUTER_API_KEY"),),
    # Groq remains a speech-to-text credential family even though it is not a
    # selectable brain provider.
    "groq": (("groq_api_key", "GROQ_API_KEY"),),
    # NVIDIA NIM (OpenAI-compatible). Only the build.nvidia.com key (nvapi-),
    # not the legacy NGC key. One key, many NVIDIA-hosted models.
    "nvidia": (("nvidia_api_key", "NVIDIA_API_KEY"),),
    "gemini": (
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
        ("realtime_gemini_api_key", "JARVIS_REALTIME_GEMINI_API_KEY"),
    ),
    "grok": (
        ("grok_api_key", "GROK_API_KEY"),
        ("xai_api_key", "XAI_API_KEY"),
    ),
    # Realtime owns dedicated slots. The generic family slots remain trailing
    # read-only compatibility fallbacks for upgraded installations. The reverse
    # direction exists too (see the trailing realtime slots on "openai" and
    # "gemini" above): a user whose ONLY credential was saved from the Realtime
    # card must still get a working Brain/Tool-Model of the same family — the
    # strict one-way scoping bricked every delegated deep turn on such an
    # install ("Kein Brain-Key gefunden" while  # i18n-allow: quoted diagnostic
    # Gemini-Live answered smalltalk, Mac forensic 2026-07-21). Precedence is
    # unchanged whenever a generic family key exists: dedicated slots win on
    # their own surface, generic slots win for Brain, and the cross-read only
    # fires as last resort.
    "openai-realtime": (
        ("realtime_openai_api_key", "JARVIS_REALTIME_OPENAI_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
    ),
    "gemini-live": (
        ("realtime_gemini_api_key", "JARVIS_REALTIME_GEMINI_API_KEY"),
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    ),
    # "grok-realtime" was removed 2026-07-16 (BUG-064 deaf-session wedge);
    # any stored realtime_grok_api_key simply stays unused in its backend.
}

# Jarvis-Agent API credentials are independently replaceable from the Agent
# tab. Generic provider slots remain a final compatibility fallback so existing
# installations keep working, but scoped Agent keys always win and are never
# consumed by Brain or Realtime. (Because these tuples splice the family
# candidates, a realtime-only key reaches the Agent tier through the same
# last-resort cross-read as the Brain tier — intentional: a single-key
# install must never brick a core path.)
JARVIS_AGENT_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "claude-api": (
        ("jarvis_agent_anthropic_api_key", "JARVIS_AGENT_ANTHROPIC_API_KEY"),
        *PROVIDER_SECRET_CANDIDATES["claude-api"],
    ),
    "openai": (
        ("jarvis_agent_openai_api_key", "JARVIS_AGENT_OPENAI_API_KEY"),
        *PROVIDER_SECRET_CANDIDATES["openai"],
    ),
    "gemini": (
        ("jarvis_agent_gemini_api_key", "JARVIS_AGENT_GEMINI_API_KEY"),
        *PROVIDER_SECRET_CANDIDATES["gemini"],
    ),
    "openrouter": (
        ("jarvis_agent_openrouter_api_key", "JARVIS_AGENT_OPENROUTER_API_KEY"),
        *PROVIDER_SECRET_CANDIDATES["openrouter"],
    ),
    "grok": (
        ("jarvis_agent_grok_api_key", "JARVIS_AGENT_GROK_API_KEY"),
        *PROVIDER_SECRET_CANDIDATES["grok"],
    ),
    "nvidia": (
        ("jarvis_agent_nvidia_api_key", "JARVIS_AGENT_NVIDIA_API_KEY"),
        *PROVIDER_SECRET_CANDIDATES["nvidia"],
    ),
}

_PROVIDER_SECRET_OVERRIDES: ContextVar[Mapping[str, str | None] | None] = ContextVar(
    "provider_secret_overrides", default=None
)


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

    # Which assistant mode is active — the user's deliberate, sticky choice
    # (``jarvis.brain.modes``). Deliberately a free string rather than a
    # Literal: user-created modes are the point of the feature, so the closed
    # set lives in ``modes.BUILTIN_SLUGS`` and an unknown value degrades to the
    # default at read time instead of failing config validation at boot.
    #
    # NOT the whole story on purpose: the Agentic IDE's screen-scoped override
    # is in-memory only and never reaches this file, so a mode a screen turned
    # on can never outlive the process.
    active_mode: str = "assistant"


class WakeWordConfig(BaseModel):
    """User-editable ``[trigger.wake_word]`` — the custom-wake-word config.

    ``phrase`` is the single source of truth (the human wake word). ``engine``
    selects how it is detected; ``resolve_wake_plan`` turns this into a concrete
    plan. See docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md.
    """

    # extra="allow": survive future [trigger.wake_word.*] sub-keys and any
    # legacy key through a self-mod pre-validate round-trip (AP-16).
    model_config = ConfigDict(extra="allow")

    # The human wake word the user wants — any phrase of their choice.
    # The single source of truth the UI/wizard edit.
    phrase: str = DEFAULT_WAKE_PHRASE
    # Detection engine. "auto" resolves the best generic path for the phrase:
    #   user-trained custom .onnx -> any-word Vosk keyword spotting ->
    #   local-Whisper transcript match -> honest hotkey-only degrade
    #   (no bundled model, no branded fallback — design 2026-07-07).
    # Validated against wake_constants.WAKE_ENGINES; unknown coerces to "auto"
    # so a stale/hand-edited value cannot brick the boot (AP-16).
    engine: str = "auto"
    # Path to a user-supplied/trained .onnx wake model (engine="custom_onnx").
    custom_model_path: str = ""
    # The language the user SPEAKS their wake word in — an INDEPENDENT setting,
    # deliberately decoupled from the app display language ([ui].language) and
    # the general recognition language ([stt].language). "auto" keeps the
    # legacy cascade (stt -> ui -> default) so existing installs behave
    # unchanged; a concrete code ("de"/"en"/"es") pins the wake model's
    # language for good — switching the app language never moves it again.
    # Resolved by jarvis/speech/wake_model_fetch.py::resolve_wake_language.
    language: str = "auto"
    # READ-COMPAT ONLY, runtime-ignored since 2026-07-10: the user-facing
    # Sensitivity slider was removed (mandate: always run every wake path at
    # its calibrated-reliable maximum-speed value, identically on every OS —
    # no per-user tuning). ``resolve_wake_plan`` no longer reads this field.
    # Kept so an existing jarvis.toml with a hand-set ``sensitivity`` still
    # parses and boots cleanly (open-source §3 / AP-16 back-compat); the
    # floor validator stays only to keep any stored value well-formed.
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

    @field_validator("language", mode="before")
    @classmethod
    def _coerce_language(cls, value: object) -> str:
        # Normalize only — membership is checked by resolve_wake_language, so an
        # unknown value simply falls through the cascade instead of failing
        # validation (a stale/hand-edited config must never brick boot, AP-16).
        text = str(value or "").strip().lower()
        return text or "auto"

    @field_validator("sensitivity", mode="before")
    @classmethod
    def _floor_sensitivity(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        # Lift below-floor values instead of rejecting: a stale config must
        # never fail validation (AP-16), and 0.5 restores a working wake.
        return min(1.0, max(0.5, number))


class TriggerConfig(BaseModel):
    wake_word_enabled: bool = False
    # Deprecated compatibility field. Older installs may still carry this
    # push-to-talk key in jarvis.toml, so the config model continues to accept
    # it, but the desktop no longer registers or exposes it.
    hotkey: str = ""
    # Call/answer toggle key. Was hardcoded "f3+f4" in resolve_hotkeys() and at
    # the SpeechPipeline call sites; now user-editable via /api/settings/keybinds.
    hotkey_call: str = "f3+f4"
    # Hangup key. Was hardcoded ("f1+f2",) at the SpeechPipeline call sites; now
    # user-editable via /api/settings/keybinds. Read directly at bootstrap.
    hotkey_hangup: str = "f1+f2"
    # Push-to-talk dictation key: HOLD to speak, release to insert. The
    # transcript lands in whatever text field currently has focus.
    #
    # Ships BOUND (maintainer directive 2026-07-28). Dictation shipped unbound
    # for a while on the theory that no chord is free on every machine; the
    # result was a headline feature that did nothing on a fresh install unless
    # the user happened to open the Shortcuts tab. The two combos below are
    # curated instead: both pass ``validate_hotkey`` on win32, darwin AND linux,
    # neither is an OS-reserved chord, and their key sets are neither subsets
    # nor supersets of each other or of Call (``f3+f4``) / Hangup (``f1+f2``),
    # so the keybind collision rule accepts all four at once. Clearing a row in
    # the Voice → Shortcuts tab is one click, and an empty value remains a
    # fully valid state: dictation still starts from the bar, from the UI, and
    # from ``jarvis api dictation start`` — the last of which is the documented
    # Wayland path, where the compositor (not the app) owns global shortcuts.
    hotkey_dictate: str = "ctrl+right_alt+j"
    # Hands-free dictation key: press once to start, press again to stop. Its
    # own action (not ``[dictation].mode``) so a user can have BOTH a hold key
    # and a toggle key armed at the same time; ``[dictation].mode = "toggle"``
    # stays honoured for installs that configured it, and only changes how
    # ``hotkey_dictate`` behaves.
    hotkey_dictate_toggle: str = "ctrl+right_alt+space"
    # Insert the most recent dictation into the focused field AGAIN — the
    # recovery key for a paste that landed nowhere. It exists because the
    # clipboard is not a fallback here: a successful paste deliberately puts
    # the previous clipboard content back, so the transcript is gone from the
    # clipboard a second later and the local history is the only durable copy.
    #
    # Ships bound for the same reason the two dictation keys do (a recovery
    # action nobody can find is not a recovery action). Ctrl+Alt+V is the
    # documented suggestion; it does overlap with "Paste Special" in some
    # office suites, and because the hotkey backend POLLS key state rather than
    # registering with the OS, the key is not swallowed — both would fire.
    # Clearing the row is one click, and an empty value stays fully valid: the
    # action is also `jarvis api dictation paste-last`, which is the documented
    # Wayland path (there the compositor, not the app, owns global shortcuts).
    # A macOS user can record a Command-based combination instead; the
    # validator accepts `cmd+...` on darwin.
    hotkey_paste_last: str = "ctrl+alt+v"
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    # When false (default), the pipeline keeps the mic open after the
    # response (conversation mode) and only hangs up via HANGUP_RE, the idle
    # timeout, or a hotkey. When true, every voice turn ends after Jarvis
    # finishes speaking and a fresh wake is required for the next turn.
    # History: single-turn became the default 2026-05-18 because open-mic mode
    # then triggered on every word in the room; the endpointing/echo fixes
    # since removed that failure, and shipping single-turn made every fresh
    # install feel broken ("Jarvis hangs up after each answer") while the
    # maintainer's local jarvis.toml quietly overrode it — the AP-23 class.
    # Maintainer directive 2026-07-18: conversation mode is the shipped
    # behaviour; single-turn stays available as an opt-in.
    single_turn_mode: bool = False
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
    # Deprecated compatibility field, accepted so existing configuration files
    # keep loading after the desktop push-to-talk feature was removed. Its value
    # is intentionally ignored by ``resolve_hotkeys``.
    push_to_talk: bool = False

    def resolve_hotkeys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return the active call hotkey and an empty legacy PTT slot.

        ``SpeechPipeline`` still accepts the two-tuple during the compatibility
        window, but standard desktop construction never arms push-to-talk. A
        blank call key means the user explicitly cleared the action in Settings.
        """
        return (tuple(h for h in (self.hotkey_call,) if h.strip()), ())
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
    # Set by every user-facing provider switch. Once present, the persisted
    # provider is authoritative over a stale JARVIS__STT__PROVIDER inherited
    # from an older desktop process or User-scope environment entry. Fresh
    # headless installs keep the normal ENV-over-TOML contract until a user
    # explicitly chooses a provider through Jarvis.
    provider_user_selected: bool = False
    # Where transcription goes when ``provider`` keeps failing at RUNTIME — a
    # depleted or revoked key (401/402), or a rate limit that survives the
    # retry ladder. ``auto`` (the default) asks the key-aware resolver for a
    # provider in a DIFFERENT family that the user actually holds a credential
    # for; crossing to a second provider in the same family buys nothing,
    # because one dead key takes both down (AP-22). A concrete id pins the
    # fallback to that provider, and an empty value disables crossing entirely
    # and keeps the honest single-provider failure. Honoured by the runtime
    # cross-family fallback in ``jarvis.plugins.stt`` — this key was carried in
    # shipped configs for a long time while nothing read it, which is why it is
    # spelled out here: dead config is a lie.
    fallback: str = "auto"
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
    # When True AND a real turbo/cuda inference has been VERIFIED on this host,
    # a CUSTOM wake phrase (the transcription-based ``stt_match`` path) runs the
    # strong ``large-v3-turbo`` model on the GPU instead of the small ``base``
    # model on the CPU — far better proper-noun recall and ~120 ms/window vs
    # ~700 ms. History: the default was flipped to False on 2026-06-30 because
    # on the maintainer's Blackwell GPU (RTX 5070 Ti / sm_120) CTranslate2's
    # ``model.transcribe`` hung on every live inference under the then-current
    # runtime (AP-25). Re-measured 2026-07-05 on the same GPU (ctranslate2
    # 4.7.1 + torch 2.11-cu128): 40/40 inferences under in-process torch-OpenMP
    # load, zero hangs, p50 117 ms — the hang was constellation-specific, not
    # "Blackwell forever". The gate is therefore no longer this blind flag but
    # an automated out-of-process inference probe (one killable subprocess run,
    # cached per ctranslate2 version — ``jarvis.plugins.stt.
    # _wake_gpu_inference_verified``), plus a live backstop that drops back to
    # base/cpu and persists the bad verdict if the swapped-in GPU model ever
    # wedges. The transcription wake still cannot reach "Hey Google" reliability
    # — that needs a trained neural keyword-spotting model (``custom_onnx``).
    #
    # DEFAULT FLIPPED TO CPU (2026-07-09): the GPU hot-swap is now OFF by
    # default and this flag is the COMPLETE CPU↔GPU switch for the wake path
    # (it gates BOTH the plain and the custom-phrase turbo/cuda branch in
    # build_wake_whisper AND the background hot-swap). Rationale: the GPU wake
    # relied on two sticky, never-re-probed caches (data/wake_cuda_probe.json,
    # data/wake_gpu_probe.json) — a single transient first-probe failure latched
    # the wake to the wedge-prone base/cpu model across EVERY future restart with
    # no log explaining why ("worked fast, then dead after a reboot"). A
    # CPU-first wake is the universal, reproducible floor (matches §3's
    # torch-/GPU-free base). Power users can still opt in with
    # ``wake_high_accuracy = true`` (the automated inference probe + live backstop
    # still guard it). The always-on vosk_kws engine is CPU-only regardless.
    wake_high_accuracy: bool = False
    language: str = "auto"
    # Vocabulary biasing passed to Whisper's ``prompt`` field — the same
    # mechanism commercial dictation tools use to keep proper nouns and
    # domain terms stable. Empty string means "no bias", and the cloud STT
    # plugin caps overly-long values internally. Read by the cloud STT
    # plugins (currently Groq); the local FasterWhisperProvider intentionally
    # ignores it because an initial-prompt on silent audio used to
    # hallucinate the prompt itself as the transcript.
    bias_prompt: str = ""
    # Which model each CLOUD recognizer uses, keyed by provider id
    # (``{"openrouter-stt": "openai/gpt-4o-transcribe"}``). A per-provider slot
    # rather than one global value, because ``model`` above is a
    # faster-whisper checkpoint name and a checkpoint name is meaningless to a
    # hosted API — forwarding one global string to whichever provider happened
    # to be selected is how a picked cloud model reached no provider at all
    # and a fresh install would have posted ``large-v3-turbo`` to Groq.
    # Unset (the default) means "the plugin's own default model", which is the
    # behaviour every install had before this key existed.
    models: dict[str, str] = Field(default_factory=dict)
    # Sampling temperature for transcription. ``0.0`` on purpose and by
    # default: transcription is a measurement, so the same recording has to
    # come back the same way twice. Forwarded only to providers whose model
    # accepts the field (``jarvis.plugins.stt.capabilities``), so a backend
    # that rejects it keeps working.
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)


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
    # Master output volume knob, 0.0–1.0 (1.0 = 100% = loudest). Consumed by the
    # shared gain helper (jarvis.audio.gain), which scales it to a makeup boost +
    # soft limiter so 100% is genuinely loud — raw TTS speech is far quieter than
    # mastered music — while below the unity point it plainly attenuates. Applied
    # to EVERY sink (local speaker, browser voice, telephony), so it works on
    # every OS and transport, including a headless server with no audio device.
    # Provider-independent; editable live from the Settings "Volume" slider.
    volume: float = Field(default=1.0, ge=0.0, le=1.0)
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
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    model: str | None = None
    deep_model: str | None = None      # Optional: stronger reasoning model
    # Canonical model for tool-bearing turns. ``cu_model`` remains a
    # compatibility alias for installations predating the Tool Model rename.
    tool_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("tool_model", "cu_model"),
    )
    # Realtime voice pick (selectable Realtime model + voice). Only meaningful
    # for the two realtime-tier providers (openai-realtime / gemini-live); the
    # realtime model reuses `model` above. "" -> the adapter's own hardcoded
    # default voice (no regression for providers/tiers that never set this).
    voice: str = ""
    auth_mode: str | None = None       # "oauth" | "api_key"
    base_url: str | None = None
    # Self-hosted cards only (today: local-realtime): the command Jarvis runs
    # to start/revive the server behind ``base_url`` when it is unreachable.
    # Empty = Jarvis never spawns anything; the user runs the server manually.
    launch_command: str = ""
    # Latency sprint 1 (2026-04-30): Gemini thinking budget per provider tier.
    # Value is forwarded to ``types.ThinkingConfig.thinking_budget``.
    # ``None``  → SDK default (auto-budget, highest latency footprint).
    # ``0``     → thinking disabled (e.g. router tier — pure tool routing
    #             needs no reasoning).
    # ``-1``    → dynamic-auto (provider decides per request).
    # ``> 0``   → fixed token cap for the thinking portion.
    # Currently only evaluated by ``GeminiBrain``; other providers ignore it.
    thinking_budget: int | None = None

    @property
    def cu_model(self) -> str | None:
        """Compatibility alias for callers that still use the old field name."""
        return self.tool_model

    @cu_model.setter
    def cu_model(self, value: str | None) -> None:
        self.tool_model = value


class BrainPolicyConfig(BaseModel):
    use_routing_model_for_intent: bool = True
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
    an LLM tool choice — the user utterance is passed verbatim to the
    Jarvis-Agent bridge (Wave-4 migration: previously the sub-Jarvis tier).

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
        # Analysis/investigation (2026-07-18: promoted from the maintainer's
        # field-tuned jarvis.toml — fresh installs missed these and answered
        # "analysiere das Repo" inline instead of spawning a worker)
        "analysier", "analyz", "untersuche", "untersuch",
        "pruefe", "prüfe", "investigate", "examine", "inspect",
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
        "delegier",
    ])

    # External system markers — when the utterance mentions a repo/PR/issue,
    # we spawn even without a clear action verb (e.g. "How many PRs are open?").
    external_system_markers: list[str] = Field(default_factory=lambda: [
        "pr", "prs", "issue", "issues", "repo", "repository",
        "github", "gitlab", "branch",
        # 2026-07-18: promoted from the maintainer's field-tuned jarvis.toml
        # so agent/worker mentions spawn on fresh installs too.
        "subagent", "subagenten", "sub-agent", "sub-agents",
        "openclaw", "open-claw",
    ])

    # Force-Spawn-Phrases (User-Mandate 2026-05-14): explicit-only trigger
    # list. When `force_spawn_mode = "strict"` (default), ONLY these phrases
    # cause a spawn — everything else stays inline in the router brain.
    # Earlier behaviour (every spawn_verb hit = spawn) was too eager and
    # spawned heavy workers for trivial knowledge questions like
    # "Was ist ein Verbrenner-Motor?". The list captures the user's actual
    # signals for "I want a heavy worker, not a one-shot answer":
    # explicit Jarvis-Agent / sub-agent mentions plus deep-research markers.
    force_spawn_phrases: list[str] = Field(default_factory=lambda: [
        # Explicit Jarvis-Agent / sub-agent mentions. The "openclaw" variants
        # are the retired internal codename, kept as a legacy trigger so a
        # user who still says it out loud keeps working.
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
    # heuristic. Default is "strict" per user mandate 2026-05-14; since the
    # 2026-07-21 mandate strict is EXPLICIT-ONLY — a background agent starts
    # only when the user names the vehicle or a delegation/depth trigger from
    # `force_spawn_phrases`. The former implicit strict-mode spawns (generic
    # sub-agent work, heavy-research artifacts, build-a-deliverable) are
    # retired; non-explicit heavy tasks are answered inline or OFFERED as a
    # delegation the user confirms (jarvis/brain/spawn_gate.py).
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

    # Heavy-research classifier (live bug 2026-06-14, a long-haul trip-research
    # turn). Conjunctive gate (precision over recall): a research/analysis VERB
    # must be present AND a heaviness signal — a horizon/multi-step marker, OR
    # >= heavy_research_min_verbs_multiclause verb matches (multi-clause), OR
    # length >= heavy_research_min_chars with a verb. RETIRED from the spawn
    # decision 2026-07-21 (strict mode is explicit-only; see force_spawn_mode
    # above) — the fields stay for config compatibility and the classifier
    # remains available for telemetry/tests. See ADR-0011.
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
    # smalltalk inputs go straight through the brain, not via Jarvis-Agent spawn.
    smalltalk_allowlist: list[str] = Field(default_factory=lambda: [
        # Greetings / Hangup
        "hallo", "hi", "hey", "moin", "guten morgen", "guten abend",
        "auf wiedersehen", "tschuess", "tschüss", "bye",
        "goodbye", "good morning", "good evening",
        # Smalltalk
        "wie geht", "how are you", "how's it going",
        "was machst du", "was machen wir",  # neutralise "mach" as a verb trigger
        "danke", "thank you", "thanks",
        # 2026-07-18: promoted from the maintainer's field-tuned jarvis.toml —
        # casual openers fresh installs misrouted to the action path.
        "was geht", "es geht ab", "geht ab",
        "alles gut", "alles fit", "alles klar", "passt schon",
        "what's up", "whats up", "sup",
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
    # replaced by the Jarvis-Agent bridge (see docs/jarvis-agents-bridge.md §11).
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
    # Realtime-tier provider preference + cross-family fallback chain (AP-22).
    # None until the user opts into realtime voice. Reuses BrainTierConfig so the
    # fallback shape matches [brain.router]/[brain.worker].
    realtime: BrainTierConfig | None = None
    # Canonical provider for tool-bearing turns in Pipeline and Realtime.
    # ``computer_use`` below remains a compatibility alias for older configs.
    tool_model: BrainTierConfig | None = Field(
        default=None,
        validation_alias=AliasChoices("tool_model", "computer_use"),
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

    @property
    def computer_use(self) -> BrainTierConfig | None:
        """Compatibility alias for the old ``[brain.computer_use]`` tier."""
        return self.tool_model

    @computer_use.setter
    def computer_use(self, value: BrainTierConfig | None) -> None:
        self.tool_model = value


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
    # rejects any residual length-capped generation. The Stage-2 judge
    # returns FULL page bodies per add/update, so a batched response
    # needs several thousand tokens (live 2026-07-17: 4000 truncated on
    # every provider and stalled the whole chain).
    max_output_tokens: int = 8000
    # Background path — latency-insensitive. CLI-login providers (codex
    # via ChatGPT OAuth) regularly need >90 s for a full 8-candidate
    # batch with complete page bodies; a timeout that always fires turns
    # the fallback provider into a 90 s tax on every failed primary.
    timeout_s: float = 180.0


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
        Slug of the user's own entity page. Empty or unsafe values resolve to
        the neutral ``user`` slug in the conversation-memory pipeline, so a
        fresh install uses ``entities/user.md`` and carries no personal name.
        Onboarding may configure a safe slug later. When that page exists,
        every session page links it in the ``## Related`` backbone footer,
        wiring each session into the graph through the shared user hub instead
        of floating as an island.
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
    user_entity_slug: str = ""
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
    # A durable candidate must become visible in the vault promptly, but not
    # necessarily on its own dedicated judge run: each Stage-2 run re-sends an
    # ~11k-char system prompt plus full neighbour page bodies, so firing per
    # candidate multiplied that fixed cost across every fact-yielding turn
    # (2026-07-28 cost audit). Three coalesces an active conversation's burst
    # into one run, and the age flush below still bounds how long a LONE
    # candidate can wait — that pair is what keeps spec A4's visibility
    # promise.
    consolidate_after_candidates: int = 3
    # Age-based flush (spec A4): even below the count threshold, pending
    # candidates older than this become a JOURNAL trigger so a quiet fresh
    # install still produces visible pages. 0 disables the age flush.
    flush_pending_max_age_minutes: int = 10


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

    ``rate_limit_seconds`` is an opt-in cost control. The default reviews every
    eligible completed turn so a second durable fact in the same realtime
    conversation is not silently discarded.
    """

    model_config = ConfigDict(extra="allow")

    aggressive_mode: bool = True
    # Keep this aligned with ExtractorConfig.min_user_chars. Stage 2 remains
    # the quality gate; a 12-character ownership statement can be durable.
    min_user_chars: int = 12
    rate_limit_seconds: int = 0


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
    # Personal-salience floor (1-5): candidates the model scores below this
    # are dropped in Stage 1. 3 keeps peripheral personal facts and drops
    # world-knowledge trivia; raise it for a leaner vault. The default equals
    # jarvis/memory/wiki/constants.py DEFAULT_SALIENCE (unscored candidates
    # sit exactly on the floor).
    min_salience: int = 3
    # Rollback switch to the pre-basis regime: when False, behavioral
    # (lived-experience) grounding is treated as ungrounded and only
    # explicit assertions survive.
    behavioral_inference: bool = True


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
    embedding_model: str = "qwen3-embedding:4b"
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
    # How long an armed approval gate waits for a decision before the default
    # deny. Deliberately short: an unattended, non-pre-authorized ask-tier
    # call should fail fast and honestly (the worker observes the denial and
    # adapts, ADR-0031/W3) instead of burning its iteration budget blocked.
    tool_approval_timeout_s: float = Field(default=60.0, ge=5.0, le=600.0)


class SkillsConfig(BaseModel):
    """Deterministic skill matching (2026-07-25).

    Author-written voice triggers are unaffected by everything here; these knobs
    govern only the relevance layer that catches paraphrases.
    """

    #: Master switch for the deterministic relevance layer. Off = exactly the
    #: pre-2026-07-25 behaviour (author regex + the LLM listing).
    relevance_enabled: bool = True

    #: While true, a FIRE-band relevance match is RECORDED but does not capture
    #: the turn — the narrowed candidate hint still ships. Shipped true for the
    #: first release so real decisions could be reviewed via
    #: ``GET /api/skills/match-log`` before the layer was allowed to act.
    #: Flipped to false on 2026-08-12 after that review ran its course: 14 days
    #: of live telemetry showed zero FIRE-band relevance events (nothing to
    #: capture, nothing miscaptured) while every paraphrase fell through to a
    #: model that never called run-skill on its own. The guards that made the
    #: shadow default safe (dispatching-class veto, clear-winner margin,
    #: definitional-question guard, min-band floor) all remain armed.
    relevance_shadow: bool = False

    #: Weakest band allowed to capture a turn. Anything below stays a
    #: suggestion the model may ignore.
    auto_fire_min_band: Literal["fire", "narrow"] = "fire"

    #: Optional threshold overrides. ``None`` keeps the calibrated
    #: corpus-derived defaults from jarvis/skills/relevance.py — set these only
    #: with a number from scripts/skill_relevance_calibrate.py, never to make a
    #: single over-fire go away (AP-27).
    fire_threshold: float | None = None
    hint_threshold: float | None = None

    #: How many narrowed candidates ride the per-turn context on a NARROW turn.
    narrow_candidates: int = 3


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
    """Top-level ``[harness.jarvis_agent]`` config for the Jarvis-Agent worker
    harness (accepts the legacy ``[harness.openclaw]`` alias, see below).

    ⚠️ INERT TODAY (2026-06-28): Jarvis-Agent is NOT a registered harness —
    there is no ``jarvis_agent`` (formerly ``openclaw``) entry-point in
    pyproject.toml (Welle-4 removed the subprocess
    worker, ~92% hang; see docs/BUGS.md). This block is a Wave-2 schema stub:
    setting ``enabled = true`` has NO effect — the harness cannot be dispatched
    and "start a subagent" routes to ``spawn_worker`` regardless. The boot path
    logs a warning when ``enabled`` is true but the harness is unregistered
    (see ``warn_if_phantom_worker_harness`` in jarvis/brain/factory.py). Heavy
    sub-agent work runs through the Mission-Manager (ClaudeDirectWorker), not
    here, until Wave 3 actually wires the bridge.

    Schema matches ``docs/jarvis-agents-bridge.md §4.2`` post-Wave-1
    (with AD-22..AD-24 findings incorporated). Wave 2 delivers only
    the schema + default block in jarvis.toml; Wave 3 wires the bridge.

    Deliberately NO Anthropic lock in the ``model`` default: an empty ``model``
    means the bridge resolves the frontier-pro of the active Personal Jarvis
    provider (``cfg.brain.primary``) via the provider-slug mapping from AD-6
    (gemini→google/gemini-..., claude-api→anthropic/..., openai→openai/...).
    This way Jarvis-Agent automatically follows the user's provider choice.

    AD-21 pin-version mandate: ``version`` must be set whenever the block
    exists at all — a Pydantic required field, no default. Guards against
    silent upstream drifts. Loading without the block (``HarnessConfig.
    jarvis_agent is None``) falls back to "bridge inactive".
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
    # Up to N Jarvis-Agent missions in parallel; the fourth lands in the queue (AD-13).
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
        default_factory=lambda: ["python-script"]
    )
    default_timeout_s: int = 600
    default_risk_tier: RiskTier = "monitor"
    # Output limit per harness turn back to the brain — prevents large
    # build logs from blowing the context window.
    max_output_chars: int = 4000
    # Per-harness overrides: e.g. {"jarvis_agent": {"model": "opus", "max_turns": 10}}
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
    auth_token_env: str = "JARVIS_MCP_TOKEN"  # noqa: S105 — env var NAME, not a secret
    max_call_depth: int = 3              # loop guard


class AudioConfig(BaseModel):
    input_device: str = "auto-headset"
    output_device: str = "auto-headset"
    # Optional user-defined device-name priority for the "auto-headset" resolver.
    # Each entry is a case-insensitive substring of a device name; earlier =
    # higher priority. When non-empty, these are matched BEFORE the built-in
    # generic headset list, so a user with an uncommon device (e.g. "Focusrite",
    # "Bose", "AirPods", a specific USB dongle) makes it win without editing
    # code. Empty (default) keeps the generic auto-detection. Ignored when
    # input_device/output_device is an explicit index or a concrete name.
    output_device_priority: list[str] = Field(
        default_factory=list,
        description=(
            "Preferred output-device name substrings (highest priority first); "
            "consulted before the generic headset auto-detection."
        ),
    )
    input_device_priority: list[str] = Field(
        default_factory=list,
        description=(
            "Preferred input (microphone) device name substrings (highest "
            "priority first); consulted before the generic auto-detection."
        ),
    )
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
    # Colour theme of the whole desktop app: "dark" (the product default —
    # matte black + signal yellow), "light" (warm paper + dark gold), or
    # "system" (follow the OS appearance, re-evaluated live when the OS flips).
    # Persisted here rather than only in the browser so the CHOICE survives a
    # cleared web store, so the native window frame can be painted in the right
    # colour before the web view has loaded anything (jarvis/ui/shell/window.py),
    # and so `jarvis api settings put-appearance` can drive it like every other
    # user-facing action. The frontend caches it in localStorage purely to paint
    # the boot splash without waiting for HTTP.
    theme: Literal["dark", "light", "system"] = "dark"
    # Dev mode: the frontend is not mounted from frontend/dist/ but loaded from
    # a running Vite dev server (HMR). Activated via ENV JARVIS_DEV=1 or CLI
    # --dev; the fields here simply hold the parameters.
    dev_mode: bool = False
    vite_dev_url: str = "http://localhost:5173"
    # ENV variable carrying the process-scoped UI bootstrap token. The native
    # WebView exposes it once; AuthGate exchanges it for an unrelated HttpOnly
    # session cookie and immediately clears the JavaScript value.
    auth_token_env: str = "JARVIS_UI_TOKEN"  # noqa: S105 — env var NAME, not a secret
    # Optional browser lock: when True, opening the UI in a browser on THIS
    # machine (loopback) asks for the Control Key. Off by default — the local
    # user walks straight in. Non-loopback access (LAN, VPS, reverse proxy)
    # still requires the key regardless of this flag; forwarded requests are
    # detected via relay-indicator headers, but a headerless L4 tunnel on the
    # same machine looks local — whoever forwards the port must turn this ON
    # (see surface_security.open_access_granted). Toggled live from
    # Settings → API Keys → Control Key.
    require_browser_login: bool = False
    # On-screen overlay style: "jarvis_bar" (slim default), "mascot" (the ghost
    # mascot), "voice_orb" (the procedural weather sphere — the desktop twin of
    # the in-app orb), or "none". One list: jarvis.ui.overlay_styles.
    orb_style: str = "jarvis_bar"
    # Optional explicit path to the mascot PNG. Empty = search for default asset.
    orb_mascot_path: str = ""
    # Jarvis bar: persistent (always-visible dots pill) vs only-when-active.
    bar_persistent: bool = True
    # Hex accent the bar lights up with during activity (gold on-brand).
    bar_accent: str = "#e7c46e"
    # Jarvis bar size multiplier (Settings → "Bar size" slider). The whole bar
    # scales proportionally (width AND height together, shape preserved) on top
    # of the physical-size-consistent base scale. Default 1.35 (135%) — the bar
    # reads a touch small at 100% on high-DPI monitors. Range mirrors
    # renderer.USER_SIZE_MIN/MAX (0.5–2.0); the renderer re-clamps, so this
    # validator only sanitizes a corrupt value.
    bar_size_scale: float = 1.35
    # Follow the mouse across monitors: when on, the on-screen bar hops to
    # whichever monitor the mouse cursor is currently on, keeping the SAME
    # relative spot (so different-sized monitors line up). Off pins it to one
    # monitor. Default on. Works on Windows/Linux (Tk bar) and macOS (Qt bar);
    # a headless / Wayland host with no reliable per-monitor geometry keeps the
    # single-monitor behaviour. See jarvis/ui/jarvisbar/interaction.py.
    bar_follow_cursor_monitor: bool = True
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

    @field_validator("bar_size_scale", mode="before")
    @classmethod
    def _clamp_bar_size_scale(cls, v: object) -> object:
        # Sanitize a persisted "Bar size" value on load: clamp into the
        # supported 0.5–2.0 range and fall back to 1.0 for a non-numeric /
        # non-finite value so a corrupt jarvis.toml can never brick the bar.
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 1.0
        if f != f or f in (float("inf"), float("-inf")):  # NaN / ±inf
            return 1.0
        return max(0.5, min(2.0, f))


class DuckingConfig(BaseModel):
    """Audio ducking — "Mute music while dictating" (Taskbar section).

    When ``enabled``, the audio-duck controller lowers every OTHER app's audio
    for the duration of a voice session and restores it when the session ends.
    Windows: per-app session mute via pycaw (excluding Jarvis's own PID, so the
    TTS voice is never muted). macOS: AppleScript volume duck of the known
    players (Music, Spotify) plus an opt-in master-output fallback. A graceful
    no-op elsewhere. Default off (opt-in).
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    # Grace before restoring other apps' volume (lets the TTS tail finish).
    restore_delay_ms: int = 400
    # App names never to mute — Windows process names ("Discord.exe") or plain
    # app names ("Spotify"). Empty = mute all others.
    never_mute: list[str] = Field(default_factory=list)
    # Volume other apps are ducked to (0 = full duck/mute of the app volume).
    duck_volume_percent: int = Field(default=0, ge=0, le=100)
    # macOS only: when no known player was ducked, lower the MASTER output
    # volume instead. Opt-in — the master volume also lowers Jarvis's own TTS.
    macos_master_fallback: bool = False


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
    greeting: str = ""              # optional spoken welcome; empty = neutral name-based default
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
    of extra latency per spawn and does not pay off for every Jarvis-Agent turn.

    Enable via either:
      - ENV ``JARVIS_VISION_CONTEXT=1``
      - ``[vision].context_hint_on_spawn = true`` in jarvis.toml
    """
    model_config = {"extra": "allow"}

    context_hint_on_spawn: bool = False
    timeout_s: float = 0.25     # mandate: 250 ms latency cap per spawn


class ScreenContextConfig(BaseModel):
    """Top-level ``[screen_context]`` config — one-shot, on-request screen look.

    Governs ``jarvis/screen_context/``: when the user unambiguously asks Jarvis
    to look at the screen, exactly one capture is taken of the monitor under the
    mouse cursor, filtered, handed to the turn, and dropped. There is no setting
    here that enables continuous or silent monitoring, because no such code path
    exists — see ``docs/screen-context.md``.

    Every key below is read by ``ScreenContextService`` (AP-31: no config that
    nothing honours).
    """
    model_config = {"extra": "allow"}

    #: Master switch. When off, an explicit "look at this" is answered with an
    #: honest "screen context is switched off" rather than silently ignored.
    enabled: bool = True

    #: App names or window-title fragments that are NEVER captured — not
    #: captured and redacted, but not captured at all. Case-insensitive
    #: substring match against both the app name and the window title, so a
    #: version bump ("1Password" -> "1Password 8") cannot silently stop
    #: protecting. Empty by default: shipping a guess about what a stranger
    #: considers sensitive is worse than letting them state it (§3).
    denylist: list[str] = Field(default_factory=list)

    #: Extra ``"label:regex"`` rules on top of the shipped set (card numbers,
    #: IBANs, API-key shapes, auth headers, private-key headers). The label is
    #: what the user sees in the redaction report, so it should be a word.
    sensitive_patterns: list[str] = Field(default_factory=list)

    #: Whether the shipped default patterns apply. Off only makes sense for a
    #: user who has written a complete replacement set.
    include_default_patterns: bool = True

    #: Character budget for on-screen text handed to the model. Text beyond it
    #: is cut and the cut is reported, never silently dropped.
    max_text_chars: int = 4000

    #: Seconds an unconsumed capture stays in memory before it is discarded.
    #: A capture is also discarded on first use, whichever comes first.
    ttl_s: float = 120.0

    #: OCR supplement for windows whose accessibility layer exposes no text.
    #: Off by default and dependency-free: the base install stays torch-free
    #: (§3), so this only does anything when a local OCR engine is present.
    ocr_enabled: bool = False


class ComputerUseConfig(BaseModel):
    """Top-level ``[computer_use]`` config for the Computer-Use harness.

    Controls the screenshot-click loop in
    ``jarvis/harness/screenshot_only_loop.py``. Default ON since 2026-07-18
    (maintainer directive): desktop control is the product's core promise and
    fresh installs experienced the old opt-out default as a broken feature
    (AP-23 class). Safety holds without the flag: the whitelist ships empty so
    risky actions stay ask-tier, and machines without a vision engine or
    display degrade to an honest "not active" ToolResult instead of running
    (Phase 5 shell module, see ADR-0008).
    """
    model_config = {"extra": "allow"}

    enabled: bool = True
    # Screen indicator (2026-07-15): while a CU mission controls the local
    # mouse/keyboard, a pulsing gold border glows on every monitor edge and
    # an "Esc to cancel" pill is shown (jarvis/cu/indicator).
    # Default ON; turning it off also skips the sidecar process entirely.
    # The global Escape-to-cancel listener is armed per mission regardless
    # of this flag (it is a safety affordance, not a visual).
    screen_indicator: bool = True
    # Which Computer-Use engine runs. "v2" (default) = the
    # rebuilt perceive->act->verify engine (jarvis/cu/engine.py): per-frame
    # coordinate mapping, provider coordinate conventions, UI-idle capture,
    # effect-checked actions and the idempotency ledger. The historical
    # "current", "june13" and "stable" values remain accepted for read-time
    # config compatibility but route to v2: their frozen loops lack current
    # permission, topology and foreground-window action guards.
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
    # CU v2: what the vision model SEES and acts on. "window" (default) crops
    # every capture to the foreground TARGET WINDOW — the industry-standard
    # framing for pixel-grounded GUI agents (OpenAI CUA: fixed viewport;
    # Anthropic reference: one small display the app fills; Microsoft UFO:
    # per-application screenshots). It also structurally prevents clicks from
    # landing outside the app: coordinates are clamped into the capture rect.
    # "monitor" restores the previous whole-monitor framing (rollback knob).
    capture_scope: Literal["window", "monitor"] = "window"
    # CU v2: maximize the target window on ITS OWN monitor before acting (and
    # again after each open_app/switch_window). A small floating window on a
    # large screen shrinks to stamp size in the model's downscaled frame —
    # grounding errors then click the wallpaper next to it (live incident
    # 2026-07-02). Never moves a window across monitors (mixed-DPI resize
    # trap); fixed-size dialogs and the desktop shell are left untouched.
    # DEFAULT OFF (maintainer mandate 2026-07-02): the restore/maximize
    # animation visibly "zooms" already-open windows and rearranges the
    # user's layout uninvited; the window-scoped capture above already gives
    # the model a full-size view of the window WITHOUT touching it. Opt back
    # in for setups where tiny floating windows keep mis-grounding.
    normalize_window: bool = False
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
    cursor_glide_ms: int = Field(default=0, ge=0, le=2000)
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

    # Languages the search-alias bridge writes into each page
    # (jarvis/memory/wiki/search_aliases.py). Pages are written in English by
    # the fact extractor, so a user who ASKS in another language cannot reach
    # them by keyword ("Flugzeuge" never matches "aircraft"). Listing that
    # language here makes every page carry the words its owner would actually
    # say. Empty = derive from the other language signals (reply_language, UI,
    # STT); those are frequently all "en"/"auto" even for a non-English
    # speaker, which is why this explicit list exists rather than a guess.
    search_alias_languages: list[str] = Field(default_factory=list)


class UltraWikiConfig(BaseModel):
    """UltraWiki — the semantic memory mode (design: UltraWiki/*.md).

    ``enabled`` is the either-or mode switch of the Wiki section (decision
    D-5): False = the normal wiki captures and answers, True = UltraWiki
    does. Switching is non-destructive in both directions (D-9).

    Provider slots follow the bring-your-own doctrine (D-2): empty string =
    unconfigured, chosen deliberately in the activation wizard. The embedding
    pair is semi-permanent (D-3 — changing it re-embeds the corpus), so it is
    only ever written through the guarded settings route. The Postgres
    connection string is a CREDENTIAL and lives in the secret chain under
    ``ultrawiki_db_url`` (AP-12), never here.

    ``extra="allow"`` is mandatory (AP-16): future sub-keys must survive the
    self-mod pre-validate round-trip.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    db_backend: str = "sqlite"  # "sqlite" (universal floor) | "postgres"
    # The named storage preset behind ``db_backend`` — "sqlite" | "supabase" |
    # "neon" | "postgres". Presentation only (jarvis.ultrawiki.provider_catalog
    # maps it back to db_backend): it selects the card's help text, dashboard
    # link and connect flow. Deliberately NOT a second functional enum, so the
    # store keeps exactly two code paths (AP-4 / BUG-008).
    storage_provider: str = "sqlite"
    embedding_provider: str = ""  # "ollama" | "gemini" | "openai" | "voyage" | "mistral" | "cohere"
    embedding_model: str = ""
    distill_provider: str = ""  # empty = key-aware brain chain decides
    distill_model: str = ""
    rerank_provider: str = ""  # "llm" | "voyage" | "cohere"; empty = stage skipped
    rerank_model: str = ""  # only for rerank_provider="llm"; empty = cheap tier
    ollama_endpoint: str = "http://localhost:11434"

    # Share of ONE core the ingest pipeline may occupy (0.01-1.0).
    #
    # Indexing a corpus is real work, but it is work nobody is waiting for,
    # and it used to take whatever it could get: a full core, permanently,
    # with the whole machine sluggish behind it. The worker now sleeps in
    # proportion to how long each pass ran, so this is an honest ceiling on
    # any CPU — the same guarantee on a headless VPS as on a workstation.
    # Raise it to index faster on a machine nobody is sitting at; lower it if
    # even this is noticeable. See jarvis.ultrawiki.pipeline.
    cpu_share: float = 0.05

    # How hard the background lane works at turning pictures into words and
    # recordings into transcripts:
    #   "frugal" (default) - one item at a time, and only while every other
    #                        stage is idle. A photo library is tens of
    #                        thousands of model calls, so this must never race
    #                        the import it follows.
    #   "eager"            - same lane, still one at a time, but it also runs
    #                        while other stages have work.
    #   "off"              - nothing is described or transcribed. Photos stay
    #                        findable by filename, folder and capture date.
    # Nothing here is a hard requirement: an install with no vision-capable
    # provider simply keeps its backlog until one appears.
    media_enrich: str = "frugal"

    # Where the readable Markdown projection is written (the Obsidian vault).
    # Empty = "wiki/ultrawiki-vault" under the data dir — beside the normal
    # wiki's own vault and never inside it: UltraWiki writes to its own files,
    # which is what keeps the mode switch reversible.
    vault_path: str = ""

    # -- ranking knobs (design: UltraWiki ranking pipeline, 2026-07-25) ------
    # The absolute 0-10 relevance floor an UNSOLICITED surface (context
    # injection, volunteered voice answers) must clear. Explicit searches --
    # the Ask view, the REST route, the CLI -- never apply it: the user asked
    # and sees the evidence. 0 disables the floor everywhere.
    rerank_min_score: float = 4.0
    # Per-leg RRF weights: score(d) = sum(weight / (60 + rank)). 1.0 each is
    # the article's default; 0 silences a leg without removing it.
    rrf_keyword_weight: float = 1.0
    rrf_vector_weight: float = 1.0
    # The episodic-event leg (jarvis/ultrawiki/events.py). Events are
    # precomputed answers to "when did X happen", so they are weighted like
    # any other list rather than allowed to veto one: consensus still decides
    # (design doc 01, principle 5). 0 silences the leg.
    rrf_event_weight: float = 1.0

    # -- episodic events (design doc 01, uw_events) --------------------------
    # Derive events from the distillation that already ran. Costs no extra
    # model call and nothing on the read path; false stops the derivation and
    # leaves existing rows untouched.
    events_enabled: bool = True
    # Age decay on the fused score: 0.5 ** (age_days / half_life). Stale
    # answers lose when relevance is otherwise equal. 0 disables the decay
    # (the epsilon-sized recency tiebreak still settles exact ties).
    recency_half_life_days: float = 180.0

    # -- word lexicon (jarvis/ultrawiki/lexicon.py) --------------------------
    # The vocabulary index behind word search: every term the corpus uses,
    # embedded into the SAME space as the passages, so one word can be
    # expanded into the ~20 terms nearest it by meaning.
    #
    # false stops the background harvest and the term embedding. Word search
    # keeps working — it falls back to neighbours derived from which words
    # keep company with the query in real passages, which needs no provider
    # at all — it is simply blunter. Existing rows are left untouched.
    lexicon_enabled: bool = True
    # Ceiling on how many terms ever carry a vector. The vocabulary itself is
    # unbounded (a term still answers an exact lookup for free); this bounds
    # the part that costs embedding calls, most-seen terms first. ~20 000
    # covers the working vocabulary of a personal corpus in several languages
    # at roughly the cost of embedding 20 000 very short texts, once.
    lexicon_max_terms: int = 20000
    # How many neighbours a word search asks for when the caller does not say.
    word_search_neighbours: int = 20


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
    # 150 (was 80): the vault search opens its SQLite connection lazily
    # inside this budget, so the first qualifying turn of a process regularly
    # timed out. The factory warms the connection at boot; the wider budget
    # covers a lost warm-up race and stays inaudible next to the brain call.
    latency_budget_ms: int = 150
    # 3 (was 4): short given names ("Joy", "Uwe") and initialisms ("BMW")
    # are exactly the tokens a memory question hangs on; the old floor made
    # them structurally unsearchable (2026-08-11 recall audit).
    min_keyword_length: int = 3

    # Relevance gate (jarvis/brain/wiki_relevance.py). Retrieval always
    # returns a ranked list, so without a gate every unrelated question gets
    # a personal note welded onto its answer. ``relevance_gate = false``
    # restores the ungated behaviour: search every turn, inject every hit.
    # With the gate on, every turn beyond greeting length is SEARCHED
    # (retrieval-first); the gate grades how strictly the hits are filtered.
    relevance_gate: bool = True
    # Share of the question's content terms a hit must cover to be injected.
    min_coverage: float = 0.5
    # The stricter bar applied when the turn has no personal anchor (world-
    # shaped questions, statements): a page must cover nearly the whole
    # question before it may ride along uninvited.
    strict_min_coverage: float = 0.75
    # Share of the best hit's score (within the SAME search call) a hit must
    # reach. Relative by design — the vault's scores are only comparable
    # within one call, so an absolute cutoff would be noise.
    min_relative_score: float = 0.35

    # Ambient personal knowledge — the standing identity card
    # (jarvis/brain/identity_card.py). Unlike the per-turn snippets above this
    # is a precomputed, LLM-free distillation of the user's own profile that
    # rides in the CACHED system-prompt prefix. ``false`` removes the block
    # entirely; the cap is an upper bound only (never raises the hard 600).
    identity_card: bool = True
    identity_card_max_chars: int = 600


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
    # Voice engine selector. "realtime" (default) = the full-duplex
    # speech-to-speech engine — the recommended mode. "pipeline" = the classic
    # STT->brain->TTS chain. Read once per voice session; a live change lands
    # on the next session. When no realtime-capable key exists, the session
    # falls back to the pipeline; the spoken "realtime unavailable" notice is
    # reserved for an EXPLICIT user pick (mode present in the TOML), so a
    # fresh keyless install degrades silently instead of nagging every call.
    mode: str = "realtime"
    # Session-scoped voice composition. Empty keeps the selected engine's
    # normal brain. ``codex-subscription-voice`` keeps the proven local
    # microphone/STT/TTS/playback pipeline and replaces only conversational
    # text generation with the stable Codex App Server subscription transport.
    # This must be selected explicitly: choosing the similarly named Realtime
    # provider never implies this classic-pipeline profile.
    profile: str = ""
    # Realtime tool exposure. "delegate" (default) gives the live model one
    # compact jarvis_action function and hands action turns to the key-aware
    # router Brain. This keeps the persistent realtime prompt small; declaring
    # the full dynamic catalog consumed ~26k input tokens per response and hit
    # a 40k TPM limit after one turn. "direct" remains an explicit diagnostic
    # opt-in. Read once per session; unknown values fail closed to the compact
    # delegate mode.
    realtime_tool_mode: str = "delegate"
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
    # long-haul trip-research turn). None → the pipeline derives it as a
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


#: Every language the speech recogniser can transcribe, as ISO-639-1 codes (a
#: few three-letter Whisper codes have no two-letter form). ONE source for both
#: ``[stt].language`` and ``[dictation].language`` — the REST routes
#: (``/api/settings/stt-language``, ``/api/dictation/settings``) and the frontend
#: pickers all read it from here, so the list cannot drift between layers (AP-4).
#:
#: Deliberately NOT limited to the three product locales (de/en/es). Those govern
#: what Jarvis SAYS BACK; what it can HEAR is a different question, and capping it
#: at three meant a Mandarin or Japanese speaker could not dictate at all —
#: exactly the maintainer's-config-is-not-the-baseline trap (CLAUDE.md §3). The
#: order is the recogniser's own; the UI sorts by localized name at render time
#: so no language is listed "first" by nationality.
RECOGNITION_LANGUAGES: tuple[str, ...] = (
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs",
    "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi",
    "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy",
    "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb",
    "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru",
    "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw",
    "ta", "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi",
    "yi", "yo", "yue", "zh",
)

#: The value that asks for per-utterance DETECTION instead of a fixed language.
#: Passed through to the provider verbatim — an absent argument means "no
#: opinion" there and inherits the configured pin, which is the whole reason
#: dictation's auto mode used to transcribe German speech as English.
AUTO_LANGUAGE = "auto"

#: The values a recognition-language setting accepts: detect, or any one of the
#: languages above.
RECOGNITION_LANGUAGE_CHOICES: tuple[str, ...] = (AUTO_LANGUAGE, *RECOGNITION_LANGUAGES)

#: The values ``[dictation].language`` accepts. Same set as the voice recogniser:
#: one microphone, one list of languages it understands.
DICTATION_LANGUAGES: tuple[str, ...] = RECOGNITION_LANGUAGE_CHOICES

#: The values ``[dictation].translate_target`` accepts — the language a dictation
#: is DELIVERED in. The recognition list without ``auto``: "detect it" is a
#: coherent answer to "what am I speaking" and no answer at all to "what should
#: come out", so offering it would be a dropdown entry that silently does
#: nothing (AP-31). One derivation, not a second hand-typed list (AP-4).
TRANSLATION_TARGETS: tuple[str, ...] = RECOGNITION_LANGUAGES

#: The registers the dictation polish pass may be asked to write in — the cheap
#: analogue of the per-application tone commercial dictation tools switch on.
#: Mirrors the ``polish_style`` ``Literal`` below (which is what Pydantic and the
#: OpenAPI schema read) so the validator and the UI have a list to iterate; the
#: two are pinned together by a parity test, because a vocabulary spelled twice
#: is the AP-4 drift shape.
POLISH_STYLES: tuple[str, ...] = ("neutral", "messaging", "email")


def _clamped_polish_int(value: object, *, default: int, low: int, high: int) -> int:
    """Pull a hand-edited integer back into range instead of rejecting it.

    The bounds are declared on the ``Field`` as well, so the schema still
    states the real range — but a value outside it arrives here first and is
    clamped, because a stale or hand-edited ``jarvis.toml`` must never fail
    validation and cost a boot (AP-16). Anything that is not a number at all
    (a typo, ``None``, NaN, ``inf``) falls back to the shipped default, which
    is by definition a working value.
    """
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def _clamped_polish_float(
    value: object, *, default: float, low: float, high: float
) -> float:
    """The float twin of :func:`_clamped_polish_int` — same AP-16 contract."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):  # NaN / ±inf
        return default
    return max(low, min(high, number))


class DictationConfig(BaseModel):
    """Dictation mode: speak into whatever text field currently has focus.

    TOML path: ``[dictation]`` · Attribute path: ``JarvisConfig.dictation``

    The shortcut itself lives in ``[trigger].hotkey_dictate`` with every other
    keybind; this block owns the behaviour. ``extra="allow"`` keeps the self-mod
    pre-validate pipeline safe when a future key is added (AP-16).
    """

    model_config = ConfigDict(extra="allow")

    #: ``hold`` = record while the key is down, submit on release (the mode
    #: every reference tool ships and the one people expect). ``toggle`` = one
    #: press starts, the next stops — kept for accessibility and long dictations
    #: where holding a key for minutes is not reasonable.
    mode: Literal["hold", "toggle"] = "hold"

    #: Where the finished transcript goes. ``auto`` inserts into the focused
    #: field of whatever application is in front, EXCEPT when that application
    #: is Jarvis itself — then it goes to the app's own input, because inserting
    #: into the window the user just left is both surprising and unrecoverable.
    #: ``insert`` always inserts, ``chat`` never does (transcript event only).
    target: Literal["auto", "insert", "chat"] = "auto"

    #: ``clipboard`` writes the text, sends the paste chord and restores the
    #: previous clipboard — one keystroke regardless of length, and it survives
    #: editor autocomplete. ``type`` synthesises the text character by character;
    #: correct for the rare control that ignores paste, but ~40 ms per character
    #: on Windows and easily mangled by autocomplete, so it is opt-in.
    insert_method: Literal["clipboard", "type"] = "clipboard"

    #: Which paste chord to send. ``auto`` = Cmd+V on macOS, Ctrl+V elsewhere.
    #: Many terminals do not paste on Ctrl+V, which is why the curated
    #: alternatives exist (``ctrl_v`` | ``ctrl_shift_v`` | ``shift_insert`` |
    #: ``cmd_v``, all in ``jarvis.dictation.insert.PASTE_CHORDS``).
    #:
    #: A recorded combination is also accepted, written with ``+``
    #: (``"ctrl+shift+insert"``) — the paste shortcut of whatever application
    #: you dictate into. It is a plain ``str`` rather than a ``Literal`` for
    #: exactly that reason; the validator below normalizes it and falls back to
    #: ``auto`` on anything it cannot send, because a hand-edited config must
    #: never fail validation (AP-16). A custom chord is reported honestly at
    #: delivery time: Jarvis cannot know whether the target app pasted, so the
    #: outcome is ``paste_sent``, not ``inserted``.
    paste_chord: str = "auto"

    #: Pause after writing the clipboard, before sending the chord. Too short
    #: and the target app pastes the PREVIOUS clipboard content.
    paste_delay_ms: int = Field(default=120, ge=0, le=2000)

    #: Pause after the chord, before restoring the previous clipboard. Too short
    #: and the target app has not read the clipboard yet.
    paste_delay_after_ms: int = Field(default=120, ge=0, le=2000)

    #: Put back whatever was on the clipboard before dictation. Text only — the
    #: platform clipboard layer does not carry images, so an image on the
    #: clipboard is lost either way; turning this off at least leaves the
    #: transcript there.
    restore_clipboard: bool = True

    #: Remove filler sounds with the deterministic per-language rules in
    #: ``jarvis.dictation.cleanup``. No model call, no rephrasing.
    remove_fillers: bool = True

    #: Safety ceiling for the cleanup: if the rules would drop more than this
    #: fraction of the words, the RAW transcript is used instead. A cleanup that
    #: eats a quarter of a sentence is a bug, not a cleanup.
    filler_max_removed_fraction: float = Field(default=0.25, ge=0.0, le=1.0)

    #: Ceiling on ONE dictation, in seconds. ``0`` removes it entirely.
    #:
    #: This is not a provider limit and never was. No speech-to-text request
    #: carries the whole recording: the final pass cuts the audio at its
    #: quietest points into segment-sized pieces, so a provider's file-size
    #: ceiling is reached by a SEGMENT, never by a long dictation. The only
    #: real cost of speaking longer is the buffer held in memory — 16 kHz
    #: 16-bit mono is ~1.9 MB per minute, so half an hour is ~58 MB.
    #:
    #: The default used to be five minutes, which is shorter than plenty of
    #: genuine dictations and cut them off mid-sentence. It is now half an
    #: hour: long enough that nobody speaking normally will ever meet it, short
    #: enough that a toggle-mode recording somebody forgot about does not eat
    #: the machine. Set ``0`` if you would rather have no ceiling at all —
    #: then only releasing the key, the stop event or a hangup ends a
    #: recording. The wake word is protected separately either way (see
    #: ``_dictation_wake_block_until``), so an unbounded dictation can never
    #: leave it deaf.
    max_seconds: float = Field(default=1800.0, ge=0.0, le=86_400.0)

    #: Live-transcript refresh interval while speaking. ``0`` disables the live
    #: preview entirely (the final transcription still happens).
    partial_interval_s: float = Field(default=1.2, ge=0.0, le=10.0)

    #: Segment length for the LIVE line while you speak. The old dictation lane
    #: re-transcribed the whole growing buffer on every tick, which costs
    #: O(n²) audio-seconds and, on a paid API, real money. Closed segments are
    #: transcribed once and never again; only the open tail is re-sent.
    #: ``0`` restores the legacy full-buffer behaviour.
    #:
    #: With ``final_quality_pass`` on (the default), what these short segments
    #: produce is a PREVIEW and never the delivered text — see below for why
    #: that distinction is the whole of the multilingual repair.
    segment_seconds: float = Field(default=8.0, ge=0.0, le=60.0)

    #: Re-transcribe the WHOLE recording once, in long windows, after the key
    #: is released — and deliver that instead of the stitched-together short
    #: segments.
    #:
    #: A recognizer detects the spoken language from the audio it is handed.
    #: On an eight-second segment it is not sure, and an unsure model does not
    #: merely mislabel the language — it TRANSLATES. That is why one continuous
    #: recording used to arrive with one paragraph in the language spoken and
    #: the next in English, and why a sentence that switches language halfway
    #: through could not survive at all: each segment was decided separately.
    #: Long windows remove the cause rather than patching the symptom.
    #:
    #: The cost is one extra pass over the audio. It is paid back by the live
    #: line moving to the on-device preview engine where there is one, so a
    #: desktop install sends FEWER requests than before; a host without a local
    #: engine sends the short segments as before and this pass on top.
    final_quality_pass: bool = True

    #: Length of one final-pass window, in seconds. Long enough for reliable
    #: language detection, short enough to stay inside every provider's upload
    #: limit and to keep one failed window cheap.
    final_window_seconds: float = Field(default=25.0, ge=5.0, le=60.0)

    #: How much audio consecutive windows share. The overlap is what stops a
    #: word that straddles a boundary from being cut in half; the duplicate it
    #: creates is removed from the TEXT afterwards, which is possible, while
    #: recovering half a word is not. ``0`` disables the overlap.
    final_overlap_seconds: float = Field(default=1.5, ge=0.0, le=5.0)

    #: Allow the language to change WITHIN one dictation.
    #:
    #: While this is on (the default), the final pass asks the recognizer to
    #: detect the language from each long window instead of being told one, so
    #: a sentence that starts in one language and finishes in another is
    #: transcribed as it was spoken. ``language`` above then still governs how
    #: the finished text is treated — which filler rules run, which language
    #: the polish pass writes in — but it no longer LOCKS the recognizer, which
    #: is what used to turn a pin into a translation of everything else said.
    #:
    #: Turn it off to hand the pinned language to the recognizer outright. That
    #: is the right choice when you only ever dictate in one language and the
    #: provider keeps guessing wrong; it is the wrong choice for anyone who
    #: mixes languages, which is why it is not the default.
    code_switching: bool = True

    #: Keep a local history of dictations (raw + cleaned, for auditing what the
    #: cleanup changed). Dictated text is among the most sensitive data this app
    #: holds, so it is local-only, capped, and purgeable from the UI.
    history_enabled: bool = True
    history_max_entries: int = Field(default=200, ge=0, le=5000)
    history_retention_days: int = Field(default=30, ge=0, le=3650)

    #: Which language the dictation transcription is pinned to. ``auto`` (the
    #: default, and the right answer for almost everyone) lets the provider
    #: detect it per utterance. Pinning helps only when the provider keeps
    #: guessing wrong; on a model that was not trained for the pinned language
    #: it makes recognition WORSE, so the UI says so out loud.
    #:
    #: Deliberately independent of ``[stt].language`` (the voice-turn language)
    #: and ``[wake].language`` — dictating in one language while talking to the
    #: assistant in another is a normal thing to want.
    language: str = "auto"

    #: Keep the raw audio of a dictation that produced nothing usable
    #: (``failed`` / ``cancelled`` / ``empty``) so the Restore button can
    #: transcribe it again. NEVER kept for a successful dictation: audio is the
    #: most sensitive thing this application stores, so it is only ever written
    #: when it buys back something the user actually lost. Local-only, capped by
    #: the two keys below, deleted with the history entry and purged when the
    #: history is cleared. Its own key, not a rider on ``history_enabled``, so
    #: it can be turned off on its own.
    keep_failed_audio: bool = True
    #: Delete kept audio older than this many days. ``0`` disables the age cap.
    audio_retention_days: int = Field(default=7, ge=0, le=365)
    #: Keep at most this many audio files. ``0`` disables the count cap (the
    #: age cap still applies); it does not mean "delete everything".
    audio_max_files: int = Field(default=20, ge=0, le=1000)

    # ------------------------------------------------------------------
    # The polish pass — a second, generative read-over of the transcript
    # ------------------------------------------------------------------
    # Deterministic filler removal cannot punctuate a sentence, repair the
    # capitalisation a segment boundary broke, or resolve a spoken
    # self-correction ("at 2, actually 3"). So a small, fast text model reads
    # the FINISHED transcript once — not once per segment — and writes down
    # what the speaker would have written. Every knob below exists to keep
    # that pass a no-op rather than a loss: on a timeout, an error, a missing
    # key or a failed drift guard the raw transcript is what gets delivered,
    # and the raw text stays on the history row either way.

    #: Master switch, and it ships ON. That is safe on a fresh clone with no
    #: credentials at all, which is the whole point: ``polish_provider =
    #: "auto"`` resolves through the key-aware, family-crossing chain, and
    #: that chain comes back EMPTY when the user holds no text-model key in
    #: any family. Such an install reports ``unavailable`` and delivers
    #: byte-identical text to a build without the feature — so the
    #: maintainer's keys are not what makes the default safe (AP-23).
    #: Defaulting it off would instead ship the punctuation defect to
    #: everyone who never reads a release note.
    polish: bool = True

    #: Which model family writes the polished text. ``auto`` (the default)
    #: takes whatever the user actually has, best-latency family first, and
    #: crosses to a DIFFERENT family when one is depleted, rate-limited or
    #: unreachable (AP-22). A concrete id pins the chain to that family. It is
    #: a user preference, never a code branch — the transport is chosen by
    #: wire format, not by provider name (AP-21).
    polish_provider: str = "auto"

    #: Model id inside the chosen family. Empty means the family default,
    #: which is right for almost everyone: the defaults are picked to fit the
    #: latency budget below, and a slower "better" model spends the whole
    #: budget and then delivers the raw text anyway.
    polish_model: str = ""

    #: Hard wall-clock ceiling for the whole pass, in milliseconds. It runs
    #: AFTER the final transcription, so every millisecond here is felt as a
    #: delay before the text appears; on expiry the call is cancelled and the
    #: raw transcript is delivered. 1200 ms is the honest p95 target for a
    #: consumer client talking to a cloud model over the open internet.
    polish_timeout_ms: int = Field(default=1200, ge=200, le=5000)

    #: Skip the pass above this many characters. ``0`` — the default — means
    #: no cap.
    #:
    #: This started at 4000 on the theory that long dictations are where a
    #: rewrite goes wrong most expensively. That reasoning does not survive
    #: contact with the guards it duplicates: latency is already bounded by
    #: ``polish_timeout_ms`` (a slow answer delivers the raw text), drift is
    #: already caught per-transcript by the word-ratio band and the number and
    #: protected-term checks, and the cost of a long input is measured in
    #: hundredths of a cent. What the cap actually did was silently skip the
    #: pass on exactly the dictations that need it most — four minutes of
    #: speech is roughly where a transcript stops being one sentence and starts
    #: being something the recognizer has broken into paragraphs at arbitrary
    #: points. A ceiling that switches a feature off without saying so is worse
    #: than the risk it was guarding against, and the guards that DO speak are
    #: still in front of it.
    #:
    #: Raise it above 0 if you would rather bound the pass by input size than
    #: by the clock; the value is honoured either way.
    polish_max_input_chars: int = Field(default=0, ge=0, le=1_000_000)

    #: Skip the pass below this many words. There is nothing to format in
    #: "yes" or "call me back", and skipping saves a whole round-trip on the
    #: most common short dictation. ``0`` polishes everything.
    polish_min_words: int = Field(default=4, ge=0, le=100)

    #: Output ceiling handed to the model. Bounds the cost and stops a model
    #: that decided to ANSWER the transcript from running away; a truncated
    #: answer then fails the drift guards, so the user still gets the raw
    #: text rather than half a reply.
    polish_max_output_tokens: int = Field(default=1200, ge=64, le=8192)

    #: Sampling temperature. ``0.0`` on purpose: this is a formatter, not a
    #: writer. The same transcript should come back the same way twice, and
    #: every bit of creativity here is a word the speaker did not say.
    polish_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    #: The band the polished word count must land in, as a fraction of the
    #: raw word count; outside it the raw transcript wins and the history row
    #: says the polish was rejected. Deliberately asymmetric — a formatter
    #: shrinks (fillers, false starts, repetitions) or stays flat, so the
    #: floor is generous while the ceiling is tight: growth is what an
    #: answer, a translation or an explanation looks like, and over-rewriting
    #: is the documented failure direction of every tool that does this.
    polish_drift_max_shrink: float = Field(default=0.55, ge=0.0, le=1.0)
    polish_drift_max_growth: float = Field(default=1.20, ge=1.0, le=3.0)

    #: The register the formatter writes in. ``neutral`` adds nothing to the
    #: prompt, ``messaging`` asks for a casual line without salutations,
    #: ``email`` for a written-correspondence register. Tone only — no style
    #: ever licenses a change of meaning.
    polish_style: Literal["neutral", "messaging", "email"] = "neutral"

    #: Also sharpen the WORD CHOICE, not just the writing. With this on the
    #: pass may replace a vague placeholder with the specific word that was
    #: meant ("the thing that holds the pipe" -> "the bracket") and collapse
    #: padding into the plain verb ("make a decision" -> "decide"). The prompt
    #: spends more words forbidding the ornate register than requesting
    #: precision, because that is the documented failure direction: a model
    #: asked to "improve wording" reaches for *utilize* and *facilitate*, which
    #: is the opposite of the goal. Simple and exact, never impressive.
    #:
    #: Ships OFF, and unlike ``polish_style`` this is not a matter of taste —
    #: it TRADES A GUARD. Rare-token preservation, the check that rejects an
    #: answer in which an uncommon word silently vanished, cannot survive a
    #: mode whose whole job is replacing uncommon words, so precision runs
    #: against ``precision_drift_reason`` with that one check dropped. Every
    #: other guard stands, protected terms included. A trade like that is the
    #: user's to make deliberately, never one they inherit from a default.
    #:
    #: Applies to the translate pass too, through the same prompt clause, so
    #: one switch means one thing whichever pass runs.
    polish_precision: bool = False

    #: Also re-read the transcripts of ordinary VOICE TURNS — the ones produced
    #: by talking to the assistant, not by dictating into a document.
    #:
    #: It lives in ``[dictation]`` despite not being about dictation, because it
    #: switches on the SAME pass with the same provider, ceiling, style and
    #: precision setting. A block of its own would mean configuring the wording
    #: model twice and letting the two answers drift; one switch reading one
    #: block is the smaller surprise (AP-4).
    #:
    #: **It never delays a turn.** The brain is handed the raw transcript and is
    #: already answering before this runs; the polished text arrives afterwards
    #: as ``TranscriptPolished``, for the surfaces that display and store the
    #: turn. Spending the latency ceiling between a finished sentence and the
    #: start of the answer would trade the responsiveness that makes a voice
    #: assistant usable for a comma in a log nobody was reading yet.
    #:
    #: Ships OFF, and the reason is cost rather than safety: a dictation is a
    #: deliberate act a few times an hour, while a conversation produces a turn
    #: every few seconds, and each one would spend a model call. Someone who
    #: wants readable transcripts should say so.
    #:
    #: Requires ``polish`` above — it extends that pass rather than being a
    #: second one, so with the formatter off there is nothing for it to extend.
    #: The UI reflects that by showing it inside the formatter's own block, so
    #: the dependency is visible instead of being a switch that reads as on and
    #: does nothing (AP-31).
    #:
    #: Never translates, even with ``translate`` on. That switch is about text
    #: on its way into a document; a conversation transcript is a RECORD of what
    #: was said, and rewriting the record into another language than the one the
    #: brain answered in would make the session history unreadable as a
    #: conversation.
    polish_conversation: bool = False

    # ------------------------------------------------------------------
    # The translate pass — speak one language, deliver another
    # ------------------------------------------------------------------
    # Dictate in whatever language you think in and have the text arrive in the
    # one you write in. It runs inside the SAME model call as the polish pass
    # (one round trip, not two) and under the same fail-open contract: on a
    # timeout, a dead provider or a failed guard the transcript is delivered as
    # spoken, and the history row says why.

    #: Master switch, and it ships OFF — the one switch in this block that
    #: does. The polish pass can default on because it only changes how the
    #: user's words are WRITTEN; this changes which words come out at all, and
    #: a person who never read a release note must not discover that their
    #: German dictation now arrives in English. Turning it on is a deliberate
    #: act, and it is the only thing standing between the two behaviours.
    translate: bool = False

    #: The language every dictation is delivered in while ``translate`` is on,
    #: whatever language was actually spoken. One fixed target rather than a
    #: per-source-language table: the overwhelmingly common want is "I think in
    #: my own language and write in this one", and a rule table would trade
    #: that one clear switch for a screen of pairs nobody audits.
    #:
    #: The decision reads THIS BLOCK only (``resolve_translate_target``), never
    #: the language the recognizer reported. Skipping the translation whenever
    #: the recognized language already matched the target sounded like a saved
    #: round trip and behaved like a coin flip — that tag is documented to be
    #: wrong — so the delivered language alternated with nothing the user
    #: touched explaining it. The one exception is ``language`` above: a PIN is
    #: a deliberate statement, so pinning it to this target means "I dictate in
    #: it already" and nothing is translated.
    #:
    #: Unrelated to ``[brain].reply_language``, which governs what the
    #: assistant SAYS BACK. Dictating into an English document while being
    #: answered in German is a normal thing to want.
    translate_target: str = "en"

    #: The band the translated word count must land in, as a fraction of the
    #: spoken word count. Far wider than the polish band above, and that is the
    #: point rather than a weaker guard: a faithful translation legitimately
    #: changes length in both directions (German compounds collapse into one
    #: English word; English phrasal verbs expand into German subclauses), so
    #: the polish band would reject correct translations by construction. What
    #: survives still catches the failure that matters — a model that answered
    #: the transcript instead of translating it.
    translate_drift_max_shrink: float = Field(default=0.40, ge=0.0, le=1.0)
    translate_drift_max_growth: float = Field(default=2.50, ge=1.0, le=10.0)

    @field_validator("paste_chord", mode="before")
    @classmethod
    def _coerce_paste_chord(cls, value: object) -> str:
        """Normalize only — an unusable value falls back to ``auto``.

        The rejection message is thrown away here on purpose: this validator
        runs on every config load, including one triggered by the self-mod
        pipeline, and an exception there costs a boot (AP-16). The REST layer
        calls ``normalize_paste_chord`` directly so a user who types a bad
        chord gets the sentence instead of a silent fallback.
        """
        from jarvis.dictation.insert import normalize_paste_chord

        return normalize_paste_chord(str(value or ""))[0]

    @field_validator("language", mode="before")
    @classmethod
    def _coerce_dictation_language(cls, value: object) -> str:
        """Normalize only — an unknown value falls back to ``auto``.

        A stale or hand-edited config must never fail validation (AP-16), and
        ``auto`` is always a working answer.
        """
        text = str(value or "").strip().lower()
        return text if text in DICTATION_LANGUAGES else "auto"

    @field_validator("translate_target", mode="before")
    @classmethod
    def _coerce_translate_target(cls, value: object) -> str:
        """Normalize only — an unusable target falls back to ``en``.

        Same AP-16 contract as ``_coerce_dictation_language``: a stale or
        hand-edited config must never fail validation. The fallback is a real
        language rather than ``auto`` because this field has no "detect it"
        answer — while ``translate`` is off nothing reads it, and while it is on
        it has to name somewhere for the words to go.
        """
        text = str(value or "").strip().lower()
        return text if text in TRANSLATION_TARGETS else "en"

    @field_validator("polish_provider", mode="before")
    @classmethod
    def _coerce_polish_provider(cls, value: object) -> str:
        """Normalize only — an empty or unusable pin falls back to ``auto``.

        Deliberately NOT checked against the list of families. That list is
        the single source of truth in the polish client, and importing it
        here would put a provider module on the config-load path (AP-26) and
        mirror a vocabulary into a second place (AP-4). A pin nothing answers
        to resolves exactly like ``auto`` in the chain, which is the working
        value AP-16 asks for.
        """
        text = str(value or "").strip().lower()
        return text or "auto"

    @field_validator("polish_model", mode="before")
    @classmethod
    def _coerce_polish_model(cls, value: object) -> str:
        """Normalize only — anything unusable becomes the family default.

        Case is preserved on purpose: model ids are case-sensitive on several
        families (``Qwen/Qwen3-32B``), so only surrounding whitespace goes.
        """
        return str(value or "").strip()

    @field_validator("polish_style", mode="before")
    @classmethod
    def _coerce_polish_style(cls, value: object) -> str:
        """Normalize only — an unknown style falls back to ``neutral``.

        Mirrors ``_coerce_dictation_language`` above, for the same reason: a
        stale or hand-edited config must never fail validation (AP-16), and
        ``neutral`` — append nothing to the prompt — always works.
        """
        text = str(value or "").strip().lower()
        return text if text in POLISH_STYLES else "neutral"

    # The numeric knobs below are CLAMPED rather than rejected. Their bounds
    # are declared on the ``Field`` too, so the schema keeps stating the real
    # range, but an out-of-range value is pulled back in instead of raising:
    # nobody should lose a boot to a typo in a latency budget (AP-16). The
    # visible consequence is that a bad value sent through
    # ``PUT /api/dictation/settings`` comes back corrected on the next GET
    # rather than as a 400 — for knobs that only trade latency against text
    # quality, that is the friendlier failure.

    @field_validator("polish_timeout_ms", mode="before")
    @classmethod
    def _clamp_polish_timeout_ms(cls, value: object) -> int:
        return _clamped_polish_int(value, default=1200, low=200, high=5000)

    @field_validator("polish_max_input_chars", mode="before")
    @classmethod
    def _clamp_polish_max_input_chars(cls, value: object) -> int:
        return _clamped_polish_int(value, default=0, low=0, high=1_000_000)

    @field_validator("polish_min_words", mode="before")
    @classmethod
    def _clamp_polish_min_words(cls, value: object) -> int:
        return _clamped_polish_int(value, default=4, low=0, high=100)

    @field_validator("polish_max_output_tokens", mode="before")
    @classmethod
    def _clamp_polish_max_output_tokens(cls, value: object) -> int:
        return _clamped_polish_int(value, default=1200, low=64, high=8192)

    @field_validator("polish_temperature", mode="before")
    @classmethod
    def _clamp_polish_temperature(cls, value: object) -> float:
        return _clamped_polish_float(value, default=0.0, low=0.0, high=2.0)

    @field_validator("polish_drift_max_shrink", mode="before")
    @classmethod
    def _clamp_polish_drift_max_shrink(cls, value: object) -> float:
        return _clamped_polish_float(value, default=0.55, low=0.0, high=1.0)

    @field_validator("polish_drift_max_growth", mode="before")
    @classmethod
    def _clamp_polish_drift_max_growth(cls, value: object) -> float:
        return _clamped_polish_float(value, default=1.20, low=1.0, high=3.0)

    @field_validator("translate_drift_max_shrink", mode="before")
    @classmethod
    def _clamp_translate_drift_max_shrink(cls, value: object) -> float:
        return _clamped_polish_float(value, default=0.40, low=0.0, high=1.0)

    @field_validator("translate_drift_max_growth", mode="before")
    @classmethod
    def _clamp_translate_drift_max_growth(cls, value: object) -> float:
        return _clamped_polish_float(value, default=2.50, low=1.0, high=10.0)


class MarketplaceConfig(BaseModel):
    """Plugin-marketplace connect settings (OAuth redirect mode).

    ``public_callback_base_url`` switches redirect-based OAuth handlers from the
    loopback callback server (desktop, browser reaches 127.0.0.1) to a hosted
    FastAPI callback at ``<base>/api/marketplace/oauth/callback`` (headless VPS).
    Empty string keeps the loopback/desktop behavior.
    """

    public_callback_base_url: str = ""
    # Where the community marketplace index lives. The default is the public
    # registry's GitHub Pages deployment; forks and air-gapped mirrors point
    # this at their own compiled index. Empty string disables the community
    # section entirely (browse shows only the shipped seed catalog).
    community_index_url: str = (
        "https://personaljarvis.github.io/marketplace/index.json"
    )
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


class Phase6SafetyConfig(BaseModel):
    """``[phase6.safety]`` — mission-worker safety knobs (ADR-0031).

    First Pydantic-modeled slice of the ``[phase6.*]`` tables (they were
    documentation-plus-intent before; ``bootstrap_missions`` kwargs stay the
    wiring for the older flags). ``extra="allow"`` so pre-existing raw keys
    keep loading (AP-16).
    """

    model_config = ConfigDict(extra="allow")

    injection_scanner_enabled: bool = True
    destructive_confirm_enabled: bool = True
    extra_blocked_globs: list[str] = Field(default_factory=list)
    # Mission-scoped tool pre-authorization (ADR-0031). When enabled, tools a
    # mission's broker grant contains AND that appear below are auto-approved
    # for THAT mission's calls only — the gate is answered on the bus (full
    # Proposed→Approved→Executed audit), never bypassed. ``False`` restores
    # the pre-ADR-0031 behavior (every ask-tier call waits for a human).
    worker_tool_auto_approve: bool = True
    # Exact tool names, or an MCP server family as ``server/`` (trailing
    # slash). Prefer EXACT names: a server prefix silently authorizes every
    # future tool that server adds, including destructive ones. Messaging /
    # mail / social-send families deliberately stay OUT of the default —
    # widen only by explicit operator choice. The shipped default lists
    # read-only knowledge tools so a plausibility-escalated call can never
    # stall an unattended mission.
    auto_approve_tool_families: list[str] = Field(
        default_factory=lambda: [
            "search_web",
            "wiki-list",
            "wiki-recall",
            "wiki-page-read",
            "wiki-ingest",
            "session-latest-turn",
        ]
    )


class Phase6Config(BaseModel):
    """``[phase6]`` root — mission subsystem configuration.

    Only ``safety`` is typed so far; the other tables (orchestrator, budget,
    voice, cleanup) remain raw TOML consumed via ``bootstrap_missions``
    defaults and stay loadable through ``extra="allow"`` (AP-16).
    """

    model_config = ConfigDict(extra="allow")

    safety: Phase6SafetyConfig = Field(default_factory=Phase6SafetyConfig)


class GlmCodingPlanConfig(BaseModel):
    """Where a GLM pane sends its traffic, and which model ids it asks for.

    The GLM Coding Plan has no CLI of its own: the vendor's own instructions are
    to run the ordinary Claude Code binary against a different endpoint. So a
    "GLM" pane is that binary plus this environment — which makes every value
    here load-bearing, and every one of them a *setting* rather than a constant.

    Why nothing is hardcoded:

    * ``base_url`` — accounts registered in mainland China use a different host
      from the international ones, and the two are not interchangeable. A fixed
      URL simply locks out whichever half of the world it is not.
    * the model ids — vendor documentation disagrees with itself about the
      current names, and a wrong id fails at request time in a way that reads
      like our bug. Empty means "send no model override at all" and let the
      endpoint apply its own mapping, which is the only default that cannot be
      wrong. Fill them in to pin a specific model.
    * ``request_timeout_ms`` — the CLI's stock timeout is tuned for a different
      backend and expires mid-answer here, which surfaces as a hang rather than
      as an error.

    The API key is deliberately NOT in this model: it lives in the credential
    store under ``zai_api_key`` (ENV fallback ``ZAI_API_KEY``), because a key in
    ``jarvis.toml`` is a key in a file that gets copied, shared and pasted into
    bug reports (AP-12).
    """

    model_config = ConfigDict(extra="allow")

    base_url: str = Field(
        default="https://api.z.ai/api/anthropic",
        description=(
            "Anthropic-compatible endpoint a GLM pane talks to. Use "
            "https://open.bigmodel.cn/api/anthropic for a mainland-China account."
        ),
    )
    opus_model: str = Field(
        default="",
        description="Model id for the CLI's 'opus' tier. Empty = let the endpoint decide.",
    )
    sonnet_model: str = Field(
        default="",
        description="Model id for the CLI's 'sonnet' tier. Empty = let the endpoint decide.",
    )
    haiku_model: str = Field(
        default="",
        description="Model id for the CLI's 'haiku' tier. Empty = let the endpoint decide.",
    )
    request_timeout_ms: int = Field(
        default=3_000_000,
        ge=60_000,
        description=(
            "Per-request timeout handed to the CLI. The stock value is far too "
            "short for this backend's long agentic runs and expires mid-answer."
        ),
    )


class AgenticIdeConfig(BaseModel):
    """Agentic IDE behaviour that is not per-workspace state."""

    # AP-16: a key written by a NEWER install must not fail this one's boot.
    model_config = ConfigDict(extra="allow")

    glm: GlmCodingPlanConfig = Field(default_factory=GlmCodingPlanConfig)

    prompt_writer: str = Field(
        default="auto",
        description=(
            "Who writes Agentic IDE task briefs: 'auto' (a connected coding "
            "subscription if there is one, else the API-billed quality tier), "
            "'subscription', 'api', or a specific brain provider id."
        ),
    )

    smart_recaps: bool = Field(
        default=True,
        description=(
            "Let a model write each pane's header recap — what the pane set out "
            "to do, where it stands, what is outstanding. Off falls back to the "
            "transcript-derived one, which costs nothing and says much less. An "
            "install with no reachable provider gets the fallback either way."
        ),
    )

    pane_notifications: bool = Field(
        default=True,
        description=(
            "Collect a notification whenever a terminal stops working, asks a "
            "question, or its agent exits — the bell in the Agentic IDE header. "
            "Off stops the background sweep entirely; the bell then only shows "
            "what was already collected."
        ),
    )

    @field_validator("prompt_writer", mode="before")
    @classmethod
    def _usable_writer(cls, value: object) -> str:
        """Anything unusable becomes 'auto' instead of blocking the boot.

        Whether a named provider EXISTS is decided at resolve time, not here: a
        config naming a provider this build does not ship has to degrade to the
        normal chain, never stop the app that config belongs to from starting.
        """
        text = str(value or "").strip()
        # A provider id always starts with a letter; a bare number reaching here
        # is a mis-typed or mis-serialised value, not a provider nobody has heard
        # of yet.
        if not text or not text[0].isalpha():
            return "auto"
        return text if text.replace("-", "").replace("_", "").isalnum() else "auto"


class GoogleAuthConfig(BaseModel):
    """Google credential routing: AI Studio vs Vertex AI express mode.

    Google serves the same Gemini models behind two API-key families. Classic
    AI Studio keys (``AIza...``) talk to ``generativelanguage.googleapis.com``;
    Vertex AI *express mode* keys (``AQ....``) are only accepted when the
    google-genai client is built with ``vertexai=True``. Newer AI Studio keys
    ALSO start with ``AQ.``, so the prefix alone cannot decide. ``auto`` (the
    default) probes an ambiguous key once per process and remembers the
    answer; ``always``/``never`` force the route for installs where the probe
    guesses wrong or the network blocks it. Read exclusively by
    ``jarvis.core.google_genai`` (AP-31: no unread switch).
    """

    model_config = ConfigDict(extra="allow")

    vertex_mode: Literal["auto", "always", "never"] = "auto"


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
    # Google key routing (AI Studio vs Vertex express) — see GoogleAuthConfig.
    google: GoogleAuthConfig = Field(default_factory=GoogleAuthConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    agentic_ide: AgenticIdeConfig = Field(default_factory=AgenticIdeConfig)
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
    # Persona mandate, Phase 5: top-level ``[vision]`` section.
    vision: VisionContextConfig = Field(default_factory=VisionContextConfig)
    # One-shot, intent-driven screen look (jarvis/screen_context/). Distinct
    # from ``[vision]`` above, which governs the always-on observation path.
    screen_context: ScreenContextConfig = Field(default_factory=ScreenContextConfig)
    # Phase 5/6 — Computer-Use-POAV-Harness (ADR-0008).
    computer_use: ComputerUseConfig = Field(default_factory=ComputerUseConfig)
    # Low-latency local-action gate. Hidden tools only; never exposed in the
    # router LLM schema.
    local_action: LocalActionConfig = Field(default_factory=LocalActionConfig)
    # Phase 8.4 — review pipeline configuration.
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    # Phase 6 — mission subsystem ([phase6.safety] typed; rest raw, AP-16).
    phase6: Phase6Config = Field(default_factory=Phase6Config)
    # Latency sprint 1 (2026-04-30) — master switches for performance levers.
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    # Wave 0 (omni-latency) — hot-path latency span instrumentation toggle.
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    # Phase A0+: awareness layer (continuous context). Entire subsystem
    # hot-disabled via [awareness].enabled = false (plan §15).
    awareness: AwarenessConfig = Field(default_factory=AwarenessConfig)
    # Phase B5 — wiki write-wiring: SessionRollupWorker + WikiCurator bootstrap (Agent A).
    wiki_integration: WikiIntegrationConfig = Field(default_factory=WikiIntegrationConfig)
    # UltraWiki — the semantic memory mode of the Wiki section (UltraWiki/*.md).
    ultrawiki: UltraWikiConfig = Field(default_factory=UltraWikiConfig)
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
    ack_brain: AckBrainConfig = Field(  # noqa: F821 (resolved by model_rebuild below)
        default_factory=lambda: AckBrainConfig()
    )
    # Speech pipeline sub-configs (completeness classifier, …).
    # TOML path: [speech] / [speech.completeness]
    speech: SpeechConfig = Field(default_factory=SpeechConfig)
    # Dictation mode (hold to speak, transcript lands in the focused field).
    dictation: DictationConfig = Field(default_factory=DictationConfig)
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

#: Parsed ``jarvis.toml`` payloads, keyed by path → (identity, data).
#:
#: ``load_config`` is called from over a hundred sites, several of them on the
#: event loop that also serves every WebSocket — a provider building its client
#: calls it per instantiation, and a fallback chain instantiates several
#: providers per turn. Re-reading and re-parsing a 50 KB TOML there costs ~8 ms
#: of blocked loop each time, and far worse than the milliseconds: ``tomllib``
#: allocates thousands of short-lived objects per parse, so in a long-running
#: process holding a large object graph the garbage collector starts dominating
#: and a single parse can stall for minutes. Measured live 2026-07-28: the
#: backend thread sat in ``tomllib`` at a fixed byte offset for over ten
#: minutes at 88 % of a core with ``/api/health`` timing out, while the very
#: same file parsed in 8 ms in a fresh process. The window title said
#: "Not responding" and keystrokes typed into an Agentic-IDE pane arrived
#: seconds late, because they queue behind this on the one loop.
_TOML_CACHE: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}
_TOML_CACHE_LOCK = threading.Lock()


def _copy_toml_data(value: Any) -> Any:
    """Structural copy of a parsed TOML payload.

    Handing out the cached object itself is not an option: ``_apply_env_overrides``
    writes overrides straight into the dict it is given, so the cache would
    accumulate every override ever applied and answer later callers with a
    config that was never on disk.

    A hand-rolled walk rather than ``copy.deepcopy`` because TOML yields only
    dicts, lists and immutable scalars (including ``datetime``), so none of
    deepcopy's memo bookkeeping or ``__deepcopy__`` dispatch buys anything here
    — and it is what keeps the copy cheap enough to be worth caching at all
    (0.10 ms against 0.73 ms and an 8.18 ms parse).
    """
    if type(value) is dict:
        return {key: _copy_toml_data(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_toml_data(item) for item in value]
    return value


def clear_config_cache() -> None:
    """Forget everything derived from the config file.

    Both caches invalidate themselves off the file's identity, so this exists
    for the two cases that identity cannot see: a test that rewrites a fixture
    within one filesystem timestamp tick, and :mod:`jarvis.core.config_writer`
    announcing a write it just made rather than waiting to be found out.

    The endpoint-routing cache is cleared with it, and must stay that way: it is
    derived from the same file, so anything that can leave one stale leaves the
    other stale too — and a stale route sends a provider's traffic to an address
    the user has already changed.
    """
    with _TOML_CACHE_LOCK:
        _TOML_CACHE.clear()
        _ENDPOINT_ROUTE_CACHE.clear()


def _load_toml(path: Path) -> dict[str, Any]:
    # Modification time AND size, because either alone is forgeable by an
    # ordinary edit: a rewrite within the same timestamp tick keeps the mtime,
    # and flipping a single flag keeps the size. A file we cannot stat is
    # simply not cached — the read below then reports the real error.
    identity: tuple[int, int] | None = None
    try:
        info = path.stat()
        identity = (info.st_mtime_ns, info.st_size)
    except OSError:
        identity = None

    if identity is not None:
        with _TOML_CACHE_LOCK:
            cached = _TOML_CACHE.get(path)
        if cached is not None and cached[0] == identity:
            return _copy_toml_data(cached[1])

    # tomllib does not accept UTF-8 BOM; Windows editors (Notepad etc.)
    # write it automatically on Save-As. If the file is otherwise readable,
    # we should not silently cripple the entire brain stack — so strip the
    # BOM once.
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    data = tomllib.loads(raw.decode("utf-8"))

    if identity is not None:
        with _TOML_CACHE_LOCK:
            _TOML_CACHE[path] = (identity, data)
        # The stored payload must stay the pristine parse, so the caller gets
        # its own copy to mutate rather than the object the cache keeps.
        return _copy_toml_data(data)
    return data


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
    "JARVIS__BRAIN__TOOL_MODEL__PROVIDER",
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


def _base_model_type(annotation: Any) -> type[BaseModel] | None:
    """Return the BaseModel carried by an annotation, including unions."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for candidate in get_args(annotation):
        model_type = _base_model_type(candidate)
        if model_type is not None:
            return model_type
    return None


def _container_type(annotation: Any) -> type[dict] | type[list] | None:
    """Return the mapping/list shape carried by a field annotation."""
    origin = get_origin(annotation)
    candidate = origin or annotation
    if isinstance(candidate, type) and issubclass(candidate, Mapping):
        return dict
    if candidate is list:
        return list
    for nested in get_args(annotation):
        container_type = _container_type(nested)
        if container_type is not None:
            return container_type
    return None


@lru_cache(maxsize=256)
def _expected_env_container(path: tuple[str, ...]) -> type[dict] | type[list] | None:
    """Resolve a structured ENV target from the Pydantic config schema."""
    model_type: type[BaseModel] = JarvisConfig
    for index, segment in enumerate(path):
        field = model_type.model_fields.get(segment)
        if field is None:
            return None
        if index == len(path) - 1:
            return _container_type(field.annotation)
        nested_model = _base_model_type(field.annotation)
        if nested_model is None:
            return None
        model_type = nested_model
    return None


def _apply_env_overrides(data: dict[str, Any], prefix: str = "JARVIS__") -> dict[str, Any]:
    """Override config with env variables in the format JARVIS__SECTION__KEY=value.

    Example: JARVIS__BRAIN__PRIMARY=openrouter → config["brain"]["primary"]
    """
    stt_section = data.get("stt")
    stt_provider_user_selected = bool(
        isinstance(stt_section, dict)
        and stt_section.get("provider_user_selected") is True
    )
    for env_key, env_val in tuple(os.environ.items()):
        if not env_key.startswith(prefix):
            continue
        path = env_key[len(prefix):].lower().split("__")
        if path == ["stt", "provider"] and stt_provider_user_selected:
            logging.getLogger(__name__).debug(
                "Ignoring %s because the persisted STT provider was user-selected",
                env_key,
            )
            continue
        cursor = data
        blocked = False
        for segment in path[:-1]:
            existing_segment = cursor.get(segment)
            if existing_segment is None:
                existing_segment = {}
                cursor[segment] = existing_segment
            if not isinstance(existing_segment, dict):
                blocked = True
                break
            cursor = existing_segment
        if blocked:
            os.environ.pop(env_key, None)
            logging.getLogger(__name__).warning(
                "Ignoring config override %s because its path crosses a scalar",
                env_key,
            )
            continue
        value = _coerce_env_value(env_val)
        existing_value = cursor.get(path[-1])
        expected_container = _expected_env_container(tuple(path))
        mapping_conflict = (
            expected_container is dict or isinstance(existing_value, dict)
        ) and not isinstance(value, dict)
        list_conflict = (
            expected_container is list or isinstance(existing_value, list)
        ) and not isinstance(value, list)
        if mapping_conflict or list_conflict:
            # An older drift-guard serialized structured JSON as a PowerShell
            # string such as "@{provider=model}". Replacing a TOML mapping/list
            # with that scalar bricks Pydantic validation and desktop startup.
            os.environ.pop(env_key, None)
            logging.getLogger(__name__).warning(
                "Ignoring scalar config override %s for a structured value",
                env_key,
            )
            continue
        cursor[path[-1]] = value
    return data


def _coerce_env_value(v: str) -> Any:
    """Coerce an environment string to JSON containers or a scalar."""
    lv = v.strip().lower()
    if lv.startswith(("{", "[")):
        try:
            parsed = json.loads(v)
        except (TypeError, ValueError):
            # Invalid JSON-shaped input intentionally falls through to scalar coercion.
            pass
        else:
            if isinstance(parsed, (dict, list)):
                return parsed
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


def _dedupe_worker_tier_tables(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the [brain.worker] vs legacy [brain.sub_jarvis] split-brain.

    Both tables populate the SAME field (``BrainConfig.worker`` via
    ``AliasChoices``); a file carrying both is a latent conflict whose winner
    would otherwise be an artifact of alias ordering. Make it explicit code:
    the canonical ``[brain.worker]`` wins, the legacy table is dropped from
    the parsed dict, and ONE warning names both values so the operator can
    see what was ignored. A file with only the legacy table stays untouched
    (read-compat for pre-rename installs). The on-disk heal lives in
    ``config_writer.migrate_worker_tier_table`` (called at boot).
    """
    brain = data.get("brain")
    if not isinstance(brain, dict):
        return data
    worker = brain.get("worker")
    legacy = brain.get("sub_jarvis")
    if isinstance(worker, dict) and isinstance(legacy, dict):
        logging.getLogger(__name__).warning(
            "config: both [brain.worker] (provider=%r) and legacy "
            "[brain.sub_jarvis] (provider=%r) are present — [brain.worker] "
            "wins; the legacy table is ignored and will be merged away at "
            "the next boot heal.",
            worker.get("provider"),
            legacy.get("provider"),
        )
        brain.pop("sub_jarvis", None)
    return data


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


#: Paths whose dictation-shortcut backfill has already been attempted in THIS
#: process. The on-disk marker is the durable guard; this only keeps a hot
#: ``load_config`` loop from re-reading the file for a migration that is done.
_DICTATION_HOTKEY_HEALED: set[Path] = set()
_LEGACY_CODEX_REALTIME_HEALED: set[Path] = set()


def _heal_legacy_codex_realtime_once(path: Path) -> None:
    """Route a removed Codex Realtime selection onto the stable composition."""
    if path in _LEGACY_CODEX_REALTIME_HEALED:
        return
    _LEGACY_CODEX_REALTIME_HEALED.add(path)
    try:
        from jarvis.core.config_writer import migrate_removed_codex_realtime_provider

        migrated = migrate_removed_codex_realtime_provider(path=path)
    except Exception:  # noqa: BLE001 - a boot migration must not block startup
        logging.getLogger(__name__).warning(
            "Could not migrate the removed Codex Realtime voice selection.",
            exc_info=True,
        )
        return
    if migrated:
        logging.getLogger(__name__).warning(
            "Migrated the removed codex-subscription-realtime selection to "
            "the stable ChatGPT subscription voice profile (Pipeline mode)."
        )


def _heal_dictation_hotkeys_once(path: Path) -> None:
    """Run the one-time dictation-shortcut backfill. Never raises, never blocks.

    Kept to one cheap file read per process: the writer itself short-circuits
    on a string probe once the marker is in the file, and this set stops even
    that read from repeating.
    """
    if path in _DICTATION_HOTKEY_HEALED:
        return
    _DICTATION_HOTKEY_HEALED.add(path)
    try:
        from jarvis.core.config_writer import migrate_dictation_hotkey_defaults

        migrate_dictation_hotkey_defaults(path=path)
    except Exception:  # noqa: BLE001, S110 — a boot heal must never block a load
        pass


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
        # The codex-subscription-realtime adapter was removed 2026-08-10. A
        # config still pinning it is routed onto the stable subscription
        # profile before the first parse so the UI and runtime see one state.
        _heal_legacy_codex_realtime_once(config_file)
        # One-time dictation-shortcut backfill BEFORE the file is read, so the
        # very first boot after the update already sees the healed values
        # (BUG-010 config drift; see config_writer for why a marker and not an
        # empty-means-default rule). Only for the RESOLVED path: a caller that
        # names a file explicitly — a test, a doctor script — gets it read, not
        # rewritten. Process-local guard so repeated loads cost nothing.
        _heal_dictation_hotkeys_once(config_file)
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

    # Deterministic winner for the worker-tier split-brain BEFORE env
    # overrides land (env writes into brain.worker and wins regardless).
    data = _dedupe_worker_tier_tables(data)
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
_FILE_BACKEND_ACTIVE: bool = False
# The platform backend that was active before a runtime failure forced the
# process-wide ``keyring`` module onto our file backend. Retaining it matters
# for mixed states: one failed write must not hide other credentials that are
# still readable from the OS store, and recovery probes must not mistake the
# currently installed file backend for a recovered platform backend.
_PLATFORM_KEYRING_BACKEND: Any | None = None
# The credential slot whose OS-keyring read failed most recently. On macOS a
# user who clicks "Deny" on the Keychain prompt lands exactly here: the read
# raises, the process degrades to the file backend, and only a fresh read of a
# real existing item makes macOS show the prompt again. The permissions UI
# replays this slot on a user-initiated retry (a probe on a brand-new item
# would silently succeed without ever re-prompting).
_LAST_KEYRING_FAILED_SLOT: str | None = None
_SECRET_REVISION_LOCK = threading.Lock()
_SECRET_REVISIONS: dict[str, int] = {}


def secret_revision(key: str) -> int:
    """Return the in-process revision for one credential slot.

    Provider instances use this cheap counter to refresh a replaced credential
    without performing a keyring read on every request. External credential-store
    edits still require a process restart; in-app writes increment the counter.
    """
    with _SECRET_REVISION_LOCK:
        return _SECRET_REVISIONS.get(key, 0)


def _mark_secret_changed(key: str) -> None:
    with _SECRET_REVISION_LOCK:
        _SECRET_REVISIONS[key] = _SECRET_REVISIONS.get(key, 0) + 1


# Serializes _FileCredStore's load-mutate-save cycle so two in-process
# writers (e.g. a plugin connect + a concurrent API-key save) cannot race and
# silently drop one of the two updates. Matches the config_writer lock
# pattern. Cross-PROCESS locking is a separate, deferred concern — this only
# protects concurrent callers within one Jarvis process.
_FILE_CRED_STORE_LOCK = threading.Lock()


class _FileCredStore:
    """Minimal 0600 JSON credential store keyed by ``(service, username)``."""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit = path

    def _file(self) -> Path:
        p = (
            self._explicit
            if self._explicit is not None
            else (_resolve_writable_data_dir() / "credentials.json")
        )
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
        # Per-process-unique temp name: two processes racing a write (no
        # cross-process lock yet) must not clobber each other's in-flight tmp
        # file before either reaches its atomic os.replace.
        tmp = f.with_name(f"{f.name}.tmp.{os.getpid()}")
        try:
            # Born 0600, never chmodded down after the fact: write_text +
            # chmod created the file 0644 for a moment, and on a multi-user
            # Mac (this store is the documented fallback after a declined
            # Keychain prompt) that window exposed every stored key to any
            # other local account. A stale same-pid tmp from a crashed run
            # is removed first so O_EXCL cannot trip over it.
            tmp.unlink(missing_ok=True)
            descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(data))
            os.replace(tmp, f)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"failed to write credential store {f}: {exc}") from exc

    @staticmethod
    def _k(service: str, username: str) -> str:
        return f"{service}\x00{username}"

    def get(self, service: str, username: str) -> str | None:
        return self._load().get(self._k(service, username))

    def set(self, service: str, username: str, password: str) -> None:
        with _FILE_CRED_STORE_LOCK:
            data = self._load()
            data[self._k(service, username)] = password
            self._save(data)

    def delete(self, service: str, username: str) -> None:
        with _FILE_CRED_STORE_LOCK:
            data = self._load()
            data.pop(self._k(service, username), None)
            self._save(data)


def _is_platform_keyring_backend(backend: Any) -> bool:
    """Return whether *backend* is a real platform credential-store candidate."""
    if backend is None or getattr(backend, "_jarvis_file_backend", False):
        return False
    if getattr(backend, "_jarvis_platform_wrapper", False):
        # DarwinBundleKeyringBackend (macOS Keychain item-count collapse,
        # BUG-103) wraps the real platform backend rather than subclassing
        # it. Delegate the check to the wrapped instance instead of
        # inspecting class/module names against an unpinned library (AP-28).
        return _is_platform_keyring_backend(getattr(backend, "_inner", None))
    try:
        # ``fail.Keyring`` advertises zero priority. Use that capability rather
        # than an isinstance check against unpinned third-party internals.
        priority = getattr(backend, "priority", None)
        return priority is None or priority > 0
    except Exception:  # noqa: BLE001
        return False


def _install_file_cred_backend(
    reason: str, *, retain_platform_backend: bool = True
) -> bool:
    """Force the local 0600 file credential store as the active keyring backend.

    Used both when NO OS keyring exists at all (headless VPS → ``fail.Keyring``)
    AND when a reachable-but-unusable OS keyring RAISES at runtime — e.g. a Linux
    Secret Service that is present but whose collection is LOCKED, which
    ``keyring.get_keyring()`` still reports as a viable backend, so the
    ``fail.Keyring`` check alone never engages the fallback. Idempotent: safe to
    call repeatedly (it just re-sets the same backend); the swap is logged once.
    The OS keyring stays the secure path whenever it is functional — the file
    backend is installed only when the real one is absent or provably broken.

    Returns True iff the file backend is now active.
    """
    global _FILE_BACKEND_ACTIVE, _PLATFORM_KEYRING_BACKEND
    try:
        import keyring
        import keyring.backend

        current_backend = keyring.get_keyring()
        if not retain_platform_backend:
            _PLATFORM_KEYRING_BACKEND = None
        elif _is_platform_keyring_backend(current_backend):
            _PLATFORM_KEYRING_BACKEND = current_backend

        _store = _FileCredStore()

        class _FileKeyringBackend(keyring.backend.KeyringBackend):
            priority = 0.1  # type: ignore[assignment]  # below any real OS backend
            _jarvis_file_backend = True

            def get_password(self, service: str, username: str) -> str | None:
                return _store.get(service, username)

            def set_password(self, service: str, username: str, password: str) -> None:
                _store.set(service, username, password)

            def delete_password(self, service: str, username: str) -> None:
                _store.delete(service, username)

        keyring.set_keyring(_FileKeyringBackend())
        if not _FILE_BACKEND_ACTIVE:
            logging.getLogger(__name__).warning(
                "OS credential store unusable (%s) — API keys are stored in a local "
                "0600 file under %s. Configure a Secret Service / Keychain for "
                "OS-encrypted storage.", reason, DATA_DIR / "credentials.json",
            )
        _FILE_BACKEND_ACTIVE = True
        return True
    except Exception:  # noqa: BLE001
        return False


def _ensure_keyring_backend() -> None:
    """Install the local-file credential store when the OS keyring is non-functional.

    Runs once. Installs the file backend when the current backend is the no-op
    ``fail.Keyring`` (no OS keyring at all — the headless VPS case). A
    reachable-but-LOCKED keyring that only reveals itself by RAISING on a real
    read/write is handled at runtime by ``get_secret``/``set_secret``/
    ``delete_secret`` via ``_install_file_cred_backend``. Any error is swallowed —
    a missing keyring must never break boot.

    On macOS, a viable platform backend is additionally wrapped in
    ``DarwinBundleKeyringBackend`` (BUG-103) so every Jarvis secret collapses
    into ONE Keychain item instead of one item per credential slot — an
    unsigned interpreter otherwise re-prompts "Always Allow" separately for
    each of the ~10 provider slots the pre-boot key check reads. Windows and
    Linux never wrap, so their behavior is unchanged.
    """
    global _KEYRING_BACKEND_READY
    if _KEYRING_BACKEND_READY:
        return
    _KEYRING_BACKEND_READY = True
    try:
        import keyring

        current_backend = keyring.get_keyring()
        if not _is_platform_keyring_backend(current_backend):
            _install_file_cred_backend(
                "no OS credential store available (headless host)",
                retain_platform_backend=False,
            )
        elif sys.platform == "darwin" and not getattr(
            current_backend, "_jarvis_platform_wrapper", False
        ):
            from .keychain_bundle import (
                DarwinBundleKeyringBackend,
                darwin_security_cli_vault,
            )

            keyring.set_keyring(
                DarwinBundleKeyringBackend(
                    current_backend, cli=darwin_security_cli_vault()
                )
            )
    except Exception:  # noqa: BLE001, S110 -- a missing keyring must never break boot
        pass


def _try_restore_platform_keyring_backend() -> bool:
    """Restore a recovered OS keyring before an explicit credential save.

    A transient OS-keyring read/write failure switches this process to the
    portable file backend.  Without a recovery attempt, every later in-app save
    keeps writing only that file while an older OS-keyring value survives and
    shadows it after the next restart.  Explicit user saves are the safe recovery
    boundary: re-detect the platform backend, capability-probe it with a
    non-existent slot, and retain the file backend when the platform store is
    still unavailable (the normal headless-host path).

    This never runs on boot or a normal read, so a locked Secret Service cannot
    add startup latency or prompts.  Returns ``True`` when the platform backend
    is active, including when no fallback swap had occurred.
    """
    global _FILE_BACKEND_ACTIVE, _PLATFORM_KEYRING_BACKEND
    if not _FILE_BACKEND_ACTIVE:
        return True

    keyring_mod = None
    previous_backend = None
    candidate = None
    try:
        import keyring as keyring_mod
        from keyring.core import init_backend

        previous_backend = keyring_mod.get_keyring()
        init_backend()
        candidate = keyring_mod.get_keyring()
        if not _is_platform_keyring_backend(candidate):
            raise RuntimeError("platform backend discovery found no usable backend")

        # ``init_backend()`` just installed the RAW auto-detected backend
        # (unwrapped — it replaces whatever the process-global keyring
        # pointed at). Re-wrap it on macOS before the probe below runs, so
        # the probe proves the bundle lifecycle rather than the raw
        # per-item backend (BUG-103).
        if sys.platform == "darwin" and not getattr(
            candidate, "_jarvis_platform_wrapper", False
        ):
            from .keychain_bundle import (
                DarwinBundleKeyringBackend,
                darwin_security_cli_vault,
            )

            candidate = DarwinBundleKeyringBackend(
                candidate, cli=darwin_security_cli_vault()
            )
            keyring_mod.set_keyring(candidate)

        # A read-only probe was insufficient on Windows: WinVault could read an
        # old value while every write failed with error 1312. Use a unique,
        # disposable entry and prove the full write/read/delete lifecycle.
        probe_key = f"__jarvis_backend_probe__{secrets.token_hex(8)}"
        probe_value = secrets.token_urlsafe(24)
        try:
            candidate.set_password(KEYRING_SERVICE, probe_key, probe_value)
            if candidate.get_password(KEYRING_SERVICE, probe_key) != probe_value:
                raise RuntimeError("platform credential-store probe read mismatch")
            candidate.delete_password(KEYRING_SERVICE, probe_key)
            if candidate.get_password(KEYRING_SERVICE, probe_key) is not None:
                raise RuntimeError("platform credential-store probe delete failed")
        except Exception:
            # A backend may write and then raise, so cleanup is unconditional.
            try:
                candidate.delete_password(KEYRING_SERVICE, probe_key)
            except Exception:  # noqa: BLE001, S110 -- best-effort probe cleanup
                pass
            _PLATFORM_KEYRING_BACKEND = candidate
            raise
    except Exception:  # noqa: BLE001 -- unavailable OS keyring is expected headlessly
        if keyring_mod is not None and previous_backend is not None:
            try:
                keyring_mod.set_keyring(previous_backend)
            except Exception:  # noqa: BLE001, S110 -- keep best-effort fallback active
                pass
        return False

    _PLATFORM_KEYRING_BACKEND = candidate
    _FILE_BACKEND_ACTIVE = False
    logging.getLogger(__name__).info(
        "OS credential store recovered; future credential saves use the platform keyring."
    )
    return True


def credential_store_backend() -> str:
    """Report which credential store is live: ``platform`` | ``file`` | ``unavailable``.

    ``platform`` means the OS-encrypted store (macOS Keychain / Windows
    Credential Manager / Secret Service) serves reads and writes. ``file``
    means this process degraded to the local 0600 JSON fallback — on macOS
    that is the observable state after the user declined the Keychain prompt.
    The desktop permissions UI maps this onto its Keychain row.
    """
    _ensure_keyring_backend()
    try:
        import keyring

        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 -- no keyring module on this install
        return "unavailable"
    if _is_platform_keyring_backend(backend):
        return "platform"
    if getattr(backend, "_jarvis_file_backend", False):
        return "file"
    return "unavailable"


def try_recover_platform_credential_store() -> bool:
    """User-initiated retry of the OS credential store (macOS Keychain re-prompt).

    Restores the platform keyring backend, then replays the exact read whose
    failure degraded this process to the file fallback. On macOS that replay
    is what makes the Keychain prompt appear again after an earlier "Deny" —
    the restore probe alone touches only a fresh disposable item, which never
    prompts, so without the replay a recovery would be reported that the next
    real read immediately reverts.
    """
    global _LAST_KEYRING_FAILED_SLOT
    if not _try_restore_platform_keyring_backend():
        return False
    slot = _LAST_KEYRING_FAILED_SLOT
    if slot is None:
        return True
    try:
        import keyring

        keyring.get_password(KEYRING_SERVICE, slot)
    except Exception:  # noqa: BLE001 -- the user declined the prompt again
        _install_file_cred_backend("credential-store access declined again")
        return False
    _LAST_KEYRING_FAILED_SLOT = None
    return True


def get_secret(key: str, env_fallback: str | None = None) -> str | None:
    """Retrieve a secret value from every portable credential source.

    Normal priority is OS keyring → ENV → ``.env`` → local-file
    fallback. A fallback value for the same slot is newer than a stale OS copy,
    while ENV and ``.env`` keep their documented precedence. This lets an
    in-app save survive both a temporary keyring outage and the next restart.

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

    # A file value marks a later explicit save whose platform write failed. Read
    # it up front so an older-but-readable OS value cannot shadow it after a
    # restart. A later successful platform save removes this copy.
    file_val: str | None = None
    try:
        file_val = _FileCredStore().get(KEYRING_SERVICE, key)
    except Exception:  # noqa: BLE001, S110 — unreadable fallback is absent
        pass

    platform_val: str | None = None
    # Lazy import — keyring requires pywin32 on Windows
    try:
        import keyring

        active_val = keyring.get_password(KEYRING_SERVICE, key)
        active_backend = keyring.get_keyring()
        if _FILE_BACKEND_ACTIVE and getattr(
            active_backend, "_jarvis_file_backend", False
        ):
            # The process-wide keyring now points at the file backend. Keep
            # reading the retained platform backend for slots that never needed
            # a fallback, so one failed write does not hide all OS credentials.
            file_val = active_val or file_val
            platform_backend = _PLATFORM_KEYRING_BACKEND
            if (
                platform_backend is not None
                and _is_platform_keyring_backend(platform_backend)
            ):
                try:
                    platform_val = platform_backend.get_password(KEYRING_SERVICE, key)
                except Exception:  # noqa: BLE001 — locked platform stays fallback-only
                    platform_val = None
        else:
            platform_val = active_val
    except Exception:  # noqa: BLE001
        # A reachable-but-locked OS keyring raises here even though
        # _ensure_keyring_backend saw a viable backend. Degrade to the 0600 file
        # store and retry once, so a key saved to the file fallback in a prior run
        # is still visible instead of dead-ending on the locked keyring.
        global _LAST_KEYRING_FAILED_SLOT
        _LAST_KEYRING_FAILED_SLOT = key
        if _install_file_cred_backend("keyring read failed"):
            try:
                import keyring

                file_val = keyring.get_password(KEYRING_SERVICE, key) or file_val
            except Exception:  # noqa: BLE001, S110
                pass

    # Preserve OS-keyring-over-ENV precedence when this exact slot has no newer
    # fallback value. When both copies exist, ENV/.env keep their documented
    # precedence and the fallback copy beats only the stale OS copy.
    if platform_val and not file_val:
        return platform_val

    if env_fallback and (val := os.environ.get(env_fallback)):
        return val

    # Development fallback: .env file.
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists() and env_fallback:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == env_fallback:
                return v.strip().strip('"').strip("'")

    if file_val:
        return file_val

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
    overrides = _PROVIDER_SECRET_OVERRIDES.get()
    if overrides is not None and provider in overrides:
        return overrides[provider]
    return get_secret_any(PROVIDER_SECRET_CANDIDATES.get(provider, ()))


def get_jarvis_agent_secret(provider: str) -> str | None:
    """Return the effective API key for one Jarvis-Agent provider.

    A dedicated Agent slot wins. Generic provider credentials are read only as
    an upgrade compatibility fallback; a Realtime-scoped key of the same family
    is the last resort (single-key installs must not brick the Agent tier).
    OAuth-backed Agent providers keep resolving their login separately.
    """
    candidates = JARVIS_AGENT_SECRET_CANDIDATES.get(provider, ())
    if not candidates:
        return None
    dedicated = get_secret_any((candidates[0],))
    if dedicated:
        return dedicated
    return get_provider_secret(provider)


def jarvis_agent_secret_slot(provider: str) -> tuple[str, str] | None:
    """Return the dedicated ``(keyring slot, ENV name)`` for an Agent family."""
    candidates = JARVIS_AGENT_SECRET_CANDIDATES.get(provider, ())
    return candidates[0] if candidates else None


@contextmanager
def override_provider_secrets(
    overrides: Mapping[str, str | None],
) -> Iterator[None]:
    """Task-local provider credentials for an in-process Agent or critic call.

    ``ContextVar`` keeps concurrent workers isolated and avoids mutating process
    environment variables. Normal Brain calls outside the scope are unchanged.
    Team-proxy resolution also stays authoritative because it resolves before
    :func:`get_provider_secret` is consulted.
    """
    token = _PROVIDER_SECRET_OVERRIDES.set(dict(overrides))
    try:
        yield
    finally:
        _PROVIDER_SECRET_OVERRIDES.reset(token)


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


@dataclass(frozen=True)
class _EndpointRoute:
    """Where a provider's traffic goes — the part decided purely by config."""

    base_url: str | None
    via_proxy: bool


#: Config-derived routing per (config identity, provider, vendor default).
#:
#: This is the OTHER half of the freeze whose TOML half ``_TOML_CACHE`` fixed.
#: ``resolve_provider_endpoint`` runs on every provider client build, on the
#: event loop, and a key-aware fallback chain builds several providers per turn.
#: Reaching ``load_config()`` for it rebuilt the whole ``JarvisConfig`` model
#: each time: 281 fresh objects per call, handed straight to the garbage
#: collector. Measured live on 2026-07-28 (two independent sessions, same
#: verdict): the backend thread sat ``active+gil`` inside ``JarvisConfig(**data)``
#: reached through exactly this function, and while it did, the Tk-drawn overlay
#: stopped pumping and Windows replaced the frozen window with a ``Ghost``
#: (BUG-118). Caching the model itself is not an option — a hundred sites mutate
#: the object they are handed — but the routing decision is small, immutable and
#: derived only from the file, so it can be remembered safely.
_ENDPOINT_ROUTE_CACHE: dict[
    tuple[tuple[int, int] | None, str, str | None], _EndpointRoute
] = {}


def _endpoint_route(
    cfg_obj: JarvisConfig,
    provider_id: str,
    vendor_default_base_url: str | None,
) -> _EndpointRoute:
    """Pure routing decision for one provider — no secrets, no I/O."""
    team = cfg_obj.team_proxy
    if team.enabled and team.url and provider_id not in team.local_providers:
        return _EndpointRoute(
            base_url=f"{team.url.rstrip('/')}/p/{provider_id}", via_proxy=True
        )
    prov = cfg_obj.brain.providers.get(provider_id)
    override = prov.base_url if prov is not None and prov.base_url else None
    return _EndpointRoute(base_url=override or vendor_default_base_url, via_proxy=False)


def _cached_endpoint_route(
    provider_id: str,
    vendor_default_base_url: str | None,
) -> _EndpointRoute:
    """The routing decision, without rebuilding the config model to get it.

    Keyed on the config file's identity, so an edit is picked up exactly as it
    was before — and ``clear_config_cache`` drops this alongside the parsed TOML,
    which is what ``config_writer`` announces after every write.
    """
    identity: tuple[int, int] | None = None
    try:
        info = resolve_config_path().stat()
        identity = (info.st_mtime_ns, info.st_size)
    except OSError:
        identity = None

    key = (identity, provider_id, vendor_default_base_url)
    if identity is not None:
        cached = _ENDPOINT_ROUTE_CACHE.get(key)
        if cached is not None:
            return cached

    route = _endpoint_route(load_config(), provider_id, vendor_default_base_url)
    if identity is not None:
        _ENDPOINT_ROUTE_CACHE[key] = route
    return route


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
    if config is not None:
        route = _endpoint_route(config, provider_id, vendor_default_base_url)
    else:
        route = _cached_endpoint_route(provider_id, vendor_default_base_url)

    # The credential is deliberately NOT part of what is remembered above. It
    # comes from the keyring and can be replaced, revoked or repaired while the
    # app runs — a cached one would keep a provider dead after the user fixed
    # its key in the UI, which is the opposite of what this project promises.
    if route.via_proxy:
        token = get_secret("team_proxy_token", "TEAM_PROXY_TOKEN")
        return ResolvedEndpoint(base_url=route.base_url, credential=token, via_proxy=True)
    credential = get_provider_secret(provider_id)
    return ResolvedEndpoint(
        base_url=route.base_url, credential=credential, via_proxy=False
    )


def set_secret(key: str, value: str) -> bool:
    """Store a secret in the OS keyring (or the headless 0600 file fallback).

    Returns True on success. C1: the in-app API-Keys section writes through here,
    so the headless file fallback is what makes a fresh VPS user able to save a key.
    """
    _ensure_keyring_backend()
    # A prior transient failure may have swapped this process to the file
    # backend. Re-detect the OS backend on the user's explicit save so the new
    # value cannot be shadowed by a stale platform entry after restart.
    if _FILE_BACKEND_ACTIVE:
        _try_restore_platform_keyring_backend()
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, key, value)
        # A successful OS-keyring write supersedes any stale file fallback.
        # When the installed backend IS the file fallback, deleting here would
        # erase the value that was just written.
        if not _FILE_BACKEND_ACTIVE:
            store = _FileCredStore()
            try:
                fallback_exists = store.get(KEYRING_SERVICE, key) is not None
            except Exception:  # noqa: BLE001 -- incomplete save must be reported
                return False
            if fallback_exists:
                cleanup_succeeded = False
                try:
                    store.delete(KEYRING_SERVICE, key)
                    cleanup_succeeded = store.get(KEYRING_SERVICE, key) is None
                except Exception:  # noqa: BLE001 -- synchronize below
                    cleanup_succeeded = False
                if not cleanup_succeeded:
                    # A stale fallback outranks the platform copy in
                    # ``get_secret``. If it cannot be removed, make both stores
                    # agree before reporting success; otherwise callers would
                    # immediately read the credential this save replaced.
                    try:
                        store.set(KEYRING_SERVICE, key, value)
                        if store.get(KEYRING_SERVICE, key) != value:
                            return False
                    except Exception:  # noqa: BLE001
                        return False
        _mark_secret_changed(key)
        return True
    except Exception as exc:  # noqa: BLE001
        # A reachable-but-unusable OS keyring (e.g. a locked Linux Secret Service)
        # raises even though _ensure_keyring_backend saw a viable backend. Degrade
        # to the 0600 file store and retry once, so in-app key save never 500s and
        # the credential actually persists (CLAUDE.md §3, recoverable in-app).
        if _install_file_cred_backend(f"keyring write failed: {exc!r}"):
            try:
                import keyring

                keyring.set_password(KEYRING_SERVICE, key, value)
                _mark_secret_changed(key)
                return True
            except Exception:  # noqa: BLE001
                return False
        return False


def delete_secret(key: str) -> bool:
    """Remove a secret from both the OS keyring and local-file fallback.

    Success requires that NO backend still holds the value — deleting only
    the currently active backend can resurrect a stale fallback value on the
    next process start. The operation is intentionally idempotent: an
    already-absent key counts as a successful deletion.

    Unlike ``get_secret``/``set_secret``, a failed OS-keyring delete does NOT
    swap the process-global keyring backend to the file store. That retry
    used to let a transient/locked-keyring failure masquerade as a
    successful delete: the file copy (if any) was removed and ``True`` came
    back, while the real OS-keyring entry survived untouched and reappeared
    on the next boot. The ENV layer is read-only here and is never touched.
    """
    _ensure_keyring_backend()

    # When a platform failure swapped this process onto the file backend, the
    # retained platform store is a second live copy. Delete and verify it first.
    # If that cannot be confirmed, leave the newer file copy intact: removing it
    # would immediately reveal the stale platform value through get_secret().
    retained_backend = _PLATFORM_KEYRING_BACKEND
    if _FILE_BACKEND_ACTIVE and _is_platform_keyring_backend(retained_backend):
        try:
            from keyring.errors import PasswordDeleteError

            try:
                retained_backend.delete_password(KEYRING_SERVICE, key)
            except PasswordDeleteError:
                pass
            if retained_backend.get_password(KEYRING_SERVICE, key) is not None:
                return False
        except Exception:  # noqa: BLE001
            return False

    keyring_ok = False
    try:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except PasswordDeleteError:
            # Every backend we rely on raises this specifically when the
            # entry is already absent (e.g. the Windows Credential Manager
            # backend; the file-fallback backend's delete is idempotent and
            # never raises at all) — "nothing to delete" is itself a success.
            pass
        # Some backends return success without deleting anything. Read the
        # active backend back before removing a newer fallback copy or claiming
        # that the secret is gone.
        keyring_ok = keyring.get_password(KEYRING_SERVICE, key) is None
    except Exception:  # noqa: BLE001
        # A genuine backend failure (locked Secret Service, transport
        # error, ...). Deliberately do not retry via
        # _install_file_cred_backend here: swapping the process-global
        # keyring backend just because one delete failed would silently
        # degrade every other credential read/write in this process while
        # the real OS-keyring entry survives untouched.
        keyring_ok = False

    if not keyring_ok and not _FILE_BACKEND_ACTIVE:
        # The fallback is authoritative when both copies exist. Preserve it
        # until the platform deletion can be confirmed; removing it here would
        # immediately resurface the stale platform credential through
        # ``get_secret`` and make a later retry impossible.
        return False

    file_ok = False
    try:
        store = _FileCredStore()
        if store.get(KEYRING_SERVICE, key) is not None:
            store.delete(KEYRING_SERVICE, key)
        file_ok = store.get(KEYRING_SERVICE, key) is None
    except Exception:  # noqa: BLE001, S110
        pass
    if keyring_ok or file_ok:
        _mark_secret_changed(key)
    return keyring_ok and file_ok


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
    """True when the LEGACY ``.setup-complete`` marker is absent.

    This only reflects the terminal wizard / legacy marker — the in-app
    onboarding records completion in ``setup_state.json`` instead (see
    ``jarvis.setup.state.is_onboarding_complete``). Delegates to
    ``jarvis.setup.state`` so the stdlib-only fast-boot onboarding path and
    this heavy module can never disagree on the marker location.
    """
    from jarvis.setup.state import setup_complete_marker_exists

    return not setup_complete_marker_exists()


def mark_setup_complete() -> None:
    # Read/write/delete of the marker all live in jarvis.setup.state so the
    # location can never desync between callers.
    from jarvis.setup.state import write_setup_complete_marker

    write_setup_complete_marker(f"Setup completed on Python {sys.version.split()[0]}\n")


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
