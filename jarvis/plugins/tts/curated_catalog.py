"""Curated TTS catalog — the single source of truth for the hard allowlist.

Only models/voices listed here as ``status="allowed"`` are ever selectable.
The aggregated OpenRouter speech list and the UI voice picker are filtered
through :func:`is_allowed` / :func:`allowed_openrouter_model_ids`, so
low-quality "slop" models simply never reach the selectable surface — they are
*unlisted*, not deleted (the raw data survives in
``openrouter_speech_models.py`` for provenance).

Design: docs/superpowers/specs/2026-07-07-tts-quality-curation-design.md §3.1.

Dependency-light on purpose (no ``httpx``, no ``jarvis.*`` runtime imports),
mirroring ``openrouter_speech_models.py`` so any layer can import it cheaply.

Quality bar (mid-2026 research): S = top realtime tier (Inworld, Cartesia,
Gemini 3.1 Flash), A = premium alternatives (ElevenLabs, Grok). Latency class
and language coverage feed the ranking + fallback order; ``last_eval`` scores
(WER / naturalness / drift / TTFA) are attached out-of-band by the eval suite
(§3.6) — this module holds the static, human-curated allowlist.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Sentinel for a language-agnostic voice (one voice speaks any language) — the
# personality-named voices (Gemini "Charon", Grok "leo") are all multilingual.
MULTILINGUAL = "multi"

# Voice-profile genders. Only confidently documented voices are tagged; an
# unknown voice maps to "" so every consumer degrades to today's behavior
# (fail-safe: no profile → no cross-provider voice matching).
MASCULINE = "m"
FEMININE = "f"

# Curated gender register across ALL families (single flat map — voice ids are
# unique across the vendors we ship). Sources: Google speech-generation voice
# docs (30 Gemini voices), xAI TTS guide (the five classic Grok voices — the
# 21 newer ones stay untagged until documented), Inworld voice roster, the
# five curated ElevenLabs prebuilt voices (ids), OpenAI gpt-realtime (cedar/
# marin — realtime-only, referenced for cross-path continuity), and the
# OpenRouter speech-model default voices we curate.
_VOICE_GENDERS: dict[str, str] = {
    # Gemini / google TTS + Live (masculine)
    "Puck": MASCULINE, "Charon": MASCULINE, "Fenrir": MASCULINE,
    "Orus": MASCULINE, "Enceladus": MASCULINE, "Iapetus": MASCULINE,
    "Umbriel": MASCULINE, "Algieba": MASCULINE, "Algenib": MASCULINE,
    "Rasalgethi": MASCULINE, "Alnilam": MASCULINE, "Schedar": MASCULINE,
    "Achird": MASCULINE, "Zubenelgenubi": MASCULINE, "Sadachbia": MASCULINE,
    "Sadaltager": MASCULINE,
    # Gemini / google TTS + Live (feminine)
    "Zephyr": FEMININE, "Kore": FEMININE, "Leda": FEMININE, "Aoede": FEMININE,
    "Callirrhoe": FEMININE, "Autonoe": FEMININE, "Despina": FEMININE,
    "Erinome": FEMININE, "Laomedeia": FEMININE, "Achernar": FEMININE,
    "Gacrux": FEMININE, "Pulcherrima": FEMININE, "Vindemiatrix": FEMININE,
    "Sulafat": FEMININE,
    # Grok Voice (xAI) — classic roster only
    "leo": MASCULINE, "rex": MASCULINE, "sal": MASCULINE,
    "ara": FEMININE, "eve": FEMININE,
    # Inworld
    "Josef": MASCULINE, "Dennis": MASCULINE, "Diego": MASCULINE,
    "Johanna": FEMININE, "Ashley": FEMININE, "Lupita": FEMININE,
    # ElevenLabs curated prebuilt voices (ids; all masculine)
    "onwK4e9ZLuTAKqWW03F9": MASCULINE,  # Daniel
    "JBFqnCBsd6RMkjVDRZzb": MASCULINE,  # George
    "IKne3meq5aSn9XLyUdCD": MASCULINE,  # Charlie
    "nPczCjzI2devNBz1zQrb": MASCULINE,  # Brian
    "pNInz6obpgDQGcFmaJgB": MASCULINE,  # Adam
    # OpenAI gpt-realtime (realtime path; for cross-path profile continuity)
    "cedar": MASCULINE, "marin": FEMININE,
    # OpenRouter curated speech-model defaults outside the vendor families
    "af_bella": FEMININE,          # Kokoro ("af" = adult female roster)
    "tara": FEMININE,              # Orpheus default
    "en_paul_neutral": MASCULINE,  # Voxtral default
}


def voice_gender(voice_id: str | None) -> str | None:
    """Gender of a known voice id (:data:`MASCULINE`/:data:`FEMININE`), else
    ``None``. Case-insensitive fallback covers ad-hoc user spellings."""
    vid = (voice_id or "").strip()
    if not vid:
        return None
    g = _VOICE_GENDERS.get(vid)
    if g:
        return g
    lowered = vid.lower()
    for known, gender in _VOICE_GENDERS.items():
        if known.lower() == lowered:
            return gender
    return None


@dataclass(frozen=True)
class VoiceEntry:
    """One vetted voice: its id, the ISO-639-1 language it speaks (or
    :data:`MULTILINGUAL` for a language-agnostic voice), and its curated
    gender ("m"/"f", or "" when not confidently documented)."""

    id: str
    language: str = MULTILINGUAL
    gender: str = ""


def _v(voice_id: str, language: str = MULTILINGUAL) -> VoiceEntry:
    """Catalog constructor that auto-attaches the curated gender tag."""
    return VoiceEntry(voice_id, language, _VOICE_GENDERS.get(voice_id, ""))


@dataclass(frozen=True)
class ModelEntry:
    """One vetted model on the allowlist.

    ``languages`` lists the first-class languages (de/en/es) the model covers
    well — the premium models cover far more, but these three are what Jarvis
    guarantees. ``status`` is ``"allowed"`` (selectable), ``"provisional"``
    (integrated but not yet eval-passed, hidden from the default picker), or
    ``"unlisted"`` (kept for provenance, never selectable).
    """

    family: str  # canonical provider family (matches _canonical_tts_name)
    model_id: str
    quality_tier: str  # "S" | "A"
    languages: tuple[str, ...]
    latency_class: str  # "realtime" | "standard" | "batch"
    streaming: bool
    voices: tuple[VoiceEntry, ...] = field(default_factory=tuple)
    status: str = "allowed"


# The curated, human-justified allowlist. Native premium families first, then
# the four vetted OpenRouter speech models (OpenRouter is the last-resort
# gateway — see the fallback order in jarvis/plugins/tts/__init__.py). The five
# OpenRouter open-source models (Kokoro, Orpheus, CSM-1B, both Zonos) are
# deliberately ABSENT: they fail the premium multilingual bar (weak de/es,
# beta stability, or GPU-bound) and are therefore unlisted.
_CATALOG: tuple[ModelEntry, ...] = (
    # ---- Native premium families -------------------------------------------
    # Inworld — the mid-2026 arena-#1 realtime model and the recommended premium
    # default. Voices are multilingual (one voice speaks any of 15 languages via
    # the per-turn `language` field); we curate native masculine de/en/es voices
    # plus a couple of alternates. Quality is corroborated by the market
    # research; the eval suite is the ongoing guarantee, not a precondition.
    ModelEntry(
        family="inworld",
        model_id="inworld-tts-2",
        quality_tier="S",
        languages=("de", "en", "es"),
        latency_class="realtime",
        streaming=True,
        voices=(
            _v("Josef", "de"),
            _v("Johanna", "de"),
            _v("Dennis", "en"),
            _v("Ashley", "en"),
            _v("Diego", "es"),
            _v("Lupita", "es"),
        ),
    ),
    ModelEntry(
        family="cartesia",
        model_id="sonic-3.5",
        quality_tier="S",
        languages=("de", "en", "es"),
        latency_class="realtime",
        streaming=True,
    ),
    ModelEntry(
        family="gemini-flash-tts",
        model_id="gemini-3.1-flash-tts-preview",
        quality_tier="S",
        languages=("de", "en", "es"),
        latency_class="standard",  # ~300-500 ms, chunked (no true WS streaming)
        streaming=True,
        voices=tuple(
            _v(v)
            for v in (
                "Charon", "Kore", "Orus", "Iapetus", "Rasalgethi", "Algenib",
                "Algieba", "Fenrir", "Aoede", "Zephyr", "Puck", "Leda",
                "Callirrhoe", "Autonoe", "Enceladus", "Umbriel", "Despina",
                "Erinome", "Laomedeia", "Achernar", "Alnilam", "Schedar",
                "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
                "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
            )
        ),
    ),
    ModelEntry(
        family="elevenlabs",
        model_id="eleven_flash_v2_5",
        quality_tier="A",
        languages=("de", "en", "es"),
        latency_class="realtime",
        streaming=True,
        voices=(
            _v("onwK4e9ZLuTAKqWW03F9"),  # Daniel
            _v("JBFqnCBsd6RMkjVDRZzb"),  # George
            _v("IKne3meq5aSn9XLyUdCD"),  # Charlie
            _v("nPczCjzI2devNBz1zQrb"),  # Brian
            _v("pNInz6obpgDQGcFmaJgB"),  # Adam
        ),
    ),
    ModelEntry(
        family="grok-voice",
        model_id="grok-voice-tts-1.0",
        quality_tier="A",
        languages=("de", "en", "es"),
        latency_class="realtime",
        streaming=True,
        voices=tuple(
            _v(v)
            for v in (
                "leo", "rex", "sal", "ara", "eve", "carina", "zagan", "helix",
                "orion", "luna", "iris", "altair", "zenith", "perseus",
                "helios", "lux", "kepler", "rigel", "cosmo", "celeste", "ursa",
                "sirius", "lumen", "castor", "naksh", "atlas",
            )
        ),
    ),
    # ---- OpenRouter (last-resort gateway) — only the four vetted KEEP ids ----
    ModelEntry(
        family="openrouter",
        model_id="google/gemini-3.1-flash-tts-preview",
        quality_tier="S",
        languages=("de", "en", "es"),
        latency_class="standard",
        streaming=True,
    ),
    ModelEntry(
        family="openrouter",
        model_id="x-ai/grok-voice-tts-1.0",
        quality_tier="A",
        languages=("de", "en", "es"),
        latency_class="realtime",
        streaming=True,
    ),
    ModelEntry(
        family="openrouter",
        model_id="microsoft/mai-voice-2",
        quality_tier="A",
        languages=("de", "en", "es"),
        latency_class="standard",
        streaming=True,
    ),
    # Voxtral mini: KEEP but provisional until the eval confirms the mini
    # variant's naturalness matches the full model (design §7). Still allowed
    # so it stays selectable; the eval can demote it to "provisional".
    ModelEntry(
        family="openrouter",
        model_id="mistralai/voxtral-mini-tts-2603",
        quality_tier="A",
        languages=("de", "en", "es"),
        latency_class="standard",
        streaming=True,
    ),
)


def allowed_models(
    family: str | None = None, language: str | None = None
) -> list[ModelEntry]:
    """Every ``allowed`` model, optionally scoped to a family and/or language.

    ``language`` is matched against the model's first-class ``languages`` (a
    two-letter code; a BCP-47 tag is truncated). A language-agnostic model
    (``MULTILINGUAL`` in its languages) matches any language.
    """
    short = (language or "").lower().split("-", 1)[0]
    out: list[ModelEntry] = []
    for m in _CATALOG:
        if m.status != "allowed":
            continue
        if family is not None and m.family != family:
            continue
        if short and short not in m.languages and MULTILINGUAL not in m.languages:
            continue
        out.append(m)
    return out


def _find(family: str, model_id: str) -> ModelEntry | None:
    for m in _CATALOG:
        if m.family == family and m.model_id == model_id:
            return m
    return None


def is_allowed(family: str, model_id: str, voice: str | None = None) -> bool:
    """Fail-closed: True only when ``(family, model_id)`` is an ``allowed``
    catalog entry. When ``voice`` is given and the entry curates voices, the
    voice must be one of them (entries with no curated voice list accept any
    voice — the model validates its own)."""
    m = _find(family, model_id)
    if m is None or m.status != "allowed":
        return False
    if voice is None or not m.voices:
        return True
    return any(v.id == voice for v in m.voices)


def allowed_voices(
    family: str, model_id: str, language: str | None = None
) -> list[VoiceEntry]:
    """Curated voices for an allowed model, optionally narrowed to a language.
    Empty when the model is not allowed or curates no voices here (the caller
    then falls back to the model's own ``list_voices``, filtered by this)."""
    m = _find(family, model_id)
    if m is None or m.status != "allowed":
        return []
    short = (language or "").lower().split("-", 1)[0]
    if not short:
        return list(m.voices)
    return [v for v in m.voices if v.language in (short, MULTILINGUAL)]


# OpenRouter serves other vendors' speech models; map the vendor prefix to the
# native catalog family so voice-profile continuity works through the gateway.
_OPENROUTER_VENDOR_FAMILY: dict[str, str] = {
    "google/": "gemini-flash-tts",
    "x-ai/": "grok-voice",
}


def continuity_voice(
    family: str, gender: str, model_id: str | None = None
) -> str | None:
    """The curated voice of ``family`` that continues a given voice profile.

    Used when a DIFFERENT provider has to take over mid-conversation (provider
    fallback, cross-family key crossing, realtime→surface handover): instead of
    the taking-over family's arbitrary default voice — which audibly flips e.g.
    masculine→feminine and reads as "Jarvis suddenly changed voice" — the
    caller asks for the first curated voice matching the active profile.

    Only language-agnostic (multilingual) voices qualify: a fallback voice is
    pinned once per wrapper and must be able to speak whatever language the
    turn resolves to. Returns ``None`` when the family curates no matching
    voice (caller keeps today's provider-default behavior).
    """
    fam = (family or "").strip().lower()
    if fam == "openrouter":
        mid = model_id or ""
        fam = next(
            (
                mapped
                for prefix, mapped in _OPENROUTER_VENDOR_FAMILY.items()
                if mid.startswith(prefix)
            ),
            "",
        )
        if not fam:
            return None
    for m in _CATALOG:
        if m.family != fam or m.status != "allowed":
            continue
        for v in m.voices:
            if v.gender == gender and v.language == MULTILINGUAL:
                return v.id
    return None


def allowed_openrouter_model_ids(model_ids: list[str]) -> list[str]:
    """Filter a raw OpenRouter speech-model id list down to the allowed ones,
    preserving input order. This is the boundary that drops the slop models
    from the aggregated ``?output_modalities=speech`` catalog."""
    allowed = {
        m.model_id
        for m in _CATALOG
        if m.family == "openrouter" and m.status == "allowed"
    }
    return [mid for mid in model_ids if mid in allowed]
