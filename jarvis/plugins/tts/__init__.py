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

from jarvis.plugins.tts.curated_catalog import allowed_voices as _curated_voices

log = logging.getLogger("jarvis.tts.factory")

# Voices belonging to each respective provider — prevents e.g. a
# Gemini voice ("Charon") from landing in the Grok plugin and triggering HTTP 400.
# Derived from the curated catalog (the single source of truth) so a voice the
# picker legitimately offers (e.g. Gemini "Puck", Grok "luna") is never force-
# rewritten to the family default by the factory whitelist lagging behind.
_GEMINI_VOICES = frozenset(
    v.id for v in _curated_voices("gemini-flash-tts", "gemini-3.1-flash-tts-preview")
)
_GROK_VOICES = frozenset(
    v.id for v in _curated_voices("grok-voice", "grok-voice-tts-1.0")
)

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
_INWORLD_ALIASES = frozenset({
    "inworld", "inworld-tts", "inworld_tts", "inworld-tts-2",
})

# Credential candidates per TTS family — the (keyring_key, env_var) pairs that
# hold a usable key, matching what each plugin's own key lookup reads. A fresh
# downloader's single TTS key is rarely the configured default, so the factory
# consults this and crosses to whatever TTS family the user DOES have a key for
# instead of building a keyless provider that goes silently mute (open-source
# single-provider resilience, AP-22). Families absent here are left untouched.
_TTS_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "inworld": (("inworld_api_key", "INWORLD_API_KEY"),),
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

# Cross-family probe order when the configured provider has no key: native
# premium families first (Inworld leads — the arena-#1 realtime default), then
# the common BYO-key alternatives, with OpenRouter LAST as the last-resort
# gateway (design 2026-07-07). Only a family that actually has a key is chosen.
_TTS_CROSS_FAMILY_ORDER: tuple[str, ...] = (
    "inworld", "gemini-flash-tts", "elevenlabs", "cartesia", "grok-voice",
    "openrouter",
)


def _canonical_tts_name(name: str) -> str:
    """Map any accepted TTS provider spelling to its canonical family name."""
    n = (name or "").strip().lower()
    if n in _INWORLD_ALIASES:
        return "inworld"
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


def resolve_keyed_fallback(
    exclude_family: str,
    *,
    allow_sapi5: bool = False,
    language_code: str | None = None,
    reference_voice: str | None = None,
) -> Any | None:
    """Build the first cross-family TTS provider (≠ ``exclude_family``) that has
    a usable key on this host — so a plugin's INTERNAL runtime fallback never
    lands on a keyless (mute) provider (AP-22).

    The old plugins hardcoded ``GeminiFlashTTS`` as their stage-1 fallback, so a
    user whose ONLY key is Cartesia/ElevenLabs/Grok fell, on a mid-session
    quota/outage, onto a keyless Gemini → silence. This resolves the fallback
    through the SAME key-aware cross-family order the factory uses (native
    premium first, OpenRouter last), skipping the failed family and any keyless
    one. Returns a built provider, or ``None`` when NO other family has a key
    (the caller then degrades to the opt-in SAPI5 exit or an honest mute).

    ``reference_voice`` is the failing provider's ACTIVE voice: when given and
    its profile is known, the crossed-to family speaks with its curated voice
    of the SAME profile (masculine/feminine) instead of its unrelated default,
    so the mid-session takeover doesn't audibly change who is speaking.
    """
    from jarvis.core.config import TTSConfig
    from jarvis.plugins.tts.curated_catalog import continuity_voice, voice_gender

    ref_gender = voice_gender(reference_voice)
    exclude = _canonical_tts_name(exclude_family)
    for cand in _TTS_CROSS_FAMILY_ORDER:
        if cand == exclude:
            continue
        voice_kwargs: dict[str, str] = {}
        if ref_gender:
            model_id = None
            if cand == "openrouter":
                # The candidate view carries no model, so OpenRouterTTS will
                # use its default model — match the voice against that one.
                from jarvis.plugins.tts.openrouter_speech_models import DEFAULT_MODEL

                model_id = DEFAULT_MODEL
            matched = continuity_voice(cand, ref_gender, model_id=model_id)
            if matched:
                voice_kwargs = {"voice_de": matched, "voice_en": matched}
        cfg_view = TTSConfig(
            provider=cand,
            language_code=language_code or "auto",
            allow_sapi5_fallback=allow_sapi5,
            **voice_kwargs,
        )
        if not _tts_has_credential(cand, cfg_view):
            continue
        try:
            return _build_provider(cfg_view, cand)
        except Exception as exc:  # noqa: BLE001 — a bad candidate must not abort the chain
            log.warning(
                "Keyed fallback candidate %r not buildable (%s) — trying next.",
                cand, exc.__class__.__name__,
            )
            continue
    return None


