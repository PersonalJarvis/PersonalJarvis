"""Gemini 3.1 Flash TTS Plugin (Public Preview, since 2026-04-16).

Uses AI-Studio / `google-genai` with simple API-key auth. Gemini returns
`audio/l16; rate=24000; channels=1` — raw linear PCM, no header, no
ffmpeg decoding needed.

Streaming (since 2026-06-10): with ``streaming=True`` the first sentence is
synthesized via ``client.aio.models.generate_content_stream`` and its PCM
pieces are yielded AS THEY ARRIVE — measured time-to-first-audio drops from
2.4–8.1 s (blocking full generation) to 0.6–1.3 s on the same model/voice.
It is still ONE generation per sentence (seed/temperature apply unchanged),
so voice consistency is identical to the blocking path. The historical note
"AI Studio only returns complete responses" was disproven empirically
(114 incremental chunks over Vertex, scripts/probe_tts_streaming.py).

Pseudo-streaming via sentence-by-sentence synthesis also remains: with
``chunk_by_sentence=True`` the text splits at ``.!?`` boundaries; sentence 1
streams live, sentences 2..N prefetch in parallel and yield in order.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk

# Gemini 3.1 Flash TTS output format is fixed: 24 kHz mono int16 PCM
GEMINI_TTS_SAMPLE_RATE = 24_000

# Default quota cooldown when Google's 429 response doesn't supply a retryDelay.
# 1h is conservative — daily caps reset at UTC midnight, a cooldown that waits
# longer than the real reset delay is more harmless than hammering.
_QUOTA_COOLDOWN_S = 3600.0

# Regex to read ``retryDelay: '17270s'`` out of Google error strings.
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s")
# Regex for ``quotaValue: '100'`` — just for the log, shows the user how tight the cap is.
_QUOTA_VALUE_RE = re.compile(r"quotaValue['\"]?\s*:\s*['\"]?(\d+)")


def _parse_retry_delay(error_msg: str) -> float:
    """Reads ``retryDelay`` in seconds out of Google's 429 error.

    Falls back to ``_QUOTA_COOLDOWN_S`` (1h) when nothing is recognized.
    """
    m = _RETRY_DELAY_RE.search(error_msg)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return _QUOTA_COOLDOWN_S


def _parse_quota_cap(error_msg: str) -> str | None:
    """Reads ``quotaValue`` out of Google's 429 error. Log-only."""
    m = _QUOTA_VALUE_RE.search(error_msg)
    return m.group(1) if m else None

# SAPI5 fallback: Windows-native, no quota, Hedda (DE) + Zira (EN) preinstalled
SAPI5_SAMPLE_RATE = 22_050
_SAPI5_FORMAT_22K_16MONO = 22  # SPSF_22kHz16BitMono

# 30 prebuilt voices per the launch blog — we whitelist a curated handful.
# Voices are language-agnostic; `language_code` or inline text determines the language.
# JARVIS mode: deep, formal, male voices preferred.
DEFAULT_VOICES: tuple[str, ...] = (
    "Charon",     # JARVIS default — informative, calm, butler tone
    "Orus",       # firm, authoritative — JARVIS alternative 1
    "Iapetus",    # clear, precise — JARVIS alternative 2
    "Rasalgethi", # informative, warmer
    "Algenib",    # gravelly, deeper
    "Algieba",    # neutral, previous default
    "Kore",       # warm, female
    "Fenrir",     # deep, male (excitable)
    "Aoede",      # lyrical, female
)

# Sentence splitter: hooks onto .!?… — with a lookbehind so abbreviations
# (e.g. "e.g.") aren't broken. 2026-04-24 reverted to a narrower split:
# semicolon/colon/newlines as boundaries fragmented too aggressively and
# triggered the SAPI5 fallback more often — the different sample rate
# (22050 vs 24000) forced mid-stream resample flushes, producing crackling
# and robotic artifacts.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")  # i18n-allow: DE+EN capital-letter lookahead, matched in logic


