"""Diagnostic: what keeps a brain/pipeline-building process from exiting?

Builds the same heavy objects the latency scripts build, then enumerates the
live threads — flagging NON-DAEMON ones, which are exactly what blocks the
interpreter from exiting after main() returns (the 28-min "hung shell"). Ends
with os._exit(0) so the diagnostic itself can never hang.
"""
from __future__ import annotations

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
          f"{len(nondaemon)} NON-DAEMON:", flush=True)
    for t in alive:
        flag = "DAEMON " if t.daemon else "**BLOCKS-EXIT**"
        tgt = getattr(t, "_target", None)
        mod = getattr(tgt, "__module__", "?") if tgt else "?"
        print(f"   {flag} {t.name!r}  target={mod}", flush=True)


t0 = time.perf_counter()
from jarvis.brain.factory import build_default_brain  # noqa: E402

bus = EventBus()
brain = build_default_brain(tier="router", bus=bus)
print(f"brain built in {time.perf_counter() - t0:.1f}s", flush=True)
_dump("after build_default_brain")

t1 = time.perf_counter()
from jarvis.plugins.tts import build_tts_from_config  # noqa: E402
from jarvis.speech.pipeline import SpeechPipeline  # noqa: E402
from jarvis.state.supervisor import Supervisor  # noqa: E402

tts = build_tts_from_config(cfg.tts)
pipe = SpeechPipeline(
    tts=tts, bus=bus, config=cfg, supervisor=Supervisor(bus=bus),
    enable_whisper_wake=False, enable_openwakeword=False,
    enable_local_whisper=False,
)
print(f"\npipeline built in {time.perf_counter() - t1:.1f}s", flush=True)
_dump("after SpeechPipeline ctor")

print("\n=> without os._exit, the **BLOCKS-EXIT** threads above would hang the "
      "process here until they finish (they never do).", flush=True)
sys.stdout.flush()
os._exit(0)
