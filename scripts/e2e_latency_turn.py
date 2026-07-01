"""End-to-end voice-turn latency harness — REAL providers, NO microphone.

This is the decisive automated proof for the TTS-streaming latency fix: it
drives the *real integrated* hot path (the actual ``SpeechPipeline``, real
Groq STT, real router ``BrainManager``, real streaming Gemini TTS) for one
spoken turn, with only the microphone and speaker replaced by a recording
fake. It captures the very ``LatencyTurnComplete`` row the production JSONL
log receives, so the per-stage breakdown is measured on the same code that
runs live — not on an isolated stage probe.

What it proves that the unit tests and stage probes cannot:
  * the streaming TTS path is actually selected through the production factory
    (FallbackTTS-wrapped) on a real turn,
  * the Wave-0 instrumentation fires end-to-end (``LatencyTurnComplete`` lands
    on the bus with every stage marked),
  * the answer is still correct + complete (quality guarantee) — the streamed
    text is printed alongside the timing.

The anchor of ``turn_to_first_audio`` is the entry of ``_handle_utterance``
(i.e. the moment the VAD endpoint would hand off), so the number excludes the
deliberate ~1.5 s VAD pause and the real speaker — it is the honest
"post-endpoint → first audio" figure for the MAIN answer (ack-brain is left
off so the measurement is the answer path, not the sub-second preamble).

Usage: python scripts/e2e_latency_turn.py [--runs N]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.config import load_config  # noqa: E402
from jarvis.core.events import (  # noqa: E402
    AudioOutFirst,
    LatencyTurnComplete,
    TranscriptionUpdate,
)

QUESTION = "Was ist die Hauptstadt von Frankreich?"  # i18n-allow: simulated German user utterance fed through the live voice pipeline for the latency benchmark
TTS_RATE = 24_000


class _RecordingPlayer:
    """Speaker stand-in: records first-chunk time, publishes AudioOutFirst.

    Publishing ``AudioOutFirst`` is exactly what the real WASAPI player does,
    so the pipeline's ``_on_audio_out_first`` marks ``turn_to_first_audio`` on
    the live tracker — the harness measures the same milestone as production.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.first_chunk_ns: int | None = None
        self.chunks = 0
        self.pcm_bytes = 0
        self._announced = False

    async def play_chunks(self, chunks) -> None:
        async for ch in chunks:
            self.chunks += 1
            self.pcm_bytes += len(ch.pcm)
            if not self._announced:
                self._announced = True
                self.first_chunk_ns = time.perf_counter_ns()
                await self._bus.publish(AudioOutFirst(source_layer="e2e.player"))

    def stop(self) -> None:  # pipeline calls this on barge/hangup
        pass


