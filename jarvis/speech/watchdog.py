"""Watchdog mode: the pipeline runs continuously and everything is logged.

Difference from `pipeline.py`:
- Logs go BOTH to the console AND to `./data/jarvis_watchdog.log`
- Debug WAVs (rolling-Whisper transcriptions) in `./data/wake_debug/*.wav`
- Heartbeat every 3 sec even when nothing happens
- On every wake, audio buffers are saved for later analysis

The user / I can check the log file after a test to see whether mic audio
is coming through, what Whisper transcribes, and whether wake triggers.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path


def _setup_logging(log_file: Path) -> None:
    """Logs go to both the file AND the console."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    # Console
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter(fmt))
    root.addHandler(ch)

    # File (append-Mode — mehrere Runs nacheinander ok)
    fh = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


async def _main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / "data"
    log_file = data_dir / "jarvis_watchdog.log"
    debug_dir = data_dir / "wake_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    os.environ["JARVIS_DEBUG_DIR"] = str(debug_dir)
    _setup_logging(log_file)
    _load_env()

    log = logging.getLogger("jarvis.watchdog")
    log.info("=" * 60)
    log.info("WATCHDOG-START — Pipeline mit voller Diagnostik")
    log.info("Log-Datei:   %s", log_file)
    log.info("WAV-Debug:   %s", debug_dir)
    log.info("=" * 60)

    from jarvis.brain.factory import build_default_brain
    from jarvis.core import config as cfg
    from jarvis.core.bus import EventBus
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
    from jarvis.plugins.wake.openwakeword_provider import (
        PRODUCTION_WAKE_THRESHOLD,
    )
    from jarvis.speech.pipeline import SpeechPipeline
    from jarvis.state.supervisor import Supervisor

    config = cfg.load_config()

    # Wire up event bus + supervisor — prerequisite for Orb integration
    bus = EventBus()
    supervisor = Supervisor(bus=bus)

    # Orb-Overlay als UI-Feedback: erscheint bei LISTENING, versteckt bei IDLE.
    # Start im Daemon-Thread (Tk-Mainloop) damit asyncio-Loop frei bleibt.
    try:
        from ui.orb.bus_bridge import OrbBusBridge
        from ui.orb.overlay import OrbOverlay
        orb = OrbOverlay(sticky=False, mic_reactive=False)
        orb.start_in_thread()
        bridge = OrbBusBridge(bus=bus, orb=orb)
        bridge.attach()
        log.info("Orb-Overlay + Bus-Bridge aktiv.")
    except Exception as exc:  # noqa: BLE001
        log.warning("Orb overlay failed to start (%s) — running without UI.", exc)

    # STT language from config (default "auto" = bilingual DE+EN auto-detect).
    stt_language = config.stt.language if config.stt.language not in ("", "auto") else None
    stt = FasterWhisperProvider(
        model=config.stt.model,
        device=config.stt.device,
        compute_type=config.stt.compute_type,
        language=stt_language,
    )
    from jarvis.plugins.tts import build_tts_from_config
    tts = build_tts_from_config(config.tts)
    brain = build_default_brain()

    # Output device from config.audio.output_device ("auto-headset" by default)
    # → resolved by AudioPlayer._resolve_output_device to the actual
    # headset index, so TTS doesn't play through the monitor speaker.
    output_device = config.audio.output_device or None
    _call_hk, _ptt_hk = config.trigger.resolve_hotkeys()
    pipeline = SpeechPipeline(
        call_hotkeys=_call_hk,
        ptt_hotkeys=_ptt_hk,
        hangup_hotkeys=(config.trigger.hotkey_hangup,),
        wake_keywords=("hey_jarvis",),
        # Single source of truth — see PRODUCTION_WAKE_THRESHOLD and the
        # data-driven reasoning in openwakeword_provider.py (BUG-009 episode 5,
        # 2026-05-24: the 0.06 over-correction made OWW fire on ambient speech).
        wake_threshold=PRODUCTION_WAKE_THRESHOLD,
        stt=stt,
        tts=tts,
        brain_callback=brain,
        enable_whisper_wake=True,
        # User-Mandat 2026-05-18: every turn requires a fresh wake. The Toml
        # field ``[trigger].single_turn_mode`` is the canonical source.
        continue_listening_after_response=not config.trigger.single_turn_mode,
        # ``session_idle_timeout_s`` <= 0 keeps a conversation session active
        # until a manual hangup; the 30 s default stays the safe baseline.
        idle_timeout_s=config.trigger.session_idle_timeout_s,
        bus=bus,
        supervisor=supervisor,
        input_device=config.audio.input_device or None,
        output_device=output_device,
    )
    log.info("Starting pipeline …")
    await pipeline.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nWatchdog stopped.")