# Same-family surface TTS per REALTIME provider (mode separation, 2026-07-17).
# The desktop surface re-renders a realtime turn locally when the live provider
# fails to deliver audio (scrub-gate cancel, text-only completion). That
# emergency voice must sound like the SESSION and resolve through the REALTIME
# credential slots — never jump straight to the pipeline's separately
# configured [tts] provider (live incident 2026-07-17 10:04: a gemini-live
# session's re-render spoke as "Charon @ openrouter" because the pipeline
# primary was openrouter-tts). Realtime families absent here have no native
# TTS sibling installed; their surface fallback stays on the key-aware
# pipeline chain.
_REALTIME_SURFACE_TTS_FAMILY: dict[str, str] = {
    "gemini-live": "gemini-flash-tts",
}


def build_realtime_surface_tts(
    cfg: Any, realtime_provider: str, pipeline_tts: Any
) -> Any:
    """TTS for re-rendering a REALTIME turn locally (the surface fallback).

    Mode separation: prefer a TTS of the SAME provider family as the active
    realtime session — keyed through the realtime credential slots
    (``PROVIDER_SECRET_CANDIDATES[<realtime id>]``, dedicated realtime slot
    first) and speaking the session's configured voice — over the pipeline's
    separately configured ``[tts]`` chain. The pipeline chain stays wired as
    the cross-family last resort so a dead or keyless family still degrades
    honestly instead of going mute (AD-OE6 zero-silent-drops, AP-22).

    Never raises: any resolution problem returns ``pipeline_tts`` unchanged.
    """
    provider_id = (realtime_provider or "").strip().lower()
    family = _REALTIME_SURFACE_TTS_FAMILY.get(provider_id)
    if not family:
        return pipeline_tts
    try:
        from jarvis.core.config import get_provider_secret

        api_key = get_provider_secret(provider_id)
        if not api_key:
            log.info(
                "Realtime surface TTS: no key for realtime provider %r — "
                "using the pipeline TTS chain.",
                provider_id,
            )
            return pipeline_tts

        providers = getattr(getattr(cfg, "brain", None), "providers", None)
        provider_cfg = (
            providers.get(provider_id) if isinstance(providers, dict) else None
        )
        session_voice = str(getattr(provider_cfg, "voice", "") or "").strip()
        tts_cfg = getattr(cfg, "tts", None)

        if family == "gemini-flash-tts":
            from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

            # The Live API and Gemini Flash TTS share one prebuilt-voice
            # catalog, so the session voice usually carries over verbatim;
            # an unknown voice falls back to the plugin default.
            voice = session_voice if session_voice in _GEMINI_VOICES else "Charon"
            primary: Any = GeminiFlashTTS(
                default_voice=voice,
                language_code=getattr(tts_cfg, "language_code", None) or "de-DE",
                allow_sapi5_fallback=bool(
                    getattr(tts_cfg, "allow_sapi5_fallback", False)
                ),
                streaming=bool(getattr(tts_cfg, "streaming", False)),
                api_key=api_key,
            )
        else:  # pragma: no cover — map entries always name a buildable family
            return pipeline_tts

        from jarvis.plugins.tts.fallback_tts import FallbackTTS

        log.info(
            "Realtime surface TTS: same-family %r (voice %r) serves realtime "
            "provider %r; the pipeline chain remains the last resort.",
            family, voice, provider_id,
        )
        return FallbackTTS(primary, pipeline_tts)
    except Exception as exc:  # noqa: BLE001 — resolution must never mute the fallback
        log.warning(
            "Realtime surface TTS resolution failed (%s) — using the pipeline "
            "TTS chain.",
            exc,
        )
        return pipeline_tts


