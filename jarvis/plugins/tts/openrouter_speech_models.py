"""OpenRouter speech-synthesis (TTS) model catalog + filter — pure data.

This module is deliberately dependency-light (no ``httpx``, no ``jarvis.*``
runtime imports) so both the TTS plugin AND ``jarvis.brain.model_catalog`` can
import it cheaply to build the TTS model picker.

Why this exists — the two live-verified OpenRouter facts (2026-07-02):

1. **How to isolate speech models.** OpenRouter's public ``GET /api/v1/models``
   does NOT list the ``/audio/speech`` (TTS) models in its DEFAULT response —
   they are hidden unless you pass the server-side filter
   ``?output_modalities=speech``. That filter returns exactly the speech models,
   each declaring ``architecture.output_modalities == ["speech"]``
   (``architecture.modality == "text->speech"``). So the reliable predicate for a
   TTS model is: ``"speech" in architecture.output_modalities``
   (:func:`is_speech_model`). A chat model outputs ``["text"]``, an image model
   ``["text","image"]``, a music model (Lyria) ``["text","audio"]``, an audio-chat
   model (gpt-audio) ``["text","audio"]`` — none of those are ``"speech"``, so the
   predicate cleanly excludes chat / embedding / STT / image / music / audio-chat.

2. **How to discover a model's voices.** Each speech-model object carries its own
   ``supported_voices`` list (e.g. gemini ships ``["Zephyr","Puck","Charon",...]``,
   grok ships ``["eve","ara","rex","sal","leo"]``). So per-model voice discovery
   is API-provided — :func:`voices_for_model` reads it. :data:`MODEL_VOICES` is a
   curated snapshot of that same data for the offline / synchronous path (the
   ``list_voices`` protocol method is sync and must not do network I/O on the hot
   path). Keep the two in sync; extend a row by pasting a model's
   ``supported_voices`` from the live API.
"""
from __future__ import annotations

from typing import Any

# The output modality that marks a model as a speech synthesiser. This is the
# single load-bearing predicate value (verified live 2026-07-02).
SPEECH_OUTPUT_MODALITY = "speech"

# The server-side filter that surfaces the (otherwise hidden) speech models.
# A catalog fetch MUST include this query param — filtering the default
# /v1/models response finds ZERO speech models.
SPEECH_MODELS_QUERY: dict[str, str] = {"output_modalities": SPEECH_OUTPUT_MODALITY}
SPEECH_MODELS_URL = "https://openrouter.ai/api/v1/models?output_modalities=speech"

# Last-resort default when the plugin is built with NO model. Chosen for cost
# AND fit (verified live 2026-07-02): gemini-3.1-flash-tts is a cheap flash-tier
# model from a major provider (prompt $0.000001/tok), it is multilingual (German
# included — the maintainer's first language), and its voices include "Charon" /
# "Kore" — the exact names the existing ``[tts]`` config already defaults to, so
# switching to OpenRouter with an untouched config does not trigger a voice
# mismatch. NEVER default to an expensive premium voice model.
DEFAULT_MODEL = "google/gemini-3.1-flash-tts-preview"
# Absolute-cheapest English alternative (prompt $0.00000062/tok, free completion)
# — documented so it is easy to switch the default if cost dominates over the
# German/voice-compatibility fit. Not the default (no German voices).
CHEAPEST_MODEL = "hexgrad/kokoro-82m"

# A safe default voice per known model, used when the caller's / config's voice
# is not valid for the selected model. Keys are model ids.
MODEL_DEFAULT_VOICE: dict[str, str] = {
    "google/gemini-3.1-flash-tts-preview": "Charon",
    "x-ai/grok-voice-tts-1.0": "leo",
    "microsoft/mai-voice-2": "en-US-Harper:MAI-Voice-2",
    "mistralai/voxtral-mini-tts-2603": "en_paul_neutral",
    "hexgrad/kokoro-82m": "af_bella",
    "canopylabs/orpheus-3b-0.1-ft": "tara",
    "sesame/csm-1b": "conversational_a",
    "zyphra/zonos-v0.1-transformer": "american_male",
    "zyphra/zonos-v0.1-hybrid": "american_male",
}