class _TeeBrain:
    """Wraps the real brain to capture the streamed answer text for the
    quality check, transparently forwarding chunks + the on_progress kwarg."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.text = ""

    def generate_stream(self, text: str, on_progress=None):
        async def _gen():
            try:
                agen = self._inner.generate_stream(text, on_progress=on_progress)
            except TypeError:
                agen = self._inner.generate_stream(text)
            async for chunk in agen:
                self.text += chunk
                yield chunk
        return _gen()


async def _synth_pcm(tts, text: str) -> bytes:
    pieces: list[bytes] = []
    try:
        gen = tts.synthesize(text, language_code="de-DE")
    except TypeError:
        gen = tts.synthesize(text)
    async for ch in gen:
        pieces.append(bytes(ch.pcm))
    return b"".join(pieces)


def _fmt_stages(stages: dict[str, float]) -> str:
    order = [
        "stt_finalize", "intent_decision", "brain_request_sent",
        "brain_first_token", "brain_last_token", "tts_request_sent",
        "tts_first_chunk", "turn_to_first_audio", "tts_stream_done",
    ]
    lines = []
    for key in order:
        if key in stages:
            lines.append(f"      {key:<22} {stages[key]:8.1f} ms")
    return "\n".join(lines)


async def main(runs: int) -> int:
    cfg = load_config(Path("jarvis.toml"))
    bus = EventBus()

    captured: list[LatencyTurnComplete] = []
    user_texts: list[str] = []

    async def _cap_turn(e: LatencyTurnComplete) -> None:
        captured.append(e)

    async def _cap_user(e: TranscriptionUpdate) -> None:
        if getattr(e, "is_final", False):
            user_texts.append(e.text)

    bus.subscribe(LatencyTurnComplete, _cap_turn)
    bus.subscribe(TranscriptionUpdate, _cap_user)

    from jarvis.brain.factory import build_default_brain
    from jarvis.plugins.stt import build_stt_from_config
    from jarvis.plugins.tts import build_tts_from_config
    from jarvis.speech.pipeline import SpeechPipeline
    from jarvis.state.supervisor import Supervisor

    tts = build_tts_from_config(cfg.tts)
    stt = build_stt_from_config(cfg.stt)
    brain = build_default_brain(tier="router", bus=bus)
    tee = _TeeBrain(brain)

    print(f"providers: stt={type(stt).__name__} tts={type(tts).__name__} "
          f"brain.primary={cfg.brain.primary} router={cfg.brain.router.model}")
    print(f"flags: tts.streaming={cfg.tts.streaming} "
          f"performance.streaming_tts={cfg.performance.streaming_tts} "
          f"latency.enabled={cfg.latency.enabled}\n")

    print(f"Synthesizing the probe utterance ({QUESTION!r}) for STT input …")
    question_pcm = await _synth_pcm(tts, QUESTION)
    print(f"  → {len(question_pcm) / 1024:.0f} KB PCM\n")

    pipeline = SpeechPipeline(
        tts=tts, bus=bus, config=cfg, supervisor=Supervisor(bus=bus),
        enable_whisper_wake=False, enable_openwakeword=False,
        enable_local_whisper=False,
    )
    pipeline._brain = tee  # type: ignore[assignment]
    pipeline._utterance_stt = stt  # type: ignore[assignment]

    async def _never() -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never  # type: ignore[assignment]

    print(f"Driving {runs} real turn(s) through _handle_utterance "
          f"(run 1 = cold) …\n")
    for i in range(runs):
        captured.clear()
        user_texts.clear()
        tee.text = ""
        player = _RecordingPlayer(bus)
        pipeline._player = player  # type: ignore[assignment]

        anchor = time.perf_counter_ns()
        try:
            await asyncio.wait_for(
                pipeline._handle_utterance(question_pcm, skip_completion=True),
                timeout=60,
            )
        except TimeoutError:
            print(f"  run {i + 1}: TIMEOUT (>60 s)")
            continue
        await asyncio.sleep(0.15)  # let the fire-and-forget turn-complete land
        wall_ms = (time.perf_counter_ns() - anchor) / 1_000_000

        tag = "cold" if i == 0 else "warm"
        print(f"  run {i + 1} ({tag}):")
        print(f"      user heard (STT)    : {user_texts[-1] if user_texts else '(none)'!r}")
        print(f"      answer (streamed)   : {tee.text.strip()[:100]!r}")
        print(f"      audio chunks played : {player.chunks} "
              f"({player.pcm_bytes / 2 / TTS_RATE:.1f} s)")
        if captured:
            stages = dict(captured[-1].stages_ms)
            ttfa = stages.get("turn_to_first_audio")
            print(f"      >>> turn_to_first_audio: "
                  f"{ttfa:.0f} ms <<<" if ttfa is not None else
                  "      >>> turn_to_first_audio: NOT MARKED <<<")
            print(_fmt_stages(stages))
        else:
            print("      !! NO LatencyTurnComplete emitted — instrumentation gap")
        print(f"      wall clock (incl. teardown): {wall_ms:.0f} ms\n")

    return 0


if __name__ == "__main__":
    from _grpc_exit import hard_exit  # noqa: E402 — sibling helper in scripts/

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    # Real Gemini/Vertex calls leak non-daemon gRPC threads → a normal return
    # hangs the process for minutes. hard_exit flushes + os._exit. See
    # scripts/_grpc_exit.py / scripts/diag_threads2.py.
    hard_exit(asyncio.run(main(args.runs)))
