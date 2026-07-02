"""TTS provider plugins (Google Gemini, ElevenLabs, xAI Grok, ...).

`build_tts_from_config` is the central factory for all call sites
(desktop app, speech-pipeline CLI). Only this way does switching TTS via
`jarvis.toml` stay a config edit rather than a code edit.

SAPI5 (Windows-native, robotic-sounding TTS) has been an **opt-in**
emergency exit only since 2026-04-25: by default the provider would
rather stay silent than switch to the Windows voice. Set
`tts.allow_sapi5_fallback = true` if you want audio output no matter what.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("jarvis.tts.factory")

# Voices belonging to each respective provider — prevents e.g. a
# Gemini voice ("Charon") from landing in the Grok plugin and triggering HTTP 400.
_GEMINI_VOICES = frozenset({
    "Charon", "Orus", "Iapetus", "Rasalgethi", "Algenib",
    "Algieba", "Kore", "Fenrir", "Aoede",
})
_GROK_VOICES = frozenset({"leo", "rex", "sal", "ara", "eve"})

# Accepted spellings → canonical provider name, mirroring the `if provider in (...)`
# groups in ``_build_provider`` so the credential probe + cross-family order key
# off ONE name regardless of how the user spelled it in `jarvis.toml`.
_ELEVEN_ALIASES = frozenset({"elevenlabs", "eleven-labs", "eleven_labs", "11labs"})
_CARTESIA_ALIASES = frozenset({
    "cartesia", "cartesia-sonic", "cartesia-sonic3", "cartesia-sonic-3",
    "cartesia-sonic-3.5",
})
_GROK_TTS_ALIASES = frozenset({
    "grok-voice", "grok_voice", "grok-tts", "xai-tts", "xai-voice",
})
_GEMINI_TTS_ALIASES = frozenset({"gemini-flash-tts", "gemini-flash", "gemini"})
_OPENROUTER_TTS_ALIASES = frozenset({
    "openrouter", "openrouter-tts", "openrouter_tts", "open-router-tts",
})

# Credential candidates per TTS family — the (keyring_key, env_var) pairs that
# hold a usable key, matching what each plugin's own key lookup reads. A fresh
# downloader's single TTS key is rarely the configured default, so the factory
# consults this and crosses to whatever TTS family the user DOES have a key for
# instead of building a keyless provider that goes silently mute (open-source
# single-provider resilience, AP-22). Families absent here are left untouched.
_TTS_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "gemini-flash-tts": (
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    ),
    "elevenlabs": (
        ("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
        ("eleven_api_key", "ELEVEN_API_KEY"),
    ),
    "cartesia": (("cartesia_api_key", "CARTESIA_API_KEY"),),
    "grok-voice": (
        ("xai_api_key", "XAI_API_KEY"),
        ("grok_api_key", "GROK_API_KEY"),
    ),
    # OpenRouter TTS reuses the ONE OpenRouter key shared with the brain /
    # Jarvis-Agent providers — a fresh downloader whose only credential is an
    # OpenRouter key (a gateway to many models) gets working voice for free.
    "openrouter": (("openrouter_api_key", "OPENROUTER_API_KEY"),),
}

# Cross-family probe order when the configured provider has no key: the family
# the maintainer ships first, then the common BYO-key alternatives. Only a family
# that actually has a key is ever chosen.
_TTS_CROSS_FAMILY_ORDER: tuple[str, ...] = (
    "gemini-flash-tts", "elevenlabs", "cartesia", "grok-voice", "openrouter",
)


def _canonical_tts_name(name: str) -> str:
    """Map any accepted TTS provider spelling to its canonical family name."""
    n = (name or "").strip().lower()
    if n in _ELEVEN_ALIASES:
        return "elevenlabs"
    if n in _CARTESIA_ALIASES:
        return "cartesia"
    if n in _GROK_TTS_ALIASES:
        return "grok-voice"
    if n in _GEMINI_TTS_ALIASES:
        return "gemini-flash-tts"
    if n in _OPENROUTER_TTS_ALIASES:
        return "openrouter"
    return n


def _tts_has_credential(canonical: str, tts_cfg: Any) -> bool:
    """Whether the TTS *family* ``canonical`` has a usable key on this host.

    Unknown / third-party providers (no entry in ``_TTS_SECRET_CANDIDATES``)
    return True so their path is never gated. Gemini via Vertex AI uses a service
    account rather than an API key, so a configured Vertex setup counts as a
    credential.
    """
    candidates = _TTS_SECRET_CANDIDATES.get(canonical)
    if candidates is None:
        return True
    if canonical == "gemini-flash-tts" and bool(getattr(tts_cfg, "use_vertex", False)):
        return True
    from jarvis.core import config as _cfg

    return _cfg.get_secret_any(candidates) is not None


class _VoiceOverride:
    """Read-through view of a ``TTSConfig`` that overrides only the voice fields.

    Used when crossing TTS families so a foreign default voice (e.g. the Gemini
    ``Charon``) is not inherited as a bogus ElevenLabs/Cartesia voice id — the
    crossed-to provider then resolves its OWN default. Every other attribute
    delegates to the base config (works for Pydantic models and test doubles).
    """

    __slots__ = ("_base", "_ov")

    def __init__(self, base: Any, overrides: dict[str, str]) -> None:
        self._base = base
        self._ov = overrides

    def __getattr__(self, name: str) -> Any:
        if name in self._ov:
            return self._ov[name]
        return getattr(self._base, name)


def _without_foreign_voice(tts_cfg: Any, target_canonical: str) -> Any:
    """Blank voice fields that belong to a DIFFERENT family than the target, so
    the crossed-to provider uses its own default instead of a mismatched voice."""
    target_allowed = (
        _GEMINI_VOICES
        if target_canonical == "gemini-flash-tts"
        else _GROK_VOICES
        if target_canonical == "grok-voice"
        else frozenset()
    )
    foreign = _GEMINI_VOICES | _GROK_VOICES
    overrides: dict[str, str] = {}
    for field in ("voice_de", "voice_en"):
        value = getattr(tts_cfg, field, "") or ""
        if value in foreign and value not in target_allowed:
            overrides[field] = ""
    return _VoiceOverride(tts_cfg, overrides) if overrides else tts_cfg


def _resolve_keyed_tts_provider(primary_name: str, tts_cfg: Any) -> tuple[str, Any]:
    """Pick a TTS provider the host can actually run (open-source AP-22).

    Keeps the configured provider when it has a usable key — so the maintainer
    path is untouched. Otherwise crosses to the first TTS family the user DOES
    have a key for and returns a voice-adjusted config view. When NO family has a
    key, keeps the configured provider (it degrades to the opt-in SAPI5 exit on
    Windows, or logs an honest mute) — never a silent swap.
    """
    canonical = _canonical_tts_name(primary_name)
    if _tts_has_credential(canonical, tts_cfg):
        return primary_name, tts_cfg
    for cand in _TTS_CROSS_FAMILY_ORDER:
        if cand == canonical:
            continue
        if _tts_has_credential(cand, tts_cfg):
            log.warning(
                "TTS provider %r has no usable API key; crossing to %r — the TTS "
                "family the user actually has a key for — so Jarvis is not mute "
                "(open-source AP-22). Set [tts].provider to silence this.",
                primary_name, cand,
            )
            return cand, _without_foreign_voice(tts_cfg, cand)
    log.warning(
        "No TTS provider has a usable API key (configured %r) — keeping it; voice "
        "output needs a TTS key, or the opt-in SAPI5 fallback on Windows.",
        primary_name,
    )
    return primary_name, tts_cfg


def _resolve_voice_for_provider(
    requested: str, provider: str, default: str, allowed: frozenset[str]
) -> str:
    """Returns a voice name valid for the provider.

    When the config voice doesn't belong to the current provider whitelist
    (typical case: the user switches provider, forgets to adjust the voice),
    we fall back to the provider default and log the override.
    """
    if not requested or requested not in allowed:
        if requested:
            log.info(
                "Voice %r does not match provider %r (valid: %s) — using %r.",
                requested, provider, ", ".join(sorted(allowed)), default,
            )
        return default
    return requested


def build_tts_from_config(tts_cfg: Any) -> Any:
    """Builds the TTS provider according to `config.tts.provider`.

    Honors `[tts].fallback`: when a fallback provider is configured (and differs
    from the primary), the primary is wrapped in a ``FallbackTTS`` so a
    primary-provider failure or an empty synthesis degrades to the backup voice
    instead of leaving Jarvis mute (AD-OE6 zero-silent-drops). Without a
    configured fallback the raw provider instance is returned unchanged, so
    legacy call-sites / test doubles see identical behaviour.

    Args:
        tts_cfg: `TTSConfig` instance from `jarvis.core.config`.

    Returns:
        Instance of the chosen TTS plugin (implements `TTSProvider`), or
        a `FallbackTTS` that wraps primary + fallback.

    Raises:
        RuntimeError: when the primary plugin is not importable
            (e.g. because the module was never installed). Previously
            this raised a bare ImportError that `desktop_app.py`
            swallowed via a blanket except → the entire speech pipeline gone.
            An un-buildable *fallback*, by contrast, only degrades (warning +
            primary-only), so a fallback misconfiguration doesn't kill the audio.
    """
    requested_name = (tts_cfg.provider or "gemini-flash-tts").lower()
    # Open-source AP-22: if the configured TTS provider has no usable key, cross
    # to whatever TTS family the user DOES have a key for, instead of building a
    # keyless provider that goes silently mute. The maintainer (who has the
    # configured key) is unaffected; an explicit [tts].fallback still composes.
    primary_name, primary_cfg = _resolve_keyed_tts_provider(requested_name, tts_cfg)
    primary = _build_provider(primary_cfg, primary_name)

    fallback_name = (getattr(tts_cfg, "fallback", "") or "").strip().lower()
    if not fallback_name or fallback_name == primary_name:
        return primary

    try:
        secondary = _build_provider(tts_cfg, fallback_name)
    except Exception as exc:  # noqa: BLE001 — a bad fallback must not kill audio
        log.warning(
            "TTS fallback provider %r not buildable (%s) — running primary %r only.",
            fallback_name, exc, primary_name,
        )
        return primary

    from jarvis.plugins.tts.fallback_tts import FallbackTTS

    log.info("TTS fallback active: primary=%r → fallback=%r", primary_name, fallback_name)
    return FallbackTTS(primary, secondary)


def _build_provider(tts_cfg: Any, provider: str) -> Any:
    """Build a single TTS provider instance for ``provider`` (no fallback wrap)."""
    allow_sapi5 = bool(getattr(tts_cfg, "allow_sapi5_fallback", False))

    if provider in ("elevenlabs", "eleven-labs", "eleven_labs", "11labs"):
        try:
            from jarvis.plugins.tts.elevenlabs_tts import (
                JARVIS_VOICE_DANIEL,
                ElevenLabsTTS,
            )
        except ImportError as exc:
            raise RuntimeError(
                f"TTS provider 'elevenlabs' configured, but the plugin is not "
                f"importable: {exc}",
            ) from exc
        # ElevenLabs uses voice IDs (cryptic hashes), no provider
        # whitelist — we take the config value as-is.
        return ElevenLabsTTS(
            model=tts_cfg.model or "eleven_flash_v2_5",
            default_voice=tts_cfg.voice_de or JARVIS_VOICE_DANIEL,
            language_code=tts_cfg.language_code or "de-DE",
            stability=tts_cfg.stability,
            similarity_boost=tts_cfg.similarity_boost,
            style=tts_cfg.style,
            speed=tts_cfg.speed,
            allow_sapi5_fallback=allow_sapi5,
        )

    if provider in (
        "cartesia", "cartesia-sonic", "cartesia-sonic3", "cartesia-sonic-3",
        "cartesia-sonic-3.5",
    ):
        try:
            from jarvis.plugins.tts.cartesia_tts import (
                DEFAULT_MODEL_ID,
                DEFAULT_VOICE_ID,
                CartesiaTTS,
            )
        except ImportError as exc:
            raise RuntimeError(
                f"TTS provider 'cartesia' configured, but the plugin is not "
                f"importable: {exc}. Check that jarvis/plugins/tts/cartesia_tts.py "
                f"exists and that httpx is installed.",
            ) from exc
        # Sub-table [tts.cartesia] lives in tts_cfg.model_extra (TTSConfig is
        # extra="allow"). Falls back to the plugin defaults when absent.
        extras = getattr(tts_cfg, "model_extra", None) or {}
        ct = extras.get("cartesia") if isinstance(extras, dict) else None
        ct = ct if isinstance(ct, dict) else {}
        return CartesiaTTS(
            model_id=ct.get("model_id", DEFAULT_MODEL_ID),
            voice_id=ct.get("voice_id", DEFAULT_VOICE_ID),
            voice_id_de=ct.get("voice_id_de"),
            voice_id_en=ct.get("voice_id_en"),
            voice_id_es=ct.get("voice_id_es"),
            language=ct.get("language", tts_cfg.language_code or "auto"),
            chunk_by_sentence=bool(ct.get("chunk_by_sentence", True)),
            speed=float(ct.get("speed", tts_cfg.speed)),
            allow_sapi5_fallback=allow_sapi5,
        )

    if provider in ("grok-voice", "grok_voice", "grok-tts", "xai-tts", "xai-voice"):
        try:
            from jarvis.plugins.tts.grok_voice_tts import GROK_VOICE_LEO, GrokVoiceTTS
        except ImportError as exc:
            raise RuntimeError(
                f"TTS provider 'grok-voice' configured, but the plugin is not "
                f"importable: {exc}. Check whether the file "
                f"jarvis/plugins/tts/grok_voice_tts.py exists and httpx "
                f"is installed.",
            ) from exc
        voice = _resolve_voice_for_provider(
            tts_cfg.voice_de, "grok-voice", GROK_VOICE_LEO, _GROK_VOICES,
        )
        return GrokVoiceTTS(
            default_voice=voice,
            language=tts_cfg.language_code or "auto",
            speed=tts_cfg.speed,
            allow_sapi5_fallback=allow_sapi5,
        )

    if provider in ("openrouter", "openrouter-tts", "openrouter_tts", "open-router-tts"):
        try:
            from jarvis.plugins.tts.openrouter_speech_models import DEFAULT_MODEL
            from jarvis.plugins.tts.openrouter_tts import OpenRouterTTS
        except ImportError as exc:
            raise RuntimeError(
                f"TTS provider 'openrouter' configured, but the plugin is not "
                f"importable: {exc}. Check that "
                f"jarvis/plugins/tts/openrouter_tts.py exists and httpx is "
                f"installed.",
            ) from exc
        # OpenRouter TTS validates its own voices per selected model, so the
        # config voice_de / voice_en are passed straight through (a foreign name
        # is auto-corrected to the model default inside the plugin).
        return OpenRouterTTS(
            model=tts_cfg.model or DEFAULT_MODEL,
            voice_de=getattr(tts_cfg, "voice_de", None),
            voice_en=getattr(tts_cfg, "voice_en", None),
            language=tts_cfg.language_code or "auto",
            speed=tts_cfg.speed,
        )

    if provider not in ("gemini-flash-tts", "gemini-flash", "gemini"):
        log.warning(
            "Unknown TTS provider %r — falling back to gemini-flash-tts.",
            tts_cfg.provider,
        )

    # Default / fallback: Gemini Flash TTS (previous behavior).
    try:
        from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS
    except ImportError as exc:
        raise RuntimeError(
            f"Gemini TTS plugin not importable: {exc}",
        ) from exc
    voice = _resolve_voice_for_provider(
        tts_cfg.voice_de, "gemini-flash-tts", "Charon", _GEMINI_VOICES,
    )
    # Backwards compat: old configs with an ElevenLabs voice ID map to the Gemini default.
    if voice == "onwK4e9ZLuTAKqWW03F9":
        voice = "Charon"
    return GeminiFlashTTS(
        model=tts_cfg.model or "gemini-3.1-flash-tts-preview",
        default_voice=voice,
        language_code=tts_cfg.language_code or "de-DE",
        style_prompt=tts_cfg.style_prompt or None,
        allow_sapi5_fallback=allow_sapi5,
        # Voice-consistency knobs (2026-05-24). getattr keeps older TTSConfig
        # shapes / test doubles working: missing field → historical default.
        chunk_by_sentence=bool(getattr(tts_cfg, "chunk_by_sentence", True)),
        # True streaming (2026-06-10): [tts].streaming promised "Echtes
        # Streaming" but was never forwarded — first audio waited for the
        # FULL sentence generation (2.4–8.1 s measured). getattr keeps
        # legacy TTSConfig shapes / fakes on the blocking path.
        streaming=bool(getattr(tts_cfg, "streaming", False)),
        seed=getattr(tts_cfg, "seed", None),
        temperature=getattr(tts_cfg, "temperature", None),
        # Vertex AI path (2026-05-26). Same getattr defence so legacy fakes
        # without these fields fall through onto the AI-Studio path.
        use_vertex=bool(getattr(tts_cfg, "use_vertex", False)),
        vertex_project=getattr(tts_cfg, "vertex_project", None),
        vertex_location=getattr(tts_cfg, "vertex_location", "us-central1"),
        service_account_path=getattr(tts_cfg, "service_account_path", None),
    )
