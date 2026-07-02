"""Wake-word benchmark harness — measures the stt_match wake path offline.

Reproducible evidence for the 2026-07-02 wake reliability/latency work: no
live microphone, no network, deterministic fixture selection. It exercises the
SAME production code the app runs (``build_wake_whisper`` construction,
``RollingWhisperWake`` gates/gain/matcher) against real captured audio from
``data/wake_debug/`` (1.8 s / 16 kHz mono int16 windows, filenames embed the
transcript heard at capture time).

Fixture classes (word-boundary token match on the filename transcript):
- ``pos_full``   — windows containing prefix + core ("Hey Nico ...")   -> must fire
- ``neg_bare``   — core word WITHOUT prefix ("Das war wahrscheinlich
                   Nico") — the RC5(a) false-activation class          -> must stay silent
- ``neg_ambient``— speech without the core word (rms >= 0.01)          -> must stay silent
- ``neg_quiet``  — low-level noise/breath (0.003 <= rms < 0.01)        -> must stay silent
- ``neg_silence``— synthesized near-silence windows                    -> must stay silent

Modes:
- ``window`` (default): per-window micro-benchmark. Replicates the poll loop's
  gates + peak-normalization gain, transcribes each window directly, applies
  the production reliability gates + matcher. Reports recall / false accepts /
  transcribe-time stats / cold-start cost. Fast — used for the config matrix.
- ``stream``: end-to-end wall-clock benchmark. Builds composite audio
  (lead-in + positive + tail), streams it through ``RollingWhisperWake.
  detect()`` in real time and measures speech-end -> trigger latency.
- ``stress``: ``stream`` while CPU-burner threads emulate the boot storm;
  counts wedge recoveries (RC1 reproduction).

Examples:
    python scripts/wake_bench.py --mode window
    python scripts/wake_bench.py --mode window --model tiny --bias off
    python scripts/wake_bench.py --mode stream --limit 8
    python scripts/wake_bench.py --mode stress --burners 8 --limit 6
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jarvis.core.protocols import AudioChunk  # noqa: E402
from jarvis.speech.rolling_whisper_wake import (  # noqa: E402
    RollingWhisperWake,
    _reliable_wake_transcript,
)
from jarvis.speech.wake_phrase import compile_wake_matcher  # noqa: E402

SAMPLE_RATE = 16_000
WAKE_DEBUG_DIR = REPO_ROOT / "data" / "wake_debug"

# Production gate / gain constants (mirror RollingWhisperWake defaults).
MIN_RMS = 0.003
MIN_PEAK = 0.008
TARGET_PEAK = 10.0 ** (-3.0 / 20.0)   # -3 dBFS
MAX_GAIN = 10.0 ** (40.0 / 20.0)      # 40 dB
MIN_WAKE_CONFIDENCE = 0.22
MAX_NO_SPEECH_PROB = 0.6

_PREFIX_TOKENS = {"hey", "hi", "hallo", "he"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _tokens(name: str) -> list[str]:
    stem = re.sub(r"^wake_\d+_rms[0-9.]+_", "", Path(name).stem)
    return [t.lower() for t in stem.split("_") if t]


def _rms_from_name(name: str) -> float:
    m = re.search(r"rms([0-9.]+)", name)
    return float(m.group(1)) if m else -1.0


def _has_core(tokens: list[str], core_res: list[re.Pattern[str]]) -> bool:
    return any(rx.fullmatch(t) for t in tokens for rx in core_res)


@dataclass
class FixtureSet:
    pos_full: list[Path] = field(default_factory=list)
    neg_bare: list[Path] = field(default_factory=list)
    neg_ambient: list[Path] = field(default_factory=list)
    neg_quiet: list[Path] = field(default_factory=list)


def discover_fixtures(
    core_variants: list[str], *, neg_cap: int, seed: int = 42
) -> FixtureSet:
    """Classify data/wake_debug WAVs by their filename transcript."""
    core_res = [re.compile(v, re.I) for v in core_variants]
    fx = FixtureSet()
    ambient_pool: list[Path] = []
    quiet_pool: list[Path] = []
    for entry in sorted(WAKE_DEBUG_DIR.iterdir()):
        if entry.suffix != ".wav":
            continue
        toks = _tokens(entry.name)
        if _has_core(toks, core_res):
            if any(t in _PREFIX_TOKENS for t in toks):
                fx.pos_full.append(entry)
            else:
                fx.neg_bare.append(entry)
            continue
        r = _rms_from_name(entry.name)
        if r >= 0.01:
            ambient_pool.append(entry)
        elif 0.003 <= r < 0.01:
            quiet_pool.append(entry)
    rng = random.Random(seed)
    fx.neg_ambient = sorted(rng.sample(ambient_pool, min(neg_cap, len(ambient_pool))))
    fx.neg_quiet = sorted(rng.sample(quiet_pool, min(neg_cap // 2, len(quiet_pool))))
    fx.neg_bare = fx.neg_bare[: neg_cap]
    return fx


def load_wav(path: Path) -> np.ndarray:
    """Return float32 samples in [-1, 1] at 16 kHz mono."""
    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE, path
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def scale_to_peak_dbfs(audio: np.ndarray, dbfs: float) -> np.ndarray:
    peak = float(np.max(np.abs(audio)))
    if peak <= 1e-9:
        return audio
    return audio * (10.0 ** (dbfs / 20.0) / peak)


def synth_silence_windows(n: int, seed: int = 42) -> list[np.ndarray]:
    """Near-silence with a hint of hiss — must be gated or stay unmatched."""
    rng = np.random.default_rng(seed)
    return [
        rng.normal(0.0, 0.002, int(1.8 * SAMPLE_RATE)).astype(np.float32)
        for _ in range(n)
    ]


def speech_end_s(audio: np.ndarray, threshold: float = 0.02) -> float:
    """Offset (s) of the last sample above ``threshold`` — end of speech."""
    idx = np.nonzero(np.abs(audio) > threshold)[0]
    return float(idx[-1] / SAMPLE_RATE) if len(idx) else len(audio) / SAMPLE_RATE


# ---------------------------------------------------------------------------
# Wake model construction (production path or explicit matrix cell)
# ---------------------------------------------------------------------------

def build_model(args: argparse.Namespace, phrase: str) -> Any:
    """Build the wake Whisper exactly like production (or a matrix variant)."""
    bias_phrase = None if args.bias == "off" else phrase
    if args.production:
        from jarvis.plugins.stt import build_wake_whisper

        cfg = SimpleNamespace(
            wake_model=args.model, wake_device="cpu",
            wake_compute_type="int8", wake_high_accuracy=False,
        )
        return build_wake_whisper(
            cfg, language=None, wake_phrase=bias_phrase,
            cuda_available=False, fast_first=True,
        )
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    return FasterWhisperProvider(
        model=args.model, device="cpu", compute_type="int8",
        language=None, initial_prompt=bias_phrase,
        beam_size=1, cpu_threads=args.threads,
    )


# ---------------------------------------------------------------------------
# Window mode (per-window recall / false accepts / speed)
# ---------------------------------------------------------------------------

def apply_poll_loop_gain(audio: np.ndarray) -> bytes:
    """Replicate the poll loop's peak normalization + int16 conversion."""
    peak = float(np.max(np.abs(audio)))
    gain = min(TARGET_PEAK / peak, MAX_GAIN) if peak > 1e-6 else 1.0
    return (np.clip(audio * gain, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


@dataclass
class WindowResult:
    name: str
    cls: str
    gated: bool = False
    transcribe_ms: float = 0.0
    text: str = ""
    confidence: float = 0.0
    reliable: bool = False
    matched: bool = False


async def run_window_case(
    stt: Any, matcher: Any, audio: np.ndarray, name: str, cls: str,
    language: str | None,
) -> WindowResult:
    res = WindowResult(name=name, cls=cls)
    rms = float(np.sqrt(np.mean(audio * audio) + 1e-12))
    peak = float(np.max(np.abs(audio)))
    if rms < MIN_RMS or peak < MIN_PEAK:
        res.gated = True
        return res
    pcm = apply_poll_loop_gain(audio)
    t0 = time.perf_counter()
    transcript = await stt.transcribe_pcm(pcm, language=language)
    res.transcribe_ms = (time.perf_counter() - t0) * 1000.0
    res.text = (transcript.text or "").strip()
    res.confidence = float(getattr(transcript, "confidence", 0.0) or 0.0)
    res.reliable = bool(res.text) and _reliable_wake_transcript(
        transcript,
        min_confidence=MIN_WAKE_CONFIDENCE,
        max_no_speech_prob=MAX_NO_SPEECH_PROB,
    )
    res.matched = res.reliable and matcher.search(res.text) is not None
    return res


async def run_window_mode(args: argparse.Namespace) -> dict:
    matcher = compile_wake_matcher(args.phrase, fuzzy_ratio=0.8)
    core_variants = [v.strip() for v in args.core_variants.split(",")]
    fx = discover_fixtures(core_variants, neg_cap=args.neg_cap)
    stt = build_model(args, args.phrase)
    lang = None if args.language in ("", "auto", "none") else args.language

    print(
        f"fixtures: pos_full={len(fx.pos_full)} neg_bare={len(fx.neg_bare)} "
        f"neg_ambient={len(fx.neg_ambient)} neg_quiet={len(fx.neg_quiet)}"
    )

    cases: list[tuple[str, str, np.ndarray]] = []
    limit = args.limit or len(fx.pos_full)
    volumes = [float(v) for v in args.volumes.split(",") if v.strip()]
    for p in fx.pos_full[:limit]:
        audio = load_wav(p)
        cases.append((p.name, "pos_orig", audio))
        for db in volumes:
            cases.append((f"{p.name}@{db:g}dB", f"pos@{db:g}dB",
                          scale_to_peak_dbfs(audio, db)))
    for p in fx.neg_bare:
        cases.append((p.name, "neg_bare", load_wav(p)))
    for p in fx.neg_ambient:
        cases.append((p.name, "neg_ambient", load_wav(p)))
    for p in fx.neg_quiet:
        cases.append((p.name, "neg_quiet", load_wav(p)))
    for i, audio in enumerate(synth_silence_windows(10)):
        cases.append((f"silence_{i}", "neg_silence", audio))

    results: list[WindowResult] = []
    cold_ms: float | None = None
    for name, cls, audio in cases:
        r = await run_window_case(stt, matcher, audio, name, cls, lang)
        if cold_ms is None and not r.gated:
            cold_ms = r.transcribe_ms
        results.append(r)
        if args.verbose and (r.matched or cls.startswith("pos")):
            print(f"  [{cls:>12}] matched={r.matched!s:>5} "
                  f"{r.transcribe_ms:7.0f}ms conf={r.confidence:.2f} "
                  f"text={r.text[:60]!r} ({name[:50]})")

    summary: dict[str, Any] = {"mode": "window", "config": _config_str(args),
                               "cold_first_transcribe_ms": cold_ms}
    by_cls: dict[str, list[WindowResult]] = {}
    for r in results:
        by_cls.setdefault(r.cls, []).append(r)
    times = [r.transcribe_ms for r in results if not r.gated]
    summary["transcribe_ms"] = {
        "n": len(times),
        "mean": round(statistics.mean(times), 1) if times else None,
        "median": round(statistics.median(times), 1) if times else None,
        "p95": round(np.percentile(times, 95), 1) if times else None,
    }
    print(f"\n== {summary['config']} ==")
    print(f"cold first transcribe: {cold_ms:.0f} ms" if cold_ms else "all gated?!")
    print(f"warm transcribe: median={summary['transcribe_ms']['median']} ms "
          f"mean={summary['transcribe_ms']['mean']} ms "
          f"p95={summary['transcribe_ms']['p95']} ms (n={len(times)})")
    for cls in sorted(by_cls):
        rs = by_cls[cls]
        matched = sum(r.matched for r in rs)
        gated = sum(r.gated for r in rs)
        rate = matched / len(rs) if rs else 0.0
        kind = "recall" if cls.startswith("pos") else "FALSE-ACCEPT"
        print(f"  {cls:>12}: {kind}={rate:6.1%} ({matched}/{len(rs)}) gated={gated}")
        summary[cls] = {"n": len(rs), "matched": matched, "gated": gated,
                        "rate": round(rate, 4)}
    return summary


# ---------------------------------------------------------------------------
# Stream mode (end-to-end latency through RollingWhisperWake)
# ---------------------------------------------------------------------------

async def _stream_chunks(audio: np.ndarray, chunk_ms: int = 100):
    """Yield AudioChunks at real-time pace."""
    step = int(SAMPLE_RATE * chunk_ms / 1000)
    for i in range(0, len(audio), step):
        seg = audio[i : i + step]
        pcm = (np.clip(seg, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        yield AudioChunk(pcm=pcm, sample_rate=SAMPLE_RATE,
                         timestamp_ns=time.time_ns())
        await asyncio.sleep(chunk_ms / 1000.0)


async def run_stream_mode(args: argparse.Namespace) -> dict:
    matcher = compile_wake_matcher(args.phrase, fuzzy_ratio=0.8)
    core_variants = [v.strip() for v in args.core_variants.split(",")]
    fx = discover_fixtures(core_variants, neg_cap=args.neg_cap)
    stt = build_model(args, args.phrase)
    lang = args.language if args.language not in ("", "auto", "none") else None

    # Count recover() calls (wedge events) via a counting shim.
    recover_calls = {"n": 0}
    orig_recover = getattr(stt, "recover", None)
    if callable(orig_recover):
        def counting_recover() -> None:
            recover_calls["n"] += 1
            orig_recover()
        stt.recover = counting_recover  # type: ignore[attr-defined]

    quiet_pool = [load_wav(p) for p in fx.neg_quiet[:6]] or synth_silence_windows(4)

    burners: list[threading.Thread] = []
    stop_burn = threading.Event()
    if args.burners:
        def _burn() -> None:
            a = np.random.default_rng(1).normal(size=(220, 220))
            while not stop_burn.is_set():
                a = a @ a.T
                a /= max(1e-9, float(np.max(np.abs(a))))
        burners = [threading.Thread(target=_burn, daemon=True)
                   for _ in range(args.burners)]
        for b in burners:
            b.start()
        print(f"stress: {args.burners} CPU-burner threads running")

    latencies: list[float] = []
    misses = 0
    limit = args.limit or len(fx.pos_full)
    try:
        for p in fx.pos_full[:limit]:
            pos = load_wav(p)
            lead = np.concatenate([random.Random(7).choice(quiet_pool)
                                   for _ in range(2)])
            tail = np.concatenate([random.Random(9).choice(quiet_pool)
                                   for _ in range(4)])
            composite = np.concatenate([lead, pos, tail]).astype(np.float32)
            word_end_s = len(lead) / SAMPLE_RATE + speech_end_s(pos)

            wake = RollingWhisperWake(stt, pattern=matcher, language=lang)
            t_start = time.perf_counter()
            fired_at: float | None = None

            async def _detect(w=wake, comp=composite):
                async for _kw in w.detect(_stream_chunks(comp)):
                    return time.perf_counter()
                return None

            try:
                fired_at = await asyncio.wait_for(
                    _detect(), timeout=len(composite) / SAMPLE_RATE + 10.0
                )
            except TimeoutError:
                fired_at = None
            if fired_at is None:
                misses += 1
                print(f"  MISS  {p.name[:60]}")
            else:
                lat = fired_at - t_start - word_end_s
                latencies.append(lat)
                print(f"  fired {lat*1000:6.0f} ms after word end  ({p.name[:55]})")
    finally:
        stop_burn.set()

    summary = {
        "mode": "stress" if args.burners else "stream",
        "config": _config_str(args),
        "n": len(latencies) + misses,
        "misses": misses,
        "recover_calls": recover_calls["n"],
        "latency_ms": {
            "median": round(statistics.median(latencies) * 1000)
            if latencies else None,
            "p95": round(float(np.percentile(latencies, 95)) * 1000)
            if latencies else None,
            "max": round(max(latencies) * 1000) if latencies else None,
        },
    }
    print(f"\n== {summary['config']} ({summary['mode']}) ==")
    print(f"hits={len(latencies)}/{summary['n']} misses={misses} "
          f"wedge-recovers={recover_calls['n']}")
    if latencies:
        print(f"word-end -> trigger latency: median={summary['latency_ms']['median']} ms "
              f"p95={summary['latency_ms']['p95']} ms max={summary['latency_ms']['max']} ms")
    return summary


# ---------------------------------------------------------------------------

def _config_str(args: argparse.Namespace) -> str:
    return (f"model={args.model} threads={args.threads} lang={args.language} "
            f"bias={args.bias} production={args.production}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("window", "stream", "stress"),
                        default="window")
    parser.add_argument("--phrase", default="Hey Nico",
                        help="wake phrase matching the fixture set")
    parser.add_argument("--core-variants", default=r"ni[ckh]?[ck]o|nikko|nicko|niko",
                        help="comma-separated regexes for the core word in filenames")
    parser.add_argument("--model", default="base")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--language", default="de",
                        help='"de", "en", or "auto"/"none" for auto-detect')
    parser.add_argument("--bias", choices=("on", "off"), default="on")
    parser.add_argument("--production", action="store_true",
                        help="build via build_wake_whisper (ignores --threads)")
    parser.add_argument("--volumes", default="-20,-30,-40",
                        help="peak dBFS levels for scaled positive variants")
    parser.add_argument("--neg-cap", type=int, default=60)
    parser.add_argument("--limit", type=int, default=0,
                        help="cap positive fixtures (0 = all)")
    parser.add_argument("--burners", type=int, default=0,
                        help="CPU burner threads (stress mode)")
    parser.add_argument("--json", dest="json_out", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.mode == "stress" and args.burners == 0:
        args.burners = 8

    if args.mode == "window":
        summary = asyncio.run(run_window_mode(args))
    else:
        summary = asyncio.run(run_stream_mode(args))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(f"json written: {args.json_out}")


if __name__ == "__main__":
    main()
