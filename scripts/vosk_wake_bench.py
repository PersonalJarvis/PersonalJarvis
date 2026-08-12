"""Vosk wake-word benchmark — replay a synthesized corpus through the REAL path.

Companion to ``wake_bench.py`` (which benches the stt_match path): this one
measures the ``vosk_kws`` engine end-to-end — streaming grammar stage 1 plus
the full verify stack — against synthesized speech, with the real Vosk models
installed under ``data/wake_models/vosk/``. It exists so precision/recall
changes to the verify gates are MEASURED, never hunch-calibrated (the standing
rule on every ``_SHAPE_*`` / competition bound in the provider).

Corpus: synthesized once via ``edge-tts`` (network required on first run) into
``data/wake_bench_corpus/<phrase-slug>/`` and cached; ffmpeg converts to
16 kHz mono s16 WAV. Classes per phrase:

- ``pos_isolated``  — the phrase alone                          -> must fire
- ``pos_command``   — phrase + command in one breath            -> must fire
- ``pos_mid``       — phrase EMBEDDED mid-sentence              -> must fire
- ``pos_end``       — phrase at the END of a longer sentence    -> must fire
- ``neg_random``    — unrelated words/sentences                 -> must stay silent
- ``neg_bare_core`` — the core word without its prefix          -> must stay silent
- ``neg_bare_prefix``— the prefix without the core              -> must stay silent
- ``neg_other_name``— "<prefix> <different name>"               -> must stay silent
- ``neg_flow``      — flowing dictation without the phrase      -> must stay silent

Examples:
    python scripts/vosk_wake_bench.py --phrase "Hey George"
    python scripts/vosk_wake_bench.py --phrase "Hey Jarvis" --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jarvis.core.protocols import AudioChunk  # noqa: E402
from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider  # noqa: E402
from jarvis.speech.wake_constants import (  # noqa: E402
    phrase_core,
    resolve_vosk_model_paths,
)

SAMPLE_RATE = 16_000
CHUNK_MS = 100
CORPUS_ROOT = REPO_ROOT / "data" / "wake_bench_corpus"

# A small de+en voice mix: the live install pairs a German speaker with
# English wake phrases, so both accents matter for every class.
VOICES = (
    ("de", "de-DE-KatjaNeural"),
    ("de", "de-DE-ConradNeural"),
    ("en", "en-US-JennyNeural"),
    ("en", "en-GB-RyanNeural"),
)

# Sentence templates keyed by language; "{p}" is the phrase, "{c}" the core.
TEMPLATES: dict[str, dict[str, list[str]]] = {
    "pos_isolated": {
        "de": ["{p}!"],
        "en": ["{p}!"],
    },
    "pos_command": {
        "de": ["{p}, wie ist das Wetter heute?"],  # i18n-allow
        "en": ["{p}, what's the weather today?"],
    },
    "pos_mid": {
        "de": ["Ich denke wir sind hier fertig, {p}, mach bitte weiter."],  # i18n-allow
        "en": ["I think we are done here, {p}, please continue."],
    },
    "pos_end": {
        "de": ["Okay, das reicht jetzt, {p}."],  # i18n-allow
        "en": ["Okay, that's enough for now, {p}."],
    },
    "neg_random": {
        "de": ["Pedro.", "Banane und Katalog.", "Wunderbar, vielen Dank."],  # i18n-allow
        "en": ["Pedro.", "Banana and catalogue.", "Wonderful, thank you."],
    },
    "neg_bare_core": {
        "de": ["{c}.", "Ich habe gestern mit {c} gesprochen."],  # i18n-allow
        "en": ["{c}.", "I talked to {c} yesterday."],
    },
    "neg_bare_prefix": {
        "de": ["Hey!", "Hey, hallo zusammen."],  # i18n-allow
        "en": ["Hey!", "Hey, hello everyone."],
    },
    "neg_other_name": {
        "de": ["Hey Nova!", "Hey Peter!", "Hallo Florian!"],  # i18n-allow
        "en": ["Hey Nova!", "Hey Peter!", "Hello Florian!"],
    },
    "neg_flow": {
        "de": [
            "Bitte speichere die Datei und öffne dann den Browser.",  # i18n-allow
            "Der Termin morgen wird auf halb drei verschoben, sag allen Bescheid.",  # i18n-allow
        ],
        "en": [
            "Please save the file and then open the browser with the new tab.",
            "The meeting tomorrow moves to half past two, please tell everyone.",
        ],
    },
}


def _slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c if c.isalnum() else "_" for c in folded).strip("_")


@dataclass
class FileResult:
    path: Path
    cls: str
    fired: int = 0
    log_lines: list[str] = field(default_factory=list)


def _to_wav(mp3: Path, out_wav: Path) -> None:
    """ffmpeg mp3 -> 16 kHz mono s16 WAV; removes the mp3 afterwards."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required to build the corpus (not found on PATH)")
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3),
         "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
        check=True,
    )
    mp3.unlink(missing_ok=True)


async def _synthesize(text: str, voice: str, out_wav: Path) -> None:
    """edge-tts -> mp3 -> ffmpeg -> 16 kHz mono s16 WAV (cached)."""
    if await asyncio.to_thread(out_wav.exists):
        return
    import edge_tts

    await asyncio.to_thread(out_wav.parent.mkdir, parents=True, exist_ok=True)
    mp3 = out_wav.with_suffix(".mp3")
    await edge_tts.Communicate(text, voice=voice).save(str(mp3))
    await asyncio.to_thread(_to_wav, mp3, out_wav)


def _load_wav(path: Path) -> bytes:
    import wave

    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE, path
        assert wf.getnchannels() == 1, path
        return wf.readframes(wf.getnframes())


