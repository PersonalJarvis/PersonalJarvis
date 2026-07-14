"""Per-provider model catalog — the live model list behind the API-Keys model
picker.

Where :mod:`jarvis.brain.frontier_resolver` queries a provider's ``/v1/models``
endpoint and distils it down to the single frontier pick per tier, this module
returns the *whole* catalog so the desktop UI can offer a searchable dropdown.
The two share the same upstream endpoints but answer different questions
(``frontier_resolver`` = "what is the newest model?"; ``model_catalog`` = "what
are all of them, so the user can pick one?").

Design goals (maintainer mandate 2026-06-20):
- **Always current.** The list comes from the provider's own catalog, so a model
  the provider published an hour ago appears without any code change here. There
  is no hand-maintained frontier list on the hot path — only a small ``static``
  fallback for the offline/no-key case, honestly labelled as such.
- **OpenRouter included.** Its catalog has hundreds of models, which is exactly
  why the UI needs search; this module just hands over the full list.
- **Honest source.** Every result carries ``source`` ∈ {``live``, ``cache``,
  ``static``} so the UI never pretends a stale fallback is the live catalog.

Cache: ``data/model_catalog_cache.json``, default TTL 6 h (shorter than the
frontier resolver's 24 h — fresher is better for a list the user browses), with
``force_refresh`` to bypass it on an explicit "refresh" click. OpenRouter is a
special case: its public catalog changes daily, so its cache lifetime is capped
at five minutes even when the general TTL is longer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from jarvis.core import config as cfg

log = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 6

# OpenRouter publishes model drops much more frequently than the direct
# providers. A six-hour cache made a valid new model look absent even though the
# public ``/models`` response already contained it. Cap only this volatile,
# unauthenticated catalog; callers can still use ``force_refresh`` for an
# immediate fetch, and an explicitly shorter global TTL continues to win.
_PROVIDER_TTL_CAP_SECONDS: dict[str, float] = {
    "openrouter": 5 * 60,
}

# The API-key brain providers whose catalogs we can enumerate. Codex is excluded
# on purpose: it authenticates via the ChatGPT login / a generic OpenAI key and
# its model id is largely ignored by the ``codex exec`` CLI path — it has no own
# model picker in the UI (it renders the Codex login widget instead).
CATALOG_PROVIDERS: tuple[str, ...] = (
    "claude-api",
    "openai",
    "gemini",
    "openrouter",
    "grok",
    "nvidia",
)

# Endpoint + auth shape per provider. ``auth`` selects how the key is attached:
#   "x-api-key"  → Anthropic header pair
#   "bearer"     → Authorization: Bearer (OpenAI-compatible: OpenAI, Grok)
#   "query"      → ?key= (Gemini)
#   "bearer_opt" → Authorization: Bearer if a key exists, else anonymous
#                  (OpenRouter's catalog is public)
_ENDPOINTS: dict[str, tuple[str, str]] = {
    "claude-api": ("https://api.anthropic.com/v1/models", "x-api-key"),
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models",
        "query",
    ),
    "openrouter": ("https://openrouter.ai/api/v1/models", "bearer_opt"),
    # xAI uses the OpenAI-compatible ``data[].id`` model roster and requires
    # the same bearer key used for Grok inference.
    "grok": ("https://api.x.ai/v1/models", "bearer"),
    # NVIDIA NIM speaks the OpenAI-compatible ``data[].id`` shape. Its catalog is
    # PUBLIC (verified 2026-07-08: an unauthenticated GET returns the full model
    # list), so ``bearer_opt`` like OpenRouter — the picker fills in before a key
    # is entered, and the key is attached when present.
    "nvidia": ("https://integrate.api.nvidia.com/v1/models", "bearer_opt"),
}


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One selectable model: the wire ``id`` plus a human ``label``.

    ``output_modalities`` carries the provider's declared output kinds (e.g.
    ``("text",)`` or ``("text", "image")``) when available — OpenRouter returns
    it under ``architecture.output_modalities``; direct provider ``/v1/models``
    endpoints do not, so it stays ``None`` there. A tuple (not a list) keeps the
    dataclass frozen/hashable. Used by :func:`filter_brain_models` to exclude
    image/audio/video GENERATION models that a name-substring blocklist misses
    (e.g. ``openrouter/auto``)."""

    id: str
    label: str
    output_modalities: tuple[str, ...] | None = None
    # H4/H5: per-model capability hints from OpenRouter's /v1/models
    # (``architecture.input_modalities`` + ``supported_parameters``). ``None`` when
    # the provider endpoint doesn't expose them → callers default to "capable" (no
    # regression). ``"image" in input_modalities`` ⇒ vision; ``"tools" in
    # supported_parameters`` ⇒ tool-calling.
    input_modalities: tuple[str, ...] | None = None
    supported_parameters: tuple[str, ...] | None = None


def _curated(pairs: list[tuple[str, str]]) -> list[ModelInfo]:
    return [ModelInfo(id=i, label=lbl) for i, lbl in pairs]


