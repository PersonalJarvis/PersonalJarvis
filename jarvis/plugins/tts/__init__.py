"""TTS-Provider-Plugins (Google Gemini, ElevenLabs, xAI Grok, ...).

`build_tts_from_config` ist die zentrale Factory fuer alle Call-Sites
(Desktop-App, Speech-Pipeline-CLI). Nur so bleibt der TTS-Wechsel per
`jarvis.toml` ein Config-Edit und kein Code-Edit.

SAPI5 (Windows-natives, roboterhaftes TTS) ist seit 2026-04-25 nur noch
ein **opt-in**-Notausgang: per Default schweigt der Provider lieber als
auf die Windows-Stimme umzuschalten. Auf `tts.allow_sapi5_fallback = true`
setzen, wenn man jedwedes Audio-Output haben will.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("jarvis.tts.factory")

# Voices, die zum jeweiligen Provider gehoeren — verhindert dass z.B. ein
# Gemini-Voice ("Charon") im Grok-Plugin landet und HTTP 400 ausloest.
_GEMINI_VOICES = frozenset({
    "Charon", "Orus", "Iapetus", "Rasalgethi", "Algenib",
    "Algieba", "Kore", "Fenrir", "Aoede",
})
_GROK_VOICES = frozenset({"leo", "rex", "sal", "ara", "eve"})


def _resolve_voice_for_provider(
    requested: str, provider: str, default: str, allowed: frozenset[str]
) -> str:
    """Liefert einen fuer den Provider gueltigen Voice-Namen.

    Wenn der Config-Voice nicht zur aktuellen Provider-Whitelist gehoert
    (typischer Fall: User wechselt provider, vergisst voice anzupassen),
    fallen wir auf den Provider-Default zurueck und loggen den Override.
    """
    if not requested or requested not in allowed:
        if requested:
            log.info(
                "Voice %r passt nicht zu Provider %r (gueltig: %s) — nutze %r.",
                requested, provider, ", ".join(sorted(allowed)), default,
            )
        return default
    return requested


def build_tts_from_config(tts_cfg: Any) -> Any:
    """Erzeugt den TTS-Provider entsprechend `config.tts.provider`.

    Honors `[tts].fallback`: when a fallback provider is configured (and differs
    from the primary), the primary is wrapped in a ``FallbackTTS`` so a
    primary-provider failure or an empty synthesis degrades to the backup voice
    instead of leaving Jarvis mute (AD-OE6 zero-silent-drops). Without a
    configured fallback the raw provider instance is returned unchanged, so
    legacy call-sites / test doubles see identical behaviour.

    Args:
        tts_cfg: `TTSConfig`-Instanz aus `jarvis.core.config`.

    Returns:
        Instanz des gewaehlten TTS-Plugins (implementiert `TTSProvider`), oder
        ein `FallbackTTS` der primär + fallback umschliesst.

    Raises:
        RuntimeError: wenn das primaere Plugin nicht importierbar ist
            (z.B. weil das Modul gar nicht installiert wurde). Frueher
            knallte hier ein nackter ImportError, den `desktop_app.py`
            via blanket-except verschluckte → ganze Speech-Pipeline weg.
            Ein nicht-baubarer *Fallback* degradiert dagegen nur (Warnung +
            primary-only), damit eine Fallback-Fehlkonfig den Ton nicht killt.
    """
    primary_name = (tts_cfg.provider or "gemini-flash-tts").lower()
    primary = _build_provider(tts_cfg, primary_name)

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
                f"TTS-Provider 'elevenlabs' konfiguriert, aber Plugin nicht "
                f"importierbar: {exc}",
            ) from exc
        # ElevenLabs nutzt Voice-IDs (kryptische Hashes), keine Provider-
        # Whitelist — wir nehmen den Config-Wert wie er ist.
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

    if provider in ("cartesia", "cartesia-sonic", "cartesia-sonic3", "cartesia-sonic-3", "cartesia-sonic-3.5"):
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
                f"TTS-Provider 'grok-voice' konfiguriert, aber Plugin nicht "
                f"importierbar: {exc}. Pruefe, ob die Datei "
                f"jarvis/plugins/tts/grok_voice_tts.py existiert und httpx "
                f"installiert ist.",
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

    if provider not in ("gemini-flash-tts", "gemini-flash", "gemini"):
        log.warning(
            "Unbekannter TTS-Provider %r — falle auf gemini-flash-tts zurueck.",
            tts_cfg.provider,
        )

    # Default / Fallback: Gemini-Flash-TTS (bisheriges Verhalten).
    try:
        from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS
    except ImportError as exc:
        raise RuntimeError(
            f"Gemini-TTS-Plugin nicht importierbar: {exc}",
        ) from exc
    voice = _resolve_voice_for_provider(
        tts_cfg.voice_de, "gemini-flash-tts", "Charon", _GEMINI_VOICES,
    )
    # Backwards-compat: alte Configs mit ElevenLabs-Voice-ID auf Gemini-Default.
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
        seed=getattr(tts_cfg, "seed", None),
        temperature=getattr(tts_cfg, "temperature", None),
        # Vertex AI path (2026-05-26). Same getattr defence so legacy fakes
        # without these fields fall through onto the AI-Studio path.
        use_vertex=bool(getattr(tts_cfg, "use_vertex", False)),
        vertex_project=getattr(tts_cfg, "vertex_project", None),
        vertex_location=getattr(tts_cfg, "vertex_location", "us-central1"),
        service_account_path=getattr(tts_cfg, "service_account_path", None),
    )
