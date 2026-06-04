"""End-to-End-Test: Brain → TTS → Speaker OHNE Voice-Pipeline.

Simuliert was der Watchdog machen würde: nimmt User-Text, ruft Brain,
ruft TTS, spielt Audio. Wenn Gemini 429 → SAPI5-Fallback sollte greifen.

Usage:
    python scripts/tts_brain_endtoend.py "Hallo Jarvis, sag etwas"
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path


async def main(user_text: str) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from jarvis.audio.player import AudioPlayer
    from jarvis.brain.factory import build_default_brain
    from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

    print(f"[1] Brain starten...")
    brain = build_default_brain()
    print(f"    Provider: {brain.active_provider}")
    print(f"    Tools: {sorted(brain._tools.keys())}")

    print(f"[2] Brain: {user_text!r}")
    t0 = time.perf_counter()
    response = await asyncio.wait_for(brain.generate(user_text), timeout=30)
    dt = time.perf_counter() - t0
    print(f"    Response ({dt:.2f}s): {response[:200]}")
    if not response:
        print("    !! LEER — Brain hat nichts zurückgegeben.")
        return 1

    print(f"[3] TTS starten...")
    tts = GeminiFlashTTS(default_voice="Charon", language_code="de-DE")

    print(f"[4] Audio ausgeben...")
    player = AudioPlayer()
    t1 = time.perf_counter()
    try:
        chunks = tts.synthesize(response, language_code="de-DE")
        await player.play_chunks(chunks)
        dt2 = time.perf_counter() - t1
        print(f"    OK ({dt2:.2f}s) — User sollte das gehört haben.")
    except Exception as exc:
        print(f"    !! TTS-Error: {type(exc).__name__}: {exc}")
        return 1

    await brain._brain_cache[list(brain._brain_cache.keys())[0]].close() if hasattr(
        list(brain._brain_cache.values())[0] if brain._brain_cache else None, "close") else None
    return 0


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "Sag kurz Hallo."
    sys.exit(asyncio.run(main(text)))