# Curated current model families per provider — the picker's fallback when the
# live ``/v1/models`` catalog is unreachable (no/invalid key, network down). This
# is what makes the dropdown useful for providers the user drives WITHOUT an API
# key: Claude in particular runs via the Max subscription (OAuth), so its live
# fetch always 401s — the user still expects to pick Fable / Opus / Sonnet /
# Haiku. Keep these to the *current* frontier families (maintainer mandate: never
# offer a years-old model); when a valid key exists the live catalog supersedes
# this entirely, so a new release still shows up automatically there.
CURATED_MODELS: dict[str, list[ModelInfo]] = {
    "claude-api": _curated([
        ("claude-fable-5", "Claude Fable 5"),
        ("claude-opus-4-8", "Claude Opus 4.8"),
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ]),
    "openai": _curated([
        ("gpt-5.6-sol", "GPT-5.6 Sol (preview)"),
        ("gpt-5.6-terra", "GPT-5.6 Terra (preview)"),
        ("gpt-5.6-luna", "GPT-5.6 Luna (preview)"),
        ("gpt-5.6", "GPT-5.6 (Sol alias, preview)"),
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.5-pro", "GPT-5.5 Pro"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-pro", "GPT-5.4 Pro"),
        ("gpt-5.4-mini", "GPT-5.4 Mini"),
        ("gpt-5.4-nano", "GPT-5.4 Nano"),
    ]),
    "gemini": _curated([
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
        ("gemini-3-flash-preview", "Gemini 3 Flash"),
        ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("gemini-flash-lite-latest", "Gemini Flash Lite"),
    ]),
    "openrouter": _curated([
        ("anthropic/claude-fable-5", "Claude Fable 5"),
        ("anthropic/claude-opus-4.8", "Claude Opus 4.8"),
        ("anthropic/claude-sonnet-5", "Claude Sonnet 5"),
        ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5"),
        ("openai/gpt-5.6-sol-pro", "GPT-5.6 Sol Pro"),
        ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
        ("openai/gpt-5.6-terra-pro", "GPT-5.6 Terra Pro"),
        ("openai/gpt-5.6-terra", "GPT-5.6 Terra"),
        ("openai/gpt-5.6-luna-pro", "GPT-5.6 Luna Pro"),
        ("openai/gpt-5.6-luna", "GPT-5.6 Luna"),
        ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
        ("x-ai/grok-4.20", "Grok 4.20"),
        ("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
    ]),
    # Grok 4.3 is the universal default because Grok 4.5 is not yet available
    # in every region. The authenticated live catalog replaces this fallback.
    "grok": _curated([
        ("grok-4.3", "Grok 4.3"),
        ("grok-4.5", "Grok 4.5"),
    ]),
    # NVIDIA NIM — the offline fallback when the live /v1/models catalog is
    # unreachable. NVIDIA-hosted current families (Nemotron leads: it is NVIDIA's
    # own). A valid key supersedes this with the live list, so a newly hosted
    # model still shows up automatically.
    "nvidia": _curated([
        ("nvidia/llama-3.1-nemotron-ultra-253b-v1", "Nemotron Ultra 253B"),
        ("nvidia/llama-3.3-nemotron-super-49b-v1.5", "Nemotron Super 49B v1.5"),
        ("deepseek-ai/deepseek-v4-pro", "DeepSeek V4 Pro"),
        ("deepseek-ai/deepseek-v4-flash", "DeepSeek V4 Flash"),
        ("moonshotai/kimi-k2.6", "Kimi K2.6"),
        ("z-ai/glm-5.2", "GLM-5.2"),
        ("qwen/qwen3.5-397b-a17b", "Qwen3.5 397B A17B"),
        ("meta/llama-4-maverick-17b-128e-instruct", "Llama 4 Maverick"),
        ("mistralai/mistral-large-3-675b-instruct-2512", "Mistral Large 3"),
    ]),
}


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """The model list for one provider, with an honest provenance flag."""

    provider: str
    models: tuple[ModelInfo, ...]
    source: str  # "live" | "cache" | "static" | "curated"
    fetched_at: float
    selects: str = "model"  # what the picker writes: "model" | "voice"


def _ids(ids: list[str]) -> list[ModelInfo]:
    return [ModelInfo(id=i, label=i) for i in ids]


# TTS catalogs — for most TTS providers the user-facing pick is the VOICE
# (Gemini Charon/Kore, Grok leo/rex, OpenAI alloy/nova, Google Neural2 names);
# Cartesia's meaningful pick is its MODEL (sonic-3.5). The ``[tts]`` config is a
# single block (voice_de/voice_en/model), so the picker only renders on the
# ACTIVE TTS card and sets the global value.
TTS_CATALOG: dict[str, tuple[str, list[ModelInfo]]] = {
    # ElevenLabs picks a VOICE ID (opaque hashes), so the curated list carries
    # human names as labels while the value stays the id. The picker's
    # "use custom" row lets a user paste their OWN voice id (e.g. a cloned
    # voice) instead of a curated one — kept in sync with DEFAULT_VOICES in
    # jarvis/plugins/tts/elevenlabs_tts.py.
    # Inworld — the new premium default (arena-#1 realtime, mid-2026). Voices are
    # multilingual; these native masculine de/en/es voices are the curated pick,
    # kept in sync with DEFAULT_VOICE_* in jarvis/plugins/tts/inworld_tts.py.
    "inworld": ("voice", _curated([
        ("Josef", "Josef — German, calm assistant (default)"),
        ("Johanna", "Johanna — German, warm"),
        ("Dennis", "Dennis — English, deep narrator"),
        ("Ashley", "Ashley — English, bright"),
        ("Diego", "Diego — Spanish, formal"),
        ("Lupita", "Lupita — Spanish, warm"),
    ])),
    "elevenlabs": ("voice", _curated([
        ("onwK4e9ZLuTAKqWW03F9", "Daniel — British, authoritative (default)"),
        ("JBFqnCBsd6RMkjVDRZzb", "George — British, deep narrator"),
        ("IKne3meq5aSn9XLyUdCD", "Charlie — British, mature butler"),
        ("nPczCjzI2devNBz1zQrb", "Brian — American, deep narrator"),
        ("pNInz6obpgDQGcFmaJgB", "Adam — American, classic AI voice"),
    ])),
    "gemini-flash-tts": ("voice", _ids([
        "Charon", "Kore", "Orus", "Iapetus", "Rasalgethi", "Algenib",
        "Algieba", "Fenrir", "Aoede", "Zephyr", "Puck", "Leda",
        "Callirrhoe", "Autonoe", "Enceladus", "Umbriel", "Despina",
        "Erinome", "Laomedeia", "Achernar", "Alnilam", "Schedar",
        "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix",
        "Sadachbia", "Sadaltager", "Sulafat",
    ])),
    "grok-voice": ("voice", _ids([
        "leo", "rex", "sal", "ara", "eve", "carina", "zagan", "helix",
        "orion", "luna", "iris", "altair", "zenith", "perseus", "helios",
        "lux", "kepler", "rigel", "cosmo", "celeste", "ursa", "sirius",
        "lumen", "castor", "naksh", "atlas",
    ])),
    "openai-tts": ("voice", _ids([
        "alloy", "ash", "ballad", "coral", "echo",
        "fable", "onyx", "nova", "sage", "shimmer", "verse", "marin", "cedar",
    ])),
    "google-neural2": ("voice", _ids([
        "en-US-Neural2-A", "en-US-Neural2-C", "en-US-Neural2-D", "en-US-Neural2-F",
        "de-DE-Neural2-B", "de-DE-Neural2-C", "de-DE-Neural2-D", "de-DE-Neural2-F",
    ])),
    "cartesia": ("model", _curated([
        ("sonic-3.5", "Sonic 3.5 (stable)"),
        ("sonic-3", "Sonic 3"),
        ("sonic-3-latest", "Sonic 3 latest (preview track)"),
    ])),
    # OpenRouter TTS (the last-resort gateway) — the model picker offers ONLY the
    # four allowlisted, production-grade speech models. The five open-source slop
    # models (Kokoro, Orpheus, CSM-1B, both Zonos) are UNLISTED per the hard
    # allowlist (jarvis/plugins/tts/curated_catalog.py) — they fail the premium
    # multilingual bar on de/es coverage, beta stability, or GPU dependence. Every
    # id here must satisfy curated_catalog.is_allowed("openrouter", id) — guarded
    # by tests/unit/plugins/tts/test_openrouter_curation.py.
    "openrouter-tts": ("model", _ids([
        "google/gemini-3.1-flash-tts-preview",
        "x-ai/grok-voice-tts-1.0",
        "microsoft/mai-voice-2",
        "mistralai/voxtral-mini-tts-2603",
    ])),
}