class GeminiFlashTTS:
    """TTS provider for Google's Gemini 3.1 Flash TTS (AI-Studio API key)."""

    name = "gemini-flash-tts"
    supports_streaming = True  # pseudo-streaming via sentence-chunking

    def __init__(
        self,
        model: str = "gemini-3.1-flash-tts-preview",
        default_voice: str = "Charon",  # JARVIS-Butler-Voice
        language_code: str = "en-US",
        style_prompt: str | None = None,  # Gemini TTS doesn't tolerate an inline style — disabled
        chunk_by_sentence: bool = True,
        streaming: bool = False,
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
        # True streaming (2026-06-10 latency collapse): synthesize sentence 1
        # via ``generate_content_stream`` and yield PCM pieces as they arrive.
        # Default False so bare ``GeminiFlashTTS()`` call sites keep their
        # historical blocking behaviour; production wires [tts].streaming here
        # through build_tts_from_config.
        self._streaming = streaming
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
        # Circuit breaker: if the daily quota 429'd, we skip Gemini until _quota_until.
        # Avoids long tenacity retries + unnecessary latency on the SAPI5 path.
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
        """Key lookup with .env alias support.

        `.env` uses `GOOGLE_AIStudio_API_KEY` (user-specific), but google-genai
        looks for `GEMINI_API_KEY` / `GOOGLE_API_KEY`. We bridge that.
        """
        for env_var in ("GEMINI_API_KEY", "GOOGLE_AIStudio_API_KEY", "GOOGLE_API_KEY"):
            val = cfg.get_secret(env_var.lower(), env_fallback=env_var)
            if val:
                return val
        raise RuntimeError(
            "Gemini API key not found. Set GEMINI_API_KEY or "
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

    def _build_config(self, voice: str, language_code: str | None = None) -> Any:
        from google.genai import types
        # seed / temperature are passed through only when set, so an unset
        # config is byte-for-byte the pre-2026-05-24 request. They are valid
        # top-level GenerateContentConfig fields; if the preview audio model
        # ignores one it is a harmless no-op (never an API error).
        #
        # ``language_code`` pins the pronunciation language for THIS turn. The
        # speech pipeline resolves it once (``resolve_output_language``) and
        # passes the per-turn value down; ``SpeechConfig.language_code`` IS the
        # SDK field the model reads (checked against google-genai 1.67.0 on
        # 2026-06-19: a "de-DE" pin is accepted and audio is returned — the
        # historical "not exposed in the AI-Studio speech_config" note was
        # stale; re-confirm if the SDK or model major version changes). Without
        # a pin Gemini Flash TTS — a generative multilingual model — picks the
        # language PER WORD from the text, so a German sentence ending on an
        # English loanword ("…Boss.") code-switches its tail into English (the
        # 2026-06-19 voice forensic). ``None``/empty leaves it unpinned =
        # auto-detect = the historical behaviour, so a path that omits it never
        # regresses.
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                ),
                # ``or None``: an empty string (a misconfigured resolver) means
                # "unset" just like None — both leave the model on auto-detect.
                language_code=language_code or None,
            ),
            seed=self._seed,
            temperature=self._temperature,
        )

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Synthesizes audio, yielding AudioChunks (depending on chunk_by_sentence).

        `language_code` can be overridden per call (e.g. "de-DE" / "en-US"),
        so the multi-language pipeline can switch the voice pronunciation accordingly.
        """
        self._ensure_client()
        voice = voice or self._default_voice
        # Per-turn pronunciation pin: the pipeline resolves this turn's language
        # (resolve_output_language) and passes ``language_code`` here. It is
        # threaded down to the GenerateContentConfig so the model stops
        # auto-switching the sentence tail into English on an English loanword
        # (2026-06-19 forensic). A ``None`` call-value stays unpinned = the
        # historical auto-detect behaviour. The configured ``self._language_code``
        # default is NOT used as a silent pin (that would let this layer derive
        # the language on its own terms, against the Runtime-Output-Language
        # doctrine); it survives only as the SAPI5 emergency-voice hint below.
        text = text.strip()
        if not text:
            return

        # Style prompt via a style directive placed before the actual text —
        # "Say the following <style>: ..." is the official Gemini pattern.
        # An inline prefix in parentheses sometimes triggers the safety filter → candidates=None.
        if self._style_prompt:
            text = f"Say the following in a {self._style_prompt} tone: {text}"

        if self._chunk_by_sentence:
            sentences = _split_sentences(text)
        else:
            sentences = [text]

        if not sentences:
            return

        log = logging.getLogger("jarvis.tts.gemini")

        # True-streaming fast path ([tts].streaming, 2026-06-10): sentence 1's
        # PCM pieces are yielded AS THE MODEL GENERATES them — measured
        # time-to-first-audio 0.6–1.3 s vs 2.4–8.1 s for the buffered call.
        # Zero streamed audio (quota cooldown, transport error before the
        # first byte) leaves ``sentences`` untouched, so the buffered flow
        # below synthesizes it with the complete fallback ladder (cooldown →
        # sibling bridge → SAPI5/silence). After ≥1 streamed piece the
        # sentence is DONE even if the stream broke mid-way: re-synthesizing
        # would replay its opening words (same policy as FallbackTTS).
        if self._streaming:
            streamed_any = False
            stream_gen = self._synthesize_stream_one(sentences[0], voice, language_code)
            try:
                async for piece in stream_gen:
                    if piece:
                        streamed_any = True
                        yield AudioChunk(
                            pcm=piece,
                            sample_rate=GEMINI_TTS_SAMPLE_RATE,
                            timestamp_ns=0,
                            channels=1,
                        )
            finally:
                # ``async for`` never closes its iterator (PEP 525). On a
                # barge-in the GeneratorExit lands on the yield above and
                # would leave the inner generator — and the genai HTTP
                # stream inside it — to nondeterministic GC finalization.
                # Closing it here runs its finally NOW.
                with contextlib.suppress(Exception):
                    await stream_gen.aclose()
            if streamed_any:
                sentences = sentences[1:]
                if not sentences:
                    return

        # 2026-04-24: all sentences in flight in parallel, yielded in original
        # order. Sentence 1 as fast as before, but sentences 2..N are already
        # synthesized by the time sentence 1 finishes playing — no more serial
        # network waits between sentences (F6 in the flow plan).
        tasks = [
            asyncio.create_task(self._synthesize_one(s, voice, language_code))
            for s in sentences
        ]
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

            # Gemini call came back empty (429, safety block, quota): default is
            # SILENCE instead of a silent switch to Windows SAPI5. The root
            # cause is in the log (see `_synthesize_one`), and the user notices
            # from the muted TTS that something is broken — instead of a
            # robotic stand-in voice.
            if not self._allow_sapi5_fallback:
                log.error(
                    "Gemini TTS returned no audio for sentence %d/%d (%r) — "
                    "SAPI5 fallback disabled via config (tts.allow_sapi5_fallback=false). "
                    "Audio stays silent for this sentence.",
                    i + 1, len(tasks), sentences[i][:80],
                )
                continue

            log.warning(
                "Gemini TTS empty for sentence %d/%d — SAPI5 emergency brake active (config opt-in).",
                i + 1, len(tasks),
            )
            # SAPI5 deliberately DOES fall back to ``self._language_code`` here
            # (unlike the Gemini path above, which never does). The Windows
            # SAPI5 voice catalogue has no auto-detect — it must be handed an
            # explicit language to pick the right installed voice (German vs
            # English), so a bare ``None`` would have nothing to select on. The
            # configured instance default is the operator's intended
            # emergency-voice hint, NOT a per-turn pronunciation pin — it only
            # ever reaches the rarely-used, config-opt-in SAPI5 notbremse, never
            # the live Gemini request. This asymmetry is intentional.
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

    async def _synthesize_one(
        self, text: str, voice: str, language_code: str | None = None
    ) -> bytes:
        """A single TTS call — in a thread pool because google-genai is sync.

        Catches API errors (429 / network / safety) and returns b"".
        The caller sees empty PCM → the SAPI5 fallback kicks in. On
        RESOURCE_EXHAUSTED (daily quota) we arm the cooldown breaker.

        Sibling bridge: if the configured primary model 429'd AND a
        ``sibling_bridge_model`` is set (default: gemini-2.5-flash-preview-tts),
        the same sentence is synthesized ONCE against the sibling model.
        Rationale: 2026-05-14 live diagnosis — gemini-3.1-flash-tts-preview is
        free-tier-capped (100 RPD) on pay-as-you-go accounts; the older
        gemini-2.5-flash-preview-tts runs with an identical voice catalogue
        (Charon, Orus, …) on the normal paid quota.
        """
        import logging
        log = logging.getLogger("jarvis.tts")

        # Cooldown active? If so, go straight to the sibling bridge instead of
        # a pointless API call against the blocked model.
        primary_blocked = bool(
            self._quota_blocked_until and time.monotonic() < self._quota_blocked_until
        )
        if not primary_blocked:
            try:
                return await asyncio.to_thread(
                    self._synthesize_sync, text, voice, self._model_name, language_code
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
                        "Gemini TTS quota exhausted on %s (cap=%s, retry in %.0f min). "
                        "Trying sibling bridge %s …",
                        self._model_name,
                        _parse_quota_cap(msg) or "?",
                        retry_s / 60,
                        self._sibling_bridge_model or "(disabled)",
                    )
                    primary_blocked = True
                else:
                    log.warning("Gemini TTS error (%s) — SAPI5 fallback.", exc.__class__.__name__)
                    return b""

        # Sibling bridge only if (a) configured, (b) not blocked itself.
        if not primary_blocked:
            return b""
        if not self._sibling_bridge_model:
            return b""
        if self._sibling_blocked_until and time.monotonic() < self._sibling_blocked_until:
            return b""

        try:
            pcm = await asyncio.to_thread(
                self._synthesize_sync, text, voice, self._sibling_bridge_model, language_code
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                retry_s = _parse_retry_delay(msg)
                self._sibling_blocked_until = time.monotonic() + retry_s
                log.warning(
                    "Sibling bridge %s also quota-blocked (retry in %.0f min) — silence.",
                    self._sibling_bridge_model, retry_s / 60,
                )
            else:
                log.warning(
                    "Sibling bridge %s error (%s) — silence.",
                    self._sibling_bridge_model, exc.__class__.__name__,
                )
            return b""

        if pcm and not self._sibling_bridge_announced:
            self._sibling_bridge_announced = True
            log.warning(
                "Gemini TTS sibling bridge active: primary=%s throttled → speaking "
                "via %s. Voice (%s) is language-agnostic and identical. As soon as the "
                "primary quota reopens, the code switches back automatically.",
                self._model_name, self._sibling_bridge_model, voice,
            )
        return pcm

    async def _synthesize_stream_one(
        self, text: str, voice: str, language_code: str | None = None
    ) -> AsyncIterator[bytes]:
        """True-streaming synthesis of ONE sentence — yields raw PCM pieces.

        Same model / voice / seed / temperature as the blocking call, so the
        delivery is ONE generation either way (voice consistency unchanged);
        only the transport differs: ``generate_content_stream`` hands out the
        audio while the model is still generating.

        Failure contract (composes with the blocking ladder + FallbackTTS):
          * primary quota-cooldown active → yield nothing; the caller falls
            back to ``_synthesize_one`` (which routes to the sibling bridge).
          * stream fails BEFORE the first audio byte → yield nothing (caller
            falls back, zero audio lost).
          * stream fails AFTER audio was yielded → stop with the partial
            audio; never re-synthesize (would replay the opening words).
          * RESOURCE_EXHAUSTED arms the same ``_quota_blocked_until`` cooldown
            the blocking path maintains.
        """
        log = logging.getLogger("jarvis.tts")
        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            return
        produced = False
        stream = None
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=self._model_name,
                contents=text,
                config=self._build_config(voice, language_code),
            )
            async for chunk in stream:
                for cand in chunk.candidates or []:
                    content = cand.content
                    for part in (content.parts if content else None) or []:
                        data = part.inline_data.data if part.inline_data else None
                        if data:
                            produced = True
                            yield data
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — degrade to the blocking ladder
            msg = str(exc)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                retry_s = _parse_retry_delay(msg)
                self._quota_blocked_until = time.monotonic() + retry_s
                log.warning(
                    "Gemini-TTS stream quota-blocked on %s (retry in %.0f min).",
                    self._model_name, retry_s / 60,
                )
            if produced:
                log.warning(
                    "Gemini-TTS stream broke mid-sentence (%s) — keeping the "
                    "partial audio, not re-synthesizing.",
                    exc.__class__.__name__,
                )
                return
            log.warning(
                "Gemini-TTS stream failed before first audio (%s) — blocking "
                "fallback will synthesize this sentence.",
                exc.__class__.__name__,
            )
        finally:
            # GeneratorExit (barge-in) bypasses both except clauses above —
            # this finally is the only deterministic release of the genai
            # HTTP stream. Awaiting inside a closing async generator's
            # finally is legal (PEP 525); only yielding is not.
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    def _synthesize_sync(
        self, text: str, voice: str, model: str | None = None,
        language_code: str | None = None,
    ) -> bytes:
        import logging
        log = logging.getLogger("jarvis.tts")
        assert self._client is not None
        target_model = model or self._model_name
        resp = self._client.models.generate_content(
            model=target_model,
            contents=text,
            config=self._build_config(voice, language_code),
        )
        # Robust against empty responses (safety filter, rate limit, etc.)
        if not resp.candidates:
            finish = "unknown"
            try:
                pf = resp.prompt_feedback
                finish = f"block_reason={pf.block_reason}"
            except Exception:  # noqa: BLE001
                pass
            log.warning("Gemini TTS returned no candidates (%s) — voice=%s text=%r",
                        finish, voice, text[:80])
            return b""
        cand = resp.candidates[0]
        if not cand.content or not cand.content.parts:
            log.warning("Gemini TTS candidate without parts — voice=%s text=%r",
                        voice, text[:80])
            return b""
        part = cand.content.parts[0]
        if not part.inline_data or not part.inline_data.data:
            log.warning("Gemini TTS part without inline_data — voice=%s text=%r",
                        voice, text[:80])
            return b""
        return part.inline_data.data

    def list_voices(self, language: str | None = None) -> list[str]:
        """30 prebuilt voices are language-agnostic — we return our whitelist."""
        return list(DEFAULT_VOICES)


def _split_sentences(text: str) -> list[str]:
    """Heuristic sentence splitter. Small overhead, notably better perceived latency."""
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


# ----------------------------------------------------------------------
# SAPI5 emergency fallback (Windows native, no quota)
# ----------------------------------------------------------------------

def _sapi5_synthesize(text: str, language_code: str = "de-DE") -> bytes:
    """Blocking SAPI5 call, returns raw PCM (22050Hz 16-bit mono).

    Called when the Gemini TTS call comes back empty (429 / safety block).
    Quality is mediocre, but: no quota, <100ms latency, the user ALWAYS hears something.
    """
    import logging
    log = logging.getLogger("jarvis.tts.sapi5")
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        log.warning("pywin32 not installed — SAPI5 fallback not available.")
        return b""

    # pywin32 braucht CoInitialize in jedem Thread neu
    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass

    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = voice.GetVoices()
        # Pick a voice per language
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
        log.warning("SAPI5 fallback failed: %s", exc)
        return b""
