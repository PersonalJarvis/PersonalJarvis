#!/usr/bin/env bash
# Linux (WSL) verification for the vosk_kws wake engine: same model, same
# real captured WAVs, same provider code as the Windows run.
set -u
REPO="/mnt/c/Users/Administrator/Desktop/Personal Jarvis"
VENV="$HOME/vosk-verify"

python3 -m venv "$VENV" 2>/dev/null || true
"$VENV/bin/pip" install --quiet vosk numpy pydantic 2>&1 | tail -1
"$VENV/bin/python" - <<'EOF'
import asyncio, json, sys, time, wave
from pathlib import Path
import numpy as np

REPO = Path("/mnt/c/Users/Administrator/Desktop/Personal Jarvis")
sys.path.insert(0, str(REPO))

import vosk
print(f"linux python {sys.version.split()[0]}, vosk OK", flush=True)

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider, sound_confirm
from jarvis.speech.wake_phrase import compile_wake_matcher

# pure-python confirm contract, identical on linux
assert sound_confirm("hey room", "Hey Ruben") is True
assert sound_confirm("vielen dank", "Hey Ruben") is False  # i18n-allow: utterance under test
print("sound_confirm contract OK", flush=True)

MODEL = str(REPO / "data/wake_models/vosk/de/vosk-model-small-de-0.15")
SCRATCH = Path("/mnt/c/Users/Administrator/AppData/Local/Temp/claude/C--Users-Administrator-Desktop-Personal-Jarvis/96295115-53e2-42ad-a2ab-8ff2ab5bbf36/scratchpad")
JUDGE = json.loads((SCRATCH / "judge_transcripts.json").read_text(encoding="utf-8"))
FIXDIR = REPO / "data/wake_debug"

PHRASES = ["Hey Ruben", "Hey Luca"]
MATCHERS = {p: compile_wake_matcher(p) for p in PHRASES}
pos = {p: [] for p in PHRASES}
neg = []
for name, text in JUDGE.items():
    hit = next((p for p, m in MATCHERS.items() if m.search(text)), None)
    f = str(FIXDIR / name)
    if hit:
        pos[hit].append(f)
    elif not any(c in text.lower() for c in ("nova", "nico", "niko", "ruben", "luca")):
        neg.append(f)
rng = np.random.default_rng(0)
neg = list(rng.choice(neg, 60, replace=False))

def load(p):
    with wave.open(p, "rb") as wf:
        s = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    pk = float(np.max(np.abs(s)))
    if pk > 1e-6:
        s = np.clip(s * min(0.7079 / pk, 100.0), -1, 1)
    return (s * 32767.0).astype(np.int16).tobytes()

async def stream_detect(provider, pcm):
    silence = b"\x00\x00" * 1600
    step = 3200
    chunks = [silence] * 4 + [pcm[i:i + step] for i in range(0, len(pcm), step)] + [silence] * 12
    async def _iter():
        for c in chunks:
            yield AudioChunk(pcm=c, sample_rate=16000, timestamp_ns=0)
    async for _kw in provider.detect(_iter()):
        return True
    return False

async def main():
    t0 = time.perf_counter()
    for p in PHRASES:
        prov = VoskKwsProvider(p, MODEL, keyword="k", cooldown_s=0.0)
        await prov.start()
        ok = 0
        for f in pos[p]:
            ok += await stream_detect(prov, load(f))
        print(f"  {p:9} linux E2E recall {ok}/{len(pos[p])} ({100*ok/len(pos[p]):.0f}%)", flush=True)
    prov = VoskKwsProvider("Hey Nova", MODEL, keyword="k", cooldown_s=0.0)
    await prov.start()
    fa = sum([await stream_detect(prov, load(f)) for f in neg])
    print(f"  FA ambient {fa}/{len(neg)} ({100*fa/len(neg):.1f}%)", flush=True)
    print(f"LINUX VERIFY DONE in {time.perf_counter()-t0:.0f}s", flush=True)

asyncio.run(main())
EOF