# Realtime catalogs — REALTIME_MODELS + REALTIME_VOICES, keyed by realtime
# provider id (``openai-realtime`` / ``gemini-live``). Realtime needs BOTH a
# model AND a voice selection per provider (unlike every other picker, which
# serves ONE selection), so these two dicts are looked up directly by the
# dedicated ``GET/PUT /providers/{id}/realtime-options`` endpoints rather than
# being registered into ``PROVIDER_CATALOG``/``catalog_spec`` (that machinery
# is built around a single ``selects: "model" | "voice"`` per provider).
# Curated, not live-fetched: no realtime provider exposes a ``/v1/models``
# endpoint reachable the same way as the text-brain catalogs, and a curated
# list is realtime-only by construction (never leaks a non-realtime model into
# the picker). The currently-hardcoded adapter default is always FIRST in each
# model list — the safe fallback an unset pick resolves to.
#
# openai-realtime — verified 2026-07-10 against the official Realtime model
# guide and endpoint-support table. ``gpt-realtime`` remains the adapter default
# (matches ``_MODEL`` in ``jarvis/plugins/realtime/openai_realtime.py``), while
# 2.1/2.1-mini, 2, 1.5, and mini remain selectable general voice-agent models.
# ``gpt-realtime-translate``/``gpt-realtime-whisper`` are deliberately excluded:
# they target dedicated translation/transcription sessions, not the general
# duplex voice-agent protocol implemented by this adapter.
REALTIME_MODELS: dict[str, list[ModelInfo]] = {
    "openai-realtime": _curated([
        ("gpt-realtime", "GPT Realtime (default)"),
        ("gpt-realtime-2.1", "GPT Realtime 2.1"),
        ("gpt-realtime-2.1-mini", "GPT Realtime 2.1 Mini"),
        ("gpt-realtime-2", "GPT Realtime 2"),
        ("gpt-realtime-1.5", "GPT Realtime 1.5"),
        ("gpt-realtime-mini", "GPT Realtime Mini"),
    ]),
    # gemini-live — verified 2026-07-10 against ai.google.dev/gemini-api/docs/models
    # (the Live API model list). ``gemini-3.1-flash-live-preview`` is the current
    # flagship (matches ``_MODEL`` in ``jarvis/plugins/realtime/gemini_live.py``);
    # ``gemini-2.5-flash-native-audio-preview-12-2025`` is the current 2.5-series
    # native-audio sibling still listed on that page. The older
    # ``gemini-2.0-flash-live-preview`` family is marked for shutdown — omitted.
    "gemini-live": _curated([
        ("gemini-3.1-flash-live-preview", "Gemini 3.1 Flash Live (default)"),
        (
            "gemini-2.5-flash-native-audio-latest",
            "Gemini 2.5 Flash Native Audio (latest alias)",
        ),
        (
            "gemini-2.5-flash-native-audio-preview-12-2025",
            "Gemini 2.5 Flash Native Audio",
        ),
    ]),
}

# Realtime voice catalogs — stable prebuilt-voice names (curated, not live).
# openai-realtime: verified 2026-07-10 against the official Realtime
# conversations guide — ten current voices, including Marin and Cedar.
# gemini-live: verified 2026-07-10 against the Live API capabilities guide,
# which now permits the complete 30-voice Gemini prebuilt roster.
REALTIME_VOICES: dict[str, list[ModelInfo]] = {
    "openai-realtime": _ids([
        "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse",
        "marin", "cedar",
    ]),
    "gemini-live": _ids([
        "Puck", "Charon", "Kore", "Fenrir", "Aoede", "Orus", "Leda", "Zephyr",
        "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
        "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
        "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
        "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
    ]),
}


# STT model catalogs (the ``[stt] model`` is a single global value).
STT_CATALOG: dict[str, list[ModelInfo]] = {
    "groq-api": _ids(["whisper-large-v3", "whisper-large-v3-turbo"]),
    # "faster-whisper" (local) was removed as a user-selectable STT provider in
    # v1.0.1 — see the note in provider_spec.py. The wake-word local Whisper and
    # the key-free STT fallback do NOT use this catalog (they read [stt].wake_*
    # / construct FasterWhisperProvider directly), so no wake/fallback breakage.
    "openai-api": _ids([
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "gpt-4o-mini-transcribe-2025-12-15",
        "gpt-4o-transcribe-diarize",
        "whisper-1",
    ]),
    "deepgram": _ids(["nova-3", "nova-2", "nova-2-general", "enhanced", "base"]),
    # OpenRouter STT — the model picker offers ONLY transcription models. This
    # curated snapshot mirrors the live `?output_modalities=transcription` list
    # (verified 2026-07-02); audio-in chat models are excluded. The default
    # model (openai/whisper-large-v3) is listed first.
    "openrouter-stt": _ids([
        "openai/whisper-large-v3",
        "openai/gpt-4o-transcribe",
        "openai/gpt-4o-mini-transcribe",
        "openai/whisper-1",
        "openai/whisper-large-v3-turbo",
        "google/chirp-3",
        "mistralai/voxtral-mini-transcribe",
        "qwen/qwen3-asr-flash-2026-02-10",
        "nvidia/parakeet-tdt-0.6b-v3",
        "microsoft/mai-transcribe-1.5",
    ]),
}


@dataclass(frozen=True, slots=True)
class CatalogSpec:
    """Per-provider picker spec: which tier, what it selects, the curated list,
    and whether a live ``/v1/models`` fetch is available (brain providers only)."""

    tier: str       # "brain" | "tts" | "stt"
    selects: str    # "model" | "voice"
    curated: tuple[ModelInfo, ...]
    live: bool


