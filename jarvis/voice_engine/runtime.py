"""Load the engine's models from a ``configure`` message and prove they work.

``build_models`` turns the configuration into :class:`EngineModels`;
``selftest`` is the readiness proof — every selected voice speaks a phrase,
the recogniser transcribes it back and the LLM answers one prompt — so
"ready" always means a real inference in this process, never files on disk
(``docs/local-live-voice-rebuild.md`` section 4.9).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from jarvis.voice_engine import models as store
from jarvis.voice_engine.audio import STT_RATE, resample, silence
from jarvis.voice_engine.bench.stats import error_rates
from jarvis.voice_engine.engine import EngineConfig, EngineModels

Progress = Callable[[str, float], None]

SELFTEST_PHRASES = {
    "de": "Hallo, ich bin bereit.",  # i18n-allow: German readiness phrase
    "en": "Hello, I am ready.",
}


@dataclass
class RuntimeConfig:
    languages: list[str] = field(default_factory=lambda: ["de", "en"])
    tts: str = "pocket"
    tts_options: dict[str, Any] = field(default_factory=dict)
    llm_model: str = "qwen3.5:4b-voice-8k"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_num_ctx: int = 8192
    llm_keep_alive: str = "30m"
    # Low, like the bench's tool runs: a voice turn wants the same tool for the
    # same request, not variety (plan section 4.6).
    llm_temperature: float = 0.2
    engine: EngineConfig = field(default_factory=EngineConfig)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> RuntimeConfig:
        llm = message.get("llm") or {}
        turn = message.get("turn") or {}
        engine = EngineConfig(**{k: v for k, v in turn.items() if hasattr(EngineConfig, k)})
        return cls(
            languages=[str(x) for x in message.get("languages") or ["de", "en"]],
            tts=str(message.get("tts") or "pocket"),
            tts_options=dict(message.get("tts_options") or {}),
            llm_model=str(llm.get("model") or cls.llm_model),
            llm_base_url=str(llm.get("base_url") or cls.llm_base_url),
            llm_num_ctx=int(llm.get("num_ctx") or cls.llm_num_ctx),
            llm_keep_alive=str(llm.get("keep_alive") or cls.llm_keep_alive),
            llm_temperature=float(llm.get("temperature", cls.llm_temperature)),
            engine=engine,
        )


def build_models(config: RuntimeConfig, progress: Progress) -> tuple[EngineModels, dict[str, Any]]:
    """Load every component; returns the models and a description of what loaded."""
    from jarvis.voice_engine.llm import OllamaChat  # noqa: PLC0415
    from jarvis.voice_engine.stt import ParakeetStt  # noqa: PLC0415
    from jarvis.voice_engine.tts import load_tts  # noqa: PLC0415
    from jarvis.voice_engine.turn import SmartTurn  # noqa: PLC0415
    from jarvis.voice_engine.vad import SileroVad  # noqa: PLC0415

    timings: dict[str, float] = {}
    errors: dict[str, str] = {}
    missing = [n for n in ("silero-vad-v6", "smart-turn-v3.2", "parakeet-tdt-0.6b-v3-int8")
               if not store.is_present(n)]
    if missing:
        raise RuntimeError(f"models not installed: {', '.join(missing)}")

    def timed(name: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        value = fn()
        timings[name] = round(time.perf_counter() - started, 2)
        return value

    progress("vad", 0.05)
    vad_path = store.model_path("silero-vad-v6")
    timed("vad", lambda: SileroVad(vad_path))
    progress("turn", 0.1)
    turn = timed("turn", lambda: SmartTurn(store.model_path("smart-turn-v3.2")))
    progress("stt", 0.2)
    stt = timed("stt", lambda: ParakeetStt(store.model_path("parakeet-tdt-0.6b-v3-int8")))

    voices: dict[str, Any] = {}
    voice_kind: dict[str, str] = {}
    for index, language in enumerate(config.languages):
        progress(f"tts:{language}", 0.35 + 0.35 * index / max(1, len(config.languages)))
        try:
            voices[language] = timed(f"tts:{config.tts}:{language}",
                                     lambda lang=language: load_tts(config.tts, lang,
                                                                    **config.tts_options))
            voice_kind[language] = config.tts
        except Exception as exc:  # noqa: BLE001 - the floor voice takes over
            if config.tts == "piper":
                raise
            errors[f"tts:{config.tts}:{language}"] = str(exc)[:200]
            voices[language] = timed(f"tts:piper:{language}",
                                     lambda lang=language: load_tts("piper", lang))
            voice_kind[language] = "piper"

    def tts_for(language: str) -> Any:
        if language in voices:
            return voices[language]
        return voices[config.languages[0]]

    progress("llm", 0.8)
    llm = OllamaChat(config.llm_model, base_url=config.llm_base_url, num_ctx=config.llm_num_ctx,
                     keep_alive=config.llm_keep_alive, temperature=config.llm_temperature)
    timings["llm_load"] = round(llm.warm(), 2)
    models = EngineModels(
        vad_factory=lambda: SileroVad(vad_path), turn=turn, stt=stt, tts_for=tts_for, llm=llm
    )
    progress("loaded", 0.9)
    return models, {"load_s": timings, "voices": voice_kind, "llm": config.llm_model,
                    "errors": errors}


def selftest(models: EngineModels, languages: list[str]) -> dict[str, Any]:
    """Speak, hear and answer once per language. ``ok`` is the readiness verdict."""
    report: dict[str, Any] = {"ok": True, "languages": {}}
    pad = silence(0.2, STT_RATE)
    for language in languages:
        phrase = SELFTEST_PHRASES.get(language, SELFTEST_PHRASES["en"])
        tts = models.tts_for(language)
        started = time.perf_counter()
        with models.lock(f"tts:{getattr(tts, 'name', 'tts')}:{language}"):
            audio = np.concatenate([np.asarray(c, dtype=np.float32) for c in tts.stream(phrase)])
        synth_ms = (time.perf_counter() - started) * 1000.0
        heard_started = time.perf_counter()
        with models.lock("stt"):
            heard = models.stt.transcribe(
                np.concatenate([pad, resample(audio, tts.sample_rate, STT_RATE), pad])
            )
        cer, _ = error_rates(phrase, heard)
        passed = cer <= 0.25
        report["languages"][language] = {
            "voice": getattr(tts, "name", "?"), "heard": heard, "cer": round(cer, 3),
            "synth_ms": round(synth_ms),
            "stt_ms": round((time.perf_counter() - heard_started) * 1000),
            "ok": passed,
        }
        report["ok"] = report["ok"] and passed
    started = time.perf_counter()
    answer = models.llm.chat([{"role": "user", "content": "Reply with the single word: ready."}])
    report["llm"] = {"ok": not answer.error and bool(answer.text.strip()),
                     "error": answer.error, "ms": round((time.perf_counter() - started) * 1000)}
    report["ok"] = report["ok"] and report["llm"]["ok"]
    return report
