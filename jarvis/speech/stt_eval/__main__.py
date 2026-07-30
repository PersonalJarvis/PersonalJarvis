"""Run a labelled multilingual corpus against exact STT provider/model pairs.

Example (quote contender values in shells where ``|`` is a pipe)::

    python -m jarvis.speech.stt_eval --corpus data/stt-eval/corpus.jsonl \
      --contender "current|groq-api|whisper-large-v3-turbo|0.0006667" \
      --contender "candidate|openrouter-stt|openai/gpt-4o-transcribe|0"

Prices are explicit inputs, not embedded facts, because providers can change
them independently of a Jarvis release. Reports contain metrics and stable
errors, never reference text or provider transcripts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jarvis.speech.stt_eval.corpus import load_corpus
from jarvis.speech.stt_eval.harness import (
    ContenderReport,
    Recognition,
    evaluate_contender,
)


@dataclass(frozen=True, slots=True)
class ContenderSpec:
    label: str
    provider: str
    model: str
    price_per_minute_usd: float


def _parse_contender(value: str) -> ContenderSpec:
    parts = [part.strip() for part in value.split("|")]
    if len(parts) != 4 or not all(parts[:3]):
        raise argparse.ArgumentTypeError(
            "Expected LABEL|PROVIDER|MODEL|PRICE_PER_MINUTE_USD."
        )
    try:
        price = float(parts[3])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Contender price must be a number.") from exc
    if price < 0:
        raise argparse.ArgumentTypeError("Contender price cannot be negative.")
    return ContenderSpec(parts[0], parts[1], parts[2], price)


def _provider_identity(provider: Any) -> tuple[str, str]:
    family = str(
        getattr(provider, "last_used_provider", "")
        or getattr(provider, "name", "")
        or getattr(provider, "provider_label", "")
    ).strip()
    model = str(
        getattr(provider, "last_used_model", "")
        or getattr(provider, "_model", "")
        or getattr(provider, "_model_name", "")
    ).strip()
    return family, model


def _build_exact_provider(spec: ContenderSpec) -> Any:
    """Build the requested provider and reject factory fallback substitutions."""
    from jarvis.core.config import load_config
    from jarvis.plugins.stt import build_named_stt_provider

    configured = load_config().stt
    models = dict(getattr(configured, "models", {}) or {})
    models[spec.provider] = spec.model
    pinned = configured.model_copy(update={"models": models})
    provider = build_named_stt_provider(spec.provider, pinned)
    actual_family, actual_model = _provider_identity(provider)
    if actual_family and actual_family != spec.provider:
        raise RuntimeError(
            f"Requested STT provider {spec.provider!r}, but the factory resolved "
            f"{actual_family!r}. Add its credential in the app before evaluating."
        )
    if actual_model and actual_model != spec.model:
        raise RuntimeError(
            f"Requested STT model {spec.model!r}, but the provider built "
            f"{actual_model!r}."
        )
    return provider


def _recognizer(provider: Any):
    async def recognize(pcm: bytes) -> Recognition:
        started = time.perf_counter()
        try:
            transcript = await provider.transcribe_pcm(pcm, language="auto")
        except Exception as exc:  # noqa: BLE001 - a failed contender is a measurement
            return Recognition(
                text="",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=type(exc).__name__,
            )
        languages: list[str] = []
        top = str(getattr(transcript, "language", "") or "").strip()
        if top and top not in ("auto", "unknown", "und"):
            languages.append(top)
        for segment in getattr(transcript, "segments", ()) or ():
            if isinstance(segment, dict):
                value = str(segment.get("language", "") or "").strip()
                if value and value not in languages:
                    languages.append(value)
        text = (
            getattr(transcript, "raw_text", "")
            or getattr(transcript, "text", "")
            or ""
        )
        return Recognition(
            text=str(text).strip(),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            reported_languages=tuple(languages),
            cost_usd=getattr(provider, "last_usage_cost_usd", None),
        )

    return recognize


async def _close(provider: Any) -> None:
    close = getattr(provider, "aclose", None)
    if close is not None:
        await close()


async def _run(args: argparse.Namespace) -> tuple[ContenderReport, ...]:
    items = load_corpus(args.corpus)
    reports: list[ContenderReport] = []
    for spec in args.contender:
        provider = _build_exact_provider(spec)
        try:
            reports.append(
                await evaluate_contender(
                    _recognizer(provider),
                    items,
                    label=spec.label,
                    provider=spec.provider,
                    model=spec.model,
                    repeats=args.repeats,
                    price_per_minute_usd=spec.price_per_minute_usd,
                )
            )
        finally:
            await _close(provider)
    return tuple(reports)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis.speech.stt_eval",
        description=(
            "Compare exact STT provider/model pairs on a labelled JSONL corpus."
        ),
    )
    parser.add_argument("--corpus", required=True, help="Path to the JSONL manifest.")
    parser.add_argument(
        "--contender",
        action="append",
        required=True,
        type=_parse_contender,
        metavar="LABEL|PROVIDER|MODEL|PRICE",
        help="Repeat for every exact provider/model pair to evaluate.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Transcriptions per recording (default: 3).",
    )
    parser.add_argument(
        "--out",
        default="data/stt_eval/latest.json",
        help="JSON report path (default: data/stt_eval/latest.json).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.repeats < 1:
        print("--repeats must be at least 1.", file=sys.stderr)
        return 2
    try:
        reports = asyncio.run(_run(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"STT evaluation failed: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"schema_version": 2, "contenders": [asdict(report) for report in reports]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    for report in reports:
        switch = (
            "n/a"
            if report.switch_error_rate is None
            else f"{report.switch_error_rate:.3f}"
        )
        repeatability = (
            "n/a"
            if report.repeatability_error_rate is None
            else f"{report.repeatability_error_rate:.3f}"
        )
        cost = (
            f"measured_cost=${report.measured_cost_usd:.4f}"
            if report.measured_cost_usd is not None
            else f"estimated_cost=${report.estimated_cost_usd:.4f}"
        )
        print(
            f"{report.label}: WER={report.wer:.3f}, switch={switch}, "
            f"repeatability={repeatability}, latency={report.median_latency_ms:.0f} ms, "
            f"{cost}"
        )
    print(f"Report written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