def _build_provider_catalog() -> dict[str, CatalogSpec]:
    cat: dict[str, CatalogSpec] = {}
    # The live-fetchable API brains (CATALOG_PROVIDERS). Codex + antigravity are
    # added below as curated-only — no /v1/models over their OAuth logins.
    for p in CATALOG_PROVIDERS:
        cat[p] = CatalogSpec("brain", "model", tuple(CURATED_MODELS.get(p, ())), live=True)
    # Codex — Jarvis-Agent model catalog for the ChatGPT-login worker; no
    # /v1/models over OAuth, so curated only. The concrete GPT-5.6 choices are
    # the current Codex lineup; the still-supported GPT-5.5/5.4 choices remain
    # available for users who intentionally prefer their established behavior.
    cat["codex"] = CatalogSpec("brain", "model", tuple(_curated([
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-mini", "GPT-5.4 Mini"),
    ])), live=False)
    # Antigravity — subagent model catalog for the official agy/gemini CLI
    # (OAuth login); no /v1/models over OAuth, so curated only. Flash first =
    # the fast default; Pro is the deep option. Both are valid gemini-CLI ids.
    # NOTE (verified live 2026-06-21, agy 1.0.10): the agy CLI IGNORES the chosen
    # model — neither ``--model`` nor ``settings.json model.name`` overrides it
    # (a bogus name still answers), so agy always runs its IDE-configured default.
    # The selection here therefore only takes effect on the gemini-CLI fallback
    # path (and any future agy that honors the flag); for agy it is informational.
    cat["antigravity"] = CatalogSpec("brain", "model", tuple(_curated([
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
    ])), live=False)
    for p, (selects, opts) in TTS_CATALOG.items():
        cat[p] = CatalogSpec("tts", selects, tuple(opts), live=False)
    for p, opts in STT_CATALOG.items():
        cat[p] = CatalogSpec("stt", "model", tuple(opts), live=False)
    return cat


PROVIDER_CATALOG: dict[str, CatalogSpec] = _build_provider_catalog()


def catalog_spec(provider: str) -> CatalogSpec | None:
    """The picker spec for ``provider`` (None if it has no catalog)."""
    return PROVIDER_CATALOG.get(provider)


# ----------------------------------------------------------------------
# Pure parsing + sorting (module-level for easy testing)
# ----------------------------------------------------------------------

def parse_models_response(provider: str, payload: dict) -> list[ModelInfo]:
    """Map a provider's ``/v1/models`` JSON to a flat ``list[ModelInfo]``.

    Anthropic / OpenAI / Grok share the OpenAI-compatible ``data[].id`` shape.
    Gemini lists ``models[].name`` (``models/<id>``) with an optional
    ``displayName``. OpenRouter adds a human ``name`` we surface as the label and
    an ``architecture.output_modalities`` we carry through for the brain filter.
    Entries without a usable id are dropped.
    """
    out: list[ModelInfo] = []
    if provider == "gemini":
        for m in payload.get("models", []) or []:
            raw = (m.get("name") or "").removeprefix("models/").strip()
            if not raw:
                continue
            label = (m.get("displayName") or "").strip() or raw
            out.append(ModelInfo(id=raw, label=label, output_modalities=_output_modalities(m)))
        return out

    # OpenAI-compatible shape (OpenAI / Anthropic / Grok / OpenRouter).
    for m in payload.get("data", []) or []:
        raw = (m.get("id") or "").strip()
        if not raw:
            continue
        label = (m.get("name") or "").strip() or raw  # OpenRouter has a name
        out.append(ModelInfo(
            id=raw,
            label=label,
            output_modalities=_output_modalities(m),
            input_modalities=_input_modalities(m),
            supported_parameters=_supported_parameters(m),
        ))
    return out


def _input_modalities(entry: dict) -> tuple[str, ...] | None:
    """``architecture.input_modalities`` (e.g. ``("text","image")``) or None."""
    arch = entry.get("architecture")
    if not isinstance(arch, dict):
        return None
    mods = arch.get("input_modalities")
    if not isinstance(mods, list):
        return None
    return tuple(str(x) for x in mods)


def _supported_parameters(entry: dict) -> tuple[str, ...] | None:
    """OpenRouter's top-level ``supported_parameters`` (includes ``"tools"`` when
    the model can tool-call) or None when the endpoint doesn't expose it."""
    params = entry.get("supported_parameters")
    if not isinstance(params, list):
        return None
    return tuple(str(x) for x in params)


def model_capabilities(provider: str, model_id: str) -> dict[str, bool | None]:
    """Per-model ``{vision, tools}`` hints, read SYNCHRONOUSLY from the cached
    ``/v1/models`` data (``data/model_catalog_cache.json``).

    ``None`` for a field means "unknown" — the caller defaults to capable, so there
    is NO regression for providers/models that don't expose the data. Used by the
    OpenRouter brain (H4/H5): a text-only or non-tool model the user picked degrades
    honestly (delegate / skip Computer-Use) instead of 400-ing the provider.
    """
    from jarvis.core import config as _cfg

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    mid = (model_id or "").strip()
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        for m in data.get(provider, {}).get("models", []):
            if m.get("id") == mid:
                inp = m.get("input_modalities")
                params = m.get("supported_parameters")
                return {
                    "vision": ("image" in inp) if isinstance(inp, list) else None,
                    "tools": ("tools" in params) if isinstance(params, list) else None,
                }
    except Exception:  # noqa: BLE001 — missing/corrupt cache → unknown (capable)
        pass
    return {"vision": None, "tools": None}


def pick_vision_model(provider: str) -> str | None:
    """The best vision-capable brain model of ``provider``, from the cached
    ``/v1/models`` catalog — or ``None`` when the catalog carries no modality
    data for this provider (direct provider endpoints) or no candidate exists.

    Computer-Use is screenshot-grounded: a provider whose CONFIGURED model
    cannot see images must not drop out of the CU chain when the same key
    unlocks vision-capable siblings (AP-22 — the key is fine, only the model
    choice is blind). Candidates run through the SAME brain filter + relevance
    sort as the picker, so the rescue pick equals the top row the user would
    see in the vision-filtered dropdown.
    """
    from jarvis.core import config as _cfg  # noqa: PLC0415

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entries = data.get(provider, {}).get("models", [])
    except Exception:  # noqa: BLE001 — missing/corrupt cache → no rescue
        return None
    candidates: list[ModelInfo] = []
    for m in entries:
        mid = str(m.get("id") or "").strip()
        inp = m.get("input_modalities")
        if not mid or not (isinstance(inp, list) and "image" in inp):
            continue
        out_mods = m.get("output_modalities")
        params = m.get("supported_parameters")
        candidates.append(ModelInfo(
            id=mid,
            label=str(m.get("label") or mid),
            output_modalities=tuple(out_mods) if isinstance(out_mods, list) else None,
            input_modalities=tuple(str(x) for x in inp),
            supported_parameters=tuple(params) if isinstance(params, list) else None,
        ))
    usable = sort_models(provider, filter_brain_models(candidates))
    return usable[0].id if usable else None