# Generic fallback voice when nothing else resolves. "Charon" is the default
# model's default voice and matches the existing [tts] config default.
GENERIC_DEFAULT_VOICE = "Charon"

# Curated snapshot of each speech model's ``supported_voices`` (live 2026-07-02).
# Used by the SYNC ``list_voices`` protocol method (no network on the hot path).
# The live per-model ``supported_voices`` (see :func:`voices_for_model`) is the
# source of truth when a fresh catalog object is available; this mirrors it.
MODEL_VOICES: dict[str, tuple[str, ...]] = {
    "google/gemini-3.1-flash-tts-preview": (
        "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
        "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
        "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
        "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
        "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
    ),
    "x-ai/grok-voice-tts-1.0": ("eve", "ara", "rex", "sal", "leo"),
    "microsoft/mai-voice-2": (
        "en-US-Harper:MAI-Voice-2", "es-MX-Valeria:MAI-Voice-2",
        "fr-FR-Soleil:MAI-Voice-2", "de-DE-Klaus:MAI-Voice-2",
    ),
    "mistralai/voxtral-mini-tts-2603": (
        "en_paul_neutral", "en_paul_happy", "en_paul_sad", "en_paul_excited",
        "en_paul_confident", "en_paul_cheerful", "en_paul_angry",
        "en_paul_frustrated", "gb_oliver_neutral", "gb_oliver_confident",
        "gb_jane_neutral", "gb_jane_confident", "fr_marie_neutral",
        "fr_marie_happy",
    ),
    "hexgrad/kokoro-82m": (
        "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
        "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky", "am_adam",
        "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
        "am_puck", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel",
        "bm_fable", "bm_george", "bm_lewis",
    ),
    "canopylabs/orpheus-3b-0.1-ft": (
        "tara", "leah", "jess", "leo", "dan", "mia", "zac",
    ),
    "sesame/csm-1b": (
        "conversational_a", "conversational_b", "read_speech_a",
        "read_speech_b", "read_speech_c", "read_speech_d",
    ),
    "zyphra/zonos-v0.1-transformer": (
        "american_female", "american_male", "british_female", "british_male",
        "random",
    ),
    "zyphra/zonos-v0.1-hybrid": (
        "american_female", "american_male", "british_female", "british_male",
        "random",
    ),
}

# Curated ``(id, label)`` snapshot of the speech models (live 2026-07-02),
# ordered cheap-first with the recommended default leading. This backs the model
# picker offline / when the live ``?output_modalities=speech`` fetch is
# unreachable — mirroring how ``CURATED_MODELS`` backs the brain picker.
CURATED_SPEECH_MODELS: tuple[tuple[str, str], ...] = (
    ("google/gemini-3.1-flash-tts-preview", "Google: Gemini 3.1 Flash TTS (multilingual)"),
    ("hexgrad/kokoro-82m", "hexgrad: Kokoro 82M (cheapest, English)"),
    ("x-ai/grok-voice-tts-1.0", "xAI: Grok Voice TTS 1.0"),
    ("microsoft/mai-voice-2", "Microsoft: MAI-Voice-2"),
    ("mistralai/voxtral-mini-tts-2603", "Mistral: Voxtral Mini TTS"),
    ("canopylabs/orpheus-3b-0.1-ft", "Canopy Labs: Orpheus 3B"),
    ("sesame/csm-1b", "Sesame: CSM 1B"),
    ("zyphra/zonos-v0.1-transformer", "Zyphra: Zonos v0.1 Transformer"),
    ("zyphra/zonos-v0.1-hybrid", "Zyphra: Zonos v0.1 Hybrid"),
)


# Every model id we KNOW is an OpenRouter speech model (curated snapshot above).
KNOWN_SPEECH_MODEL_IDS: frozenset[str] = frozenset(
    mid for mid, _ in CURATED_SPEECH_MODELS
) | frozenset(MODEL_VOICES)


