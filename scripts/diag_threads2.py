"""Diagnostic 2: do the real API CALLS (not just construction) leave a
non-daemon thread that blocks process exit? Tests TTS (Vertex/gRPC) and the
brain (Gemini) separately. Hard-exits at the end so it cannot hang itself.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.config import load_config  # noqa: E402

cfg = load_config(Path("jarvis.toml"))


def _dump(label: str) -> None:
    alive = [t for t in threading.enumerate() if t is not threading.main_thread()]
    nondaemon = [t for t in alive if not t.daemon]
    print(f"\n[{label}] {len(alive)} non-main threads, "
          f"{len(nondaemon)} NON-DAEMON (block exit):", flush=True)
    for t in alive:
        flag = "daemon" if t.daemon else "**BLOCKS-EXIT**"
        print(f"   {flag:14} {t.name!r}", flush=True)


async def main() -> None:
    from jarvis.plugins.tts import build_tts_from_config
    tts = build_tts_from_config(cfg.tts)
    t0 = time.perf_counter()
    try:
        async for _ in tts.synthesize("Hallo, Test.", language_code="de-DE"):
            pass
        print(f"TTS call done in {time.perf_counter() - t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"TTS call failed: {exc}", flush=True)
    _dump("after real TTS call (Vertex/gRPC)")

    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain(tier="router", bus=EventBus())
    t1 = time.perf_counter()
    try:
        async for _ in brain.generate_stream("hallo"):
            pass
        print(f"\nbrain call done in {time.perf_counter() - t1:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\nbrain call failed: {exc}", flush=True)
    _dump("after real brain call (Gemini)")


asyncio.run(main())
print("\n=> any **BLOCKS-EXIT** thread above is why the harness hung ~28 min "
      "after finishing its work.", flush=True)
sys.stdout.flush()
os._exit(0)