def _effective_primary_voice(family: str, tts_cfg: Any) -> str | None:
    """Best-effort offline prediction of the voice ``family`` will speak with.

    Mirrors each family's builder/plugin voice resolution (config voice when
    valid for the family, else the family default) WITHOUT building the
    provider. Used to derive the active voice PROFILE for fallback continuity.
    Returns ``None`` for families whose voice cannot be predicted offline
    (Cartesia ids, unknown third-party providers) — callers then skip the
    profile matching (today's behavior).
    """
    voice_de = (getattr(tts_cfg, "voice_de", None) or "").strip()
    if family == "grok-voice":
        try:
            from jarvis.plugins.tts.grok_voice_tts import DEFAULT_VOICES, GROK_VOICE_LEO
        except ImportError:
            return None
        return voice_de if voice_de in DEFAULT_VOICES else GROK_VOICE_LEO
    if family == "gemini-flash-tts":
        return voice_de if voice_de in _GEMINI_VOICES else "Charon"
    if family == "elevenlabs":
        try:
            from jarvis.plugins.tts.elevenlabs_tts import JARVIS_VOICE_DANIEL
        except ImportError:
            return None
        cleaned = _without_foreign_voice(tts_cfg, "elevenlabs")
        return (getattr(cleaned, "voice_de", None) or "").strip() or JARVIS_VOICE_DANIEL
    if family == "openrouter":
        try:
            from jarvis.plugins.tts.openrouter_speech_models import (
                MODEL_DEFAULT_VOICE,
                MODEL_VOICES,
                coerce_speech_model,
            )
        except ImportError:
            return None
        mid = coerce_speech_model(getattr(tts_cfg, "model", None))
        if voice_de and voice_de in MODEL_VOICES.get(mid, ()):
            return voice_de
        return MODEL_DEFAULT_VOICE.get(mid)
    if family == "inworld":
        extras = getattr(tts_cfg, "model_extra", None) or {}
        iw = extras.get("inworld") if isinstance(extras, dict) else None
        iw = iw if isinstance(iw, dict) else {}
        configured = (iw.get("voice_de") or "").strip()
        if configured:
            return configured
        try:
            from jarvis.plugins.tts.inworld_tts import DEFAULT_VOICE_DE
        except ImportError:
            return None
        return DEFAULT_VOICE_DE
    return None


def _continuity_fallback_voice(
    primary_name: str, primary_cfg: Any, fallback_name: str, fallback_cfg: Any
) -> str | None:
    """Voice the ``FallbackTTS`` wrapper should pin on the fallback provider so
    a mid-conversation takeover keeps the ACTIVE voice profile (the audible
    masculine/feminine character), instead of flipping to the fallback family's
    unrelated default voice.

    Returns ``None`` when the primary's profile is unknown, or when the
    fallback's own voice resolution already matches it (an explicitly chosen,
    profile-consistent fallback voice is respected, never overridden).
    """
    from jarvis.plugins.tts.curated_catalog import continuity_voice, voice_gender

    ref_voice = _effective_primary_voice(_canonical_tts_name(primary_name), primary_cfg)
    ref_gender = voice_gender(ref_voice)
    if not ref_gender:
        return None
    fb_family = _canonical_tts_name(fallback_name)
    native = _effective_primary_voice(fb_family, fallback_cfg)
    if native is not None and voice_gender(native) == ref_gender:
        return None
    model_id = None
    if fb_family == "openrouter":
        from jarvis.plugins.tts.openrouter_speech_models import coerce_speech_model

        model_id = coerce_speech_model(getattr(fallback_cfg, "model", None))
    return continuity_voice(fb_family, ref_gender, model_id=model_id)


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
    # Compare CANONICAL families, not raw strings: a provider="gemini" +
    # fallback="gemini-flash-tts" pair (or any alias pair) resolves to the SAME
    # family and must NOT build a FallbackTTS(gemini, gemini) single-family brick
    # (AP-22). primary_name is already the resolved (possibly crossed) provider.
    if not fallback_name or _canonical_tts_name(fallback_name) == _canonical_tts_name(
        primary_name
    ):
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

    # Voice-profile continuity (2026-07-17): pin the fallback family's curated
    # voice matching the primary's active profile, so a mid-conversation
    # takeover never audibly flips masculine↔feminine ("Jarvis suddenly has a
    # different voice"). None → the fallback keeps its own resolution.
    fallback_voice = _continuity_fallback_voice(
        primary_name, primary_cfg, fallback_name, tts_cfg
    )
    log.info(
        "TTS fallback active: primary=%r → fallback=%r (continuity voice: %s)",
        primary_name, fallback_name, fallback_voice or "provider default",
    )
    return FallbackTTS(primary, secondary, fallback_voice=fallback_voice)