def coerce_speech_model(model_id: str | None) -> str:
    """Return a usable OpenRouter speech-model id for ``model_id``.

    Guards against a FOREIGN model id left in the shared ``[tts].model`` config
    after switching TTS providers. The ``[tts]`` block has a single global
    ``model`` shared across every TTS provider, so when the user flips to
    OpenRouter the field can still hold another provider's value (e.g. Cartesia's
    ``sonic-2`` or Groq's ``whisper-large-v3``) — OpenRouter then 400s with
    "Model sonic-2 does not exist". Resolution:

    * empty                     → the default model,
    * a known speech model      → itself,
    * unknown but OpenRouter-shaped (contains ``/`` — every OpenRouter id is
      ``vendor/model``)          → itself (trust a NEW speech model we don't list
      yet, rather than block it),
    * unknown single-token id    → the default model (a foreign provider's id
      like ``sonic-2``; OpenRouter ids always carry a ``/``).
    """
    mid = (model_id or "").strip()
    if not mid:
        return DEFAULT_MODEL
    if mid in KNOWN_SPEECH_MODEL_IDS:
        return mid
    if "/" in mid:
        return mid
    return DEFAULT_MODEL


def _architecture(model_obj: Any) -> dict[str, Any]:
    """Best-effort extract the ``architecture`` mapping from a raw model object."""
    if isinstance(model_obj, dict):
        arch = model_obj.get("architecture")
        if isinstance(arch, dict):
            return arch
    return {}


def is_speech_model(model_obj: Any) -> bool:
    """True iff ``model_obj`` is an OpenRouter text-to-speech model.

    The verified predicate: ``"speech" in architecture.output_modalities``. This
    excludes chat (``["text"]``), image (``["text","image"]``), music/audio-chat
    (``["text","audio"]``), embedding and STT models, which never declare
    ``"speech"`` output. Robust to a missing / malformed ``architecture`` (returns
    False), so it is safe to run over an arbitrary ``/v1/models`` payload.
    """
    arch = _architecture(model_obj)
    mods = arch.get("output_modalities")
    if isinstance(mods, (list, tuple)):
        return SPEECH_OUTPUT_MODALITY in mods
    # Fallback: some payloads only carry the combined ``modality`` string.
    modality = arch.get("modality")
    if isinstance(modality, str):
        return modality.split("->")[-1].strip() == SPEECH_OUTPUT_MODALITY
    return False


def filter_tts_models(models: list[Any]) -> list[Any]:
    """Keep only speech-synthesis models from a raw ``/v1/models`` ``data`` list.

    NOTE: OpenRouter hides speech models from the DEFAULT ``/v1/models`` response;
    fetch with ``?output_modalities=speech`` (:data:`SPEECH_MODELS_URL`) so there
    is anything to keep. This filter is the second, in-process guard that
    guarantees no non-speech model slips into the TTS picker regardless of how the
    list was fetched.
    """
    return [m for m in models if is_speech_model(m)]


def voices_for_model(model_obj: Any) -> list[str]:
    """The voices valid for ``model_obj``, read from its live ``supported_voices``.

    Falls back to the curated :data:`MODEL_VOICES` snapshot (by id) and finally to
    an empty list, so a caller always gets the CORRECT voices for the selected
    model when the live object carries them.
    """
    if isinstance(model_obj, dict):
        voices = model_obj.get("supported_voices")
        if isinstance(voices, (list, tuple)) and voices:
            return [str(v) for v in voices]
        mid = str(model_obj.get("id") or "").strip()
        if mid in MODEL_VOICES:
            return list(MODEL_VOICES[mid])
    return []


def voices_for_model_id(model_id: str | None) -> list[str]:
    """Curated voices for a model id (sync, offline). Empty when unknown."""
    if not model_id:
        return []
    return list(MODEL_VOICES.get(model_id.strip(), ()))