#: Name markers of the FAST model class (low-latency siblings). Computer-Use
#: issues one vision call per step, so step latency — not peak intelligence —
#: dominates mission wall-clock (live forensic 2026-07-02: think=60.8s of a
#: 75.8s mission on a flagship model). Data, not logic; extend by adding a row.
_FAST_CLASS_MARKERS: tuple[str, ...] = (
    "flash", "haiku", "mini", "lite", "turbo", "air", "nano",
)


def provider_has_modality_data(provider: str) -> bool:
    """True when the cached catalog carries ``input_modalities`` for at least
    one of ``provider``'s models — i.e. a "no vision model found" verdict is
    an informed NO, not missing data. Direct provider endpoints (gemini /
    claude-api / openai) expose no modality metadata and return False."""
    from jarvis.core import config as _cfg  # noqa: PLC0415

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return any(
            isinstance(m.get("input_modalities"), list)
            for m in data.get(provider, {}).get("models", [])
        )
    except Exception:  # noqa: BLE001
        return False


def is_fast_class_model(model_id: str | None) -> bool:
    """True when the model id names a low-latency sibling (fast class).

    Markers match WHOLE id tokens (split on non-alphanumerics), never raw
    substrings — "mini" as a substring matches every "ge**mini**" model and
    classified Gemini PRO as fast (live bug 2026-07-02).
    """
    if not model_id:
        return False
    tokens = set(re.split(r"[^a-z0-9]+", model_id.lower()))
    return any(mark in tokens for mark in _FAST_CLASS_MARKERS)


def pick_fast_vision_model(provider: str) -> str | None:
    """The best FAST vision-capable model of ``provider`` (or ``None``).

    Same candidate set as :func:`pick_vision_model` (vision input + the brain
    filter), but the fast class leads: a "flash"/"haiku"/"mini"-style sibling
    of a known family beats the flagship. Within each band the picker's
    relevance sort decides. Falls back to the plain vision pick when the
    provider has no fast vision sibling.
    """
    from jarvis.core import config as _cfg  # noqa: PLC0415

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entries = data.get(provider, {}).get("models", [])
    except Exception:  # noqa: BLE001 — missing/corrupt cache → no pick
        return None
    candidates: list[ModelInfo] = []
    for m in entries:
        mid = str(m.get("id") or "").strip()
        inp = m.get("input_modalities")
        if not mid or not (isinstance(inp, list) and "image" in inp):
            continue
        out_mods = m.get("output_modalities")
        params = m.get("supported_parameters")
        candidates.append(ModelInfo(
            id=mid,
            label=str(m.get("label") or mid),
            output_modalities=tuple(out_mods) if isinstance(out_mods, list) else None,
            input_modalities=tuple(str(x) for x in inp),
            supported_parameters=tuple(params) if isinstance(params, list) else None,
        ))
    usable = sort_models(provider, filter_brain_models(candidates))
    if not usable:
        return None
    # Fast class first — but only KNOWN families (family rank > 0), so an
    # obscure "-mini" of an unknown vendor never beats a Gemini Flash / Haiku.
    for m in usable:
        if _family_rank(m.id) > 0 and is_fast_class_model(m.id):
            return m.id
    return usable[0].id


def _output_modalities(entry: dict) -> tuple[str, ...] | None:
    """Pull ``architecture.output_modalities`` from a model entry as a tuple.

    Returns ``None`` when the field is absent (direct provider ``/v1/models``
    endpoints don't return ``architecture``) so the filter knows to fall back to
    the substring blocklist rather than treat "no data" as "no output".
    """
    arch = entry.get("architecture")
    if not isinstance(arch, dict):
        return None
    mods = arch.get("output_modalities")
    if not isinstance(mods, list):
        return None
    return tuple(str(x) for x in mods)


# Substrings (case-insensitive on the id) that mark a model as NOT a usable
# chat/reasoning brain: generative-media (video/image/music), audio I/O, speech,
# embeddings, moderation/safety classifiers. These can never back the brain (the
# probe would 404/400), and showing them in a brain picker is pure noise — the
# Gemini catalog in particular front-loads Veo/Imagen/Lyria/Nano-Banana. A truly
# exotic model is still reachable via the free-text custom entry.
_NON_BRAIN_MARKERS: tuple[str, ...] = (
    "veo", "imagen", "lyria", "nano-banana", "dall-e", "dalle", "sora",
    "whisper", "transcrib", "speech", "tts", "-audio", "audio-",
    "embed", "image", "moderation", "rerank", "realtime", "-live", "guard",
)


# Output modalities that disqualify a model as a chat/reasoning brain — a model
# that GENERATES image/audio/video can't back the text brain (the probe 400s and
# it is pure noise in the picker). NOTE: this is about OUTPUT only; a model that
# ACCEPTS image INPUT (vision) but outputs text is a valid — even required —
# brain (Computer-Use needs vision-input), so input modalities are NOT filtered.
_GENERATION_OUTPUT_MODALITIES: frozenset[str] = frozenset({
    "image", "audio", "video", "music", "speech",
})


def filter_brain_models(models: list[ModelInfo]) -> list[ModelInfo]:
    """Keep only models that can plausibly serve as a chat/reasoning brain.

    BOTH checks always apply (a model is dropped if EITHER fires):

    1. **Name blocklist** (:data:`_NON_BRAIN_MARKERS`) — drops embedding / rerank
       / moderation / safety-classifier (e.g. ``llama-guard``) and known media
       families. These OUTPUT text yet are not chat brains, so modality alone
       can't catch them — the name signal must always run.
    2. **Output modality** (:data:`_GENERATION_OUTPUT_MODALITIES`, when declared)
       — drops anything that GENERATES image/audio/video/music. Robust where the
       name blocklist isn't: e.g. ``openrouter/auto`` outputs text OR image yet
       has no marker in its id. Absent on direct provider ``/v1/models`` (no
       ``architecture`` field) → check 1 carries those.

    Vision-INPUT models (image in, text out) are KEPT — Computer-Use needs them;
    only OUTPUT modalities gate. Capability-based, never provider-name-based
    (AP-21). The UI's free-text entry still reaches anything dropped.
    """
    out: list[ModelInfo] = []
    for m in models:
        # 1. Name blocklist — always, even when text is the output (classifiers).
        low = m.id.lower()
        if any(mark in low for mark in _NON_BRAIN_MARKERS):
            continue
        # 2. Output-modality exclusion when the provider declares it.
        mods = m.output_modalities
        if mods is not None and any(x in _GENERATION_OUTPUT_MODALITIES for x in mods):
            continue
        out.append(m)
    return out


