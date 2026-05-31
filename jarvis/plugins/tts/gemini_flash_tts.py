"""Gemini 3.1 Flash TTS Plugin (Public Preview, seit 2026-04-16).

Nutzt AI-Studio / `google-genai` mit einfachem API-Key-Auth. Gemini liefert
`audio/l16; rate=24000; channels=1` — Raw Linear-PCM, kein Header, kein
ffmpeg-Decoding nötig.

Kein *echtes* Streaming (AI-Studio-API liefert nur komplette Responses).
Pseudo-Streaming via Satz-für-Satz-Synthese ist implementiert: bei
`chunk_by_sentence=True` wird der Text an `.!?`-Grenzen gesplittet, jeder
Satz einzeln synthetisiert, Chunks werden sofort geyielded — der erste
Satz kann abgespielt werden während der zweite noch synthetisiert.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk

# Gemini 3.1 Flash TTS Output-Format ist fest: 24 kHz mono int16 PCM
GEMINI_TTS_SAMPLE_RATE = 24_000

# Default quota cooldown wenn Googles 429-Response keinen retryDelay liefert.
# 1h ist konservativ — Daily-Caps resetten zur UTC-Mitternacht, ein laenger
# als realer Reset-Delay wartender Cooldown ist harmloser als Hammering.
_QUOTA_COOLDOWN_S = 3600.0

# Regex zum Auslesen von ``retryDelay: '17270s'`` aus Google-Error-Strings.
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s")
# Regex fuer ``quotaValue: '100'`` — nur fuer's Log, zeigt User wie eng der Cap ist.
_QUOTA_VALUE_RE = re.compile(r"quotaValue['\"]?\s*:\s*['\"]?(\d+)")


def _parse_retry_delay(error_msg: str) -> float:
    """Liest ``retryDelay`` aus Googles 429-Error in Sekunden.

    Fallback ist ``_QUOTA_COOLDOWN_S`` (1h) wenn nichts erkannt wird.
    """
    m = _RETRY_DELAY_RE.search(error_msg)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return _QUOTA_COOLDOWN_S


def _parse_quota_cap(error_msg: str) -> str | None:
    """Liest ``quotaValue`` aus Googles 429-Error. Nur fuer Logs."""
    m = _QUOTA_VALUE_RE.search(error_msg)
    return m.group(1) if m else None

# SAPI5-Fallback: Windows-native, keine Quota, Hedda (DE) + Zira (EN) preinstalled
SAPI5_SAMPLE_RATE = 22_050
_SAPI5_FORMAT_22K_16MONO = 22  # SPSF_22kHz16BitMono

# 30 Prebuilt Voices laut Launch-Blog — wir whitelisten eine kuratierte Handvoll.
# Stimmen sind sprachagnostisch; `language_code` bzw. Inline-Text bestimmt die Sprache.
# JARVIS-Mode: tiefe, formale, männliche Stimmen bevorzugt.
DEFAULT_VOICES: tuple[str, ...] = (
    "Charon",     # JARVIS-Default — informativ, ruhig, Butler-Ton
    "Orus",       # firm, autoritär — JARVIS-Alternative 1
    "Iapetus",    # clear, präzise — JARVIS-Alternative 2
    "Rasalgethi", # informativ, wärmer
    "Algenib",    # gravelly, tiefer
    "Algieba",    # neutral, vorheriger Default
    "Kore",       # warm, weiblich
    "Fenrir",     # tief, männlich (excitable)
    "Aoede",      # lyrisch, weiblich
)

# Satz-Splitter: haengt an .!?… an — mit Lookbehind um Abkuerzungen (z.B. "z.B.")
# nicht zu brechen. 2026-04-24 zurueck auf engen Split: Semikolon/Doppelpunkt/
# Newlines als Grenzen fragmentierten zu aggressiv und triggerten SAPI5-Fallback
# haeufiger — die verschiedene Sample-Rate (22050 vs 24000) erzwang Resample-
# Flushs mid-stream, was Knarzen und robotische Artefakte erzeugte.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")


class GeminiFlashTTS:
    """TTS-Provider für Googles Gemini 3.1 Flash TTS (AI-Studio-API-Key)."""

    name = "gemini-flash-tts"
    supports_streaming = True  # pseudo-streaming via sentence-chunking

    def __init__(
        self,
        model: str = "gemini-3.1-flash-tts-preview",
        default_voice: str = "Charon",  # JARVIS-Butler-Voice
        language_code: str = "en-US",
        style_prompt: str | None = None,  # Gemini-TTS verträgt keinen Inline-Stil — deaktiviert
        chunk_by_sentence: bool = True,
        allow_sapi5_fallback: bool = False,
        sibling_bridge_model: str | None = "gemini-2.5-flash-preview-tts",
        seed: int | None = None,
        temperature: float | None = None,
        use_vertex: bool = False,
        vertex_project: str | None = None,
        vertex_location: str = "us-central1",
        service_account_path: str | None = None,
    ) -> None:
        self._model_name = model
        self._default_voice = default_voice
        self._language_code = language_code
        self._style_prompt = style_prompt
        self._chunk_by_sentence = chunk_by_sentence
        self._allow_sapi5_fallback = allow_sapi5_fallback
        # Vertex AI path (2026-05-26). When use_vertex=True we authenticate via
        # service-account credentials instead of an AI-Studio API key, bypassing
        # the 100-RPD Preview cap that triggered the daily Sibling-Bridge voice
        # switch. None of these touch the AI-Studio code path; defaults preserve
        # historical behaviour.
        self._use_vertex = use_vertex
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location
        self._service_account_path = service_account_path
        # Voice-consistency knobs (2026-05-24). Gemini Flash TTS is a generative
        # model: every call re-improvises the delivery, so the voice "drifts"
        # between the cached pre-answer and the live answer, between sentences,
        # and day-to-day. A fixed ``seed`` makes identical text render
        # identically run-to-run; a lowered ``temperature`` shrinks the
        # sampling variance in prosody. ``None`` keeps the SDK/model default
        # (pre-2026-05-24 behaviour). Pair with ``chunk_by_sentence=False`` so
        # the whole utterance is ONE generation = one coherent voice take.
        self._seed = seed
        self._temperature = temperature
        self._client: Any = None  # lazy
        # Circuit-Breaker: wenn Tagesquota 429'd, skippen wir Gemini bis _quota_until.
        # Vermeidet lange tenacity-Retries + unnoetige Latenz auf SAPI5-Pfad.
        self._quota_blocked_until: float = 0.0
        # Sibling-Bridge: when the configured model returns RESOURCE_EXHAUSTED
        # (typical case 2026-05: gemini-3.1-flash-tts-preview is hard-capped
        # at 100 requests/day on every Google AI Studio project — Pay-as-you-go
        # billing does not auto-promote new Preview models), retry the same
        # sentence once against a sibling Gemini TTS model that uses the
        # identical voice catalogue (Charon, Orus, …) and runs on the normal
        # paid quota. Setting sibling_bridge_model=None disables the bridge
        # entirely and restores pre-2026-05-14 behaviour (silence on 429).
        self._sibling_bridge_model = sibling_bridge_model
        # Per-model quota timer for the bridge target. We don't want to spam
        # the sibling endpoint either if Google ever lowers its limits.
        self._sibling_blocked_until: float = 0.0
        # Log the first bridge usage exactly once so the user sees the model
        # change in the live log without spamming every sentence.
        self._sibling_bridge_announced = False

    def _resolve_api_key(self) -> str:
        """Key-Lookup mit .env-Alias-Support.

        `.env` verwendet `GOOGLE_AIStudio_API_KEY` (User-spezifisch), google-genai
        sucht aber `GEMINI_API_KEY` / `GOOGLE_API_KEY`. Wir brücken das.
        """
        for env_var in ("GEMINI_API_KEY", "GOOGLE_AIStudio_API_KEY", "GOOGLE_API_KEY"):
            val = cfg.get_secret(env_var.lower(), env_fallback=env_var)
            if val:
                return val
        raise RuntimeError(
            "Gemini-API-Key nicht gefunden. Setze GEMINI_API_KEY oder "
            "GOOGLE_AIStudio_API_KEY in .env / Credential Manager."
        )

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from google import genai
        if self._use_vertex:
            # Service-account JSON via GOOGLE_APPLICATION_CREDENTIALS env. The
            # path field is the single source of truth — exporting it here
            # means the Cloud SDK auth chain picks it up even if the launcher
            # was started before the env was set.
            import os
            resolved: str | None = None
            if self._service_account_path:
                # Expand ~ so a config value like "~/.config/jarvis/vertex-sa.json"
                # resolves to the user's home dir cross-platform — Google's auth
                # chain does not expand tilde on its own. Only expand when ~ is
                # actually in the path so absolute paths pass through unchanged
                # (pathlib otherwise normalises POSIX separators to OS-native).
                resolved = self._service_account_path
                if "~" in resolved:
                    from pathlib import Path
                    resolved = str(Path(resolved).expanduser())
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = resolved
            # The AI-Studio keys must not be present in env when going through
            # Vertex — the SDK otherwise warns "Both GOOGLE_API_KEY and
            # GEMINI_API_KEY are set. Using GOOGLE_API_KEY." and routes the
            # request to AI Studio anyway, which would re-introduce the
            # 100-RPD cap. Strip them defensively at client-build time.
            for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
                os.environ.pop(k, None)
            # vertex_project is deliberately empty in the tracked config; the
            # project ID is meant to arrive via the JARVIS__TTS__VERTEX_PROJECT
            # env override (kept out of version control). When that override is
            # missing after a clean clone, fall back to the project_id inside
            # the service-account JSON we just resolved — that file is the
            # authoritative owner of the Vertex project, so deriving from it
            # removes a drift-prone second config knob. Without this, an empty
            # project raised on every sentence and left Jarvis mute (it would
            # hear + think but never speak). Only fail loudly if neither the
            # config nor the SA file can supply a project.
            if not self._vertex_project and resolved:
                derived = self._project_id_from_sa(resolved)
                if derived:
                    self._vertex_project = derived
                    logging.getLogger("jarvis.tts").info(
                        "Gemini-TTS Vertex: vertex_project was empty — derived "
                        "'%s' from service-account file %s.", derived, resolved,
                    )
            if not self._vertex_project:
                raise RuntimeError(
                    "use_vertex=True but vertex_project is empty and no "
                    "project_id could be read from the service-account file. "
                    "Set the JARVIS__TTS__VERTEX_PROJECT env var (or "
                    "[tts].vertex_project in jarvis.toml) to the GCP project "
                    "ID that owns the aiplatform.googleapis.com API."
                )
            self._client = genai.Client(
                vertexai=True,
                project=self._vertex_project,
                location=self._vertex_location,
            )
            logging.getLogger("jarvis.tts").info(
                "Gemini-TTS Vertex AI client built: project=%s location=%s model=%s",
                self._vertex_project, self._vertex_location, self._model_name,
            )
            return
        self._client = genai.Client(api_key=self._resolve_api_key())

    @staticmethod
    def _project_id_from_sa(path: str) -> str | None:
        """Best-effort read of `project_id` from a service-account JSON file.

        Returns None on any problem (missing file, malformed JSON, absent
        field) so a broken key degrades to the loud RuntimeError in
        ``_ensure_client`` rather than crashing client construction with an
        unexpected traceback.
        """
        try:
            import json
            from pathlib import Path
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        project = data.get("project_id")
        return project if isinstance(project, str) and project else None

    def _build_config(self, voice: str) -> Any:
        from google.genai import types
        # seed / temperature are passed through only when set, so an unset
        # config is byte-for-byte the pre-2026-05-24 request. They are valid
        # top-level GenerateContentConfig fields; if the preview audio model
        # ignores one it is a harmless no-op (never an API error).
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                ),
            ),
            seed=self._seed,
            temperature=self._temperature,
        )

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Synthetisiert Audio, yielded AudioChunks (je nach chunk_by_sentence).

        `language_code` kann pro Call überschrieben werden (z.B. "de-DE" / "en-US"),
        damit Multi-Language-Pipeline die Voice-Aussprache passend wechseln kann.
        """
        self._ensure_client()
        voice = voice or self._default_voice
        # language_code ist aktuell nicht im Gemini-AI-Studio-API speech_config
        # exponiert — die Voice entscheidet primär über die Aussprache. Wir
        # behalten den Parameter trotzdem für zukünftige Vertex-Migration.
        _ = language_code or self._language_code
        text = text.strip()
        if not text:
            return

        # Style-Prompt via Style-Direktive vor dem eigentlichen Text —
        # "Say the following <style>: ..." ist das offizielle Gemini-Pattern.
        # Inline-Prefix in Klammern triggert manchmal Safety-Filter → candidates=None.
        if self._style_prompt:
            text = f"Say the following in a {self._style_prompt} tone: {text}"

        if self._chunk_by_sentence:
            sentences = _split_sentences(text)
        else:
            sentences = [text]

        if not sentences:
            return

        # 2026-04-24: Alle Saetze parallel in Flight, in Original-Reihenfolge
        # yielden. Satz 1 gleich schnell wie vorher, Saetze 2..N aber bereits
        # synthetisiert wenn Satz 1 abgespielt ist — keine seriellen Netzwerk-
        # Waits mehr zwischen Saetzen (F6 im Fluss-Plan).
        tasks = [
            asyncio.create_task(self._synthesize_one(s, voice))
            for s in sentences
        ]
        log = logging.getLogger("jarvis.tts.gemini")
        for i, task in enumerate(tasks):
            pcm = await task
            if pcm:
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=GEMINI_TTS_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )
                continue

            # Gemini-Call leer (429, safety-block, quota): Default ist SCHWEIGEN
            # statt Silent-Switch auf Windows-SAPI5. Der Root-Cause steht im Log
            # (siehe `_synthesize_one`), und der User merkt am stummen TTS dass
            # etwas kaputt ist — statt einer roboterhaften Ersatzstimme.
            if not self._allow_sapi5_fallback:
                log.error(
                    "Gemini-TTS lieferte kein Audio fuer Satz %d/%d (%r) — "
                    "SAPI5-Fallback per Config deaktiviert (tts.allow_sapi5_fallback=false). "
                    "Audio bleibt fuer diesen Satz stumm.",
                    i + 1, len(tasks), sentences[i][:80],
                )
                continue

            log.warning(
                "Gemini-TTS leer fuer Satz %d/%d — SAPI5-Notbremse aktiv (Config-Opt-in).",
                i + 1, len(tasks),
            )
            fallback_pcm = await asyncio.to_thread(
                _sapi5_synthesize, sentences[i], language_code or self._language_code
            )
            if fallback_pcm:
                yield AudioChunk(
                    pcm=fallback_pcm,
                    sample_rate=SAPI5_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )

    async def _synthesize_one(self, text: str, voice: str) -> bytes:
        """Ein einzelner TTS-Call — in Thread-Pool weil google-genai sync ist.

        Faengt API-Errors (429 / Netzwerk / Safety) ab und returnt b"".
        Caller sieht leeres PCM → SAPI5-Fallback greift. Bei RESOURCE_EXHAUSTED
        (Tagesquota) aktivieren wir den Cooldown-Breaker.

        Sibling-Bridge: wenn das konfigurierte primary Modell 429'd UND ein
        ``sibling_bridge_model`` gesetzt ist (Default: gemini-2.5-flash-preview-tts),
        wird der gleiche Satz EINMAL gegen das Sibling-Modell synthetisiert.
        Begründung: 2026-05-14 Live-Diagnose — gemini-3.1-flash-tts-preview ist
        Free-Tier-capped (100 RPD) auf Pay-as-you-go-Konten; das ältere
        gemini-2.5-flash-preview-tts laeuft mit identischem Voice-Katalog
        (Charon, Orus, …) auf normaler bezahlter Quota.
        """
        import logging
        log = logging.getLogger("jarvis.tts")

        # Cooldown aktiv? Wenn ja, gleich auf Sibling-Bridge gehen statt
        # sinnloser API-Call gegen das geblockte Modell.
        primary_blocked = bool(
            self._quota_blocked_until and time.monotonic() < self._quota_blocked_until
        )
        if not primary_blocked:
            try:
                return await asyncio.to_thread(
                    self._synthesize_sync, text, voice, self._model_name
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                    # Parse retry-delay from the response if available, fall
                    # back to a 1h cooldown (Google's daily caps reset on UTC
                    # midnight — a long cooldown is safer than hammering).
                    retry_s = _parse_retry_delay(msg)
                    self._quota_blocked_until = time.monotonic() + retry_s
                    log.warning(
                        "Gemini-TTS Quota erschoepft auf %s (cap=%s, retry in %.0f min). "
                        "Versuche Sibling-Bridge %s …",
                        self._model_name,
                        _parse_quota_cap(msg) or "?",
                        retry_s / 60,
                        self._sibling_bridge_model or "(disabled)",
                    )
                    primary_blocked = True
                else:
                    log.warning("Gemini-TTS Fehler (%s) — SAPI5-Fallback.", exc.__class__.__name__)
                    return b""

        # Sibling-Bridge nur wenn (a) konfiguriert, (b) selbst nicht geblockt.
        if not primary_blocked:
            return b""
        if not self._sibling_bridge_model:
            return b""
        if self._sibling_blocked_until and time.monotonic() < self._sibling_blocked_until:
            return b""

        try:
            pcm = await asyncio.to_thread(
                self._synthesize_sync, text, voice, self._sibling_bridge_model
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                retry_s = _parse_retry_delay(msg)
                self._sibling_blocked_until = time.monotonic() + retry_s
                log.warning(
                    "Sibling-Bridge %s ebenfalls quota-blockiert (retry in %.0f min) — silence.",
                    self._sibling_bridge_model, retry_s / 60,
                )
            else:
                log.warning(
                    "Sibling-Bridge %s Fehler (%s) — silence.",
                    self._sibling_bridge_model, exc.__class__.__name__,
                )
            return b""

        if pcm and not self._sibling_bridge_announced:
            self._sibling_bridge_announced = True
            log.warning(
                "Gemini-TTS Sibling-Bridge aktiv: primary=%s gedrosselt → sprich "
                "ueber %s. Voice (%s) ist sprachagnostisch identisch. Sobald die "
                "primary-Quota wieder offen ist, switcht der Code automatisch zurueck.",
                self._model_name, self._sibling_bridge_model, voice,
            )
        return pcm

    def _synthesize_sync(self, text: str, voice: str, model: str | None = None) -> bytes:
        import logging
        log = logging.getLogger("jarvis.tts")
        assert self._client is not None
        target_model = model or self._model_name
        resp = self._client.models.generate_content(
            model=target_model,
            contents=text,
            config=self._build_config(voice),
        )
        # Robust gegen leere Antworten (Safety-Filter, Rate-Limit, etc.)
        if not resp.candidates:
            finish = "unknown"
            try:
                pf = resp.prompt_feedback
                finish = f"block_reason={pf.block_reason}"
            except Exception:  # noqa: BLE001
                pass
            log.warning("Gemini-TTS lieferte keine candidates (%s) — voice=%s text=%r",
                        finish, voice, text[:80])
            return b""
        cand = resp.candidates[0]
        if not cand.content or not cand.content.parts:
            log.warning("Gemini-TTS candidate ohne parts — voice=%s text=%r",
                        voice, text[:80])
            return b""
        part = cand.content.parts[0]
        if not part.inline_data or not part.inline_data.data:
            log.warning("Gemini-TTS part ohne inline_data — voice=%s text=%r",
                        voice, text[:80])
            return b""
        return part.inline_data.data

    def list_voices(self, language: str | None = None) -> list[str]:
        """30 Prebuilt-Voices sind sprachagnostisch — wir returnen unsere Whitelist."""
        return list(DEFAULT_VOICES)


def _split_sentences(text: str) -> list[str]:
    """Heuristischer Satz-Splitter. Kleiner Overhead, deutlich bessere Perceived-Latency."""
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


# ----------------------------------------------------------------------
# SAPI5-Emergency-Fallback (Windows native, keine Quota)
# ----------------------------------------------------------------------

def _sapi5_synthesize(text: str, language_code: str = "de-DE") -> bytes:
    """Blockt SAPI5-Call, liefert rohes PCM (22050Hz 16-bit mono).

    Wird aufgerufen wenn Gemini-TTS-Call leer zurückkommt (429 / safety-block).
    Qualität ist mittelmäßig, aber: Quota-frei, <100ms Latenz, User hört IMMER was.
    """
    import logging
    log = logging.getLogger("jarvis.tts.sapi5")
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        log.warning("pywin32 nicht installiert — SAPI5-Fallback nicht verfügbar.")
        return b""

    # pywin32 braucht CoInitialize in jedem Thread neu
    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass

    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = voice.GetVoices()
        # Wähle eine Stimme je Sprache
        pick_substring = "German" if language_code.lower().startswith("de") else "English"
        picked = None
        for i in range(voices.Count):
            desc = voices.Item(i).GetDescription()
            if pick_substring in desc:
                picked = voices.Item(i)
                break
        if picked is None and voices.Count > 0:
            picked = voices.Item(0)
        if picked is not None:
            voice.Voice = picked

        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = _SAPI5_FORMAT_22K_16MONO
        stream.Format = fmt
        voice.AudioOutputStream = stream
        voice.Speak(text, 0)  # 0 = synchronous
        raw = stream.GetData()
        return bytes(raw) if raw else b""
    except Exception as exc:  # noqa: BLE001
        log.warning("SAPI5-Fallback fehlgeschlagen: %s", exc)
        return b""