async def _chunks(pcm: bytes, clock: list[float]) -> AsyncIterator[AudioChunk]:
    """Chunk the padded stream and advance the STREAM clock per chunk.

    ``clock[0]`` drives the provider's ``_monotonic`` seam: the bench decodes
    faster than real time, so backoff/cooldown deadlines must follow stream
    time or a latched retry would never come due inside a file (the live
    pipeline gets real-time chunks, where the two clocks agree). The tail
    padding is generous on purpose: it carries the post-phrase seconds during
    which a latched candidate's backoff deadline expires.
    """
    chunk_bytes = SAMPLE_RATE * CHUNK_MS // 1000 * 2
    lead = b"\x00" * int(0.4 * SAMPLE_RATE) * 2
    tail = b"\x00" * int(4.0 * SAMPLE_RATE) * 2
    stream = lead + pcm + tail
    for i in range(0, len(stream), chunk_bytes):
        clock[0] = i / (SAMPLE_RATE * 2)
        yield AudioChunk(
            pcm=stream[i : i + chunk_bytes],
            sample_rate=SAMPLE_RATE,
            timestamp_ns=i * 1_000_000_000 // (SAMPLE_RATE * 2),
        )


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self.lines.append(record.getMessage())


async def _run_file(
    phrase: str, model_paths: list[str], shared_models: dict, wav: Path, cls: str
) -> FileResult:
    provider = VoskKwsProvider(
        phrase,
        model_path=model_paths[0],
        model_paths=model_paths,
    )
    provider._models = shared_models  # share the loaded models across files
    provider._load_attempted = True
    clock = [0.0]
    provider._monotonic = lambda: clock[0]  # stream time, see _chunks
    result = FileResult(path=wav, cls=cls)
    capture = _Capture()
    vlog = logging.getLogger("jarvis.wake.vosk")
    vlog.addHandler(capture)
    old_level = vlog.level
    vlog.setLevel(logging.DEBUG)
    try:
        async for _keyword in provider.detect(_chunks(_load_wav(wav), clock)):
            result.fired += 1
    finally:
        vlog.removeHandler(capture)
        vlog.setLevel(old_level)
    result.log_lines = [
        ln for ln in capture.lines
        if "verify OK" in ln or "SUPPRESSED" in ln or "WAKE fired" in ln
    ]
    return result


async def _build_corpus(phrase: str) -> dict[str, list[Path]]:
    core = " ".join(phrase_core(phrase)).title()
    root = CORPUS_ROOT / _slug(phrase)
    out: dict[str, list[Path]] = {}
    jobs: list[tuple[str, str, Path]] = []
    for cls, by_lang in TEMPLATES.items():
        out[cls] = []
        for lang, voice in VOICES:
            for i, template in enumerate(by_lang[lang]):
                text = template.format(p=phrase, c=core)
                wav = root / cls / f"{voice}_{i}.wav"
                out[cls].append(wav)
                jobs.append((text, voice, wav))
    sem = asyncio.Semaphore(4)

    async def _one(text: str, voice: str, wav: Path) -> None:
        async with sem:
            await _synthesize(text, voice, wav)

    await asyncio.gather(*(_one(*j) for j in jobs))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phrase", default="Hey George")
    parser.add_argument("--language", default="en", help="primary wake language")
    parser.add_argument("--json", default="", help="write raw results to this file")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    model_paths = resolve_vosk_model_paths(args.language)
    if not model_paths:
        raise SystemExit("no Vosk models under data/wake_models/vosk/ — install one first")
    print(f"phrase={args.phrase!r} models={[Path(p).name for p in model_paths]}")

    corpus = await _build_corpus(args.phrase)

    # Load each model ONCE and share it across per-file providers (documented
    # Vosk multi-client pattern: one Model, many recognizers).
    loader = VoskKwsProvider(
        args.phrase, model_path=model_paths[0], model_paths=model_paths
    )
    for path in model_paths:
        await asyncio.to_thread(loader._ensure_model, path)
    shared_models = loader._models

    results: list[FileResult] = []
    for cls, files in corpus.items():
        for wav in files:
            res = await _run_file(
                args.phrase, model_paths, shared_models, wav, cls
            )
            results.append(res)
            if args.verbose:
                mark = "FIRE" if res.fired else "    "
                print(f"  [{mark}] {cls:15s} {wav.name}")
                for ln in res.log_lines:
                    print(f"         | {ln[:150]}")

    print()
    print(f"{'class':16s} {'files':>5s} {'fired':>5s}  verdict")
    failures = 0
    by_class: dict[str, dict[str, int]] = {}
    for cls in TEMPLATES:
        rows = [r for r in results if r.cls == cls]
        fired = sum(1 for r in rows if r.fired)
        want_fire = cls.startswith("pos_")
        ok = fired == len(rows) if want_fire else fired == 0
        if not ok:
            failures += 1
        verdict = "ok" if ok else ("MISSED WAKES" if want_fire else "FALSE FIRES")
        by_class[cls] = {"files": len(rows), "fired": fired}
        print(f"{cls:16s} {len(rows):5d} {fired:5d}  {verdict}")
        if not ok and not args.verbose:
            bad = [r for r in rows if bool(r.fired) != want_fire]
            for r in bad[:6]:
                print(f"    -> {r.path.name}")
                for ln in r.log_lines[-2:]:
                    print(f"       | {ln[:150]}")

    if args.json:
        payload = json.dumps(
            {
                "phrase": args.phrase,
                "classes": by_class,
                "files": [
                    {
                        "path": str(r.path),
                        "class": r.cls,
                        "fired": r.fired,
                        "log": r.log_lines,
                    }
                    for r in results
                ],
            },
            indent=2,
        )
        def _write_json() -> None:
            Path(args.json).write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write_json)
        print(f"raw results -> {args.json}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
