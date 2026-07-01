"""TTS output sanity check: speaks a fixed sentence via the SAPI5 fallback.

Usage:
    python scripts/tts_output_sanity.py

Use this when you:
- Start the watchdog and hear nothing
- Aren't sure whether the right output device is active
- Want to know whether Windows TTS (Hedda) can play at all

This script bypasses Gemini entirely and directly uses the native Windows
SAPI5 fallback from `jarvis/plugins/tts/gemini_flash_tts.py`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


async def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from jarvis.audio.player import AudioPlayer
    from jarvis.core.protocols import AudioChunk
    from jarvis.plugins.tts.gemini_flash_tts import SAPI5_SAMPLE_RATE, _sapi5_synthesize

    text = (
        "Test eins zwei drei. Wenn du mich hörst, ist dein Audio-Output "  # i18n-allow
        "korrekt eingerichtet und Windows-TTS funktioniert."  # i18n-allow
    )

    print(f"[1] Starting SAPI5 synthesis: {text!r}")
    pcm = await asyncio.to_thread(_sapi5_synthesize, text, "de-DE")
    if not pcm:
        print("    !! SAPI5 returned no bytes. Check whether pywin32 is installed:")
        print("       pip install pywin32")
        return 1
    print(f"    OK: {len(pcm)} bytes PCM ({len(pcm) / 2 / SAPI5_SAMPLE_RATE:.2f}s)")

    print("[2] Playing audio on the system default output …")
    player = AudioPlayer()
    chunk = AudioChunk(
        pcm=pcm,
        sample_rate=SAPI5_SAMPLE_RATE,
        timestamp_ns=0,
        channels=1,
    )

    async def _gen():
        yield chunk

    await player.play_chunks(_gen())
    print("[3] Done. Did you hear the test sentence?")
    print()
    print("   - YES → TTS + audio path is fine. If Jarvis is still silent,")
    print("           the problem is in the Brain or the pipeline wiring.")
    print("   - NO  → the audio device is wrong. Windows sound settings:")
    print("           right-click the speaker icon → 'Choose audio output'.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
