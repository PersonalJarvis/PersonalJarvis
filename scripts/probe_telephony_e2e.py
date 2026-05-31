"""In-process E2E probe for the telephony media-stream handler (DoD item 3).

Drives a synthetic Twilio call through ``TelephonyCallSession`` (the same class
the WS handler uses) and prints the transcript, Jarvis's response, and the
number of outbound mu-law frames produced. No real Twilio account, no public
tunnel, no microphone.

By default it uses deterministic FAKES for STT/Brain/TTS so it runs anywhere
with no API key. Pass ``--real`` to use the configured real STT/Brain/TTS
(needs provider keys + a 16 kHz WAV fixture for STT).

Usage:
    python scripts/probe_telephony_e2e.py
    python scripts/probe_telephony_e2e.py --real --wav path/to/utterance.wav
"""

from __future__ import annotations

import argparse
import asyncio
import math
import struct
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001, S110 - console encoding is best-effort
    pass

# Make `tests` importable for the fakes when run from the repo root.
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.telephony.audio import TWILIO_SAMPLE_RATE, pcm16_to_ulaw
from jarvis.telephony.session import TelephonyCallSession


def _ulaw_b64(amp: int, ms: int = 20, freq: int = 300) -> str:
    import base64

    n = TWILIO_SAMPLE_RATE * ms // 1000
    if amp == 0:
        pcm = b"\x00\x00" * n
    else:
        pcm = b"".join(
            struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / TWILIO_SAMPLE_RATE)))
            for i in range(n)
        )
    return base64.b64encode(pcm16_to_ulaw(pcm)).decode("ascii")


class _Collector:
    def __init__(self) -> None:
        self.media = 0
        self.marks = 0
        self.clears = 0

    async def send(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "media":
            self.media += 1
        elif ev == "mark":
            self.marks += 1
        elif ev == "clear":
            self.clears += 1


async def _run_fake() -> int:
    from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS

    sink = _Collector()
    transcript = "Wie spät ist es?"
    session = TelephonyCallSession(
        call_sid="PROBE",
        stream_sid="MZPROBE",
        send=sink.send,
        stt=FakeSTT([transcript]),
        brain=FakeBrain("Es ist genau vierzehn Uhr dreißig, alles im grünen Bereich."),
        tts=FakeTTS(ms_per_char=2),
        language_code="de-DE",
        greeting="Hier ist Jarvis.",
    )
    session._endpointer.silence_ms = 100
    session._endpointer.min_speech_ms = 60

    await session.speak_greeting()
    # lead silence -> speech -> trailing silence triggers the turn
    for _ in range(2):
        await session.handle_media(_ulaw_b64(0))
    for _ in range(8):
        await session.handle_media(_ulaw_b64(15000))
    for _ in range(12):
        await session.handle_media(_ulaw_b64(0))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if session.turns >= 1:
            break

    print("=== Telephony E2E probe (fakes) ===")
    print(f"Transcript : {transcript}")
    print("Response   : Es ist genau vierzehn Uhr dreißig, alles im grünen Bereich.")
    print(f"Turns      : {session.turns}")
    print(f"Outbound mu-law media frames : {sink.media}")
    print(f"Marks      : {sink.marks}  Clears: {sink.clears}")
    ok = session.turns >= 1 and sink.media > 0
    print("RESULT     :", "OK" if ok else "FAILED")
    return 0 if ok else 1


async def _run_real(wav_path: str) -> int:
    import wave

    from jarvis.brain.factory import build_default_brain
    from jarvis.core.config import load_config
    from jarvis.plugins.stt import build_stt_from_config
    from jarvis.plugins.tts import build_tts_from_config
    from jarvis.telephony.audio import resample_pcm16

    cfg = load_config()
    with wave.open(wav_path, "rb") as wf:
        rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    if rate != 16000:
        pcm = resample_pcm16(pcm, rate, 16000)

    stt = build_stt_from_config(cfg.stt)
    tts = build_tts_from_config(cfg.tts)
    brain = build_default_brain(bus=None, tier="router")

    sink = _Collector()
    session = TelephonyCallSession(
        call_sid="PROBE-REAL",
        stream_sid="MZREAL",
        send=sink.send,
        stt=stt,
        brain=brain,
        tts=tts,
        language_code=cfg.tts.language_code,
    )
    transcript = (await stt.transcribe_pcm(pcm, sample_rate=16000)).text
    await session._run_turn(pcm)
    print("=== Telephony E2E probe (real stack) ===")
    print(f"Transcript : {transcript}")
    print(f"Turns      : {session.turns}")
    print(f"Outbound mu-law media frames : {sink.media}")
    ok = sink.media > 0
    print("RESULT     :", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="Use the configured real stack")
    parser.add_argument("--wav", default="", help="16 kHz mono WAV for --real STT input")
    args = parser.parse_args()
    if args.real:
        if not args.wav:
            print("--real requires --wav <16kHz mono wav>", file=sys.stderr)
            return 2
        return asyncio.run(_run_real(args.wav))
    return asyncio.run(_run_fake())


if __name__ == "__main__":
    raise SystemExit(main())
