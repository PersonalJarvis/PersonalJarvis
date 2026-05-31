"""TTS-Output-Sanity-Check: spricht einen festen Satz via SAPI5-Fallback.

Benutzung:
    python scripts/tts_output_sanity.py

Nutze das wenn du:
- Watchdog startest und nichts hörst
- Nicht sicher bist ob das richtige Output-Device aktiv ist
- Wissen willst ob Windows-TTS (Hedda) überhaupt spielbar ist

Das Script umgeht Gemini komplett und nutzt direkt den Windows-nativen
SAPI5-Fallback aus `jarvis/plugins/tts/gemini_flash_tts.py`.
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
        "Test eins zwei drei. Wenn du mich hörst, ist dein Audio-Output "
        "korrekt eingerichtet und Windows-TTS funktioniert."
    )

    print(f"[1] SAPI5-Synthese starten: {text!r}")
    pcm = await asyncio.to_thread(_sapi5_synthesize, text, "de-DE")
    if not pcm:
        print("    !! SAPI5 lieferte keine Bytes. Prüfe ob pywin32 installiert ist:")
        print("       pip install pywin32")
        return 1
    print(f"    OK: {len(pcm)} bytes PCM ({len(pcm) / 2 / SAPI5_SAMPLE_RATE:.2f}s)")

    print("[2] Audio abspielen auf System-Default-Output …")
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
    print("[3] Fertig. Hast du den Test-Satz gehört?")
    print()
    print("   - JA  → TTS + Audio-Pfad stimmt. Wenn Jarvis trotzdem stumm,")
    print("           liegt's am Brain oder an der Pipeline-Verdrahtung.")
    print("   - NEIN → Audio-Device ist falsch. Windows-Sound-Einstellungen:")
    print("           Rechtsklick Lautsprecher-Icon → 'Audioausgabe auswählen'.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