def _is_stale(provider: str, model_id: str) -> bool:
    """True if ``model_id`` is an end-of-life model we demote in the list.

    Reuses ``frontier_resolver.STALE_MODELS`` (the maintained EOL set). For
    OpenRouter the id is namespaced (``openai/gpt-4o``); we test the part after
    the slash against the same set.
    """
    from jarvis.brain.frontier_resolver import STALE_MODELS

    if model_id in STALE_MODELS:
        return True
    if "/" in model_id and model_id.rsplit("/", 1)[-1] in STALE_MODELS:
        return True
    return False


# Family relevance ranking — PRESENTATION ORDER ONLY (never gates behavior,
# AP-21; the analogue of provider_spec's ``recommended`` badge). Higher rank =
# listed first. Without this, OpenRouter's namespaced ids (``z-ai/…``,
# ``qwen/…``, ``sao10k/…``) sort reverse-alphabetically and bury the flagship
# families below the 80-row display cap — the exact "I search GPT 5.5 and get
# nothing, only obscure models show" report (2026-06-28).
#
# Two bands, mirroring the user mandate "the best models people really use —
# top performance AND bang-per-token":
#   30s — flagship frontier families everyone reaches for first.
#   20s — very popular, strong price/performance ("value") families.
#   10s — older-but-mainstream known families.
# Matched as a lowercase substring of the id; the FIRST hit wins, so entries are
# ordered most- to least-specific. Unknown families (incl. community fine-tunes
# like sao10k/undi95/thedrummer) get rank 0 and sink below every known family
# but stay above stale/EOL models. The free-text custom entry still reaches
# anything, so this is a sensible default order, not a hard curation.
_FAMILY_RANK: tuple[tuple[str, int], ...] = (
    # Flagship frontier
    ("claude-opus", 39), ("claude-fable", 39), ("claude-sonnet", 38),
    ("gpt-5", 37), ("gemini-3", 36), ("grok-4", 35), ("claude-haiku", 33),
    # Popular / strong value
    ("deepseek", 29), ("kimi", 28), ("glm-5", 27), ("qwen3", 26),
    ("qwen-3", 26), ("glm-4", 25), ("llama-4", 24), ("mistral-large", 23),
    ("mistral-medium", 22), ("gpt-oss", 21), ("command-r", 20),
    # Older-but-mainstream
    ("gpt-4", 15), ("gemini-2", 14), ("grok-3", 13), ("llama-3", 12),
    ("mistral", 11), ("qwen", 10), ("glm", 10), ("command", 9),
)


def _family_rank(model_id: str) -> int:
    """Presentation-only relevance band for ``model_id`` (higher = first)."""
    low = model_id.lower()
    # OpenAI o-series (``o3``/``o4``): bare id for the direct provider,
    # namespaced (``openai/o3``) on OpenRouter. Matched on the tail's prefix to
    # avoid an "o3" substring false-positive elsewhere in the id.
    tail = low.rsplit("/", 1)[-1]
    if tail.startswith(("o3", "o4")):
        return 34
    for needle, rank in _FAMILY_RANK:
        if needle in low:
            return rank
    return 0


# ----------------------------------------------------------------------
# Presentation-only model classification — the picker's filter chips + star
# ----------------------------------------------------------------------
# These tag a model for the API-Keys picker's "Free / Frontier / Best value"
# chips and the maintainer's star. They are PRESENTATION ONLY (AP-21): a tag
# never changes which model is pinned, how it is gated, or what the brain does.
# The two quality bands REUSE ``_family_rank`` (the one source of truth for list
# order) so the chips can never drift from the ordering — Frontier is the
# flagship band, Best value the strong price/performance band.

# Rank floors that split ``_family_rank``'s band into the two quality chips.
# Frontier ≥ 33 = flagship families (Opus/Fable/Sonnet/GPT-5/Gemini-3/Grok-4/
# o3-o4/Haiku); 20 ≤ value < 33 = popular price/performance families (DeepSeek/
# Kimi/GLM/Qwen/Llama-4/Mistral-large.../gpt-oss/command-r).
FRONTIER_RANK_FLOOR = 33
VALUE_RANK_FLOOR = 20


def _squash(text: str) -> str:
    """Lowercase ``text`` stripped of every non-alphanumeric character.

    Mirrors the picker's separator-insensitive search so a star pattern matches
    an id regardless of vendor-prefix punctuation: ``anthropic/claude-opus-4.8``
    and the direct ``claude-opus-4-8`` both squash to ``claudeopus48``.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Maintainer's hand-picked "best models" — they get a star in the picker. Matched
# on the SQUASHED id tail (vendor prefix + a ``:free``/``:nitro`` variant suffix
# stripped) so the same pick is starred whether it comes from a direct provider
# (``claude-opus-4-8``) or OpenRouter (``anthropic/claude-opus-4.8``). Extend
# freely — this is a curated favourites list, presentation only.
STARRED_MODELS: frozenset[str] = frozenset({
    _squash("claude-opus-4.8"),       # Opus 4.8
    _squash("claude-opus-4.8-fast"),  # Opus 4.8 (Fast)
    _squash("gpt-5.6"),               # GPT-5.6 alias (direct OpenAI)
    _squash("gpt-5.6-sol"),           # GPT-5.6 flagship (OpenAI/OpenRouter)
    _squash("gpt-5.5"),               # GPT-5.5
    _squash("gemini-3.5-flash"),      # Gemini 3.5 Flash
    _squash("claude-fable-5"),        # Fable 5
    _squash("glm-5.2"),               # GLM-5.2
})


def is_free_model(model_id: str, label: str = "") -> bool:
    """True for a zero-cost model. OpenRouter marks these with a ``:free`` id
    suffix and a ``(free)`` label; both are checked so the flag survives whichever
    signal a future catalog keeps."""
    return ":free" in model_id.lower() or "(free)" in label.lower()


def is_starred_model(model_id: str) -> bool:
    """True if ``model_id`` is one of the maintainer's starred picks."""
    tail = model_id.lower().rsplit("/", 1)[-1]
    tail = tail.split(":", 1)[0]  # drop a ``:free``/``:nitro`` variant suffix
    return _squash(tail) in STARRED_MODELS