def _build_provider(tts_cfg: Any, provider: str) -> Any:
    """Build a single TTS provider instance for ``provider`` (no fallback wrap)."""
    allow_sapi5 = bool(getattr(tts_cfg, "allow_sapi5_fallback", False))

    if provider in _INWORLD_ALIASES:
        try:
            from jarvis.plugins.tts.inworld_tts import (
                DEFAULT_MODEL as INWORLD_DEFAULT_MODEL,
            )
            from jarvis.plugins.tts.inworld_tts import (
                DEFAULT_VOICE_DE,
                DEFAULT_VOICE_EN,
                DEFAULT_VOICE_ES,
                InworldTTS,
            )
        except ImportError as exc:
            raise RuntimeError(
                f"TTS provider 'inworld' configured, but the plugin is not "
                f"importable: {exc}. Check that "
                f"jarvis/plugins/tts/inworld_tts.py exists and httpx is installed.",
            ) from exc
        # Per-language voices live in the [tts.inworld] sub-table (extra="allow"),
        # NOT [tts].voice_de — that field may hold a foreign Gemini voice name
        # ("Charon") which is not a valid Inworld voice. Falls back to the
        # plugin's native defaults (Josef/Dennis/Diego). The shared [tts].model
        # is ignored here (it may hold another family's id); the model comes from
        # the sub-table or the plugin default.
        extras = getattr(tts_cfg, "model_extra", None) or {}
        iw = extras.get("inworld") if isinstance(extras, dict) else None
        iw = iw if isinstance(iw, dict) else {}
        return InworldTTS(
            default_voice_de=iw.get("voice_de") or DEFAULT_VOICE_DE,
            default_voice_en=iw.get("voice_en") or DEFAULT_VOICE_EN,
            default_voice_es=iw.get("voice_es") or DEFAULT_VOICE_ES,
            model=iw.get("model") or INWORLD_DEFAULT_MODEL,
            language=tts_cfg.language_code or "auto",
            speed=float(iw.get("speed", getattr(tts_cfg, "speed", 1.0))),
            allow_sapi5_fallback=allow_sapi5,
        )

    if provider in ("elevenlabs", "eleven-labs", "eleven_labs", "11labs"):
        try:
            from jarvis.plugins.tts.elevenlabs_tts import (
                JARVIS_VOICE_DANIEL,
                ElevenLabsTTS,
                coerce_elevenlabs_model,
            )
        except ImportError as exc:
            raise RuntimeError(
                f"TTS provider 'elevenlabs' configured, but the plugin is not "
                f"importable: {exc}",
            ) from exc
        # Cross-family config hygiene: [tts] shares ONE model + voice field
        # across every TTS family. After switching FROM Cartesia/Gemini/Grok TO
        # ElevenLabs, a stale foreign model ('sonic-2') or voice name ('Kore')
        # can remain and 400s every ElevenLabs call — the provider then reads as
        # "Not working — synthesized 0 bytes" while the fallback voice speaks, so
        # it looks broken yet audible. Drop foreign values → ElevenLabs defaults.
        # A genuine ElevenLabs voice id (cryptic hash) is preserved as-is.
        eleven_cfg = _without_foreign_voice(tts_cfg, "elevenlabs")
        return ElevenLabsTTS(
            model=coerce_elevenlabs_model(tts_cfg.model),
            default_voice=eleven_cfg.voice_de or JARVIS_VOICE_DANIEL,
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
