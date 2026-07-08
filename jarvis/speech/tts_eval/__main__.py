"""CLI: score a TTS provider against the hard corpus and write a JSON report.

    python -m jarvis.speech.tts_eval --provider inworld
    python -m jarvis.speech.tts_eval --provider cartesia --language de

Builds the exact requested provider (not the key-aware cross-resolve, so you
eval what you asked for), times TTFA/RTF around its streaming synthesis, runs the
metric backends (WER always; MOS/drift when a model is provided), gates each item
against the acceptance thresholds, and prints a per-item + overall verdict.

OFF the voice hot path — a developer/CI tool. Design §3.6.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from jarvis.speech.tts_eval.corpus import HARD_CORPUS, EvalItem, items_for_language
from jarvis.speech.tts_eval.harness import EvalReport, SynthResult, Thresholds, evaluate
from jarvis.speech.tts_eval.metrics import default_metrics

_BCP47 = {"de": "de-DE", "en": "en-US", "es": "es-ES"}


def _make_synth_fn(provider: str):
    """A synth function that builds the exact provider once and times each call."""
    from jarvis.core.config import TTSConfig
    from jarvis.plugins.tts import _build_provider, _canonical_tts_name

    fam = _canonical_tts_name(provider)
    tts = _build_provider(TTSConfig(provider=fam), fam)

    async def synth(item: EvalItem) -> SynthResult:
        lang = _BCP47.get(item.language, item.language)
        chunks: list[bytes] = []
        sample_rate = 24_000
        t0 = time.perf_counter()
        ttfa_ms: float | None = None
        async for chunk in tts.synthesize(item.text, language_code=lang):
            if ttfa_ms is None:
                ttfa_ms = (time.perf_counter() - t0) * 1000.0
            chunks.append(bytes(chunk.pcm))
            sample_rate = chunk.sample_rate
        gen_s = time.perf_counter() - t0
        total_pcm = sum(len(c) for c in chunks)
        audio_s = (total_pcm / 2 / sample_rate) if sample_rate else 0.0
        rtf = (gen_s / audio_s) if audio_s > 0 else None
        return SynthResult(
            chunks=tuple(chunks), sample_rate=sample_rate, ttfa_ms=ttfa_ms, rtf=rtf
        )

    return synth, tts


def _report_to_dict(report: EvalReport) -> dict:
    return {
        "provider": report.provider,
        "passed": report.passed,
        "pass_rate": round(report.pass_rate, 3),
        "thresholds": vars(report.thresholds),
        "items": [
            {
                "id": r.item.id,
                "language": r.item.language,
                "tags": list(r.item.tags),
                "passed": r.passed,
                "reasons": list(r.reasons),
                "metrics": {k: v for k, v in vars(r.result).items() if v is not None},
            }
            for r in report.items
        ],
    }


async def _evaluate_provider(args: argparse.Namespace, items) -> EvalReport:
    synth_fn, tts = _make_synth_fn(args.provider)
    metrics = default_metrics(
        whisper_model=args.whisper_model, dnsmos_model_path=args.dnsmos_model
    )
    try:
        return await evaluate(
            synth_fn, items, metrics, Thresholds(), provider=args.provider
        )
    finally:
        aclose = getattr(tts, "aclose", None)
        if aclose is not None:
            await aclose()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis.speech.tts_eval",
        description="Score a TTS provider against the hard eval corpus.",
    )
    p.add_argument(
        "--provider", required=True, help="TTS provider/family (e.g. inworld, cartesia)."
    )
    p.add_argument(
        "--language", default="", help="Restrict to one language (de|en|es). Default: all."
    )
    p.add_argument(
        "--whisper-model", default="base", help="faster-whisper size for round-trip WER."
    )
    p.add_argument(
        "--dnsmos-model", default=None, help="Path to a DNSMOS ONNX model (enables MOS)."
    )
    p.add_argument(
        "--out", default="data/tts_eval/latest.json", help="Where to write the JSON report."
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    items = items_for_language(args.language) if args.language else HARD_CORPUS
    if not items:
        print(f"No corpus items for language {args.language!r}.", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(_evaluate_provider(args, items))
    except KeyboardInterrupt:
        return 130

    # File I/O + printing are synchronous (kept out of the async path, ASYNC240).
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(_report_to_dict(report), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    verdict = "PASS" if report.passed else "FAIL"
    print(f"\nTTS eval [{args.provider}] — {verdict}  ({report.pass_rate:.0%} items passed)")
    for r in report.items:
        mark = "ok " if r.passed else "FAIL"
        detail = "" if r.passed else "  -> " + "; ".join(r.reasons)
        print(f"  [{mark}] {r.item.id:12} {detail}")
    print(f"\nReport written to {out}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