@dataclass(frozen=True, slots=True)
class ModelTags:
    """Presentation-only classification of one model for the picker's filters.

    Four independent booleans (a model can be both ``value`` and ``free``), so
    there is no enum to drift across the Python↔TS boundary — just flags the UI
    renders as chips/stars. None of them gate behavior (AP-21)."""

    free: bool
    frontier: bool
    value: bool
    starred: bool


def classify_model(model_id: str, label: str = "") -> ModelTags:
    """Tag a model for the picker's filter chips + star, REUSING the family
    relevance ranking so the chips never drift from the displayed list order."""
    rank = _family_rank(model_id)
    return ModelTags(
        free=is_free_model(model_id, label),
        frontier=rank >= FRONTIER_RANK_FLOOR,
        value=VALUE_RANK_FLOOR <= rank < FRONTIER_RANK_FLOOR,
        starred=is_starred_model(model_id),
    )


# Variant markers that demote a model WITHIN its family — smaller/cheaper or
# special-purpose siblings rarely wanted as the default pick. The full flagship
# (incl. ``-pro``) is NOT demoted.
_SPECIALIZED_MARKERS: tuple[str, ...] = (
    "-mini", "-nano", "-lite", ":free", "-codex", "-chat",
    "-search", "-air", "-flash-lite",
    # Special-purpose siblings — not the default chat brain, so they rank below
    # the plain model of the same family. (Image/audio variants are already
    # dropped upstream by filter_brain_models, so they need no marker here.)
    "-deep-research", "-multi-agent", "-customtools",
)


def _is_specialized(model_id: str) -> bool:
    """True for a smaller/special sibling that should sit below the flagship."""
    low = model_id.lower()
    if any(mark in low for mark in _SPECIALIZED_MARKERS):
        return True
    # Dated snapshot (``-2024-08-06`` / ``-20260423``) → demote vs. the clean
    # alias, mirroring frontier_resolver's clean-over-dated preference.
    return bool(re.search(r"-\d{4}-\d{2}-\d{2}$", low) or re.search(r"-\d{6,}$", low))


def _version_key(model_id: str) -> tuple[int, ...]:
    """Numeric version tuple from the id tail (newer sorts higher).

    ``openai/gpt-5.5`` → ``(5, 5)``, ``z-ai/glm-4.5v`` → ``(4, 5)``. Date-like
    runs of 6+ digits are dropped so a snapshot can't beat a semantic version.
    """
    tail = model_id.lower().rsplit("/", 1)[-1]
    return tuple(int(n) for n in re.findall(r"\d+", tail) if len(n) < 6)


def _model_line(model_id: str) -> str:
    """Group sibling models of the same product line (different versions).

    Strips the version numbers and normalises separators, so
    ``anthropic/claude-opus-4.8`` and ``…-4.7`` share the line
    ``anthropic-claude-opus`` — while distinct product tiers keep their tier
    word and stay separate (``google/gemini-3.5-flash`` → ``google-gemini-flash``
    vs ``google/gemini-3.1-pro-preview`` → ``google-gemini-pro-preview``).
    """
    no_ver = re.sub(r"\d+(?:\.\d+)*", " ", model_id.lower())
    return re.sub(r"[^a-z]+", "-", no_ver).strip("-")


def sort_models(provider: str, models: list[ModelInfo]) -> list[ModelInfo]:
    """Order by relevance: known frontier/value families first, newest version
    first within a family, smaller/special variants and EOL models last.

    Sort key (all compared ``reverse=True`` so "more" wins):
      1. non-stale before stale (EOL models always at the very bottom);
      2. family flagship before the rest (:func:`_model_line` pre-pass) — only
         the NEWEST non-special model of each known product line leads, so the
         top rows show different providers' current flagships instead of one
         provider's whole back-catalogue (e.g. Opus 4.7/4.6/4.5 no longer wall
         off GPT-5.5);
      3. main variant before mini/nano/free/dated (:func:`_is_specialized`);
      4. family relevance band (:func:`_family_rank` — flagship > value > known
         > unknown), so GPT/Claude/Gemini/Grok and the popular value families
         (DeepSeek/GLM/Qwen/Kimi) lead instead of whatever vendor prefix sorts
         highest alphabetically;
      5. newer version first (:func:`_version_key`);
      6. id, as a stable final tiebreaker.

    Search is still the real discovery tool for the long tail (esp. OpenRouter),
    so this is a sensible default order, not a hard curation.
    """
    # Pre-pass: the newest version per product line, among non-stale / non-
    # special / known-family models. A model that matches its line's newest
    # version is the "flagship" and rides the top band; older same-line versions
    # fall to the second band.
    best_version: dict[str, tuple[int, ...]] = {}
    for m in models:
        if _is_stale(provider, m.id) or _is_specialized(m.id) or _family_rank(m.id) == 0:
            continue
        line = _model_line(m.id)
        ver = _version_key(m.id)
        if ver > best_version.get(line, ()):
            best_version[line] = ver

    def _is_flagship(m: ModelInfo) -> bool:
        if _is_stale(provider, m.id) or _is_specialized(m.id) or _family_rank(m.id) == 0:
            return False
        return _version_key(m.id) == best_version.get(_model_line(m.id))

    return sorted(
        models,
        key=lambda m: (
            not _is_stale(provider, m.id),
            _is_flagship(m),
            not _is_specialized(m.id),
            _family_rank(m.id),
            _version_key(m.id),
            m.id,
        ),
        reverse=True,
    )


# ----------------------------------------------------------------------
# Catalog with cache + live fetch + static fallback
# ----------------------------------------------------------------------

class ModelCatalog:
    """Live model lists per provider with a TTL cache and honest fallbacks."""

    def __init__(
        self,
        cache_path: Path | None = None,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        http_client_factory: object | None = None,
    ) -> None:
        # Anchor the cache to the app data dir (PROJECT_ROOT/data), not the CWD,
        # so a headless Linux VPS started from any directory still caches.
        self._cache_path = cache_path or (cfg.DATA_DIR / "model_catalog_cache.json")
        self._ttl_seconds = ttl_hours * 3600
        # provider -> (fetched_at, models)
        self._cache: dict[str, tuple[float, list[ModelInfo]]] = {}
        self._lock = asyncio.Lock()
        self._client_factory = http_client_factory
        self._load_cache()

    # -- cache I/O -----------------------------------------------------

    def _load_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for prov, entry in data.items():
                models = [
                    ModelInfo(
                        id=m["id"],
                        label=m.get("label") or m["id"],
                        # Preserve modality so the brain filter stays consistent
                        # on a cache hit (else openrouter/auto would slip back in).
                        output_modalities=(
                            tuple(m["output_modalities"])
                            if isinstance(m.get("output_modalities"), list)
                            else None
                        ),
                        input_modalities=(
                            tuple(m["input_modalities"])
                            if isinstance(m.get("input_modalities"), list)
                            else None
                        ),
                        supported_parameters=(
                            tuple(m["supported_parameters"])
                            if isinstance(m.get("supported_parameters"), list)
                            else None
                        ),
                    )
                    for m in entry.get("models", [])
                ]
                self._cache[prov] = (float(entry.get("fetched_at", 0.0)), models)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("model_catalog_cache.json corrupt — discarded: %s", exc)
            self._cache.clear()

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            prov: {
                "fetched_at": ts,
                "models": [
                    {
                        "id": m.id,
                        "label": m.label,
                        **(
                            {"output_modalities": list(m.output_modalities)}
                            if m.output_modalities is not None
                            else {}
                        ),
                        **(
                            {"input_modalities": list(m.input_modalities)}
                            if m.input_modalities is not None
                            else {}
                        ),
                        **(
                            {"supported_parameters": list(m.supported_parameters)}
                            if m.supported_parameters is not None
                            else {}
                        ),
                    }
                    for m in models
                ],
            }
            for prov, (ts, models) in self._cache.items()
        }
        self._cache_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )

    def _is_fresh(self, provider: str, fetched_at: float) -> bool:
        ttl_seconds = min(
            self._ttl_seconds,
            _PROVIDER_TTL_CAP_SECONDS.get(provider, self._ttl_seconds),
        )
        return (time.time() - fetched_at) < ttl_seconds

    @staticmethod
    def _present(provider: str, models: list[ModelInfo]) -> tuple[ModelInfo, ...]:
        """Filter to brain-usable models, then order newest/frontier first."""
        return tuple(sort_models(provider, filter_brain_models(models)))

    # -- public API ----------------------------------------------------

    async def list_models(
        self, provider: str, *, force_refresh: bool = False
    ) -> CatalogResult:
        """Return the catalog for ``provider`` with an honest ``source`` flag.

        Brain providers with a live endpoint: fresh cache → ``cache`` (no
        network); else fetch → ``live`` (cache updated); fetch failure → stale
        cache or the curated ``static`` fallback. TTS/STT providers (and Codex)
        have no ``/v1/models`` endpoint → the curated catalog (``curated``). The
        ``selects`` field tells the UI whether it picks a model or a voice.
        """
        spec = catalog_spec(provider)
        if spec is None:
            return CatalogResult(provider, (), "static", 0.0, "model")

        # TTS / STT / Codex: curated list only (no live endpoint). Returned as-is
        # (no brain-model filtering/sorting — voices and STT models are not brain
        # models and must keep their curated order).
        if not spec.live:
            return CatalogResult(
                provider=provider,
                models=tuple(spec.curated),
                source="curated",
                fetched_at=0.0,
                selects=spec.selects,
            )

        async with self._lock:
            cached = self._cache.get(provider)
            if cached and not force_refresh and self._is_fresh(provider, cached[0]):
                return CatalogResult(
                    provider, self._present(provider, cached[1]), "cache", cached[0], "model",
                )

            try:
                models = await self._fetch_raw(provider)
            except Exception as exc:  # noqa: BLE001 — a UI list must never crash the page.
                log.info("Model catalog fetch for %s failed: %s", provider, exc)
                if cached:
                    return CatalogResult(
                        provider, self._present(provider, cached[1]), "cache", cached[0], "model",
                    )
                static = self._static_fallback(provider)
                return CatalogResult(
                    provider, self._present(provider, static), "static", 0.0, "model",
                )

            now = time.time()
            self._cache[provider] = (now, models)
            self._save_cache()
            return CatalogResult(
                provider, self._present(provider, models), "live", now, "model",
            )

    # -- network -------------------------------------------------------

    async def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()  # type: ignore[operator]
        return httpx.AsyncClient(timeout=12.0)

    async def _fetch_raw(self, provider: str) -> list[ModelInfo]:
        """Call ``provider``'s catalog endpoint and parse it. Raises on no key
        (except OpenRouter, whose catalog is public) or a transport/HTTP error."""
        if provider not in _ENDPOINTS:
            raise ValueError(f"Unsupported provider: {provider}")
        url, auth = _ENDPOINTS[provider]
        key = cfg.get_provider_secret(provider)
        if not key and auth != "bearer_opt":
            raise RuntimeError(f"No API key configured for {provider}.")

        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        if auth == "x-api-key":
            headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01"}
        elif auth == "bearer":
            headers = {"Authorization": f"Bearer {key}"}
        elif auth == "bearer_opt":
            if key:
                headers = {"Authorization": f"Bearer {key}"}
        elif auth == "query":
            params = {"key": key or ""}

        client = await self._client()
        async with client:
            resp = await client.get(url, headers=headers, params=params)
            # ``bearer_opt`` endpoints are public. A stale/revoked optional key
            # must not hide the live model roster from the picker: retry once
            # anonymously on an authentication/permission rejection. Inference
            # still uses and validates the credential normally; this fallback is
            # scoped only to public catalog discovery.
            if auth == "bearer_opt" and headers and resp.status_code in (401, 403):
                log.info(
                    "Optional credential rejected by the public %s model catalog; "
                    "retrying anonymously.",
                    provider,
                )
                resp = await client.get(url, params=params)
            resp.raise_for_status()
            return parse_models_response(provider, resp.json())

    # -- static fallback ----------------------------------------------

    def _static_fallback(self, provider: str) -> list[ModelInfo]:
        """The curated current model family for ``provider``.

        Used when the live catalog is unreachable AND there is no cache — so the
        picker still offers a full, useful selection (esp. Claude via Max, whose
        live fetch always 401s). Falls back to the maintained tier defaults for
        any provider not in :data:`CURATED_MODELS`.
        """
        curated = CURATED_MODELS.get(provider)
        if curated:
            return list(curated)
        try:
            from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER
        except Exception:  # noqa: BLE001
            return []
        seen: dict[str, ModelInfo] = {}
        for tier in ("router", "deep"):
            mid = TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(provider)
            if mid:
                seen.setdefault(mid, ModelInfo(id=mid, label=mid))
        return list(seen.values())
